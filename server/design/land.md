# `app/land` — the tool lands a passed branch

An independent role session can leave its verdict at a completed turn without exiting. Its
tool-owned run record carries the committed branch tip while its outcome remains `running`;
the same recorded-session PASS check below decides whether that tip can land. A live lane process
keeps its checkout and branch through the reap step so steering remains possible.

`docs/technical/MODEL.md` §4 **D3**, settled 2026-09-07. Until then the last step of a ticket was a person
typing `git merge --squash`, and on 2026-09-07 that person was the only thing between a PASS
verdict and a merged ticket: `SRV-FIX-432`'s QA lane wrote its verdict, set `status: pass`, and
could go no further, because `main` is checked out in the factory's own root and a lane cannot
check it out a second time.

This module is the only place in the tool that runs a git subcommand which writes to the
repository it was given. `app/delivery` stays read-only — its `READ_ONLY` set, and
`test_delivery.py::test_only_read_only_git_subcommands_are_used`, are untouched by this module's
existence, and that separation is the point: the module that decides *what became of the work* and
the module that *moves a ref* are different modules with different allowlists.

## Why this is not a document write

`docs/technical/MODEL.md` §2 S2 says the tool writes none of the target repository's documents. It does not
write one here. Every byte the squash commit carries was written by a lane's agent and committed
on the branch; the tool moves a ref over commits that already exist. `test_land.py`'s first case
asserts that as a property — the tree on `main` afterwards is the branch's tree **byte for byte** —
so a code path that opened a document for writing would fail it.

## The allowlist, and what is deliberately not in it

```
writes : merge  commit  push  branch  worktree
reads  : rev-parse  rev-list  log  ls-tree  cat-file  for-each-ref  status  diff  merge-base
```

`land::git::run()` refuses anything outside it **by name, before a process is spawned**, exactly as
`delivery::git()` does. Two absences are decisions rather than oversights:

- **`fetch`.** The ticket pre-decided the write set and `fetch` was not in it, so the tool merges
  the remote-tracking ref as the delivery probe already sees it and never moves a remote-tracking
  ref of its own accord. A stale `origin/main` therefore shows up as a push that fails, which is a
  refusal, and never as a landing that quietly used the wrong base.
- **`reset`.** `merge --abort` is the whole of the recovery. `reset --hard` would be the obvious
  belt-and-braces, and it is also the one command in git that can destroy work that was never
  committed — which is precisely what this module's mandatory negative exists to prevent. The one
  path that could strand a staged squash (a `commit` that fails after a `merge --squash` that
  succeeded) is unreachable while the base is merged into the branch first, and is reported as a
  refusal naming the root rather than cleaned up by a command that can lose a person's work.

Every invocation is logged whole, before it runs:

```
land: ran ["git","--no-optional-locks","-C","<cwd>","merge","--squash","ticket/T-1"]
```

That single line is what makes *"no invocation ever carries `--force`"* a property somebody can
check by sweeping the product's own output, instead of a list of commands a session typed out and
believed.

## PASS is one question, asked in one place

`Lander::attempt_` is the only place the question is asked, and both halves are read **from the
branch that carries the round's last word** — never from the working tree, never from `main`:

1. the verdict is the **latest round** on that branch: `<reports>/<id>-qa-round<N>.md` with the
   highest integer `N`, else `<reports>/<id>-qa.md` — one path, chosen by a rule, never the first
   entry of a glob;
2. the first `#` heading in it names exactly one of `PASS` and `FAIL` — `PASS WITH MINORS` counts,
   and a heading naming both decides nothing;
3. `cat-file` the ticket document on the same branch: its frontmatter must say `status: pass`.

Steps 1 and 2 were each a loose match once, and each landed work a QA session had refused. §1
wrote the verdict as `<docs.reports>/<id>-qa*.md`, which reads like one document and is a **set**;
the first version took the first entry `ls-tree` returned, and git sorts `-` (0x2D) before `.`
(0x2E), so a kept `<id>-qa-round1.md` was opened and the live `<id>-qa.md` never was. A FAIL landed
on one side of that and a genuine pass was refused on the other. The verdict is now the single path
the `qa-ticket` skill writes and `catalog/render.cpp:665` already reads, and a `<id>-qa*.md` that
is *not* that path is named in the refusal as an earlier round rather than silently promoted.

