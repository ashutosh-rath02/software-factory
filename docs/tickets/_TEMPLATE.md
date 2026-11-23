---
id: SRV-TYPE-NNN         # SRV-* server · WEB-* web · FAC-EPIC-* the requirement above them
title: <FEATURE NAME>
parent: FAC-EPIC-NNN     # or: standalone
workspace: factory-server   # factory-server | factory-client | factory
                            # one of the names under "workspaces" in `.factory/factory.json`;
                            # any other name groups as unassigned
status: todo             # todo | in-progress | awaiting-qa | pass | fail | blocked
                         # awaiting-qa: GATE green, QA running. Only QA sets pass/fail.
contract: factory-v0.1.0
seams:                   # S1 S2 S3 S4 S5 S6 — space separated, blank if none

# ONE of these two, matching `workspace:` above. Ten stages either way, so no surface can
# disagree about a total. The sequence a group shows comes from ITS TICKETS' lines, not from
# the manifest — `docs/technical/workflow/WORKFLOW.md`, "Where a stage sequence actually comes from".
#
#   factory-server  stages: SPEC TEST IMPL CONVENTION BUILD GATE REVIEW QA MERGE VERIFY
#   factory-client  stages: DESIGN SPEC TEST IMPL BUILD GATE REVIEW QA MERGE VERIFY
#
# The web sequence LEADS WITH DESIGN and drops CONVENTION. Design first is the hard rule: a
# screen implemented first has no specification to be checked against. CONVENTION is absent
# because the client has no diff-reading checker — a gap, not a decision, and REVIEW carries it
# by hand until one exists.
stages: SPEC TEST IMPL CONVENTION BUILD GATE REVIEW QA MERGE VERIFY
escaped_from:            # set on a FIX whose defect got past a green gate — then §3 is mandatory
created: YYYY-MM-DD
---

# Factory Ticket — <FEATURE NAME>

> **This file is a form, not work.** `catalog::Store::isDocument` skips `_*.md` and `README.md`, so
> it does not appear on the requirements screen. That was not free: `SRV-FIX-043` and `SRV-FIX-263`
> both reported this template being counted as a requirement — with a status, an age, a workspace
> chip and a child — because the convention lived in the test suite and not in the tool.
>
> The frontmatter above is what the tool reads. `parent` links this ticket to its epic; `workspace`
> decides which group it shows in and whose stage sequence it runs, and it must be one of the names
> declared under `workspaces` in `.factory/factory.json` — a name nothing declares is grouped as
> `unassigned`, which is where every ticket in this repository sat until `SRV-FIX-021`. Keep
> `status` current: the board shows exactly what is written there.
>
> **HOW TO USE (developer):**
> A criterion may not quantify over a domain it does not enumerate. Follow the shared
> workflow's acceptance rule and name the scenarios or bounded set in §1.
> 1. Copy this file to `docs/tickets/<PREFIX>-NNN-<slug>.md` — the NAMING RULE:
>    - `PREFIX` = `SRV-FEAT` · `SRV-FIX` · `SRV-REF` for the server; `WEB-*` for the web client;
>      `FAC-EPIC` for a requirement that owns children in both. `FEAT` new capability, `FIX`
>      defect, `REF` refactor with no behaviour change.
>    - **THE PREFIX IS THE WORKSPACE, and the tool answers for both series.** `SRV-*` ⇔
>      `factory-server`, `WEB-*` ⇔ `factory-client`, and `FAC-` is epics and ONLY epics —
>      `test_component_scheme.py` refuses any other pairing. Work that genuinely spans both
>      components is an EPIC WITH CHILDREN; there is no third prefix, because `docs/technical/workflow/WORKFLOW.md` line 1
>      is *one product, two components*.
>    - `NNN` — **ask the tool, never count files**, with the prefix your workspace takes:
>      ```bash
>      .claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"SRV-FIX"}'   # factory-server
>      .claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"WEB-FIX"}'   # factory-client
>      ```
>      This line named the retired ticket series until 2026-09-03, and the endpoint answered `422`
>      for `SRV-FIX` because no ticket used it. An agent following this template literally COULD
>      NOT file an `SRV-` id, which is why every session kept producing `FAC-*` — they were obeying
>      the repository. Neither is true now: the endpoint derives its series from the tickets, so it
>      began answering the moment they carried the prefix.
>      A lane is a checkout cut at claim time and nothing merges into a live one, so the highest id
>      YOU can see is not the highest id. Two lanes both filed `SRV-FIX-185` within an hour on
>      2026-08-21 and `409 ambiguous_ticket_id` then made both unreachable (`SRV-FIX-188`). If the
>      tool is not running, **stop and say so** — do not fall back to counting.
>    - `slug` = 1–3 kebab-case words naming the behaviour, not the implementation
>    - The ticket id is also the commit prefix (`SRV-FIX-NNN: …`) — one grep traces ticket ↔
>      commits ↔ report.
>    - **Every ticket works on its own branch**, `ticket/<id>`. Nothing commits to `main` before QA
>      — not the agent, not the orchestrator, not a "quick fix". This rule exists because its
>      opposite was written here once and an agent obeyed it: 659 lines landed on `main` unreviewed.
> 2. Fill ONLY the blanks in **§1 Request**. Leave **§2** untouched.
> 3. In a fresh session, send one line: **`Run ticket docs/tickets/<file>.md`** (or `/run-ticket <file>`).
>
> The agent does SPEC → TEST → IMPL → CONVENTION → BUILD → GATE, then **starts the QA session
> itself** and reports where its log is. QA is a separate session and only QA can mark the ticket
> passed — but starting it was never yours to remember.

---

## 1. Request  (fill these — everything else is automatic)

- **Found by:** <who or what surfaced this, with a date. For a FIX: the session, the gate, or the
  person, and the artefact that shows it.>
- **The evidence:** <for a FIX, the raw output — a failing assertion, a screenshot, a response body.
  Quote it verbatim in a fenced block. A paragraph of intent is not evidence.>
- **Feature / behaviour:** <what a person can do afterwards that they cannot do now, in behaviour
  terms, never in implementation terms.>
- **Seams (`docs/technical/MODEL.md` §2):** <the invariants this work touches, cited never paraphrased. If the
  ticket cannot be written without contradicting or extending one — or if a decision in §4 is marked
  OPEN — **stop and say so**: it is a model change, and writing it as a ticket is the exact failure
  the model exists to prevent.>
- **Surfaces (`docs/product/SPEC.md` §5–6):** <the endpoint ids and socket frames this touches — `E12`,
  `M8`. `E22` names a surface a ticket TOUCHES; `S4` names a guarantee it may NOT BREAK. Both belong
  on the line.>
