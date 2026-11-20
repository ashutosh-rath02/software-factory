# `manifest` — the only module that knows a target repository has a shape

`workspaces.<name>.start_preflight` is an optional E18 readiness object, separate from the
closed E19 command table. Its required nonempty `command` is shell text; optional
`timeout_seconds` is an integer 1–60 (default 15), and optional `exempt_ticket_ids` is an array
of exact nonempty ticket IDs. `resolveCommands_()` checks the command's program reachability
with the same warning as build/gate/review. The manifest declares repository policy; E18 runs
the command inside the selected profile's lane boundary on every start. No provider call or
run ledger is created for a failed check.

`S1` is one sentence: *everything repository-specific enters through `.factory/factory.json`*.
Below this module, no path, workspace, stage, role or resource name means anything to the tool.
`test_manifest.py::test_no_module_but_manifest_knows_a_path` is that sentence as a grep over
`app/`.

## A workspace's commands are ONE table

`build`, `gate` and `review` are a closed set of NAMES the tool owns, and the only thing it owns
about them: the string under each is the repository's, what it does is the repository's, and its
exit code is reported without judging acceptance (`docs/product/SPEC.md` §10). E33 routing
compares that code only with the caller's explicit accepted exit policy; it does not infer quality.

The set lives in exactly one place — `kCommands` in `store.cpp` — and everything else is derived
from it:

| derived | is | used by |
|---|---|---|
| `Store::commandKeys()` | the names, in table order | `lane::command::start`, to refuse a fourth name and to list the three in `detail.declared` |
| `Workspace::command(key)` | the declared string, or `nullptr` | `lane::command::start`, to answer `404 not_declared` |
| `Workspace::declaredCommands()` | one entry per key this workspace declared | `resolveCommands_()`, the load-time `command_unrunnable` check |

**A fourth command is one row in that table and one property in `docs/technical/contracts/manifest.schema.json`.** Nothing
else. That is the whole point, and it is a reaction to a specific failure rather than a preference:
before `SRV-FEAT-500` the three derivations were three hand-maintained lists — a
`which != "build" && which != "gate"` guard in `lane`, a `which == "gate" ? gate : build` ternary
beside it, and the pair in `declaredCommands()`. `docs/operations/ESCAPES.md`, 2026-08-22
(`SRV-FIX-191`/`SRV-FIX-195`): *the toolchain that builds is not the toolchain that gates* — a
prerequisite proven for one command and assumed for the rest, which is what separate lists produce
every time.

`resolveCommands_()` has quantified over `declaredCommands()` since `SRV-FIX-202`, so a command
added to the table is checked at load on the day it is added and not on the day somebody remembers.
`test_manifest.py::test_review_is_checked_at_load_with_build_and_gate` asserts the other half from
the source: `app/` holds exactly ONE site that can emit `command_unrunnable`.

## What the load-time check can and cannot see

It resolves the PROGRAM a declared command names, in the three places a lane is given — `/usr`,
the workspace's `toolchain`, the repository — and warns rather than refusing, because the line that
answers such a warning is written by a person and a tool that refuses until it exists cannot be
started (`SRV-FIX-202` round 2).

It cannot see whether the RUN will work. `SRV-FIX-221` is a command whose first word resolves and
whose own tooling is not in the lane; `SRV-FIX-505` is a program that resolves as a symlink whose
target a lane may not read. Both are open, and both are the same limit: the check is at the
granularity of the word, and running is at the granularity of the process.

## Local routing runtime path

`Data.routingDir` is joined here beneath the existing `runsDir` as `.factory/runs/.routing`.
It is internal runtime state with the existing ignored run history, not another manifest field or
repository document directory. The routing component receives the resolved path and invents no
repository root. E33 derives command names and declarations from the same table as E19.

## Agent-tree presets (SRV-FEAT-573)

The optional `trees` object declares named selections and `default_tree` selects one at start when
E18 omits `tree`. Each selection stores a `rules` decider and agent names. The loader refuses an
unsupported decider with its JSON pointer because the runtime schema checker does not implement
`enum`. A manifest without `trees` retains the implicit `off` selection.

A preset may also declare `continuation: goal`, `compact_on_stage: false`, and
`token_budget` as a positive integer. The loader refuses any other continuation value or
`compact_on_stage: true` with the field's JSON pointer. The latter is deferred to SRV-FEAT-596;
an explicitly false field is retained in E18 and run history but has no runtime effect.
The selected preset, including these optional fields, is retained in run history. Provider goal
and compatibility decisions belong to `agent`; the manifest owns their declaration.

Run history persists provider token totals under `usage`: `input_tokens`, `cached_input_tokens`,
`output_tokens`, and `reasoning_output_tokens`. E7 and the stored meta derive `effective_tokens`
as input minus cached input plus 0.1 times cached input plus output. Each notification replaces
the totals; later metadata writes, branch reference updates and orphan settlement preserve them.

## An effort no profile can honour is refused where it is written (SRV-FEAT-574)

Four optional keys carry reasoning effort: `agents.<p>.efforts` and `agents.<p>.default_effort` per
profile, `roles.<r>.effort` per role, and one top-level `run_effort` — the role-level value for
`run`, which is one key here rather than a `roles.run` entry the tool would then have to treat
unlike every other role, because `F31` gives a role a lane and a prompt and `run` has neither.

The values are the PROVIDER's words and the tool holds no list of levels: `claude --help` prints
five, and the codex app-server's `ReasoningEffort` is a non-empty string with no enum at all. A
level set written into this module would be wrong for one provider the day either changes.

Three faults are refused at LOAD, each naming its own JSON pointer:

- `default_effort` outside its own profile's `efforts` — a default that could never be selected;
- `run_effort`, and
- `roles.<r>.effort` outside the `efforts` of a profile that declares some.

The last two are refused here rather than at the start because of who the caller is. `api/roles.cpp`
turns any start refusal into a `LOG(WARNING)` and a released lane: a role has no screen to be
refused on, so an effort no declaring profile could honour would become exactly the silent drop to
the provider's default that this feature exists to prevent. A profile declaring **no** `efforts` is
exempt from all three — it has opted out, and a repository must still be able to run it.
