# `lane` — one queue, and everything that must not run on it

Goal status from the live agent is kept on the lane record for the current run. A blocked,
budgetLimited, or usageLimited status makes that live lane need the operator and names the status
as its blocked reason. A new run clears the prior goal status; active and complete clear it during
the same run. A simultaneous permission request keeps its actionable reason and request ID;
the goal reason appears after that request is resolved.

`lane::Store` is the busiest component in the server and the one with the most ways to get an
executor wrong. It owns the lane set, the exclusive-resource queue, the roles, the watches on
every running process, and three kinds of timer. This note is what `SRV-FIX-443` had to write
down before it could fix any of it.

## Configuration is prepared for every process

`onPrepareConfig` is an API-composed callback registered on `exec_`. `startProcess` resolves the
selected profile and actual worktree, then calls that single preparer for both agent launches
and declared shell commands. The callback returns opaque generated and borrowed file lists;
the lane owns seed-once writes and carrying those files on the existing worker hop, not their
provider-specific syntax. E19 retains manifest-default selection. Its behavior cannot depend on
a prior E18 launch or on configuration surviving the startup ownership sweep. The selected-only
directory projection and all confinement mounts are unchanged; `agent-session.md` owns the
configuration rationale.

## One queue, named `exec_`

Process admission reserves a lane in `spawning_` on `exec_` before confinement, configuration
carrying and fork leave for a Worker. The final admission checks the expected lane claim's ticket,
workspace, role, branch, worktree, claim time and additional workspaces, as well as worktree readiness,
live processes and queued resources. Declared command wrappers carry their observed record into
this check; routing additionally supplies its source-bound record. Concurrent routed commands,
manual commands and agent launches therefore share one admission boundary. A Worker completion
checks the same claim and reservation again before publishing a pid or attaching a watch.
The launch passes both environment overrides and explicit exclusions to confinement; exclusions
apply only to the new child and do not mutate the server process environment.

Normal release, recovery, forgetting and resource release refuse a reserved spawn. Forced release
cancels the reservation and waits for its owned process, if already forked, to be killed and reaped
through the existing reaper. The existing `StopPid` type communicates cancellation to the Worker:
zero means no published pid, minus one means cancelled, and a positive value is the owned child.
The Worker publishes its pid with a compare-and-exchange, so cancellation before publication still
kills that child. The lane remains reserved until reaping completes; an old callback cannot attach
to a later claim or signal its process.

An agent launch's existing start hold remains in force. If force release cancels a spawn while that
hold exists, the cancelled launch first resolves its normal callback; `releaseStartHold` then resumes
the deferred release. This preserves the existing preflight lifecycle and prevents an old hold
completion from touching a reclaimed lane. Pending-spawn mutations and release continuations stay
on `exec_`; no additional executor, timer or atomic declaration is introduced.

Resource handoff treats an owned pending spawn as working even before a pid is published.
Ordinary commands explicitly declare `holdResourcesUntilExit` through `attachProcess`; their watch
protects resources independently of any earlier agent's turn state. That watch stays registered
after output EOF until the existing reaper reports the actual terminal result, then resource
reconciliation runs on `exec_`. Closing stdout and stderr therefore cannot grant a resource to a
queued role while a command continues running. Agent launches leave this capability unset, so an
idle agent can still hand its resource to its declared review role under the existing policy.

Every mutation of `lanes_`, `holder_`, `queue_`, `byRequestId_`, `roles_`, `watches_`, `silence_`,
`settling_` and `killing_` is posted onto `exec_`, and every read that answers an endpoint is
too. There is no second executor and no mutex: `docs/technical/ARCHITECTURE.md` says asio's answer to
handlers that share state is a strand, and `exec_` is it.

The name is historical — every other component calls its queue `queue_` — and `SRV-FIX-443`
deliberately did not rename it. Renaming is churn the diff did not need, and `lane::Store` already
has a `queue_` member that is something else entirely: `std::map<std::string,
std::vector<std::string>> queue_`, the waiting list per exclusive resource.

`tools/conventions.py`'s `one-queue-per-component` rule counts `core::Queue` members per component
header and refuses a second one.

## Three timers, and where they run

All three are `core::Task` built on `exec_`, so their handlers are serialised with every other
handler that touches the state they mutate.