- **Workspaces touched:** <`factory-server` · `factory-client` · `factory`>
- **Pass criteria** — one block per workspace touched. **Each is a BEHAVIOUR, with the test that
  evidences it named underneath.** Not a bare test name: a criterion that is only a test name can
  be satisfied by writing the test that satisfies the name, and QA can then only grade the
  assertion instead of the product.
  ```
  1. Open the wiki surface on a repository with six declared documents. All six appear, in the
     declared order, each under its own section heading.
     Evidence: client/web/tests/wiki.test.tsx::renders_the_declared_sections
  ```
  - `factory-server` → the behaviour, evidenced by named pytest cases in
    `server/tests/test_<area>.py`
  - `factory-client` → the behaviour a person completes, in the used state, evidenced by named
    vitest cases; plus the browser gate surface if the change is visible
  - **Every block carries a mandatory NEGATIVE test** — the wrong, unauthorised or malformed input
    the system must refuse correctly. A criterion that cannot fail is not a criterion.
  - **A criterion whose evidence is a REDUCTION is asserted over a population of at least TWO
    KINDS, and the kinds are NAMED in the criterion.** `max`, `min`, most-frequent, `any`, `all`,
    a worst-of, a roll-up, a total: on a population of one kind every one of them is the identity,
    so the case passes without ever asking the reduction a question. Name the kinds — *one
    authorship commit, two bookkeeping merges* — because a fixture whose composition is unstated
    is a fixture nobody can tell apart from a singleton.

    `SRV-FIX-376` fixed the squash merge's lost author and evidenced it on a branch of two commits
    by one author and no merges. The derivation counts `%an` over `main..<branch>`, and the very
    block being verified mandates merging `main` INTO the branch above that query — so the fixture
    omitted exactly the commits the subject guarantees will exist, and the merger's own bookkeeping
    outvoted the member the first time anybody used it (`SRV-FIX-378`).
  - **A criterion about what happens after a round ends is asserted on a lane whose session RAN
    and FINISHED ITS TURN (`process: idle`, its process alive).** That is the state every real
    round ends in. A claimed lane that never started a run is the singleton of that population: it
    may be one of the kinds, never the only one. `SRV-FEAT-415` released lanes "with no process
    alive in them" and evidenced it on a lane where no run had ever started, so nothing it shipped
    released a lane any real round left (`SRV-FIX-600`); `SRV-FIX-123` and `SRV-FIX-276` are the
    same fixture shape one ledger row earlier.
- **Safety:** <defaults to local only: no writes outside this repository, no deletion of a lane or a
  run history, no `main` push before QA. Override explicitly or not at all.>
- **Pre-decided:** <every judgement call already made, so the run session never has to ask.>

---

## 2. Standing process  (FIXED — the agent follows this verbatim; do not edit)

> A ticket does not copy this section. It ends with the pointer line
> `## 2. Standing process — docs/tickets/_TEMPLATE.md §2, fixed; read it once per session.` (SRV-FIX-580)

FIRST read: `CLAUDE.md` at this repository's root, `docs/technical/MODEL.md` §2 (the seams), and
`docs/technical/ARCHITECTURE.md`. Deliver §1 to the **Definition of Done: every named build target green ∧
every §1 assertion PASS (tests actually executed, output quoted) ∧ tier 3 driven by hand in the web
client.**

For context and output handling, follow `docs/technical/workflow/WORKFLOW.md`'s **Context and evidence** section.

- **FOUR WAYS A CHECK LIES, and each cost this repository multiple QA rounds. Read them before
  writing an assertion, not after.**

  1. **A SET YOU TYPED is bounded by what you already thought of.** Derive "every command", "every
     document", "every place an id lives" from the system — `rglob`, `PATH`, "is this an executable
     file", "every backticked span in these documents" — never from a literal list. A hand-written
     tuple of eight command prefixes missed `soak.sh` and cost `SRV-FIX-351` four rounds; a list of
     five document paths omitted `CLAUDE.md` and cost `SRV-FIX-368` two. Both lists agreed with the
     code they checked, which reads like confirmation and is one blind spot counted twice. If the
     derivation over-collects, exclude by a stated CATEGORY with a reason per entry — never by
     deleting rows until it passes.

  2. **A TEST WRITTEN AFTER THE CODE can only agree with it.** Write it from the criterion's words
     first and watch it fail FOR THE REASON THE CRITERION GIVES. 21 of this repository's 54 FAIL
     verdicts are a check fitted to what already worked. And the test must execute the PRODUCT — the
     launcher's real argv, the skill's own fenced commands, the live endpoint. **If a helper inside
     the test does the thing the ticket is about, the test is testing the helper**, and it will pass
     while the product is broken.

  3. **A CHECK THAT SWEEPS NOTHING HAS NOT RUN.** Any case that iterates asserts its sweep is
     non-empty. A ratchet passed on its first run having examined nothing (its file was on an
     unmerged branch); a rule frozen "from ticket 363" governed no ticket; one global ceiling
     governed every new `SRV-` ticket and no `WEB-` one, because the series are far apart. Freeze
     per series, and say what to do when the sweep is empty.

  4. **VALIDATE AGAINST THE ARTIFACT, NOT A PROXY OF IT.** Export a design frame to PNG and LOOK at
     it before building the surface — five surfaces built from text extracted out of `.pen` frames
     were structurally wrong in seven places. Probe the real permission boundary rather than a model
     of it; a model kinder than the boundary passes what the system refuses. After a change to a
     shared shell or layout, open EVERY surface: one screenshot of the page you were thinking about
     is not coverage.

- **ARCHITECTURE RULES (hard):**
  - **Server** — Airpurifier Engine/Component pattern, event-driven `boost::asio`. One action per
    `_<action>.cpp` file. **NEVER block a handler thread** — long work goes to an async worker.
    **NEVER add a mutex to event-driven code** — asio's answer to handlers that share state is an
    **executor**: give the state its own `strand` and bind its handlers to it.
    `core/` is the reusable framework; product code lives in `app/`.

    The precondition, because the rule above is unsafe without it: **the engine runs a pool of
    threads on one `io_context`** — four by default — so handlers run concurrently and *"the engine
    thread"* is not a thing. State reached from more than one handler needs a strand. This sentence
    was missing for five tickets and the rule read as though a single-threaded reactor made
    synchronisation unnecessary; see `docs/operations/ESCAPES.md`.
  - **Dependency direction** — `api ──▶ catalog · delivery · lane · agent ──▶ manifest ──▶ core`.
    Arrows point one way. Nothing under `app/` may be included by `core/`, and no module may reach
    sideways into a sibling's internals: `api` composes, the others do not know about each other.
  - **The manifest is the only door.** Below `app/manifest`, no module names a path, a workspace, a
    stage, a role or a resource, and no such name means anything to the tool. That is `S1`.
  - **Client** — services in `src/services/` (one http client, one socket client — nothing else
    talks to the wire), pages in `src/pages/`, components in `src/components/`. **Types are
    generated**: `npm run gen:types`, `gen:api-types`, `gen:tokens`. Never hand-write one, and never
    write a colour anywhere but `docs/design/`.
  - **The client never polls.** HTTP loads a surface; `catalog.changed` reloads exactly the document
    it names. `no-poll.test.tsx` fails on a timer-driven fetch or render.
  - Match the existing file's style: **no comments unless asked**, no new patterns, no
    over-engineering. Reasoning goes in `docs/design/<module>.md` — `SRV-FEAT-229` and
    `SRV-FEAT-230` are the pattern.
