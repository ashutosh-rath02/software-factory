---
name: qa-ticket
description: QA a factory ticket in a SEPARATE session from the one that built it — re-run every §1 criterion yourself, default to refuse, write the verdict, and on PASS merge to main. Use for "/qa-ticket <path>", "QA ticket X", or whenever an implementing session has finished its GATE stage.
---

# QA a factory ticket

You did not build this. That is the point.

The session that implemented a ticket checks the cases it thought of — which are the same cases it
coded for. The case it never imagined is exactly the one that ships. So QA runs in its own session,
with its own context, and reaches its own verdict.

**You arrive at a ticket whose every §1 criterion the implementing session has already run to
green.** That is now the condition of the handover, not a courtesy: validation is theirs, and
verification — independent, cold, defaulting to refuse — is yours. So a criterion you find unrun,
red, or quietly BLOCKED without a filed ticket is a **finding about the handover** and is written up
as one, before anything else. You still re-run every criterion yourself; being told it is green is
not evidence that it is.

**Every path here is relative to this repository — the factory.** One project, one `docs/` tree.

## The one rule that makes this work

**Read §1 of the ticket and the repository. Nothing else.**

Do **not** read, and do not let anyone paste you:

- `docs/reports/<ticket-id>-report.md` — the implementer's account of their own work
- their chat summary, their explanation of why something is acceptable
- the diff as a narrative ("here's what I changed and why")

You may read the code, the tests and the logs as *evidence*, never as *argument*. A narrative tells
you where to look, which is exactly what a fresh checker must not be told. If you have already been
given one, say so in the verdict — it is a limitation of that QA run.

Read the report only **after** your verdict is written, and only to see whether you disagree. If you
do, that disagreement is the most valuable thing on the ticket.

## QA'ing a batch

You may be handed **several tickets at once**, over one branch that already carries them integrated.
Everything below applies unchanged, with three additions:

- **Copy every ticket's §1 criteria, and keep them separate.** The verdict is per ticket. One blob
  verdict makes a batch unshippable the moment anything is wrong with any part of it.
- **Spend the break attempt on the seams between them.** The defects that reach a batch are the ones
  no single branch could see: a field two tickets widened to mean different things, a test one added
  that rejects what another still writes.
- **A criterion that fails only in the union is a finding against the union**, not against whichever
  ticket you happened to be reading. File it as its own `FIX` and say which two tickets combine to
  produce it.

## Token discipline (SRV-FIX-580) — every call re-sends the whole context

Measured on 2026-10-10: one QA session made 258 model calls at a median context of
135k tokens (35M input); one call loaded ~40 screenshots, which then rode along in every later call. A call costs the context, not its output, so the rule is **fewer calls**:

- **Read a file once, whole, in one call** — batch several files into one command. Never page a
  file in 50–200 line chunks across calls, and never re-read a file you have not changed.
- **§2 is `docs/tickets/_TEMPLATE.md` §2.** Read it once per session; the ticket carries a pointer.
- **Do not re-run a test whose inputs did not change** since its last captured result — quote
  the capture. Run the narrowest selection (`pytest file::case`) while iterating.
- **The workspace gate runs once per GATE round** (`tools/tier_gate.py`). Wait for a detached gate
  with ONE blocking call — `timeout 2400 sh -c 'until grep -q GATE_EXIT= <log>; do sleep 30; done'`
  — never a poll per turn.
- **Never open screenshots to judge them.** The browser gate's pixel comparison decides; open one
  image only when it names a drift a person must look at.
- **Write evidence to files and quote the decisive line**, not whole logs, into the conversation.
- **Cost bounds (`WORKFLOW.md` § Cost bounds).** This is at most the ticket's second QA session; if
  a second one would still FAIL, say so and stop — the operator decides, not a third round.
