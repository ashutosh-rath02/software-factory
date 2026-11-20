# `core::Queue::offload` — a hop with a type, and the result is a parameter

Why the worker's answer is the completion's argument, written down because the shape it replaced
compiled everywhere and could not be seen by anything.

## What it is

```cpp
exec_.offload(
    [pid] { return git::waitFor(pid); },
    [this](Exit ended) { reconcile_(ended); });
```

Work runs on a `core::Worker` thread; the completion runs on **this queue**, with the work's
return value as its parameter. `R = std::invoke_result_t<F>`: `void` means `done()`, anything
else means `done(R)`. The one `std::shared_ptr<R>` that carries the value across the two threads
lives inside the template, in this file, once — which is the point.

## What it replaced

`core::Worker::post(task, callback)` ran the task on the pool and posted the callback onto the
**raw engine io_context** — not onto any queue. Every strand-owned caller therefore wrote the hop
back by hand:

```cpp
auto alive = std::make_shared<bool>(false);
core::Worker::instance().post(
    [alive, holderPid] { *alive = git::isRunning(holderPid); },
    [this, name, waiting, alive, holderPid, done] {
        exec_.post([this, name, waiting, alive, holderPid, done] {
            ...
        });
    });
```

Three lambdas, the capture list written twice, and a heap slot per value because the worker
returned nothing. On 2026-09-08 `app/` held **30** of the two-argument form, **13** hand-written
re-hops and **57** result slots — eight of them at one call site.

Two things go wrong with that shape, and only one of them is ugly.

- **The completion can land on the wrong executor.** Forget the inner `exec_.post` and the
  completion mutates the component's state from an engine thread beside every handler that owns
  it. `docs/operations/ESCAPES.md`'s 2026-08-23 row is the measurement: delete the re-post from the claim
  preflight and `ENGINE_THREADS=8 pytest tests/test_lane.py -q` answers `1 failed, 45 passed`
  with `test_a_lane_cannot_be_claimed_twice` returning `worktree_failed` instead of `lane_exists`.
- **The slot outlives the answer.** A `shared_ptr` allocated before the hop is writable from both
  sides for as long as anything holds it; a parameter is not.

## Why it is the compiler that refuses, and not the gate

A rule owned this shape from the start — `tools/conventions.py::manual_worker_hop` — and it never
once fired. It keyed on `boost::asio::post(` appearing within twelve lines of a
`Worker::instance().post(`, and `app_names_asio` exists to guarantee that string is absent from
`app/`. An unsatisfiable precondition reports zero on every input, `--list` prints a dead rule and
a clean tree identically, and the tree answered `conventions: clean` for sixteen days with thirty
of the shape in it (`SRV-FIX-476`, `escaped_from: SRV-FEAT-239`).

So the two-argument `core::Worker::post` is **private, with `core::Queue` its only friend**. There
is no `Worker::offload` and no free `core::offload`: a second public spelling of a completion on
the raw engine is the same hole. The next hand-written hop is a build failure rather than a
baseline row, and a build failure cannot retire itself.

`manual_worker_hop` keeps the two shapes the compiler cannot see — a task lambda assigning through
a dereferenced capture, and a completion that posts onto a queue in its own body — and each has a
firing case in `tests/test_conventions.py`, so it cannot die the way its predecessor did.

`core/file.cpp` is the one place left that writes the hop by hand. It holds a raw strand rather
than a `core::Queue`, and it is inside `core/`, where naming `boost::asio` is the module's job.

## What happens when the work throws

`Worker::post` catches, logs and drops it — both overloads, which is new for the one-argument one
(`SRV-FIX-476`): 19 HTTP actions moved onto it, and an exception escaping a worker lambda would
otherwise unwind `workerIo_.run()` and be swallowed by the pool thread's own restart loop.

In the value branch the slot is `std::make_shared<R>()` — **default-constructed, deliberately**.
The completion is posted whether or not the work threw, so `done` must have something to receive;
a default `R` is what the `std::make_shared<T>()` slots this replaced left behind, so the throw
path is byte-identical to the shape it came from. The cost is that `R` must be
default-constructible, and the compiler says so at the call site. `std::optional` would trade a
known-wrong value for undefined behaviour on the same path.

A request whose render throws is therefore logged and never answered — the router's `500` guard
(`core/http.cpp:249-268`) only sees what throws on its own frame. That is `SRV-FIX-484`, filed
against this ticket: the fix belongs to the `Responder`, not to 19 call sites.

## What still belongs on the worker

Only what waits: a subprocess spawn, `waitpid`, file I/O, a directory walk. **A syscall that
returns without waiting does not** — `kill(pid, 0)` is a permission check against the process
table, and three sites offloaded it, which bought a thread hop, a heap slot and a reply whose
snapshot described a different instant from its own `holder_alive`. See `design/lane.md`.