- **READ THE LEDGER BEFORE §1'S CRITERIA ARE WRITTEN (hard).** Before a single pass criterion
  exists, grep `docs/operations/ESCAPES.md` for the rows this ticket's own subject already has, and for the
  rows its `escaped_from:` target already has. Cite the matching row in §1, and put
  `recurrence_of: <ticket id>` in the frontmatter naming the ticket that filed it.

  This is not bookkeeping. **55% of the ledger is a source leaking a second time**, and the second
  ticket is almost always written as though it were the first: the earlier row is never opened, so
  nothing it learned reaches §1, and the new fix is scoped to whatever the new verdict happened to
  name. A cause with two rows is a cause **this** ticket is answering too, and §1 has to say so
  before it can be scoped to it. Asserted by `test_a_repeat_escape_names_the_earlier_one`, which
  builds source → the tickets that filed against it from the ledger itself, so the citation is
  checked rather than trusted.
- **DESIGN (hard, `factory-client` only — and it is the FIRST commit).** A change to a surface
  starts with a change to `client/web/design/`. Cite the frame in §1, change it, and only then
  write a test against it. Never the other way round, and never both at once with the design
  written afterwards to match what was built: a screen implemented first has no specification it
  can be checked against, and the code becomes its own criterion.

  `client/web/design/BRIEF.md` lists the frames the design owes. `design/README.md` has the loop
  and why this repository, not the canvas, is the source of truth — a design surface that has
  never read `docs/operations/ESCAPES.md` re-decides what the product has already decided, measured on 2026-09-02
  when an export deleted `--st-none` and made `--st-done` the same ink as `--t3`.

  A server ticket has no such stage and owes the equivalent as a RULE: **a ticket that changes a
  module updates `server/design/<module>.md` in the same commit.**
- **A DESIGN CRITERION IS ABOUT THE FRAME, NOT THE LAYER YOU ADDED (hard, `factory-client`).** A
  check that asserts the row a ticket drew — its presence, its position, its copy — passes on a
  frame that contradicts itself one line above it. Assert the frame as a **state**: every number it
  draws beside a collection agrees with the other numbers in the frame, and the frame is a state the
  shipped component can be in — **drive the REAL component with the frame's own numbers and require
  what it renders to be the frame's own copy**, rather than restating the component's rule in an
  assertion that can only agree with itself.

  `WEB-FEAT-332` drew a SEARCH row on a canvas that was never saved; `WEB-FIX-334` saved it, and
  corrected the backlog size in the copy it wrote — and left a different one in the header one line
  above it, which the shipped header cannot draw at all. Its check asserted the row's presence, its
  position and its result-line copy, and nothing asked whether the frame as a whole described a
  state the surface could be in (`WEB-FIX-336`). That is the design-side twin of *a claim about a
  collection is asserted in the same case as the collection*: two facts, each correct, forming a
  false sentence. The general shape, for any artefact a ticket edits one layer of — **the criterion
  is about the artefact.**
- **SPEC** — restate the contract from §1 BEFORE coding: the endpoint's request/response JSON, the
  socket frame's fields, the store's full shape. If `docs/technical/contracts/http.schema.json` or
  `docs/technical/contracts/socket.schema.json` changes, edit it FIRST and regenerate — the client's types derive from
  it, and the browser gate validates the real server against it. State the SPEC in the report; do
  not start IMPLEMENT until it is written down.
- **TEST-FIRST (hard)** — write the §1 assertions as real tests BEFORE the implementation, run them,
  and **quote the RED output** in the report. A test that was never red proves nothing.
- **A STAND-IN IS JUDGED AGAINST THE REAL THING (hard)** — whenever the subject of a ticket is an
  **external program's interface**: its wire format, its argv, its stdin, its exit codes, the shape
  of what it prints. **Capture the real program once, commit the capture as a fixture, and drive
  every case from it — including the mock, the stub, the fake or the stand-in the tests spawn.** The
  fixture is the arbiter; the stand-in has to give the same verdict the recording shows, and where
  the real thing refuses, dies or exits non-zero, so must the stand-in. Say in the report which
  fixture each case rests on and how to re-capture it.

  A mock written by the session that wrote the code cannot disagree with it. `SRV-FEAT-009` chose a
  real child process over a mock — deliberately, and for good reasons about signals and zombies —
  then wrote it to read plain lines from stdin because that is what the tool wrote. Both halves
  agreed; nothing checked either against the CLI; the CLI reads stdin **to EOF as one prompt**, so
  eight green criteria described a session that could only ever have one turn.

  Cost is not a reason to skip it. Capturing is a one-off — the recording is committed and every
  later run is free — and *"a live dependency is too slow / too expensive / too flaky for a gate"*
  is an argument for recording it, never for inventing it.

  **And when what you hand the program is a LOCATION — a home, a config directory, a cache, a temp
  dir — the capture is taken while it does the job the product needs, and it records what the
  program WROTE there.** A capture that only shows the program starting proves nothing about what
  that location must permit, and "it authenticated" is the shape of gate that has now let three
  defects through the same line of code: `SRV-FEAT-039` redirected an agent's configuration
  directory and checked that it could log in; the trust store went with it unexamined
  (`SRV-FIX-047`) and so did a per-session scratch directory the program must CREATE before it can
  start a shell (`SRV-FIX-052`) — so every lane could read, reason, and not run one command, for a
  day, while the product looked like it was working. Enumerate what the redirection moved, and for
  each row say **which way it travels**; a row nobody drove the program through is a row nobody
  measured.
- **A REFUSAL ABOUT A BOUNDARY IS ASSERTED AT THE BOUNDARY (hard).** When a check exists to keep
  something unreachable, a criterion showing the loader said `422` is not the criterion. One case
  must show, from INSIDE the thing being protected against, that what the refusal protects is still
  out of reach — a process in the sandbox that cannot read the file, a client that cannot see the
  field, a lane that cannot open the key.

  `SRV-FIX-077` guarded against binding a whole home into a lane and asserted the status code for
  the two spellings its author had in mind. `/home` is neither of them, contains every home on the
  machine, and loaded cleanly — a lane could read `~/.ssh`, measured as `REACHED-SSH`. Every other
  criterion in that ticket asserted at the receiver; this one stopped at the loader, and that is the
  one that had the hole. A refusal the boundary does not confirm is a refusal about a status code.
- **A CLAIM ABOUT A COLLECTION IS ASSERTED IN THE SAME CASE AS THE COLLECTION (hard).** A count, a
  total, a badge, a summary line, a heading — or a SENTENCE, or an empty state — drawn beside a
  collection is asserted in ONE assertion with that collection: `label === 'RUNS · ' + rows.length`,
  not a case for the label and a case for the rows. And never against the source it was computed
  from: a number and the thing that produced it agree with each other by construction, which is why
  such a case passes while the screen contradicts itself.

  `WEB-FEAT-016` asserted §4.10's table exhaustively and one of its own cases had the defect on
  screen: one row, `RUNS · 4` above it, and the assertion read the row. The count came from the
  ticket DOCUMENT and the rows from the run history, so a lane producing output sat under
  `0 runs · RUNS · 0` (`WEB-FIX-091`). Two facts, each correct, forming a false sentence.
  `WEB-FIX-088` is the same rule from the other side — §4.6's empty-state SENTENCE, *"Nothing has
  run on this ticket yet."*, was drawn on a ticket with two runs while the suite asserted that copy
  verbatim on a fixture carrying four. An empty state is a claim about a collection being empty.
  Assert it against the collection.

  **And the collection asserted must be the one the copy NAMES.** A claim about a collection no
  endpoint in the response carries is a claim the surface may not make, and a criterion that checks
  the copy beside a condition over some OTHER collection satisfies every word above while the screen
  says something false. `WEB-FEAT-001` obeyed this bullet exactly: `dashboard.test.tsx:273` asserted
  *"No requirements in this repository."* in one expression with the condition that draws it, and
  that condition reads `delivery` + `needs_you` + `unpushed` — every one of them built from the
  ticket set, none of them reading a requirement. Both halves agreed with each other, neither was
  the collection the sentence named, and the case shared the mistake with the code (`WEB-FIX-002`).

  So the criterion names the noun and proves it: ask the endpoint that carries the OTHER collection
  in the SAME case, on a repository where the two disagree — epics present, tickets absent — and
  derive which collection moves the answer rather than asserting that it does.
