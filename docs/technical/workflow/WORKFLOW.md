# The workflow — one product, two components

[Documentation](../../INDEX.md) / [Technical](../README.md)

Three levels, because they answer three different questions.

| level | id | asks | owns |
|---|---|---|---|
| product | `FAC-EPIC-*` | *is the requirement met?* | the contract freeze and the L2 gate. **No code.** |
| server | `SRV-*` | *does the server do it?* | `server/` |
| web | `WEB-*` | *does the screen do it?* | `client/web/` |

A requirement and its children live in the **same** documents tree — `render.cpp` keys parent
lookup by `{repo, parent}`, so a child in another repository is not a child at all
(`test_a_parent_names_a_requirement_in_its_own_repository`). The per-component namespace is carried
by the **prefix**, not by the directory.

## Context and evidence

- Read the required shared documents once per session. Keep their rules in context across stages;
  reopen changed files or the sections needed to resolve uncertainty. After compaction, restore the
  current stage, open criteria, file references and evidence paths before resuming. Each independent
  QA session still reads its own required context.
- Search for relevant files and sections before reading whole directories, backlogs or historical
  reports. A stage transition alone does not require reading unchanged context again.
- Keep complete command captures on disk. Inspect summaries and relevant failures first, then open
  enough surrounding output to establish the result. Reports quote the deciding lines verbatim,
  including failures and exit status, and link the full capture. Explicit §1 evidence requirements
  still apply; an abbreviated display does not reduce what must be executed or preserved.
- Save the complete report once. Chat delivery gives the outcome, validation summary, outstanding
  blockers and a link to that report rather than repeating it.
- During a fix loop, run the failing assertion; run the required gates on the final changed tree.
  In the same session, reuse a capture only when its relevant inputs are unchanged. Independent QA
  and post-merge VERIFY still execute their required checks.

A criterion may not quantify over a domain it does not enumerate. Name the scenarios or the
bounded set being judged in the request; a new member outside that set becomes a separate fix.
This keeps an independent break attempt from turning acceptance into an unbounded series of rounds.

## Documentation map and product context

Start product orientation at [INDEX.md](../../INDEX.md), then the reviewed [overview](../../overview/README.md) and
current [roadmap](../../overview/ROADMAP.md). Open the owning product, technical, operations or design source
for the task. This supplements required context; it does not replace model seams, contracts or
ticket acceptance criteria. Independent QA obtains evidence directly; a summary never proves acceptance.

Keep goal, scope and current capability state in the overview; milestones and outstanding acceptance
in the roadmap. Category entry points link to authoritative documents. Existing epics, tickets,
reports and exported design files keep their declared paths. Source and contract moves update every
consumer, generator, adapter and active reference in the same change. Templates belong under samples
or their existing underscore-prefixed locations. Notes hold drafts; accepted conclusions move to the owning source
with a link left in the note.

A maintained summary records review date, source baseline and evidence links. Distinguish proposed,
implemented, independently accepted, finally verified, released and running states. Document
publication status is distinct from product state. Refresh summaries when their claims change;
preserve dated reports as history. Missing evidence stays unknown rather than inheriting another
version's PASS.

Link a new guide from its category README and give it a link back to that category or the index.
Use repository-relative links; keep one authoritative copy. Move the source into its owning category,
update active consumers and references, then remove its retired path. Historical facts and verdicts
remain intact; relocation does not establish current acceptance.
Preserve byte-for-byte evidence snapshots. When their directory context is incomplete, restore
navigation through an explicit provenance map to matching existing files or exact archived Git
versions. Record source and destination hashes; never substitute a newer version as old evidence.

After adding/removing/renaming a documentation file or evidence asset, or changing an indexed
title/frontmatter status, run `python3 docs/tools/doc_inventory.py`, then
`python3 docs/tools/doc_inventory.py --check`. Run the check after changing links or headings too.
Generation maintains the compact [inventory entry point](../../DOCUMENTS.md) and its category
catalogs under `docs/catalog/`; edit their sources and regenerate rather than editing catalog pages.
The inventory includes every existing non-ignored file under `docs/`, repository documentation and
component-owned design references. It checks coverage, local link targets, heading anchors and
reachability from the index. Private ignored files and dependency/build output are excluded.
The offline inventory separates current guidance, templates, fixtures and history; it is navigation,
not a runtime catalog. Existing document and process gates still apply to shared instructions.

