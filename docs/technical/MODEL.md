# The factory — product model

[Documentation](../INDEX.md) / [Technical](README.md)

`factory-v0.1.0`

This is the layer above tickets. `SPEC.md` says what gets built and `ARCHITECTURE.md` says how the
code is arranged; neither is something a ticket can be checked against, because both are written as
description. This file is written as **constraint**: four parts — the target, the seams, the plan,
the decisions — and one rule that makes them load-bearing.

> **A ticket may not contradict or extend this model. If it needs to, it stops and the model
> changes first.**

The rule exists because of a measured failure. `SRV-FIX-290` split one root into two, fixed the one
consumer that failed loudly, and enumerated no others; `SRV-FIX-293`, `SRV-FIX-296` and
`SRV-FIX-298` are the three consumers it did not enumerate, each found by hand, each after a gate
went green. Four generations of one change, because there was no statement of the boundary for the
first ticket to be checked against — only its own §1, which it wrote itself.

---

## 1. Target

> One local tool. It reads whatever repository it is pointed at, shows that repository's
> requirements and tickets, runs agent processes in isolated lanes against them, and streams what
> those agents do to a browser. One developer, one workstation, one repository, one tool process.

That sentence and the four deliberate exclusions under it are `SPEC.md` §1 and are not restated
here. Read them there. Their status in this document is that **each exclusion is a decision** (§4),
so inverting one is a model change and never a ticket.

---

## 2. Seams

Seven boundaries the tool orchestrates. Each is one invariant sentence — the thing that must stay
true — plus what proves it is needed and where it is enforced.

A ticket cites the seam it must not contradict. It is a different citation from the endpoint ids
already in `seams:` frontmatter: `E12` names a surface a ticket *touches*; `S4` names a guarantee it
*may not break*. Both belong on the line.

---

### S1 · Shape — the repository's vocabulary means nothing to the tool

> **Everything repository-specific enters through `.factory/factory.json`. Below `manifest`, no
> module names a path, a workspace, a stage, a role or a resource — and no such name means anything
> to the tool.**

Enforced at: `app/manifest` is the only module that knows the target has a shape. `E22` accepts a
stage the declared sequence does not contain and reports `unknown_stage` rather than erroring; `F31`
treats a role name as a string with no built-in and no default.

Why it is stated: this is the boundary that decays invisibly. A `catalog` that hardcodes
`docs/tickets`, or a `lane` that knows a resource is called a board, still passes every test until
the second repository exists.

---

### S2 · Documents — one writer

> **The target repository's documents are the only state about its work. The tool reads them from
> disk on every request, keeps no index or cache that can disagree with them, and writes none of
> them.**

What the tool *does* write is `.factory/` and nothing outside it — the six paths in `SPEC.md` §2.
Run logs are not a second source of truth: nothing else can produce a process's own output.

Enforced at: `E3`–`E7` read on every request. `catalog` holds no copy.

Why it is stated: `ARCHITECTURE.md` — "adding one creates a second source of truth that will
disagree with git, and disagreeing with git is the failure this whole process exists to prevent."
The word that carries the weight is **one**: the exclusion in `SPEC.md` §1 is about a *second
writer* racing the first, not about storage. See D2 — this is the seam the client-editing request
collides with, and the only one with an open proposal against it.

---

### S3 · Roots — two roots, one join

> **A path out of the manifest is relative to the DOCUMENT root; a lane worktree is a checkout of
> the GIT root. Every path built from a manifest-relative value is joined in exactly one place,
> which reads the roots itself and takes neither as an argument — and that place is the only
> producer.**

Enforced at: `E1` reports both roots. `E12` refuses `no_git_root` at claim time rather than at
`E18` after a branch and a worktree exist.