- **A VALUE AGGREGATED FROM A COLLECTION IS ASSERTED ON A COLLECTION THAT DISAGREES WITH ITSELF
  (hard).** Anything reduced from many things to one — a badge over a group, a rolled-up state, a
  worst-of, a total, an "all of these are X" — is asserted on a collection with **more than one
  member, whose members do not agree**. A one-element fixture makes every reduction look like the
  identity: `min`, `max`, `first`, `any` and `all` are indistinguishable on a singleton.

  `SRV-FEAT-165` shipped `?filter=done` over an aggregate that ranked `done` ahead of
  `not_started`, so a requirement with one merged ticket and five never started was returned as
  done. Its three fixtures spread three delivery states ACROSS three requirements, one ticket each:
  every state was present, and the reduction was never exercised, because the defect lives INSIDE a
  requirement and the fixture put nothing inside one. All three cases passed, the browser gate
  passed, and QA found it in one call against the real repository (`SRV-FIX-166`).
- **A CRITERION THAT DISTINGUISHES TWO POSSIBLE SOURCES ASSERTS THEM WHERE THE SOURCES DISAGREE
  (hard).** When the code could be reading either of two values — `HEAD` or a branch, the request or
  the record, the manifest or the filesystem, the cache or the disk — the fixture **forces them
  apart before the assertion**, and the criterion names both: the one that must be read, and the one
  that must not. A fixture in which the two hold the same value proves only that the code read one
  of them, and it passes identically whichever one that is.

  `SRV-FEAT-065` asserted a role lane's independence exhaustively and every measurement was taken
  where the run lane's branch and `HEAD` were the same commit. The tool then cut a QA lane from
  `HEAD` while a concurrent session was committing there: the lane carried three commits of somebody
  else's ticket and none of the fix it existed to judge, and `status: pass`, which only a role may
  set, could be set from a tree the work was not on (`SRV-FIX-184`). One
  `git merge-base --is-ancestor`, taken at a moment when the two disagreed, is the whole of the
  criterion that was missing.
- **A VALUE TWO LANES MUST AGREE ON IS ALLOCATED BY THE TOOL, NEVER COMPUTED BY EACH LANE FROM ITS
  OWN CHECKOUT — AND ANY CRITERION ABOUT IT IS ASSERTED WITH AT LEAST TWO LANES ACTING
  CONCURRENTLY (hard).** A lane is a checkout cut at claim time, and nothing merges into a live one.
  So any value derived from *what this checkout contains* — the next id in a sequence, the next free
  number in a range, whether a name is taken — has as many correct answers as there are lanes, and
  every one of them is right. Name the single owner in §1 (the tool, an endpoint, a file one process
  writes), and write the criterion with **N ≥ 2 callers released together**, never as a loop.

  A sequential loop is green against a read-then-write with no exclusion, which is the shape the
  race lives in: one caller cannot tell a counter from a race. `SRV-FIX-188` is the instance. Every
  lane criterion this repository has ever written asserted **isolation** — own worktree, own branch,
  own configuration directory, own session — all true, all still wanted, and isolation working
  exactly as specified is what produced two right answers to a question with one. *A counter is the
  one thing that cannot be isolated.*
- **A HANDOVER IS JUDGED BY THE STATE IT LEAVES, NOT BY THE ACTION IT REPORTS (hard).** *"I launched
  it"* is a claim about a call. *"A process is alive"* is a fact about the machine. Anything a report
  asserts about work continuing somewhere else — a QA session, a background build — is asserted with
  `ps`, with `git log`, or with the thing itself, and **never** with the existence of a file that
  the FAILING path also creates.

  The skill told every headless agent to hand over with a trailing `&`, which a headless session
  cannot run. The failure left a 15-byte log reading `Execution error`, and the skill's own rule,
  the hook written to enforce it, and the report all read that file's existence as *it ran*. Two
  tickets died that way four days apart, byte-identically, and one report quoted a pid that was
  already dead when the sentence was written (`SRV-FIX-124`).

  **And a process being alive is not the end of the rule either — the state a handover leaves
  includes the launched session's ABILITY TO DO THE JOB IT WAS STARTED FOR, asserted from inside
  it.** `SRV-FIX-124`'s own gate proved a QA session was alive 67 s after launch and asked nothing
  else; `SRV-FIX-351` is what that cost. A session with read-only permissions lives, reads, and
  writes a thorough verdict — it is indistinguishable from a healthy one by every probe outside it,
  and the verdict it produces reads like a QA run that happened. So when you hand work to another
  session, the criterion runs **the commands that work is written in, in that session**, and quotes
  their output. A liveness check is not an ability check, and three tickets have now been decided
  by the difference.
- **A JOURNEY CRITERION NAMES WHAT MUST BE ON THE SURFACE WHILE IT IS WALKED (hard).** *"Leave the
  page and come back and it is still there"* is worth exactly as much as the **it** that happened to
  be drawn when the check ran. State, in the criterion, which kinds must be on the screen before the
  journey starts — and assert they were, so a surface that quietly stopped producing one of them
  fails instead of passing faster.

  `WEB-FIX-051` asserted its away-and-back journey in a real browser, in both themes, with a
  screenshot of each. The lane it walks draws `permission_request · prose · tool · file_edit · turn`
  — and no `command` entry, ever. So `148/148` was green over a defect that destroyed every command
  panel a person had run, permanently, because nothing on the wire replays one (`WEB-FIX-072`).
  Nothing in a passing log distinguishes *covered* from *not present*: a route is not a state.
- **WHEN A TICKET MAKES A MODULE THE SOURCE OF TRUTH FOR A SURFACE, ONE CRITERION DRIVES THAT MODULE
  WITH NO SURFACE ATTACHED (hard).** Moving authority into a store, a cache, a service or a
  singleton moves what has to be asserted with it. A rule enforced by the consumer is not a rule the
  module has — it is a rule that consumer has, and the next consumer will not.

  `WEB-FIX-051` made the client's socket module the seed for every mount and asserted it through
  four cases, every one of which mounts a component. Its own regression case passed through the
  COMPONENT's run-change rule, never reaching the module's, which did not have one. The module
  dropped every early entry of a second run as a replay for two months (`WEB-FIX-073`). One case
  calling the module's own entry point, reading its own state, would have said so on the day.
