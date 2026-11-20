# REVIEW — what a review of this repository reads, and how it ranks what it finds

[Documentation](../../INDEX.md) / [Technical](../README.md)

`REVIEW` is the seventh stage of both stage sequences, and since `SRV-FEAT-500` it is a **command
the tool runs**: each workspace declares one beside its `build` and its `gate`, `E19` starts it in
the lane, and its output arrives on `/ws` as `command` frames like any other. This file is the
command's instructions — the passes it makes, the severities it ranks by, and the diff it does not
read.

**Findings do not approve and do not block.** The tool reports an exit code and has no opinion
about it (`docs/product/SPEC.md` §10); the verdict on a ticket is `QA`'s and nothing here changes that.
A review is a second reader of the diff, and a second reader is worth having precisely because it
is not the gate.

The declared command selects its reviewer. `.claude/scripts/review.sh [base]` retains its legacy
Claude default; `FACTORY_REVIEW_DRIVER=codex` selects non-interactive Codex, and
`FACTORY_REVIEW_MODEL` supplies the model explicitly. The manifest owns that selection, not the
server or the UI. A confined declared command also sets `CODEX_HOME=/factory/agent-config` to use
the configuration already projected for the selected profile; it does not mount another
provider's credentials or weaken confinement. Outside a lane, use the existing Codex configuration
or supply its directory explicitly.