Why it is stated, and the clause that keeps being dropped: **the sole-producer half**. `SRV-FIX-293`
landed the one function and did not land the case asserting it is the only producer, so
`SRV-FIX-296` (a ref watch armed from the wrong root, silent, five seconds late) and `SRV-FIX-298`
(a lane agent's cwd one directory too high — on this monorepo the wrong directory exists, so a
`FAC-*` BUILD stage goes green having compiled Acme's server) both followed. A compiler that
refuses a wrong argument cannot refuse a consumer that never calls the function. `SRV-FIX-298` is
open and owes that case over **all** manifest-relative values, not only `docs/` ones.

---

### S4 · Reachability — a lane reaches what it was given, and nothing else

> **A host path is reachable inside a lane iff the lane bound it in — including every toolchain a
> declared command needs to run. The tool enforces this; it is never a rule the agent is asked to
> observe.**

Enforced at: `app/lane` owns the confinement. `E12` binds; `E19` runs a declared command inside it.

Why it is stated: seven generations of one bug, each re-deriving what a lane can reach —
`SRV-FEAT-008` → `SRV-FIX-063` (queues for a board it cannot reach) → `SRV-FIX-077` (holds the
board, none of the toolchain that talks to it) → `SRV-FIX-191` (holds the workspace, none of the
toolchain that builds it) → `SRV-FIX-202` → `SRV-FIX-218`/`219`/`221` (bound in, never on `PATH`;
resolved against the wrong `cd`; resolves and still exits 127) → `SRV-FEAT-297`. None of them could
change the others, because none of them was checked against a statement of the boundary. The `iff`
is both directions: unreachable-but-needed is `077`; reachable-but-unbound is a confinement hole.

---

### S5 · Exclusivity — one holder, and one authority on who holds

> **A resource declared exclusive is held by at most one lane at a time. Whether a lane is live is
> answered by `GET /api/lanes` and by nothing else, and a lane's resources are released only by
> releasing the lane through the tool.**

Enforced at: `app/lane` owns the queue — "one board" and "one staging database" are the same
problem. `E9` answers liveness; `E13` releases.

Why it is stated: on 2026-08-06 a session surveyed four lane worktrees with `git status`, found
`dirt=0`/`ahead=0`, called that "verified empty" and removed them. One was 410 s into a run holding
`board-master`. `git status` answers *does this hold uncommitted files*; it cannot answer *is an
agent running in it*. The server reported `crashed · worktree_missing`, the agent kept running with
`--chdir` into a deleted directory, and the next lane queued forever on a board a corpse still held.

---

### S6 · Wire — every crossing is declared, and the declaration generates the code

> **The agent CLI's frame format is parsed only in `agent/transcript`. Everything that crosses into
> `api` or the client is the tool's own vocabulary, declared in `http.schema.json` and
> `socket.schema.json`, from which the client's types are generated — never hand-written on either
> side.**

Enforced at: `npm run gen:types` and `npm run gen:api-types`. The browser gate validates the real
server's live responses against the same files, so a schema that drifts from the C++ turns a gate
red instead of blanking a screen.

Why it is stated: it is what makes a second agent backend a change in one directory, and it is the
reason a field added on one side cannot silently disagree with the field the other side reads.

---

### S7 · The operator's checkout — a ref moves only with the working tree that holds it

> **The tool never edits a working tree it did not create, and never moves a ref that such a
> working tree has checked out unless the working tree moves with it. The only way it moves the
> operator's own checkout is git's own fast-forward, `git merge --ff-only`, which refuses rather
> than overwrite local changes; when git refuses, the tool lands nothing, reaps nothing, and says
> which paths stood in the way.**

Enforced at: `app/land` is the only module that moves a base ref. Every landing runs its merge and
squash in a scratch worktree the tool creates and removes. It moves `refs/heads/<base>` by
`update-ref` only when no working tree the tool did not create has `<base>` checked out. Reaping a
branch or a lane worktree happens after the base has moved and the operator's checkout, if it holds
the base, has fast-forwarded — never before.

Why it is stated: `SRV-FEAT-437` failed three QA rounds on one undrawn boundary. Round 2 ran
`git merge --squash` inside the operator's checkout and left it mid-merge with conflict markers.
Round 3 moved the squash to a scratch worktree and then moved `refs/heads/main` under the
operator's checkout with `update-ref`: `HEAD` followed the ref while the index and working tree
stayed behind, so the checkout showed the whole landing as staged deletions, the landed files were
not on disk, the branch that held them was already reaped, and the operator's next ordinary commit
reverted the ticket. The remedy the tool printed, `git merge --ff-only main`, answered "already up
to date". Two mechanisms, one cause: the deployment is a checkout on `main` with a developer mid-edit
in it, and nothing said what a landing may do to that checkout.

---

## 3. Plan

Two versions. A finish line is a sentence that can be checked, not a set of tickets.

The finish lines below are constraints. The [roadmap](../overview/ROADMAP.md) carries the reviewed
current implementation and acceptance state; the [overview](../overview/README.md) maps capabilities
to their sources.

### v0.1 — the tool runs its own work

**Finish line: one `FAC-*` ticket is driven start-to-merge from the web client — claimed, run
through its stage sequence, QA'd and merged — with no step taken in a terminal.**

Reached to the last stage and not past it, 2026-09-07. `SRV-FIX-432` was claimed, run (twice) and
QA'd inside the tool, and stopped at MERGE: a lane is a worktree, and git refuses to check out
`main` in a second worktree while the primary checkout holds it, so the branch sat at
`status: pass`, unshipped, until an operator squashed it by hand. The same run showed a lane
pushing two raw commits to `origin/main` with the operator's own forge credential. The four tickets
formerly listed here were about the document root differing from the git root; the factory is its
own repository now, both roots are one directory, and none of them applied.

Reviewed on 2026-10-03 against `f2b3754d`: D3 landing is implemented and `SRV-FEAT-437` records
`pass`; `SRV-FIX-438` records superseded by `SRV-FIX-532`; `SRV-FEAT-439` (restart from the screen)
still records `todo`. The 2026-09-07 run above remains historical evidence. Implementing the missing
mechanisms does not itself establish the end-to-end finish line; its real journey still needs evidence.

### v0.2 — remote

**Finish line: a second machine drives a lane, with an identity the tool can name and a transport
it can trust.**

Begins after v0.1's finish line, not before. It inverts `SPEC.md` §1 exclusion 1 and therefore owns
its own security model — identity, transport, and multi-user semantics for lanes (S5) and
confinement (S4). See D1 for why the order is not negotiable.

---

## 4. Decisions

Settled decisions. A ticket may cite one; a ticket may not re-open one.

### D1 · Remote access is v0.2, after v0.1's finish line — 2026-08-29, settled

`SPEC.md` §1 exclusion 1 is the largest deliberate omission in the product and is what keeps the
whole tool free of a permission system. Reversing it pulls in authentication, identity, transport
security and multi-user semantics for exactly the two seams — S4 and S5 — that four open fixes are
still moving.

The order is not a preference about features. A security model designed now would be designed
against seams that the next lane fix relocates, which is the same rewrite this document exists to
prevent, arriving through a different door. The tool has never run one of its own tickets
end-to-end; adding a security model to something that does not yet work means debugging both at
once.

### D2 · Writing documents from the client — OPEN, blocking

**Status: proposed, not decided. No ticket may implement this while it reads OPEN.**

The request is that the client be able to change a requirement or ticket from the screen. This
contradicts S2 and inverts `SPEC.md` §1 exclusion 2 verbatim, so it enters here rather than as a
ticket. What has to be decided is not whether editing is useful — it is whether the exclusion's
stated reason survives a narrower shape.

The exclusion's reason is **racing**, not storage: "a UI that edits them would be a second writer
racing the first, and the state model only survives if there is exactly one writer." That admits
three shapes, and they are not equally costly:

1. **Through the agent.** The client sends a message; the agent in the lane edits the document.
   No new writer, contradicts nothing, and `E14` already exists. This is available today and covers
   every ticket that has a lane.
2. **Direct write when no lane holds the document.** The tool becomes a writer, but not a
   concurrent one — the lane is the lock that already exists, and a document no lane holds has
   exactly one other writer, the human's editor. This is the shape that could survive S2, and it
   costs S2 a clause: *the tool writes a document only while no lane holds it.*
3. **Direct write, unconditional.** A second writer racing the first. This is what the exclusion
   refuses, and nothing here changes its reasoning.

Open question for the decision: shape 2 makes the tool a writer of the repository's state, which is
the property S2 was written to deny outright. Whether the lane is a sufficient lock depends on
whether a human's editor is a writer the tool can be sure about — and it is not. That is the
argument to answer before this becomes a decision.

### D3 · A PASS verdict lands the work — 2026-09-07, settled

`SPEC.md` §1 exclusion 3 left merging to the forge, and `CLAUDE.md` already said a passed factory
ticket merges to `main` directly because the QA session IS the gate. The two were reconciled by a
person typing `git merge --squash`, and on 2026-09-07 that person was the only thing between a
PASS verdict and a merged ticket.

**Decision: the verdict is the trigger.** When a verdict file that says PASS lands on a ticket's
branch, the tool squash-merges that branch to `main` with the author derived from the branch's
non-merge commits and every other author as a trailer, pushes `main`, and reaps the branch and the
worktree. It does this on its own account, never a lane's. There is still no merge button: nothing
a person clicks and nothing an agent writes causes a merge, except a verdict written by a session
that did not build the work — which is the gate the workflow already names.

Exclusion 3 narrows to what it was protecting: the tool opens no pull request, reviews nothing,
and judges nothing. The merge it performs is the one its own gate decided. A FAIL verdict merges
nothing; a verdict on a branch whose `main` has moved is merged INTO first and refused if that
conflicts, so a conflict is a `needs you` and never a broken `main`.

Why this is not a document write under S2: the squash writes commits and refs, not files under the
document root; the documents on `main` afterwards are byte-identical to the ones the QA session
committed on the branch. The tool still authors no requirement, ticket, report or verdict.

Two consequences, both deliberate. Lanes lose the forge credential entirely — the tool pushes, a
lane does not — which closes the S4 hole measured the same day. And "one commit per ticket reaches
`main`" becomes a property the tool holds rather than a rule a person follows.

### D4 · The deployment is the operator's checkout on `main`, and a landing is a fast-forward of it — 2026-09-07, settled

`CLAUDE.md` tells the operator to run the tool against the repository they are working in. So the
document root the tool serves is a checkout on `main` that a person is editing, and a landing under
D3 is, from that person's side, a change to their own working tree. Three rounds of `SRV-FEAT-437`
each answered that with a different mechanism and none of them with a rule; S7 is the rule.

**Decision.** A landing moves the operator's checkout only by `git merge --ff-only` run in it, after
the squash commit exists and the remote has it. If git refuses because local changes would be
overwritten, the landing is refused as `needs_you` naming those paths; `main`'s ref, the remote, the
branch and the lane worktree are all left exactly as they were. An operator with unrelated edits in
flight is not blocked, because a fast-forward that touches none of their files succeeds. An operator
editing a file the ticket also changed is asked, once, to commit or set aside their edit. The
alternative — the tool serving a checkout of its own so that `main` is never checked out by a person
— is the better shape and is deferred to the version that makes the tool remote (v0.2), where the
document root stops being a person's working directory.

---

### D5 · Finite local command routing returns judgment to main — 2026-10-04, settled

SRV-FEAT-565 adds a provider-neutral controller for explicitly submitted command plans. It advances
only on complete persisted terminal evidence for unchanged Git-visible inputs and a declared exit
policy. Reuse is opt-in because ignored outputs and external dependencies are outside the identity.
The controller uses existing lane confinement and exclusive resources; journals are runtime state
under a manifest-owned path. Restart stops uncertain dispatches for reconciliation rather than
replaying effects. Ambiguous decisions return to main, and repeated identical failures produce a
stable request for advisor review. Neither a command success nor that checkpoint is QA, acceptance,
a provider session or authority to land. S1–S7 and the independent verdict boundary remain intact.

## 5. How a ticket uses this

- **§1 cites, it does not restate.** A ticket that needs a boundary writes `S3` and the invariant's
  name. It does not paraphrase the invariant, because a paraphrase is a re-derivation and a
  re-derivation is how `SRV-FIX-293` came to assert `cd factory/server` when the manifest says
  `cd server`.
- **`seams:` carries both kinds of citation** — the seam ids it may not contradict, and the
  endpoint ids it touches. `seams: S3, S4, E12` reads correctly.
- **A ticket that must contradict a seam stops.** It is not downgraded, argued around, or scoped
  small enough to slip past. It becomes a proposed change in §4, with the reasoning the exclusion
  it inverts was written with, and the work waits for a decision.
- **An escape that traces to a seam names it.** `docs/operations/ESCAPES.md` gains the seam id in the row, so
  the fourth generation of one bug is visible as the fourth rather than as a new one.
