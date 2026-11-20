# `app/agent` — supervising a run

## Preset goal and usage (SRV-FEAT-593)

A Codex run reads its selected tree preset at start. After the app server returns a thread ID,
the run sends one `thread/goal/set` before its initial turn. The objective names the ticket and
independent QA handover; a declared positive token budget travels in the same request. Later
turns in that run do not reset the goal. Stage compaction is deferred to SRV-FEAT-596;
`compact_on_stage: true` is refused when the manifest loads.
E18 waits for a successful goal response confirming the same thread and active status before it
answers `201` or starts the initial turn. A rejected or malformed goal response fails startup and
terminates the process; a recorded app-server rejection drives that negative test.

The transcript parser recognizes goal-status and cumulative token-usage notifications as control
frames, scoped to the run's thread ID. Blocking goal statuses reach lane state through an API
callback; active and complete clear that reason. Each usage update writes the four provider totals
to the run ledger, so an E7 row can compute effective tokens from the latest totals after lane
release or reuse. The ledger, not the disposable transcript, owns those figures. A Claude start
with a preset declaring continuation is refused before preflight or spawn.
Provider-started `turn/started` notifications for the run's thread activate the turn and publish
that state, so messages are held and interrupt addresses the provider's current turn ID.

## Completed role turns (SRV-FIX-586)

A normalized completed-turn report carries the emitting run ID and role. API uses that identity
to record the role branch's committed tip while the process stays available for another turn.
A `run` role's completed turn is not independent verdict evidence. Later run metadata updates
retain any branch and tip already recorded for the session.

## E18 start preflight (FAC-EPIC-378)

An optional workspace `start_preflight` is repository policy, run once on every E18 start
(including resume) and role auto-start. It runs after ticket, workspace, and selected agent/model
are known and before `agent::Store::startRun` creates a run record or provider process. It executes
through the lane's actual confined process path using the selected profile. The command sees only
`FACTORY_TICKET_ID`, `FACTORY_WORKSPACE`, `FACTORY_AGENT_PROFILE`, and `FACTORY_AGENT_MODEL` in
addition to the normal confined environment. An exact ticket-ID exemption skips the command.
The API owns a per-lane pending reservation spanning prompt lookup and preflight; a concurrent
start returns `409 start_preflight_running`. Nonzero exit or spawn refusal returns
`409 start_preflight_failed`; timeout returns `504 start_preflight_timeout` only after the child
process group is killed and its leader reaped. Output is serialized to a full capture below the manifest's
`runsDir/.start-preflight/`; errors include its path and bounded inline output with a truncation
flag. A refused start writes no run ledger entry. The check receives the selected profile's
private config seed and environment through the same lane launch path as the eventual agent.
Pending, failed and timed-out responses offer the existing explicit `retry` remedy. Reissuing
the original request still enters the same reservation/readiness path; it never interrupts the
held check or creates a second provider run. Only a successful readiness check admits the retry.
The lane also holds its identity during that pending interval, so even a forced release cannot
reclaim its name for another ticket before the agent start is answered. API releases the lane
hold before returning a refusal; automatic roles can then abandon their lane. On server stop,
an atomic cancellation flag prevents offloaded confinement setup from spawning a late process;
the stop path signals already published preflight process-group IDs synchronously because the
engine stops its event service immediately after component stops return.
The start map and timer still belong exclusively to `api::Manager`'s queue. Only shutdown
cancellation crosses that executor: lock-free boolean/integer atomics let an offloaded spawn
observe stop and publish its PID, while an atomically published immutable shared-pointer snapshot
lets synchronous `Manager::stop()` signal every active preflight without reading the queue-owned
map. Publication occurs only at reservation/release; no handler waits for a worker, mutex or
condition variable. The worker checks cancellation before and after spawn so neither ordering
between stop and PID publication leaves a child alive. This hook is a readiness check, not a run
or one of E19's three declared commands.
The lane reaper uses `waitid(..., WNOWAIT)` for preflight leaders, then kills any remaining group
members and clears the published shutdown PID while the exited leader still reserves that PID.
Shutdown first atomically claims a positive PID by changing it to its negative; the reaper waits
for that one in-flight signal before `waitpid` reaps. If the reaper clears first, shutdown's claim
fails. This closes the read-before-clear gap even when the snapshot remains alive during Codex
startup, without blocking an event handler.