| timer | lives in | fires |
|---|---|---|
| sweep | `sweepTask_`, one per component | every `SWEEP_INTERVAL_MS` (5 s) while `lanes_` is non-empty, to notice a worktree that has gone |
| silence | `silence_`, one per lane | at the lane's silence deadline, to announce `motion: silent` |
| settle | `settling_`, one per lane | every `SETTLE_STEP_MS` while a recover waits for the reaper to clear a pid |
| kill grace | `killing_`, one per in-flight kill | every `KILL_STEP_MS` for the length of a force-release or recover |

Before `SRV-FIX-443` all of them ran on `core::Component`'s own queue, because `core::Task`'s
constructor took a `Component*` and that was the only executor it could reach. No component posts
a handler onto the base queue, so all three ran beside the handlers that own their state. See
`design/core-task.md`.

`silence_`, `settling_` and `killing_` all destroy or replace the map entry that owns the running
task from inside that task's own handler. `core::Task::Impl` is held by `shared_ptr` and the
`async_wait` completion captures `shared_from_this()` for exactly this reason;
`test_task.py::test_a_task_may_destroy_itself_inside_its_own_handler` is the case that keeps it
true.

`killing_` is keyed by **pid**, not by lane name. `release()` erases the lane record before the
kill begins and lane names are reused, so a name-keyed entry could be cancelled by the next
occupant of that name — `SRV-FIX-247` and `SRV-FIX-256` are what that costs. A pid is unique for
the life of the kill and a lane has at most one live process.

## The kill grace is stepped, not slept

`git::killGroup` used to poll in 20 ms `sleep_for` steps for up to `KILL_GRACE_MS` (4 s) and then
`KILL_DEADLINE_MS` (2 s) more. It ran on `core::Worker`, whose pool is **two** threads, so two
concurrent force-releases occupied both and every offload behind them stalled: the catalog render
behind every page load, `lane::diff::read`, id allocation, and the reaper `finish_` posts — which
`settle_` then waits `SETTLE_TRIES × SETTLE_STEP_MS` for, gives up on, and forces `pid = 0` while
the process is still alive.

It is now `lane::Store::kill_`, a `core::Task` on `exec_`:

```
waited == 0                              signalGroup(pid, SIGTERM)
waited == KILL_GRACE_MS                  signalGroup(pid, SIGKILL)
every step                               groupIsEmpty(pid) ? done(true) : arm the next step
waited >= KILL_GRACE_MS + DEADLINE       done(false)
```

`git::signalGroup` is `::kill(-pid, sig)` and returns at once. `git::groupIsEmpty` reaps the
leader with a `WNOHANG` `waitpid` — which does not block — and then asks `/proc`. Nothing sleeps
and no thread is held; the wait is an asio timer like every other wait in this server.

Release and recover call `kill_` **before** their `core::Worker` hop rather than inside it, so the
Worker is only occupied while it is actually doing work: `remove_all` of the agent config,
`whatItHolds`, `removeWorktree` for release; `reCarry` for recover. Ordering is unchanged — the
kill still completes before the worktree is removed.

## What `reserve_` receives from its Worker hop

A claim may not touch the disk from the strand. `reserve_` used to call
`git::worktreeForBranch(root_, record.branch)` — `fork` + `execvp` + a blocking `waitpid` on
`git worktree list --porcelain` — directly on `exec_`, and `skipTaken_` stat-ed in a loop beside
it. One of four io threads was held for as long as git took, and every `list`, `release`,
`setTurn`, `noteOutcome` and `noteActivity` queued behind it. `SRV-FIX-108` measured this on
2026-08-07; `SRV-FIX-443` is the ticket that finally moved it.

The preflight hop that already existed now also returns a `Reservation`:

```cpp
struct Reservation {
    bool adoptable;        // the branch's registered worktree is one of ours, and it is there
    std::string worktree;
    int number;            // the first free lane number at or above `from`
    int from;              // the nextNumber_ the scan started at
};
```

`git::worktreeForBranch` is called **lexically inside** the Worker lambda, not from a helper the
lambda calls — `tools/conventions.py`'s `blocking-call-outside-a-worker` rule is lexical, and a
rule that cannot see the call is a rule that reports zero. `scanFor_` holds only the pure part:
the adoptability test and `firstFreeNumber`.

