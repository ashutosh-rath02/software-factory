# Filesystem work: the workflow, and what is left to do

Written after migrating `catalog` onto `core/file.h`. Part 1 is the procedure — the rules are
the mistakes that pass made, in the order it made them. Part 2 is the remaining work, scoped
against what the code actually contains rather than what a sweep like this usually assumes.

---

# Part 1 — The workflow

Five phases. Each ends with a build and a named gate; nothing proceeds on a red one.

## Phase 0 · Measure before designing

Do not open a file until the numbers are on the table. The `catalog` migration was justified
by three commands, and one of them changed the design:

```bash
ls -1 <every watched dir> | wc -l                 # what a pass costs, in entries
grep -rn 'expires_after' app/ core/               # every timer, and which are polls
grep -rn 'Worker::instance().post(' app/          # every place a worker completion lands
```

**A timer is not a poller.** Of the six timers in this server, one polls the filesystem; the
rest wait on a pid, a grace period, or an idle socket. A sweep that "removes polling" and
touches those is a sweep that broke process supervision for no gain.

*Gate:* a written line per timer saying what it waits on, and a count for every directory.

## Phase 1 · Put the mechanism in `core/`, and the hard cases with it

The caller gets `Created / Modified / Deleted` and nothing else. Overflow, a directory that
does not exist yet, and a kernel with no inotify are absorbed by `core`, because a hard case
in the interface is a hard case every caller re-implements.

Rules this phase is judged on:

1. **`app/` never names `boost::asio`.** If a `core/` header is included by `app/`, its asio
   members go behind a pimpl. `core::Queue` is what `app/` holds instead of a strand, and
   `Queue::offload(work, done)` is what it holds instead of `Worker::post` plus a hand-written
   `boost::asio::post(strand, …)` hop.
2. **Per-entry work that costs a file read runs on the worker that stats the entry.** That is
   what `Enrich` is for. Without it the caller opens the file from its own queue, and a file
   read on a pool thread is the one thing this server does not do.
3. **No comments.** `CLAUDE.md` says so. Writing them and stripping them afterwards cost 105
   lines in this pass.

*Gate:* `grep -c asio` is 0 in every `app/` file that includes the new header; build is warning-free.

## Phase 2 · Migrate ONE module, and read the diff as a stranger

One module. `catalog` was chosen because it is the smallest thing that exercises every case:
multiple directories per registration, two id conventions, and a consumer downstream.

Rules:

4. **Registration goes in `init()`, never `start()`.** `core::Engine::start()` runs every
   `init()` before the pool exists and every `start()` **concurrently with it**. A `start()`
   that mutates a registry another component's `start()` already posted work over is a data
   race, and it is the bug this pass shipped and caught in review rather than in a test.
5. **Types belong to the component.** No free-standing `namespace x { … }` beside a
   `COMPONENT`, no anonymous namespace of helpers. Enums, structs and pure functions nest;
   helpers become private statics. It makes the dependency real: `catalog::Ids` reaching
   `catalog::Store::IdSurvey` was always true and only became visible here.
6. **Anything O(n) on a queue is suspect.** Copying a 473-entry stamp map to hand it to a
   worker is not blocking, and it is still the wrong amount of work on an io thread. A partial
   pass copies only what its events named.

*Gate:* the module's own tests, then the full suite.

## Phase 3 · Sweep for what the old mechanism left behind

Deleting a loop leaves state whose only reader went with it. This pass left `stopping_` — a
write in `stop()` that nothing read — and two public members with no caller outside the module.

```bash
grep -n '<member>' app/<mod>/*.cpp app/<mod>/*.h
for m in <each public method>; do grep -rn "\.$m(\|>$m(\|::$m(" app/ | grep -v '<mod>/'; done
```

7. **A write with no reader is dead, not harmless.** It reads as synchronisation that is not
   happening.
8. **Do not claim a property you have not run.** This pass claimed the migration turned the
   `{mtime,size}` blind spot from a missed change into a redundant read. It does not — the
   stamp still gates the emit. The claim was corrected in review; it should not have been made.

*Gate:* no dead members; the public surface is what is actually called.

## Phase 4 · Gate on the suite that already existed

The existing tests are the contract. A refactor that needs a new test to prove it works has
changed behaviour, and that is a different ticket.

*Gate:* `cd factory/server && pytest tests/ -q`, full suite, against a baseline run on `main`
so a pre-existing failure is not read as a regression.

---

# Part 2 — What is left

## Done