`SRV-FIX-607` made the one path the **latest round's**. The kit's `qa-ticket` skill writes round 1
to `<id>-qa.md` and round `N` to `<id>-qa-round<N>.md`, keeping every earlier round, and the kit's
plan tool reads the highest round as the ticket's evidence; a lander that opened only `<id>-qa.md`
read a round-2 PASS as round 1's FAIL, and every ticket that passed in round 2 or later in a product
repository was landed by hand. The rule, `Lander::verdictPath_(request, ref)`:

- the candidates are the files in `<reports>` on that ref named exactly `<id>-qa.md` or
  `<id>-qa-round<N>.md` with `N` all digits; anything else (`-qa-round1-FAIL.md`, `-qa2.md`) is not
  a verdict and is named in the refusal when no verdict exists;
- `<id>-qa.md` is round 1 — that is the kit's naming — so the highest `N` wins and rounds compare
  as **integers**, not text (`round10` after `round9`);
- `<id>-qa-round1.md` does not outrank `<id>-qa.md`: it names the same round, and this repository's
  own older convention keeps the live verdict in `<id>-qa.md` with round 1 archived beside it, so
  the tie goes to `<id>-qa.md` (`test_a_kept_earlier_round_never_decides_for_the_live_verdict`).

The path is chosen **per ref**, because two refs can hold different rounds: the role branches that
take part in the descendant decision are the ones that carry *a* verdict by this rule, and the
receipt rule asks whether a recorded session's tip carries **that** path with a PASS. A later round
written by the run session itself is therefore read, and refused as before — *a ref that carries a
document is not a session*. Reading the latest round makes a refusal neither weaker nor stronger:
a later FAIL is never overridden by an earlier PASS.

This repository's own historical archive shape — the live verdict moved into `<id>-qa.md` and
rounds 1…N-1 renamed to `<id>-qa-round<k>.md` — is no longer what the lander reads when `k ≥ 2`
exists beside it: it would read round N-1. Every ticket in that shape has landed; a new one would be
refused rather than mis-landed, because the archived round is a FAIL or was not left by a session.

Step 2 was a bare `find("PASS")`. This repository really writes
`# <id> — QA verdict — round 1 FAIL · round 2 **PASS**` — `docs/reports/SRV-FIX-131-qa.md` and
`SRV-FIX-293-qa.md` — so the multi-round heading is its own convention, and the same convention
runs the other way as often as a ticket fails its second round. The inverse of those two headings
landed a FAIL. Reading the LAST word instead of the first would only move the guess: a heading
reading *FAIL (an earlier round said PASS)* is wrong the other way. **A heading naming both is not
a decision the tool may take**, so it refuses and prints the heading for a person to settle. Two
real documents in this repository stop auto-landing because of that, which is the price, and it is
paid in a `needs you` rather than in a merge.

Reading from the branch is not a detail. The state after a landing is *verdict and `status: pass`
on `main`, and a branch that carries neither* — so a tool that asked `main` would re-fire on every
ticket it had ever landed, forever. `test_land.py` case 4(b) is that state, and it is a refusal.

## Which branch is asked — the descendant, not the run branch

`SRV-FIX-475`. The first version asked `ticket/<id>` and only `ticket/<id>`, and a QA lane never
writes there. The verdict is written in the QA lane, on the QA lane's own branch; the run branch is
checked out in the run lane's worktree, so `git branch -f ticket/<id>` and
`git fetch origin ticket/<id>:ticket/<id>` are both refused by git, `S4` confines the lane to its
own worktree so it cannot `merge --ff-only` inside the run lane's checkout either, and a push to
`origin/ticket/<id>` moves nothing this module reads — it resolves local refs in `root_`. The only
actors who can move the run branch are the run lane's own session, which has stopped, and a person.
On 2026-09-08, the first day this module ran against real QA output, that person was the operator,
three times in four minutes, for `SRV-FIX-443`, `SRV-FIX-470` and `WEB-FEAT-366`.

So the question is asked of **the descendant**:

1. collect `ticket/<id>`, always, and every `ticket/<id>-<role>` for a role in the request whose
   tip carries `<reports>/<id>-qa.md`;
2. if one of them contains every other (`merge-base --is-ancestor`), read the heading and the
   `status:` from **that** tip and land **that** tip — its tree is the run branch's tree plus the
   verdict, which is what `main` should hold, and the refusals name the branch they were read from;