## Named drivers (SRV-FEAT-558)

The manifest resolves named profiles and their model allowlists before launch. The
legacy singular agent remains a Claude profile named `default`. Profile names are
restricted to letters, digits, underscores and hyphens because they namespace the
lane's private configuration directory. The selected profile supplies its own
executable, credentials and start prompt; profiles never share session IDs.

Codex uses `app-server --listen stdio://`. Initialization precedes thread creation
or resume, and E18 does not answer 201 until the thread ID is known. A 30-second
startup deadline or RPC initialization error fails the start and terminates the
child. Thread and turn IDs are independent. A held message starts only after the
previous turn completes; interruption is a turn RPC, not a signal to app-server.
Ending drains held turns before closing stdin; kill retains process-group semantics.

`transcript` decodes vendor frames into entries/control events. API composition
routes normalized approval requests to the permissions store; the agent component
does not depend on that sibling. Unknown server requests fail closed. Codex runs
inside the same outer confinement as Claude, with its own workspace-write sandbox.
Only auth.json is borrowed; the developer's config and permissions are not copied.

Protocol reference: <https://learn.chatgpt.com/docs/app-server>. Installed CLI
generated schemas and real captures are the conformance evidence; deterministic
peers test lifecycle failures without consuming model calls.

`tests/fixtures/cli-app-server-codex-2026-09-18.json` preserves both directions of
the installed app-server's real exchanges against a synthetic repository. Re-capture
with `tests/capture_codex_protocol.py`, supplying the executable, declared model,
authentication directory and output path explicitly; ordinary tests never call the
provider. Its create, update and delete operations establish the parser's file-change
contract: one completed `fileChange` item can produce several `file_edit` entries,
one per path. Add/delete diffs carry plain content; update diffs carry unified hunks.
Counts come from those bytes, never from the current filesystem. Only the completed
status reports success; failed/declined edits remain visible with `ok: false`.
Started notifications do not duplicate the completed entries. File edits also update
the existing last-words channel with their tool and path.

Factory confinement is the outer filesystem and network boundary for Codex, so app-server receives
`danger-full-access` only to disable its redundant inner sandbox. The profile's declared permission
mode remains independent: `edit_free` becomes app-server `approvalPolicy: never`; explicit `ask_all`
becomes `untrusted`, so mutating shell commands reach Factory's permission decision surface even
without the inner sandbox. An absent mode keeps `on-request`. A linked worktree is Codex's project identity, so
the lane boundary regenerates `config.toml` for the exact checkout rather than trusting the developer
checkout. E18 prompt text is otherwise opaque: only an all-whitespace value means no initial turn;
leading and trailing bytes on a real prompt are preserved.

`api` registers one worktree-specific configuration preparer with `lane::Store`.
The store calls it after resolving the profile and final checkout, for every process launch.
The callback delegates to `agent::launch::configSeed`, the sole owner of configuration syntax;
the lane receives opaque generated and borrowed file lists. Agent-session requests do not carry
a second seed made for the operator checkout, and neither sibling reaches into the other.

### Cold declared commands

E19 uses the manifest's default profile, not whichever profile last ran through E18. Its shell
command remains the exact declared string, including any explicit configuration environment.
Both paths pass through the same configuration preparer before confinement spawns the process.
A command therefore needs no previous agent run to furnish its generated trust and selected
borrowed file. Recovery's borrowed-file refresh also delegates to `configSeed`.

Preparation does not add mounts: only the existing selected profile directory is projected at
`/factory/agent-config`, with its legacy singular location retained where applicable. Generated
files remain seed-once, and command-created local state survives repeated commands. Startup still
sweeps configuration owned by a dead server; a recovered lane's next command prepares it again.
Other profiles and undeclared host configuration remain unreachable, and actual command output
and nonzero exit codes remain facts, not a clean-review verdict.

Decisions behind `app/agent/{session,launch,history,proc,transcript}.{h,cpp}`, moved out of the
code by `SRV-FEAT-232`.

## Nothing blocks, including the things that look like they must