**The strand allocates the number; the Worker only proposes one.** Two claims in flight scan the
disk and get the same free number, and both are right — a lane is a checkout and nothing merges
into a live one, so a value two claims must agree on cannot be computed from what either of them
sees. The strand is the single owner. When it runs and finds `scan.number < nextNumber_`, or a
live record already carrying that name, it **re-posts the scan** rather than stat-ing; both checks
are in memory. `SRV-FIX-188` is the row this rule comes from.

`persist_` is the one exception and it is a decision, not an oversight: one small file write per
lane change, synchronous on the strand. It is not a subprocess, `blocking-call-outside-a-worker`
does not see it, and `SRV-FIX-443` §1 names it as an accepted risk.

## What may still block, and where

`tools/conventions.py` carries the whole list with a written reason each — see
`blocking-primitive-in-app` and `blocking-call-outside-a-worker` in
`tools/conventions-baseline.json`. In this module:

- `app/lane/git.cpp` — `exec_`'s blocking `waitpid` on the child it forked, reached only from a
  `core::Worker`; and `reapLeader_`'s `WNOHANG` wait, which does not block and is what lets
  `groupIsEmpty` run on the strand.
- `app/lane/store.cpp` — the reaper `finish_` posts, on a Worker and only after EOF on the child's
  pipe; `adopt_`'s `git::registeredWorktrees`, which runs from `init()` on the main thread before
  the engine pool exists.
- `app/lane/state.cpp` and `app/lane/command.cpp` — two `std::atomic` holding state no executor
  owns. `SRV-FIX-444` owns moving them.

## Which calls leave the queue, and which do not

The rule is **does it wait**, not is it a syscall.

Off the queue, always, onto a `core::Worker` via `exec_.offload`:

- a subprocess spawn — every `git::` call in `app/lane/git.cpp` that reaches `run` or
  `runQuiet`, closed transitively by `tools/conventions.py::subprocess_functions()`;
- the blocking `waitpid` in `finish_`, which waits for as long as the child takes to die;
- file I/O and directory walks — `git::presenceOf`, `git::whatItHolds`, `reCarry`,
  `fs::remove_all`, `agent::history::stagesByTicket`.

On the queue, synchronously:

- `git::isRunning(pid)` — `kill(pid, 0)` sends no signal. It is a permission check against the
  process table, it returns without waiting, and `subprocess_functions()` does not derive it
  (`test_conventions.py::test_the_blocking_set_is_derived_not_listed` asserts its absence). It
  is already called this way at three sites in this file, and `SRV-FIX-476` removed the three
  that offloaded it: `store.cpp`'s `releaseResource` and `startGate`, and
  `command.cpp`'s queued-lane refusal.
- `git::groupIsEmpty` — a `WNOHANG` wait, which is why `kill_` can poll it from a `core::Task`.
- `persist_` — one small file write per lane change, an accepted risk named in `SRV-FIX-443` §1.

**Offloading a call that does not wait is not free and not neutral.** It buys a thread hop and, in
the shape it was written in, a heap slot per value; and because the state is re-read on the queue
after the hop, the reply and the snapshot inside it described two different instants. A
`resource_busy` body carried `holder_alive` measured before the round trip and a `position` measured
after it.

## The result of offloaded work is the completion's parameter

`exec_.offload(work, done)` runs `work` on a Worker and `done` on `exec_`, with `work`'s return
value as `done`'s argument. Nothing in this module allocates a `std::shared_ptr` to carry a result
across the hop and nothing re-posts its own completion onto `exec_`: both were the price of
`core::Worker::post(task, callback)`, whose completion landed on the raw engine, and both are now
shapes `tools/conventions.py::manual_worker_hop` yields on. Where the work produces more than one
value it returns a struct — `ClaimScan`, `Worktree`, `Started`, `Exit` in `store.h`.
`design/core-queue.md` has the reasoning.

## The refusal on a passed ticket is decided in `api`, not here

`SRV-FIX-478`. `E12` refuses a claim on a ticket whose document says `status: pass`, and the
comparison lives in `app/api/_claim-lane.cpp` on the `catalog::Document` the endpoint already
loads — after the lookup answered `Found`, before `lane::Store::claim` is entered. **`lane::Store`
does not learn the word `pass`.**