## Known failures and run readiness

An applicable BLOCK or FAIL becomes a structured cause with its capture, owning fix ticket and
deterministic probe. Use `server/tools/ticket_readiness.py record` to retain incidents under the
same reviewed cause ID; a new capture is evidence for that cause, not another copy of the fix.
Keep these records in the repository's versioned registry rather than expanding every ticket's
standing instructions. Existing escaped defects still follow the ledger and regression rules below.
Use the CLI's scoped result and evidence links; do not load the whole registry into each model round.

A workspace may declare `start_preflight` in its manifest. Factory executes that local command
inside the selected profile's lane before every start, including role starts and resume. The
command receives the ticket, workspace, profile and model as environment values. It must be bounded
and provider-free. A failed, unavailable or timed-out preflight stops the start before an agent
run is created; preserve its full capture and name the dependency. The registry's `check` command
reports unrelated scopes explicitly and permits only the named repair tickets to bypass their own
open cause. Manifest exemptions are reserved for repairing the preflight mechanism itself.

Resolve a cause only after the actual fix has RED, GREEN, negative and independent-QA evidence.
The `resolve` command validates and retains typed receipts and artifact hashes; its QA receipt is
an attestation from the independent workflow, not QA performed by the command. Resolved probes
continue to execute on later starts. When a fix lands, recheck blocked dependents and continue from
the failed stage with their existing implementation and evidence, running every required final gate.

---

## 1 · Product — `FAC-EPIC-*`

Phases, not stages. An epic owns no code and closes on one thing.

```
REQUIREMENT → ANALYSIS → CONTRACT FREEZE → CHILDREN → L2 GATE → ROLL-UP
```

**CONTRACT FREEZE comes before any child is written.** The two halves generate from shared files,
so a child that starts before the freeze is a child building against a contract that is still
moving:

| file | regenerate with | consumed by |
|---|---|---|
| `docs/technical/contracts/http.schema.json` | `npm run gen:api-types` | `client/web/src/types/api.ts` |
| `docs/technical/contracts/socket.schema.json` | `npm run gen:types` | `client/web/src/types/socket.ts` |
| `docs/technical/contracts/manifest.schema.json` | — | the server, at load |
| `client/web/design/` | `npm run gen:tokens` | `client/web/src/styles/tokens.css` |

**The L2 gate is the only thing that closes an epic.** Every child at QA `pass` is *not* done: the
journey has to be walked end to end, by a person, against a repository that is not the fixture.
`SRV-FEAT-165` passed three green cases and a green browser gate, and QA found the defect in one
call against the real thing.

---

## 2 · Server — `SRV-*`

```
SPEC · TEST · IMPL · CONVENTION · BUILD · GATE · REVIEW · QA · MERGE · VERIFY
```

| stage | what it is |
|---|---|
| `SPEC` | the endpoint's JSON, the frame's fields, the store's full shape — written before code |
| `TEST` | §1's assertions as real cases, run, **red output quoted** |
| `IMPL` | the smallest diff. Root cause only |
| `CONVENTION` | `./tools/conventions.py` — reads the **diff**, not the behaviour. Ratchets |
| `BUILD` | `./build.sh build`, warning-free |
| `GATE` | tier 1 + tier 2 + tier 3 (below) — and the document gate, for a ticket that changed a document under `docs/` |
| `REVIEW` | the workspace's declared `review` command, run through `E19` — `docs/technical/workflow/REVIEW.md` is what it reads |
| `QA` | a separate session, started only once GATE has every §1 criterion green; §1 and the repo only, defaults to refuse |
| `MERGE` | on `pass`, straight to `main` — **squashed, one commit per ticket**. No PR |
| `VERIFY` | the full suite on `main`, after the merge — **and the branch stops being kept**: `.claude/scripts/landed.sh` (SRV-FIX-427) |