- **A CRITERION ABOUT A WRITE NAMES WHO ELSE FINDS OUT, AND HOW (hard).** For any change that mutates
  shared state — an endpoint that creates or destroys something, a file the tool writes, a record
  another surface reads — the actor that performed it is the observer that **cannot fail**: it holds
  the answer in the response it just received. State, as an executable criterion, what a SECOND
  observer sees, driven by something other than the actor: another connection, another tab, another
  process, or the tool acting on its own behalf.

  `WEB-FEAT-044` asserted *"the rail shows it with no reload — E12's `201` IS the row"*, which was
  true and stayed true, and every other observer was outside the sentence: no lane claimed by
  anything but the browser you were looking at ever reached a screen. It surfaced when the tool
  began claiming lanes for itself and nobody was told (`SRV-FIX-080`) — a whole feature invisible on
  the one screen it was built for.
- **WHEN A TICKET WIDENS WHAT A SHARED SOURCE CONTAINS, IT NAMES EVERY CONSUMER OF THAT SOURCE AND
  STATES, FOR EACH, WHETHER THE WIDENING APPLIES (hard).** A store, a catalog, a listing, a config
  object: the moment a ticket makes one hold MORE than it did, every reader of it inherits the extra
  by default. Enumerate the readers in §1 — by endpoint, by screen, by module — and write one line
  each: *this one gets the wider answer* or *this one does not, and here is the criterion that says
  so*. A consumer nobody named is a consumer that inherited the change silently, and silence is
  indistinguishable from a decision.

  `SRV-FIX-181` taught the catalog to read every repository a monorepo declares, because no `FAC-*`
  ticket could be claimed in a lane. `E3`, `E4`, `E5`, `E12`, `E22`, `E26` and the lane rail all
  read that one catalog, and they all got the wider answer — so the requirement screen of a roti
  machine began answering, in six of eleven rows, about the tool being used to look at it
  (`SRV-FIX-199`). The widening was right for five consumers and wrong for one, and no line in `181`
  had to be written about which.
- **A RECORD THAT NAMES A LANE IS ASKED, IN A TEST, WHAT IT SAYS AFTER THAT LANE IS RELEASED
  (hard).** Two escapes in three days came from state keyed by something recycled or destroyed:
  `SRV-FIX-247` — `sessionByLane_` keyed by a lane NAME, and lane names are reused, so a new occupant
  resumed a dead session; `SRV-FIX-256` — `roles_` holding the name of a lane that had been released,
  so stopping a checker from the browser retired that ticket's checker for the life of the process,
  silently, while the screen went on drawing a control pointing at it.

  A lane is the one thing in this tool designed to be destroyed and its name reused. Every fixture
  is claim → act → assert → tear down, so a record that outlives the lane is never interrogated.
  **Any record storing a lane name owes a case that releases the lane and then asks the record.**
  `test_roles.py` carries those cases together under one heading.
- **A WORKAROUND YOU BUILT IN ORDER TO TEST THE PRODUCT IS A TICKET (hard).** If the only way to run
  your own real-thing criterion was to build, in a script or a fixture, something the product should
  have done for you — file it as a ticket **before you write the report**, and name it in the
  report's findings with the file and the lines. Two sessions in one batch independently wrote a
  private agent home into throwaway test scaffolding, each said in its own report that the product
  does not do it, and neither filed it; the third session found it as a defect that had stopped
  every lane from running a command (`SRV-FIX-052`). Scaffolding that exists because the product is
  missing a feature is the clearest requirement anybody will ever write down, and it is written down
  in the one place nobody re-reads.
- **SOAK, if the subject waits, polls, forks or races.** Run `server/tests/soak.sh` on the named case
  — 100 runs under CPU oversubscription — and quote it. A test whose premise is "the thing started,
  therefore it is visible" is a coin flip, and a coin flip that lands right is indistinguishable from
  a gate. `SRV-FIX-034` found `test_kill_wedged_process` had been counted as evidence by **eight**
  merged tickets while its premise was false on every run ever made: `&` in a shell returns at
  `fork`, and until `execve` the child still carries its parent's command line, so the `pgrep` that
  "proved" it had started could not see it. Wait on a fact the thing itself produces, never on time
  passing.
- **WATCH THE MANDATORY NEGATIVE FAIL (hard).** **Commit the implementation first**, then break the
  thing the negative guards, run it, quote the failure, commit the reproduction patch beside the log,
  and revert.

  The order is not stylistic. Break it before committing and the obvious way to capture the patch —
  `git diff -- <file>` — produces a diff containing your *whole implementation plus the break*, and
  the obvious way to undo it — `git checkout -- <file>` — deletes work that was never committed.
  That has already destroyed one implementation mid-ticket.

  **Break it by reverting your fix.** If reverting the fix leaves the negative green, it is guarding
  something else and this ticket has no negative — which break you choose *is* the test.
  `WEB-FIX-031` found its mandated negative could not pin its own defect: "the list never answers"
  fails an existence-wait too, so a full revert would have sailed through it. It wrote a third case
  — a list that answers *late* — deterministic-red against the old code, and only then had a
  negative. `SRV-FEAT-002`'s first working sandbox let a write into the main checkout **return 0**
  while both "the file did not appear" and "`git status` unchanged" stayed green.
- **IMPLEMENT** — the smallest diff that meets §1. Fix the ROOT CAUSE; a workaround, a
  retry-wrapper, a sleep, or a "temporary" patch is a FAIL even if the gate goes green. Where a
  table or registry exists, a new case is a table line, not new plumbing.
- **DIAGNOSTIC HYGIENE (hard)** — debug instrumentation belongs to exactly ONE hypothesis. The
  moment the evidence kills that hypothesis, REMOVE its instrumentation before probing the next one
  — never stack probe on probe. Instrumentation that stays must be named in the report with its
  standing purpose.
- **NO-RIDER RULE (hard)** — this ticket may only change behaviour its own §1 assertions cover.
  Anything else gets its own asserted test in the same commit, or its own ticket. A gate proves
  exactly what it asserts: a bundled rewrite whose property is unasserted can invert that property
  with the gate GREEN.

  It constrains **behaviour, not the file list.** A header that must declare a new member, a
  `CMakeLists.txt` that must list a new source, a marker only this ticket may clear — none of those
  is a rider, and a rule read as forbidding them forbids its own fix. If you cannot make the change
  §1 asks for without touching something §1 did not name, touch it and say so in the report.
- **NO-PATCH RULE (hard)** — do not extend a store, a schema or an API response field-by-field
  across tickets. Design the full shape in SPEC, then implement it once.
