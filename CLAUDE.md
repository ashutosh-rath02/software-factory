# The factory — develop with agents, against any repo

A local tool you point at a repository. It reads that repo's requirements, tickets and reports,
gives each piece of work an isolated lane, runs an agent in it, and lets you watch and steer that
agent while it works.

**This repository is its own git repository.** It is cloned into other repos and today it sits
inside `software-factory/` as a gitlink, but nothing here may depend on that parent existing. A
path that walks above this directory is a defect.

| Half | Path | Language | Build |
|---|---|---|---|
| Server | `server/` | C++17 on `server/core` | `cd server && ./build.sh run` → :7777 |
| Client | `client/` | React + TS (Vite) | `cd client/web && npm run dev` |
| Both | `.` | — | `./build.sh run "$PWD"` → http://localhost:7779 |

Open **7779**, not 7777. The client proxies `/api` and `/ws` to the server so the browser sees one
origin and CORS never applies; 7777 on its own serves JSON.

## The one boundary

> The factory knows **agents, lanes, resources and sessions**.
> It does not know what a board is, what a seam is, or what the repo it is pointed at builds.

Everything repository-specific enters through one door — the manifest — and everything else is
downstream of it. `docs/technical/ARCHITECTURE.md` is the module map and the dependency direction;
`docs/technical/MODEL.md` §2 is the same boundary written as **seven invariants a ticket is checked against**,
and a ticket may not contradict or extend one. If it needs to, it stops and the model changes first.

## Which repo an instance is pointed at

The server is *given* a repository, never discovers it. **There are two manifests here and which
one you point at decides which backlog you see.**

```bash
cd factory && ./build.sh run "$PWD"          # THIS repo — FAC-* tickets are read, claimed and run here
cd .. && factory/build.sh run "$PWD"         # the parent repo — its own backlog, no FAC-* ids
```

`.factory/factory.json` in this directory declares the factory's three workspaces
(`factory-server`, `factory-client`, `factory`). A `FAC-*` id asked of an instance pointed
elsewhere is `404 not_found` **by design** (`SRV-FEAT-244`) — that is the wrong instance, not a
missing ticket.

`GET :7777/api/manifest` returns `409` on an unknown manifest major and `422` naming the JSON
pointer that failed. `GET :7777/api/health` is the liveness check.

## The contracts, and that they are generated

Three files are the source of truth and three commands turn them into code. **Nothing they
generate is ever hand-written.**

```bash
cd client/web && npm run gen:types        # docs/technical/contracts/socket.schema.json  → src/types/socket.ts
cd client/web && npm run gen:api-types    # docs/technical/contracts/http.schema.json    → src/types/api.ts
cd client/web && npm run gen:tokens       # client/web/design/nocturne.css + tokens.json → src/styles/tokens.css
```

- **The socket is one path, `/ws`.** Every frame is `{"m":"<name>","ts":"<iso>","seq":<int|null>,…}`.
  A surface sends `subscribe {scope}` and gets one `snapshot` for that scope, then that scope's
  deltas and no other's; a scope is `{requirements:true}`, `{ticket:<id>}` or `{lane:<lane>}`. A
  reconnecting client sends `resume {lane, after_seq}` and gets exactly the frames it missed — or a
  `snapshot` carrying `replaces_tail`, and then it discards its tail. `ping` → `pong`; `seq` exists
  on `transcript` frames only. A bad frame gets an `error` and the connection stays open.
- **HTTP bodies are `docs/technical/contracts/http.schema.json`.** The browser gate validates the *real* server's live
  responses against it, so a schema that drifts from the C++ turns a gate red instead of blanking a
  screen.
- **Colour is `client/web/design/`.** `theme.test.tsx` asserts every token is byte-equal to the design's
  in both themes. **No colour may be written anywhere else in the client.** Do not hand-edit the
  exported design files — change the design, export again, let the diff show what moved.

The client never polls: HTTP loads a surface, `catalog.changed` reloads exactly the document it
names, and `no-poll.test.tsx` fails on a timer-driven fetch or render.

## Reading the target repo's documents

Read straight off disk on every request — no index, no cache:
`GET /api/requirements?q=&filter=all|needs|running|blocked` (`400 bad_filter` on anything else),
`GET /api/requirements/{id}`, `GET /api/tickets/{id}`, `GET /api/tickets/{id}/report`. A document
changing on disk pushes `catalog.changed` on `/ws`.