**The branch is kept until VERIFY, and not after.** A release keeps the branch, always, because at
release time nobody can know whether the work landed. That rule had no counterpart: nothing said
when a kept branch stops needing keeping, and this repository reached 37 branches and 27 worktrees
against zero lanes. `VERIFY` is where the answer first exists — the ticket is `pass` and its
squashed commit is on the base branch — so it is where the keeping ends.

`.claude/scripts/landed.sh` reports by default and removes only with `--remove`. It cannot ask git:
after `git merge --squash` a landed branch is not an ancestor, so git's own delete-if-merged
refuses it and `rev-list base..branch` is non-empty — **both of git's safety answers say *unmerged*
about work that is on main**. It decides from the ticket and the squashed subject instead, refuses to remove
anything backing a live lane, and refuses outright if `GET /api/lanes` does not answer, because
that endpoint is the only thing that can say whether an agent is running in a checkout.

**There is no `DESIGN` stage here** and that is deliberate — the server has no surface. Its
equivalent is a rule, not a stage: **a ticket that changes a module updates
`server/design/<module>.md` in the same commit.** A note that drifts from the code is worse than no
note, because the next reader believes it.

### Server gate axes

| axis | catches | runner |
|---|---|---|
| source | a strand in `app/`, an anonymous namespace, a comment in production code | `tools/conventions.py` |
| behaviour · tier 1 | the ticket's own assertions | `pytest tests/test_<area>.py -v` |
| behaviour · tier 2 | the module it touched **plus** `test_isolation.py test_confine.py`, always | 21 s |
| race | anything that waits, polls, forks or races | `tests/soak.sh`, 100 runs under oversubscription |
| documents | a document rule the ticket's own files break, and an eval a ledger row named | `docgate` — what THIS branch ADDS to it, at GATE when the ticket changed a document. A commit touching a **process file** — anything under `.claude/`, the documents `_TEMPLATE.md` §2's `FIRST read:` sentence names, the template, the manifest — runs the ledger eval over every case `docs/operations/ESCAPES.md` says landed (`SRV-FEAT-498`) |
| human · tier 3 | what a person sees | drive it at :7779 |
| release · tier 4 | everything | `pytest tests/ -v` — **VERIFY only** |

The real refusal corpus is part of the repository/document gate, independently of the changed
module's local test list. `test_errors.py` derives emitted codes from source and judges actual HTTP
bodies against the schema, §11 governance and remedy invariants; its repository marker makes those
checks execute during comparative docgate. `test_errors_gate.py` runs pytest's actual repository
selection, compares it with the corpus's declared assertions, and checks that a new literal
refusal-factory code absent from the live corpus is refused. A ticket adding an error code updates
its real provocation and contract decision before GATE finishes; a row or a module-local status-code
test does not replace the shared wire judgment.

**The document gate runs at GATE, not only after the merge.** A ticket that edits anything under
`docs/` — a ticket document, a report, a ledger row, its own `status:` line — has changed what the
repository's document rules are about, and `pytest tests/ -m repository` is the 52 cases that read
them. `WEB-FIX-328` reached `awaiting-qa` with that gate red **naming `WEB-FIX-328`**, because the
commit that sets `status: awaiting-qa` is the commit that puts a ticket into the enumerated domain
of the case that refused it; the gate was named only *"after a merge"*, which is after the last
session that could act on it. It stays at VERIFY as well — a defect that exists only in the union
of two branches shows up nowhere else.

It is **not** a fourth tier and it does not move tier 3: a person still finishes GATE by driving
the ticket at :7779.