A full pipe would stall one of the four io threads and everything queued behind it
(`core/engine.h`), so stdin is written with an async write and never a blocking one — now
`core::Stream::write`. The kill grace is a timer and never a sleep. The two things that really
block — building the argv and reaping — go to a `core::Worker`.

## The FIFO, and why stdin is framed

E18's prompt is a message like any other. Once the input is framed, the CLI answers no prompt given
in the argv — measured; it does not complain either, it simply never replies — so the prompt goes
down the run's stdin first, ahead of anything E14 can add. `turnActive` is derived from those same
bytes, so the two cannot disagree.

A write that fails means the child is gone. The outbox is cleared and the outcome is left to
`noteExit`, which is the one place a run settles.

## The kill grace

`SIGTERM`, then a timer, then `SIGKILL` — and the timer checks that the run it is about to kill is
still the same run at the same pid. A lane that started something new in the meantime must not be
killed by the previous run's grace.

`SRV-FIX-034` is why the reap is asserted on the process table rather than on `pgrep`: `&` in a
shell returns at `fork`, and until `execve` the child still carries its parent's command line, so a
`pgrep` that "proved" a process had started could not see it.

## `crashed` and `killed by you` are the tool's own words

They are what this module WROTE when it reaped the process, not an inference from an exit status.
Everything downstream — `catalog/render`'s process axis, §3.2 — is a lookup in that vocabulary
rather than a second opinion. See `docs/design/catalog-rendering.md`.

## Three events, and they append

`onRun` / `onEntry` / `onTurn` were plain `push_back` with no executor guard, in a class whose
siblings guarded. They now live on the component's `core::Queue` like everything else. Registration
happens at construction, before the pool exists, so the ordering is fixed by construction rather
than by assumption.

## The transcript has exactly one writer

The run's log has one writer because two would interleave, and `seq` is assigned in one place. A
request that lives only in a frame which has already gone past is a request a page reload loses,
which is why the entry is appended to the file as well as published.

Replay resolves across the same ledger collection that feeds E7 and E31. When a run's record
survives but `transcript.jsonl` has been deleted or renamed, the unsuccessful replay retains the
path from the ledger that owns that record; folding several unsuccessful probes into a default
answer must not erase the only diagnostic E8 can give the operator.

## Last words — derived in the parser, coalesced in the store

`SRV-FEAT-454`. `lanes[].last_words` (E9) and the `lane.words` frame (M18) say what the agent in a
lane last SAID or last CALLED, as one line. Two decisions are worth writing down, because both
could plausibly have gone somewhere else.

**The derivation lives here, in `transcript`, and only here.** `docs/technical/MODEL.md` S6 says the CLI's frame
format is parsed in this one module and everything crossing into `api` is the tool's own
vocabulary. Deriving the line anywhere downstream would mean a second module knowing what a
`tool_use` block is; deriving it from the rendered entry JSON would mean parsing the tool's own
output back out of a string it had just written. So `Entry` gains `wordsKind` and `words` beside
`hasCost` and `sessionCostUsd`, `session` carries the pair to the lane store exactly as it carries
`costUsd`, and nothing under `app/lane` or `app/api` ever opens a CLI frame.

The three kinds are the agent's own utterances: `prose`, `tool`, `file_edit`. `tool_result` is what
the tool said BACK, and `turn` and `error` are the tool's own bookkeeping, so a `tool_result` block,
a `result` frame and an unparseable line all leave the line where it was. That is why the flag is
set in the two producing branches rather than cleared in the others — a rule written as an
exclusion list acquires a hole every time a kind is added.

**The coalescing lives in the store, not in the parser and not in `api`.** The rate is a property of
a LANE — several entries a second at peak, read by a person once every few seconds — and the lane
is what `lane::Store` keys by, so the debounce sits beside `silence_` on the same executor and dies
with the lane in the same place. It is leading-edge with a 2 s cooldown and a trailing emit: the
first entry after a quiet moment moves the line at once, and a burst produces one more frame
carrying the last value rather than one per entry. A pure trailing debounce was rejected because an
agent that never stops emitting would never emit a frame at all — the line would go quiet exactly
while the lane was busiest.

`at` is stamped in the store from `state::nowMs()`, which is the clock the test hook advances, so
the ordering a case asserts is the ordering the product would produce. It is not persisted: after a
restart a live lane reads `null` until its next entry, and that is the same answer a new run gives,
which is the one `setRun` clears.

