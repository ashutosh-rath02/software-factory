# The factory — specification

[Documentation](../INDEX.md) / [Product](README.md)

`factory-v0.1.0` · derived from `FAC-EPIC-001`, `ARCHITECTURE.md`, `manifest.schema.json` and the
design `client/web/design/Dashboard.dc.html` (the dashboard) with `Ticket.dc.html` (the detail it links to,
and the file the client's tokens are generated from).

`MODEL.md` is the layer above this one. This document is description — what gets built; `MODEL.md`
is constraint — the six invariants a ticket may not contradict, the version finish lines, and the settled
decisions. The four exclusions in §1 below are decisions in that sense: inverting one is a change to
`MODEL.md` §4, never a ticket.

This is what gets built. It is written so an implementation session can be run from it without making
product decisions of its own: where the design decided something, this records the decision; where
the design was silent, this decides and gives the reason in a line. The design is **accepted as
delivered** and is the source of truth for anything visual — nothing here redesigns it. Where this
document and the design disagree, §8 names the disagreement and which one wins.

Section 11 is the checklist a review session re-runs against this file, with the commands.

---

## 0. Table of contents

| § | |
|---|---|
| 1 | What this specifies — and the four things it does not |
| 2 | The one boundary, and the manifest that is the only door through it |
| 3 | Vocabulary: run, session, turn, lane · the three state axes · "needs you" · delivery |
| 4 | Screens — fourteen surfaces, each with its data named |
| 5 | HTTP — 23 endpoints, each with method, path, request, response, missing-thing |
| 6 | Socket — 16 messages, each with direction, payload, and when it is emitted |
| 7 | Features — 31, each with an acceptance criterion that can be run |
| 8 | Where the design and the ticket disagree, and which one wins |
| 9 | Manifest field map — every field used, no field unused |
| 10 | Architecture compliance — which module owns what, and what may not know it |
| 11 | How to check this document |

The screen list in §4 and the endpoint list in §5 are the inventory the first two checks in §11 run
against.

---

## 1. What this specifies — and what it does not

One local tool. It reads whatever repository it is pointed at, shows that repository's requirements
and tickets, runs agent processes in isolated lanes against them, and streams what those agents do to
a browser. One developer, one workstation, one repository, one tool process.

### Deliberately not covered

1. **Anything remote or multi-user.** No authentication, no accounts, no sessions-as-identity, no
   running an agent on another machine, no two people looking at the same factory. Every one of those
   changes the security model, and the tool binds to loopback only. This is the single largest
   omission and it is deliberate: it is what keeps the whole thing free of a permission system.
2. **Writing the target repository's documents.** Requirements, tickets and reports are read-only in
   this tool. They are written by an editor or by an agent inside a lane, and git is their history. A
   UI that edits them would be a second writer racing the first, and the state model in
   `ARCHITECTURE.md` ("the documents are the database") only survives if there is exactly one writer.
3. **Pull requests, review, and judging whether work is any good.** Opening a pull request,
   reviewing one and watching CI are jobs the forge already does better, and a merge button is
   precisely the control that must not be one click away from an agent's output. Narrowed by
   `MODEL.md` D3 on 2026-09-07: the tool performs exactly one merge, the squash of a ticket branch
   that a separate QA session marked PASS, on its own account, and pushes it. Nothing a person
   clicks and nothing an agent writes causes that merge except the verdict.
4. **Deciding whether work is any good.** The tool records what an agent claimed and whether it cited
   a run for it (§3.4). It does not score, lint, or judge; there is no "quality" number anywhere in
   it. Judgement is a human's, or another agent's inside a lane.

Two smaller ones, stated so nobody has to guess: no general scheduling or queueing of *work*
(only of exclusive resources), and no cost budget or spend cap — cost is reported, never enforced.
E33 is a bounded exception: an explicitly submitted finite sequence of already declared commands
runs locally in one lane. It returns decisions to main and cannot grade or complete a ticket.

---

## 2. The one boundary

> The tool knows **agents, lanes, resources, sessions, documents**.
> It does not know what any of those documents mean, what the repository builds, or what its stages
> are called.

Everything repository-specific arrives through one door: `.factory/factory.json`, validated against
`manifest.schema.json`. The `manifest` module is the only module that knows the target repository has
a shape (§10). Field-by-field usage is §9; the contract rules are the epic's:

- `factory` carries `major.minor`. An unknown **major** is refused with a message naming both
  versions — never guessed at. An unknown **minor** loads, with unknown keys reported and ignored.
- Additive changes only. A field may be added; a field may not change meaning.
- Nothing else in the repository is required. A repository with a manifest and no documents at all
  loads, and shows empty lists rather than an error.
- **One manifest is one instance's view, and a directory may have its own.** The tool is pointed at
  a root and reads the manifest at that root; a subdirectory that carries a manifest of its own is
  simply another root, and pointing a second instance at it is the whole mechanism. So a monorepo
  holding a product and the instrument used to build it does not have to choose between showing
  both and losing one: the product's manifest declares the product, the instrument's manifest
  declares the instrument, and each screen answers about the repository its instance was given
  (`SRV-FEAT-244`). Declaring a second repository under `repos` is the other answer, and it is the
  right one when the two really are one body of work.
- **And that root is the DOCUMENT root, which is not necessarily the GIT root** (`SRV-FIX-290`). The
  document root is what the operator passed: where `.factory/factory.json` and the `docs/` tree
  live, given and never discovered. The git root is where `.git` is, and lane worktrees and
  confinement need *that* one. For a repository whose manifest sits at its own top level they are
  one directory, which is every fixture this suite had — and for a manifest inside a monorepo, which
  is the case the paragraph above exists to support, they are not. So the git root is **discovered**
  by walking up from the document root, `E1` reports both so an operator can see which is which, and
  a root with no git repository above it is refused at `E12` with `no_git_root` rather than at `E18`
  after a branch and a worktree have been made: a lane that cannot be confined can never be run, and
  a tool that cannot run a lane must not hand one out. Until this was fixed the factory could read
  and claim its own tickets and never run one, so every `FAC-*` ticket ran outside the tool.
- **A lane's worktree is a checkout of the GIT root, so every document path resolved inside one is
  relative to the document root's position WITHIN it** (`SRV-FIX-293`). `SRV-FIX-290` made the two
  roots differ; every path the tool had been building still assumed they were one. A ticket at
  `<document root>/docs/tickets/X.md` is at `<worktree>/<offset>/docs/tickets/X.md`, where the
  offset is the document root's path below the git root — empty for a repository whose manifest is
  at its own top level, which is why nothing caught it. The tool joins such a path in **one place**,
  and that place takes no root as an argument: it reads the git root itself, so no caller can pass
  the wrong one. A refusal naming a path is naming the path the tool actually looked at, and
  `ticket_not_on_branch` may not be reachable for a document that is present — an operator who can
  disprove the stated cause with one `ls` has been sent to look for a commit that is not missing.
- **The git root is the repository GIT reports, not a directory whose name happens to be `.git`**
  (`SRV-FIX-292`). A linked worktree — anything `git worktree add` made, which is how this
  repository runs every parallel session and every QA — carries a `.git` **file** holding
  `gitdir: …`, and it encloses a repository in every sense git understands. So the walk asks *which
  repository is this*, not *what filesystem type is this*: a `.git` **directory** naming a
  repository counts, and so does a `.git` **file** whose pointer resolves to one. A name with no
  repository behind it — absent, a stale pointer, an empty directory wearing the name — counts as
  nothing and is refused at `E12` by name.

  For a document root inside a linked worktree the git root is **that worktree**, not the
  repository it was cut from: the worktree is the checkout `git worktree add` runs in and the tree
  confinement binds, while the common directory `--git-common-dir` names holds the objects and refs
  and is not a checkout at all. Confinement follows the same pointer — it binds the common
  directory read-only wherever it really lives, and the lane's own git directory writable.
- **The walk has the ceiling git has, which is none.** A manifest root buried under a repository
  that has nothing to do with it is served, and a lane is handed out there. That is deliberate: git
  answers the same, so a person standing in that directory gets that repository from `git status`,
  from `git worktree add`, and from every commit an agent makes in a lane cut there. A depth limit
  or a `--git-root` override would be a second answer to a question that already has one, and the
  operator chose the directory they passed. `E1` reports both roots so the choice is visible rather
  than silent.

### What the tool reads out of a document

The manifest is the only door for *shape*. It is not the only contract: the tool also reads five keys
out of a document's frontmatter, and those five words are fixed. A repository adopting this tool
writes them; it does not get to rename them, and the manifest offers no field that would let it.

| Key | On | What it decides |
|---|---|---|
| `id` | requirement · ticket | the document's identity, and what `{id}` in a URL matches. Falls back to the file's stem. |
| `parent` | requirement · ticket | the only link upward, and it always lives on the child. **On a ticket** it names a requirement, resolved **in the ticket's own repository first, then by id in any other declared repository** that holds exactly one requirement of that id (SRV-FEAT-555): a value that resolves nowhere — `standalone`, empty, or an id no declared repository carries — puts the ticket in the synthetic `standalone` group (E3, E4) rather than nowhere. **On a requirement** it names another requirement, resolved **by id across every repository the manifest declares** (`SRV-FEAT-547`): E3 carries `parent` and `children` on every row and the tree is those links, the counts roll up over every descendant, and a value that resolves to nothing or closes a cycle leaves every requirement involved at the top level with an `unknown_parent` or `parent_cycle` warning and never an error. A requirement never lists its children — only one of the two can be wrong at a time. |
| `workspace` | ticket | which group it falls in (E4) and whose stage sequence its `n/m` is counted against. **The value must be one of the names declared under `workspaces` in the manifest**; anything else, or nothing, groups as `unassigned`. |
| `status` | requirement · ticket | passed through and displayed as an opaque string (§10). |
| `stages` | ticket | this document's own sequence. Present → `stage.source:"ticket"`; absent → the manifest's `stages`, and `stage.source:"manifest"`. |

Every other key in the frontmatter is carried through untouched and reaches a screen only because
`display.ticket_fields` named it (E5, §4.5). A repository that already calls one of these five
concepts something else renames it in its documents; the alternative — a manifest field naming which
key means `workspace` — was considered and refused, because the day two repositories give the same
structural concept two names is the day this table stops being a contract and starts being a
default. `title` is read as well and is deliberately not on this list: it is a label, and a document
without one falls back to its id rather than failing.

The failure mode this table exists to prevent is not an error message. A repository whose tickets
name the concept something else parses cleanly, lists correctly, and quietly groups **everything**
into `unassigned` — so F3's grouping, the reason the screen groups at all, applies to nothing, and no
assertion anywhere goes red. A repository adopting the tool should assert this about its own
documents, once, in its own suite.

### What the tool writes

`.factory/` in the target repository, and nothing outside it:

| Path | What | Committed? |
|---|---|---|
| `.factory/factory.json` | the manifest | yes, by a human |
| `.factory/permissions.json` | granted permission rules (§7 F22) | yes — it is a policy, and it should be reviewable in a diff |
| `.factory/worktrees/<lane>/` | lane checkouts | no |
| `.factory/runs/<run-id>/transcript.jsonl` | one append-only log per run | no |
| `.factory/runs/<run-id>/meta.json` | run identity, outcome, cost | no |
| `.factory/ids.json` | the ticket ids E26 has handed out, by series | no — the tool's own memory, not a policy |

`ids.json` is the same kind of thing one row down: losing it costs a gap in a sequence and never a duplicate, because the high-water mark itself is read from the documents and this only remembers the ids that were reserved and never written (E26). A target repository that does not ignore it watches its own `git status` change, exactly as for the two rows above — and the tool does not edit anybody's `.gitignore`.

The run logs are not the database `ARCHITECTURE.md` forbids. That rule forbids a second source of
truth about the repository's documents; a process's own output log is not about the documents and
nothing else can produce it. Losing `.factory/runs/` costs history, never correctness — every screen
that reads it degrades to "no runs recorded" (§4).

---

## 3. Vocabulary

### 3.1 The four nouns

- **Lane** — one worktree, one branch, one agent seat, and possibly one exclusive resource held. A
  lane is created by claiming it and lives until released. Lanes are named `lane/NN` by the tool.
- **Session** — the agent CLI's own resumable conversation. One session may span several runs.
- **Run** — one agent process, spawn to reap. Identified by `run_id`, injected into the process's
  environment as `FACTORY_RUN_ID`. This is the unit the RUNS table lists and the unit a report cites.
- **Turn** — one human message and everything the agent produces before it stops. Cost and duration
  are accumulated per turn, and summed to the run, the lane, and the ticket.

A lane holds at most one run at a time. A ticket may have had many runs, in several lanes, over
several sessions — the RUNS tab is the record of all of them (§4.10).

### 3.2 The three state axes — never flattened

Straight from the design's vocabulary sheet, which is the authority here.

| Axis | Values | Meaning |
|---|---|---|
| `process` | `not_started` · `queued` · `running` · `blocked` · `idle` · `finished` · `crashed` · `killed` | where the process is |
| `motion` | `producing` · `silent` (with `silent_for_s`) | whether anything is arriving |
| `evidence` | `nothing_claimed` · `claimed_unproven` · `claimed_proven` | what has been claimed and whether a run backs it |

Two additions to the design's five process values, both required by §1 of the ticket and neither
visible as new decoration:

- **`queued`** — the lane exists and holds a ticket, but an exclusive resource it needs is held by
  another lane. No process exists yet.
- **`blocked`** — the process is alive and mid-turn, and cannot continue until a person answers.
  `blocked.reason` is `permission` or `question`. This is the state the ticket's feature 2 asks for,
  and it is distinct from `running · silent` (nobody is being asked; it is just slow) and from `idle`
  (the turn ended, and the agent is not mid-anything).

`crashed` is the process dying on its own. `killed` is a person killing it (§7 F16). They are not the
same fact and the RUNS table shows them differently.

The design's rule holds: **the axes are never flattened into one status.** Two combinations are
alarms and carry a border; nothing else does — `running + silent`, and `finished + claimed_unproven`.

### 3.2b One derived view is permitted, and its derivation is written here

`§3.2`'s rule is about STORAGE and about the wire: no endpoint serves a field that is three axes
squeezed into one, because a reader cannot get the three back out of it and two surfaces then
disagree about what a word meant. `deliveryCount`'s own description records the consequence — the
design's strip *"mixes a lane's `process`, a role's state and delivery into one line, which is
§3.2's rule broken in the one place a person most wants it broken."*

**That last clause is the honest one, and it is why one exception exists.** A manager's first
question is *where is the work stuck*, and the answer crosses all three axes. Refusing the question
does not make it go away; it makes somebody compute it in their head, differently each time.

So: **the axes stay separate in the data, and exactly one flattened PRESENTATION axis is allowed,
on the condition that its derivation lives here and nowhere else.** Three constraints make it safe:

1. **Derived, never stored and never served.** No endpoint has a `stage` field carrying these ten
   values. `E30` serves `delivery` (§3.5) as one axis, unchanged.
2. **Mutually exclusive and exhaustive.** Every ticket falls in exactly one cell. The cells sum to
   the total, and a cell nobody can reach is a cell that gets deleted — a count that does not sum
   is `WEB-FIX-091` again, one screen contradicting itself.
3. **One definition.** The table below is the only place the flattening happens. A surface that
   recomputes it is a second answer to one question.

| cell | predicate | reads |
|---|---|---|
| `unclaimed` | no lane holds the ticket | lane |
| `claimed` | a lane holds it ∧ `process == not_started ∨ queued` | lane · process |
| `agent running` | `role == run` ∧ `process ∈ {running, blocked, idle}` | role · process |
| `awaiting QA` | `role == run` ∧ `process == finished` ∧ no `qa` lane exists | role · process · lane |
| `QA running` | `role == qa` ∧ `process ∈ {running, blocked, idle}` | role · process |
| `QA failed` | the QA verdict is `fail` | verdict |

**`run` is the role's name, not `agent`.** This table said `agent` and no code path has ever emitted
that word: `_claim-lane.cpp:65` defaults an unnamed claim to `run`, and every other role is one the
MANIFEST declares — `roles.qa` here. A predicate naming a role the tool cannot produce makes three
of these nine cells unreachable, which `SRV-FEAT-363`'s QA found by transcribing this table and
applying it to `E30`'s own body. Corrected to the vocabulary the tool and the manifest share; a
target repository that declares other roles names them itself, which is why the word cannot be
fixed in code.
| `awaiting merge` | verdict `pass` ∧ `delivery ∈ {unshipped, waiting}` | verdict · delivery |
| `merged` | `delivery == done` ∧ no escape cites it | delivery |
| `escaped` | an `ESCAPES.md` row names this ticket after it passed | ledger |

`All` is the unfiltered total and is not a cell — it is the sum, and asserting it against the sum
is what keeps the row honest.

**The three alarm combinations survive the flattening and are drawn ON the cell, never instead of
it**: `running + silent` and `finished + claimed_unproven` keep their border, and `crashed` keeps
its colour, inside whichever cell the ticket falls in. Flattening decides WHERE a ticket is
counted; it may not decide what it looks like. That is the distinction §3.2 is protecting, and the
reason `crashed`, `silent` and `claimed_unproven` do not collapse into one word for "bad".

### 3.3 "Needs you"

The counter in the top bar and the per-requirement pill mean exactly this set:

```
needs_you(lane) =
     process == idle                                   the agent asked and stopped
  ∨  process == blocked                                it is waiting on your answer
  ∨  process == crashed                                it died and nobody has looked
  ∨  process == finished ∧ evidence == claimed_unproven it says done and cites nothing
  ∨  process == running  ∧ motion == silent            it is alive and going nowhere
  ∨  delivery == stalled                               a session was launched and is gone
  ∨  delivery == unshipped                             work exists that nobody else can see
```

The last two clauses are §3.5's and do not need a lane: a ticket nobody has ever opened a lane
for can still be stalled, and the two states that need a person most are the two the tool learns
without one. A ticket counts once however many clauses it satisfies.

`silent` begins after **120 s** with no frame from the process. A compile or a test run legitimately
takes a minute; two minutes of nothing does not happen in a healthy run. Nothing about that number is
repository-specific, so it is not a manifest field.

### 3.4 Claimed, and proven

`evidence` is derived, not reported:

- A report document exists for the ticket at `docs.reports/<ticket-id>-report.md` → **claimed**.
- That document contains the string of a `run_id` the tool assigned → **claimed_proven**.
- Otherwise → **claimed_unproven**, which is an alarm.

That is the whole mechanism, and it is why `FACTORY_RUN_ID` is in the environment: an agent that
writes a report can cite the run it actually did the work in, and one that invents a report cannot.
The tool reads the string and does not judge the report's contents.

### 3.5 Delivery — what actually happened

`evidence` (§3.4) answers *does the report cite a run*. **`delivery` answers a different question
with a different failure mode: did the work leave this machine.** A document's `status:` field is a
claim written by a session that may not have survived to write it truthfully, so `delivery` is
derived from **git and the lane set the tool owns** and from nothing else.

| Value | Meaning |
|---|---|
| `not_started` | no branch, no commits, no process — nothing has happened yet |
| `running` | a lane the tool started holds it and its agent is alive |
| `stalled` | a session was launched, is gone, and left nothing new |
| `unshipped` | work exists that nobody outside this machine can see |
| `waiting` | a pull request is open; a human has to merge |
| `done` | it is on the base branch |
| `unknown` | the tool cannot tell, and says why in `reason` |

**The evidence, in the order it is weighed — first match wins.**

1. `running` — `E9` lists a lane holding this ticket whose `state.process` is `running`, `blocked`
   or `idle`: the process the tool started is alive. Nothing else on this machine is evidence of
   work in progress, whatever its program name or command line says — an operator's own session
   that names the ticket, a build tool in the worktree, a headless session the tool did not start.
   `S5` gives that fact to `E9` and to nothing else (`SRV-FIX-453`), and `detail` names the lane.
   **One exception, and only one:** when every live lane holding the ticket is `idle` — its
   session finished its turn — and the document says `status: pass`, rule 2 is asked first. A
   session that wrote its verdict and stayed up is not work in progress once that work is on the
   base branch, and calling it `running` is what kept such a ticket's lanes held for ever
   (`SRV-FIX-600`). If rule 2 does not hold, the ticket is `running` exactly as before.
2. `done` — the ticket's delivery report is tracked on the base branch, or the ticket's branch is
   merged into it. Either is sufficient, because a forge deletes the branch on merge and a state
   that needs the branch calls every merged ticket `unshipped`.
3. `unshipped` — the ticket's worktree holds uncommitted changes, or its branch holds commits no
   remote has. `detail` names the count.
4. `waiting` / `unshipped` — the branch is on a remote: `waiting` when the forge reports an open
   pull request for it, `unshipped` with `detail: "pushed, no pull request"` when the forge answers
   and there is none. When the forge cannot be asked at all, a fully pushed unmerged branch is
   `waiting` and `detail` says the forge was not reachable — the tool never converts *I could not
   ask* into an answer.
5. `stalled` — the document says a session is in flight and none of the above holds.
6. `not_started` — nothing anywhere.

The document is read at exactly one point, rule 5, and only as the answer to *was a session
launched* — never as the answer to *what happened to it*. Rule 5 is also the only rule that can be
wrong in the safe direction: a ticket nobody launched is `not_started`, never `stalled`.

**Disagreement is the most valuable thing on the row.** The object carries the claim beside the
derived state and marks the pair when a document could not be true:

| the document claims | the states that agree with it |
|---|---|
| `todo` | `not_started` · `running` · `unknown` |
| `in-progress` | `running` · `unshipped` · `unknown` |
| `awaiting-qa` | `running` · `unshipped` · `waiting` · `unknown` |
| `pass` | `done` · `waiting` · `unknown` |
| anything else | every state — the document claims nothing about delivery |

Where they disagree the screen shows **the derived state** and says the document disagrees. It
never shows the claim alone, and — because the tool does not write the target repository's
documents (§1) — it never corrects the field: a status the tool rewrote would be a second writer
racing the first, and the disagreement is the finding, not a defect to be tidied away.

**Read-only, and provably.** Every git invocation carries `--no-optional-locks` and is one of six
subcommands — `rev-parse`, `for-each-ref`, `rev-list`, `ls-tree`, `status`, `worktree` — none of
which writes. The set is served at `E1` so it can be asserted rather than reviewed, and a
subcommand outside it is refused by the code that spawns them, not by convention.

### 3.5b The verdict and the ledger — the two facts git does not carry

§3.2b's nine cells read six things per ticket. Four of them already exist: `lane`, `process` and
`role` are facts about a lane (§3.2), and `delivery` is derived from git (§3.5). The other two are
written by a person into a document, and this section is the only place that says where each is
read.

**The verdict of record is a pair, not a word.** A ticket's `status:` field is a claim — §3.5 spends
its opening paragraph on why — and a claim is exactly what a `QA failed` cell must not be built on.
A verdict document on its own cannot say which way the verdict went either, unless the tool reads
somebody's prose, which is a repository's vocabulary entering through the one door §1 closes. So the
answer is the two facts the tool can see without learning either:

> **`verdict` is `pass` or `fail` when a verdict document exists for the ticket in `docs.reports`
> AND the ticket's own `status:` claims one of those two words. It is `null` otherwise, whatever the
> document claims.**

The verdict document is `<ticket-id>-qa.md`, beside the delivery report §3.4 already names
`<ticket-id>-report.md` in the same declared directory. **The tool reads no word out of it** — its
existence is the whole of what it contributes, exactly as the report's existence is the whole of
`claimed` in §3.4. A ticket claiming `pass` with nothing beside it stays `null`: a session marking
its own work passed is the thing a separate check exists to prevent, and a cell that took the claim
at face value would draw that session's word as somebody else's verdict.

This is the tool's SECOND `<ticket-id>-` filename. A third is the point at which the name becomes a
manifest key rather than a constant — two is a pattern and three is a directory layout the tool
taught itself, which is `S1` going the way §9.1 exists to stop.

**The ledger is the file the manifest names, and the tool knows none of its columns.** `escaped` is
true when a row of the first markdown table in the file `docs.escapes` (§9.1) names carries this
ticket's id in one of its cells. Which column means what is the repository's and not the tool's — `E28` is
written on that same sentence — so the tool matches an id it already holds in its catalogue and
reads nothing else. A repository that declares no ledger answers `false` for every ticket: no ledger
and no row are the same answer to *does a row name this*, and having no escapes is not an error.

### 3.6 Remedy — the action that ends a state

**Every state the tool can put on a screen carries an action that ends it. A refusal names what to
do *and offers it*. No failure's only remedy is a shell, and no failure's only remedy destroys
work.**

A **remedy** is that action. It travels inside the error that needs it — §5's envelope, beside
`code`, `message` and `detail` — and never in a second call the client has to know to make: a
remedy that arrives separately from its failure is a remedy that can be missing at the moment the
failure is shown.

| member | what it is |
|---|---|
| `action` | a verb from the closed set in §9. A server that invents a verb the client does not know is a schema failure, not a blank button |
| `target` | what the verb acts on, so the client builds the call **from the remedy** rather than re-deriving it from the code. A remedy the client has to interpret is a remedy that drifts |
| `label` | the words on the control. The server's own sentence, rendered as given |
| `destroys` | what is lost, `[]` when nothing is, named from §9's nouns and nothing else. This is the member that makes *no failure's only remedy destroys work* checkable rather than aspirational |
| `confirm` | present when the action needs a human sentence before it fires, absent when it does not. Whether to confirm is the **server's** statement about consequence, not a client's taste |

`remedy: null` is legal only where §11 check 12 names it — a code in its exemption table, or a
single **site** in its site-exemption table for a code that stays remediable elsewhere — **with its
reason**. A silent null is a red gate.

**A remedy is a property of a SITE, not of a code.** The same code is a dead end at one endpoint and
a reload away from ending at another: `not_found` for a lane the manifest may never have been
reloaded for is remediable, and `not_found` for a `transcript.jsonl` somebody deleted is terminal,
because the tool does not write a run's log. `SRV-FIX-343` is where that distinction was paid for —
`E8` offered **Reload the manifest** over a cleaned log, a control whose only possible outcome was
the same `404` again.

Where a remedy may appear is §8 D1's, and it is one sentence: **by the failure that needs it, in
the place the failure is shown, and never as a standing control.**

---

## 4. Screens

One section per surface, and the count is the number of sections rather than a sentence beside them
(§11 check 1). Eleven were drawn in `Factory.dc.html`, which the 2026-09-02 design replaced —
`Ticket.dc.html` now draws that detail surface and is where those eleven live; three (4.12, 4.13,
4.14) are behaviour this specification adds and are built from the design's existing vocabulary
rather than new decoration (§8); six (4.15 – 4.20) are `Dashboard.dc.html`'s, which landed on
2026-08-29 drawing eight destinations of which five had nothing here to be built against
(SRV-FEAT-304).

The list is append-only, so a surface's number is the order it was specified in and not the order it
sits in the chrome: 4.14 is a top-level destination beside 4.11 and 4.12, and it is last here because
it was written last (WEB-FEAT-050). Append-only also means a later section may re-draw an earlier
one's surface without replacing its number — §4.20 is §4.14 as a table, and says so in both places.

Every surface below names its data. `E<n>` are the endpoints in §5, `M<n>` the socket messages in §6.

Three rules apply to all of them, so they are stated once:

- **Loading** — the frame renders immediately from whatever the last snapshot held; only the region
  waiting on data shows a placeholder. Never a spinner over a whole page, and never a layout that
  moves when data lands.
- **Empty** — the design's dashed rectangle with a mono caption and one sentence of what to do.
- **Socket down** — the design's `SOCKET LOST · RECONNECTING` block, pinned at the top of the
  affected surface: *"The transcript below is what arrived before the drop. Nothing has been
  discarded; your draft is intact."* Live counters freeze; nothing is cleared, nothing is refetched
  behind the user's back, and no control that would send something to an agent is enabled.

### 4.1 App shell — top bar

Always present, 38 px. Brand, breadcrumb (`requirements / REQ-014 / TKT-142`), `VOCABULARY`,
`PERMISSIONS` (§8 D4), `LANES` (§4.14), the socket chip, the `N lanes need you` chip, the theme
toggle.

`LANES` is the third `chrome-link`, and it borrows `VOCABULARY`'s own class for the same reason
`PERMISSIONS` did: *"styled exactly as VOCABULARY"* has to mean the same class or it stops being
true.

**Where the chip goes.** Since WEB-FEAT-050 the rail on a ticket screen shows only that ticket's
lanes (§4.4), which makes this chip the one thing on the screen that can still say a lane needs a
person *somewhere else*. So it is a link, and its destination is the answer to the question it just
asked:

| the count | where it goes | why |
|---|---|---|
| exactly 1 | `#/tickets/<that lane's ticket>` | there is one answer; take the person to it, not to a list of one |
| 2 or more | `#/lanes` (§4.14) | the chip cannot show which; the surface that can is one click away |

The count and the destination come out of **one** answer — `E9`'s lane set, in `E9`'s order — because
a chip that counted from one source and navigated from another could point at a lane it never
counted.

- **Data** — `E1` for the version and the repository name, `E9` for the lane set behind the counter,
  `M2 lane.state` to keep it live. The socket chip is client-local: green while a frame arrived within
  15 s, otherwise the crash colour and the word `reconnecting`.
- **Loading** — brand and breadcrumb only; the two chips appear when their data lands.
- **Empty** — no lanes: the `needs you` chip is absent, not a zero.
- **Socket down** — the socket chip states it; the `needs you` count stays at its last known value
  and is dimmed, because a stale count that admits it is stale is more useful than a blank.

### 4.2 Requirements list

Filter input, five filter chips (`all · needs you · running · blocked · done`), a count line, and one row per
requirement: id · state dot · title · a "needs" pill when something in it needs a person · a delivery
badge · one tag per workspace it touches · status · last-updated. The row takes a left edge in the
state's colour when it needs a person and none when it does not, so the column of marks is scannable
without reading a word.

The delivery badge is §3.5's, and it replaces the document's `status` in the reader's eye rather than
sitting next to it: it reads the derived word, and when the document disagrees it says so in the same
badge (`unshipped · the document says pass`). `stalled` and `unshipped` carry the alarm border; `done`
does not. A requirement's badge is `done` only when EVERY ticket under it is `done`; otherwise it is the most urgent of the tickets that are not. `done` means *the work is on the base branch*, and one ticket of six landing is not that (`SRV-FIX-166`) — while `stalled` and `unshipped` keep the front of triage, because they are the two that need somebody. The row is a summary and the
detail screen is where the ticket that caused it is named.

- **Data** — `E3`, whose rows carry `delivery`. `M2 lane.state` and `M13 catalog.changed` update rows in place. Filtering and
  search are server-side (`E3` takes both) so the count line and the rows cannot disagree.
- **Loading** — the filter bar is live immediately; rows arrive as one block.
- **Empty** — no requirements: *"No requirements found under `<the manifest's epics path>`."* naming
  the path, because the overwhelmingly likely cause is a manifest pointing at the wrong directory. A
  filter that matches nothing says so differently, and offers to clear the filter.
- **Socket down** — rows stay, dots dim, the list does not refetch.

### 4.3 Requirement detail

Header (id, title, an `N of M need you` pill, the requirement's own summary text), then one group per
workspace: the workspace's name, its stage sequence as declared, a ticket count, and a row per ticket
(id · dot · title · state · delivery badge · stage `4/8` · lane · cost). The badge is 4.2's, per
ticket: this is the screen where a `stalled` row names which ticket the requirement's alarm came from.

Grouping by workspace is the design's, and the reason is in its own copy: each workspace runs its own
stage sequence, **so progress is only comparable within a group**. The tool never compares `4/8`
against `2/4`.

- **Data** — `E4`. `M2`, `M3 stage`, `M4 lane.metrics`, `M13`.
- **Loading** — header first (it is one document), groups after.
- **Empty** — a requirement with no children: *"No tickets reference this requirement yet."*
- **Socket down** — as 4.2.

### 4.4 Ticket — lane rail

236 px, left, collapsible and remembered. Header `LANES` + count. **One card per lane of the ticket
this screen is about, and no others** — the count beside `LANES` is that number and never the
repository's total. Each card: state dot · lane name · ticket id · the state line in the state's
colour · an optional sub-line · a progress bar with the stage name · elapsed and cost. The card of
the selected lane is bordered and tinted; so is any card whose lane needs a person. Six states are
drawn on the vocabulary sheet and all six are lane cards, not a separate component.

**What this rail used to be, and what now does that job.** Until WEB-FEAT-050 it listed *every* lane
in the repository, and that was this specification's deliberate reading rather than an oversight. It
was carrying two jobs at once: the lanes of the ticket in front of you, and a standing view of
everywhere else. The second one loses. The ticket screen is the surface a person works *inside*, and
four other tickets' lanes in a permanent left column spends three quarters of that column on
documents nobody on this screen is reading. **The job it was carrying is not dropped — it is moved,
to two places that are better at it:**

- **§4.1's `N lanes need you` chip.** It counts **every** lane from `E9`, it is in the chrome of
  every surface rather than on one of them, and it links to what it counted. This is the load-bearing
  one: the filter below costs a person nothing they can notice *only because that chip exists*. Take
  the chip away and the filter becomes a way to lose work — a lane sitting `idle · waiting for you`
  on a ticket nobody has open would then be reachable by no path at all.
- **§4.14, the lanes surface** — every lane, laid out, one click from anywhere.

**A ticket with no lane of its own still shows the empty block**, even when other tickets have lanes.
The condition is *this ticket has no lane*, not *no lane exists anywhere* — the emptiness is a fact
about the ticket, and a rail that suppressed the block because somebody else's lane exists would take
away the one control that fixes it, on exactly the screen where fixing it is the next thing to do.

**A ticket may hold more than one lane** — a role starts one of its own (F31) — so this is filtered
*by ticket* and never reduced to "the one lane".

**And the second lane is drawn UNDER the first** (WEB-FEAT-079). A lane whose `role` is not `run` is
indented beneath the `run` lane of the same ticket **in the same workspace**; the `run` lane keeps its
place in `E9`'s order among the top-level cards and the lanes under it keep `E9`'s order among
themselves. Two identical siblings carrying the same ticket id is what this replaces — a person
opening a ticket could not tell the work from the check on it.

- **The parent is not a guess, and it costs no new field.** The server refuses a second lane on the
  same `(ticket, role)` with `409 lane_exists`, and a lane's branch is a function of that pair
  (§3.1), so **a ticket has at most one `run` lane**. The relation is already inside the `role` every
  `E9` row carries. No endpoint, no socket message, no second definition of any state — §4.14's rule,
  applied here.
- **It is a statement about the TICKET, not about who triggered what.** The tool does know which
  lane's stage report started a role (F31), and does not publish it; F31's only guard is that a role
  does not trigger *itself*, so a role lane could in principle start another. Drawing the ticket's
  `run` lane as the parent is what the person reading that ticket needs. If the trigger itself is
  ever wanted, it is a new field on `E9` and a new ticket, never an inference made in a browser.
- **A `run` lane that is no longer working hands its exclusive resources to its own ticket's role
  lane** (SRV-FIX-276). A role lane needing a resource its trigger holds is not a queue to wait in:
  the holder is the lane that asked for the check, and it will not release while it waits for the
  check it asked for. So when the trigger's process is **not `running`** — `idle` *or* `finished`,
  i.e. `!(live && (turnActive || !turnEnded))` — its holdings move to the role lane the tool started
  from that lane's own stage report. Three conditions, all required: the **same ticket**, a lane
  whose role is not `run`, and a trigger that is not still working. A `running` trigger keeps
  everything, which is `SRV-FIX-123`'s rule and is unchanged — two agents driving one exclusive
  device produce one corrupted run, not two failures. `E13` is unaffected: this is not a release, the lane keeps its
  worktree and its branch, and only the resource rows in `E9` change hands.
- **And it is an INVARIANT, not an event** (SRV-FIX-276 round 3). The rule above is re-evaluated
  wherever the resource table is recomputed — `advance_` sweeps stalled triggers and then grants in
  the same pass — and every path that can make it true calls that one function: a claim, a role
  admission, a turn ending, a release, and both places a lane's process ends, `E16` and a crash
  alike. Deciding it at a list of event sites is what failed twice: each site answers *may this move
  right now*, and the condition keeps becoming true at an instant no site fires. A resource must
  never be released outside that pass either — clearing a holder without walking the queue in the
  same breath orphans it, with lanes waiting on a resource nobody owns.
- **An orphan is drawn, never hidden.** `E13` releases one lane at a time, and a `run` lane released
  while the check on it is still working is exactly the moment that check is the only thing worth
  looking at. A role lane with no `run` lane to sit under draws at top level.
- **The card names its role** — the role's word, on the card, beside the ticket id it keeps. A `run`
  lane draws no tag: `run` is the tool's default and a tag on every card says nothing. The card is
  still §4.11's own `LaneCard`; nesting is a container around it, not a second drawing of a lane.

- **Data** — `E9`, then `M2` and `M4` for every subsequent change. The elapsed and silence numbers
  tick client-side from `since` timestamps, so a quiet socket does not freeze a clock. **The filter
  is the client's, over the one shared lane set, and not a per-ticket query**: `E9` is also §4.1's
  data and §4.14's, and an endpoint that answered "this ticket's lanes" would be a second answer
  that could disagree with the count in the chip — which is the number this whole product exists to
  put in front of somebody.
- **Loading** — the rail's frame with the count blank.
- **Empty** — this ticket has no lane: the design's `NO LANE ASSIGNED` block, with `Start a lane` as
  the action. Unchanged in appearance, and reached far more often than before.
- **Socket down** — cards keep their last state, clocks stop, `Start a lane` is disabled.

**What is SHARED across lanes, and is therefore not a lane's to compute** (`SRV-FIX-188`). Every
rule above this line is about isolation — this rail draws one ticket's lanes and no others, a lane
owns its checkout, its branch, its configuration directory and its session, and none of that is any
other lane's business. **A counter is the one thing that cannot be isolated.** A lane is a checkout
cut at claim time and nothing merges into a live one, so a value each lane derives from its own copy
of the repository is a value two lanes can derive two *different* answers to — both correct by the
rule they were given, and the question wrong. The list, so the next such value is recognised as one
before it is implemented per-lane:

| shared value | who owns it | why a lane may not compute it |
|---|---|---|
| the next ticket id in a series | the tool — `E26` | two lanes filing at the same time each read a checkout the other's file is not in, so both answer the same number and are both right |
| an exclusive resource, and its queue | the tool — `E9`, `E12` | one physical thing, and two holders is one corrupted run |
| a lane seat (`lanes.host`) | the tool — `E9`, `E12` | a limit each lane counted for itself is not a limit |
| the standing policy and the granted rules | the tool — `E11`, `E21`, `E23` | a rule granted in one lane governs the next tool call in every lane |
| a run id, and the log it names | the tool — `E18`, `runsDir` | two runs assigned one id overwrite each other's transcript |

**And a criterion about a shared value is asserted with at least two lanes acting at once.** One
caller cannot tell a counter from a race: a sequential loop is green against a read-then-write with
no exclusion, which is the implementation the race lives in. `SRV-FIX-188` is the escape that proves
it — every lane criterion in this document asserted *separateness*, all of them true, and
separateness working exactly as specified is what produced two right answers to a question that has
one.

### 4.5 Ticket — header, state chips and stage strip

Identity row: ticket id · title · workspace tag · lane name · `4 runs · 18m 40s · $1.86`. Then the
three state chips — one per axis, in that order, `PROCESS` / `MOTION` / `EVIDENCE` — and then the
stage strip: one chip per declared stage, done stages outlined, the active one tinted, the rest
ghosts, with a right-hand caption.

The caption is where the ticket's feature 4 lands: the design writes `stage 4/8 · core sequence`, and
this specification extends it to `stage 4/8 · round 2 of 4 · <sequence source>` when the active stage
has been entered more than once. The strip itself stays linear (§8 D2).

Extra frontmatter fields named by the manifest's `display.ticket_fields` render here as mono tags
beside the workspace tag, label and value, in declaration order. This is the mechanism that lets a
repository put its own vocabulary on the screen without the tool knowing what any of it means.

- **Data** — `E5`. `M2`, `M3`, `M4`.
- **Loading** — identity row from the document; chips and strip when the lane state lands.
- **Empty** — a ticket that has never run: all three chips read their `not_started` value
  (`PROCESS · NOT STARTED`, `MOTION · —`, `EVIDENCE · NOTHING CLAIMED`) and every stage chip is a
  ghost.
- **Socket down** — chips dim and keep their last value; the strip is unaffected (it comes from the
  document).

### 4.6 Ticket — SESSION tab (the transcript)

The heart of it. A scrolling column, max 900 px, of typed entries. The design's vocabulary sheet
fixes how each one looks and this specification adds no new shapes:

| Entry | Rendering (design's own words) | Payload |
|---|---|---|
| `human` | accent-edged panel, `YOU`, time, and a delivery mark | `text`, `mark: delivered\|held` |
| `prose` | full contrast, sans, wide leading — the only thing that reads like prose | `text` |
| `tool` | one collapsed mono line: chevron · outcome icon · tool · argument · duration | `tool`, `summary`, `ok: true\|false\|null`, `duration_s` |
| `tool` expanded | a panel with `INPUT` and `RESULT` | `input`, `result` |
| `file_edit` | a `tool` line reading `Edit  <path> · +18 −6`; expanded shows the unified diff in place of INPUT/RESULT | `path`, `added`, `removed`, `diff` |
| `tool_result` | indented mono block with a left rule | `text` |
| `error` | crash-coloured block, `ERROR · <time>` | `text` |
| `turn` | a rule with `turn 4 · 3m 12s · $0.41` centred | `n`, `duration_s`, `cost_usd` |
| `silence` | a silent-coloured rule, `NO OUTPUT · 4m 12s`, and one line: *last tool call has not returned · turn cost still accruing* | `silent_for_s` |
| `held` | the silent-coloured rectangle under the message it belongs to: `NOT PROCESSED · AGENT IS MID-TURN` … `send again to interrupt` | `held_count` |
| `stopped` | `STOPPED BY YOU · turn 4 · 41s · $0.86` + what already ran and stayed on disk | `text`, `sub` |
| `ended` | the `stopped` shape, `SESSION ENDED · <n> turns · $<total>` | `text`, `sub` |
| `killed` | the `stopped` shape in the crash colour, `KILLED BY YOU · mid-turn` | `text`, `sub` |
| `permission_request` | the `held` rectangle in the accent colour, with three buttons | see §7 F22 |
| `permission_decision` | one mono line: `allowed once · Bash` / `rule added · Bash(npm run *)` / `denied` | `decision`, `rule` |
| `stage` | the `turn` rule shape: `GATE · round 2` | `stage`, `round` |
| `command` | a `tool` line whose tool name is `gate` or `build`, expanded to the output | as `tool` |
| `isolation_refusal` | crash-coloured `error` block: `REFUSED · outside the lane` and the path | `path`, `operation` |

`file_edit` is worth its own note: it is the ticket's feature 3, and the design's own example data
already contains `Edit  core/tasks/build.py · +18 −6` as a tool line. What was missing was the diff
on expansion, not a new line type — so nothing is invented, the diff simply replaces INPUT/RESULT for
this kind. Editing files is the most common thing an agent does and it should be the easiest thing in
the transcript to skim, and to open.

- **Data** — `E8` for backfill (and for any run in the RUNS tab), then `M5 transcript` for live
  entries. Entries carry a monotonic `seq` per run; the client applies them in order and requests a
  resync on a gap (§6.2). **Nothing polls, and nothing is rebuilt on a timer** — a re-render that
  discards a half-typed message is the failure this architecture exists to prevent.
- **Loading** — the last 200 entries of the current run, then live; older entries page in on scroll.
- **Empty** — a lane with no run yet: the design's `NO LANE ASSIGNED` / *"Nothing has run on this
  ticket yet"* block.
- **Socket down** — the reconnect block pins to the top, the transcript keeps everything that
  arrived, and on reconnect the client resumes from its last `seq` rather than reloading.

### 4.7 Ticket — composer

A textarea that grows from 2 to 8 rows, a hint line, the session cost, and one button. The design is
explicit and this specification changes none of it: **one control, three jobs.** Idle it says `Send`.
Mid-turn it becomes `Stop`, in the crash colour, in the same place. With something held it says
`Send again`. Above the textarea, when anything is held: `N message held · send again to interrupt the
turn`.

The rules, in the design's words: *"A message sent while the agent is silent posts as held with the
rectangle above; sending again interrupts and delivers everything held, in order. Nothing is silently
dropped and nothing is rolled back behind your back."* `Enter` sends, `Shift+Enter` is a newline.

- **Data** — `E14` to send, `E15` to interrupt, `E16`/`E17` for end and kill (which live in the
  header's lane menu, not here — §8 D1). `M2` decides which of the three labels the button shows.
- **Loading / Empty** — the composer is present and enabled as soon as a lane has a live session; with
  no session it is disabled and reads *"Start a lane to send a message."*
- **Socket down** — the textarea stays editable and the draft is preserved; the button is disabled,
  because a send whose outcome cannot be observed is worse than a send that did not happen.

### 4.8 Ticket — TICKET.MD tab

The ticket document, rendered, read-only, with its path and the words `read only` above it. The
design shows a title, a summary and a numbered `ACCEPTANCE` list, which is this repository's own
ticket shape rendering as ordinary markdown — the tool applies no structure of its own beyond
splitting frontmatter from body.

- **Data** — `E5` (`body_markdown`), `M13 catalog.changed` to re-render when the file changes on disk.
- **Loading** — the path line, then the body.
- **Empty** — a ticket id with no file: *"This ticket is referenced but no document exists at
  `<path>`."*
- **Socket down** — unaffected; it is a document, not a live thing.

### 4.9 Ticket — REPORT.MD tab

The delivery report, or the design's empty state, whose copy is a product decision worth preserving
verbatim: `NO DELIVERY REPORT` — *"The agent has not claimed this ticket done. A report appears here
the moment it does — with or without the evidence to back it."*

When a report exists, a banner above it states the evidence verdict of §3.4: either `cites run <id>`
or, in the alarm colour, `claims done · cites no run`.

- **Data** — `E6`, `M13`.
- **Loading** — the tab header shows the report exists before its body arrives; no flicker between
  empty and present.
- **Empty** — as above.
- **Socket down** — unaffected.

### 4.10 Ticket — RUNS tab

A table: `RUN · STARTED · OUTCOME · STAGE · DUR · COST`, newest first, the outcome in its state
colour. Outcomes are the design's, plus the two states §3.2 added: `running`, `ended cleanly`,
`crashed mid-turn`, `killed by you`, `finished · no proof`, `finished · proof`.

Selecting a row loads that run's transcript into the SESSION tab, read-only, with a banner naming the
run — reading back a finished run is why `agent` keeps every session instead of overwriting it.

- **Data** — `E7`; `E8` for a selected historical run; `M11 run` appends and updates rows live.
- **Loading** — the header row, then rows.
- **Empty** — *"No runs recorded for this ticket."* (also the honest answer when `.factory/runs/` has
  been cleaned.)
- **Socket down** — rows stay; a `running` row keeps its label and its clock stops.

### 4.11 Vocabulary sheet

The design's own reference surface, reached from the top bar: the three axes with every value and its
note, the six lane-card states, the transcript line samples, the composer states, the stage sequence,
and the empty/error samples. It is the thing an implementation is gated against, so it ships as part
of the product rather than as a document.

- **Data** — `E2` only, for the stage names in the sequence sample; every other value on this surface
  is a constant in the client, and it is deliberately the *same* constant table the rest of the UI
  renders from. A vocabulary sheet maintained separately from the components it documents is a
  vocabulary sheet that will lie. It makes no other request.
- **Loading / Empty / Socket down** — static; renders offline, and says so by simply working.

### 4.12 Permissions — the standing policy and what has been granted

Reached from the top bar. Three parts, and the ticket is right that they only work together:

1. **The standing policy** — three values, as radio options with one line of consequence each:
   *ask about everything* · *edit files freely but ask before commands* · *never ask*. The manifest's
   `agent.permission_mode` is the default; a change here applies to lanes started afterwards, which
   the control says out loud.
2. **The granted rules** — a table of every standing rule: pattern · where it came from
   (`manifest` or `granted`) · when · how many times it has matched · `revoke`. Rules that came from
   the manifest cannot be revoked here; the row says *declared in the manifest* and links to the file,
   because a UI that silently overrides a committed file is how a repository stops meaning what it
   says.
3. **Nothing else.** No per-tool matrix, no wildcard editor. `Allow always` writes one rule and this
   screen is where it can be read and undone.

The reason this screen exists at all: `allow always` is one click, ten clicks make a policy nobody
can state, and an unreadable policy is indistinguishable from *never ask*.

A request that carries `frozen_by` is drawn differently, and it is the only request on this screen
the policy above it did not decide. The manifest's `evidence` block says which line of a ticket's §1
names what evidences a criterion and after which stage that evidence stops being freely editable;
once a run has reported that stage, a call touching one of those files is asked here whatever the
standing policy says. The request names the ticket and the number of the criterion whose evidence
the file is — *this is what criterion 3 is checked by* — and `Allow always` still writes its rule,
which this screen still lists, and still does not answer the next touch of the same file. There is
no always for a frozen path, because the person who wrote the criterion is the one who may change
it. A request with no `frozen_by` is drawn exactly as it was.

- **Data** — `E11` for the rules, `E21` to set the policy, `E20` to revoke, `E23` for the inline
  request's three buttons, `M6`/`M7`/`M12` to keep the list and any pending request live.
- **Loading** — the policy control first (it is one value), the table after.
- **Empty** — no granted rules: *"Nothing has been granted. Every request will be asked."*
- **Socket down** — the table is last-known and dimmed; revoke and policy change are disabled.

### 4.13 The background tab

Not a screen — what the product does when the person is not looking. Three events reach out, and
exactly three:

| Event | Why |
|---|---|
| a permission request appears | the run is stopped dead until it is answered |
| a lane becomes `idle` or `blocked` | the agent is waiting on a person and burning nothing |
| a run `crashed` | it is over and nobody knows |

A stage completing does **not** reach out. Nor does a run finishing normally, and nor does silence —
silence is already visible and resolves itself often enough that a notification for it would train the
person to ignore all of them.

- **Data** — `M6 permission.request`, `M2 lane.state`, `M11 run`.
- **How** — the document title takes a `(N)` prefix and the favicon a dot, always; a system
  notification is raised additionally when the browser has granted permission, and permission is
  requested at the moment the person first starts a lane, never on page load.
- **Loading / Empty** — no notification without an event; the title prefix disappears when the count
  reaches zero, which happens the moment the tab is focused and the lane is looked at.
- **Socket down** — the title is prefixed with `(offline)`, because the absence of notifications
  otherwise reads as *nothing is happening*, which is exactly wrong.

### 4.14 All lanes

A top-level surface at `#/lanes`, reached from `LANES` in the top bar beside `VOCABULARY` and
`PERMISSIONS`. **Every lane in the repository**, drawn as the same `LaneCard` §4.4's rail draws, in a
full-width layout. It is what §4.4 stopped being (WEB-FEAT-050): the whole of `E9`'s answer, on a
surface whose whole job is that answer, instead of in a column belonging to a document.

It is **not a new data source**: no endpoint, no socket message, no second definition of any state.
§3.3's seven clauses stay the server's, and this surface draws `needs_you` exactly as the rail does.

**Grouped by workspace**, groups in the order the manifest declares them, any workspace the manifest
does not declare after them, and lanes inside a group in `E9`'s own order. The grouping is §4.3's and
it is here for §4.3's reason: a lane card carries a progress bar and a stage, **progress is only
comparable within a group**, and a flat list of every lane in a repository is a column in which `4/8`
sits directly above `2/4` to be compared. Grouping by *state* was the alternative and it is refused:
which of §3.3's clauses a lane satisfies has exactly one drawing in this product — the card's own
colour and border — and a second one made out of headings would be §3.2's three axes flattened into
the single status this specification refuses to have.

**§4.4's nesting applies inside a group, and never across one** (WEB-FEAT-079). A role lane is drawn
under the `run` lane of its ticket **in its own workspace group**; one whose `workspace` differs from
that ticket's `run` lane draws at top level in the group it belongs to. Reaching across a group
boundary to nest would put `4/8` under `2/4` — the exact comparison the grouping above exists to
prevent — and would move a card out of the group whose count claims it.

The card is the rail's, with two things absent and both deliberate:

- **no selection** — nothing is open here, so nothing is *the* lane;
- **no release button** — `E13` destroys a lane, and it belongs on the ticket screen where the branch
  it keeps is the thing being looked at. This surface reads; it adds no write.

Clicking a card goes to `#/tickets/<its ticket>`, where §4.4's rail then shows that lane. **The two
surfaces are one path**, and it runs in both directions: the rail's card is a link to its ticket
already, and this is the same card doing the same thing.

**`E9`'s exclusive resources are not listed here.** They are already on the card that needs one — a
queued lane's sub-line names the resource, what it is for, who holds it, and its place in the line
(§4.4) — and a resource table beside the cards would be a second drawing of one fact, which is the
drift §4.11 exists to prevent.

**§4.20 re-draws this surface and changes none of its data** (SRV-FEAT-304). `Dashboard.dc.html`
lays the lanes out as a table rather than as a page of cards, and adds the count the project rail
carries. Everything below — `E9` once, no second definition of any state, `E9`'s order inside a
group, no selection, no release button — is what §4.20 inherits unchanged; read that section for the
drawing and this one for why the data is what it is.

- **Data** — `E9` once, then `M2 lane.state`, `M3 stage` and `M4 lane.metrics`. This surface
  subscribes to the `requirements` scope, which is one of the three every lane frame is routed to
  (`laneStateScopes`, §6.2 M14) — so it is live for lanes on tickets nobody has open, which is the
  whole point of it. Elapsed and silence tick client-side from `since`, as on the rail. **It never
  polls and never refetches on focus**: the cheap way to build a "show me everything" screen is to
  re-read `E9` on a timer, and that is the thing this architecture exists not to have.
- **Loading** — the surface's frame, groups blank.
- **Empty** — no lane anywhere: the design's dashed block, *"No lane has been claimed in this
  repository. Open a ticket and start one."* It names no ticket, because there is no ticket on this
  screen to start one on — the action lives in §4.4's block and §4.7's composer, and an action here
  would have to invent a ticket to take it against.
- **Socket down** — cards keep their last state, clocks stop, nothing is refetched behind the user's
  back.

### 4.15 Wiki

The pages a repository writes about itself, read the way §4.8 reads a ticket: whole, off disk, and
without the tool understanding a word of them. `Dashboard.dc.html` draws this as a grid of cards
under a heading of the repository's own choosing; that heading is **the repository's**, and this
specification does not know it. The drawing's §13 is one section of one repository's wiki, not a
section the tool has.

**The manifest is the only door.** `docs.wiki` (§9.1) names a directory; every `*.md` under it is
one section, in the order the documents' own frontmatter gives and, failing that, by filename. A
repository that declares no `docs.wiki` **has no wiki surface and no rail row for one** — not an
empty one, not a placeholder. That refusal is `FAC-FEAT-302`'s rail rule and it is `S1`'s whole
sentence: a second module naming `docs/wiki` because it is the usual name would be the tool
learning a directory layout nobody told it.

**Frontmatter is passed through, never interpreted.** The tool reads `title` for the section's name
and nothing else; every other key travels to the client untouched and is rendered as a label beside
the page, exactly as `display.ticket_fields` does for a ticket (§4.8). A page saying it was derived
from three other documents is a fact about that repository's method, and a tool that ranked pages by
it would be a tool with an opinion about how that repository writes.

**One request for the whole set, and this is a decision rather than an oversight.** Moving between
sections is the reason this surface exists, so a second round trip per section would put a network
wait on the surface's only interaction. A wiki is a handful of documents a person wrote by hand; when
one is not, the cost is visible in `E27`'s own answer — `bytes` per section — rather than hidden in a
lazy load that makes the first section fast and the tenth mysterious.

Diagrams are the pages' own: a fenced block the client renders (`mermaid` today) and, where it cannot,
the fence's text, which is still the diagram. The tool neither draws nor validates one.

**Three things the drawing has are deliberately not here.** Its cards carry a status dot, a
machine-read `detected` line and a per-card action — and every one of them needs the tool to know
what the card is ABOUT. A status is a judgement of a page's subject, a `detected` line is a live
reading of a thing the page describes, and an action is a verb against it. This surface renders
documents; a repository whose pages report on live state writes that state into the page, and the
page is what changes on disk and what `M13` announces. The alternative is a wiki the tool has an
opinion about, which is `S1` gone in a different direction from the one §9.1 guards.

- **Data** — `E27` once, then `M13 catalog.changed` for the exact page that changed on disk. No
  polling, no refetch on focus: a wiki is the least volatile thing in the repository and the socket
  already says when it moves.
- **Loading** — the section list first, because it comes in the same answer; the page body renders
  as it arrives.
- **Empty** — `docs.wiki` declared and the directory absent, or holding no `*.md`: the design's
  dashed block naming the resolved path, *"Nothing under `<the resolved docs.wiki path>`."* — the
  same shape §4.2 uses, and for the same reason: this is a manifest pointing at the wrong directory
  nine times in ten. `docs.wiki` **not declared** is not this state and draws nothing at all.
- **Socket down** — the pages stay; they are documents, and nothing about them was live.

### 4.16 Escapes

One ledger, one surface: the defects a gate passed. `docs.escapes` (§9.1) names a **file**, not a
directory, because a ledger is a single document a row is appended to — and the surface is that
document's table, one card per row, in the file's own order.

**The columns are the file's.** The tool reads the first markdown table in the document, takes its
header row as the column names, and renders every row as a card whose fields are those names. It
knows none of them. A repository whose ledger says `Date · Escape · Escaped from · Found by · Root
cause of the MISS · Change landed` gets six fields with those six headings; one that writes four gets
four. Naming a column in this specification would be the tool deciding what a repository has to
record about its own misses, which is the same overreach as naming its directories.

**What the tool does add is the link, and only where it can prove one.** A cell whose text contains a
ticket id the catalog knows becomes a link to that ticket (§4.8). Nothing else is parsed: no dates
are read, no severity is inferred, no row is ranked. An id it cannot resolve stays text — a ledger
naming a ticket that has been deleted is a fact worth seeing, not an error worth swallowing.

**And no control that writes.** The design puts `Write regression ticket` and `Reopen <id>` on every
card. Both are writes into the repository's documents, which §1 says the tool does not do — a ticket
the tool authored is a document nobody wrote, in a directory the tool is only supposed to read, and
`E26` exists precisely because even allocating an *id* had to be separated from writing the file. The
card links to the ticket the row names (§4.8) and to the ticket that escaped, and the person writes
what they decide to write.

**No aggregate.** The design draws a `Gate health` panel — escapes per merge, first-round pass rate,
a median. Every one of those is a ratio over a denominator this tool does not have and would have to
invent, and a number a screen computes from a document it does not understand is the drift §4.11
exists to prevent. The count of rows is the count of rows, and it is drawn beside them in one
assertion (`WEB-FIX-091`).

- **Data** — `E28` once, then `M13 catalog.changed` for the ledger's own path.
- **Loading** — the heading and the count line when the answer lands; rows in one block.
- **Empty** — `docs.escapes` declared and the file absent: the design's dashed block naming the
  resolved path. Declared, present and holding no table row: *"No defect has got past a gate here."*
  — a real and good state, drawn as a sentence rather than as an error. Not declared: no surface and
  no rail row, exactly as §4.15.
- **Socket down** — rows stay; nothing on them was live.

### 4.17 Tickets

Every ticket in the repository, in one table, grouped by the requirement each names as its `parent`.
It is the surface `FAC-EPIC-004`'s defect class asks for: §4.2 answers *which requirements need
somebody* and §4.3 answers *what is under this one*, and neither answers *where is `TKT-142`* without
knowing which requirement it belongs to first.

**Grouped by parent, and the group header carries the group's own counts.** A ticket whose `parent`
names no requirement on disk falls in the same `standalone` group `E3` and `E4` already have — one
name for one set, because two names for it is how the tool comes to disagree with itself about which
tickets exist.

**Three columns are derived and one is the document's**, and the table says which is which:

| column | where it comes from |
|---|---|
| id · title | the ticket document (§2) |
| provenance | `delivery.state` (§3.5) — `done` · `waiting` · `unshipped` · `not_started`, with `detail` as the sub-line. It is git, never the `status:` field, and where the document disagrees the badge says so exactly as §4.2's does |
| stage · lane | `stage` and the lane the ticket holds, as §4.3's row draws them |
| review | the declared roles for this ticket and what became of them (`roles`, `E5`, F31), beside §3.4's `evidence`. A repository declaring no role has an empty column, not an invented one |
| owner | the author of the commits on the ticket's branch, read with `rev-list` — one of §3.5's six subcommands, so this column costs the read-only guarantee nothing. **No branch means no owner**, and the cell falls back to whatever `display.ticket_fields` named, passed through untouched: a repository that records an assignee in frontmatter sees it, and one that does not sees an empty cell rather than a name the tool made up |

**The group header carries the group's own numbers and no others**: the parent's id and title, the
count of tickets in it, and the design's two-segment bar — merged, then pushed — over that count. All
three are members of the same group object in `E29`'s answer, so the bar cannot describe a set the
rows below it are not (`WEB-FIX-091`). The design also draws a `cloned by 3 projects` badge on a
group; there is no such fact anywhere the tool reads — `repos` says what this repository contains and
nothing about who else has a copy — so it is not drawn.

**Filtering, search and paging are the server's**, exactly as `E3`'s are and for the same reason: the
count line, the group counts and the rows must come out of ONE answer, in that answer's order, or the
screen contradicts itself while every assertion about the rows passes (`WEB-FIX-091`). The line under
the table reads `showing <from>–<to> of <total>`, and all three are members of `E29`'s response.

**Two filters, and they are not one filter.** Provenance (§3.5's values) and stage are different axes
and are never merged into a status chip row — §3.2's rule, which this table is the most tempting
place in the product to break.

- **Data** — `E29`, then `M2 lane.state` and `M3 stage` patch rows in place, and `M13
  catalog.changed` reloads exactly the ticket it names.
- **Loading** — the filter bar and the search box are live immediately; rows arrive as one block.
- **Empty** — no tickets under the manifest's `tickets` directories: *"No tickets found under `<the
  manifest's tickets path>`."*, naming the path. A filter or a query matching nothing says so
  differently and offers to clear it, as §4.2 does — the two are different questions and must not
  share an answer.
- **Socket down** — rows stay, live cells dim, nothing is refetched.

### 4.18 Home

The first screen, and the only one whose question is *what should I do next*. Three panels, one
request, and no panel that spends money or moves a branch.

**Where the work is.** One cell per `delivery` value (§3.5), in §3.5's own table order, each with
its count. Delivery is already the one derived word for *what happened to this work*, and it is the
axis `E30` serves.

**A cell is a link into §4.17 filtered to it — once §4.17 has a surface.** It has none in this
client (`routes.ts` carries no tickets address), and seven controls that go nowhere are worse than
seven counts that do not pretend to. While that holds the cells carry no link and the panel carries
one to §4.2's list instead, which is the destination that exists. The clause is not softened: it is
conditioned on the surface it names, and the child of `FAC-EPIC-007` that builds §4.17's client is
what discharges it.

The cells §3.3 counts — `stalled` and `unshipped` — are tinted, which is the design's own
sentence — *"tinted stages are waiting on a person, not on an agent"* — expressed in the
vocabulary the product already has.

**§3.2b's nine cells are drawn from this one read, and from nothing else.** The nine predicates
read `lane · process · role · verdict · delivery · ledger` PER TICKET, and `E30.tickets` carries all
six — the first three from the lane holding the ticket, `delivery` from §3.5, and the last two from
§3.5b, which is the only place that says where a verdict and the ledger are read. It carries **no
cell**: the flattening is the client's one derived presentation axis and lives in the one module
§3.2b names, so a server that served the cell would break §3.2b constraint 3 from the other side —
a second answer to a question with one definition. A strip drawn instead from three reads joined in
a browser is the defect this surface exists to prevent, one paragraph below.

`delivery` stays in the answer beside the array. It is a different axis (§3.2) and the panel drew it
alone until the array existed; the two are never merged into one row, and the counts remain
assertable against the array they were read with.

**Needs you.** One row per ticket satisfying §3.3, with the clause that fired written out, and — where
there is one — the remedy §3.6 defines, from §9.2's closed verb set. This is D1's amendment read
forwards rather than backwards: the row IS the failure being shown, so the action that ends it belongs
on the row, and a ticket in no such state contributes no row and therefore no control.

**Unpushed work.** The tickets whose `delivery` is `unshipped` with commits no remote has (§3.5 rule
3), each with the branch, the count and the age. **It names them and does not push them.** The design
offers a `Push N branches` button; §3.5's read-only property is six subcommands, none of which
writes, served at `E1` so it can be asserted rather than reviewed — and one write verb here would end
that guarantee for the whole product in exchange for one button. The panel's job is that nobody
discovers a week of work living on one laptop, and naming it does that.

**One request, and this is the decision the surface exists to protect.** Three panels drawn from three
endpoints is three answers about one repository at three instants, which is `FAC-EPIC-004`'s defect
class generalised: the counts in the first panel and the rows in the second would be free to disagree,
and each would be correct. `E30` answers all three from one read.

- **Data** — `E30`, then `M2 lane.state` and `M13 catalog.changed`. It subscribes to the
  `requirements` scope, which every lane frame is routed to (§6.2 M14), so a lane started on a ticket
  nobody has open still moves this screen.
- **Loading** — the three panel frames, each filling as the one answer lands.
- **Empty** — a repository with no tickets at all: the design's dashed block, *"No tickets in this
  repository."*, over *"`docs/tickets` is declared and readable, and holds no document the tool
  counts."* **It names TICKETS, and the noun is the criterion.** Every member of `E30`'s answer is
  built from the ticket set and none of it reads a requirement, so a block here claiming there are no
  *requirements* is a claim about a collection this answer does not carry — false, and first read, of
  any repository between `/split-requirement` and the first `/new-ticket`, which has epics and no
  tickets (`WEB-FIX-002`).

  **And this state carries the outbound edge, because it replaces the panel that had it.** The one
  link off this screen — `all requirements →`, into §4.2 — lives in the strip's header, and the
  strip is exactly what an empty repository does not draw. A first screen that is where every
  unknown hash resolves, with nothing on it that goes anywhere, is the half of `WEB-FEAT-001`'s F2
  that its fix left standing.

  An empty `needs you` panel is a different and much better state and says so in its own words:
  *"Nothing needs you."*
- **Socket down** — counts and rows hold their last value and dim; no remedy control is enabled,
  because every one of them is a call the socket would have to report the result of.

### 4.19 Runs

Every run in the repository, newest first — the repository-wide index `E7` is the per-ticket slice of.
One row per run: id · kind · ticket · lane · stage · wall clock · cost, the columns the design draws,
and clicking one opens §4.10's replay for it.

**Kind is the role that ran it**, from the manifest's `roles` (F31) with `run` for a run nobody
declared a role for. It is not a second vocabulary: a repository that declares a `qa` role sees `qa`
here because it wrote that word, and one that declares none sees one kind.

**It is history, and history is expendable (§2).** `.factory/runs/` is not committed, so this surface
is about this machine and says so under the table in one line. A run whose log has been cleaned keeps
its row — `meta.json` is what this list reads — and opening it is `E8`'s `404`, which §4.10 already
draws as a sentence rather than as a failure.

- **Data** — `E31` once, then `M11 run` for a run starting and for a run reaching a terminal outcome.
  Elapsed ticks client-side from `started_iso`, as everywhere else.
- **Loading** — the table frame, rows in one block.
- **Empty** — no run recorded: the design's dashed block, *"Nothing has run in this repository yet."*
  It offers no action, for §4.14's reason: there is no ticket on this screen to start one on.
- **Socket down** — rows stay, live clocks freeze, nothing is refetched.

### 4.20 Lanes — the table, and the count the rail carries

`Dashboard.dc.html` draws the lane surface as a **table**, not as a wall of cards: `Lane · Ticket ·
Stage · Wall clock · Cost`, with a role lane indented under the lane it belongs to, and one final row
naming the lanes that are free. **This is §4.14's surface and §4.14's data**; what it changes is the
drawing, and what it adds is the count.

The drawing changes because the reason for the cards has gone. §4.14's card is §4.4's card, and
§4.4's card earns its area by being the thing you watch: one lane, one ticket, a progress bar you are
staring at. A page of them is that card used as a row, and a row is what a table draws better —
seven lanes fit on a screen, the clocks align, and the costs add up in a column instead of in the
reader's head. Everything §4.14 decided about the DATA stands unchanged: `E9` once and no second
definition of any state, `needs_you` exactly as the rail computes it, `E9`'s own order inside a
group, no selection and no release button.

**Grouped by workspace, as §4.14 groups.** The reason survives the change of shape: a stage cell reads
`4/8`, progress is only comparable within a group (§4.3), and a flat table is a column in which `4/8`
sits directly above `2/4` to be compared.

**Role lanes indent under their trigger, inside a group and never across one** (`WEB-FEAT-079`,
unchanged). The indented row says which role it is and that it is a separate session on the same
ticket, because the one thing a person misreads about two rows carrying one ticket id is whether they
are one process.

**The header carries one link and one sentence.** `All runs` goes to §4.19, because the question a
person asks after *what is running* is *what has run*; the sentence beside the heading is `E9`'s own
occupied-of-total with the number of them that are producing, which is the same pair of facts the
rail's count is and comes out of the same answer.

**The free lanes are one row, not one row each.** `E9`'s `seats` already says how many exist and how
many are used; the row names the free ones and carries the one action this surface has, which is to
claim one — and that action is `E12`, offered here because a free seat with nothing to do is not a
failure and needs no remedy.

**The count the rail carries is this surface's, and it is one number from one answer**: occupied of
total, from `E9`'s `seats`, beside the same word the row uses. A rail that counted from its own read
of `E9` while the table counted from another would be two answers to one question — which is
`WEB-FIX-091` exactly, and the rule is the same one: the label and the collection are asserted
together.

- **Data** — `E9` once, then `M2 lane.state`, `M3 stage`, `M4 lane.metrics` and `M17 lane.membership`
  for a row appearing or going away. It never polls and never refetches on focus (§4.14).
- **Loading** — the table frame and the group headings; rows as the one answer lands.
- **Empty** — no lane claimed anywhere: §4.14's sentence, unchanged — *"No lane has been claimed in
  this repository. Open a ticket and start one."* — above the free-lane row, which is present whenever
  a seat is free and is the whole of the screen in a repository where nothing has started.
- **Socket down** — rows keep their last state, clocks stop, nothing is refetched.

### 4.21 Metrics — how the process itself is doing

One screen for the numbers this repository computes once per epic with a script today, over a
window a person chooses. Eight numbers, each with the sentence that says how it was derived,
because a metric whose derivation is not on the screen beside it is a number somebody will
argue about instead of act on.

**Every number names its own collection, and the collection is on the screen with it.** The
first-pass rate is drawn beside the count of tickets it is a share OF, not beside a total from
somewhere else: a rate over one population and a count over another is two true facts forming a
false sentence, which is `WEB-FIX-091` in the one place a reader has no way to check.

**A rate that was never measured draws `—`, never `0%`.** `null` draws as an em dash beside
its `n` of 0; `0` draws as `0%`. They are different facts — *no ticket reached QA* and *no ticket
passed first time* — and a screen that renders both the same way reports a failing process over a
repository that has simply not started.

**A tile draws the `n` `E32` served it, and a dash where it served none.** `rounds_per_ticket`
and `rework_after_build.share` are reduced over the tickets with a round in the window and the
response carries no count for that collection, so those two tiles draw `n —` and say so in
their derivation line. A surface that computed a population out of a numerator and a share would
be drawing a collection nobody served it.

**The tiles, and the one sentence each carries about where its number came from.** The wire name
is the tile's name, so a person reading the screen and a person reading `E32` are reading the
same thing:

| tile | the number | derived from |
|---|---|---|
| `window_days` | the window every other tile is taken over | the `?days=` the control asked for, echoed back |
| `runs` | every run record the tool wrote inside the window | `started_iso` on each record under `runs_dir` |
| `tickets_with_verdict` | the tickets a verdict was RUN for, and the rate's own denominator | a ticket with at least one role run (`roles.<name>`, F31) inside the window |
| `first_pass_qa_rate` | the share of those whose FIRST role run was their only one and whose verdict says `pass` | §3.5b's pair — a verdict document beside the report AND the ticket's own `status:` |
| `rounds_per_ticket` | how many rounds a ticket takes | the runs nobody declared a role for, counted per ticket and reduced to a median and a mean |
| `stage_minutes` | where a round's time goes | the gap between one `stage` frame (`E22`) and the next in the same run, per stage the repository declared |
| `active_minutes` | how much of a round is the agent working | the sum of a run's `turn` durations, never wall clock |
| `spec_to_land_hours` | how long a ticket takes to reach the base branch | the first frame naming the ticket's own FIRST stage, to the commit that landed it |
| `escapes_in_window` | how many defects got past a gate | rows of the first table of `docs.escapes` whose date cell falls inside the window |
| `rework_after_build` | how often §1 moves after work starts | tickets whose own document was committed to the base branch after their first round began |

- **Address** — `#/metrics`, and `Metrics` in the rail after `Escapes`. The window control is
  `7` / `30` / `90` days, `30` selected on arrival because that is `E32`'s own default; the
  control sends `?days=` and nothing else.
- **Data** — `E32` once, for the window the control names, and again when the window changes.
  It never polls: nothing on this screen ticks, and `M13 catalog.changed` is not subscribed
  because a metric is a reduction over a month and not a live value.
- **Loading** — the eight tiles with their headings, values as the one answer lands.
- **Empty** — a window with no runs in it: the dashed block, *"No run has been recorded in this
  repository."*, and no number — the sentence is asserted in one expression with `runs`, which
  is the collection it is about.
- **Refused** — `400 bad_window`: the error's `message` in the surface's error slot, the
  numbers already on screen left where they are, and nothing retried. A refusal about the
  window a person asked for may not blank the answer to the one they had.
- **Socket down** — the numbers stay; nothing here is live, so there is nothing to freeze.

---

## 5. HTTP

`http://127.0.0.1:<port>` — loopback only. JSON in, JSON out, UTF-8. Actions are HTTP, live data is
the socket (§6); the client never polls.

Conventions, stated once so each endpoint below can be terse:

- Errors are `{"error": {"code": "<slug>", "message": "<one sentence>", "detail": {…}}}`.
- **A thing that does not exist is `404` with `code: not_found` and a `detail.path` naming where the
  tool looked.** Naming the path is not decoration: nine of ten of these are a manifest pointing at
  the wrong directory, and a 404 that does not say where it looked sends the reader to the code.
- A malformed document is never a 500. It comes back with `parse_error` set on the item and the rest
  of the list intact; one unreadable file may not blank a screen.
- An action on a lane in the wrong state is `409` with `code: wrong_state` and `detail.state`.
- Writes are idempotent by `request_id` when the client supplies one, so a retry after a socket blip
  cannot double-send a message.
- **Every endpoint states which repositories it reads**, in the column below. `repos.<name>.role`
  (§9.1) splits the declared repositories into the product's and the tool's own, and the two are not
  the same set for every reader: a document is *readable* by id everywhere and *listed* only where it
  belongs. The column is where that decision is recorded per endpoint, so widening what the catalog
  holds forces a per-endpoint answer instead of every consumer silently inheriting one
  (`SRV-FIX-199` §3).

| # | Method | Path | What | Reads which repositories |
|---|---|---|---|---|
| E1 | GET | `/api/health` | version, repository, uptime | none |
| E2 | GET | `/api/manifest` | the resolved manifest | every declared repo |
| E3 | GET | `/api/requirements` | requirement list, filtered | **product repos only** |
| E4 | GET | `/api/requirements/{id}` | one requirement and its tickets, grouped | every declared repo by id; **product repos only** for `standalone` |
| E5 | GET | `/api/tickets/{id}` | one ticket: frontmatter, extras, body, lane, stages | every declared repo |
| E6 | GET | `/api/tickets/{id}/report` | the delivery report | every declared repo |
| E7 | GET | `/api/tickets/{id}/runs` | run history | every declared repo |
| E8 | GET | `/api/runs/{run_id}/transcript` | transcript replay | none |
| E9 | GET | `/api/lanes` | every lane, plus resources and their queues | every declared repo |
| E10 | GET | `/api/lanes/{lane}/diff` | files changed and commits made | none |
| E11 | GET | `/api/permissions/rules` | standing rules and the policy | none |
| E12 | POST | `/api/lanes` | claim a lane for a ticket | every declared repo |
| E13 | DELETE | `/api/lanes/{lane}` | release a lane | none |
| E14 | POST | `/api/lanes/{lane}/messages` | send text to the agent | none |
| E15 | POST | `/api/lanes/{lane}/interrupt` | interrupt the turn, deliver held | none |
| E16 | POST | `/api/lanes/{lane}/end` | end the session after this turn | none |
| E17 | POST | `/api/lanes/{lane}/kill` | kill the process now | none |
| E18 | POST | `/api/lanes/{lane}/runs` | start a run in the lane | every declared repo |
| E19 | POST | `/api/lanes/{lane}/commands` | run the workspace's `build`, `gate` or `review` | none |
| E20 | DELETE | `/api/permissions/rules/{rule_id}` | revoke a granted rule | none |
| E21 | PUT | `/api/permissions/policy` | set the standing policy | none |
| E22 | POST | `/api/progress` | the agent reports its stage (env-injected URL) | every declared repo |
| E23 | POST | `/api/permissions/decisions` | answer a pending permission request | none |
| E24 | POST | `/api/permission` | a run's own agent asks whether it may make a tool call | none |
| E25 | POST | `/api/lanes/{lane}/recover` | kill the run, re-seed the lane, keep the session | none |
| E26 | POST | `/api/tickets/ids` | allocate the next ticket id in a series | every declared repo |
| E27 | GET | `/api/wiki` | the repository's own pages, whole | every declared repo |
| E28 | GET | `/api/escapes` | the ledger of defects a gate passed | every declared repo |
| E29 | GET | `/api/tickets` | every ticket, grouped by parent, paged | **product repos only** |
| E30 | GET | `/api/home` | where the work is · needs you · unpushed, in one read | **product repos only** |
| E31 | GET | `/api/runs` | every run in the repository, newest first | none |
| E32 | GET | `/api/metrics` | how the process is doing over a window of N days, computed on request | every declared repo |
| E33 | POST | `/api/lanes/{lane}/routing` | execute an explicit finite command plan | none |
| E34 | GET | `/api/lanes/{lane}/routing/{request_id}` | read retained source-bound command evidence | none |

#### E1 · GET `/api/health`
- **Request** — none.
- **Response** — `200 {"factory":"0.1","manifest":"ok"|"invalid","repo":{"name","root"},"uptime_s","delivery":{"available":bool,"reason"?,"base"?,"git":[…],"forge"?}}`.
  `delivery.git` is the exact set of git subcommands §3.5 permits, served rather than described so
  the read-only promise is an assertion a test makes and not a paragraph a reviewer reads.
  `available:false` with a `reason` is what a directory that is not a git repository answers, and it
  is the one place the whole surface's "I cannot tell you" is stated once instead of per row.
- **Missing** — no manifest, or an invalid one: still `200`, with `manifest:"invalid"` and
  `detail.reason`. Health answers *is the process up*, and a process that is up with a broken manifest
  must be able to say so — that is the case where the client shows a full-page manifest error.

#### E2 · GET `/api/manifest`
- **Request** — none.
- **Response** — `200` with the manifest as loaded, normalised: defaults filled in, unknown keys
  listed under `warnings`, `resolved.paths` giving the absolute path each `docs` entry resolved to.
  A manifest that declares `repos` carries that block instead of a top-level `docs`, and
  `resolved.paths` names each repository's three directories under `repos.<name>.docs.*`.
- **Missing** — no `.factory/factory.json`: `404 not_found` with `detail.path`. Unknown **major**:
  `409 unsupported_manifest_major` naming both versions and refusing to load — the epic's rule, and
  the tool guesses at nothing.
- **A monorepo is a set of repositories, and the manifest says so.** `repos` is an optional
  top-level block: each entry has a `path` relative to the repository root and a `docs` tree of its
  own, and **every path inside a repo is relative to that repo's `path`** — its documents and, via
  `workspaces.<name>.repo`, its workspaces. So two repositories may both write
  `"tickets": "docs/tickets"` and resolve to different absolute directories, and when one of them is
  finally split out into a repository of its own, its entry's `path` becomes `"."` and nothing else
  in the manifest changes. The tool is *given* this structure: it discovers no directory, defaults
  no repository, and a declared repo that leaves the repository the tool was pointed at is refused
  with everything else (§10).
  A ticket belongs to the repo whose `tickets` directory holds its file — read, never declared per
  document — and **an id held by two repos is not a ticket**: E5, E6 and E12 answer
  `409 ambiguous_ticket_id` naming both files rather than serving whichever was read first.
  **No `repos` block is one implicit repository at `.`** carrying the top-level `docs`, so every
  manifest written before this block existed resolves to the byte-identical absolute paths it did
  then. The major stays `0.1`.
- **`repos` and a top-level `docs` together** — `422 invalid_manifest` with
  `detail.reason:"docs_and_repos"` and `detail.pointer:"/docs"`. Two answers to *where are the
  documents* is a manifest that means nothing, and ranking them silently is how the next reader
  learns the wrong rule. A workspace that names no `repo` while `repos` is declared, or one that
  names a repo nobody declared, is refused the same way (`workspace_repo_missing`,
  `unknown_repo`) — placing it by declaration order would be the tool defaulting a fact about
  somebody else's repository.
- **A `devices` entry that is not a device node** — `422 invalid_manifest` with
  `detail.reason:"not_a_device"` or `"device_not_found"`, `detail.pointer` naming the entry
  (`/resources/<name>/devices/<i>`) and `detail.path` naming what it resolved to. Refused **at
  load**, for the same reason as everything else in this list: `--dev-bind` on a directory is a
  read-write bind of that whole tree into the lane, so one manifest line would hand a lane the
  developer's home — listed and writable — from inside the sandbox that exists to make that
  impossible. Three places said it could not happen (§1's Safety clause, §1's Pre-decided list,
  and E12's own paragraph below) and none of them checked; a guarantee nothing enforces is
  `ESCAPES.md` row 1 reachable again through a typo. The question asked is *"is this a character
  or block device"* and never *"is this spelled `/dev`"* — a string comparison let `/dev/..`
  through, which canonicalises to `/` and produced a lane that hung with no output for ninety
  seconds. A node that is simply **absent** is refused too rather than dropped in silence: a lane
  holding the lock with no device is the defect the field exists to fix (`SRV-FIX-063`).
- **A `toolchain` entry that is not there, or too broad** — `422 invalid_manifest` with
  `detail.reason:"toolchain_not_found"` or `"toolchain_too_broad"`, `detail.pointer` naming the entry
  (`/workspaces/<name>/toolchain/<i>`) and `detail.path` naming what it resolved to. At load, for
  `roles.<name>.prompt_file`'s reason and one of its own: a workspace whose toolchain is absent
  produces a lane that wins an exclusive resource and then cannot run its own `build`, which is a
  refusal arriving after the scarce thing is already taken. `too_broad` is a property of **any declared host path the tool will read-bind into a lane** —
  a `workspaces.<name>.toolchain` entry and `agent.command`'s resolved directory alike, since
  `confine` binds both from the same list. It is `/`, or any path that
  CONTAINS the developer's home — the home itself, its parent, and anything above that. One
  manifest line naming a whole home, or the directory holding every home, is a read-only bind of
  somebody's ssh keys into every lane for that workspace, which is `SRV-FEAT-002`'s guarantee
  removed by a declaration rather than by a bug. The question is what the path IS, not how it is
  spelled (`SRV-FIX-077`), and it is asked as a RELATIONSHIP rather than against a list of
  spellings (`SRV-FIX-078`, where `/home` was neither `/` nor the home and loaded cleanly).
  A path UNDER the home is the ordinary case and stays allowed — `~/.espressif` is what a
  workspace is supposed to declare.
- **A command a lane could not run** — a `warnings` entry with `code:"command_unrunnable"`,
  `pointer` naming the command (`/workspaces/<name>/<key>`), `program` naming the word that was
  not found and `field` naming `workspaces.<name>.toolchain`. **The manifest still loads and the
  lane is still claimed**; this is a fact about the repository, not a refusal. Asked of **every**
  command a workspace declares — `build` and `gate` are two members of one set — because
  `SRV-FIX-077` proved the toolchain exists and `SRV-FIX-191` proved a client lane can `build`,
  and neither asked the question one word over: this repository's own `server` and
  `factory-server` declare `pytest tests/ -v`, which no lane has ever been able to run, and every
  suite they were gated on was run by an agent that installed pytest into its lane's tmpfs by
  hand (`SRV-FIX-202`).
  A program may resolve in the three places `confine` gives a lane and nowhere else — `/usr`, the
  workspace's declared `toolchain`, and the repository — and a bare word is looked for in the
  executable directories those three PROVIDE: `/usr`'s standard `bin`/`sbin` set, each declared
  toolchain and the `bin` directories under it, and the repository's own `bin`. **Never on the
  server's own `PATH`.** `SRV-FIX-202`'s QA measured one manifest answering `200` from a server
  started by a login shell and `422` from one started with a plain `/usr` `PATH`, while inside the
  lane the program was `not found` in both — the toolchain is bound and never reaches the lane's
  `PATH` (`SRV-FIX-218`, open). A word carrying an expansion or a glob is **skipped, not
  reported** — a check that reports what it cannot read is the same defect as a check that
  reports everything.
  It is a warning rather than a refusal because a refusal lands on the repository before the
  manifest line that answers it does, and the manifest is the human's file: round 1 of
  `SRV-FIX-202` refused, and on this repository `GET /api/manifest` became `422` and every
  `POST /api/lanes` `409 invalid_manifest` — six workspaces declared, none claimable, including
  one that declares no commands at all. Refusing again is a decision for the day the manifest
  declares what its gates need AND `SRV-FIX-218` has made the lane's `PATH` derive from that
  declaration, which is what makes the prediction sound in both directions.
- **Untrusted agent configuration** — `agent.config_trust` declared and not satisfied:
  `422 invalid_manifest` with `detail.reason:"agent_config_untrusted"`,
  `detail.pointer:"/agent/config_trust"`, `detail.path` naming the **configuration file** and
  `detail.field` naming the **key to add inside it**. `pointer` says which part of the manifest is
  wrong; `field` says what to write where it points, so the refusal can be acted on without
  reading the source. Refused at load and not at the run, for `roles.<name>.prompt_file`'s reason
  (F31) and one worse: an agent whose declared permissions were dropped does not fail — it runs,
  answers, and asks about everything, which is what the product looks like when it is working.
  Absent means no check at all.

#### E3 · GET `/api/requirements`
- **Request** — `?q=<substring, optional>&filter=all|needs|running|blocked|done` (default `all`).
  `q` matches id and title, case-insensitively. `done` keeps the rows whose `delivery.state` is
  `done` — §3.5's word for *it is on the base branch*, which a requirement earns only when every
  ticket under it has — and drops every other row, the standalone row included. It is the question the other four cannot ask: they are about work in flight, and
  this one is about work that is over. A repository git cannot read reports `unknown` on every
  row, so `filter=done` there returns an empty list and never all of them.
- **Response** — `200 {"requirements":[{id,title,status,parent,children:[…],updated_iso,workspaces:[…],state:{process,motion,evidence},delivery:{…},needs_you:{count,label},ticket_count,own_ticket_count,parse_error?}],"total","in_flight"}`.
  `label` is the pill's text (`2 need you`, `unproven claim`, `waiting on you`) and is computed
  server-side so the list and the counter cannot drift apart. `delivery` is §3.5's object —
  `{state,detail,claimed,disagrees,needs_you,reason?}` — and on a requirement row it aggregates its
  tickets' by §3.5's rule: `done` only when every one of them is, otherwise the most urgent of those
  that are not. `needs_you.count` counts §3.3's clauses, `stalled` and `unshipped` among
  them.
- **The tree is `parent` and `children`, and the array is flat** (`SRV-FEAT-547`).
  `requirements` carries EVERY requirement. `parent` is the id this one hangs under — resolved by
  id across every repository the manifest declares — and `""` when it names none, names one that is
  not on disk, or is in a cycle; `children` is its direct children's ids, in the order the
  documents are listed. A **top-level row is one whose `parent` is `""`**, and a surface draws a
  row with a parent under that parent and never at the top (§4.2). Ids and not nested objects,
  because a child's own row is already in this array and a second copy of it could disagree with
  the first; a `children` id the body does not carry — a filter narrowed it away, or a document was
  deleted mid-request — is a row the surface draws at the top rather than a crash.
- **`ticket_count` is the roll-up; `own_ticket_count` is this requirement's own.** The roll-up
  spans the requirement and every descendant, and so do `state`, `delivery`, `needs_you` and
  `workspaces` — which is why `filter=needs` and `filter=done` weigh a requirement by everything
  beneath it, one level up from the rule `SRV-FIX-166` wrote for a requirement's own tickets.
  `own_ticket_count` is what `ticket_count` meant before `SRV-FEAT-547` and is the only number on
  the row that is not rolled up.
- **A parent that cannot be followed is a warning, never an error.**
  `warnings:[{code:"unknown_parent",id,value,message}]` — the requirement's `parent:` names no
  requirement in any declared repository; it stays top-level and its own children still nest under
  it. `warnings:[{code:"parent_cycle",ids:[…],message}]` — one entry per cycle, `ids` naming its
  members in the order the walk found them; every member stays top-level. `200` either way, and the
  same two warnings reach `E4`. The walk visits each requirement once, which is what bounds both.
- **Reads product repositories only** — a repository the manifest declares `"role": "tool"`
  (§9.1) is the thing the work is done WITH, and its requirements are not rows here. `total`, the
  `needs you` totals and every one of the five filters are computed over the product repositories
  alone, so the count line and the rows narrow together and neither can report the other's set.
  This removes rows from THIS list and nothing from the tool: the same documents stay addressable
  by id at `E4`, `E5` and `E6`, claimable at `E12`, and visible on the lane rail at `E9`
  (`SRV-FIX-199`). `role` defaults to `product`, so a manifest that never heard of the key lists
  exactly what it always did; a manifest whose every repository is `tool` is refused at load
  (§9.1) rather than drawing an empty screen.
- **Standalone** — after the requirement rows, ONE synthetic row for the tickets whose `parent`
  names no requirement on disk: `id:"standalone"`, `title:"Standalone"`, an empty `status` and
  `path` because no document backs it, `updated_iso` the newest of those tickets', and
  `workspaces`, `state`, `delivery`, `needs_you` and `ticket_count` aggregated over them exactly
  as a real row aggregates over its children. It is counted in `total`, matched by `q` and dropped
  by `filter=needs` like any other row. No orphan tickets ⇒ no row; a repository that declares a
  requirement with `id: standalone` owns the name and its document is the only row. It aggregates
  the PRODUCT repositories' orphans and no others — it is a row of this list, and it narrows with
  the rest of it.
- **Missing** — the `docs.epics` directory absent: `200` with an empty list and
  `warnings:[{code:"path_missing",path}]`. A missing directory is a manifest mistake, not an error
  state, and the screen says which path it is.
- **Manifest invalid** — `200`, an empty list, and
  `warnings:[{code:"invalid_manifest",message,pointer?,path?,field?}]` carrying E2's own refusal
  verbatim. **E9 answers the same way**, with empty `lanes` and `resources`.

  A failed load and a repository with no work in it produced the identical picture — empty
  everywhere, `warnings:[]`, nothing anywhere saying why — and in this repository a manifest that
  declares a device makes an unplugged USB cable load-bearing for whether any tickets appear at
  all (`SRV-FIX-071`). `200` and not a refusal, because `E1` already answers `200` with
  `manifest:"invalid"` and a data endpoint contradicting that would be the tool disagreeing with
  itself about whether it is up; the `code` is what lets a surface draw this as THE REASON the
  list is empty rather than as a footnote under it. Nothing is half-loaded: the lists are empty
  because nothing loaded, and that is what the warning says.

#### E4 · GET `/api/requirements/{id}`
- **Request** — path id, as written in the document's frontmatter.
- **Response** — `200 {id,title,summary,body_markdown,needs_you:{count,total},delivery:{…},children:[{id,title,repo,status,own_ticket_count,ticket_count,delivery:{…},tickets:[…groups…]}],groups:[{workspace,stage_sequence:[…],count,tickets:[{id,title,state,delivery:{…},stage:{index,total,round?},lane,cost_usd}]}],warnings:[…]}`.
  Every ticket row carries its own §3.5 object; the requirement's own aggregates them by §3.5's rule.
  Groups are ordered as the manifest declares its workspaces; a ticket naming a workspace the manifest
  does not declare goes in a final group called `unassigned` rather than disappearing.
- **`children` is the direct child requirements, one level deep** (`SRV-FEAT-547`), in E3's order,
  each carrying that child's `repo` — the manifest's own name for the repository holding it — and
  its tickets grouped **in E4's own `groups` shape, reused**: byte for byte what `E4` for that child
  answers. A grandchild belongs on ITS parent's page; `E3` is where the whole tree is. A child's
  `ticket_count` is the same roll-up `E3`'s row for that id carries, so one word has one meaning on
  both endpoints, and `own_ticket_count` sits beside it because the roll-up is drawn next to a
  collection holding the own tickets only.
- **`state` and `delivery` here are rolled up** over this requirement and every descendant, so
  §4.2's row and §4.3's header cannot say two different words about the same document in the same
  instant (F27). **`needs_you` is not**: it is `{count,total}` over the `groups` this page draws,
  and a total over tickets the page does not carry would be a claim about a collection nobody can
  see.
- **`warnings`** — the same array `E3` answers, `unknown_parent` and `parent_cycle` included.
- **Standalone** — `id: standalone` answers in this same shape from the tickets whose `parent` names
  no requirement on disk, with the tool's own one-line `summary` in place of a document's. In a
  repository with no such ticket it is `404 not_found`, the same as any other id nobody wrote — a
  group with nothing in it does not exist, which is the rule that keeps the row off E3. Its members
  are the PRODUCT repositories' orphans only, because it is E3's synthetic row opened
  (`SRV-FIX-199`).
- **A tool repository's requirement is still served by id.** `role` (§9.1) decides what E3 LISTS and
  what the synthetic `standalone` group collects; it does not decide what an id resolves to. A
  requirement of a repository declared `"role": "tool"` answers `200` here with its own repository's
  tickets grouped exactly as a product requirement's are — the same guarantee that keeps `E5`, `E6`,
  `E7`, `E12` and the lane surface whole. Readable and displayed are different questions
  (`SRV-FIX-199`).
- **Missing** — `404 not_found` with `detail.path`.

#### E5 · GET `/api/tickets/{id}`
- **Request** — path id.
- **Response** — `200 {id,title,workspace,status,parent,stages:[…],stage:{index,total,round,of,source},extras:[{label,value}],body_markdown,path,lane,runs:{count,duration_s,cost_usd},report:{exists,evidence},state:{…},delivery:{…}}`.
  `delivery` is §3.5's object for this ticket, and `status` beside it stays exactly what the
  document said — the tool reports both and rewrites neither.
  `roles:[{role,stage,state:"pending"|"started",lane?,run_id?}]` is F31's, present only when a
  declared role has been reached for this ticket. It is where a role waiting for a lane seat is
  visible: `pending` is *queued, not dropped*, and a role the tool started silently would rebuild
  the failure F31 exists to fix one layer down.
  `extras` is exactly the manifest's `display.ticket_fields`, in declaration order, values passed
  through untouched — the tool does not know what any of them mean. `stage.source` is `ticket` when the
  document declared its own sequence and `manifest` when it fell back.
- **Missing** — `404 not_found` with `detail.path`. A ticket whose frontmatter will not parse: `200`
  with `parse_error` and `body_markdown` intact, so the document can still be read.
- **Ambiguous** — one id under the `tickets` directory of two declared repos:
  `409 ambiguous_ticket_id` with `detail.paths` naming every file that claims it. Never `200` with
  the first of them: first-wins is the cheap implementation of a collection, and it serves the
  wrong document forever without ever saying so. E6 and E12 answer the same way for the same id.

#### E6 · GET `/api/tickets/{id}/report`
- **Request** — path id.
- **Response** — `200 {path,body_markdown,evidence:{verdict:"claimed_proven"|"claimed_unproven",run_id?}}`.
- **Missing** — `404 not_found` with `detail.path`. This 404 is the normal case, not an error: it is
  what the empty state in 4.9 renders, and the client must not log it as a failure. The path it
  names is in the repo that holds the ticket: a report belongs beside the document it reports on.

#### E7 · GET `/api/tickets/{id}/runs`
- **Request** — path id.
- **Response** — `200 {"runs":[{run_id,n,started_iso,outcome,stage,duration_s,cost_usd,lane,session_id,tree,effort}]}`, newest first. `tree` is the run's recorded `{name,decider,agents}` selection; historical records without that field read as the implicit `off` tree. `effort` is the reasoning effort actually sent, or `null` when none was sent. Both values come from the run's own `meta.json` and survive restart and lane reuse.
- **Missing** — unknown ticket: `404`. Known ticket with no runs: `200` and an empty list — the two
  are different questions and must not share an answer.

#### E8 · GET `/api/runs/{run_id}/transcript`
- **Request** — `?after_seq=<int, default 0>&limit=<int, default 200, max 1000>`.
- **Response** — `200 {"run_id","entries":[{seq,ts,kind,…payload}],"next_after_seq","complete":bool}`.
  Entry kinds and payloads are the table in §4.6.
- **This body is also `M1 snapshot`'s `data` for a lane scope**, with `lane` added — over HTTP for a
  named run, over the socket for whatever a lane's log still holds. One body, so a surface fills from
  either without a second code path. **`entries` is entries in both**: an entry carries `kind` and no
  `m`, and `M5`'s frame — which wraps one under `entry` — is not a member of this list. The socket's
  sequence log holds whole `M5` frames because `resume` replays frames (§6.2); it renders the entry
  and the frame together, from one call, and hands each path its own. `SRV-FIX-049` is what happens
  when it does not: `entries` carried frames, the client drew every one as nothing, and the whole
  transcript blanked on each resubscribe while every assertion about the list stayed green.
- **Missing** — unknown run, or a run whose log has been cleaned: `404 not_found` with `detail.path`
  pointing at the expected `transcript.jsonl`, and **`remedy: null`** — §11 check 12's site-exemption
  table carries the reason. History is expendable (§2) and the screen says so plainly rather than
  pretending the run never happened — the run's `meta.json` row survives in E7. The `remedy` member
  is present and null; absent is a different answer and not this one.

#### E9 · GET `/api/lanes`
- **Request** — none.
- **Response** — `200 {"lanes":[{lane,ticket_id,workspace,role,branch,worktree,state:{process,motion:{value,silent_for_s,since},evidence,blocked?:{reason,request_id}},stage:{index,total,round},progress_pct,remaining_s,elapsed_s,cost_usd,last_words,run_id,session_id,resources_held:[…],waiting_for?}],"resources":[{name,exclusive,description,held_by,queue:[…]}],"seats":{"host":{"total","used"}}}`.
  `progress_pct` is the share of the ticket's own stage sequence that is FINISHED —
  `(stage.index - 1) * 100 / stage.total`, integer, floored at 0, since the stage last reported is
  the one in progress — and `100` once the ticket's `status:` is one the manifest declares under
  `status.done`. `stage.index` and `stage.total` are both positions in the ticket's own sequence
  (the manifest's only when the ticket declares none). It was `stage.index / stage.total` against
  the manifest's length until `SRV-FIX-599`: a `pass` ticket whose process never reports the stages
  after `QA` drew 70 % for ever.
  `remaining_s` is how long the stages still ahead are expected to take, measured and never
  guessed: for each, the median of its durations in this workspace's stage history — the time
  from its stage entry to the next one OF THE SAME RUN, or to that run's end when it was the run's
  last — less the time
  already spent in the current stage. A stage nobody in the workspace ever measured adds nothing;
  one with only one or two measurements makes the answer `null`, as does a sequence where nothing
  ahead was ever measured. `0` when the ticket is done. Required and nullable.
  `last_words` is what the agent in that lane last SAID or last CALLED —
  `{kind:"prose"|"tool"|"file_edit",text,at}`, or `null` until this run's first such entry
  (`FAC-EPIC-373` §3). `text` is the first non-empty line of a prose block clipped to 160 code
  points with a trailing `…`, the tool's name for a `tool`, and the tool's name, one space and the
  path the transcript carries for a `file_edit`; `at` is the server's clock when the entry was
  parsed. It is **required and nullable**, never absent: a server that forgets to serve it is red
  in the browser gate's schema check rather than silently missing from a card. It is memory beside
  `cost_usd` and is not persisted, so a live lane reads `null` after a restart until its next
  entry, and it is reset where `run_id` is set for a new run (`E18`).
- **Missing** — no lanes: `200` with empty arrays and the seat counts from the manifest. A worktree
  that has vanished from disk under a live lane: that lane comes back with
  `state.process:"crashed"` and `detail.reason:"worktree_missing"` rather than being omitted — and
  it keeps `needs_you`, because it died and nobody has looked (§3.3).
  **It is reported for a grace and then it is gone.** The tool confirms the absence over two
  sweeps of its own cadence and then reaps the lane: the process group is killed, the resources are
  released, the queue behind them advances, and the row leaves `E9` like any other released lane.
  So the clause above is the FIRST of two phases, not a permanent state — `reported` while a
  person can still act on it, `removed` once the tool has. This sentence used to say only the
  first half, from a time when nothing ever cleaned the lane up, and `SRV-FIX-105` is where the
  second half came from: a lane the tool had forgotten kept an agent process and an exclusive
  resource that the lane behind it was queued on.
  **A `stat` that could not answer is not an absence.** An unreadable directory, an unmounted
  filesystem or an unreachable lane root leaves every lane exactly where it was — the reap acts on
  *absent*, never on *could not tell*, and never at all while the lane root itself cannot be read.
  **A lane root that is GONE is an answer, not a silence**: every worktree beneath it is gone, and
  every lane under it is reaped. `rm -rf` on the lane checkouts is how this state is reached in
  practice, and a tool that refuses to act on it strands exactly the lanes it exists to clean up.
  **The two confirmations are consecutive.** An interval the tool could not see through resets the
  count rather than being counted through: two observations either side of a blind window have
  confirmed nothing about the window. The cost is deliberate and one-directional — a filesystem
  that flaps between *absent* and *unreadable* is never reaped and the lane is released by hand,
  because a lane that outlives its grace can be recovered and a killed one cannot.
- **Manifest invalid** — `200`, empty `lanes` and `resources`, and the same
  `warnings:[{code:"invalid_manifest",…}]` E3 carries, for the same reason: a rail with no cards
  and a rail whose manifest never loaded are the same picture and must not be (`SRV-FIX-071`).
- **A checkout no record describes** — `SRV-FIX-432`. The lane set is the set of CHECKOUTS under the
  lane root, never the set of files the last process happened to leave. At startup the tool reads
  `.factory/lanes/*.json` back and then sweeps `<worktrees>/lane/` itself, and **every entry
  directly under it is accounted for exactly once**: it is a lane, or it is a warning.
  An entry is **adopted** iff it is a registered worktree of this repository, sits directly under
  the lane root, and its branch matches `ticket/*` — id and role read back off the branch
  (`ticket/<id>` is role `run`, `ticket/<id>-<role>` is that role), `workspace` `""` and
  `resources_held` empty because `app/lane` may not read a ticket's frontmatter and an invented
  workspace would claim resources for a lane in which nothing is running, `state.process`
  `"not_started"` because adoption starts nothing. The derivation is then **written down** —
  `.factory/lanes/<lane>.json`, exactly as a claim writes it — so it is a repair and not a
  per-boot guess.
  Anything else under the lane root comes back as `warnings:[{code:"unaccountable_worktree",path,…}]`,
  one row per entry, alongside `invalid_manifest` rather than instead of it. **Adoption is a READ**:
  nothing is created, moved, pruned or removed to produce either answer, and `E13` stays the only
  path that removes anything.
  This is what `S5` costs to keep true. On 2026-09-06, on this repository, `.factory/lanes/` was
  empty, `E9` answered `{"lanes":[]}`, and `git worktree list` held four lane checkouts carrying
  36 commits — the sole authority on *is an agent running in it* silent about every one of them.

#### E10 · GET `/api/lanes/{lane}/diff`
- **Request** — `?path=<file, optional>` for one file's patch instead of the summary.
- **Response** — `200 {"lane","branch","base","files":[{path,status,added,removed}],"commits":[{sha,subject,iso}],"patch"?}`. Totals included.
- **Missing** — unknown lane: `404`. A lane whose branch has no commits and no changes: `200` with
  empty arrays, which the screen renders as *"Nothing changed in this lane yet."*

#### E11 · GET `/api/permissions/rules`
- **Request** — none.
- **Response** — `200 {"policy":"ask_all"|"edit_free"|"never_ask","policy_source":"manifest"|"session","rules":[{rule_id,pattern,source:"manifest"|"granted",granted_iso,match_count,revocable}]}`.
- **Missing** — no `.factory/permissions.json`: `200`, empty `rules`, `policy` from the manifest. An
  unparseable file: `200` with `warnings` and `rules:[]` — a corrupt rules file must fail closed, to
  asking, never open.

#### E12 · POST `/api/lanes`
- **Request** — `{"ticket_id","workspace","role":"<free string, default \"run\">","request_id"?}`.
- **Response** — `201` with the lane object of E9. The tool creates the worktree and the branch
  `ticket/<ticket-id>` (a second lane on the same ticket gets `ticket/<id>-<role>`), and claims the
  exclusive resources that workspace needs. If a resource is held, the lane is created `queued` with
  `waiting_for` set — a queued lane is a real lane, visible in the rail, not a failed request.
- **A ROLE JUDGES THE WORK, SO A ROLE LANE IS CUT FROM THE BRANCH THE WORK IS ON.** A lane whose
  role is not `run` is branched from `ticket/<ticket-id>` — the branch of that ticket's `run` lane,
  which §4.4 makes unique — when that branch exists, and from `HEAD` when it does not. A **released**
  run lane still counts: `E13` always keeps the branch, and releasing the run lane before starting a
  role is the normal sequence rather than an edge case. A `run` lane is cut from `HEAD`, unchanged:
  the work starts from where the repository is. The role's branch is still its own — `ticket/<id>-<role>`,
  branched FROM the run branch and never a second checkout OF it, so what a role commits cannot move
  the branch it is judging. Before `SRV-FIX-184` every lane was cut from `HEAD`, which is the MAIN
  checkout's commit: `SRV-FIX-174`'s QA lane carried three commits of a concurrent session and none
  of the fix it existed to judge, and `status: pass` — which only a role may set — can be set from a
  tree the work is not on. The request and the response are unchanged; a base ref is not on the wire.
- **AND ON EVERY CLAIM, NOT ONLY THE FIRST.** `E13` always keeps the branch, so every claim of a
  role after the first meets a branch that already exists — and a claim that merely checks that
  branch out hands the role the round it last saw. `docs/TICKET-WORKFLOW.md` makes a QA FAIL the
  beginning of a round whose first commit is a RED test on the run branch, and the session that
  checks that commit is a role lane claimed for the second time. So the work is **brought to the
  role on every claim**: `ticket/<ticket-id>` is MERGED INTO `ticket/<ticket-id>-<role>`, which
  keeps the role's own commits — its earlier verdict is the evidence a release exists to preserve —
  and moves `ticket/<ticket-id>` not at all. A base already in the role's history is a no-op, so a
  re-claim with no new work leaves the branch exactly where it was. **Only a role follows the
  work**: a `run` lane re-claimed is exactly as it was left, because a tool that moved somebody's
  branch between claims would be rewriting their work. And when the work cannot be brought in
  cleanly the claim **refuses** — `409 work_conflicts`, naming the lane, the base and git's own
  conflict text — and leaves the branch where it was: a tree that is neither round looks current,
  where a stale one at least looks stale. `SRV-FIX-184` resolved the base correctly and then the
  branch-already-exists path discarded it, so the second QA round of a ticket read the first
  round's tree (`SRV-FIX-189`).
- **What a lane carries** — every process a lane runs is confined, and what it can reach is a list,
  not a filter: its own worktree (writable), the four places under `<repo>/.git` a commit writes,
  a toolchain and `/etc` read-only, an empty tmpfs `HOME`, a tmpfs scratch, **the directory the
  agent's binary lives in, read-only**, and **one directory of its own for the agent's
  configuration, writable, at `/factory/agent-config`**. The main checkout is ABSENT, not denied.
  The developer's own configuration directory is not bound at all: the tool carries the one
  credential file `agent.credentials_dir` names into the lane's directory — a hard link where one
  filesystem allows it, a `0600` copy where it does not — and GENERATES the trust the agent needs
  for the repository root, in a file the tool owns. That directory is outside the repository and
  outside the developer's home, it is one per lane, and it is removed when the lane is released,
  so what an agent writes about its session dies with the lane that wrote it. Nobody edits
  anything in their home to start a lane.
- **A lane's `/dev` is its own**, and this is the sentence that was missing. The confinement mounts a
  fresh minimal devtmpfs, so none of the host's device nodes are in it — which made an exclusive
  resource that IS a device a lock the tool honoured over a thing the winner could not reach, so no
  workspace whose gate has to talk to one could ever run in a lane (`SRV-FIX-063`). A resource may
  therefore name device nodes with `resources.<name>.devices`, and each one is bound **at the path the manifest wrote,
  into the lane holding that resource, and into no other**. At the path it WROTE and not at the one
  it resolves to: a manifest names a device through a stable symlink precisely because the kernel's
  numbered node can change between boots, and binding the canonical name instead gives the lane a
  device under a name nobody declared — every script and prompt that uses the manifest's own string
  then meets `ENOENT` while the tool reports the resource held, which is this paragraph's own defect
  one name down (`SRV-FIX-069`). Only the declared name is bound; adding the canonical one as well
  would put something in the lane no manifest asked for. **And `devices` is only meaningful on an
  `exclusive` resource** — nothing queues on a non-exclusive one, so no lane ever holds it and the
  devices would be bound into none; declaring both is refused at load with
  `detail.reason:"devices_need_exclusive"` (`SRV-FIX-070`). Never a directory and never `/dev` itself: a lane that
  could see the host's `/dev` could see every disk on the machine, which is the guarantee
  `SRV-FEAT-002` exists for. A lane holding no resource is given no device, so "the lane holds the
  resource" and "the lane can open what the resource declares" are the same sentence rather than two.
- **And a lane gets what its WORKSPACE says it needs from the host**, which is the other half of the
  paragraph above and the half `SRV-FIX-063` left unbuilt. A device the lane can open is worth
  nothing without the toolchain that talks to it: a compiler installed under the developer's home
  does not travel into a tmpfs home, and no `devices` entry can carry a D-Bus socket because an
  adapter has no node. So `workspaces.<name>.toolchain` names host paths — a directory, a file, a
  socket — and each is bound **read-only, at the path the manifest wrote**: `~/x` under the LANE's
  own home, because inside a lane `~` is the lane's home and that is where a build script looks;
  anything else at its own absolute path. Read-only is the whole of the widening, and the lane's
  home stays an otherwise-empty tmpfs. A workspace that declares nothing gets nothing, exactly as
  before the key existed (`SRV-FIX-077`).
- **Two questions are asked before anything is taken**, on a worker, before the reservation: does
  every declared toolchain path still exist, and does a process on this host already hold a device
  of an exclusive resource this workspace needs. Either answer refuses the claim — `409
  workspace_unrunnable` with `detail.reason:"toolchain_missing"`, or `409 device_busy` with
  `detail.reason:"device_in_use"` naming the `pid` and `comm` — and a refused claim creates no
  record, no branch and no worktree, and leaves every resource free. **The ordering is the
  guarantee**: a workspace that cannot run its build may not hold the scarce thing while it fails.
  The second question is asked out here because it cannot be asked in there: `--unshare-pid` hides
  every process that is not the lane's own, so the usual `lsof`/`pgrep` check answers *clean* from
  inside a lane no matter who is using the device — inoperative, and failing open. It is a floor
  and not a proof: a holder owned by another user is not readable in `/proc` and reads as no holder.
- **No git root** — `409 no_git_root`, naming the document root it searched up from. Confinement and
  worktrees need a git root, and without one every run in the lane would answer
  `confinement_unavailable` after a branch and a worktree had already been made. A lane that cannot
  be confined can never be run, so the tool does not hand one out (`SRV-FIX-290`).
- **A ticket the repository says has passed** — `409 already_passed`, in every role, with
  `detail` naming the `ticket_id`, the `status` the document carries and the `path` of the file it
  was read from, relative to the document root. The endpoint already loads the ticket document to
  answer *does this id exist*; until `SRV-FIX-478` it used only whether the lookup found it, so one
  click on a ticket whose file says `status: pass` cut `ticket/<id>` again, took a seat, took the
  workspace's exclusive resources and started an agent on work that is finished. The comparison is
  `doc.status == "pass"`, exact and lowercase — the same literal `land`, `delivery` and `catalog`
  already compare, so no vocabulary is added and `S1` is not extended. It is decided in `api`, on
  the document, **before `lane::Store` is entered**: the same ordering `workspace_unrunnable` and
  `no_git_root` hold, so a refused claim leaves no request-id record, no reservation, no seat, no
  branch and no worktree. The refusal is by status alone and names no role — a passed ticket has
  nothing left to run and nothing left to judge. Every other status claims exactly as before, a
  document with no `status:` line included, because a FAIL *begins* a round and the round needs its
  lane. A lane that was live when the pass was written is not touched by this refusal. The tool lets
  go of it once the pass has LANDED (`SRV-FIX-600`): a lane whose session has finished its turn
  (`idle`) is ended through `E16`'s path, and once its process has exited it is released through
  the same `lane::Store::release` call `E13` makes — never `force`, never a kill, branch kept,
  `E13`'s worktree rules unchanged (`S5`). A lane `running` or `blocked` on a permission is never
  ended, and a passed ticket whose work is not on the base branch keeps every lane it has. The status is read from the file on every
  claim, so the way back from `pass` is a person editing the document or filing a `FIX` — the tool
  writes none of the repository's documents (`S2`), which is why the remedy is `null`.
- **Missing** — unknown ticket or workspace: `404` naming which. No free seat: `409 no_seat` with the
  seat counts. A lane that cannot be confined does not run a process: `409 confinement_unavailable`
  at the moment something is started, with `detail.reason` — and a confinement is refused rather
  than weakened, including when the tool's own configuration directory would land inside the
  developer's home.

#### E13 · DELETE `/api/lanes/{lane}`
- **Request** — `?force=true` optional.
- **Response** — `200 {"lane","released":true,"worktree_removed":bool,"branch_kept":true,"detail":{"reason",…}}`.
  The branch is **always** kept — every refusal and every success below leaves it by name, so no
  answer this endpoint gives can destroy a commit. The worktree is removed only when the lane holds
  no uncommitted change, and `detail.reason` says which happened. Held resources are released and
  the queue advances.
- **And the same call answers for a lane that exists on disk and NOT in memory** — the ordinary
  state after a restart, since the tool does not adopt lanes it cannot supervise. This used to be a
  `404`, keyed on exactly the record that is missing, so the tool refused to start *and* refused to
  clean up and the only way forward was a shell (`SRV-FIX-059`). Now `released` is **`false`** —
  there was no lane to release — with `detail.reason:"leftover"`, and the four answers are:
  `404 not_found` when nothing is at the path; `409 not_ours` (`detail.ours:false`) when what is
  there is not a worktree this repository registered; `409 uncommitted_changes` with the count;
  and `409 unmerged_commits` with `detail.branch` and `detail.commits` when the branch carries work
  **no other branch, tag or remote has**. Otherwise `200` and the checkout is gone.
- **The two removals in this endpoint do not ask the same question, and the difference is the
  point.** A *supervised* release — a lane the tool has a record of, ended by whoever can see its
  row — removes a worktree whose branch has unmerged commits, because the branch is kept and
  refusing would leave the checkout for the next claim of that lane number to trip over. A
  *leftover* clear is pressed blind against something the tool cannot describe, so it refuses on
  both halves of "holds work": an uncommitted change **and** a commit nothing else carries. Counted
  against every other ref rather than against `main` by name — the question is whether the work
  exists anywhere else. `git status --porcelain` answers only the first half, and asserting only
  that half is how the second one shipped unguarded (`SRV-FIX-059` QA, finding 1).
- **An IGNORED path is work too, and it is refused separately with a consent of its own.**
  `git status --porcelain` does not list what `.gitignore` covers, so a checkout holding a gate's
  own capture, a build tree, an embedded database or a private key answered `uncommitted_changes:
  0` — an affirmative statement that the directory held nothing. The removal now asks
  `git status --ignored` as well and refuses with `409 ignored_work`, **naming every path**, because
  a person deciding whether to lose something is owed what it is. When git cannot say what is there
  the answer is `409 unreadable_worktree` and the removal is refused: consenting to lose a list
  nobody could produce is not consent. `?discard_ignored=true` is that consent and means only
  *"I have read those paths and accept losing them"* — it is **not** `force`, which means kill the
  process, and it waives nothing about uncommitted work or an unmerged branch, both still checked
  after it and both still refusing.
- **So a role lane's checkout SURVIVES a forced release, and that is the decided answer** (F31,
  `SRV-FIX-059`). A started role writes — a verdict, a log, a capture — so by the time anyone stops
  it the lane holds something git will not reproduce, and the rule above applies to it exactly as it
  applies to a person's lane. The release still succeeds: the lane goes free, the seat comes back,
  held resources are released and the queue advances, the branch is kept, and `worktree_removed` is
  `false` with `detail.reason` naming which half refused. **A release that destroys a checker's
  evidence to tidy a directory has the failure backwards.** The consequence is real and belongs
  here rather than in a reader's surprise: stopped role lanes accumulate checkouts under
  `.factory/worktrees/`, and the way to clear one is this endpoint with `discard_ignored` — never a
  shell, because only the tool can say whether a lane is live (root `CLAUDE.md`).
- **Missing** — unknown lane: `404`. A live process: `409 wrong_state` unless `force=true`, which
  kills it first (E17) and says so in the response.

#### E14 · POST `/api/lanes/{lane}/messages`
- **Request** — `{"text","request_id"?}`.
- **Response** — `200 {"status":"delivered"|"held","held_count","entry_seq"}`. `held` when the process
  is mid-turn: the text is queued, appears in the transcript with `mark:"held"` under the design's
  rectangle, and is delivered — in order, all of it — by the next interrupt (E15). Nothing is dropped.
- **Missing** — unknown lane: `404`. No live session: `409 wrong_state`. Empty text after trimming:
  `400 empty_message`.

#### E15 · POST `/api/lanes/{lane}/interrupt`
- **Request** — `{}`.
- **Response** — `200 {"interrupted":true,"delivered":<n>}`. Interrupts the turn, then delivers every
  held message in order. What already ran stays on disk and nothing is reverted — the design's
  promise, and it is a promise about behaviour, not copy.
- **Missing** — unknown lane: `404`. Not mid-turn: `200 {"interrupted":false,"delivered":<n>}` —
  delivering held work is still the right thing, so this is not an error.

#### E16 · POST `/api/lanes/{lane}/end`
- **Request** — `{}`.
- **Response** — `202 {"ending":true}`. The current turn is allowed to finish, stdin is closed, the
  process is reaped, and the run's outcome is `ended cleanly`. `M11` reports the transition; the lane
  stays claimed and the session id is retained so a later run can resume it.
- **Missing** — unknown lane: `404`. No live process: `409 wrong_state`.

#### E17 · POST `/api/lanes/{lane}/kill`
- **Request** — `{"grace_s": <int, default 5>}`.
- **Response** — `202 {"killing":true}`. `SIGTERM`, then `SIGKILL` after the grace period, then reap
  the process group. Outcome `killed by you`. This exists for a process too wedged to hear an
  interrupt, so it must not itself depend on the process cooperating — which is why it is a separate
  endpoint from E16 and why it kills the group, not the leader.
- **Missing** — unknown lane: `404`. No live process: `200 {"killing":false}` — the desired state
  already holds, and an error here would be pedantry at the worst possible moment.

#### E18 · POST `/api/lanes/{lane}/runs`
- **Optional start readiness** — a workspace's `start_preflight` declares a local command,
  `timeout_seconds` (1–60, default 15) and exact `exempt_ticket_ids`. The command executes inside
  the selected profile's confined lane before the agent or its run record is created. Explicit
  prompts, declared prompts, resumed runs and automatic role starts share this check. Its scope
  is passed as `FACTORY_TICKET_ID`, `FACTORY_WORKSPACE`, `FACTORY_AGENT_PROFILE` and
  `FACTORY_AGENT_MODEL`. A missing declaration preserves the existing start behavior. A nonzero
  result or unavailable check returns `409 start_preflight_failed`; timeout returns
  `504 start_preflight_timeout`. A concurrent pending start returns `409 start_preflight_running`.
  Failed checks retain their complete capture separately from agent history. A preflight does not
  approve a ticket's gate or QA; the declared command decides only whether startup is ready.
- **Request** — `{"prompt"?,"resume":bool (default true),"agent"?,"model"?,"tree"?,"effort"?}`. `agent` names one of
  the manifest's declared profiles; omission selects `default_agent`. `model` selects one of that
  profile's declared models, or its default when omitted. `effort` selects one of that profile's
  declared `efforts`; **omitted, it resolves in one order — the lane's role (`roles.<r>.effort`, or
  `run_effort` for a run nobody declared a role for), then that profile's `default_effort`, then
  nothing at all.** A profile declaring no `efforts` is launched with no effort whatever is declared
  above it: it has opted out, and that is the behaviour every repository had before the key existed. Legacy singular `agent` manifests expose
  the `default` profile and retain their existing model-default behavior. `http.schema.json` links
  E18's request to `StartRunRequest`; both request and response types are generated from that file.
- **Tree selection** — `tree` names a preset in the manifest. Omission selects `default_tree`, or
  the implicit `off` tree when the manifest declares no presets. An unknown or malformed selection
  returns `400 invalid_agent_selection`. Before preflight or agent creation, each named agent file
  must be a regular file inside the lane checkout at the selected driver's provider path;
  otherwise E18 returns `409 tree_agent_missing` naming the path. The lane agent receives
  `FACTORY_TREE`, `FACTORY_TREE_AGENTS` and `FACTORY_TREE_DECIDER`; legacy manifests receive only
  `FACTORY_TREE=off`.
- **Response** — `201 {"run_id","session_id","pid","agent","driver","model","tree","effort"}`. The attribution
  is the resolved profile/model/effort, not merely the fields supplied by the request; `effort` is
  `null` when the launch sent none. The effort reaches the provider in that provider's own interface
  and nowhere else: `--effort <level>` once on the Claude argv, beside `--model`, on a fresh
  `--session-id` session and a `--resume`d one alike; `effort` on **every** codex `turn/start`, and
  never on `thread/start` or `thread/resume`, because `TurnStartParams` is the only one of the three
  the installed app-server schema gives an `effort`. Nothing is mapped between the two providers'
  level names. Spawns the declared
  driver inside the lane's worktree with that profile's command, permission mode and allowed tools,
  and with `FACTORY_RUN_ID`, `FACTORY_PROGRESS_URL` and `FACTORY_PROGRESS_TOKEN` in the environment.
  `resume:true` continues the selected profile's existing session; provider session identifiers
  are never translated into another provider's thread identifiers. Executable and credential paths
  cannot be selected by the client. Unknown profiles/models and malformed selection fields return
  `400 invalid_agent_selection` before spawning, with no fallback to another driver. **An `effort`
  the selected profile does not declare, an empty one, and one asked of a profile declaring none are
  that same refusal, before the spawn, and never a quiet fall back to the default.** That refusal has
  to be the tool's because it is nobody else's: the installed `claude` answers `--effort bogus` with
  `Warning: Unknown --effort value 'bogus' — ignoring it and using the default effort.` on stderr,
  exit `0`, and runs at its default — recorded in
  `server/tests/fixtures/cli-effort-2026-10-10-claude-drops-an-undeclared-effort.json`.
- **And with no `prompt`, the repository's own words** — `agent.start_prompt_file`. A start carries no
  text when the person starting it has already said what they want by starting it, so the run is given
  the contents of that file, with `{ticket_id}`, `{ticket_path}`, `{repo_root}` and
  `{triggered_by_run}` substituted and nothing else read — **the same file mechanism, the same four
  placeholders and the same load-time refusal as a role's `prompt_file`** (F31), because a start's
  prompt is that problem and a second implementation of it is the defect one layer up. `{ticket_path}`
  and `{repo_root}` are in **the lane's filesystem** — the ticket under this lane's worktree, and that
  worktree — for the reason F31 gives: a path from the tool's own view of the repository names a file
  the agent cannot open. The prompt goes
  down the run's stdin, which is the only channel that reaches the agent once the input is framed
  (`--input-format stream-json`); a prompt in the argv is answered by nobody. **A `prompt` in the
  request wins**: the typed box is a person overriding the default, never a second message appended to
  it. `resume:true` still applies — a person pressing Start means *run it*, and a resumed session gets
  the declared prompt like any other. A repository that declares no `start_prompt_file` behaves exactly
  as it did before this key existed: nothing is written, and nothing claims otherwise — see §3.2.
- **OPEN, not contract — `SRV-FIX-052`.** A started run that was given nothing *ought* to report
  `idle` rather than `running`: §3.2's process axis is about whether work is happening, a CLI reading
  a framed stdin blocks on an empty one for ever, and reporting `running` for a blocked pipe is the
  process axis answering the liveness question with the work question's word. **The code does not do
  this today** — `app/lane/state.cpp` returns `running` while a turn has not ended, and `turnEnded`
  starts false — and this paragraph is written as the open question it is rather than as a rule,
  because a specification that states a behaviour nothing implements is the drift this document
  exists to prevent. `SRV-FIX-048` made the state unreachable in the configured case and filed the
  rest; the sentence becomes contract in the ticket that lands the code and the test together.
- **Missing** — unknown lane: `404`. A run already live: `409 wrong_state`. A lane still `queued` on a
  resource: `409 resource_busy` with the holder and the queue position. A declared `start_prompt_file`
  that cannot be read at the moment of the start — it was proven at load, so this is a file that went
  away since: `500 start_prompt_unreadable` with `detail.path` naming it, and **no run is started**.
  Starting one that can never be given anything is the failure this rule exists to prevent.
  A ticket the lane's branch does not carry — written, listed by the catalog, and **not committed**:
  `409 ticket_not_on_branch` with `detail.ticket_id`, `detail.path` (the path *in the lane*) and
  `detail.branch`, and no run is started. It is the same rule as the line above applied to the other
  file the prompt names, said before the money is spent and naming what to do about it: commit it
  and start again. A lane's worktree is a **checkout** while the catalog reads the working tree, so
  a document can be on the screen, be offered a Start button, and not exist in the only filesystem
  its agent has (`SRV-FIX-060`).
- **THE TABLE, so the rule is read rather than remembered.** `E18`'s refusals and what the role
  caller does with each. A refusal added to this endpoint gains a row here in the same commit, and
  `test_roles.py::test_every_refusal_e18_lists_is_applied_to_the_role_caller_too` reads this list out
  of this file and requires each row to name its role-side answer. `SRV-FIX-060` applied the rule to
  one refusal and `SRV-FIX-123` to a second, three months apart, because it lived in prose both
  times.

  | E18's refusal | the role caller |
  |---|---|
  | `404` unknown lane | **cannot occur** — the role claims its own lane and holds the name it was given |
  | `409 wrong_state`, a run already live | **cannot occur** — a role lane is new, so nothing is running in it |
  | `409 resource_busy` | **defers** — the start is held and runs when the grant arrives (`SRV-FIX-123`) |
  | `500 start_prompt_unreadable` | **abandons** — the lane is released and the log names the path |
  | `409 ticket_not_on_branch` | **abandons** — the lane is released and the log names the ticket and branch (`SRV-FIX-060`) |
  | `409 start_preflight_running` | **abandons** — another start already owns this role lane |
  | `409 start_preflight_failed` | **abandons** — the lane is released and the log names the check's failure |
  | `504 start_preflight_timeout` | **abandons** — the lane is released after the check is killed and reaped |
  | `400 invalid_agent_selection`, an undeclared effort | **cannot occur** — a role start names no `effort` of its own, and a `run_effort` or `roles.<r>.effort` that a declaring profile cannot honour is refused at LOAD with `422`, before any lane exists. A role has no screen to be refused on, so this refusal is moved to where there is one |

  Two answers, and the difference is whether waiting can change it: a resource is temporary, so the
  start waits; an unreadable file and an absent document are not, so the start is abandoned before a
  process exists.

- **And the same refusal belongs to F31's start, which is the other caller.** `{ticket_path}` is
  built by one function with two callers — this endpoint and a role starting itself — and a rule
  applied to one of them is not applied. A role has no request to answer, so it does not `409`: it
  is **abandoned before any process exists**, the lane claimed for it is released, and the log names
  the ticket and the branch. A role lane needs this more than E18 does, not less: its branch is cut
  fresh from `HEAD`, so it carries neither the uncommitted document nor the triggering lane's
  commits. `SRV-FIX-060` shipped the check at E18 alone and QA reached the agent through the role in
  one step — type a prompt into Start, which needs no ticket, then report a stage.

#### E19 · POST `/api/lanes/{lane}/commands`
- **Request** — `{"which":"build"|"gate"|"review"}`. The three are a CLOSED set of names the tool
  owns, and the only thing it owns about them: what each string does is the repository's business,
  and `review` was added by `SRV-FEAT-500` because `REVIEW` was the one stage of both sequences
  with no command behind it. A name outside the set is `400 bad_which` with `detail.declared`
  listing all three.
- **Response** — `202 {"lane","command_id","which","command":"<the string from the manifest>"}`. Runs that
  workspace's declared command in the lane's worktree; stdout and stderr stream into the transcript as
  a `command` entry, and the exit code lands on it. The tool has no opinion about what the command
  does or what its output means — it declares the exit code and nothing else.
- **Missing** — unknown lane: `404`. The workspace declares no such command: `404 not_declared` with
  `detail.field` naming the manifest key that would have to exist — the same refusal for all three
  names, from one lookup, so a fourth command cannot acquire a fourth spelling.

#### E20 · DELETE `/api/permissions/rules/{rule_id}`
- **Request** — none.
- **Response** — `200 {"revoked":true,"remaining":<n>}`. Rewrites `.factory/permissions.json` and
  emits `M12`. Takes effect on the next request the rule would have matched, including inside a live
  run.
- **Missing** — unknown rule: `404`. A rule whose source is the manifest: `409 not_revocable` with a
  message naming the manifest file, per 4.12.

#### E21 · PUT `/api/permissions/policy`
- **Request** — `{"policy":"ask_all"|"edit_free"|"never_ask"}`.
- **Response** — `200 {"policy","applies_to":"new_runs"}`. Kept for the life of the tool process; the
  manifest remains the default and is not rewritten, because a click in a UI should not silently
  change a committed file.
- **Missing** — an unknown value: `400 bad_policy` listing the three.

#### E22 · POST `/api/progress`
- **Request** — `{"token","stage","round"?,"of"?,"note"?}`. The token is the one injected into the
  run's environment, and it identifies the run — this is how a stage report is attributed without the
  agent knowing its lane.
- **Response** — `202 {"accepted":true}`. Emits `M3 stage` and appends a `stage` entry to the
  transcript. This endpoint is the reason no script has to be copied into a target repository.
- **And a declared role starts, in a new lane** — F31. If the manifest declares a role whose
  `start_on_stage` is the name just accepted, the tool claims a **new lane** for the same ticket in
  that role and starts a run in it carrying the text of the role's `prompt_file`. The start is a
  consequence of the report and not part of the answer: the response is the same `202` either way,
  and the started run is announced by `M11` like any other. Three guards, all F31's: **once** per
  ticket per stage per role, **never** a role's own run starting itself again, and one lane seat —
  with no seat the role is `pending` on E5 rather than dropped. A repository with no `roles` block
  reaches none of this and behaves exactly as it did before F31.
- **And releasing the role's lane makes the role startable again** (`SRV-FIX-256`). The *once per
  ticket per stage per role* guard is a record of a LIVE checker, not a permanent mark on the
  stage: when that lane goes — released by a person from the rail, or reaped after its worktree
  vanished — the record goes with it, and the next report of the same stage starts a new one. The
  guard still refuses a second checker while the first is alive, which is what it exists for.
  Without this, Stop, Kill and Release — the three controls `F31`'s lanes exist to give — were a
  one-way door: later reports answered `202` and did nothing, and `E5` went on naming a lane `E9`
  no longer had.

- **Missing** — unknown or expired token: `404 not_found` — deliberately not `403`, since this
  endpoint is reachable by anything on the loopback interface and should not confirm token shapes. A
  stage name the ticket's sequence does not contain: `202`, recorded, and reported with
  `unknown_stage:true`; the tool has no opinion about stage names (§10) and a mismatch is a fact about
  the repository, not an error.

#### E23 · POST `/api/permissions/decisions`
- **Request** — `{"request_id","decision":"once"|"always"|"deny"}`.
- **Response** — `200 {"decision","rule_id"?,"unblocked":true}`. `once` releases this request only;
  `always` appends one rule to `.factory/permissions.json` (E11 lists it, E20 revokes it) and releases
  the request; `deny` returns the denial to the agent, which continues its turn knowing it was refused
  — a denial is an answer, not a kill.
- **Missing** — unknown or already-answered request: `404 not_found` with `detail.state`, which is the
  normal race when two tabs are open. A request whose run has since died: `410 gone`, and the
  transcript entry is marked voided rather than left waiting forever.

#### E24 · POST `/api/permission`
- **Request** — `{"token","tool","input":{…},"tool_use_id"?}`. The token is the RUN's own, exactly
  as E22's is: valid while the run is live and never after it.
- **Response** — `200 {"decision":"allow"|"deny","reason","asked":<bool>}`, **and it blocks until
  the decision exists** — from the standing policy immediately, or from E23 when a person clicks.
  `allow` and `deny` are the agent CLI's own `permissionDecision` words, because the caller hands
  this straight back to it; `ask` is deliberately not one of them, since this answers only when
  there is nothing left to ask.
- **Who calls it** — the agent's own `PreToolUse` hook, which the tool seeds into the lane's
  configuration directory along with the `settings.json` that declares it (E12, `SRV-FEAT-057`).
  The CLI reads that file out of `CLAUDE_CONFIG_DIR` with no extra argv — measured against
  `claude 2.1.220` and committed as
  `server/tests/fixtures/cli-permission-2026-07-31-pretooluse-hook-vetoes-a-call.json`, because
  `--permission-prompt-tool` does not exist in that version and a remembered flag has cost this
  repository three tickets.
- **Missing** — a token no live run holds: `404 not_found`. An unknown token and a malformed one
  produce the SAME bytes: anything on loopback can reach this, and an endpoint that answers
  differently is one that tells a stranger what a token looks like.
- **The hook FAILS OPEN.** If the tool cannot be reached it prints nothing and the CLI proceeds
  under its own `--permission-mode`. A hook that failed closed would wedge every lane on the
  machine mid-turn whenever the factory restarted, with no way for anyone to say so — the run
  cannot ask and the person cannot answer. **The tool being down is not the same fact as a person
  saying no**, and the two must not produce the same behaviour.

#### E25 · POST `/api/lanes/{lane}/recover`
- **Request** — none. The lane is the path.
- **Response** — `200 {"lane","recovered":true,"session_id","killed":bool,"destroys":[],
  "detail":{"re_carried":[…],"killed_pid"?},"seats":{"host":{"total","used"}}}`. The run's process
  group is killed, the lane's agent configuration directory is re-seeded from
  `agent.credentials_dir`, and **the session is kept**: `session_id` is the one the lane already
  had, `re_carried` names each file put back, and `destroys` is `[]` — which is what makes
  `recover_lane` (§9.2) offerable as the way out of `spawn_failed` and `wrong_state` without
  costing the transcript. The worktree, the branch and the run history are untouched; this is not
  `E13`.
- **Missing** — unknown lane: `404 not_found`. A process that survived being killed:
  `409 wrong_state` with `detail.state`, `detail.lane` and `detail.pid`, **and nothing done** — the
  configuration is not re-seeded and the session is not disturbed, because a half-recovered lane is
  worse than one that refused.
- **Why it is here, three tickets late.** `SRV-FEAT-177` built this endpoint and §9.2's
  `recover_lane` row has pointed at `E25` ever since. §5 had no such number and §11 check 2 could not
  say so: check 2 iterates the blocks that exist and judges each one, so an endpoint CITED and never
  specified is outside its loop. `test_spec.py::test_an_endpoint_cited_but_never_specified_is_a_failure`
  is the case that closes that door (`SRV-FEAT-304`).

#### E26 · POST `/api/tickets/ids`
- **Request** — `{"prefix":"<prefix>"}` — the prefix the id will carry, exactly as it will be
  written: `FAC-FIX`, `FIX`, `WEB-FEAT`. Nothing else; the caller does not say which repository it
  is in and does not say what number it thinks is next.
- **Response** — `201 {"id","prefix","series","number"}`, e.g.
  `{"id":"SRV-FIX-196","prefix":"SRV-FIX","series":"SRV","number":196}`. **One id, to one caller,
  and no two callers ever receive the same one.** Every allocation is serialised on one strand
  inside the tool, so N callers arriving at once get N distinct, contiguous numbers, and the
  answer is on disk in `.factory/ids.json` before it is returned.
- **Missing** — a prefix that no declared repository's tickets use: `422 unknown_prefix`, naming
  the prefix in `detail.prefix` and listing what *is* in use in `detail.known`, and **nothing is
  allocated**. Inventing a counter for a mistyped prefix silently starts a second numbering series,
  which is the defect this endpoint exists to close wearing a different hat. A body carrying no
  `prefix` string is `400 bad_request`. Both are exempt from §3.6 under §11 check 12: the remedy is
  a corrected request, and a call cannot carry the fix for its own malformation.

**Why the tool allocates and the caller does not count.** Every caller of this endpoint is in a
lane, and a lane is a checkout cut at claim time that nothing merges into afterwards (§4.4). A
caller that counts the files it can see counts a different set from its sibling, so two lanes filing
within the same window produce one number twice — two right answers to a question that has one. A
counter read from a filesystem that each caller sees a different version of is not a counter. This
is not a lock file and not a wider glob: those are the same read-then-write with a wider read.

**The series a prefix belongs to.** An id is `[<series>-]<type>-<number>`, and the counter is the
**series**', never the type's: `FAC-FEAT`, `FAC-FIX` and `FAC-REF` draw from one sequence, and
`FEAT`, `FIX` and `REF` from another. A counter per type hands out a number another type has already
spent — `FAC-FEAT-191` when `SRV-FIX-191` is a document on disk — which is the same id collision one
column to the left. `series` is on the response so the caller can see which sequence it was served
from, and it is `""` for a prefix that is only a type.

**The high-water mark spans every declared repository, and every ref.** `repos` is a set (E2), and the mark is the
highest number any of them holds for the series — not the highest in whichever repository the caller
happens to be working in. A monorepo whose two documents trees disagree about their highest id has
exactly one next id, and it is one more than the larger.

**Reservation is not creation.** The tool returns an id and records it spent; whether a document is
ever written with it is the caller's business, and a crashed caller's id is *not* reissued. An id
nobody used is a gap in the sequence, and a gap is free — reissuing what an agent never wrote is how
the collision comes back on the recovery path. For the same reason this write is **not** idempotent
by `request_id`: a retried call spends a second id, which costs a gap, and the alternative costs a
duplicate.

**What it does not do.** It never writes, renames or reads meaning into a ticket document — `catalog`
reads the documents and this endpoint reduces their ids to one number (§10). The block reservation a
person writes ahead of time for a planned set of children is a different question with a different
answer, and it stays a document a human writes.

#### E27 · GET `/api/wiki`
- **Request** — none. Filtering a handful of hand-written pages is the client's, and the whole set
  is one answer (§4.15).
- **Response** — `200 {"sections":[{id,title,path,bytes,front:[{label,value}],body_markdown,parse_error?}],"total"}`.
  `id` is the document's filename without its extension — the repository's own word, and the only
  name this surface has for a page. `title` is the frontmatter's `title` and falls back to the
  filename when a document has none. `front` is **every other frontmatter key**, in the order the
  document wrote them, values passed through untouched: the tool renders them as labels and knows
  none of them, exactly as `display.ticket_fields` reaches `E5`'s `extras`. Order is the frontmatter's
  own ordering key where the repository's documents carry one, and filename order where they do not —
  read from the documents, never declared in the manifest, because a repository that renumbers its
  wiki should not also have to edit `.factory/factory.json`.
- **Missing** — `docs.wiki` **not declared**: `404 not_found` with `detail.key:"docs.wiki"` and no
  `detail.path`, because there is no path to name — the answer is a line to add to the manifest, and
  that is what the refusal says. Declared and the directory absent: `200` with an empty list and
  `warnings:[{code:"path_missing",path}]`, the same shape `E3` uses, because a missing directory is a
  manifest mistake and not an error state. A document whose frontmatter will not parse: it stays in
  the list with `parse_error` set and `body_markdown` intact — one unreadable file may not blank a
  screen (§5).
- **Reads every declared repository**, in declaration order, and a section's `id` is prefixed by its
  repository's name where two of them hold a document with the same filename. `E5`'s
  `409 ambiguous_ticket_id` is not the right answer here: a ticket id is a name the repository chose
  to be unique and a wiki filename is not, so two repositories each writing `ports.md` is ordinary and
  refusing to serve either would be the tool inventing a constraint.

#### E28 · GET `/api/escapes`
- **Request** — none.
- **Response** — `200 {"columns":[…],"rows":[{cells:[{column,text,ticket_id?}]}],"total","path"}`.
  `columns` is the header row of the **first markdown table** in the file, verbatim; `rows` are its
  rows in the file's order, one `cells` entry per column. `ticket_id` is set on a cell whose text
  contains an id the catalog resolves, so the client can link it and does not have to parse
  anything; a cell naming an id nobody wrote carries text and no `ticket_id`. Nothing is sorted,
  ranked, dated or scored — the file's order is the repository's statement about its own history.
- **Missing** — `docs.escapes` **not declared**: `404 not_found` with `detail.key:"docs.escapes"`,
  as `E27`. Declared and the file absent: `200` with empty `columns` and `rows` and
  `warnings:[{code:"path_missing",path}]`. Present with no markdown table in it: `200`, empty
  `columns` and `rows`, and no warning — a ledger with no rows yet is a repository that has not been
  caught out, which is a state and not a mistake.
- **Reads every declared repository.** Each declares its own ledger and each ledger is its own
  surface section, in declaration order: one repository's misses are not another's, and merging two
  tables whose columns the tool does not understand would be the tool deciding they mean the same
  thing.

#### E29 · GET `/api/tickets`
- **Request** — `?q=<substring, optional>&provenance=all|done|waiting|unshipped|not_started`
  (default `all`) `&stage=<name, optional>&page=<int, default 1>&page_size=<int, default 50, max 200>`.
  `q` matches id, title, owner and lane, case-insensitively. `provenance` takes `delivery.state`'s
  own values (§3.5) and `stage` a name from the sequence the ticket declares — two axes, never
  collapsed into one status parameter (§3.2).
- **Response** — `200 {"groups":[{parent,title,count,tickets:[{id,title,workspace,status,stage:{index,total,round?},lane,delivery:{…},evidence,roles:[…],owner:{name,source:"commits"|"document",value?},extras:[{label,value}],cost_usd}]}],"total","from","to","page","pages"}`.
  Groups are the requirements the tickets name as `parent`, in `E3`'s order, with the synthetic
  `standalone` group last and holding exactly what `E4`'s does. **`total`, `from` and `to` are
  members of this answer and are what the count line renders**: a screen that computed its own
  totals from the page it received would say `showing 1–12 of 12` on a repository with three hundred
  tickets, and every assertion about the rows would pass (`WEB-FIX-091`).
  `owner.source` is `commits` when the name came from the ticket branch's authors — `rev-list`, one
  of §3.5's six read-only subcommands — and `document` when there is no branch and the value is a
  field `display.ticket_fields` named, passed through untouched. **A ticket with neither has no
  owner**, and the member is absent rather than an empty string: *nobody has touched this* and
  *somebody wrote a blank* are different facts.
- **Reads product repositories only** — it is `E3`'s list one level down, and the two narrow
  together or the ticket table answers about the tool while the requirement list answers about the
  product (`SRV-FIX-199`). A tool repository's ticket stays addressable at `E5` and claimable at
  `E12`, unchanged.
- **Missing** — the `docs.tickets` directories absent: `200` with an empty list and
  `warnings:[{code:"path_missing",path}]`. A `page` beyond the last: `200` with an empty `tickets`
  in every group, `page` echoing what was asked and `pages` saying what exists — never a `404`,
  because a stale page number in a URL is not a missing thing. `page_size` above the maximum is
  clamped and the response says so with `warnings:[{code:"clamped",field:"page_size"}]`; refusing
  would be a `400` a person cannot act on from a link somebody sent them.
- **Manifest invalid** — `200`, an empty list, and `E3`'s `warnings:[{code:"invalid_manifest",…}]`
  verbatim, for `E3`'s reason.

#### E30 · GET `/api/home`
- **Request** — none. Every panel of §4.18 comes out of this one read, at one instant.
- **Response** — `200 {"delivery":[{state,count}],"needs_you":{"total","rows":[{ticket_id,title,clauses:[…],detail,remedy:{…}|null}]},"unpushed":{"total","rows":[{ticket_id,title,branch,commits,age_s}]},"lanes":{"occupied","total"},"tickets":[{ticket_id,title,lane,role,state:{process,motion,evidence},delivery:{…},status,verdict,escaped}]}`.
  `delivery` is one entry per §3.5 value **in §3.5's table order, including the values whose count is
  zero** — a row of counts that omits its empty members is a row whose shape changes under the
  reader. `needs_you.rows` carry §3.3's clauses by name, so the screen says *why* rather than
  drawing an alarm; `remedy` is §3.6's object or `null`, and a `null` here is ordinary — most of
  §3.3's clauses end when a person reads something, not when the tool acts.
  `needs_you.total` counts tickets and not clauses: a ticket satisfying three of them is one row and
  one unit of somebody's attention (§3.3).
- **`tickets` is the axes, one row per ticket in the catalogue, and never the cell they flatten to.**
  §3.2b's nine cells are computed from it in the client's one module; no member of this row carries
  those ten values, and a case asserts that over the body so a later commit that helpfully serves
  the cell turns red. `status` is the document's claim and `delivery` is what git says — both, as
  §4.17's row carries both. `verdict` and `escaped` are read where §3.5b says and nowhere else: a
  verdict document beside the ticket's report **and** a `pass`/`fail` claim, or `null`; a row of the
  declared ledger naming this ticket, or `false` for every ticket when no ledger is declared.
- **Nothing here writes.** `unpushed` names branches and offers no push: §3.5's read-only property is
  six git subcommands, served at `E1` so it can be asserted, and this endpoint adds no seventh
  (§4.18).
- **Reads product repositories only**, as `E3` and `E29` do, and for the same reason: a first screen
  that counted the tool's own tickets among the product's would answer a question nobody asked
  (`SRV-FIX-199`).
- **Missing** — a repository with nothing in it: `200`, every `count` zero, both row lists empty. The
  `docs` directories absent: the same, with `warnings:[{code:"path_missing",path}]` per path.
- **Manifest invalid** — `200`, zeros, empty lists and `E3`'s `invalid_manifest` warning verbatim. A
  first screen that was blank for two different reasons and said which for neither is `SRV-FIX-071`
  on the surface a person opens first.

#### E31 · GET `/api/runs`
- **Request** — `?limit=<int, default 100, max 500>&before=<run_id, optional>` for the page after a
  given row. Newest first, always.
- **Response** — `200 {"runs":[{run_id,n,kind,ticket_id,ticket_title,lane,stage,outcome,started_iso,ended_iso?,duration_s,cost_usd,session_id}],"total","committed":false}`.
  Every field is `E7`'s row plus the three a repository-wide list needs and a per-ticket one does
  not — `ticket_id`, `ticket_title` and `kind`. `kind` is the role the run was started for
  (`roles.<name>`, F31) and `run` for one nobody declared a role for: the manifest's word, never the
  tool's. `committed:false` is served rather than described, so §4.19's line about this being one
  machine's history is a fact the client reads instead of a sentence somebody typed into a component.
- **This is `E7` without the ticket filter, and deliberately the same row.** Two shapes for one
  record is how a list and a detail come to disagree about an outcome, and `E7` stays the per-ticket
  slice rather than being replaced by `?ticket_id=` — §4.10 asks *what has run on this ticket* and
  this asks *what has run*, and a surface should not have to pass a filter to ask the question it was
  built for.
- **Missing** — `.factory/runs/` absent or empty: `200` with an empty list and `total:0`. History is
  expendable (§2) and its absence is a state, not an error. A `before` naming a run the tool does not
  have: `400 bad_request` with `detail.param` and `detail.before` — an unknown cursor is a value
  this endpoint cannot honour and not a resource that is missing, so the refusal is about the
  parameter and `404` stays `E8`'s answer, where the path itself names the absent thing. Answering
  it with the first page would silently restart the list, and answering it with `404` would make
  *this repository has no runs at all* and *your cursor is stale* indistinguishable to a client
  that reads status codes.

#### E32 · GET `/api/metrics`
- **Request** — `?days=<int, default 30>`, the window every number below is taken over. An
  integer in `1..3650`; anything else is `400 bad_window` with `detail.param:"days"` and
  `detail.days` echoing what was sent.
- **Response** — `200 {"window_days","runs","tickets_with_verdict","first_pass_qa_rate",
  "rounds_per_ticket":{"median","mean"},"stage_minutes":[{"name","median","mean","n"}],
  "active_minutes":{"median","mean","n"},"spec_to_land_hours":{"median","n"},
  "escapes_in_window","rework_after_build":{"tickets","share"}}`.
  Computed when asked, from what the tool already holds — the run records under `runs_dir`
  (§2), the documents `E29` reads, the ledger `E28` reads and the base branch's commits §3.5
  already walks. **Nothing is indexed, nothing is cached and nothing is written** (`S2`).
  - `runs` counts every run record whose `started_iso` is inside the window.
  - `tickets_with_verdict` counts the tickets a verdict was RUN for — those with at least one
    role run (`roles.<name>`, F31) inside the window — and it is `first_pass_qa_rate`'s own
    denominator, served beside it so a surface cannot draw the rate over some other
    collection's count (`WEB-FIX-091`).
  - `first_pass_qa_rate` is the share of those tickets whose FIRST role run was their only one
    **and** whose §3.5b verdict pair says `pass`. A ticket whose role run left no verdict
    document did not pass first time; it is in the denominator and not in the numerator,
    because *nobody wrote the verdict down* is an outcome and not an absence of one.
  - `rounds_per_ticket` reduces the runs nobody declared a role for — the rounds of work —
    per ticket that has one in the window.
  - `stage_minutes` is one row per stage a run REPORTED (`E22`), the minutes between that
    frame and the next stage frame of the same run, or that run's last working entry for the
    last one. The names are the repository's own — a ticket's `stages:` line or the manifest's
    (`S1`) — and a stage nobody reported has no row.
  - `active_minutes` is the run's working time: the sum of its `turn` durations, never wall
    clock, because a session keeps its seat after its turn ends.
  - `spec_to_land_hours` runs from the first frame naming the FIRST stage of that ticket's own
    sequence to the commit that landed it on the base branch — the commit whose subject names
    the ticket and which touches something the manifest did not declare as documents. A ticket
    with no landing is not in `n`.
  - `escapes_in_window` counts rows of the first markdown table of `docs.escapes` whose date
    cell — the first cell that reads as one — falls inside the window. Which column that is
    stays the repository's business (§3.5b): the tool looks for a date, not for a heading.
  - `rework_after_build` counts the tickets whose own document was committed to the base branch
    AFTER their first run in the window started, over the same tickets `rounds_per_ticket`
    reduces.
- **Every rate is `null` when its denominator is zero, and a number when it is not.** `0`
  means *measured, and none qualified*; `null` means *nothing was measured*. They are different
  facts about a repository and a screen that renders both as `0%` reports a failing process
  over a repository that has simply not started. Counts stay `0`, because a count of nothing is
  nothing.
- **Computed on an async worker, never on an io thread.** The walk reads every run's
  `transcript.jsonl` and asks git for the base branch's commits; a handler thread that did
  that would stall the three others queued behind it (§2, `E31`'s neighbour and `SRV-FIX-108`).
- **Missing** — no run records at all: `200`, `runs: 0`, every count `0` and every rate `null`.
  History is expendable (§2) and its absence is a state, exactly as `E31` answers it. No
  `docs.escapes` declared: `escapes_in_window: 0` and no refusal — `E28`'s `404` is about a
  surface with nothing to draw, and a metric with no ledger behind it is a zero and not a dead
  end. Not a git repository: `spec_to_land_hours.n: 0` and `rework_after_build.tickets: 0`,
  with every other number unchanged — §3.5's *"I cannot tell you"* is per axis and never blanks
  the answer.

#### E33 · POST `/api/lanes/{lane}/routing`
- **Request** — `{"request_id":"build-gate-1","steps":[{"which":"build","accepted_exit_codes":[0],"reuse":true},{"which":"gate","accepted_exit_codes":[0],"require_decision":true}]}`.
  A request ID is 1–64 ASCII letters, digits, underscores or hyphens. There are 1–16 steps;
  exit codes are unique integers in 0–255. Optional booleans default false. Duplicate members,
  unknown keys and invalid fields are refused before effects. Commands must exist in the selected
  workspace's manifest; stages, roles and provider choices are never inferred.
- **Response** — `202` for a new job, `200` for the identical retained request, with
  `{"request_id","lane","ticket_id","source","state","action","reason","next_index","receipts","checkpoint"}`.
  `state` is `running`, `completed`, `needs_main` or `interrupted`; `action` is `wait`, `main` or
  `advisor`. Each terminal receipt binds the exact command, accepted exit policy, Git-visible
  source identity and complete stdout/stderr capture. Code advances only after a persisted terminal
  result with an accepted exit and unchanged inputs. Opt-in reuse also verifies the retained capture
  digest and length. Ignored outputs and external environments are outside that identity.
  Unexpected exit, source drift or an explicit decision stops before the next command. Two
  consecutive actual failures with identical source, command, exit and capture expose one stable
  `repeated_error` checkpoint. It asks for advice; it does not launch an advisor. Completion means
  `action:main`, `reason:commands_completed`, never ticket acceptance. Restart does not replay an
  uncertain dispatch. E19/M10 retain their existing confinement, resource and streaming behavior.
- **Missing** — unknown lane: `404 not_found`; malformed request: `400 bad_request`; unknown command:
  `400 bad_which`; undeclared command: `404 not_declared`. Same ID with another plan:
  `409 routing_conflict`; another active plan: `409 routing_busy`; external process:
  `409 wrong_state`; queued resource: `409 resource_busy`; unreadable or damaged evidence:
  `409 routing_unresolved`. No refusal ends another process or rewrites acceptance documents.

#### E34 · GET `/api/lanes/{lane}/routing/{request_id}`
- **Request** — optional `?include_output=true`; any other supplied value is `400 bad_request`.
- **Response** — `200` with E33's retained job shape, without dispatching a command. Receipts carry
  `index`, `which`, `command`, `command_id`, `source`, `source_after`, `exit_code`, `capture_path`,
  `capture_sha256`, `bytes`, `accepted`, `reused` and `complete`. The explicit output option adds
  `output_base64` containing every captured byte, including an empty complete capture. Changed
  inputs or corrupt evidence cannot be returned as valid reusable completion.
  If the terminal source scan was unavailable, only the final nonreused receipt of
  `needs_main/main/source_unresolved` carries an empty `source_after`, meaning unknown inputs.
  After current inputs are repaired, main can inspect its complete capture and submit a new unique
  plan, including after restart. Unknown terminal inputs never certify completion or reuse.
- **Missing** — unknown lane or request: `404 not_found`; unreadable or contradictory retained
  evidence: `409 routing_unresolved`. Runtime journals live under the manifest-resolved routing
  directory; the endpoint writes no ticket, report, verdict, provider configuration or Git ref.

---

## 6. Socket

One socket, `/ws`. It carries everything live; **the client never polls, and nothing is rebuilt on a
timer.** The prototype's polling loop ate half-typed messages, and this is the mechanism that makes
that mistake hard rather than merely discouraged.

### 6.1 Framing

Every frame is `{"m":"<name>","ts":"<iso>","seq":<int|null>,…payload}`. `seq` is per-run and
monotonic on `transcript` frames only; every other message is state and carries no sequence.

### 6.2 Client → server

| # | Message | Payload | Emitted when |
|---|---|---|---|
| M14 | `subscribe` | `{scope:{lane?,ticket?,requirements?:true}}` | a surface mounts. The server replies `M1 snapshot` for that scope and then sends deltas for it. |
| M15 | `resume` | `{lane,after_seq}` | the socket reconnects and the client already holds part of a run's transcript. The server replays from the run log, then goes live; if the log cannot cover the gap it sends `M1` instead and the client discards its tail. |
| M16 | `ping` | `{}` | every 10 s of client idleness, so a dead connection is detected in 15 s rather than at the next user action. The server answers `M1`-less with a bare `pong` frame. |

**`M14` and `M15` are answered in two different shapes, and that is deliberate.** A `subscribe` on a
lane — and a `resume` the log cannot cover — is answered with `M1`, whose `data` is `E8`'s body:
`entries` are **entries**, `{seq,ts,kind,…payload}`, the same values `GET /api/runs/{id}/transcript`
returns. A `resume` the log *can* cover is answered with the **`M5` frames themselves**, `m` and all,
because the client applies them in order through the same path a live entry takes.

The sequence log therefore holds both, rendered together from one call and stored side by side.
Neither is derived from the other at read time: recovering an entry by parsing a frame back apart
gives the two shapes a second place to disagree, in the one structure whose invariant is that it
holds exactly what goes out. `SRV-FIX-049` is the bill for having held only the frame — the snapshot
poured frames into `entries`, the client drew each one as nothing (§4.6: an undeclared `kind` renders
nothing, deliberately), and the transcript emptied itself every time the tab was left and re-entered.
Every socket case subscribed and then listened, so every one of them read the live path and none read
the other. A case that reads one value **by both paths and compares them** is what catches this class,
and `test_the_snapshot_and_the_live_frames_describe_the_same_entry` is that case.

### 6.3 Server → client

| # | Message | Payload | Emitted when |
|---|---|---|---|
| M1 | `snapshot` | the full state for the subscribed scope — same shapes as E3/E4/E5/E9 | immediately after `subscribe` or an unresumable `resume`, and after the server restarts a subscription it could not keep. It is the only frame that replaces state; every other frame patches. |
| M2 | `lane.state` | `{lane,ticket_id,state:{process,motion,evidence,blocked?},delivery:{…},waiting_for?,needs_you}` | any axis of a lane's state changes: process transition, silence crossing 120 s, silence ending, evidence changing when a report appears, a resource being granted — **and any change to `delivery` (§3.5)**, which is a fact about a ticket rather than about a lane. A ticket with no lane still has one: that frame carries `lane:null` and `ticket_id`, and the client patches the row by id. One message per change, not a periodic tick, which is what keeps the client's no-polling rule true for a state the tool learns by looking at the world. |
| M3 | `stage` | `{lane,ticket_id,stage:{index,total,name,round,of,source},progress_pct,remaining_s,regressed_from?}` | `E22` accepts a stage report, or a lane's ticket document changes on disk — its sequence or its `status:` — once per lane the ticket holds (`SRV-FIX-599`). `progress_pct` and `remaining_s` are E9's, from the same function. `regressed_from` is set when the reported stage is earlier in the sequence than the last one. |
| M4 | `lane.metrics` | `{lane,elapsed_s,cost_usd,remaining_s,turn:{n,cost_usd}}` | a turn ends, and when the agent reports usage mid-turn. Never on a timer: the clocks tick client-side from `since` timestamps (§4.4), so a quiet lane produces no traffic at all. |
| M5 | `transcript` | `{lane,run_id,seq,entry:{kind,…}}` — kinds per §4.6 | one frame per entry, as the entry is parsed out of the agent's output. This is the only high-rate message, and it is append-only: an entry is never modified after it is sent, except a `tool` entry gaining its result, which is sent as its own frame with the same `seq` and `patch:true`. |
| M6 | `permission.request` | `{lane,request_id,tool,input_summary,rule_that_would_be_written,nearest_miss?:{rule_id,pattern,why_it_did_not_match},expires_at:null}` | the agent asks for permission. `expires_at` is null on purpose: a request never times out, because a request that times out into a denial produces a run that failed for a reason nobody saw. |
| M7 | `permission.resolved` | `{lane,request_id,decision:"once"\|"always"\|"deny",rule_id?}` | a person answers at `E23`, or the request is voided because the run died. Sent to every client, so a request answered in one tab stops waiting in the other. |
| M8 | `resource` | `{name,held_by,queue:[…]}` | an exclusive resource is claimed, released, or its queue changes. This is how a `queued` lane learns it is next. |
| M9 | `isolation` | `{lane,operation,path,refused:true}` | the lane's confinement refuses an access. Always also appended to the transcript (§4.6) — a refusal that is only a log line is a refusal nobody sees, which is the failure `SRV-FEAT-002` exists to fix. |
| M10 | `command` | `{lane,command_id,which,chunk?,exit_code?}` | output arrives from an `E19` command, and once more when it exits. `which` is one of the three names E19 takes — `build`, `gate`, `review` — and the frame's shape does not change between them. |
| M11 | `run` | `{lane,run_id,n,outcome,started_iso,ended_iso?,stage,duration_s,cost_usd}` | a run starts, and when it reaches a terminal outcome. Drives the RUNS tab and the background-tab notification for a crash. **A run a declared role started (F31) is one of these like any other** — the tool starts it, so the tool announces it, and this frame is how the person is told that a check they did not launch is now running. There is no separate message for it: a second frame shape would be the tool having an opinion about what a role is (§10). |
| M12 | `rules` | `{policy,rules:[…]}` | a rule is granted or revoked, or the policy changes. Sent to every client so a second tab cannot show a policy that is no longer in force. |
| M13 | `catalog.changed` | `{kind:"epic"\|"ticket"\|"report",id,path,change:"created"\|"modified"\|"deleted"}` | a watched document changes on disk — from an editor, a git operation, or an agent in a lane. The client refetches exactly that document. This is what makes "the documents are the database" true rather than aspirational: an agent writing its report makes the report tab fill in, with no polling and no cache to invalidate. |
| M17 | `lane.membership` | `{lane,ticket_id,workspace,change:"claimed"\|"released"}` | the lane **set** changes — `E12` claims one, `E13` releases one, or the tool claims one for itself (F31). Every other lane frame patches a row that already exists; this is the only one that says a row appeared or went away. The client re-reads `E9` on `claimed` and drops the row on `released` — the same event-then-read shape as `M13`, and for the same reason: the frame names what moved, and the receiver reads exactly that. |
| M18 | `lane.words` | `{lane,ticket_id,last_words:{kind,text,at}}` | the agent said something or called something, and this is that one line. **Never `null`** — a frame is sent only when there are words, and the `null` a new run starts with reaches the client through the `E9` re-read `M17 claimed` already triggers and through `M2`'s process transition to `running`. **Coalesced per lane to at most one frame per 2 s, trailing value wins**, because an agent emits several entries a second at peak and a person reads the line once every few seconds — a debounce armed by an entry, never a periodic tick. Routed to the same scopes `M2` reaches, so the lanes surface (§4.14), which subscribes to `requirements`, hears it. Not a flag on `M2`: `M2` fires when an axis of a lane's STATE changes, and a frame carrying `state`, `delivery` and `waiting_for` on every sentence the agent writes is the wrong shape for the rate. |

Eighteen messages. Three client→server, fifteen server→client, and the bare `pong` frame, which
carries no payload and is not state.

**Why `M17` exists rather than a flag on `M2`** (SRV-FIX-080). `M2` is *a state a surface renders has
changed*, and a released lane has no state to render — a tombstone inside it would make "two shapes,
one message" into three, and a client would have to tell a patch from a removal by the absence of a
field. Membership is a different fact from state and it gets its own word.

**Without it the tool cannot show its own work.** Until `SRV-FIX-080` every publisher was an edge on
a lane that already existed, so a lane claimed while a page was open reached it only on a reload —
and `F31`'s whole point is that the tool claims a lane **nobody asked for**, on a screen somebody is
already watching. `E12`'s `201` is a lane row for the browser that POSTed it and for nobody else.

---

## 7. Features and their acceptance criteria

Every criterion is something that can be run: a named test case, or a request and the response it
must produce. Server cases live in `factory/server/tests/`, client cases in `factory/client/web/tests/`.
The seven features the design does not draw are marked **[§1-N]** with their number in the ticket.

**F1 · Point the tool at a repository.** It loads `.factory/factory.json`, validates it, refuses an
unknown major, and serves that repository and no other.
*Criterion* — `test_manifest.py::test_unknown_major_refused`: a manifest with `"factory":"9.0"` makes
`GET /api/manifest` return `409 unsupported_manifest_major` naming both versions, and no other
endpoint serves data.

**F2 · Requirements list, searched and filtered.**
*Criterion* — `test_catalog.py::test_requirements_filter`: three requirement documents on disk, one
with a lane needing a person; `GET /api/requirements?filter=needs` returns exactly that one, and
`?q=` matching a title returns exactly that one.

**F3 · Requirement detail, grouped by workspace, each group with its own stage sequence.**
*Criterion* — `test_catalog.py::test_requirement_groups`: tickets in two workspaces with different
declared sequences come back in two groups, each carrying its own `stage_sequence` and its own
`total`; a ticket naming an undeclared workspace lands in `unassigned`.

**F4 · The ticket document, read-only, with the repository's own extra fields.**
*Criterion* — `test_catalog.py::test_ticket_extras`: with `display.ticket_fields:["alpha","beta"]`,
`GET /api/tickets/{id}` returns `extras` as those two labels with their frontmatter values in that
order, and a third frontmatter field not named there is absent.

**F5 · The delivery report, and its absence.**
*Criterion* — `test_catalog.py::test_report_absent_then_present`: `GET …/report` is `404` with
`detail.path`; the file is written; `M13 catalog.changed` arrives and the next GET is `200` with an
`evidence` verdict.

**F6 · Run history that outlives the run.**
*Criterion* — `test_runs.py::test_history_survives_restart`: two runs, tool process restarted,
`GET /api/tickets/{id}/runs` still lists both with their outcomes and costs.

**F7 · Claim a lane, release a lane.**
*Criterion* — `test_lane.py::test_claim_creates_worktree_and_branch`: `POST /api/lanes` yields a
worktree on branch `ticket/<id>`; `DELETE` releases the seat, keeps the branch, and reports whether
the worktree was removed.

**F8 · Exclusive resources queue instead of colliding.**
*Criterion* — `test_lane.py::test_second_lane_queues_on_resource`: two lanes needing one exclusive
resource; the second is created `queued` with `waiting_for` set, `POST …/runs` on it is `409
resource_busy`, and releasing the first produces `M8 resource` followed by that lane becoming
startable.

**F9 · A refused access is visible, not silent.** Enforcement is `SRV-FEAT-002`; this specifies the
surface.
*Criterion* — `test_isolation.py::test_refusal_is_reported`: an agent attempting to write outside
its worktree produces `M9 isolation` and an `isolation_refusal` entry in the transcript naming the
path, and the file outside the lane is unchanged.

**F10 · Start a run.**
*Criterion* — `test_agent.py::test_run_starts_with_env`: `POST …/runs` returns a `run_id`, and the
spawned process's environment carries `FACTORY_RUN_ID`, `FACTORY_PROGRESS_URL` and
`FACTORY_PROGRESS_TOKEN`, with `cwd` inside the lane's worktree.

**F11 · The transcript is live, and nothing polls.**
*Criterion* — `test_ws.py::test_transcript_streams_in_order`: entries arrive as `M5` frames with
contiguous `seq`, within 200 ms of the agent producing them; and
`client/web/tests/transcript.test.tsx::does_not_poll` asserts no timer-driven fetch exists — a rendered
transcript with a half-typed draft survives 100 arriving entries with the draft byte-identical.

**F12 · Tool calls collapse and expand.**
*Criterion* — `client/web/tests/transcript.test.tsx::tool_expands`: a `tool` entry renders one line with
tool, argument and duration; clicking reveals `INPUT` and `RESULT`; the expansion survives 20 further
entries arriving.

**F13 · [§1-3] File edits in the transcript, with a diff.**
*Criterion* — `client/web/tests/transcript.test.tsx::file_edit_line`: a `file_edit` entry renders as
`Edit  <path> · +18 −6` collapsed, and expands to the unified diff — not to `INPUT`/`RESULT`. Server
side, `test_transcript.py::test_edit_carries_counts` asserts `path`, `added`, `removed` and `diff` are
parsed from the agent's output rather than recomputed from disk, so the entry describes the edit that
happened and not the file's later state.

**F14 · Send, hold, interrupt — in order, nothing dropped.**
*Criterion* — `test_agent.py::test_held_then_interrupt_delivers_in_order`: two `POST …/messages`
during a turn both return `status:"held"`; `POST …/interrupt` returns `delivered:2` and the agent's
stdin receives both, in the order they were sent, after the interrupt.

**F15 · Stop the turn without losing what ran.**
*Criterion* — `test_agent.py::test_stop_keeps_disk_state`: a turn that has already edited a file is
interrupted; the edit is still on disk, the session is still alive, a `stopped` entry names the turn
and its cost, and the next message continues the same session.

**F16 · [§1-5] End a session cleanly; kill a wedged one.**
*Criterion* — `test_agent.py::test_end_lets_turn_finish`: `POST …/end` during a turn lets the turn
complete, then reaps; outcome `ended cleanly`; the session id is retained.
`test_agent.py::test_kill_wedged_process`: a child that ignores `SIGTERM` and reads no stdin is gone
within `grace_s + 1`, the whole process group with it, outcome `killed by you`; and `POST …/kill` on a
lane with no process returns `200 {"killing":false}`.

**F17 · Three axes, never flattened, and one counter.**
*Criterion* — `test_state.py::test_axes_independent`: a lane that is `running` and silent for 130 s
and has a report claiming done reports all three axes independently, and `needs_you` is true for both
reasons; `client/web/tests/chips.test.tsx::three_chips` asserts three chips render, never one merged
status.

**F18 · [§1-2] Blocked on a person is its own state.**
*Criterion* — `test_state.py::test_blocked_distinct_from_silent`: a lane awaiting a permission answer
reports `process:"blocked"`, `blocked.reason:"permission"`, and `motion` unchanged; it counts toward
`needs_you`; and after 130 s it is still `blocked`, never reclassified as `running · silent` — a
correct agent waiting on a person is not a stalled one, and the two must not share a colour.

**F19 · Silence is detected and counted.**
*Criterion* — `test_state.py::test_silence_threshold`: no output for 119 s leaves `motion:"producing"`;
at 121 s exactly one `M2` frame reports `silent` with `since`; and no further frames are sent while it
stays silent. `client/web/tests/lane-card.test.tsx::counter_ticks_locally` asserts the displayed counter
advances with no socket traffic.

**F20 · [§1-4] Stages, rounds, and going backwards.**
*Criterion* — `test_progress.py::test_round_and_regression`: `POST /api/progress` reporting `GATE`
then `IMPL` then `GATE` yields `M3` frames whose third carries `round:2` and whose second carries
`regressed_from:"GATE"`; `GET /api/tickets/{id}` reports `stage.round:2`. The tool stores what was
reported and enforces no cap — a retry limit belongs to the repository's process, not to this tool
(§10), and one that reports `round 2 of 4` gets that caption because it said so.

**F21 · Run the workspace's declared build, gate or review command.**
*Criteria* —
- `test_lane_command.py::test_gate_command_streams`: `POST …/commands {"which":"gate"}` runs the
  exact string the manifest declares, in the lane's worktree, streams `M10` chunks, and finishes
  with the real exit code; a workspace declaring no `gate` returns `404 not_declared` naming the
  field.
- `test_lane_command.py::test_review_runs_and_replays_like_build`: each of the three names starts
  the string ITS OWN key declares, publishes `M10` on the lane scope naming that key, and a client
  that goes away and comes back mid-command receives the rest of it and the exit code. The names
  are read off the endpoint's own refusal and cross-checked against `manifest.schema.json`, so a
  fourth command joins the sweep the day it is declared (`SRV-FEAT-500`).

**F22 · [§1-1] Permissions: policy, request, and the list of what was granted.** Three parts, and
none of them works alone.
*Criteria* —
- `test_permissions.py::test_request_names_the_rule`: an out-of-policy tool use produces `M6` carrying
  `rule_that_would_be_written`, and when an existing rule nearly matched, `nearest_miss` with the rule
  and why it did not match. This is the one that has already cost a run: an agent waited on an
  approval nobody could see, for a command a rule was supposed to cover and did not.
- `test_permissions.py::test_allow_always_writes_one_readable_rule`: `once` unblocks and writes
  nothing; `always` unblocks and appends exactly one rule to `.factory/permissions.json`; `deny`
  returns the denial to the agent and the run continues.
- `test_permissions.py::test_granted_rules_are_listable_and_revocable`: `GET /api/permissions/rules`
  lists it with `source:"granted"` and a match count; `DELETE` removes it and the next matching
  request asks again, inside the same live run.
- `test_permissions.py::test_manifest_rule_not_revocable`: a rule from `agent.allowed_tools` lists as
  `source:"manifest"`, `revocable:false`, and `DELETE` is `409 not_revocable`.
- `test_permissions.py::test_corrupt_rules_file_fails_closed`: an unparseable
  `.factory/permissions.json` yields an empty rule set and asks about everything.
- `client/web/tests/permissions.test.tsx::policy_names_its_consequence`: the three policy options render
  with one line of consequence each, and changing the policy states that it applies to lanes started
  afterwards.

**F23 · [§1-6] Review the lane's diff.**
*Criterion* — `test_lane.py::test_diff_lists_files_and_commits`: after an agent edits two files and
commits once, `GET …/diff` returns both files with `+n/−n` and the commit with its subject and sha;
`?path=` returns that file's patch. `client/web/tests/diff-tab.test.tsx::diff_tab_renders` asserts the
files, the totals and the commits are all reachable without leaving the ticket screen — deciding
whether to keep the work is the point of the exercise, and it cannot be done from a transcript.

**F24 · [§1-7] Reach a person whose tab is in the background.**
*Criterion* — `client/web/tests/notify.test.tsx::three_events_and_no_more`: a permission request, a lane
going idle, and a crash each raise a notification and each set the title prefix; a stage completing, a
run finishing normally, and a silence crossing its threshold raise none. Stage completions are the
frequent event, and a channel that fires on frequent events is a channel that gets muted.

**F25 · Socket loss is survivable and honest.**
*Criterion* — `client/web/tests/socket.test.tsx::reconnect_resumes`: the socket drops; the reconnect
block appears; the draft and the transcript are untouched; on reconnect the client sends `resume` with
its last `seq` and receives only what it missed. `test_ws.py::test_resume_after_gap_falls_back` asserts
that when the log cannot cover the gap the server sends `snapshot` instead, and the client's tail is
replaced rather than merged.

**F26 · Theme.** Dark by default, light available, remembered. Both are in the design and both are
gated against its token values, per `client/web/design/README.md`.
*Criterion* — `client/web/tests/theme.test.tsx::tokens_match_design`: every token the client defines is
byte-equal to the value in `client/web/design/nocturne.css` or the design file's own `:root` block, in both
themes. A colour drifting is a red build, not a discussion.

**F27 · Cost and duration, attributed.**
*Criterion* — `test_metrics.py::test_cost_rolls_up`: two turns in one run and two runs on one ticket;
turn costs sum to the run, run costs sum to the ticket, and the numbers on the lane card, the ticket
header and the RUNS table are the same numbers from the same source.

**F28 · Claimed is not proven.**
*Criterion* — `test_evidence.py::test_unproven_claim_is_an_alarm`: a report that names no run id gives
`evidence:"claimed_unproven"`, sets `needs_you`, and renders with a border; the same report with the
run id present gives `claimed_proven` and no border.

**F29 · The documents are the state.**
*Criterion* — `test_catalog.py::test_no_cache_disagrees_with_disk`: a ticket document edited on disk
produces `M13`, and the next read returns the new content; no request returns a value the file does
not contain, and the tool holds no copy that can outlive it.

**F30 · The screen shows what happened, not what the document claims.** §3.5's `delivery`, derived
from git and the process table, on E3, E4, E5 and M2. Seven cases, each against a git repository the
test builds; the repository the tool is pointed at is never the one it is tested on.
*Criteria* —
- `test_delivery.py::test_a_document_claiming_pass_with_nothing_pushed_is_unshipped`: frontmatter
  says `pass`, the branch holds commits, no remote has them → `delivery.state:"unshipped"` with
  `detail` naming the commit count. This is the case that has happened twice.
- `test_delivery.py::test_a_launched_session_that_died_is_stalled`: `status: awaiting-qa`, no live
  process, nothing committed since → `stalled`.
- `test_delivery.py::test_a_merged_ticket_is_done_even_with_its_branch_deleted`: the branch is gone
  and the state is still `done` (§3.5 rule 2).
- `test_delivery.py::test_a_live_session_is_running_and_a_stray_process_is_not`: a process running
  the manifest's `agent.command` and naming the ticket is `running`; a build tool or dev server in
  that ticket's worktree is not.
- `test_delivery.py::test_the_derived_state_wins_and_the_disagreement_is_reported`: document `pass`,
  git `unshipped` → both are carried and `disagrees:true`.
- `test_delivery.py::test_needs_you_counts_stalled_and_unshipped`: §3.3's counter includes them, on
  the requirement row and on the requirement's own header.
- **Negative** — `test_delivery.py::test_a_repository_with_no_git_still_serves`: pointed at a
  directory that is not a git repository, E1 says `delivery.available:false` with a `reason`, E3, E4
  and E5 answer `200` with `delivery.state:"unknown"` and a `reason`, and **nothing answers 500**.
  Degrading to *I cannot tell you* rather than to a blank screen is the whole point: the screen is
  what the person came for.
- **Read-only** — `test_delivery.py::test_only_read_only_git_subcommands_are_used`: `E1`'s
  `delivery.git` is exactly `for-each-ref · ls-tree · rev-list · rev-parse · status · worktree`, and
  the running server's log shows no invocation outside that set.
- Client — `delivery-badge.test.tsx::the_row_shows_the_derived_state`: a ticket claiming `pass` and
  derived `unshipped` renders the `unshipped` badge and the disagreement, and never the word `pass`
  as its state. `delivery-badge.test.tsx::stalled_and_unshipped_carry_the_alarm_border`: and `done`
  does not.
- Browser gate — against a fixture repository holding one of each state, all five render, and the
  two rows that need a person are inside the viewport without scrolling.

**F31 · A declared role starts itself when its stage is reached.** The repository declares roles and
the tool starts them. It does not know what a role *is*: `start_on_stage` is a string it compares,
`prompt_file` is a path it reads, `lane` is the word `new`, and the role's name is passed through
untouched (§10). There is no built-in role, no default, and no name with a meaning. A repository
with no `roles` block behaves exactly as it did before this feature existed.

Why a **new lane** and not another run in the reporting one: a new lane is a new worktree, a new
branch and a new session, so the started role cannot inherit the transcript, the reasoning or the
framing of the lane that triggered it. That independence is the point of the feature, and making it
structural is better than asking for it.

**And the independence is from the SESSION, never from the WORK — a role lane is cut from the
branch the work is on** (E12). The role's worktree starts from `ticket/<ticket-id>`, the branch of
that ticket's `run` lane, when that branch exists, and from `HEAD` only when it does not; the role's
own branch is still its own, `ticket/<id>-<role>`, branched FROM the run branch rather than a second
checkout OF it, and a re-claim brings the work to it again rather than resuming where the last round
ended (`SRV-FIX-184`, `SRV-FIX-189`).

This sentence is here because its absence was load-bearing. For as long as this paragraph explained a
new lane purely as independence, *independence from the commits* read as the same virtue as
independence from the context — and `lane::git::addWorktree` cut every role lane from the main
checkout's `HEAD`. On 2026-08-21 a QA lane claimed for `SRV-FIX-174` carried three commits of a
concurrent session and **not the fix it existed to judge**; `git merge-base --is-ancestor f096c936
ticket/SRV-FIX-174-qa` answered NO (`docs/operations/ESCAPES.md`). A checker that cannot see the work re-runs
every criterion against unmodified code — failing correct work, or passing an invariant the tree
already held and letting `status: pass` be set from a branch the fix is not on. *QA is a different
session* had been implemented as *QA is a different tree*, and the difference was never that.

Why the prompt is a **file in the repository**: it is committed, diffable and reviewable, for the
same reason `.factory/permissions.json` is. The tool substitutes `{ticket_id}`, `{ticket_path}`,
`{repo_root}` and `{triggered_by_run}` — the four things it already knows — and reads no further.

**Two of the four are PATHS, and they are in the AGENT'S filesystem, not the tool's.** A lane binds
`.git`, its worktree metadata and its own worktree, and nothing of the main checkout — so
`{ticket_path}` is the ticket **under the lane's worktree** and `{repo_root}` **is** that worktree.
A path taken from the tool's own view of the repository names, for the only process that is ever
handed it, a file that does not exist. This is not a detail of the implementation: it is what the
value MEANS, and it is written here because a fifth placeholder will one day be added from this
paragraph. Every value the tool substitutes into a prompt is a statement made in the agent's world,
and whoever adds one owes the reader which world that is (`SRV-FIX-055`).

**And a role is not the only thing a `prompt_file` serves.** E18's `agent.start_prompt_file` is the
same mechanism for the start a *person* asks for: read the declared file, substitute those four, pass
the rest through byte for byte, refuse at load when it is not there. It is one implementation and one
placeholder set, deliberately — the day a start's prompt is built a second way is the day the two
disagree about what `{ticket_path}` means. `{triggered_by_run}` is empty for a person's start, because
nothing triggered it; a placeholder the tool does not define is still left exactly as the repository
wrote it, in both callers.

*Criteria* — `test_roles.py`:
- `test_a_declared_role_starts_on_its_stage`: a manifest declaring a role on stage `GATE`; a lane
  reports `GATE` at `E22`; a **second** lane appears, on its own branch, running the prompt from the
  declared file, and `M11` reports the run.
- `test_the_started_lane_cannot_see_the_triggering_one`: the started run's working directory is its
  own worktree, its session id differs from the triggering run's, and its transcript contains
  nothing from the lane that triggered it. This is the criterion the whole feature exists for.
- `test_the_prompt_is_the_repositorys_own_text`: the process is started with the file's contents,
  with only the four placeholders above substituted; a placeholder the tool does not define is left
  untouched rather than blanked.
- `test_a_role_starts_once_per_ticket_per_stage`: the same stage reported three times starts one
  run, not three. A retried stage is normal (F20 has rounds); a fleet of duplicate checkers is not.
- `test_no_roles_block_changes_nothing`: a manifest without `roles` behaves exactly as before, and
  reporting a stage starts no lane.
- `test_a_role_naming_a_missing_prompt_file_is_refused_at_load`: `E2` reports the manifest invalid
  with `detail.path` naming the file that is not there — at load, not at the moment the stage fires.
  A role that fails only when it is finally needed is a role that fails at the worst time.
- `test_no_seat_defers_rather_than_drops`: with every lane seat taken the role is `pending` on `E5`
  and starts when a seat frees. Dropping it silently would rebuild the exact failure this feature
  exists to fix, one layer down.
- **Negative** — `test_a_role_cannot_trigger_itself`: a role whose own run reports the stage that
  declares it does **not** start another. Left out, one misconfigured manifest spawns lanes until
  the seats run out, and the tool that was supposed to make the process reliable becomes the thing
  that takes the machine down.

**What the worst case costs.** Every automatic start is bounded by three independent guards, and
each is worth stating with what its absence costs: the **seat** (`lanes.host`) caps how many runs
can exist at once, so without the other two the ceiling is still the declared seat count and the
cost is a full factory rather than a full machine; the **once per ticket per stage per role** rule
caps how many a retried stage can produce, and without it a gate re-reported four times starts four
checkers of the same ticket; the **self-trigger refusal** is the one that bounds the loop, and
without it a role declared on a stage its own prompt reports is a cycle that stops only when the
seats are gone — the seat count is what turns that from unbounded into merely useless.

---

## 8. Where the design and this ticket disagree

Both were written by people who could not both be right about everything, and picking one silently is
how a specification becomes a source of arguments later. There are seven. In each case the visual
decision stays with the design and the behavioural decision stays with the ticket, which is the rule
that resolves most of them.

**D1 · The stop controls — the disagreement the ticket said would be here.**
The design is emphatic: *"One control, three jobs. Idle it says Send. Mid-turn it becomes Stop, in
the crash colour, exactly where your hand already is."* There is no end-the-session control and no
kill. The ticket required both: ending lets the current turn finish, and killing exists precisely for
a process too wedged to hear an interrupt.
**Resolution: the design wins on the whole question.** This paragraph once read *the ticket wins on
behaviour* and put `End session` and `Kill` in a small menu on the lane in the ticket header. **The
developer overturned that on 2026-07-29**, cutting the menu and the ticket that would have built it:
there is **one control**, it is the composer's, it turns from `Send` into a rectangle while the agent
produces, and pressing it again cancels the turn. No menu, no `End session`, no `Kill`, nowhere.
`E16` and `E17` remain endpoints (F16); what they lost is a standing control, because putting a kill
next to Send is how a session dies by accident.

**The amendment — ratified by the developer, 2026-08-21.** D1 decided *where a destructive verb may
not live*, and that is kept whole: a standing menu of destructive verbs beside a running agent stays
refused, and the composer remains the only control for a **turn**. What D1 did not decide is the gap
between *cancel this turn* and *destroy this lane*. The composer's control is `E15 interrupt`, and
the composer is disabled when no live session exists — so a session that is idle-but-unusable has no
control at all, and a lane sat in exactly that gap for thirteen hours (`FAC-EPIC-006`). D1 gains one
clause:

> A remedy is offered **by the failure that needs it, in the place the failure is shown, and never
> as a standing control.** A healthy lane shows no Stop and no Recover.

This is an amendment and not a reversal: every hazard D1 refused is still refused, and nothing
standing is added. `E16` and `E17` reach a screen only as §9's `end_session` and `kill_run` verbs,
offered by the failure that needs them and gone with it.

**D2 · The stage strip is linear; work is not.**
The design draws one chip per stage, in order, one active. The ticket requires the model to carry a
step that failed and sent work backwards, and a step entered more than once.
**Resolution: the design wins on the drawing, the ticket wins on the data.** `stage` carries `round`,
`of`, `source` and `regressed_from` (M3, F20), and the *only* visual consequence is the strip's
right-hand caption, which the design already uses for `stage 4/8 · core sequence` and which becomes
`stage 4/8 · round 2 of 4 · <source>`. No new chip, no branch drawn in the strip, no history
visualisation. The full sequence of stage transitions is in the transcript, where the `stage` entries
already are.

**D3 · A fifth tab.**
The design draws four tabs: `SESSION · TICKET.MD · REPORT.MD · RUNS`. Feature 6 needs the lane's diff
somewhere.
**Resolution: the ticket wins**, with `DIFF · N files` as a fifth tab in the row the design already
made data-driven. The alternative — a modal or a separate screen — is worse for the same reason the
report is a tab: deciding about the work means moving between what it did and what it changed.

**D4 · Permissions have no drawing at all.**
The design has no permission request, no policy control and no granted-rules list. The ticket treats
them as behaviour, not decoration, and requires all three.
**Resolution: the ticket wins**, and the surfaces are assembled from the design's existing
vocabulary: the inline request is the `held` rectangle in the accent colour with three buttons; the
rules list is the design's table pattern; the policy control is the token system's radio group. The
only addition to the design's chrome is one word in the top bar — `PERMISSIONS`, styled exactly as
`VOCABULARY` — because a revocable list nobody can reach is not revocable.

**D5 · `silentSignal: quiet` is not built.**
The design exposes a prop offering silence without a counter.
**Resolution: the counter always.** *"Silent 4m 12s"* is actionable and *"silent"* is not — the number
is what tells a person whether to wait or to interrupt, and it is the reason the vocabulary sheet
calls this state *"alive, going nowhere"* rather than just *"quiet"*. The prop is a design option, and
this is the specification choosing between two things the design was content to offer both of.

**D6 · `restart demo` is not a feature.**
The link in the design's composer resets its scripted demonstration. It has no product meaning and is
not built. Said out loud because a specification that silently drops something visible in the design
is indistinguishable from one that missed it.

**D7 · `Sprint in flight` and `Release train` are drawn and are not specified.**
`Dashboard.dc.html` draws two more panels than §4 has sections for: a sprint card with a window, a
budget, a burn-down and a list of blockers, and a release train of tags with what each one carries.
**Resolution: neither is specified, and that is a decision rather than an omission.** Both need
something this tool does not have and would have to invent:

- *Sprint* needs a document kind nobody writes. There is no sprint document, no window and no budget
  anywhere the tool reads, and a screen for a process that does not exist is how §4 came to declare
  fourteen surfaces while §11 could not check six of its own claims.
- *Release train* needs a tagging discipline nobody follows. `git tag` answers with nothing in the
  repository this was drawn against, so every ticket falls into one bucket and the surface is a list
  with a single heading.

**The sentences that would make each specifiable**, so this is a door and not a wall. A sprint becomes
a surface the day a repository declares a fourth document kind under `docs` whose documents carry a
window and the requirement ids inside it — one more tree of documents the tool reads and does not
understand, exactly as §4.15's wiki is, and one more optional key in §9.1. A release train becomes a
surface the day the base branch carries tags: `for-each-ref` and `rev-list` are already two of §3.5's
six read-only subcommands, so the grouping is *the first tag whose commit contains this ticket's
merge*, computed from what the tool already reads, with `untagged` as the last group.

Neither gets a §4 section and neither gets a rail row until then. A row leading to a screen that
explains why it is empty is a destination costing a click to say nothing — which is the same refusal
§4.15 makes for an undeclared `docs.wiki`, and the one `FAC-FEAT-302`'s rail already makes for every
destination this client cannot serve.

### And one disagreement with the manifest schema

`manifest.schema.json` declares the resource relation twice: `workspaces.<name>.needs` and
`resources.<name>.needed_by`. This specification uses `needed_by` — it is the form the epic's own
example uses, and it keeps everything about a scarce resource in one block — and **`workspaces.needs`
is removed from the schema** in the same commit as this document. Two ways to declare one relation is
two sources of truth that will eventually disagree, and the pass criterion for this ticket requires
that every field in the schema is used or gone. This is also, strictly, a second file changed by a
ticket whose safety note says it writes one; the criterion is more specific than the note, so the
criterion wins and the change is recorded here rather than made quietly.

---

## 9. Manifest field map, and the closed sets a remedy is built from

### 9.1 Every field in the manifest

Every field in `manifest.schema.json`, and where this specification relies on it. The check in §11
runs both directions: a field used here that the schema does not define is drift, and a field the
schema defines that appears nowhere here is drift.

| Field | Used by |
|---|---|
| `factory` | §2 version rule · E2 refusal on unknown major · E1 · F1 |
| `workspaces.<name>` | §4.3 grouping · E4 group order · E5 `workspace` · E12 |
| `workspaces.<name>.path` | E12 worktree layout · E18 `cwd` · E19 working directory — relative to its repo's `path`, so `"server"` under a repo at `factory` is `factory/server` |
| `workspaces.<name>.repo` | E2 — which `repos` entry this workspace belongs to, and therefore what its `path` is relative to. Required once `repos` is declared and meaningless without it; a name nobody declared is refused (`unknown_repo`) |
| `workspaces.<name>.gate` | E19 `which:"gate"` · F21 |
| `workspaces.<name>.build` | E19 `which:"build"` · F21 |
| `workspaces.<name>.review` | E19 `which:"review"` · F21 — the third and last member of the closed set of command names, added by `SRV-FEAT-500` so the `REVIEW` stage is a command the tool runs rather than a reading somebody reports having done. Checked at load by the same code path as the other two, and its findings approve and block nothing: the tool reports the exit code and §10 is why it has no opinion about it |
| `workspaces.<name>.start_preflight.command` | E18 optional confined readiness command, before an agent starts |
| `workspaces.<name>.start_preflight.timeout_seconds` | E18 upper bound on the readiness command, 1–60 seconds |
| `workspaces.<name>.start_preflight.exempt_ticket_ids` | E18 exact ticket ids that bypass the readiness command |
| `workspaces.<name>.toolchain` | E12 the host paths read-bound into every lane for this workspace, at the path written here — a leading `~/` means the LANE's home, so a build script that looks for `~/<toolchain>` finds it · E2 refusal when one is not there or is broad enough to be a whole home · E12's refusal, before any resource is claimed, when one has gone since load (`SRV-FIX-077`) |
| `resources.<name>.exclusive` | §3.2 `queued` · E9 `resources` · E12 claim · F8 |
| `resources.<name>.needed_by` | E12 which resources a lane must hold · E9 queue · F8 |
| `resources.<name>.description` | §4.4 the sub-line on a queued lane card · E9 |
| `resources.<name>.devices` | E12 the host device nodes bound into the lane that HOLDS this resource, and into no other — at the path written here, not the one it resolves to; only on an `exclusive` resource (`SRV-FIX-063`, `SRV-FIX-069`, `SRV-FIX-070`) · declared in `manifest.schema.json`, which did not know the field until `SRV-FIX-069` and warned `unknown_key` on every manifest that used it |
| `lanes.host` | E9 `seats` · E12 `409 no_seat` |
| `repos.<name>.path` | E2 — one repository inside the repository the tool was pointed at, relative to its root; every path the entry declares is relative to THIS one, which is what makes splitting it out later a deletion rather than a migration. Refused beside a top-level `docs` (`docs_and_repos`). **A block with one entry is the ordinary case and not a degenerate one**: this repository's own manifest declares a single repo since `SRV-FEAT-244`, because the factory that lives beside the product was given a manifest of its own instead of a second entry — the schema goes on supporting many, since the tool is pointed at monorepos that really do keep two |
| `repos.<name>.role` | E3 · E4 — whether this repository is the thing being worked ON (`product`, the default) or the thing being worked WITH (`tool`). A `tool` repository's requirements are not rows in E3 and not members of E4's `standalone` group; every other endpoint reads it exactly as before, so its tickets stay addressable and claimable. Two values and no third, and no per-request override — the question *is this the product or the tool* is answered once, by the person who wrote the manifest. A value that is neither is refused at load (`unknown_role`), and a manifest whose every repository is `tool` is refused at load (`no_product_repo`): a tool that displays nothing is not a narrower tool, it is a blank screen (`SRV-FIX-199`) |
| `repos.<name>.docs.epics` | E3 · E4 · §4.2 empty state names this path — read for every declared repo, and every one of them watched |
| `repos.<name>.docs.tickets` | E5 · E12 · §4.8 — the repo whose directory holds a ticket's file is the repo that ticket belongs to; an id in two of them is `409 ambiguous_ticket_id` |
| `repos.<name>.docs.reports` | E6 · §3.4 evidence · §4.9 — a report is read beside the ticket it reports on, in that ticket's own repo |
| `repos.<name>.docs.wiki` | E27 · §4.15 — this repository's own pages. Optional and with no default: undeclared means no surface and no rail row |
| `repos.<name>.docs.escapes` | E28 · §4.16 — this repository's own ledger, one file. Optional, no default, the same refusal |
| `docs.epics` | E3 · E4 · §4.2 empty state names this path — the implicit repo at `.`, for a manifest that declares no `repos` |
| `docs.tickets` | E5 · §4.8 — the implicit repo's |
| `docs.reports` | E6 · §3.4 evidence · §4.9 — the implicit repo's |
| `docs.wiki` | E27 · §4.15 — the implicit repo's pages. **Optional, and with no default**: `epics`, `tickets` and `reports` default because every repository has them and the tool would be useless without a guess; a wiki is a thing a repository may simply not have, and a default would be the tool naming a directory nobody declared, which is the one thing §2's boundary exists to stop. Undeclared ⇒ no surface, no rail row, and `E27` answers `404` naming the key rather than a path |
| `docs.escapes` | E28 · §4.16 — the implicit repo's ledger. A FILE, not a directory: a ledger is one document a row is appended to. Optional and with no default, for `docs.wiki`'s reason and with `E28`'s same refusal |
| `stages` | E5 `stage.source:"manifest"` fallback · §4.5 strip · §4.11 sample |
| `status.in_flight` | §3.5 rule 5 — the one fact a document is allowed to contribute · F30 |
| `display.ticket_fields` | E5 `extras` · §4.5 tags · F4 |
| `evidence.line_prefix` | E2 echo · E24 — the literal a line of a ticket's §1 begins with when it names what evidences the criterion above it. The tool holds no such word and looks for nothing it invented; the part of the reference before `::` is the path |
| `evidence.frozen_after` | E2 echo · E22 arms the freeze on the stage a run REPORTS · E24 answers `asked` for a path that §1 named, whatever the policy says and whatever has been granted · `M6 frozen_by` · §4.12. Refused at load naming `/evidence/frozen_after` when it is not a member of `stages`: a stage nobody can report is a freeze that can never fire |
| `agent.command` | E18 spawn · E12 the lane's read-only bind of the directory its first word resolves to |
| `agent.credentials_dir` | E12 the one credential file carried into the lane's own configuration directory · E2 refusal when it is not there |
| `agent.config_trust.file` | E2 refusal when the agent's configuration does not carry this repository · `detail.path`. Optional, and unnecessary on the default path: E12 generates the trust each lane uses |
| `agent.config_trust.keyed_by_path` | E2 — the member of that file keyed by repository path |
| `agent.config_trust.flag` | E2 — the member that must be `true`, named in `detail.field` |
| `agent.permission_mode` | §4.12 default policy · E11 `policy_source:"manifest"` |
| `agent.allowed_tools` | §4.12 seeded rules with `source:"manifest"` · F22 |
| `agent.model` | E18 default, overridable per run · E7 recorded per run |
| `agent.start_prompt_file` | E18's prompt when the request carries none · E2 refusal when it is not there · F31's one mechanism |
| `default_agent` | E2 validates the declared profile name · E18's choice when the request omits `agent`; legacy singular `agent` exposes `default` |
| `default_tree` | E2 validates the declared preset name · E18's choice when the request omits `tree` |
| `trees.<name>.decider` | E2 accepts only `rules` in this contract · E18 exports the selected decider |
| `trees.<name>.agents` | E18 checks each selected provider file in the lane before preflight, then exports the names |
| `agents.<name>.driver` | E2 accepts the declared transport · E18 launches Claude stream JSON or Codex app-server · E7/E31 preserve the driver attribution |
| `agents.<name>.command` | E2 resolves the executable · E18 launches it inside the lane; clients select the profile name, never this command |
| `agents.<name>.models` | E2 requires a non-empty declared choice set · E18 rejects an undeclared model before spawning |
| `agents.<name>.default_model` | E2 requires membership in the profile's models · E18 uses it when model is omitted · E7/E31 retain the resolved model |
| `agents.<name>.model` | The legacy model spelling is accepted in the profile shape; plural profiles resolve their authoritative model from `default_model` |
| `agents.<name>.credentials_dir` | E2 validates the declared source · E18 prepares profile-specific agent configuration; the source developer files remain unchanged |
| `agents.<name>.config_trust.file` | E2 validates the profile's optional declared trust source, using the same rule as singular `agent.config_trust` |
| `agents.<name>.config_trust.keyed_by_path` | E2 the member of that trust source keyed by repository path |
| `agents.<name>.config_trust.flag` | E2 the member that must be true in the declared repository entry |
| `agents.<name>.permission_mode` | E18 the selected profile's launch policy; Factory lane confinement and permission decisions remain authoritative |
| `agents.<name>.allowed_tools` | E18 the selected profile's declared launch tool policy; it cannot add host paths to the lane |
| `agents.<name>.start_prompt_file` | E18 the selected profile's default start prompt, unless a role supplies its own prompt file · E2 refuses an unreadable declaration |
| `agents.<name>.efforts` | E2 the closed set of provider effort words this profile may be launched at · E18 refuses an effort outside it before spawning · absent means this profile carries none and E7 records `null` |
| `agents.<name>.default_effort` | E2 requires membership in that profile's own `efforts` · E18 uses it when neither the request nor the role names one · E7/E18 record the resolved value |
| `roles.<name>.start_on_stage` | E22's automatic start · F31 |
| `roles.<name>.lane` | E22 — `new`, and the only value there is · F31 |
| `roles.<name>.prompt_file` | E18's prompt for a started role · E2 refusal when it is not there · F31 |
| `roles.<name>.effort` | E18 the effort a run started for this role is launched at, over `run_effort` and the profile default · E2 refuses a value a declaring profile has not declared |
| `run_effort` | E18 the effort a run with no declared role is launched at, over the profile default and under an explicit request · E2 refuses a value a declaring profile has not declared |
| ~~`workspaces.<name>.needs`~~ | removed — §8, duplicate of `resources.needed_by` |

<!-- EXAMPLE · this block is a target repository's own data, not the tool's -->
```json
{
  "factory": "0.1",
  "workspaces": {
    "core":   { "path": ".",     "build": "make -C core", "gate": "cd core && pytest tests/ -v" },
    "edge":   { "path": "edge",  "gate": "npm --prefix edge test" },
    "web":    { "path": "web",   "build": "npm --prefix web run build", "gate": "npm --prefix web test" }
  },
  "resources": {
    "climate-chamber": { "exclusive": true, "needed_by": ["edge"], "devices": ["/dev/ttyUSB1"], "description": "one chamber, one run at a time" }
  },
  "lanes":   { "host": 3 },
  "docs":    { "epics": "docs/requirements", "tickets": "docs/tickets", "reports": "docs/reports" },
  "stages":  ["SPEC", "IMPL", "GATE", "DELIVER"],
  "display": { "ticket_fields": ["contract", "owner"] },
  "agent":   { "command": "claude", "permission_mode": "edit_free", "allowed_tools": ["Read", "Bash(npm test*)"] },
  "roles":   { "reviewer": { "start_on_stage": "GATE", "lane": "new", "prompt_file": ".factory/prompts/reviewer.md" } }
}
```
<!-- END EXAMPLE -->

Everything the tool knows about that repository is in those twenty lines. The names `core`, `edge`,
`web`, the stage names, the chamber, the role called `reviewer` and the paths are all the
repository's own words, and the tool attaches meaning to none of them.

### 9.2 The remedy verbs — a closed set

§3.6's `action`, and every value it may take. The set is **closed and small**: it starts at what the
six classes of dead end need and grows by amendment, never by a server string the client has to
guess at. It is declared here, carried by `http.schema.json`, and reaches the client by
`npm run gen:api-types` like every other wire word — three places that check 12 requires to be one
set, because two of them agreeing is how a blank button ships.

`target` is what the verb acts on, so the client builds the call from the remedy. `destroys` below
is the **worst case** a person must assume for that verb; the `destroys` on the wire is the server's
statement about the one failure in hand, and may be narrower.

<!-- REMEDY-VERBS -->

| action | what it does | `target` | the call it becomes | `destroys` |
|---|---|---|---|---|
| `retry` | re-issue the request that just failed, unchanged | `{}` | the failed request itself | `[]` |
| `reload_manifest` | re-read the manifest from disk and resolve it again | `{}` | not built yet — `WEB-FEAT-179`'s | `[]` |
| `claim_lane` | claim a lane for this ticket | `{ticket, workspace}` | E12 | `[]` |
| `open_lane` | put this lane's own surface on screen, DIFF tab, where the work that is in the way is named | `{lane}` | §4.4 · E10 | `[]` |
| `start_run` | start a run in the lane | `{lane}` | E18 | `[]` |
| `end_session` | let this turn finish, then end the session | `{lane}` | E16 | `[]` |
| `kill_run` | kill the process now | `{lane}` | E17 | `["turn"]` |
| `recover_lane` | kill the run, re-seed the lane's environment, keep the session | `{lane}` | E25 — `SRV-FEAT-177`'s | `[]` |
| `release_lane` | release the lane and remove its checkout | `{lane, force}` | E13 | `["session","worktree"]` |
| `discard_ignored` | release, removing the ignored files the refusal named | `{lane, paths}` | E13 with consent | `["session","worktree","ignored_files"]` |
| `release_resource` | free an exclusive resource whose holder is gone | `{resource, lane}` | not built yet — `SRV-FEAT-180`'s | `[]` |

<!-- END -->

Two of these name no endpoint yet. That is the point of freezing a contract before the children
build against it: the verb and its target are decided here, once, and the endpoint arrives under the
ticket that owns it rather than under whichever session reached the problem first.

### 9.3 What `destroys` may name

Nouns this specification already uses, and no others. `destroys` is rendered on a control and
compared in a test, so it is a closed vocabulary and not free prose.

<!-- DESTROYS-NOUNS -->

| noun | what is lost |
|---|---|
| `turn` | the turn in flight, and whatever the agent had produced in it that had not been written down (§3.1) |
| `session` | the agent CLI's resumable conversation, and the memory of it on disk (§3.1) |
| `worktree` | the lane's checkout, and anything committed only on its branch (§3.1) |
| `ignored_files` | files git was told to ignore inside the lane's checkout — a gate capture, a database, keys (`SRV-FIX-059`) |

<!-- END -->

---

## 10. Architecture compliance

The dependency direction in `ARCHITECTURE.md` is `api → {catalog, lane, agent, routing} → manifest → core`,
arrows one way, no sideways reach. Nothing in §5–§7 crosses it. The three consequences worth naming
because they are the ones that would decay first:

**`manifest` is the only module that knows the repository has a shape.** Every path in §5 that touches
the repository is resolved by `manifest` and passed down as a typed value. `catalog` never contains a
directory name; `lane` never contains a resource name; the client's types are generated from
`manifest.schema.json`, so a field added on one side cannot silently disagree with the other.

**`agent` is the only module that knows the agent CLI's wire format.** Every entry kind in §4.6, every
permission request in F22, every cost number in F27 and every session id is parsed inside
`agent/transcript`. The socket messages in §6 are the tool's own vocabulary, not the CLI's — which is
what makes a second agent backend a change in one directory. Nothing in `api` or the client may
pattern-match on the CLI's frame names.

**`catalog` does not learn git, and `delivery` does not learn documents.** §3.5 is a second module
under `app/`, a sibling of `catalog`, and `api` composes the two — the arrows stay one way. `catalog`
reads the documents and offers a hole where a delivery object goes; `delivery` runs the subprocesses
and is handed a ticket id and the one thing the document is allowed to tell it (was a session
launched). Neither includes the other's header. A `catalog` that shelled out to git would be the
same boundary failure as a `catalog` that hardcoded a directory name, one layer down.

**No module has an opinion about the repository's process.** Stage names are strings from the
manifest or the ticket (E22, F20). No retry cap, no required sequence, no stage whose name means
something to the tool. `E22` accepting a stage that the declared sequence does not contain, and
reporting it as `unknown_stage` rather than an error, is this rule being enforced at the one endpoint
most tempting to make clever. E33 takes a finite explicit command order and exit policy from
the caller; it does not infer stages, prescribe a workflow, judge output text or approve acceptance.

**And a role name is a string too** (F31). `qa`, `reviewer`, `security` are words the repository
chose; the tool compares one string to a stage name, reads a file the repository wrote, and starts a
process. There is no built-in role, no default role and no special case, and every word of meaning —
what the check is, that it should start cold, what it must read — lives in the repository's own
prompt file. This is the endpoint that rule is most tempting to break at now, exactly as `E22` was
before it.

Two more, from the epic and from this repository's standing rules: **the engine thread is never
blocked** — process supervision, file watching, `git` invocations and command execution all run in a
`core/worker`, and E16/E17/E19 return `202` for exactly that reason — and **no mutex in event-driven
code**; shared state is reached from the io_context, and `core/ws` is what makes the client's
no-polling rule implementable at all.

---

## 11. How to check this document

The checkable criteria from the ticket, with the command. Run from the repository root.

**Checks 1 – 7 and 12 are executed** by `factory/server/tests/test_spec.py`, one case each, and the
commands below are what each case does, written out for a reader. They were printed here and run by
nobody for four months, and two of them were red the whole time: check 3 named a total the table had
grown past, and check 6 — *the assertion the whole product rests on* — found four lines. Neither is
noticeable by reading, which is the whole argument for a check being a case rather than a paragraph
(`SRV-FEAT-304`).

    cd factory/server && pytest tests/test_spec.py -v

**No check below states a count it then has to keep in step by hand.** A literal in one of these
commands is a second place for a number to live and the first place for it to be wrong — it is what
made check 3 read `expect 16` over a table of seventeen rows. Every total here is derived from the
document, and where the document states one in prose, the case compares that sentence against the
rows it is about.

**1 · Every screen has a source.** Every `### 4.N` block in §4 carries a `**Data**` line naming at
least one `E<n>` or `M<n>`. How many surfaces there are is whatever §4 holds — printed, never
asserted:
```bash
python3 - <<'PY'
import re
blocks = re.split(r'^### (4\.\d+.*)$', open('factory/docs/product/SPEC.md').read(), flags=re.M)[1:]
pairs = list(zip(blocks[::2], blocks[1::2]))
print(len(pairs), 'surfaces')
for head, body in pairs:
    data = re.search(r'\*\*Data\*\*(.*?)(?:\n- \*\*|\n###|\n---)', body, re.S)
    src = re.findall(r'`(E\d+|M\d+)', data.group(1)) if data else []
    print(('OK   ' if src else 'FAIL '), head.split()[0], ','.join(src) or 'NO SOURCE')
PY
```

**2 · Every endpoint is complete** — method, path, request, response, missing-thing:
```bash
python3 - <<'PY'
import re
spec = open('factory/docs/product/SPEC.md').read()
blocks = re.split(r'^#### (E\d+) · (\S+) `([^`]+)`.*$', spec, flags=re.M)[1:]
n = 0
for i in range(0, len(blocks), 4):
    eid, method, path, body = blocks[i:i+4]
    n += 1
    missing = [f for f in ('Request', 'Response', 'Missing') if f'**{f}**' not in body]
    print(('OK   ' if not missing else 'FAIL '), eid, method, path, ' '.join(missing))
print(n, 'endpoints')
PY
```

**3 · Every socket message states its trigger, and §6 agrees with its own tables.** Both of §6's
tables carry an `Emitted when` column, and §6's closing sentence states the totals in words. The
rows are the fact and the sentence is checked against them:
```bash
python3 - <<'PY'
import re
spec = open('factory/docs/product/SPEC.md').read()
rows = re.findall(r'^\| (M\d+) \| `', spec, re.M)
said = re.search(r'^(\w+) messages\. (\w+) client\u2192server, (\w+) server\u2192client', spec, re.M)
print(len(rows), 'rows:', ' '.join(rows))
print('\u00a76 says:', said.group(0))
PY
```

**4 · Every feature has a runnable criterion.** Each `**F<n> ·` block contains `*Criterion*` or
`*Criteria*`, and no criterion is prose. The two counts are the check — they are one number, and
neither of them is written down here:
```bash
test "$(grep -c '^\*\*F[0-9]* · ' factory/docs/product/SPEC.md)" \
   = "$(grep -c '^\*Criteri'      factory/docs/product/SPEC.md)" && echo 'one criterion per feature'
```

**5 · All seven of the ticket's added features are specified**, named, with criteria:
```bash
grep -c '\[§1-[1-7]\]' factory/docs/product/SPEC.md        # expect 7, one per feature
```

**6 · No repository-specific knowledge leaked in.** The assertion the whole product rests on:
```bash
grep -icE '\b([b]oard|[s]eam|[f]irmware|[e]sp32|[i]tor3|[r]oti)s?\b' factory/docs/product/SPEC.md   # expect 0
```
The first letter of each word is bracketed so that this line — the check itself — is not the one thing
the check finds. The regex is otherwise the ticket's, character for character, and it is zero for the
whole file including the example block: the example uses a repository invented for it, so the exemption
the ticket allows for target-repository data is not needed anywhere in this document.

Every word below is written with its first letter bracketed for the same reason the expression is, and
the trailing `s?` is why that now matters in the prose as well as in the check.

`s?` sits inside the word boundary because without it every member was singular-only, and the check
answered `0` about the plural of all six. This document carried exactly one such word: §2 above named
`MODEL.md` §2's six by `[s]eams` until `SRV-FIX-306`, so a check blind to the plural was blind to the
only leak left in the file.

`[s]eam` stays on the list. `CLAUDE.md` states the boundary this check defends and names `[b]oard` and
`[s]eam` in one sentence as the same kind of word, so a target repository's rail row of that name is
exactly what check 6 exists to keep out of this document. `MODEL.md`'s six are named here by the tool's
own word — `invariants` — and `MODEL.md` is not in this check's subject. **A member may leave this list,
and this paragraph is where the departure is written down**, because shortening the list and fixing the
document are otherwise the same diff:
`test_removing_a_word_from_check_6_is_not_a_way_to_make_it_green` fails on a shorter expression than the
last committed one unless a line here names the word and says it was removed.

**7 · The manifest matches** — in both directions. Every field the schema defines appears in §9's
table, and every field §9 relies on exists in the schema:
```bash
python3 - <<'PY'
import json, re
def walk(node, path=''):
    kids = []
    for k, v in (node.get('properties') or {}).items():
        p = f'{path}.{k}' if path else k
        kids.append(p)
        yield from walk(v, p)
    ap = node.get('additionalProperties')
    if isinstance(ap, dict):
        kids.append(f'{path}.<name>')
        yield from walk(ap, f'{path}.<name>')
    yield (path, kids)
tree = dict(walk(json.load(open('factory/docs/technical/contracts/manifest.schema.json'))))
paths = {p for p in tree if p}
leaves = {p for p in paths if not tree.get(p)}          # a field with no children of its own
spec = open('factory/docs/product/SPEC.md').read()
table = re.search(r'\n## 9\..*?\n\n(\|.*?)\n\n', spec, re.S).group(1)
rows = [r for r in table.splitlines() if r.startswith('| `')]      # struck-through rows are removals
used = {re.sub(r'[`~]', '', r.split('|')[1]).strip() for r in rows}
print(len(leaves), 'leaf fields in the schema;', len(used), 'rows in §9')
print('schema leaf no spec relies on :', sorted(leaves - used) or 'none')
print('spec row the schema lacks     :', sorted(used - paths) or 'none')
PY
```

**8 · The architecture is respected.** §10, read against `ARCHITECTURE.md`. Not scriptable, and the
two clauses to check by hand are that nothing but `manifest` knows the repository has a shape and
nothing but `agent` knows the CLI's wire format.

**9 · Negative — the boundaries are stated.** §1 names four things this does not cover, with reasons
(the ticket asks for at least three), plus two smaller ones in the closing paragraph:
```bash
sed -n '/^### Deliberately not covered/,/^## 2\./p' factory/docs/product/SPEC.md | grep -c '^[0-9]\. \*\*'
```

**10 · Negative — the disagreements are recorded.** §8 names each one, with which side wins and
why — including the stop controls the ticket predicted, and the two panels `Dashboard.dc.html` draws
that are deliberately not specified (D7). The count is §8's own sentence against §8's own headings,
and neither number is written here:
```bash
grep -c '^\*\*D[0-9] · ' factory/docs/product/SPEC.md
grep -n 'There are [a-z]*\. In each case' factory/docs/product/SPEC.md
```

**11 · Reserved, and not silently reused.** Check 11 — *every endpoint that changes something is
named by a §4 surface's `Data` line* — belongs to `FAC-EPIC-002`'s specification amendment, which
was renumbered away before it landed. The number stays free for it: three documents already cite
`check 11` and `check 12` by number, and a check that quietly took 11 would make all three wrong.

**12 · Every error the tool can show names the action that ends it.** §3.6's rule, held by a test
instead of by a sentence. Every error code `factory/server/app` can put in an error envelope appears
in exactly one of the first two tables below — with a remedy, or with an exemption **and a reason**.
The third table is a refinement of the first and not a fourth answer: a code that carries a remedy
almost everywhere names, there, the one **site** at which it does not, with its own reason:

```bash
cd factory/server && pytest tests/test_spec.py -v
```

The rows are **derived from the source on every run**, never from a list somebody maintains here: an
error code is a literal assigned to a member named `errorCode`, written into a literal
`{"error":{"code":…}}` body, or passed to the code argument of a discovered refusal factory.
The factory's emitted envelope identifies that argument: `catalog::errorJson` takes code first,
while routing and preflight response factories take status first and code second. A literal in a
message or target argument is not a code. Factory discovery supports ordinary named return
signatures, `const std::string&` code parameters and exact-qualified or same-owner bare literal
callers; namespace aliases, suffix-qualified calls, named constants and other dynamic/binding
forms remain SRV-FIX-185, rather than a claim of complete C++ data flow. `{"code":…}` outside an error envelope is not one — `path_missing` and
the `warnings[]` copy of `invalid_manifest` are notices about the repository, and a notice is not a
state anybody is stuck in.

*The remedy table.* Read `` `verb` → `[…]` `` as *this verb, costing this*; ` · ` separates two
remedies for one code. Every code here has at least one remedy costing `[]`, which is the whole of
*no failure's only remedy destroys work*.

<!-- CHECK12-REMEDIES -->

| code | remedies |
|---|---|
| `agent_not_declared` | `reload_manifest` → `[]` |
| `agent_start_failed` | `reload_manifest` → `[]` |
| `confinement_unavailable` | `retry` → `[]` |
| `device_busy` | `retry` → `[]` · `release_resource` → `[]` |
| `ignored_work` | `open_lane` → `[]` · `discard_ignored` → `["session","worktree","ignored_files"]` |
| `invalid_manifest` | `reload_manifest` → `[]` |
| `invalid_agent_selection` | `reload_manifest` → `[]` |
| `tree_agent_missing` | `reload_manifest` → `[]` |
| `lane_exists` | `open_lane` → `[]` |
| `no_git_root` | `reload_manifest` → `[]` |
| `no_seat` | `reload_manifest` → `[]` · `release_lane` → `["session","worktree"]` |
| `not_declared` | `reload_manifest` → `[]` |
| `not_found` | `reload_manifest` → `[]` · `claim_lane` → `[]` · `retry` → `[]` |
| `not_revocable` | `reload_manifest` → `[]` |
| `remove_failed` | `retry` → `[]` |
| `resource_busy` | `release_resource` → `[]` · `release_lane` → `["session","worktree"]` · `retry` → `[]` |
| `routing_busy` | `retry` → `[]` |
| `spawn_failed` | `retry` → `[]` · `recover_lane` → `[]` |
| `start_prompt_unreadable` | `reload_manifest` → `[]` · `retry` → `[]` |
| `start_preflight_failed` | `retry` → `[]` |
| `start_preflight_running` | `retry` → `[]` |
| `start_preflight_timeout` | `retry` → `[]` |
| `ticket_not_on_branch` | `retry` → `[]` |
| `uncommitted_changes` | `open_lane` → `[]` · `release_lane` → `["session","worktree"]` |
| `unmerged_commits` | `open_lane` → `[]` · `release_lane` → `["session","worktree"]` |
| `unreadable_worktree` | `retry` → `[]` |
| `unsupported_manifest_major` | `reload_manifest` → `[]` |
| `workspace_unrunnable` | `reload_manifest` → `[]` · `retry` → `[]` |
| `worktree_failed` | `retry` → `[]` |
| `worktree_in_the_way` | `open_lane` → `[]` · `release_lane` → `["session","worktree"]` |
| `worktree_unreadable` | `retry` → `[]` |
| `write_failed` | `retry` → `[]` |
| `wrong_state` | `start_run` → `[]` · `recover_lane` → `[]` · `end_session` → `[]` · `kill_run` → `["turn"]` · `retry` → `[]` |

<!-- END -->

*The exemption table.* `remedy: null`, and why — one sentence each, because the cheap way to make
this check green is to exempt everything and a reason is the only thing that costs anything to
write. Two shapes qualify and no third: **the tool may not act** (acting would break a boundary §1
or §2 states), and **the control that produced it is already the way out** (offering it again is
noise, not a remedy).

<!-- CHECK12-EXEMPTIONS -->

| code | why no action the tool could offer would end this |
|---|---|
| `already_passed` | The ticket's own document says `status: pass`, and the way out of that state is a person filing a `FIX` or editing the status — the tool does not write the repository's documents (§1, `S2`), so every action it could offer would be the tool deciding that finished work is unfinished. Nothing was taken and nothing is half-done: the claim is refused before `lane::Store` is entered, so there is no lane, no branch and no worktree for an action to act on. `detail.path` names the file the status was read from and `detail.status` echoes the word it carries, which is the whole of what a person needs to open the one document that ends this. |
| `ambiguous_ticket_id` | Two documents in the repository claim one id, and the tool does not write the repository's documents (§1). `detail.paths` names both files; the person edits the one they meant, and the next read answers. |
| `bad_decision` | A decision is `once`, `always` or `deny`, and the control that produces one offers exactly those three. This refuses a request no client of this contract can build — a person never reaches it and a test does. |
| `bad_filter` | The filter is one of `all`, `needs`, `running`, `blocked`, `done`, and the surface draws one control per value. A sixth string is not a request this client can build, so no action offered to this client would correct the caller — a person never reaches it and a test does. |
| `bad_lane` | A lane name that would leave the lane root: nothing was looked at and nothing was touched. The tool names every lane itself, so this path was fabricated, and an action that ended the state would be the tool following it. |
| `bad_policy` | A policy is `ask_all`, `edit_free` or `never_ask`, and the surface is a radio group over those three. Reaching this means the caller is not this client, and no action offered to this client would reach the caller. |
| `bad_request` | A request missing a member the contract requires. The remedy is a corrected request, which is the caller's to build — a call cannot carry the fix for its own malformation. |
| `bad_which` | A command is the workspace's `build` or its `gate`, and the surface draws one control per declared string. There is no third value for a remedy to correct. |
| `bad_window` | The window is an integer number of days and the surface offers a control over the values it can build. A window that is not one is a request no client of this contract can make, so the correction is the caller's, and an action offered to this client would not reach it — a person never reaches this and a test does. Nothing was read and nothing was computed: the refusal happens before the walk, so there is no half-finished answer for a remedy to finish. |
| `empty_message` | A message with no text in it. The composer is on screen, holds the cursor, and is the control that ends this state; offering the same control a second time is noise rather than a way out. |
| `gone` | The run this permission request belonged to is gone, so nothing was granted and nothing is left to grant or deny. The row leaves the screen with the run, and a remedy would act on a thing that no longer exists. |
| `not_ours` | Something is at this lane's path that this tool did not create, and it will not be touched. The refusal **is** the safety property; an action that ended it would be the tool removing a directory it does not own. `detail.path` names it. |
| `routing_conflict` | A request ID already retains a different immutable plan. Choosing a new ID or reconciling the caller's intent belongs to main; retrying the conflicting request cannot replace its history, and the tool must not rewrite that history or decide which plan the caller meant. Nothing new was dispatched. |
| `routing_unresolved` | Source, retained evidence or the current lane claim cannot establish authority for this request. Main or the operator must reconcile that diagnostic under D5 before further work: the tool cannot reconstruct a lost capture, bless another incarnation's evidence, edit repository inputs or replay uncertain effects. An action offered as an automatic repair would contradict E33/E34's no-replay and claim-bound evidence rules. |
| `unknown_prefix` | `E26` allocates an id for a prefix the manifest declares. A prefix it does not declare is a request no client of this contract can build — the surface offers the declared ones — so the correction is the caller's, and an action the tool offered would be the tool inventing a series the repository never named. |
| `work_conflicts` | The role's own commits and the work under review touch the same lines, and reconciling them is a decision about the repository's CONTENT, which the tool does not make (§1). Nothing was taken and nothing is left half-done: the role's branch is where it was, `ticket/<id>` never moved, and the claim created no lane for an action to act on. `detail.base` names the branch that would not come in and `detail.git` carries git's own conflict text; a person reconciles the two on the role's branch and the next claim takes the work. |

<!-- END -->

*The site exemption table.* One endpoint of one code, where that code stays remedied everywhere
else. Four things make a row legal, and `test_spec.py` holds all four: the code **is** in the remedy
table (a code that is nowhere remediable belongs in the table above, not here), it is **not** in the
exemption table (a code is exempt or it is remedied, and one that is both is a code check 12 says
nothing about), the site is an endpoint `§5` declares, and the reason is a sentence held to the same
bar. The table is narrow on purpose: a per-site null is the cheapest thing in this document to abuse,
and every row costs an endpoint id somebody can open and read.

<!-- CHECK12-SITE-EXEMPTIONS -->

| code | site | why no action the tool could offer would end this |
|---|---|---|
| `not_found` | `E8` | The run's `transcript.jsonl` is absent or has been cleaned, and the tool does not write a run's log — nothing it could offer would bring one back. The row survives in `E7` and `E31` from `meta.json`: the row is the record, the log is the evidence, and losing the evidence is not losing the record (§4.19). The message says which of the two happened and `detail.path` names where the tool looked. |

<!-- END -->