- **CONVENTION (hard) — the gate that reads the DIFF, not the behaviour.** Run it after IMPLEMENT
  and before BUILD; it takes under a second, and it is the only stage that asks whether the code was
  written the way this codebase is written.

  ```bash
  cd server && ./tools/conventions.py        # factory-server
  ```

  It **ratchets**: every violation already in `tools/conventions-baseline.json` is tolerated and a
  NEW one fails. `--list` prints what is currently accepted. A workspace with no checker declared
  skips this and **says so in the report**.

  **`--accept` is not how you make it green.** Re-recording the baseline is a deliberate act that
  goes in the report with a reason, exactly like softening a criterion — which it is.

  The reason this stage exists is in `docs/operations/ESCAPES.md`, and it is structural rather than a matter of
  taste. An escape is a defect found after a green gate, so escapes are BEHAVIOURAL; a convention
  violation never fails a test, never causes an incident, and therefore never produces a row. The
  ledger cannot learn from what does not break on the day.

  **And a rule that grep cannot see belongs in the code, not in the checker.** The first version of
  this gate carried a `registration-in-start` rule for the exact architecture defect that motivated
  it; the rule reported zero, the defect was reintroduced, and it still reported zero — the call in
  `start()` went through a private helper one frame down. It was deleted and replaced by a refusal
  in `core/file.cpp`: `Watch::add()` after `start()` is `LOG(FATAL)`, and the process aborts naming
  the directory. Prefer that shape. **A convention the compiler or the constructor can refuse is
  worth more than a convention a script can grep, and both are worth more than a paragraph here.**
- **BUILD** — every target named in §1:
  - `cd server && ./build.sh build`
  - `cd client && npm run build` (`tsc -b` must pass — type errors are build failures)
  A warning-free build is expected; justify any new warning.
- **GATE — tiers 1–3 plus the document gate, run them, quote them; never claim by eyeball.**

  **This repository runs tiers 1–3 per ticket, NOT the whole suite.** At 479 tests and 16 minutes,
  re-run at every stage, the full suite cost 1–2 hours per ticket to validate five new assertions.
  Tier 4 is a RELEASE gate.

  1. **Tier 1 — the ticket's own assertions.** Every §1 case, by name, with its output quoted.
  2. **Tier 2 — the module it touched, plus the isolation floor, always:**
     ```bash
     cd server && pytest tests/test_<module>.py tests/test_isolation.py tests/test_confine.py -v
     cd client && npm run test
     ```
     `test_isolation.py` and `test_confine.py` run on every ticket regardless of what it touched —
     21 s, and they are the two properties a lane cannot be allowed to lose quietly.
  3. **Tier 2b — the browser gate, SCOPED to the surfaces §1 names, for any client-visible
     change:**
     ```bash
     cd client && npm run gate -- --surfaces <the surfaces §1 names> --themes dark
     ```
     It builds the bundle, starts the C++ server against `gate/fixture-repo`, serves the build
     through `vite preview` and drives Chrome headless: rows, groups, markdown, the socket chip,
     the schema, contrast, and a screenshot of every surface it was given. **A green vitest run is
     not a screen anybody can read.**

     **The scope is GATE's; the whole sweep is VERIFY's** (`WEB-FEAT-379`). Unscoped this is ten to
     twelve minutes of wall clock and client tickets launched it fifteen to thirty-six times in one
     run, because there was no way to run less than everything. The names are the first column of
     `client/web/gate/surfaces.mjs`'s `SURFACES` and `LIVE_SURFACES`; `FACTORY_GATE_SURFACES` and
     `FACTORY_GATE_THEMES` say the same thing and the argument wins; an unknown one is refused by
     name before any phase runs. The inventory, the vitest suite and the build are **never** scoped.

     **A check the scope left out is printed and counted — `SKIP(scope)` per check and
     `skipped(scope): n` in the summary — and decides nothing.** `docs/operations/ESCAPES.md`, 2026-07-29:
     five client lanes were told not to run the browser gate so five headless Chromes would not
     make each other flaky, all five merged on a green vitest run, and four gate assertions reached
     `main` having never once executed. A way to run less than everything is that instruction with
     a nicer interface unless a check that did not run is visibly a check that did not run.

     Every run ends with what each phase cost — `inventory 1s · vitest 61s · build 6s · server 2s ·
     sweep 190s · total 4m 20s`. **Quote that line in the report.**
  4. **The document gate — for a ticket that changed any document under `docs/`**, its own
     `status:` line included. **One command, and it is not `pytest`:**
     ```bash
     cd server && ./tools/docgate.py --ticket <TICKET-ID>
     ```
     The gate is red on `main` today for reasons older than any one ticket, so its exit status is
     not a verdict about your branch. **Own what your own commit adds:** the script runs
     `pytest tests/ -m repository` here, runs the same command again in a scratch worktree at your
     merge-base, and reports the failing SET the branch ADDS — **exit 0 added nothing, exit 1
     names what it added, exit 2 the question could not be answered, and a gate that could not be
     answered is never reported green.** The set is the case names **and the ids each failing case
     lists**: half of them enumerate the tickets they refuse, so a case that is already red grows
     your ticket's name without the list of case names changing at all. That is exactly how
     `WEB-FIX-328` reached QA with the gate red naming `WEB-FIX-328`, and how `WEB-FIX-334`
     merged with it (`SRV-FIX-384`). The comparison is the script's job now, not yours, and it
     writes both raw logs to `docs/reports/<TICKET-ID>/` for you.

     52 cases, ~30 s a side, and they read the DOCUMENTS a ticket writes rather than its code.
     The commit that sets `status: awaiting-qa` is the commit that puts a ticket into the domain
     of half of them, so run it AFTER that commit. It is not a fourth tier and it does not replace
     tier 3. `SRV-FIX-354` is open against the gate's root resolution inside a lane worktree; the
     script prints the two `server/` directories it ran from, and the report quotes that line
     rather than claiming it.
  5. **Tier 3 — DOGFOOD. Drive the ticket in the web client, by hand, as a person.**
     `./build.sh run "$PWD"`, open http://localhost:7779, and run the ticket from the screen the way
     somebody would who wanted the thing it does.

     **A green browser gate does NOT satisfy this.** The gate drives `gate/fixture-repo`, a
     synthetic repository, which is exactly what `WEB-FIX-248` and `WEB-FIX-261` are about. The
     report records what was driven and what the screen did; **"I ran it" is not evidence.**

     A bug found in the client here becomes its own `FAC-*` ticket and the current ticket
     **continues** — the interruption is how this backlog gets written by use rather than by
     inspection.
  6. **TRIAGE every FAIL to its owning module** with evidence: wrong bytes or fields on the wire ⇒
     the sender; correct data, wrong display or state ⇒ the receiver. Then:
     - Root cause **inside this ticket's scope** → fix → rebuild → re-gate. **Loop to green, cap 4
       rounds**, then stop and report the failing output. **Never re-word an assertion to make it
       pass.** Inside the loop the failing assertion runs alone; run the full tier 1+2 ONCE before
       the round ends. A round that never ran the tier is not a round.
     - **A QA FAIL is a missing assertion, not just a broken product (hard).** When QA hands this
       ticket back, the round's **first commit is not the fix** — it is the assertion that would
       have caught what QA found, RED, and **transcribed from QA's own reproduction** (the commands
       and raw output in the verdict), never from the criterion's name. Then fix until it is green,
       then the tier. A QA FAIL is the same evidence as an escape — a gate said yes, or said
       nothing, where it should have said no — so the check lands in the **same commit** as the fix.
       `SRV-FIX-059` is why, twice over: its round 4 went green on a test with the right name because
       the fixture repository had no `.gitignore` and its lane never ran, so *the one shape the
       criterion was about was the one shape untested*; and five rounds of patching left five
       patched clauses where they should have left five assertions.
     - Root cause **out of scope** → file a ticket for EACH, from this template, §1 filled, with the
       failing output as evidence. Mark the assertion **BLOCKED(<ticket>)** in the report — never
       silently absorb an out-of-scope fix.