Two reasons, and the second is the one that makes it a rule rather than a preference.

`pass` is a word about the target repository's WORKFLOW. `S1` gives every such name to `manifest`
and to the modules that compose above it; a store that queues, reserves and confines has no
business knowing which of a repository's status strings means finished. It is the same placement
`not_found` and `ambiguous_ticket_id` already have — both are decided in `api` off the catalog's
answer, and neither reaches this module either. `api` composes; `catalog`, `lane` and `delivery`
do not know about each other.

And the ordering **is** the guarantee `docs/product/SPEC.md` §1 states for this refusal: *no worktree, no
branch, no seat taken, no resource held, no row on the rail*. `reserve_` is where a claim starts
costing something — a request-id record, a seat, an exclusive resource, a `ticket/<id>` branch —
so a refusal written inside it would have to undo what it had already taken, and the undo path is
where `SRV-FIX-063` and `SRV-FIX-070` both lived. Refusing one frame earlier, in the caller, makes
"nothing was taken" true by construction instead of true by cleanup. `workspace_unrunnable` and
`no_git_root` are refused ahead of the reservation for exactly this reason.

The other half is `S5`, and it is what the refusal deliberately does **not** do. A pass written
while a lane is live changes nothing about that lane: it is still listed by `E9`, its worktree
stands, and only `E13` releases it. A refusal that also tidied up would take a running agent's
worktree out from under it — the 2026-08-06 shape, one door further in.

## A declared command is one of a closed set the tool names

`E19` takes three names and no fourth: `build`, `gate` and `review`. The set lives in exactly one
place — `manifest::Store::commandKeys()` — and `lane::command::start` reads it rather than testing
the name against literals of its own. **What each command DOES is the repository's**; the tool owns
the name, runs the string the manifest declared under it, and reports an exit code it has no opinion
about (`docs/product/SPEC.md` §10).

The set itself, and the table both accessors are derived from, belong to `manifest` and are
written down in `design/manifest.md`. What belongs HERE is the consequence for `lane`: this module
asks `manifest` which names exist and which of them this workspace declared, and holds no list of
its own.

`SRV-FEAT-500` added the third one, and the shape it had to fix first is why this paragraph exists.
`gate` and `build` were carried by two hand-maintained pairs — a `which != "build" && which != "gate"`
guard and a `which == "gate" ? gate : build` ternary — and a third name added to that shape is a
third branch that has to agree with the load-time check by hand. `docs/operations/ESCAPES.md`, 2026-08-22
(`SRV-FIX-191`/`SRV-FIX-195`): *the toolchain that builds is not the toolchain that gates* — a
prerequisite proven for one command and assumed for the rest. So the declared set is derived in one
function, `Workspace::declaredCommands()` walks it, `Workspace::command(key)` is the one lookup, and
`resolveCommands_()` — the load-time `command_unrunnable` check — has been quantified over that set
since `SRV-FIX-202`. A fourth command is a line in `commandKeys()`, a line in `command()`, and a
property in `docs/technical/contracts/manifest.schema.json`. It is not a fourth branch anywhere.

## One workspace's missing toolchain is not the manifest's failure (`SRV-FIX-491`)

A `toolchain` entry that is not on this machine invalidated the WHOLE manifest, so a workspace
nobody was claiming took every surface down with it. The instance a `factory-server` lane points
at its own repository is the case that made it matter: such a lane is given `~/.gitconfig` and
`~/.local/bin` and deliberately not `~/.nvm`, so `factory-client` is unbuildable inside it and the
tool refused to serve the repository it is a lane OF — `409` on the requirement surface, the ticket
surface and the lane rail, and tier 3 unreachable from the place every ticket is worked.

The fact moves to the workspace: `Workspace::buildable` is false and the load carries a
`workspace_unbuildable` warning naming the workspace, the declared entry, the path it resolved to
and the JSON pointer. Nothing is loosened — `E12` already asked *can this workspace build* at claim
time and answered `409 workspace_unrunnable` with `reason: toolchain_missing`, and it is now the
only place that refuses. A document that is not a manifest is refused exactly as before; that
distinction is the whole of the change and the mandatory negative drives three shapes of it.