**A change to a process file is gated on the whole eval set (`SRV-FEAT-498`).** `docs/operations/ESCAPES.md`
is not only a record: 117 of its rows name the case that closed the incident, and
`test_every_case_the_ledger_names_is_collected` requires every one of those cases to still be
reachable. Editing a skill, `CLAUDE.md`, the ticket template or the manifest is editing how every
future round is run, so `docgate.py` prints `process change: <n files>` and names them before it
prints any verdict — and the eval runs on every branch either way, because it is a `repository`
case and that selection is conditional on nothing. The one exclusion is a row whose
`Change landed` cell names a ticket still `todo` or `in-progress`: that case is filed and not
landed, and the ticket id is printed per excluded row on every run.

**Own what your own commit adds — and the script does the owning.** The gate is red on `main`
today for reasons older than any one ticket, so its exit status is not a verdict about a branch.
The verdict is the failing SET this branch ADDS against the same command on its merge-base: an
unchanged set passes, a NEW name blocks. **The set is the case names AND the ids each failing case
lists** — half these cases enumerate the tickets they refuse, so a case already red grows a name
without changing the list of case names. That is exactly how `WEB-FIX-328`, and then
`SRV-FIX-384`'s own status line, entered `test_a_ticket_that_asks_for_a_screenshot_has_one`.

That comparison was a session's job, done by reading, twice a round, and nothing automated it:

```bash
cd server && ./tools/docgate.py --ticket <id>
```

One command, one answer. It runs the gate here, runs the same command in a scratch worktree it
creates at the merge-base and removes on every exit path, and prints
`base: N red · head: M red · added: K` followed by one line per addition — `NEW CASE` with the
node id, `NEW ID` with the case AND the id it grew. **Exit 0 added nothing, exit 1 names what it
added, exit 2 the question could not be answered.** Exit 2 is the one that matters most: a run
with no pytest summary line — a killed gate, the `GATE_EXIT=` lie of 2026-08-27 — is not a
smaller failing set, it is no answer at all, and it writes no log that could later be quoted as
one. With `--ticket` both raw logs land under `docs/reports/<id>/`, which is where every session
was already putting them by hand.

`SRV-FIX-354` is open against the gate's root resolution and a session in a lane meets it
immediately — the gate resolves its root by walking above the factory, so in a lane worktree it
reports `FileNotFoundError` about a `.factory/` that is not there rather than a verdict about the
documents. `docgate` does not fix that; it prints the two `server/` directories it ran the gate
from, so the report quotes where rather than asserting it. Named here rather than left out, for
the reason `geometry` is named in §3.

---

## 3 · Web — `WEB-*`

```
DESIGN · SPEC · TEST · IMPL · BUILD · GATE · REVIEW · QA · MERGE · VERIFY
```

Ten stages, as the server has — so no surface can disagree about a total. `CONVENTION` is replaced
by `DESIGN`, at the **front**.

### DESIGN is first, and that is the whole point

> **A change to a surface starts with a change to `client/web/design/`.** The ticket's first commit
> touches the design. Never the other way round, and never both at once with the design written
> afterwards to match what was built.

A screen implemented first has no specification it can be checked against — the code becomes its own
criterion, which is the shape of gate this repository already keeps a ledger about. The ticket cites
the frame it implements; `BRIEF.md` is the list of frames the design owes.

`CONVENTION` is absent because **the client has no diff-reading checker**. That is a gap, not a
decision — `tools/conventions.py` is C++-only and nothing plays its part here. Until one exists,
`factory-client`'s declared `review` command carries it: `docs/technical/workflow/REVIEW.md`'s fourth pass is the
client's generated-types and no-poll rules, and `SRV-FEAT-500` made the stage a command the tool
runs rather than a reading somebody reports having done.

### Web gate axes — and the two that do not exist yet