3. if two of them have diverged — neither contains the other — land nothing and log both tips by
   name: `<a> (<sha>) and <b> (<sha>) have diverged; merge one into the other`.

Divergence is refused and never resolved: merging two branches is a decision with a conflict
surface, and `S2`/`S7` say the tool edits no tree it did not create. The log line is the remedy,
and `WEB-FEAT-366` is the shape — its run lane committed one report fix after QA had branched.

The examination fingerprint carries every candidate branch's tip, not just the run branch's, or a
verdict appearing on a role branch that never moves the run branch would change nothing the tool
compares and would be examined once, at the next restart. Reaping is unchanged: `request.branch`
and its checkout are reaped by the existing rules, on the tree that reached `main`.

## The separate-session guard — the tool's own record, not the shape of a ref

D3's gate is *a verdict written by a session that did not build the work*. This guard was wrong
three times in two rounds, and every one of those was the same mistake in a different namespace:
it asked **does another ref carry this document** where §1 asks **did another session write it**.

- Round 1 accepted any ref but `refs/heads/<branch>`, so `refs/remotes/origin/ticket/<id>` — the
  run branch's own mirror, made by `git push` — was read as somebody else. Two concurrent QA
  sessions found that independently.
- Round 2 narrowed it to a local branch named `<run branch>-<something>` at a different tip. That
  was wrong in **both** directions at once: `git branch ticket/<id>-keep <tip>~1` still walked in,
  and a role branch merged back with git's DEFAULT fast-forward stands at the run branch's own tip,
  so a verdict a real QA session wrote stopped landing.

Refs are not evidence. A lane can make, move, name and push any ref it likes, and so can a person;
no arrangement of them distinguishes *a second session ran* from *the same session typed another
command*. What does distinguish it is the tool's own record of the sessions it started.

Git command capture pipes use close-on-exec descriptors. A long-lived role process must not
inherit a writer from a concurrent delivery command and hold that command's read open.

**The rule.** A verdict is separately written when **both** hold:

1. `.factory/runs/*/meta.json` carries a run whose `ticket_id` is this ticket and whose `role` is
   not `run` — the ledger `agent::history::write` fills in from the control plane every time the
   tool dispatches a session, and which survives the lane's release; and
2. the branch that role's lane was given — `lane::Store::branchFor_`'s deterministic
   `ticket/<id>-<role>` — either **contains the commit that wrote the verdict** on the run branch
   (`merge-base --is-ancestor`) or carries `<reports>/<id>-qa.md` at its own tip.

Clause 2 is deliberately satisfied by either reading, because a verdict document moves between
rounds: `ticket/SRV-FIX-379-qa` really holds round 2's FAIL while `ticket/SRV-FIX-379` holds the
later PASS — **a person put it there**, by fast-forwarding the run branch inside the run lane's
worktree, which is the manual step `SRV-FIX-475` removed. Ref *equality* appears nowhere in it,
which is what makes the fast-forward case ordinary rather than special.

**Why this one cannot be walked around the way the last two were.** A lane is confined to its own
worktree; `<root>/.factory/runs` is outside it, and `test_isolation.py::test_write_outside_lane_is_refused`
is the case that says so. So a run record is a fact about what the TOOL did, and a session cannot
write one about itself. `git push`, `git branch` and `git merge` — the three commands that defeated
the previous two rules — cannot produce one.

**And when there is no record, nothing lands.** A verdict written outside any lane the tool started
is refused with *"no role session recorded for `<id>`; a QA done outside the tool is landed by a
person"*. That is the honest answer rather than a guess: on a fresh clone `.factory/runs/` is empty
— it is gitignored, machine-local, the same hazard `server/CLAUDE.md` records for `.factory/ids.json`
— so the tool lands on the machine whose own records show it dispatched the review, and nowhere
else. `api` reads the ledger and hands `land` the role names; `land` reaches into no sibling.

## Why the tool lands at all, and what a hand push around it costs

`SRV-FIX-470`. The rule this module implements — one commit per ticket reaches `main`, a squash,
never a merge commit and never the branch's own history — was written in three places before this
module existed: `CLAUDE.md`, `.claude/skills/qa-ticket/SKILL.md` and this file. On 2026-09-08
`WEB-FIX-370`'s QA lane read the skill, wrote *"PASS WITH MINORS, merged to `main`"*, and ran

    git merge origin/main --no-edit
    git merge-base --is-ancestor origin/main HEAD && git push origin HEAD:main