The agent's credentials are the same question one key over. `agent.credentials_dir` is `~/.claude`,
and inside a lane `$HOME` is a tmpfs, so the declared directory is not there — while the credential
the lane really has was carried into `CLAUDE_CONFIG_DIR` by the tool itself. When this process is
in a lane (`FACTORY_LANE_HOME` is set) and a home-relative `credentials_dir` is not on disk, the
carried directory is the answer. Outside a lane, and for any credentials directory declared as an
absolute path, nothing changes: a declared directory that is missing is still `422`.

## A ticket names the workspaces its lane binds (`SRV-FIX-456`, `SRV-FIX-517`)

`docs/technical/contracts/http.schema.json` and `docs/technical/contracts/socket.schema.json` are read BY the client and changed BY server
tickets, and `test_schema_consumers.py` exists to force the consuming workspace's build and gate
the moment a `factory-server` ticket edits one. From a lane it could not: a `factory-server` lane
is bound `~/.gitconfig` and `~/.local/bin` and deliberately not `~/.nvm`, so the check a schema
change is supposed to pay for was one that only ever ran on a workstation.

**A ticket's `workspaces:` frontmatter line names the extra workspaces whose toolchain its lane
binds.** Absent means its own workspace only. The manifest gains nothing — `api` reads the key off
the ticket document it already has in hand at claim time and passes the names down; `lane` unions
those workspaces' toolchains at claim and again at spawn, from the one function that answers *what
does this lane hold*. `docs/technical/contracts/manifest.schema.json` documents the key, because the schema is where a
reader looks for what a workspace name may appear in.

**The lane says what it holds in the tool's own log**, not in a response: §1 widens no surface, and
the sentence a session needs is the one that distinguishes *this gate is not mine to run* from
*this gate is broken*. `npm: command not found` is the second sentence for the first situation, and
three sessions debugged the wrong thing on it.

## How far a lane is, and how long it has left (`SRV-FIX-599`)

A lane's position is **derived at render time**, from the ticket's stage history and the ticket's
own sequence, not stored on the lane record. The record used to carry `stageIndex` and
`stageRound`, written when `E22` accepted a report: a lane claimed after a restart therefore read
`0` while the ticket's history said `REVIEW`, and the index was a position in the ticket's sequence
divided by the MANIFEST's length. The snapshot now carries `stages_` itself, and `api::laneProgress`
turns one catalog listing, the manifest and that history into `{stage, progress_pct,
remaining_s}` per ticket — the one function `E9`, `E12`'s `201`, `M3` and `M4` all call, so no two
surfaces can compute the bar differently. `lane` never reads the catalog; `api` composes.

**Done is a word the manifest declares** (`status.done`), never a word the tool knows. A ticket at
one of them is `100` and `0` left; without the declaration nothing is done.

**The estimate is measured or absent.** A sample is the time from a stage entry to the next one of
the SAME RUN, or to `ended_iso` when it was the last entry of an ended run — so a run that ends at
`QA` still measures `QA`. A stage reported last in a run that never ended is not a sample, whatever
comes after it: every report carries its `runId` (the transcript's run when seeded, the token's run
when reported live), because a ticket's history concatenates its runs and two lanes of one ticket
interleave theirs. QA round 1 (`SRV-FIX-599-qa.md` F1) is what that cost: closing an entry with its
neighbour in the ticket's history turned the gap between two runs, or another lane's report, into
a duration nobody measured. Per
stage ahead: no samples skips it, one or two make the answer `null`, three or more add the lower
median. The skip is the operator's decision during SPEC: in `example-platform` 0 of 318 runs reported
a stage after `QA`, so requiring three samples for every stage ahead answered `null` for every
lane. Every stage ahead skipped is `null`, not `0` — nothing measured is not nothing left.

Live reports are stamped with `state::nowMs()` when `exec_` records them; seeded ones with the
transcript entry's `ts`. `M4` carries `remaining_s` so the client counts it down on the clock it
already ticks `elapsed_s` with, and `M3` is published for every lane of a ticket whose document
changes, which is how a status set to done reaches a card without a reload. Reading the catalog
is disk work, so every one of these is computed on `core::Worker`, never on `exec_` or an I/O thread.