| | |
|---|---|
| `core/file.{h,cpp}` | inotify on the engine's own io_context; `Created/Modified/Deleted`; overflow, late directories and no-inotify absorbed; `FILE_WATCH` / `FILE_HANDLER` |
| `core/queue.{h,cpp}` | `post` and `offload`; the strand behind a pimpl |
| `app/catalog/store` | migrated; −264/+60; no timer, no asio, no anonymous namespace, no free namespace block |

## The one real remaining poller

**`delivery::Probe`** — `FACTORY_DELIVERY_POLL_MS`, default **5000 ms**, and every pass forks
`git`. It is the only other thing in this server that asks the disk a question on a timer.

It is **not** a repeat of `catalog`, and pretending otherwise is how this goes wrong:

- what changes is a git ref, and refs live in two places — loose files under `.git/refs/` and
  a single packed `.git/packed-refs` that a `git gc` rewrites. Watching one and not the other
  sees half the updates;
- `.git/index`, `.git/HEAD` and the worktree all move independently;
- the answer still costs a fork, so an event cannot replace the pass — it can only decide
  **when** to run it.

So the honest migration is smaller than "remove the poll": **an event arms the existing pass,
and the 5 s timer stays as the floor.** A repository nobody is touching stops forking git
every five seconds; one being actively committed to answers immediately instead of up to 5 s
late. Both are wins, neither needs the ref-storage question answered.

*Its own ticket. Not a `catalog` follow-up.*

## Not filesystem work, despite being timers

Named here so the next sweep does not take them:

| site | waits on |
|---|---|
| `lane/store.cpp:773` sweep | dead lane processes — the process table |
| `lane/store.cpp:2095` settle | a pid to leave |
| `lane/store.cpp:2456` | a queued resource |
| `agent/session.cpp:746` grace | the kill grace, one-shot |
| `api/socket.cpp:305` tick | idle websocket connections |

## The `core::Queue` conversion

Independent of the watch, and the larger half by volume. Every `app/` module still builds its
own strand: `catalog/ids`, `lane`, `agent`, `delivery`, `api`, `permissions`.

Order — cheapest and most isolated first, each its own ticket, each gated on the full suite:

1. `catalog/ids` — one strand, one worker hop, and it is already next to migrated code
2. `permissions` — one strand, no filesystem
3. `delivery` — do it with the probe ticket, not separately
4. `agent` — pipes and a grace timer; the strand is the smaller part
5. `lane` — 3 timers, 24 filesystem calls, the largest
6. `api` — 25 anonymous namespaces; structural, and worth doing last

`Queue::offload` is the point of the exercise: `lane/command.cpp:110-114` writes the
`Worker::post` → `boost::asio::post(strand)` hop by hand, and every site that does is a site
that can put the completion on the wrong executor. After conversion the wrong version does not
compile.

That last sentence was **false for sixteen days**. Five modules were converted; `lane` — listed
fifth here, *"the largest"* — and `api` were not, and the two-argument `core::Worker::post` stayed
public, so 30 sites in `app/` went on writing the hop by hand with 57 result slots behind them,
under a convention gate that could not fire (`docs/operations/ESCAPES.md`, 2026-08-23 row). `SRV-FIX-476`
finished the conversion and made the sentence true: the overload is private to `core::Queue`, the
result is the completion's parameter, and `test_queue.py::test_a_completion_off_the_queue_has_no_type`
requires the compiler to refuse the hand-written version with a control probe beside it.

## Decided, and not being done

`FILE_WATCH` gave the *incoming* side a declaration surface. The *outgoing* side stays
hand-rolled: `catalog::Store::onChange` remains a `std::function` typedef, an `onX()` and a
`std::vector<Handler>`, with its two subscribers as lambdas in `api::Manager::init()`.

A generic event registry — `SRV-FEAT-228`'s `core/event.h`, or the producing half of
`FILE_WATCH` — was **considered and declined**. `onChange` already has the safe one of the four
semantics that ticket catalogued: it appends, and registration is queue-guarded. The asymmetry
with `FILE_WATCH` is real and is accepted; it is not a defect to be scheduled.

Recorded here so that a later reader finds a decision rather than an oversight, and so the same
proposal is not made a third time without new evidence. What would count as new evidence: a
second subscriber that a producer silently evicts, which is a **replace**-semantics `onX()` —
and `permissions::Store` has four of them (`store.cpp:144-147`). That is a bug in those four
methods, fixable with a `std::vector`, and not an argument for a registry.