Nine round commits and a merge commit reached `main` — including `WEB-FIX-370: status pass — set
by QA`, which is not a change to the product at all. Three other tickets landed correctly the same
day, which is the point: a rule followed when a session chooses to follow it is unenforced, and
what makes this module different from a paragraph is that it does not choose.

`--is-ancestor` is the trap, and it is worth naming because it looks like a check. It answers *can
this push fast-forward the remote*, a question about whether git will refuse; the rule is about
**what shape arrives**, and no arrangement of that question answers it. The two agree exactly as
often as the branch happens to already be one commit.

The cost is permanent and it lands on readers rather than on the session that saved the minute.
`git log --oneline main` is read to answer *what changed and why*, one line per ticket; the range
now carries nine for one ticket. `test_authorship.py` derives the squash author over
`main..<branch>` with `--no-merges`, and a merge commit ON `main` is a shape it was never written
for. And `origin/main` is shared, so the history cannot be repaired without a force-push that is
the operator's decision and nobody else's — which is why `server/tests/test_main_history.py`
ratchets from `efefdc60` and refuses the next one instead of rewriting the first.

The remedy is this module and not a stronger sentence. A session's part ends at *verdict and
`status: pass` committed on the branch, branch pushed*; the lander does the rest, in scratch
worktrees, or refuses with a reason. Where it refuses — no run record on this machine, a heading
naming both verdicts, a holder of the base with overlapping changes — the answer is a person, and
`needs you` is the honest form of that answer. It is never a `git push … HEAD:main`.

## The order, and why nothing happens in anybody's checkout

1. trigger, PASS, and the guard — reads only;
2. **merge the remote-tracking `<base>` into the branch's commit, in a scratch worktree** the
   lander creates detached and removes itself;
3. derive the author from `log --no-merges` over `<base>..<merged>`, most frequent wins;
4. **`merge --squash` and `commit`, in a second scratch worktree** detached at `<base>`'s tip;
5. move `<base>` to the new commit;
6. `push`;
7. reap.

Step 2 is what makes step 4 exact. Once the base is an ancestor, the squash is the branch's tree and
nothing else — the property case 1 asserts. Step 4 uses `log --no-merges`, and step 2 is why that
flag is load-bearing: the tool's own catch-up merge lands in the range the author is counted over,
which is `docs/operations/ESCAPES.md` 2026-09-04 (`SRV-FIX-376` → `SRV-FIX-378`) reaching this code.

**Both merges happen in scratch worktrees, and that is the whole of round 3's second fix.** The
first version merged into the branch's own lane checkout and squashed inside the operator's primary
checkout. A squash conflicts whenever `main` holds something the branch does not — which is every
time `main` has moved locally and not been pushed — and `git merge --squash` leaves the conflict
**in the working tree it ran in**: a staged half-merge and `UU` markers in the operator's own files.
The tool then refused every later landing because that checkout was dirty, so **one conflict jammed
the queue** until a person repaired it by hand. That is a broken `main` in everything but name, and
D3 forbids it.

### A scratch outlives no attempt (`SRV-FIX-518`)

**A scratch the tool made is removed after every outcome of the step it was made for, with the
force that outcome needs.** The sentence has to say *every outcome* because the outcome that leaves
one behind is the one nobody writes a line for: `git worktree remove` refuses a checkout holding
modified or untracked files, and a merge stopped in conflict is exactly that. `closeScratch_` ran
unforced, so a conflicting merge-in left the scratch, its conflict markers and its worktree
registration on disk, and `openScratch_` refused a path that already existed — the scratch path is
`<temp>/factory-land-<id>-<purpose>-<pid>-<tip[0:12]>` and the branch's tip does not move while it
waits, so the next attempt wanted the same path byte for byte and got
`no scratch checkout could be made to merge main into ticket/<id>`. That names neither the conflict
nor the files in it. On 2026-09-11 four tickets sat behind that sentence for four and a half hours
while the one line that said `main will not merge into ticket/SRV-FEAT-498: server/tests/test_spec.py`
had scrolled two hours up the log.

