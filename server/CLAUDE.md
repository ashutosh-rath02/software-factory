# factory/server — the C++ half

C++17 on `core/`, event-driven `boost::asio`. Reads a target repository's documents, owns lanes and
the agent processes in them, and serves HTTP + `/ws` on **:7777**.

```bash
./build.sh build                 # cmake + make
./build.sh run [repo-root]       # given a repository, never discovers one
pytest tests/test_<area>.py -v   # tier 1/2 — what a ticket runs
pytest tests/ -v                 # tier 4 — RELEASE only, ~16 min
pytest tests/ -m repository      # the document gate, after a merge
./tools/conventions.py           # CONVENTION stage — reads the diff, not the behaviour
```

Generated files live under the checkout's `.cache/server/`: `build/` for the release binary and
compile database, `build-tsan/` for ThreadSanitizer, and `pytest/` for pytest's cache. Each checkout
owns its cache; the source directory stays free of these generated folders.

The root test runner and soak runner put Python bytecode under `.cache/python/`. For direct Python
commands from this directory, set `PYTHONPYCACHEPREFIX="$PWD/../.cache/python"` to use the same
location. The shared IDE settings hide bytecode caches created by commands without that variable.

Ticket prefix: **`SRV-*`**. Tickets and reports live in the repository root's `docs/`, because a
requirement's children must sit in the same repository as the requirement — asserted by
`test_a_parent_names_a_requirement_in_its_own_repository`.

## Modules, and which way the arrows point

```
api ──▶ catalog ──┐
 │                │
 ├───▶ delivery ──┼──▶ manifest ──▶ core
 ├───▶ lane ──────┤
 └───▶ agent ─────┘
```

Nothing under `app/` may be included by `core/`. No module reaches sideways into a sibling's
internals — `api` composes, the others do not know about each other.

| module | owns |
|---|---|
| `manifest/` | read and validate `.factory/factory.json`. **The only module that knows a target repo has a shape** (`S1`) |
| `catalog/` | epics, tickets, reports, read off disk on every request from the paths the manifest gave |
| `delivery/` | what git and the process table say became of a ticket's work |
| `lane/` | worktrees, branches, the exclusive-resource queue, confinement |
| `agent/` | supervise a process, own its stdin/stdout, parse the transcript |
| `api/` | HTTP and WebSocket surface, one action per `_<action>.cpp` |

## The rules that are not style

- **NEVER block a handler thread.** The engine runs a pool on one `io_context` — four by default —
  so handlers run concurrently and *"the engine thread"* is not a thing. Long work goes to an async
  worker. That precondition was missing for five tickets and the rule read as though a
  single-threaded reactor made synchronisation unnecessary; see `docs/operations/ESCAPES.md`.
- **NEVER add a mutex to event-driven code.** asio's answer to handlers that share state is an
  **executor**: give the state its own `strand` and bind its handlers to it.
- **A component has exactly one `core::Queue`, and every timer it owns is built on it.**
  `core::Task` takes the queue, so there is nothing to remember: `core::Component` has none and
  the wrong binding does not compile (`SRV-FIX-443`).
- **One action per `_<action>.cpp`.**
- **No comments in production sources.** The reasoning goes in `design/<module>.md` —
  `SRV-FEAT-229` and `SRV-FEAT-230` are the pattern, and `tools/conventions.py` enforces the
  absence.
- **A convention the compiler or the constructor can refuse beats one a script can grep.**
  `file::Watch::add()` after `start()` is `LOG(FATAL)` because a grep rule for the same defect
  reported zero while the defect was live.

## `design/` — the decisions, kept out of the code

`core-task.md` · `core-file-watch.md` · `catalog-rendering.md` · `ticket-ids.md` ·
`permissions.md` · `agent-session.md` · `lane.md` · `lane-confinement.md` · `lane-recovery.md` ·
`filesystem-refactor-workflow.md` · `remedies.md`

A ticket that changes one of these modules updates its design note in the same commit. A note that
drifts from the code is worse than no note, because the next reader believes it.

## Two live hazards

- **The id ledger is machine-local.** `.factory/ids.json` is gitignored, so on any other clone
  `ids.cpp` restarts the sequence from the highest id on disk. `docs/tickets/_HIGH-WATER.md` is the
  portable copy and records the night five ids were issued two or three times. Fixing it properly
  changes what `SPEC.md` §2 says the tool may write, so it is a specification change.
- **A lane holds resources `git status` cannot see.** Only `GET :7777/api/lanes` knows whether an
  agent is running in a worktree. Release through `DELETE /api/lanes/<lane>?force=true`.