- **SAFETY (hard)** — obey §1. Default: this repository only. **Never remove a lane checkout without
  asking the tool whether it owns a live lane there.** `git status` answers *does this hold
  uncommitted files*; it cannot answer *is an agent running in it*. Only `GET :7777/api/lanes` can.
  On 2026-08-06 a session surveyed four lane worktrees, found `dirt=0` and `ahead=0`, called that
  "verified empty" and removed them — one was 410 s into a run. Release through the tool
  (`DELETE /api/lanes/<lane>?force=true`), which kills the process and frees its resources, and only
  then remove anything.
- **REVIEW — read the diff against this repo's rules.** QA asks *did it do what §1 said*. Nobody has
  yet asked *is this code any good*, and those are different questions: work can meet every
  criterion and still be a mess somebody lives with for three years. Read the whole diff against
  `CLAUDE.md` — the engine is never blocked, no mutex in event-driven code, one action per
  `_<action>.cpp`, nothing under `app/` included by `core/`, no colour outside `docs/design/`, no
  hand-written generated type. Name what you would change and change it, or say why it stands. A
  review that finds nothing on a diff of any size has not happened.
- **IF YOU WERE TOLD YOU ARE PART OF A PARALLEL BATCH: stop at GATE.** Set `status: awaiting-qa`,
  commit on your branch, report what you added to every file the batch shares, and start nothing.
  The QA and MERGE bullets below are for a ticket running **alone** — in a batch they happen once,
  over the integrated union. This paragraph exists because §2 is fixed and a batch agent was
  otherwise handed two instructions it could not both obey.
- **SEVERITY — only a BLOCKER stops the merge.** Every finding, yours or QA's, carries one:
  `blocker` (a person cannot do the thing §1 exists for; work is lost; a working surface broke; it
  is unsafe) or `minor` (real, worth fixing, and they can still do it). A ticket with no blocker
  merges — `PASS WITH MINORS` — with **every minor filed as its own ticket, id from the tool, in
  the same commit**, because an unfiled minor is a blocker. **A red §1 criterion is never minor:**
  §1 is the human's statement of what the feature is and no agent downgrades the request it was
  given; if it truly does not matter, the human edits §1. **The same cause a second time is a
  blocker** — it has stopped being cosmetic and become evidence about the check. A filed minor that
  ships is an accepted risk, and an accepted risk is a decision; an unnoticed one is not.
- **VALIDATE BEFORE YOU HAND OVER (hard).** GATE does not end while a single §1 criterion is
  unexecuted or red. **This session runs every one of them itself**, quotes the output, and fixes
  what is red until it is green; a criterion that cannot go green is BLOCKED by a ticket filed with
  its failing output, and the block is named in the report before the handover, never inside it.
  A criterion nobody ran is not a criterion that passed.

  **The last command before `status: awaiting-qa` is the check itself**, and it reads the ticket
  and the committed logs rather than your account of them:

  ```bash
  server/tools/handover.py docs/tickets/<file>.md
  ```

  One row per criterion — the case, whether it exists, the log it was RED in, the log it is GREEN
  in — and a non-zero exit naming every miss. Exit 0 is the handover. A red row is fixed before
  it, never inside it, and `manual: N` is how much of this ticket no program has checked
  (`SRV-FEAT-480`).

  **QA is started only when every one of them is green.** Handing over earlier spends a whole
  session establishing what this one already knew — `WEB-FEAT-001` was handed over at `PARTIAL` on
  2026-09-02 and its QA returned nine criteria none of which it had verified. Validation is this
  session's job; verification is the next session's, and they are not the same job done twice.
- **QA — a SEPARATE SESSION, not a subagent of this one (hard).** The agent that built it cannot
  grade it: it will check the cases it thought of, which are the same cases it coded for.
  - **The implementing session STARTS it, once validation is green. It does not ask a person to.**
    A handover that needs somebody to notice is a handover that gets skipped — three tickets merged
    unQA'd before this line was written, each one ending politely with "next step is yours".
    Separate *session* is the requirement; a manual trigger never was.

    ```bash
    # last thing before the report is written, from this repository's root, not blocking:
    .claude/scripts/start-qa.sh docs/tickets/<file>.md
    ```

    **No trailing `&`, and that is the point.** A headless session (`claude -p`) cannot run a
    command ending in `&` — it is rejected as deferring execution past approval-time checks, and
    there is no prompt to accept it. The `&` lives inside the script, along with `--model
    claude-opus-5` (the model rule, not negotiable) and `--permission-mode acceptEdits`, without
    which the session does the entire check and then discards it asking for a permission nobody is
    there to give. **In a lane you do not run this script at all** — report the `QA` stage and stop;
    the tool claims the qa-role lane itself, and running both races two sessions for one verdict.

    **Tell it to commit the verdict the moment it writes it, and again after each criterion.** Four
    QA runs on this repository have now ended early; the only one that survived usefully was the one
    committing as it went, and it let the next session finish the two criteria left rather than redo
    eleven.

    A nested headless session is a real, separate process with its own context — which is exactly
    what "not a subagent" asks for. The implementing session then reports the log path and the fact
    that QA is running; it does not wait for it, read it, or answer it.
  - **You may not start QA on a ticket you have not finished**, and you may not start it twice. If
    the log already exists, say so instead of launching another.
  - QA is given **§1 and the repo only**. Not the implementer's report, not their summary, not their
    explanation of why something is fine. A narrative is exactly what a fresh checker must not have.
  - QA re-runs every §1 criterion itself, from the commands in §1 — it never accepts a quoted result.
  - QA **defaults to REFUSE**: a criterion it cannot verify from raw output is a FAIL, not a pass.
  - For a ticket with a user surface, QA opens the gate's screenshots (static and live, both themes)
    and writes a per-surface judgment; no judged screenshot, no pass.
  - QA writes `docs/reports/<ticket-id>-qa.md` with its own verdict per criterion.
  - **Only a QA PASS lets `status:` become `pass`.** The implementing session may never set it.
