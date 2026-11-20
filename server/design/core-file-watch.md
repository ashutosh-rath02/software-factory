# `core/file.h` — filesystem change as an event, not a poll

Design for review. Not a ticket yet, and nothing is implemented.

## The problem

`catalog` is the only poller left in this server. Every 250 ms it stats every document in every
declared directory — 752 files today across two repositories — and diffs `{mtime, size}` against
the previous pass. Per file that is `is_regular_file` + `modifiedAtNs` + `file_size` +
`insideRepository_`, and the last one is `fs::weakly_canonical`, which `lstat`s every path
component. Measured warm-cache, one second of watching costs ~60 ms of syscall work, and it grows
linearly: `factory/docs/reports/` alone is 473 files and gains one per ticket run.

Everything else in the server is already event-driven — `http` has an acceptor, `agent` has pipes,
`lane` has one-shot timers armed by activity. And the client is *forbidden* to poll:
`no-poll.test.tsx` fails on a timer-driven fetch. The rule stops one layer short of the disk.

## What goes in `core/`

`core/file.h` + `core/file.cpp`, namespace `file`, one component: `file::Watch`.

Same placement as `http::Server` — a transport-shaped thing in `core/` with its own namespace, not
under `core::`. The namespace is `file` and not `fs` because `fs` is already
`namespace fs = std::filesystem` throughout `app/`.

```cpp
namespace file {

using Strand = boost::asio::strand<boost::asio::io_context::executor_type>;

// What happened to one entry inside a watched directory.
struct Change {
    enum class Kind {
        Appeared,     // created, or moved in
        Modified,     // a writer closed it
        Vanished,     // deleted, or moved out
        Rescan,       // this registration's knowledge is stale — read the directory again
    };

    Kind kind = Kind::Rescan;
    std::string dir;      // the directory as it was registered
    std::string name;     // the entry inside it; empty on Rescan
    std::string path;     // joined here, so no caller joins a path (SPEC.md §10)
};

using Handler = std::function<void(const Change&)>;
using Id = unsigned long long;

class Watch : public core::Component {
public:
    static Watch& instance();

    void init() override;
    void start() override;
    void stop() override;

    // The handler is invoked ON `strand` — the CALLER's, never this component's. See D1.
    Id add(const std::string& dir, Strand& strand, Handler handler);
    void remove(Id id);

    // False when the kernel would not give us an inotify instance. Callers fall back; the
    // tool does not stop working because a container has no inotify. See D7.
    bool available() const;

    // Every event the kernel had queued before this call has been delivered. See D10.
    void barrier(std::function<void()> done);
};

}
```

## Mechanism

`inotify_init1(IN_NONBLOCK | IN_CLOEXEC)` gives a file descriptor.
`boost::asio::posix::stream_descriptor` wraps it, and from there it is one `async_read_some`
re-armed forever on the io_context — structurally identical to the acceptor in `core/http.h`.
No thread, no timer, no polling. Boost 1.74 on this machine has `stream_descriptor`; the limits
are `max_user_instances=128`, `max_user_watches=65536`, `max_queued_events=16384`.

The read buffer is 64 KB. inotify returns only whole records, so there is no partial-record state
to carry between reads.

## The decisions

**D1 · The handler runs on the caller's strand, and `add()` takes it.**
The state a handler mutates belongs to the caller — `catalog::Store::stamps_` — and only the
owner's executor can serialise the owner's state. This is the same sentence `core/engine.h` ends
its threading contract with, about why `Engine` cannot post a component's `stop()` on its behalf.
A watch that delivered on its own strand would hand every subscriber a second executor over state
that already has one.

**D2 · One inotify instance for the process, many watch descriptors.**
`max_user_instances` is 128 and `max_user_watches` is 65536, so instances are the scarce resource
and watch descriptors are not. One instance also gives D10 for free: the event queue is per
instance and FIFO **across every watch descriptor on it**.

**D3 · The mask is chosen, and `IN_MODIFY` is deliberately not in it.**

| watched | becomes |
|---|---|
| `IN_CREATE`, `IN_MOVED_TO` | `Appeared` |
| `IN_CLOSE_WRITE` | `Modified` |
| `IN_DELETE`, `IN_MOVED_FROM` | `Vanished` |
| `IN_DELETE_SELF`, `IN_MOVE_SELF`, `IN_UNMOUNT` | `Rescan` for that registration, then re-add |
| `IN_Q_OVERFLOW` | `Rescan` to **every** registration |

`IN_MODIFY` fires once per `write()`, so a 40 KB report emits a burst and half of them name a file
that is still being written. `IN_CLOSE_WRITE` fires once, when the writer closes the descriptor.
That is the debounce, provided by the kernel, and it is why this design has **no debounce timer**.
Callers never see a raw inotify mask.

**D4 · Coalescing is the caller's, and it is already written.**
An event says *look at this path*. `catalog` stats it and reports a `Change` only if the stamp
actually moved. So a duplicate event costs one `stat`, not one frame on the wire — the
`{mtime, size}` diff that is the whole watch today becomes the dedup step instead.

