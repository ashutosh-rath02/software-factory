# `core::Task` — a wait that runs on a queue

Why the server's version is not the firmware's, written down because the obvious move was to copy
`firmware/master/app/components/core/task.h` and three of its decisions would have been wrong here.

## What it is

```cpp
core::Task pollTask_ {queue_, [this] { pollTick_(); }};
pollTask_.start(pollMs_, 1);
```

The handler runs on the `core::Queue` it was given, so it is serialised with everything else that
touches the component's state. `app/` never names `boost::asio`, which is the rule that makes a
`core` type necessary at all.

**The queue is a parameter, and that is the whole of it.** Between `355a4ff2` (2026-08-23) and
`SRV-FIX-443` the constructor took a `Component*` and built its timer on
`strandOf(component->queue())` — `core::Component`'s own `queue_`, a strand **no component posts a
handler onto**. Every component keeps its state on a queue of its own (`lane::Store::exec_`,
`api::Socket::queue_`, …), and two strands serialise nothing between them, so the sweep, the
silence tasks and the settle tasks mutated `lanes_`, `silence_` and `settling_` beside every
handler that touches them. It measured as 11 ThreadSanitizer data races the first time a lane
timer was ever run under TSan beside a handler.

The case written for that commit asserted the string `strandOf(c->queue())` appears in
`task.cpp`, and stayed green for the whole life of the defect: *the component's queue* had two
referents. There is no source-reading case here now. `core::Component` has no queue, `core::Task`
takes one, and the wrong construction has no type — asserted by a compile probe in
`test_task.py::test_a_task_cannot_be_built_without_a_queue`, which requires the compiler to
refuse it and requires an otherwise identical call with a `core::Queue&` to compile in the same
run.

## No macro

The firmware has `M_TASK(name)` / `M_TASK_HANDLER(cls, name)`, and this server has the same shape in
`HTTP_SERVICE` and `FILE_WATCH`. It is **not** used here — and between `355a4ff2` and
`SRV-FIX-443` it was, which is how the executor got lost. **A macro cannot name a queue.**
`M_TASK(sweep)` expands to `core::Task(this, …)`, so the only executor it can reach is the base
component's, and the argument this section already made against the macro turned out to be the
argument against the defect.

Those two earn a macro because the registration is **global and otherwise invisible**: a route is
handed to `http::Server`, a watch to `file::Watch`, and without a declaration in the component's
header nothing in the class says the component answers `/api/health` or reacts to a directory. The
macro's link error — *declared, never implemented* — is the guarantee that a wire nobody wired
fails the build.

A `Task` is a **member**. `core::Task poll_;` in the header already declares it, and the handler is
right there. A macro would add a declaration/definition split and then catch the split it created.
It solves its own problem.

It also does not fit half the sites. Two tasks here are fixed component members; three are per
entity — a grace per run, a silence per lane, a settle per lane — and a macro cannot declare those
at all.

## `repeats` has no default

`firmware/.../core/task.h` is `start(uint32_t interval, int32_t loop = -1)` — **infinite by
default** — and that default has already cost this codebase a debugged failure. A 5 ms repeat
queued about eight `Timeout` events, which drained as back-to-back retries in the next state and
aborted an OTA. The firmware's own guidance is now *"always pass `loop = 1` for watchdog and
one-shot timers"*, which is a rule compensating for a default.

Every wait in this server is one-shot and re-armed by whatever produced the work. So the parameter
is required — `start(interval, 1)`.

**`Task::Once` and `Task::Forever` are not in the code.** They were described here before they
existed and are still not written; `1` and `-1` are what the call sites pass. `SRV-FIX-443` was
scoped to one executor change rather than an API restoration and deliberately left them alone.

## A typed duration, not an `int`

**Not implemented.** `start(uint32_t interval, int32_t loop)` takes milliseconds as a number, and
the grace call site still reads `(graceS > 0 ? graceS : 5) * 1000`. This section describes what the
type should become, not what it is; `SRV-FIX-443` left it for the same reason. A unit carried in a
comment is a unit that goes wrong at the fourth call site, so this is a real debt and it is written
down as one rather than as a description of the code.

## Destroying it cancels it, and that replaces the identity guard

The old code hand-wrote a stale-completion check twice, in two shapes:

```cpp
if (ec || stopping_ || sweepTimer_ != timer) return;              // store.cpp:776
if (held == silence_.end() || held->second != timer) return;      // store.cpp:2461
```

`Task` is movable and its destructor stops it, so it lives in a `std::map` **by value** and
replacing an entry cancels the wait it replaced:

```cpp
silence_.insert_or_assign(name, core::Task {exec_, [this, name] { … }});
silence_.at(name).start(static_cast<uint32_t>(wait), 1);
```

`disarm_()` is now `silence_.erase(name)` and nothing compares identities. The generation counter
that makes a cancelled or re-armed wait not fire lives once, inside `Task`.

## Threading

`Impl::timer` is constructed on the strand of the queue it was **given** (`core/queue-impl.h`,
core-internal), so the asio completion runs there and `stop()` from that queue is serialised with
it. `strandOf` is core-internal, so a timer built on it cannot be reached from `app/` — which is
the other half of the rule: `app/` names no executor at all.

Two ways this has gone wrong, both now closed by the type rather than by a rule:

* the timer on the **raw pool** (`Engine::service()`), which only posted the callback and raced
  `generation` between a pool thread and the queue — survived four green full-suite runs and was
  found by reading the code;
* the timer on the **base component's queue**, which is a strand nothing else uses, so it raced
  every handler of the component that owned it (`SRV-FIX-443`).

`core::Component` now has no queue, so neither is reachable: there is exactly one executor in
scope at a `core::Task` construction, and it is the one the caller passed.

`lane::Store::kill_` is the newest reason the `shared_from_this` self-capture below is
load-bearing. It arms a task at `killing_[pid]` from inside the handler of the task already living
there, every 20 ms for the length of a kill grace.