Frontmatter keys the tool reads are `id`, `parent`, `workspace`, `status`, `stages`; every other key
is passed through untouched and reaches the screen only if `display.ticket_fields` names it.

**A ticket writes `workspace:`** — one of the names declared under `workspaces` in the manifest.
This repository wrote `repo:` until `SRV-FIX-021`, so every ticket grouped as `unassigned` while
every assertion about the grouping passed; the key is the tool's word and the value is the target
repository's, and `test_catalog.py::test_every_ticket_in_this_repository_names_a_declared_workspace`
keeps it true.

**`_*.md` and `README.md` are not documents.** `catalog::Store::isDocument` skips them, so a
template is a form and not a requirement — `SRV-FIX-043` and `SRV-FIX-263` are what it cost to
learn that the convention had lived only in the test suite.

## Testing — tiers, not the whole suite

**A ticket runs tiers 1–3. The full suite is a RELEASE gate, not a ticket gate.** At 479 tests and
16 minutes, re-run at every stage, it cost 1–2 hours per ticket to validate five new assertions.

| tier | what | when |
|---|---|---|
| 1 | the ticket's own assertions | every ticket |
| 2 | the module it touched **plus** `test_isolation.py` and `test_confine.py` (always, 21 s) | every ticket |
| 3 | **drive it in the web client by hand** | every ticket |
| 4 | `cd server && pytest tests/ -v` — all of it | release only |

`cd server && python3 tools/tier_gate.py` is the `factory-server` workspace gate (SRV-FIX-579): it
selects tier 1 from the ticket's `Evidence:` lines and tier 2 from the modules the branch touched,
and counts only failures not listed in `server/tests/known-red.txt`. A ticket that fixes a
known-red case removes its line; a ticket never adds one.

```bash
cd server && pytest tests/test_lane.py -v          # tier 1/2
cd client/web && npm run test                          # vitest
cd client/web && npm run gate -- --surfaces tickets,ticket-md --themes dark   # GATE, tier 2b
cd client/web && npm run gate                          # VERIFY — every surface, both themes
cd server && pytest tests/ -m repository           # the document gate — at GATE for a ticket that
                                                   # changed a document, and again after a merge
cd server && ./tools/docgate.py --ticket <id>      # the same gate answered as a difference, and
                                                   # what this branch adds to it
```

**A process change runs the ledger eval (`SRV-FEAT-498`).** `docs/operations/ESCAPES.md` is the eval set,
not only the record: every `test_*` a row's *Change landed* cell names must still be reachable, and
`test_every_case_the_ledger_names_is_collected` is the case that says so. A commit that touches a
**process file** — anything under `.claude/`, the documents `docs/tickets/_TEMPLATE.md` §2's
`FIRST read:` sentence names, the template itself, `.factory/factory.json` — changes how every
future round is run, and `docgate.py` names those files before it prints a verdict. The eval itself
is unconditional: it carries `@pytest.mark.repository` and runs on every branch. A row whose
*Change landed* cell names a ticket still `todo` or `in-progress` is the ONE exclusion — filed, not
yet landed — and its ticket id is printed on every run.

**`npm run gate` is the client's real gate**, not vitest. It builds the bundle, starts the C++
server against `gate/fixture-repo`, serves the build through `vite preview` and drives Chrome
headless: rows, groups, markdown, the socket chip, the schema, contrast, and a screenshot of every
surface in both themes. A green vitest run is not a screen anybody can read.

**It takes a scope** (`WEB-FEAT-379`): `--surfaces a,b --themes dark`, or `FACTORY_GATE_SURFACES`
/ `FACTORY_GATE_THEMES`, with the argument winning. Scoped to the surfaces a ticket's §1 names
it is GATE's tier 2b and costs a few minutes; unscoped it is ten to twelve and belongs to
VERIFY. The names are the first column of `client/web/gate/surfaces.mjs`'s `SURFACES` and
`LIVE_SURFACES`, an unknown one is refused before any phase runs, and a check the scope left out
is printed and counted — `skipped(scope): n` — never passed. Every run ends with what each phase
cost.

**GATE does not end until the ticket has been driven as a person.** A green browser gate does *not*
satisfy this: the gate drives `gate/fixture-repo`, a synthetic repository, which is exactly what
`WEB-FIX-248` and `WEB-FIX-261` are about. Open the client and run the ticket from the screen the
way a person would. The report records what was driven and what the screen did; "I ran it" is not
evidence.