Worth noting what this does to the soundness question. `{mtime, size}` is a *verifying trace* in
the *Build Systems à la Carte* sense, and it is unsound for two writes in the same nanosecond at
the same size. Today that is a **missed** change. After this it is a **redundant read**, because
detection comes from the kernel and the stamp only decides whether to re-render. Strictly better,
and it removes the reason to reach for content hashing.

**D5 · Overflow is an event, not an error.**
`IN_Q_OVERFLOW` means the kernel dropped events — a `git checkout` in a lane can do it. The
answer is `Rescan`, and `catalog` responds by running the `scan_()` it already has, once, on a
worker. The full scan is not deleted; it is demoted from *the mechanism* to *the resync path*.

**D6 · A directory that does not exist yet.**
`inotify_add_watch` fails `ENOENT`. A repository may declare `reports/` before anyone has written
one. `add()` then walks up to the nearest existing ancestor, watches that for the missing
basename, and on `Appeared` adds the real watch and emits one `Rescan`. Bounded by the repository
root; below that the registration reports unavailable rather than walking to `/`. No retry timer.

**D7 · `available() == false` is degraded, never fatal.**
inotify can be absent — fd exhaustion, a restricted container, a filesystem that does not support
it (some network mounts). The tool must still work, so `catalog` keeps its 250 ms timer and arms
it only when `available()` is false. **The change is not "replace polling with events"; it is
"add events, and poll only when events are unavailable or were lost."**

**D8 · Non-recursive, on purpose.**
One watch per registered directory. No subtree walking, no watch storms, no `ENOSPC` at 65536.
Recursion is where inotify designs die, and nothing here needs it — `catalog` watches six fixed
directories. If a later ticket needs a tree, that is that ticket's problem to argue.

**D9 · `stop()` posts the close onto the watch's own strand.**
Exactly what `http::Server::stop()` does with its acceptor, and for exactly the reason
`SRV-FIX-025` records: `core::Engine::forceStop_()` calls every component's `stop()` from
whichever pool thread took the signal, while the read handler is running on another. Closing the
descriptor inline is that same race. The origin's `core/http.cpp` had it; this file must not
reintroduce it.

**D10 · `barrier()` — the assertion problem, solved in core rather than in a test.**
A watcher cannot distinguish *nothing happened* from *not delivered yet*, so a test asserting
"edit the document → `catalog.changed` arrives" has nothing bounded to wait on. With a 250 ms
poll it can sleep; with events it cannot.

The sound answer is Watchman's cookie: write a sentinel, wait to observe its own event, and
everything queued before it is known-delivered. D2 makes it cheap — the queue is FIFO across the
whole instance, so **one** cookie directory owned by the tool (under `.factory/`, never a watched
document directory) is a barrier for every registration. The sentinel is created and unlinked by
`core`, so no test writes into the repository under test and `permenv.unchanged()` stays true.

This is in `core/` and not in `conftest.py` on purpose: a synchronisation point built in test
scaffolding because the product lacks one is the thing `§2`'s workaround rule says to file as a
ticket.

## What `catalog` becomes

| today | after |
|---|---|
| `start()` arms a 250 ms timer | `start()` primes once with the existing `scan_()`, then arms nothing |
| every pass stats 752 files | a handler stats **one** path |
| `scheduleScan_()` is the loop | `scheduleScan_()` is called for prime, for `Rescan`, and for the `!available()` fallback |
| `Scan` struct handed strand→worker→strand | unchanged, and still the resync path |
| `stamps_`, `primed_`, `handlers_` on the strand | unchanged |

`init()` gains six `add()` calls, one per declared directory, each passing `catalog`'s own strand.
The `Scan` handoff, the change kinds, `onChange`, and everything downstream of it are untouched —
which is what makes the existing suite the behaviour gate.

## Gate

`cd factory/server && pytest tests/ -v` — the full suite, unchanged. `test_catalog.py` and
`test_ws.py` already assert that a document changing on disk pushes `catalog.changed`, and this
ticket may not alter one line of either. Plus:

- a case that drives `Rescan` by forcing `IN_Q_OVERFLOW` (write more than `max_queued_events`
  entries with the reader stalled) and asserts no change is lost;
- a case with `available()` forced false that asserts the fallback still reports the change;
- `barrier()` used by both, so neither is a sleep.

## Out of scope

Recursive watching · `fanotify` · macOS `kqueue`/FSEvents · watching lane worktrees · content
hashing · migrating any other module. `core/file.{h,cpp}` has no origin counterpart in
`server/core`, so `core/VENDORED.md` gets a row saying it was written here — the same rule that
applies to any `core/` file that did not come from the origin.

## Risks, honestly

1. **Linux only.** Already true of this product (`bwrap`, `/proc`), but `core/` is vendored and
   the row in `VENDORED.md` has to say so.
2. **A writer that never closes.** `IN_CLOSE_WRITE` is the debounce; a process holding a document
   open and flushing forever would go unnoticed until it closes. No writer here does that — agents
   append and close, editors rename.
3. **This is a CPU and latency change, not a correctness fix.** Today's poller is simple and has
   no failure modes. This adds three that must be handled deliberately: overflow, watch lifecycle,
   and the barrier. D5, D6 and D10 are those three, and each keeps the old path as its fallback.