## The run record names the ref the session left (`SRV-FIX-450`)

`meta.json` carries `branch` and `branch_tip`: the branch that session held and the tip it left it
at. They exist for one consumer — `land`, which cannot decide from refs whether a second session
passed a ticket, because a run lane can create any ref a role lane would.

`agent` does not compute them. It knows a run's LANE and nothing about worktrees or branches, and
reaching into `lane` for them would be a sideways include. `api` composes instead: it already
listens for a run's outcome and each completed role turn. For a non-`run` role, it asks
`lane::Store` for that lane's branch, resolves the tip, and merges the pair into the record through
`history::noteRef`. Both that merge and every run metadata write use the worker's serialized
executor strand. The read-modify-replace operations therefore cannot overlap or overwrite each
other's cost or branch fields. A completed turn requests a delivery pass after the receipt is
written; the role process remains live.

Receipt persistence can finish after the last branch commit. API's completed delivery snapshot
subscription re-reads these tool-owned records on the existing bounded scan interval, and the
lander includes their acceptance fields in its refusal fingerprint (SRV-FIX-571). A receipt-only
change therefore reconsiders a refusal without restarting the session or changing a Git ref.
The receipt still has to identify the independent session's own committed PASS.

## Selected tree (SRV-FEAT-573)

E18 resolves a declared preset before the readiness subprocess. The provider file convention is
owned by `agent::launch`: a Claude profile needs `.claude/agents/<name>.md`, and a Codex profile
needs `.codex/agents/<name>.toml` in the selected lane checkout. The check runs on a worker and
refuses before any process starts. A symlink at the provider directory, agents directory or file
is refused because a host-visible target outside the lane does not make an agent file reachable
inside the lane. A run stores the effective selection in its own metadata, so
history remains stable after a manifest edit or lane reuse. The process environment receives the
selection; older manifests receive only `FACTORY_TREE=off`, with inherited
`FACTORY_TREE_AGENTS` and `FACTORY_TREE_DECIDER` removed from the child environment.
## Reasoning effort is resolved once, where the argv is built (SRV-FEAT-574)

`CLAUDE.md`'s model rule said main and QA run at high effort and nothing the tool launched carried
one, so the rule was a sentence. The effort now travels the same route the model does, with one
difference that decides the design: **the lane's role has to be known before the argv exists.**

`launch::build` runs inside `startRun`, and `run->role` is set from the SPAWN RESULT — after the
argv is already built. So `StartRequest` carries `role`, set by `api/_start-run.cpp` from
`gate.lane.role` and by `api/roles.cpp` from `start.role`. It is a string this module compares and
never interprets, so `S1` is untouched: the factory still attaches no meaning to a role's name.

`launch::resolveEffort` is the only place the order is decided — request, then the role
(`roles.<r>.effort`, or `run_effort` when the role is empty or `run`), then the profile's
`default_effort`, then nothing. A profile declaring no `efforts` answers nothing at every step: it
has opted out, and that is what every repository did before the key existed.

Each provider's own interface carries it and nothing is mapped between them:

- Claude takes `--effort <level>` on the argv, beside `--model`, on a fresh `--session-id` session
  and a `--resume`d one alike.
- Codex takes it on **every** `turn/start` and on neither `thread/start` nor `thread/resume`. That
  is not a choice: of the three, only `TurnStartParams` has an `effort` in the installed
  app-server's schema. Because `write_` is the one path every message takes, the turn after a
  resume carries it without a second code path.

**The refusal is the tool's because it is nobody else's.** The installed `claude` answers
`--effort bogus` with a warning on stderr, exit `0`, and runs at its default — recorded in
`tests/fixtures/cli-effort-2026-10-10-claude-drops-an-undeclared-effort.json`. An undeclared
effort is therefore `400 invalid_agent_selection` at E18 before the spawn, and never a quiet drop.

The run remembers what was SENT, so `meta.json`, E7 and E31 report `effort` or `null` — `null`
being both a profile that declared none and every record written before the field existed.
`history::Meta` needs `hasEffort` for that, because `stringAt` collapses a JSON null to `""`.
Nothing is keyed by lane name, so a released and recycled lane name cannot hand one run's effort
to another.