## Ticket workflow

**`docs/technical/workflow/WORKFLOW.md` is the whole of it** — three levels, and a different stage sequence per
component:

```
FAC-EPIC-*   REQUIREMENT → ANALYSIS → CONTRACT FREEZE → CHILDREN → L2 GATE → ROLL-UP
SRV-*        SPEC · TEST · IMPL · CONVENTION · BUILD · GATE · REVIEW · QA · MERGE · VERIFY
WEB-*        DESIGN · SPEC · TEST · IMPL · BUILD · GATE · REVIEW · QA · MERGE · VERIFY
```

Ten stages either way. The web leads with **DESIGN** — a change to a surface starts with a change
to `client/web/design/`, because a screen implemented first has no specification to be checked
against. It drops `CONVENTION` because the client has no diff-reading checker; that is a gap the
REVIEW stage carries by hand.

```
/new-ticket "<one-line request>"   → docs/tickets/FAC-<TYPE>-NNN-<slug>.md   (§1 filled from real code)
/run-ticket docs/tickets/<file>.md → SPEC → TEST(red) → IMPL → BUILD → GATE → docs/reports/<id>-report.md
/qa-ticket  docs/tickets/<file>.md → a SEPARATE session re-runs every §1 criterion → <id>-qa.md
```

- Ticket id = commit prefix (`SRV-FIX-347: …`) — one grep traces ticket ↔ commits ↔ report.
- §1 of a ticket is the human's; §2 (standing process) is fixed and the agent follows it verbatim.
  It lives once, in `docs/tickets/_TEMPLATE.md` §2; a ticket carries a pointer line (SRV-FIX-580).
- **A pass criterion is a BEHAVIOUR, and names the test that evidences it.** Not a bare test
  name. Write what a person does and what the product then does; name the case underneath.

  ```
  1. Open the wiki surface on a repository with six declared documents. All six appear, in the
     declared order, each under its own section heading.
     Evidence: client/web/tests/wiki.test.tsx::renders_the_declared_sections
  ```

  The rigour is unchanged — the case is named, it is executable, and it is watched RED before it
  is trusted. What changes is what the criterion is ABOUT. A criterion that is only a test name
  can be satisfied by writing the test that satisfies the name, and QA reading it can only grade
  the assertion; 21 of this repository's 54 FAIL verdicts are a check fitted to what already
  worked, and `SRV-FIX-351` failed four rounds in one day without its mechanism ever being wrong.
  A behaviour gives the test something outside itself to be checked against.

  **Where the product IS the check** — a gate, a permission grant, a document rule — the behaviour
  is still stated in terms of who is affected: *a QA session can run the gates the workflow names*,
  not *the allow-list contains six rules*.
- **Every block carries a mandatory NEGATIVE** — the wrong, unauthorised or malformed input the
  system must refuse. A criterion that cannot fail is not a criterion.
- Small serial change (one-line fix, copy/CSS tweak) → do it **inline**, no ticket, no subagent.
- **Never `ls docs/tickets/ | tail` for the next id.** Ask the tool:
  `.claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"SRV-FIX"}'` — the prefix its
  WORKSPACE takes: `SRV-*` for `factory-server`, `WEB-*` for `factory-client`, `FAC-EPIC` for a
  requirement.
  A lane is a checkout cut at claim time, so the highest id *you* can see is not the highest id —
  two lanes both filed `SRV-FIX-185` on 2026-08-21 and `409 ambiguous_ticket_id` made both
  unreachable (`SRV-FIX-188`).

### A passed ticket merges to `main` directly — no PR

The factory has one developer, so a pull request has no reviewer and the gate it represents does not
exist; **the QA session IS the gate**, and a passed ticket waiting for a formality is finished work
nobody can use. On `status: pass`, the branch is merged to `main` and pushed.
**One commit per ticket reaches `main` — `git merge --squash`, never a merge commit and never the
branch's own history.** A branch carries the round it actually took: a red test, a fix, a second
fix, a rename, a revert. That is evidence while the ticket is open and noise the moment it lands.
`git log --oneline main` is read to answer *what changed and why* — one line per ticket — not to
reconstruct how each piece was arrived at.
**The squash keeps the branch's author** — `git commit --author="$AUTHOR"`, derived from `git log --no-merges main..<branch>`, with a `Co-Authored-By:` trailer for every other author. Git writes the person merging as BOTH names otherwise, and a member's work becomes yours permanently (`SRV-FIX-376`). **`--no-merges` is part of the derivation, not a detail of it:** the rule below — *Before merging, `git fetch` and merge `main` INTO the branch first* — puts merge commits authored by whoever merged inside `main..<branch>`, so without the flag the merger's own bookkeeping outvotes the member, 2:1 after two rounds and 1:1 after one (`SRV-FIX-378`). A merge commit is not authorship.