| axis | catches | runner |
|---|---|---|
| tokens | a colour that is not the design's | `theme.test.tsx`, byte-equality, both themes |
| palette | two states that are one colour | `state-tones.test.ts`, OKLab ΔE ≥ 0.05 |
| behaviour · tier 1/2 | the ticket's assertions, then the suite | `npm run test` |
| schema | a response the client's types do not describe | the gate validates the **live** server |
| visual | a rendered pixel that moved | `gate/baselines/` + the corpus |
| contrast | text below WCAG on its own ground | in `npm run gate` |
| documents | a document rule the ticket's own files break, and an eval a ledger row named | `docgate` — what THIS branch ADDS to it, at GATE when the ticket changed a document. A commit touching a **process file** — anything under `.claude/`, the documents `_TEMPLATE.md` §2's `FIRST read:` sentence names, the template, the manifest — runs the ledger eval over every case `docs/operations/ESCAPES.md` says landed (`SRV-FEAT-498`) |
| human · tier 3 | what a person sees | drive it at :7779 |
| **inventory** | **a component simply MISSING** | `npm run gate:inventory` — armed |
| scope | ten minutes of sweep for a ticket that touched two surfaces | `npm run gate -- --surfaces a,b --themes dark` — tier 2b at GATE |
| **geometry** | a pinned number that drifted | does not exist |

`inventory` is armed and is the first rung of `npm run gate`. It catches what a pixel diff and a
geometry probe both sail past: an element that was never drawn — *you cannot measure or diff what
is not there*. Its sets come from `design/ref/extract.py`, which reads the design's own `const`
blocks, never from whoever drew the design; a set the drawing agent wrote is bounded by that
agent's imagination and agrees with the drawing every time.

Seen red: deleting one `awaiting-merge` cell reported **4 named misses across two sets**, exit 1.
It measured 70/112 on a design its author had just called finished.

`geometry` remains absent — no pinned number is asserted against the built screen yet. It is
listed rather than omitted, because a table of axes with one silently missing is the same lie an
unarmed gate tells.

### Which browser gate runs when — the scope (WEB-FEAT-379)

`npm run gate` takes a scope, and the two stages that run it take different ones.

| stage | invocation | what it sweeps | what it costs |
|---|---|---|---|
| `GATE` · **tier 2b** | `npm run gate -- --surfaces <the surfaces §1 names> --themes dark` | those surfaces, that theme | a few minutes |
| `VERIFY` | `npm run gate` | every declared surface, both themes | ten to twelve minutes |

The names are the first column of `client/web/gate/surfaces.mjs`'s `SURFACES` and `LIVE_SURFACES`;
`FACTORY_GATE_SURFACES` and `FACTORY_GATE_THEMES` say the same thing and the argument wins. An
unknown name is refused, by name and with the declared names listed, before any phase runs.

**The inventory, the vitest suite and the build are never scoped.** They are the client's real gate
and they cost about a minute together; what a scope narrows is the browser sweep, which is the ten
minutes.

**A check the scope left out is printed and counted — `SKIP(scope)` per check, `skipped(scope): n`
in the summary — and decides nothing.** That is not decoration. `docs/operations/ESCAPES.md`, 2026-07-29: five
client lanes were told not to run the browser gate so five headless Chromes would not make each
other flaky, all five merged on a green vitest run, and four gate assertions reached `main` having
never once executed. A way to run less than everything is that instruction with a nicer interface
unless a check that did not run is visibly a check that did not run.

**Every run prints what each phase cost** — `inventory 1s · vitest 61s · build 6s · server 2s ·
sweep 190s · total 4m 20s`, from the trap, so a run that died at the build prints it too. The
numbers in `FAC-EPIC-374` §1 had to be reconstructed from transcript timestamps; the next
measurement is a grep.

---

## Cost bounds — operator decision, 2026-10-11

Measured on the SRV-FEAT-573/574 round: one ticket took six QA sessions, another three GATE/REVIEW
loops, each loop a full model session. These bounds keep quality while capping that spend.

- **Round caps.** At most **two GATE rounds** in one implementation session and **two QA sessions**
  per ticket. When a cap is reached the session stops with `needs_you`, names the failure still
  open, and the operator decides: narrow scope, split, accept a filed minor, or defer.
- **Independent QA by risk.** A separate QA session is required when the change is big or risky:
  it changes a contract (`docs/technical/contracts/**`, the manifest schema or `.factory/factory.json`),
  lane isolation, confinement or permissions (`server/app/lane/**`, permission checks), landing
  (`server/app/land/**`), more than one server module, or the ticket is a `FEAT`. Otherwise the
  ticket carries `qa: operator`: the implementing session validates (tier gate, document gate,
  every §1 criterion) and stops at `awaiting-qa` without reporting the QA stage; the operator reads
  the diff and lands it by hand (squash, author kept), which the lander already defers to a person.