The Codex adapter runs `exec --json` with a read-only sandbox and `--ephemeral`, passes the review prompt
on stdin, and parses only `--output-last-message`'s file. Progress stdout is not a finding and a
missing final file is `unreadable_answer`; an explicitly empty file is a completed review with no
findings. Diagnostics remain on stderr and the reviewer's non-zero status is preserved. The
[official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
describes that output-file interface. The recorded real-program check is reproducible with
`python3 server/tests/capture_review_codex.py --model <declared-model>`; it reviews only synthetic
instructions and a throwaway mutex diff, sends no private repository content, and records findings
without altering the reviewed checkout. Reviewing a real ticket still sends that ticket's diff
and the review instructions to the configured provider and requires the operator's authorization.

---

## 1. The passes

A review makes four passes over `git diff <base>...HEAD`. Each one has a document it reads the
diff against, and a finding cites the file and line in the diff — never a general observation.

### Pass 1 · Seams — `docs/technical/MODEL.md` §2

The seven seams are what a ticket may not contradict or extend. Read the diff against all seven,
and above all these three:

- **S1 · Shape.** Everything repository-specific enters through `.factory/factory.json`. Below
  `app/manifest`, no module may name a path, a workspace, a stage, a role or a resource. A new
  literal `"server"`, `"GATE"`, `"qa"` or `"board-master"` outside that module is a finding.
- **S4 · Reachability.** A host path is reachable inside a lane iff the lane bound it in. A change
  that widens what a lane can read, or that adds a toolchain a declared command needs without
  declaring it, is a finding.
- **S6 · Wire.** Every crossing is declared and the declaration generates the code. A hand-edited
  `client/web/src/types/api.ts` or `socket.ts`, or a response field the schema does not describe,
  is a finding.

### Pass 2 · The four ways a check lies — `docs/tickets/_TEMPLATE.md` §2

Read every test the diff adds against the four, because a check that lies is invisible to every
other gate in this repository:

1. **A set somebody typed** where the set should have been derived from the system — a literal
   tuple of command prefixes, document paths or endpoint names.
2. **A test written after the code**, which can only agree with it — and, worse, a test whose
   helper does the thing the ticket is about, so the helper is what is under test.
3. **A sweep that swept nothing** — any case that iterates and does not assert its collection is
   non-empty.
4. **A proxy instead of the artefact** — a model of a boundary rather than the boundary, a
   stand-in nothing recorded, one screenshot standing in for every surface.

### Pass 3 · The server's architecture rules — `server/CLAUDE.md`

- a handler thread is never blocked; long work goes to an async worker;
- no mutex in event-driven code — state shared between handlers gets a strand;
- one action per `_<action>.cpp`;
- nothing under `app/` is included by `core/`, and no module reaches sideways into a sibling;
- no comments in production sources — the reasoning belongs in `server/design/<module>.md`, and a
  ticket that changes a module updates that note in the same commit.

### Pass 4 · The client's two standing rules — `client/web`

- **Types are generated.** `npm run gen:types`, `gen:api-types` and `gen:tokens` own
  `src/types/socket.ts`, `src/types/api.ts` and `src/styles/tokens.css`. A hand-written line in
  any of them is a finding, and so is a colour written anywhere but `client/web/design/`.
- **The client never polls.** HTTP loads a surface; `catalog.changed` reloads exactly the document
  it names. A timer-driven fetch or render is a finding — `no-poll.test.tsx` exists because this
  one keeps coming back.

## 2. The two severities

They are `CLAUDE.md`'s, quoted, because a review that grades on a scale of its own is a review
nobody can act on:

| severity | what it means |
|---|---|
| `blocker` | a person cannot do the thing §1 exists for — or the change loses work, breaks a surface that already worked, or is unsafe |
| `minor` | real, worth fixing, and a person can still do what the ticket exists for: cosmetics, an internal contradiction nobody reaches, a sharper test, a gate that could see more |

A `blocker` is fixed before the handover to QA. A `minor` is filed as its own ticket, with an id
from the tool, in the same commit as the merge — an unfiled minor is a blocker.

## 3. The exclusions

A review that spends its findings on these is a review nobody reads twice.

| excluded | why |
|---|---|
| generated files — `client/web/src/types/*.ts`, `client/web/src/styles/tokens.css` | they are output. The finding belongs on the schema or the design that generated them, and a hand edit is Pass 1's S6 finding rather than a style note |
| `docs/reports/` and everything under it | reports and their committed logs are a record of what happened. They are appended to, never corrected |
| `docs/operations/ESCAPES.md` rows already in the base | every branch appends to one table; the rows a branch did not add are not its diff |
| whitespace-only and pure-move hunks | the diff shows them; a person reading the finding cannot act on them |

The runner supplies a complete changed-file inventory and, when it fits the prompt bound, the
full diff outside `docs/reports/`. It lists omitted report and log changes explicitly and leaves
their files on disk. If the actionable diff would exceed the conservative 900,000-byte prompt
bound, the runner says so before starting the reviewer. It sends the same instructions and
complete inventory, then requires the reviewer to inspect every actionable changed path through
read-only `git diff --no-ext-diff <base>...HEAD -- <path>` in the checkout. This changes transport,
not the four passes or the set of files they cover. Production, tests, process documents and
generated files all remain in review scope; the other exclusions above govern findings rather
than removing changed files from scope. Evidence can be read by repository-relative path when
needed. If even the instructions and complete inventory exceed the bound, the runner fails locally
without starting a reviewer. A diff containing only reports returns `nothing_to_review` without
starting a reviewer. The prompt identifies the task as read-only REVIEW, and the reviewer reads
the cited policy sections, searching for relevant sections before opening whole documents; it does
not start ticket implementation, QA, or reporting. The runner works from the repository root even
when the command starts in a nested workspace.

## 4. What a finding looks like

One line each, so a report can be grepped and a person can sort:

```
blocker server/app/lane/command.cpp:63 the enum lists two names where the manifest declares three.
minor docs/technical/workflow/WORKFLOW.md:58 the stage table describes the stage instead of naming its command.
```

`<severity> <file>:<line> <one sentence>`. Blockers first. Nothing else on stdout.

`.claude/scripts/review.sh` exits **`0` whatever it FOUND** — findings do not approve or block —
and **non-zero when the review did not RUN**. The two are not the same fact and nothing else in
this repository can tell them apart, because a review that could not start and a review that found
nothing produce the same empty stdout. So:

**The outcome is a WORD, not the exit code.** Every run prints exactly one
`review: outcome=<name>` line on stderr, and that line is the answer to *what happened*:

| `outcome=` | exit | what happened |
|---|---|---|
| `ran` | `0` | the review ran. Whatever is on stdout is the whole of what it found, including nothing |
| `nothing_to_review` | `0` | the actionable diff was empty after excluding reports. Nothing was reviewed, which is not a diff with nothing wrong in it |
| `no_base` | `2` | there was no base to diff against — nothing was reviewed |
| `prompt_too_large` | `2` | instructions and inventory exceed the bound even without inline hunks — no reviewer was started |
| `reviewer_failed` | the reviewer's own status | no toolchain, no credentials, a crash. `SRV-FIX-505` is the standing instance |
| `unreadable_answer` | `3` | the reviewer answered and no line of it was a finding |

The word exists because **no exit code can be reserved**: the reviewer is an arbitrary program and
may exit with any number this script might have wanted for itself, so a `3` from the reviewer and
this script's own `3` are the same byte and different facts. The number is still reported — `E19`
puts it on the last `command` frame and §10 says the tool has no opinion about it — and it is the
reviewer's REAL status when the reviewer ran, which is what a person debugging needs.

Every one of these says what happened on **stderr**, which `E19` interleaves into the same
`command` frames as stdout, so a person watching the SESSION tab sees the reason and not a blank
panel. A report that reads any outcome but `ran` as "no findings" is the defect this table exists
to stop.