**Forcing is inside `S7` here, and only here, because of where the path comes from.** `Scratch::path`
is non-empty only on the success branch of `openScratch_`, which is the `worktree add` this process
ran three lines above; it is never a path a person named and never a path the tool merely found. A
directory that merely STANDS at the path the tool would have chosen is a working tree the tool did
not create, whatever its name looks like — so `openScratch_` asks `git worktree list --porcelain`
whether that path is a registered worktree of this repository at a `detached` head (every worktree
this module makes is `--detach`ed, so a record carrying `branch` is somebody's checkout), forces the
removal only then, `prune`s the registration behind it, and otherwise refuses the attempt NAMING the
path and touches nothing. `prune` is safe to run on every close and is run there: it deletes
registrations whose directory is already gone and never a working tree, which is the only thing
that clears the ten `prunable` records a previous server process left among the eight scratches.
Both refusals reach `E5` through the caller, which is also the only place either is logged — the
caller's line carries the ticket id and `openScratch_`'s would not. `reap_` is the other side of the same rule and keeps its refusal: a lane's
checkout holds somebody's work, and `--force` there would be the landing losing it.

**And an attempt is asserted twice — the second is the one that meets what the first left behind.**
`SRV-FEAT-437` criterion 3 asserted everything a conflicting landing may not damage: `main`, the
remote, the branch, its worktree, `E5`. The scratch is the tool's own and is named in none of that,
and the case examined the tip once, so the defect lived entirely in the sentence the SECOND attempt
produced. The class is *a check about what an operation must not damage that never asks what the
operation itself left behind*. The lander memoises a tip it has examined, so the second attempt is
provoked the way the real world provokes it — the base moves while the conflict stands — and both
attempts must name the same conflicting paths.

Now: nothing the tool does can leave a mark on a checkout it did not create. On any failure the
scratch worktree is removed and `<base>`'s tip, tree and index, and the operator's checkout, are
byte-identical to what they were. **The branch's own ref is never moved either** — the merge-in
happens on a detached commit, so a lane's branch is exactly where its session left it, and criterion
3's *"the branch's own tip is unchanged"* is structurally true instead of being restored by an
abort.

**Moving the base is `S7`, and `S7` is the reason three rounds failed.** The deployment `D4`
describes is a checkout on `main` with a person mid-edit in it, and the tool serves that checkout.
So a landing is, from their side, a change to their own working tree, and the rule is:

> the tool never edits a working tree it did not create, and never moves a ref such a tree has
> checked out unless the tree moves with it.

Which tree holds `<base>` is read from `git worktree list --porcelain` and from nothing else. The
`branch` line answers it exactly: a record reading `branch refs/heads/<base>` is a checkout holding
the base, and since every worktree this module creates is `--detach`ed, such a record is always one
the tool did not create. Round 3 asked `rev-parse --abbrev-ref HEAD` in `root_` — **one directory**
— which in this very repository prints `ticket/SRV-FEAT-437` while `refs/heads/main` is held by a
worktree at `/…/.git/modules/factory`. That is not a corner case; it is the factory's own layout,
and it made round 3's `update-ref` fire with no dirty checkout needed at all.

So: **nothing holds the base** → `update-ref <new> <old>`, a compare-and-swap, which is S7's own
carve-out. **A tree holds it** → `git merge --ff-only <new>` run inside that tree, and **there is no
fallback**. A fallback is what round 3 was: when the permitted mechanism declined, it did the
forbidden one instead. `update-ref` cannot be made safe under a holder by any ordering, because
every command that could bring that tree's index forward — `checkout`, `reset`, `read-tree` — is
deliberately absent from the allowlist, and adding one would itself be the act S7 forbids.

**The order, and why the pre-flight exists.** `D4` says the fast-forward happens *"after the squash
commit exists and the remote has it"* and also that when git refuses *"`main`'s ref, the remote, the
branch and the lane worktree are all left exactly as they were"*. Those two sentences are not
jointly satisfiable by attempting the fast-forward after the push: a refusal then has already moved
the remote. So the order is **squash → pre-flight → push → move → reap**, and the pre-flight is what
makes the second sentence true.

The pre-flight intersects the paths the landing changes with everything `status` reports in the
holder — modified, untracked **and ignored**. It is deliberately **stricter than git**: it can only
refuse where git would have refused, never permit where git would not, so it cannot become the
"model kinder than the boundary" that passes what the system refuses. The real `merge --ff-only`
still runs and its refusal is still authoritative — the paths in the message are the ones git named,
read off its own tab-indented output. Including *ignored* paths is the one place the pre-flight is
stricter than git on purpose: git overwrites an ignored file during a fast-forward without
complaining, so an overlap there is work lost that `--ff-only` would never have refused.

**Reaping is authorised by position in that order**, never by a tree comparison — the comparison the
`update-ref` had made vacuously true. And a checkout is removed only on **provenance**: a path under
the manifest's own worktrees directory, or one a lane record names. Cleanliness was the wrong test;
a person's hand-made worktree on a ticket branch, left tidy, was being deleted. Provenance has to be
structural rather than a record, because a lane record is gone the moment the lane is released —
the same trap the separate-session guard fell into in round 1.

`update-ref` is a sixth write and the ticket pre-decided five. It is named here because it is the
only way to move a ref with no working tree attached, and that is now the only case it is used in.
`git branch -f` refuses a branch that is checked out, which is exactly the case this exists for.

## Reaping, which is the half that can lose work

```
reap  ⇔  <merged>^{tree} == <base>^{tree}  ∧  <branch> is an ancestor of <merged>
       ∧ the checkout is absent, or `status --porcelain` AND `status --ignored` are both empty
       ∧ no live lane process is running in that checkout
```

The comparison is against the commit that was actually squashed rather than the branch's ref,
because the merge-in now happens on a detached commit and the branch's ref does not move. The two
readings coincide whenever the base was already an ancestor, which is what every earlier case
measured; where they differ, the ancestry clause is the one that answers the real question — *does
`<base>` now contain everything this branch had*.

The tree comparison, not `merge-base --is-ancestor`, is the test: a squash makes none of the
branch's commits an ancestor of `main`, so ancestry answers *no* for every branch this module ever
lands and would delete nothing. Equally, "it landed, therefore delete it" is the answer that loses
work the moment `main` carries something the branch does not — the branch is then the only ref that
holds its own tree, and it is kept.

The checkout goes first and the branch second, because git refuses to delete a branch that is
checked out somewhere. So a checkout kept for either half of `E13`'s cleanliness rule keeps its
branch too, and the log says which half refused.

The third clause is `CLAUDE.md`'s lane rule, which outranks convenience: *never remove a lane
checkout without asking the tool whether it owns a live lane there* — the 2026-08-06 incident where
a survey of four "verified empty" worktrees removed one that was 410 s into a run. `land` cannot
ask; `api` asks `lane::Store` and passes the answer down. Nothing here releases a lane: `E13`
remains the only way (S5).

## Threading

`Lander` owns a `core::Queue` and every piece of its state lives on it — the refusal map, the
per-ticket fingerprints, the pending queue. The git work itself is a chain of child processes and
is offloaded with `queue_.offload`, so no handler thread is ever blocked and no mutex exists. One
landing runs at a time: the completion of each attempt starts the next.

The fingerprint contains every candidate tip, the base and remote base tips, the declared roles,
the source checkout idle state, and the independent session receipt fields: run ID, role, branch
and tip. Receipt and role sets are sorted and deduplicated; receipt fields are length-prefixed.
Duration, costs and other unrelated metadata do not invalidate a refusal. A receipt saved after
the verdict commit must change the fingerprint even when all Git refs remain unchanged.
Unchanged acceptance inputs still suppress repeated examinations and merge/push work.

A second record, the tip a ticket was last landed at, stops the churn the fingerprint alone leaves
behind. A branch that landed and was then KEPT — because its checkout is dirty, which is E13's
rule and not this module's to override — still has a PASS verdict on it, so every later move of
`main` made it landable again: another merge of `main` into somebody's branch, another squash that
committed nothing, and another push, forever. `test_land.py`'s criterion 5 found it by having its
own fixture destroyed — the re-attempt pushed a `main` the fixture needed to keep unpushed. The
record is the branch's tip, not a flag, so a NEW commit on that branch is a new round and is landed
again; only the tip that already landed is skipped. And a landing that committed nothing pushes
nothing: the tool pushes what it landed, never a person's unrelated local commits.

Refusals are published as an immutable `shared_ptr<const Refusals>` on the same strand, and `api`
fetches it beside the delivery snapshot. That is how `E5` says `needs_you` with the conflicting
paths in `detail` without `delivery` ever learning that this module exists.

## A landing is remembered by the tip it squashed (`SRV-FIX-519`)

The record above is what makes the module idempotent, and the shape it is stated in is the whole of
it: **a landing is remembered by the TIP it squashed, and a kept branch at that tip is finished
work rather than a candidate.** The examination stops there — before any scratch is opened and
before `main` is merged into anything — so the second look costs four `rev-parse` calls and nothing
else.

It is not a tree comparison. `reap_` may use one because two trees being equal is exactly the fact
that authorises a delete; an EXAMINATION decided that way would call a branch a person squashed by
hand *landed*, and the tool would then never look at it again, so a verdict that arrived later
could never be acted on. Landed is the tool's own record of the squash it made.

The consequence the human has to decide, written here because nothing else records it: every
`factory-server` lane holds `.cache/server/build`, `E13`'s reap rule keeps any checkout whose
`git status --ignored` is non-empty, and therefore **no server lane's checkout is ever reaped by
the tool** and every landed server branch is kept. That is why the state this record exists for is
the ordinary one and not an edge. Whether ignored build output should keep a checkout is not
decided here; a ticket that changes the reap rule is its own.

## The verdict a landing rests on is a session's, not a ref's (`SRV-FIX-450`)

Refs cannot carry authorship. A run lane can create `ticket/<id>-qa` at its own tip with one
`git branch`, and as a graph that is **indistinguishable** from the legitimate fast-forward shape:
in both, the role branch is exactly the run branch's tip. So no question about refs can decide
whether a second session passed this ticket, and every ratchet that tried moved the hole rather
than closing it — the namespace, then the run record, then the document test underneath both.

The boundary is `.factory/runs/`, which the control plane writes and no lane can forge. Every run
record now carries `branch` and `branch_tip`: the ref that session held and the tip it left it at,
filled by `api` from `lane::Store` when the run ends (`api` composes; `agent` does not reach
sideways into `lane`). A landing rests on a recorded role session whose tip is an ancestor of the
branch being read and whose OWN copy of the verdict document **says PASS**. A stale role branch
carrying an earlier round's FAIL therefore validates nothing, and the refusal names the session
that is missing rather than the ref that was found.

It is the session's VERDICT that is read, not its bytes. A round-2 verdict lives on the run branch
while the role branch still holds round 1's — `ticket/SRV-FIX-379-qa` held a FAIL of 18304 bytes
while `ticket/SRV-FIX-379` held the later PASS of 21710 — so a guard asking whether the same BLOB
is on a second ref would call that state a self-pass and refuse a real ticket for ever. What a
landing may not do is DISAGREE with the session that was recorded.

## The squash author is the WORK's (`SRV-FIX-477`, `SRV-FIX-425`)

`git log` prints newest first and the old tally replaced its incumbent only on a strict majority,
so a tie went to whoever committed last — and on every ticket that is the QA session, which writes
the verdict by construction. `--no-merges` had already removed the bookkeeping shaped like a MERGE
and left the bookkeeping shaped like a DOCUMENT.

So the tally is taken over the WORK. A commit is the VERDICT, not the work, when every path it
changes is the ticket document itself or a file whose basename begins `<id>-qa`; the category is
stated that way rather than by a list of filenames, and a commit whose changed paths cannot be read
at all — a merge — is work, so `--no-merges` still decides those. If the branch is nothing but
verdict commits the tally falls back to all of them, because a branch with no work still has an
author. A tie among the work goes to whoever committed EARLIEST — the branch's own first author —
never to whoever committed most recently. Trailers are unchanged: every other non-merge author on
the branch is a `Co-Authored-By:`, the grader included.

`qa-ticket/SKILL.md`'s fenced `AUTHOR=` block performs the identical derivation, and
`test_authorship.py` RUNS that block rather than a copy of it — under this rule the pipeline is the
subject, and a Python model of it carrying the fix would grade the model.


## Receipt-only reconsideration (SRV-FIX-571)

API composition subscribes to each completed delivery snapshot, including snapshots whose Git
fingerprint is unchanged, and supplies current tool-owned role-session receipts. The existing
bounded scan interval therefore sees late completion receipts without a new poller, public API,
source commit or restart. Git-change subscriptions keep their original semantics. API still
coalesces identical socket delivery bodies, and the lander's acceptance fingerprint suppresses
unchanged refusals. Existing kept-candidate idempotence remains separate from this cache.
No receipt invalidation grants acceptance: the deciding branch must still carry PASS and the
independent session's own committed verdict at its recorded tip must also say PASS.