- **Batch by QA surface.** Tickets whose criteria share one QA surface — the same gates, the same
  module and test files — are implemented together on one branch under one round ticket, as
  `SRV-FIX-532` did, and verified by one QA session. The round ticket lists the folded ids; one
  commit lands under its id.

## Rules that hold at every level

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
- **The implementing session validates; QA verifies.** GATE does not end while a §1 criterion is
  unexecuted or red, and **QA is not started until every one of them is green**. A criterion that
  cannot go green is fixed or BLOCKED by a filed ticket before the handover, never carried into it.
- **QA is a different session, never a subagent**, gets §1 and the repo only, defaults to refuse,
  and is the only thing that may set `status: pass`.
- **A passed ticket merges to `main` directly, and it merges SQUASHED.** One developer, so a PR has
  no reviewer and the gate it represents does not exist. **One commit per ticket reaches `main`** —
  `git merge --squash ticket/<id> && git commit --author="$AUTHOR"`, never a merge commit and
  never the branch's own history — the author is DERIVED from `git log --no-merges main..<branch>`
  because a squash otherwise records the person merging as both names (`SRV-FIX-376`).
  **`--no-merges` is load-bearing:** the merge instruction (`CLAUDE.md`, and `_TEMPLATE.md` §2's
  MERGE bullet) requires merging `main` INTO the branch first, and those merge commits are authored
  by the merger and sit inside that range, so counting them hands the branch back to whoever
  resolved the last conflict (`SRV-FIX-378`). A
  branch carries the round it took (a red test, a fix, a second fix, a rename); that is evidence
  while the ticket is open and noise once it lands, and `git log --oneline main` is read to answer
  *what changed and why*, one line per ticket. The squashed message is the ticket's only surviving
  account, so it carries what the round LEARNED — a thin one destroys it. A ticket that FAILS
  merges nothing.
  **This is an accepted risk, decided and written down (FAC-EPIC-375, 2026-09-11).** The
  AI-native SDLC playbook requires a human approval at the merge that no agent can pass; this
  repository substitutes an independent QA session for it because it has one developer, and a PR
  with no second reader is a gate that does not exist. The day a second person works here, this
  paragraph is the decision to revisit, not a default to inherit.
- **Only a BLOCKER stops the merge.** Every finding carries a severity: `blocker` — a person cannot
  do the thing §1 exists for, or work is lost, or a working surface broke — versus `minor`, which is
  real and does not stop them. Three verdicts: `PASS`, `PASS WITH MINORS` (merges, every minor
  filed as its own ticket in the same commit), `FAIL` (a blocker; merges nothing). **An unfiled
  minor is a blocker**, **a red §1 criterion is never minor** — only the human may change the
  request — and **the same cause a second time is a blocker**. A filed minor that ships is an
  accepted risk, not an escape.
- **An escape is a defect in the check.** The `FIX` sets `escaped_from:`, answers §3 including
  *where else is this cause right now* with a query and its verbatim output, lands the structural
  change in the SAME commit, and adds a row to `ESCAPES.md`.
- **A QA FAIL is a missing assertion.** The next round's first commit is the assertion that would
  have caught it — red, transcribed from QA's own reproduction.
- **English only**, for everything an agent produces.
- **Ask the tool for an id.** `POST /api/tickets/ids` — a lane cannot see the highest id, and two
  lanes computing it independently is `SRV-FIX-188`.

## Where a stage sequence actually comes from

Each ticket's own `stages:` line. The manifest's `stages` is only the fallback for a ticket that
declares none, and a requirement's group shows the sequence of the tickets in it
(`test_requirement_groups`). So the two sequences above are set by the two blocks in
`docs/tickets/_TEMPLATE.md`, and no tool change is needed to give a component its own workflow.