- **MERGE — on `status: pass`, the branch merges to `main` and is pushed. There is no PR.**

  The factory has one developer, so a pull request has no reviewer and the gate it represents does
  not exist; **the QA session IS the gate**, and a passed ticket waiting for a formality is finished
  work that nobody can use. **This applies to `FAC-*` tickets only** — every ticket in a repository
  the factory is pointed at keeps whatever flow that repository has.

  The merge is not a licence to skip anything before it. **A ticket that FAILS merges nothing.**

  **ONE COMMIT PER TICKET REACHES `main` — `git merge --squash`, never a merge commit and never
  the branch's own history.**

  ```bash
  # WHO WROTE IT SURVIVES THE MERGE. A squash keeps neither of git's two names by default:
  # `git commit` writes the person merging as BOTH author and committer, so a member's work
  # becomes yours the moment you land it — silently and permanently. DERIVE, never type.
  AUTHOR=$(git log --no-merges --format='%an <%ae>' main..ticket/<id> | sort | uniq -c \
           | sort -rn | head -1 | sed 's/^ *[0-9]* //')
  TRAILERS=(); while read -r who; do [ -n "$who" ] && TRAILERS+=(--trailer "Co-Authored-By: $who")
    done < <(git log --no-merges --format='%an <%ae>' main..ticket/<id> | sort -u \
              | grep -vxF "$AUTHOR")

  git checkout main && git merge --squash ticket/<id>
  git commit --author="$AUTHOR" "${TRAILERS[@]}"
                      # <TICKET-ID>: what a person can now do — then why, and what the gate
                      # could not previously say, and what the mandatory negative proved
  git push origin main
  ```

  A branch carries the round it took — a red test, a fix, a second fix, a rename, a revert — and
  that is evidence while the ticket is open and noise once it lands. `git log --oneline main`
  answers *what changed and why*, one line per ticket. The branch keeps the transcript and is not
  deleted until the report is committed.

  The squashed message is the ticket's ONLY surviving account, so a thin one destroys what the
  round learned. That is this rule's cost and it is paid in the message.

  1. **Read `git diff --stat main...HEAD` before merging, and put the file count in the report.**
     Every gate above asks whether the code works; none asks what the commit *contains*. A `git add`
     that swept in a build directory passes every test and merges 4 MB of one machine's absolute
     paths into `main`. If a file in that list is not something you meant to write, it does not go
     in — and if it is generated, it belongs in `.gitignore` in the same commit.
  2. **`git fetch`, then merge `main` INTO the branch first, resolve there, and only then merge
     out.** `main` is shared with concurrent sessions, and a conflict resolved on the branch is a
     conflict nobody else sees.
  3. **`docs/operations/ESCAPES.md` conflicts on almost every integration** because every branch appends a row
     to the same table. **Keep BOTH sides, always.**
  4. **Once QA has started, the branch is frozen** until its verdict lands. No commit, no amend, no
     rebase. A verdict is pinned to a sha; move the sha and the verdict is about code that no longer
     exists, which has already cost one full re-run.
- **VERIFY — after the merge, on `main`, not on your branch. Run the full suite once.**
  ```bash
  cd server && pytest tests/ -v            # tier 4
  cd server && pytest tests/ -m repository # the document gate
  cd client && npm run test && npm run gate   # UNSCOPED — every surface, both themes
  ```
  The browser gate here takes **no** `--surfaces` and **no** `--themes`. GATE ran it over what §1
  named; this is the run that says the surfaces the ticket did not name still work, and it is the
  only place the union of two branches is swept whole.
  This is where a defect that exists only in the *union* of two branches shows up, and it is the
  only place it can: each branch was green against a `main` that did not yet have the other's
  change, so both gates were honestly green and both were incomplete. It has happened twice — a
  shared field two tickets widened to mean different things, and a new test on one branch rejecting
  documents the other branch was still writing. A ticket is not finished when it is merged; it is
  finished when the merged thing works.
- **ESCAPE RULE (hard) — a green gate that let a bug through is a defect in the CHECK.** If a defect
  is found after this ticket passed — in another session, in the client, or reported by whoever uses
  it — then fixing the bug is only half the job:
  1. the `FIX` ticket carries `escaped_from: <this ticket>` in its frontmatter,
  2. it answers §3's five questions,
  3. that change lands in the SAME commit as the fix — a process change deferred is a process change
     that never happens,
  4. one row is added to `docs/operations/ESCAPES.md`.

  "Be more careful next time" is not an answer. Neither is fixing the symptom and moving on. When
  two escapes share a cause, that cause is the next ticket.
- **KEEP THE LOG (hard).** Every capture a gate was decided on is copied to
  `docs/reports/<ticket-id>/` and **committed** next to the report —
  `<ticket-id>-<what>-<date>.log`. The report still quotes the deciding lines, because a reader
  needs the answer without opening a file; the raw capture is there so QA can check the quote was
  not the flattering half. A gate whose log was thrown away is a gate nobody else can audit.
  Captures over 1 MB are gzipped; if anything is trimmed, the report says exactly what was cut.
- **DELIVER** a report: one line per stage, the assertion table (assertion → PASS/FAIL/BLOCKED → the
  quoted output line), the exact commands to reproduce, the `git diff --stat` file count, and any
  new endpoint or socket frame to add to `CLAUDE.md`. **SAVE it to
  `docs/reports/<ticket-id>-report.md`** (link the ticket, note branch/commit + date). In chat,
  deliver the outcome, validation summary, outstanding blockers and a link to the saved report.
  Commit on `ticket/<id>` with prefix `<ticket-id>: `. I will NOT read
  code — the report is the deliverable. Only ask if a §1 behaviour is genuinely undecided.
- **ENGLISH ONLY (hard)** — this ticket, the report, the QA verdict, every commit message, every
  test name and every line of reasoning is written in English, including in a session driven
  entirely in another language. The human writes requests in whatever language they like and that
  never changes; the agent answers in English. The reason is not style: pass criteria are executable
  assertions and verdicts quote raw output verbatim, `SRV-FIX-042` already failed once for asserting
  a model's word choice, and a clause that loses precision in translation becomes a wrong assertion.
- **NO TOOL ATTRIBUTION, ANYWHERE (hard)** — no `Co-Authored-By`, no vendor or model name, no
  generated-with footer, in **any** commit message, report or document. The record carries the work,
  never the tool that produced it.

---

## 3. Escape analysis  (ONLY on a FIX with `escaped_from:` — otherwise delete this section)

- **What the gate asserted:** <quote the criterion from the escaped ticket's §1, verbatim>
- **Why the bug satisfied it:** <the mechanism — a proxy was tested, a fake answered better than
  reality, the criterion covered state and the bug was timing, coverage stopped short>
- **The criterion that would have caught it:** <the BEHAVIOUR that would have been wrong, and the
  test that evidences it — a named case or a gate command. Not a bare test name>
- **What changes so the whole class is caught:** <a template rule, a skill rule, a gate script, a
  new mandatory negative — something structural. If nothing reasonable would have caught it, say so
  plainly; an accepted risk is a decision, an unnoticed one is not.>
- **Where else is this cause, right now?** <the four questions above are all about the CHECK, and
  every one of them can be answered without opening the tree. This one cannot. Run a query over the
  repository — a `grep`, an `rg`, a script — that finds the cause rather than the symptom, and paste
  **the query and its VERBATIM output** in a fenced block. Then one **disposition line per hit**, at
  the start of a line:

  - `- fixed here: <path:line>` — it is repaired in THIS commit,
  - `- filed as <TICKET-ID>: <path:line>` — the ticket exists, in this commit, and names the site,
  - `- no other site: <why the query is the right one>` — the query found nothing.

  A query that finds nothing is a valid answer; silence is not, and neither is prose about having
  looked. 55% of `docs/operations/ESCAPES.md` is a source that leaked twice or more, because a class named in
  an analysis gets fixed at the one site the verdict happened to name and ships everywhere else.
  Asserted by `test_an_escape_ticket_above_the_ceiling_names_where_else_the_cause_is`.>
- **Landed in this commit:** <the file(s) the process change touches>