The squashed message is therefore the ticket's ONLY surviving account, so it carries what the round
LEARNED and not merely what it changed. A thin squash message destroys the round; that is this
rule's cost and it is paid in the message, not by keeping the sub-commits.

The merge is not a licence to skip anything before it: QA still re-runs every §1 criterion itself,
still defaults to refuse, and still may not set `status: pass` on work it did not verify. What is
removed is only the packaging step after the verdict.

### Severity — only a BLOCKER stops the merge

**Every finding carries a severity, and the verdict is decided by the worst one.**

| severity | what it means | what it does |
|---|---|---|
| `blocker` | a person cannot do the thing §1 exists for — or the change loses work, breaks a surface that already worked, or is unsafe | **FAIL.** Nothing merges |
| `minor` | real, worth fixing, and a person can still do what the ticket exists for: cosmetics, an internal contradiction nobody reaches, a sharper test, a gate that could see more | **PASS WITH MINORS.** It merges, and each minor is FILED |

Three verdicts, then: `PASS` (nothing found), `PASS WITH MINORS` (no blocker, every minor filed),
`FAIL` (at least one blocker). A finished feature held back for a cosmetic defect is work nobody
can use, and a queue of them is how a backlog stops meaning anything.

**Three guards, because *minor* is the word this rule will be abused through.**

1. **An unfiled minor is a blocker.** The ticket exists, with an id from the tool, in the same
   commit as the merge, and the verdict names it. That is the whole mechanism that makes *"we will
   fix it later"* a thing anybody can find later.
2. **A red §1 criterion is never minor.** §1 is the human's statement of what the feature is, and
   no session — implementing or QA — may downgrade the request it was given. If a criterion really
   does not matter, the HUMAN edits §1, and that is a different act with a different author.
3. **The same cause a second time is a blocker.** A minor that comes back has stopped being
   cosmetic; it is evidence about the check, and `docs/operations/ESCAPES.md` says 55% of this ledger is a
   source leaking twice.

**A filed minor that ships is an accepted risk, not an escape.** An escape is a gate saying yes
where it should have said no; a defect written down, given an id and merged deliberately was
decided. An accepted risk is a decision — an unnoticed one is not.

Before merging, `git fetch` and merge `main` INTO the branch first, resolve there, and only then
merge out — `main` is shared with concurrent sessions, and a conflict resolved on the branch is a
conflict nobody else sees. `docs/operations/ESCAPES.md` conflicts on almost every integration because every
branch appends a row to the same table; **keep BOTH sides, always.**

### Four rules that outrank convenience

- **Model rule (hard): the role picks the model, the lane's provider picks the name.** Every
  session that owns a ticket — run-ticket, qa-ticket, every orchestration session — runs on the
  provider's workhorse at high effort. Launch headless Claude sessions with
  `claude --model claude-opus-5 -p …`; a Codex lane takes the manifest's Codex default.

  | Role | Claude | Codex | Effort | May write |
  |---|---|---|---|---|
  | Main (run / QA / orchestration) | Opus | `gpt-6.1-sol` | high | its own lane |
  | Worker · explorer · researcher | Opus (`model: "opus"`) | `gpt-6.1-sol` | medium | worker: its own lane; others: nothing |
  | Advisor | Fable | `gpt-6-astra` | high (Claude) · `xhigh` (Codex) | **nothing** |

  The agents are declared once per provider: `.claude/agents/*.md` and `.codex/agents/*.toml`,
  same names, same instructions. The selected manifest tree names the permitted helpers;
  select the empty `off` preset to run alone. A missing selected provider file refuses the run
  before any process starts. The design note `docs/notes/2026-10-03-claude-codex-agent-tree-design.md` keeps every decision
  slot selectable at start and removable. **Fable — and its Codex peer — run only as the advisor**: a
  read-only session called at three checkpoints, reading a bounded brief and searching the
  transcript rather than reading it whole, (before the plan, when the same failure cause
  repeats at one stage, before GATE hands over to QA), and silent on every other turn. The advisor
  never edits, commits, sets `status:`, writes a verdict or authorises a repair round the workflow
  does not already allow. Main applies its advice or records why not. Fable as main, worker or QA
  is still forbidden.