- **What a lane does not have — do not try, do not retry.** Measured 2026-10-10: 22 tool calls
  failed on these. `/tmp` is read-only (write under `$TMPDIR`, which is `/tmp/factory`); the git
  common directory is read-only (no `git worktree add`, no new refs outside `ticket/`); there is no
  GitHub credential (never `git push` — the tool lands the local branch); there is no provider login
  (live-agent cases are the operator's host run, see SRV-FEAT-574 §1); Chrome, when bound, is at
  `CHROME_PATH=/opt/google/chrome/chrome`. A command that fails for an environment reason is
  recorded once with its error and never re-run.

## Procedure

1. **Check the handover before you copy anything.** From this repository's root:

   ```bash
   server/tools/handover.py docs/tickets/<file>.md
   ```

   It prints one row per §1 criterion — the case, whether it exists, the log it was RED in, the log
   it is GREEN in — and exits non-zero naming each miss. **A red row is a finding about the
   handover, written up as one before you re-run a single criterion**: a case that exists nowhere,
   or one no committed log ever shows failing, is the first blocker of most FAIL verdicts this
   repository has written. `manual: N` tells you how much of the ticket no program can check, which
   is the part that is entirely yours.

   Then **copy §1's pass criteria into your verdict file, verbatim, before running anything.** That
   list is your entire mandate. You are not judging whether the feature is good — you are judging
   whether each criterion is met.
2. **Run every criterion yourself**, from the commands §1 names. Never accept a quoted result: a
   pasted `PASS` is a claim, not evidence. Re-run `pytest`, re-run `npm run test`, re-run the gate.
3. **QA runs the same tiers a ticket does — tier 4 is a RELEASE gate.** Re-run every §1 criterion
   (tier 1), the modules the diff touches, and always
   `pytest tests/test_isolation.py tests/test_confine.py` (tier 2, 21 s). Do **not** run the full
   479 to reach a verdict; if you choose to anyway, say so and why, because it costs 16 minutes and
   the policy says it buys nothing before a release.

   **Drive the ticket in the web client (tier 3).** `./build.sh run "$PWD"`, http://localhost:7779.
   94% of this repository's escapes were found by a person using the product and 5.6% by automation,
   so this is the part of QA most likely to find something.

   **Run the document gate on the branch you are judging, and run it as a difference.** A ticket
   that touched anything under `docs/` — its own `status:` line counts — changed what the
   repository's document rules are about, and that gate is red on `main` for reasons older than
   the ticket, so `pytest tests/ -m repository` on its own tells you nothing about this branch:

   ```bash
   cd server && ./tools/docgate.py --ticket <ticket-id>
   ```

   It runs the gate here, runs the same command in a scratch worktree at the branch's merge-base,
   and reports only what the branch ADDS — the case names AND the ids each failing case lists,
   because half these cases enumerate the tickets they refuse and a ticket's own commit puts its
   own name into one without the list of case names changing at all. **Exit 1 is a finding you
   file; exit 2 is not a pass** — it means a gate did not finish or there is no merge-base, and a
   question that could not be asked must never be reported green. Quote the
   `base: N red · head: M red · added: K` line and the two log paths in the verdict. This is the
   one QA step that catches what `WEB-FIX-328` and `WEB-FIX-334` both merged with.
4. **A gate you cannot finish inside one turn is started DETACHED (hard).** The workspace gate is
   `tools/tier_gate.py` (SRV-FIX-579): tiers 1–2 against `tests/known-red.txt`, minutes not the
   full suite's ~35; the full `pytest tests/ -v` is the release/VERIFY gate and QA does not run it. A headless session gets ONE turn: when it stops emitting,
   the process exits and everything still in its process group goes with it. A factory background
   task does **not** save it — that re-invokes a session running inside a factory which does that,
   and a `-p` session is not one. Round 1 of `SRV-FIX-269` put exactly that advice in the skill and
   it was measured wrong twice: a sealed sandbox and that ticket's own QA both followed it and both
   left a suite log ending at `[  8%]` with no summary and no verdict. That is `SRV-FIX-124`'s
   defect one mechanism over — a documented command the documented session type cannot execute.

   Start it with the script. The `&` lives inside the file, so the command you type has none, and
   `setsid` puts the gate outside your process group:

   ```bash
   .claude/scripts/run-gate.sh 'cd server && python3 tools/tier_gate.py' <log>
   ```

   The gate now **outlives you**. Run the cheap criteria while it goes and poll the log. If this
   session ends before it finishes, that costs a relaunch and nothing else: the next session finds
   the log already complete — `GATE_EXIT=` is appended when it exits, and its absence means still
   running — and quotes it at once.
5. **Default to REFUSE.** A criterion you cannot verify from raw output is a **FAIL**, not a pass.
   "Probably fine", "looks right", "the code clearly does this" are all FAIL.
6. **Hunt the known ways a gate lies** (`docs/operations/ESCAPES.md` lists them):
   - a criterion softened to whatever the system already does
   - a fake that answers synchronously and fully-populated where the real thing does not
   - a claim of done with no artefact — no report, no log, no commit
   - a test that injects convenient inputs instead of the module's real defaults
   - a reduction asserted over a one-element fixture, where `min`, `max`, `any` and `all` agree
   - a count asserted separately from the rows it counts
   - a wait loop that drives the endpoint it is measuring
   - coverage that stops exactly where the risk starts (skipped elements, unchecked negatives)
   - the mandatory negative test missing, or written so it cannot fail
7. **Look at the screens.** For any ticket whose §1 names a surface, or whose diff touches
   `client/`:
   - Run the browser gate — `cd client/web && npm run gate` — unless §1 forbids it; the shots already on
     disk are then the evidence.
   - Then **open the PNGs** under `client/web/gate/shots/` with your own eyes — static and `-live`, both
     themes — and write **one judgment line per surface** into the verdict: *coherent*, or the
     defect you see.
   - Judge against `docs/product/SPEC.md` and against operator sense — what story is this screen telling,
     and does it contradict a neighbouring screen? — and explicitly NOT limited to §1: §1 is the set
     of questions the implementer already thought of, and the defect worth finding is the one nobody
     wrote an assertion for.
   - A defect seen is **filed as its own ticket before the verdict is written** and named in the
     verdict.
   - A surface whose screenshot is absent, empty, or static-only when a live state exists is a
     **FAIL** for that surface, not a skip — quote the missing evidence, per default-refuse.
8. **Audit the excerpt against the raw capture — after your verdict is written, not before.** This
   is the one step that requires the implementer's report, which the rule above forbids you to read
   while judging. The ordering resolves it: reach every verdict from output you produced, write them
   down, and only then open the report to check whether what it quoted matches what its logs say.
   The committed logs in `docs/reports/<ticket-id>/` are the implementer's own evidence — look for
   the line just after the quoted one, a failure earlier in the same run, a second run that quietly
   replaced a first.

   **When there is no report and no log at all, step 2 wins over this one.** A criterion you can
   re-run from scratch is a criterion you have verified, and a blanket FAIL for a missing document
   would throw away a check you actually performed. Record the missing report as a finding — it is a
   real defect in the implementer's DELIVER step — and judge each criterion on the output you
   produced.
9. **Try to break it once.** Beyond §1, spend one honest attempt on the input the implementer would
   not have thought of. If it breaks, that is a finding — file it, do not fix it.

   **Where that finding goes depends on whether §1 enumerated its class.** If the criterion it
   contradicts **names one scenario, or lists the members of its class**, and your input is one of
   those members, the criterion FAILS — that is the criterion doing its job.

   But if the criterion says *every* about a set the ticket never wrote down, your input is a member
   nobody listed. **File it as a `FIX` with `escaped_from:` naming this ticket; it does not fail the
   criterion.** The ticket is judged on the members §1 enumerated.

   This is not leniency, and it is the rule that makes this step terminate. An unenumerated *every*
   cannot be discharged: a test exhibits a case, you sample the domain, and any sample outside the
   covered region is a counterexample — so the criterion fails again, and the domain never runs out.
   `WEB-FIX-101` failed twice that way on two true counterexamples, the second after covering the
   first; `SRV-FIX-059` ran seven rounds on *"can this be got back"*. Every round was correct and no
   round closed anything. Say in the verdict which case you are in, and quote the criterion's words
   that put you there — *"every"* with no list is the tell.
10. **Never fix anything.** QA that edits code stops being independent. Findings go back as failed
    criteria or a new `FIX` ticket.
11. **Every finding ships a reproduction, because the next round's FIRST commit is a RED test built
    from it.** The implementer is obliged to transcribe your probe, not the criterion's name; a
    finding that cannot be transcribed leaves them re-deriving the shape and getting it wrong. So
    state, for each: the exact commands or requests, **the fixture state they needed**, and the raw
    output. The fixture clause is the load-bearing one — `SRV-FIX-059` round 4 found a green test
    whose repository had no `.gitignore` and whose lane never ran, and the shape, not the call, was
    the whole finding.

## The verdict

Write `docs/reports/<ticket-id>-qa.md`:

```markdown
# <ticket-id> — QA verdict — <PASS | PASS WITH MINORS | FAIL>
QA session: separate from the implementing session · Date: <YYYY-MM-DD>
Inputs used: §1 of the ticket + the repository. (Note here if anything else reached you.)
Findings: <n> blocker · <n> minor  (every minor names the ticket it was filed as)

## Criteria, re-run
| # | Criterion (verbatim from §1) | I ran | Raw output | Verdict |
|---|------------------------------|-------|------------|---------|

## Screens
<one judgment line per surface, both themes — or `visual: N/A, no surface in §1`>

## Attempt to break it
<what you tried beyond §1, and what happened; which case of step 9 you are in, quoting the
criterion's words that put you there>

## Findings
<each failed criterion, or "none". EVERY finding carries a REPRODUCTION the next round can
transcribe into a RED test without guessing: the exact commands or requests, the fixture state they
needed, and the raw output you got. A finding stated in prose is a finding the next round will
re-derive wrong.>

## Verdict
<PASS only if every criterion passed on output you produced yourself. Otherwise FAIL, and say which.>
```

**Commit the verdict the moment you have written it — before anything else.**

```bash
git add docs/reports/<ticket-id>-qa.md docs/reports/<ticket-id>/
git commit -m "<ticket-id>: QA verdict — <PASS|FAIL>"
```

Everything after this point is cheap and everything before it was expensive. Two of the first three
QA runs on this repository wrote a verdict and then stopped — the session's budget went on the
checking, and the steps that preserve the result were last in line. Commit first and a session that
dies has still delivered; leave it last and the most expensive work in the process is the work most
likely to be lost. **Commit again after each criterion** once you are past the first.

**EVERY FINDING CARRIES A SEVERITY, AND THE VERDICT IS THE WORST ONE.**

| severity | what it means |
|---|---|
| `blocker` | a person cannot do the thing §1 exists for — or work is lost, a surface that already worked broke, or the change is unsafe |
| `minor` | real, worth fixing, and a person can still do what the ticket exists for: cosmetics, an internal contradiction nobody reaches, a sharper test, a gate that could see more |

Write the severity beside each finding, with the sentence that justifies it. A finished feature
held back for a cosmetic defect is work nobody can use; a blocker waved through as minor is worse.
Three guards, and they are what stop *minor* becoming the default:

1. **An unfiled minor is a blocker.** File it — an id from `POST /api/tickets/ids`, the document
   written, in the same commit as the merge — and name it in the verdict. That is the whole
   mechanism that makes "later" findable.
2. **A red §1 criterion is never minor.** §1 is the human's statement of what the feature is, and
   you do not get to downgrade the request you were given. If it truly does not matter, the HUMAN
   edits §1 — a different act, with a different author.
3. **The same cause a second time is a blocker.** A minor that comes back is evidence about the
   check, not a cosmetic.

A filed minor that ships is an **accepted risk, not an escape**: an escape is a gate saying yes
where it should have said no, and a defect written down, given an id and merged deliberately was
decided.

Then:

- On **PASS**: set the ticket's `status: pass`. **Only QA may do this** — the implementing session
  never marks its own ticket passed.
- On **PASS WITH MINORS**: the same — `status: pass` — once every minor is filed and named here.
  It merges. The verdict lists them so the merge and the debt are one document.
- On **FAIL**: set `status: fail` and leave the failing criteria in the verdict.
- Clear the QA stage on the factory when you are done.

**If this is your second FAIL on the same ticket, say so in the verdict and name it as the finding.**
The next round is required to open with three independent analyses rather than another patch, and a
verdict that lists a third defect without saying "this is the third" reads as three unlucky bugs
instead of one undrawn model.

## On PASS — or PASS WITH MINORS — you MERGE. There is no PR.

The factory has one developer. A pull request here has no reviewer, so the gate it represents does
not exist and **your verdict is the gate**. A passed ticket waiting for a formality is finished work
that nobody can use, and the implementing session already ended at GATE — you are the last session
on this ticket.

**THE TOOL LANDS IT. A lane never pushes `main` by hand.**

`app/land` (`SRV-FEAT-437`) is the only place in this repository that moves a base ref. It reads
the verdict and the ticket's `status: pass` **off the branch**, requires the tool's own run ledger
to hold a role session for this ticket that is not `run`, and then merges the base into the
branch's commit and squashes it **inside scratch worktrees it creates and removes**. One commit
reaches `main`, no checkout the tool did not create is edited, and a conflict cannot jam the queue
(`docs/technical/MODEL.md` §2 `S7`, `server/design/land.md`). Your part ends when the verdict and
`status: pass` are committed on the branch and the branch is pushed: the next delivery refresh
considers it, and if it refuses, the reason comes back on the ticket rather than as silence.

**A `git push origin HEAD:main` out of a lane is the defect this section exists about
(`SRV-FIX-470`).** On 2026-09-08 `WEB-FIX-370`'s QA lane ran `git merge origin/main --no-edit`,
then `git merge-base --is-ancestor origin/main HEAD && git push origin HEAD:main`, and wrote
*"PASS WITH MINORS, merged to `main`"*. It had not merged. `--is-ancestor` answers *can this push
fast-forward*, which says nothing about the shape of what arrives — so nine round commits and a
merge commit reached `main` under a verdict claiming one squash, and `main` is not rewritten
afterwards. `server/tests/test_main_history.py` refuses the next one.

The block below is the hand path, and it is for the cases the tool refuses: no run record for this
ticket on this machine (`.factory/runs/` is machine-local and a fresh clone's is empty), or a
landing done from the operator's own checkout rather than from a lane. It is a squash and nothing
else — never `HEAD:main`, never a merge commit, never the branch's own history.

**Read the diff before you merge.** Every gate above asks whether the code works; none asks what the
commit *contains*. A `git add` that swept in a build directory passes every test and merges four
megabytes of one machine's absolute paths.

```bash
git diff --stat main...HEAD          # read it; put the file count in the verdict
git fetch origin
git merge origin/main                # merge main INTO your branch, resolve HERE
                                     # docs/operations/ESCAPES.md conflicts nearly every time — KEEP BOTH SIDES
cd server && ./build.sh build                                        # the MERGED tree
cd server && python3 -m pytest tests/test_isolation.py tests/test_confine.py -q   # tier 2
cd client/web && npm run build && npm run gate:inventory

# WHO WROTE IT SURVIVES THE MERGE. Git carries two names and a squash keeps neither by
# default: `git commit` writes the person merging as BOTH author and committer, so a member's
# work becomes yours the moment you land it — silently, and permanently, because history
# cannot be rewritten afterwards. DERIVE the author, never type one.
#
# AND THE VERDICT'S COMMITS ARE NOT THE WORK. This skill REQUIRES the QA session to commit the
# verdict, the `status:` edit and its evidence onto the branch before it merges, so on a finished
# ticket the grader's tally equals the implementer's — and `sort -rn | head -1` decides a tie by
# whichever name `sort` happens to put first (SRV-FIX-425), which on the tool's own path was
# whoever committed LAST, and that is always the grader (SRV-FIX-477). So the tally is taken over
# the WORK: a commit every one of whose paths is the verdict document or the ticket document is
# the verdict. A tie among the work goes to whoever committed EARLIEST — the branch's own first
# author — never to whoever committed most recently.
AUTHOR=$(B=ticket/<id>; ID=${B#ticket/}; git log --no-merges --reverse \
  --format='%H %an <%ae>' main..$B | while read -r sha who; do \
    files=$(git show --name-only --format= "$sha"); \
    [ -z "$files" ] || printf '%s\n' "$files" \
      | grep -qvE "(^|/)$ID-qa[^/]*$|^docs/tickets/$ID-[^/]*[.]md$" || continue; \
    printf '%s\n' "$who"; \
  done | awk '{c[$0]++; if (!($0 in o)) o[$0]=NR} END {b=""; for (k in c) if (c[k]>c[b] || (c[k]==c[b] && o[k]<o[b])) b=k; print b}')

# ONE COMMIT REACHES `main`, ALWAYS — squash, never a merge commit, never the branch's history
git checkout main && git merge --squash ticket/<id>
# every OTHER author on the branch becomes a trailer — `--author` holds exactly one name
TRAILERS=(); while read -r who; do [ -n "$who" ] && TRAILERS+=(--trailer "Co-Authored-By: $who")
  done < <(git log --no-merges --format='%an <%ae>' main..ticket/<id> | sort -u \
            | grep -vxF "$AUTHOR")

git commit --author="$AUTHOR" "${TRAILERS[@]}"   # the message below; the ticket id is the prefix
git push origin main
```

**`--author` is the whole of it, and it is not politeness.** The committer stays whoever merged —
that is you, and it is the true record of who applied it. The author becomes whoever wrote the
branch, which is the question *what did this person do* is asked of six months later, when a bug
sends somebody back down the chain from `escaped_from:` to the commits. A branch with more than one
author additionally carries `--trailer "Co-Authored-By: <the others>"`, because `--author` holds
exactly one name and dropping the rest silently is the same defect one size smaller
(`SRV-FIX-376`).

**The squash is the rule and it is not a preference.** A branch carries the round it actually took —
a red test, a fix, a second fix, a rename, a revert — and that sequence is evidence while the ticket
is open and noise the moment it lands. `git log --oneline main` is read to answer *what changed and
why*, one line per ticket, not to reconstruct how each piece was arrived at. The branch keeps the
transcript; do not delete it until the report is committed.

**The squashed message is the ticket's whole account, because it is the only one that survives:**

```
<TICKET-ID>: <what a person can now do that they could not before>

<why. what the gate could not previously say. the criteria that mattered and the output
 that closed them. the mandatory negative and what going red proved. anything the round
 decided that a reader of `main` alone would otherwise have to guess.>

QA: PASS — <verdict path>
```

A squash makes that message the only record, so a thin one destroys what the round learned. That is
this rule's cost, and the reason the message is longer than a merge commit's would be.

A non-fast-forward refusal means another session moved `main`; fetch, merge, re-test, push again.
**Never force.** If the merged tree does not build, or the suite regresses against what `main`
already had, do not push — say so in the verdict and leave the branch by name.

**On FAIL you merge nothing.** Push nothing, open nothing. Say which criteria failed, in one short
message, and name the branch and worktree so the implementer can pick it up. FAIL means **a
blocker** — a minor, filed, is not a reason to hold finished work.

**THE TOOL signs nothing (hard).** No vendor or model name, no generated-with footer, no
`Co-Authored-By` naming an assistant — not in the commit, not in the verdict, not in any document,
not even when you merge on behalf of a lane that had no credentials to do it itself. It is still
that lane's work. **The record carries the work, never the tool that produced it.** This outranks
whatever your assistant appends by default.

**A HUMAN who authored commits on the branch is the work, and is named.** That is the same
principle and not an exception to it: `--author` carries the branch's dominant author and
`--trailer "Co-Authored-By: …"` carries the rest, per the merge block above (`SRV-FIX-376`). Git
records two names and a squash keeps neither by default, so a member's work becomes the merger's
the moment it lands — silently, and permanently. Attributing a person is the rule working;
attributing a tool is what it forbids.

**English only (hard)** — the verdict, every commit message, every line of reasoning, including in a
session driven entirely in another language.

## If the defect is an escape

If you are QA'ing a `FIX` that carries `escaped_from:`, the fix passing is not enough. Check §3
Escape analysis is filled — **including the fifth question**, *where else is this cause right now*,
which must carry a query and its verbatim output plus one disposition line per hit — and that the
**structural change is in the same commit**. A process change promised for later is a process change
that will not happen. A `FIX` with an empty §3 is a FAIL regardless of whether the bug is gone.