- **English only, hard, for everything an agent produces** — every ticket, report, QA verdict,
  analysis, commit message, code comment, test name and line of agent reasoning, including in
  sessions driven entirely in another language. The human writes requests in whatever language they
  like and that never changes; the agent answers in English. The reason is not style: pass criteria
  are executable assertions and verdicts quote raw output verbatim, `SRV-FIX-042` already failed
  once for asserting a model's word choice, and a clause that loses precision in translation becomes
  a wrong assertion — a gate that says yes when it should say no.
- **The implementing session VALIDATES; QA VERIFIES — and QA is not started until validation is
  green.** GATE does not end while any §1 criterion is unexecuted or red. This session runs every
  one of them itself, quotes the output, and fixes or files-and-BLOCKs whatever will not go green;
  a criterion nobody ran is not a criterion that passed. **Only when every one is green does a NEW
  session take over as QA** — a different session, never a subagent, given §1 and the repo and not
  the report, and the only thing that may set `status: pass`. **Which tickets need that session,
  the two-round caps and batching by QA surface are `WORKFLOW.md` § Cost bounds (2026-10-11):** a
  `qa: operator` ticket is validated here and landed by the operator from the diff.

  Both halves are load-bearing and they answer different failures. Handing off early spends a whole
  session establishing what this one already knew: on 2026-09-02 `WEB-FEAT-001` was handed over at
  `PARTIAL` and its QA session returned nine unverified criteria. And a subagent inherits this
  session's framing — the same assumptions, the same blind spot, the same list of cases it already
  thought of — so the second pass has to start cold.
- **An escape is a defect in the check.** A bug found after its ticket passed means a gate said yes
  when it should have said no: the `FIX` sets `escaped_from:`, answers §3, lands the structural
  change in the SAME commit, and adds a row to `docs/operations/ESCAPES.md`. **A QA FAIL is the same evidence
  one step earlier**, so the round after a FAIL commits the missing assertion FIRST, RED, and
  transcribed from QA's own reproduction, before it commits any fix.

## Lanes — never remove a checkout without asking the tool

`git status` answers *does this hold uncommitted files*; it cannot answer *is an agent running in
it*. Only `GET :7777/api/lanes` can.

On 2026-08-06 a session surveyed four lane worktrees, found `dirt=0` and `ahead=0` for each, called
that "verified empty" and removed them — one was 410 s into a run holding `board-master`. The server
reported `crashed · worktree_missing`, the agent kept running with `--chdir` into a deleted
directory, and the next lane queued forever on a board a corpse still held.

Release through the tool — `DELETE /api/lanes/<lane>?force=true`, which kills the process and frees
its resources — and only then remove anything.

## Code style

- No comments unless asked. The reasoning goes in `server/design/<module>.md`, not in the source —
  `SRV-FEAT-229` and `SRV-FEAT-230` are the pattern.
- No over-engineering; follow the existing pattern in each half.
- Read files before modifying them.
- **NEVER block engine/io_context threads** — the engine runs a pool of threads on one `io_context`
  (four by default), so *"the engine thread"* is not a thing. Long work goes to an async worker.
- **NEVER add a mutex to event-driven code** — asio's answer to handlers sharing state is an
  **executor**: give the state its own `strand` and bind its handlers to it.
- `server/core/` is the reusable framework; product code lives in `server/app/`. Nothing under
  `app/` may be included by `core/`, and no module may reach sideways into a sibling's internals —
  `api` composes, the others do not know about each other.
- One action per `_<action>.cpp` file.

## Where the documents are

```
docs/product/SPEC.md                     what gets built — §7 is the features and their executable criteria
docs/technical/MODEL.md                  the same as CONSTRAINT — seven seams a ticket may not contradict
docs/technical/ARCHITECTURE.md           module ownership and dependency direction
docs/technical/workflow/WORKFLOW.md      the three levels and each component's stages and gate axes
docs/operations/ESCAPES.md               every gate that said yes when it should have said no
docs/epics/  docs/tickets/  docs/reports/  one tree — FAC-EPIC-* · SRV-* · WEB-*
server/design/                          the decisions behind each server module
client/web/design/                      the visual design, its tokens, and the brief
```
