---
name: run-ticket
description: Execute a factory ticket end-to-end — SPEC → TEST(red) → IMPL → CONVENTION → BUILD → GATE(tiers 1-3) → committed report, then start QA. Use when the user says "Run ticket docs/tickets/X.md", "/run-ticket X", or hands you a ticket file to deliver.
---

# Run a factory ticket

The **§2 Standing process** (`docs/tickets/_TEMPLATE.md` §2) **is the contract** — follow it verbatim, it outranks this file.
This skill is the operating procedure around it.

**Every path here is relative to this repository — the factory.** One project, one `docs/` tree, one
`server/`, one `client/`.

## Report your stage (at EVERY step, not only when finished)

The moment you enter a new step:

```bash
curl -sS -X POST "$FACTORY_PROGRESS_URL" -H 'Content-Type: application/json' \
  -d "{\"token\":\"$FACTORY_PROGRESS_TOKEN\",\"stage\":\"IMPL\"}"
```

Use the exact name from the ticket's own `stages:` — `SPEC`, `TEST`, `IMPL`, `CONVENTION`, `BUILD`,
`GATE`, `REVIEW`, `QA`, `MERGE`, `VERIFY`. A `202` means it landed. Re-entering a step in a later
round adds `"round":2,"of":4`.

**No `$FACTORY_PROGRESS_URL` means you are not running in a lane** — skip this and do not report an
error.

The current step has to be visible from outside, not only in the agent's head. This command was
blank in the skill for weeks while the mechanism had long shipped, and the consequence was **12 runs
on one ticket, 9h 46m, $38.81, and the stage strip still reading `0/10` with no cell lit**. Keep
writing the stage into the report as well — the report is for whoever reads it later, this command
is for whoever is watching the screen now.

## Token discipline (SRV-FIX-580) — every call re-sends the whole context

Measured on 2026-10-10: one implementation session made 715 model calls at a median context of
122k tokens (86M input); 218 calls were partial file reads and 98 were test runs, 41 of them exact
repeats. A call costs the context, not its output, so the rule is **fewer calls**:

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
- **Cost bounds (`WORKFLOW.md` § Cost bounds).** Two GATE rounds at most in this session, then stop
  with `needs_you` naming what is still red. A ticket marked `qa: operator` stops at `awaiting-qa`
  after GATE and REVIEW and does **not** report the QA stage — the operator lands it from the diff.
- **What a lane does not have — do not try, do not retry.** Measured 2026-10-10: 22 tool calls
  failed on these. `/tmp` is read-only (write under `$TMPDIR`, which is `/tmp/factory`); the git
  common directory is read-only (no `git worktree add`, no new refs outside `ticket/`); there is no
  GitHub credential (never `git push` — the tool lands the local branch); there is no provider login
  (live-agent cases are the operator's host run, see SRV-FEAT-574 §1); Chrome, when bound, is at
  `CHROME_PATH=/opt/google/chrome/chrome`. A command that fails for an environment reason is
  recorded once with its error and never re-run.

## 0. Load context (before anything)

1. Read the ticket file once. Its §2 is `docs/tickets/_TEMPLATE.md` §2 — a pointer line in new
   tickets, an identical copy in older ones; read the template's §2 once, never both.
2. Read `CLAUDE.md`, `docs/technical/MODEL.md` §2 (the seams), `docs/technical/ARCHITECTURE.md`.
3. Grep `docs/operations/ESCAPES.md` for this subject and for the `escaped_from:` target.
4. If §1 has a genuinely undecided behaviour, ask ONE batched question now. Everything under
   "Pre-decided" is closed — do not reopen it.

## 0b. The agent tree — advisor at three checkpoints, helpers only when needed

`CLAUDE.md`'s model rule names the roles; the agents are `.claude/agents/*.md` (Claude lanes) and
`.codex/agents/*.toml` (Codex lanes), same names. **The selected membership is `$FACTORY_TREE_AGENTS`.** Read its space-separated names; if it
is empty, skip the helper checkpoints and run the ticket as a single session. If it contains
`advisor`, spawn the advisor exactly at these checkpoints and nowhere else. The server checked
the selected provider files in the lane before starting this process; do not probe files here:

| Checkpoint | When |
|---|---|
| `before-plan` | after step 0, before SPEC is written |
| `repeated-error` | the same failure cause shows up a second time at one stage |
| `before-done` | GATE is green, before START QA |

Hand it the checkpoint name, the ticket path, `git rev-parse HEAD`, and the path of **this
session's own transcript** (Claude: the newest `*.jsonl` under `~/.claude/projects/<cwd slug>/`;
Codex: the newest rollout under `~/.codex/sessions/`), plus `git diff --stat` and the latest gate
   summary line. It searches the transcript for what it needs since the previous checkpoint; it does
   not read it whole (SRV-FIX-580: a whole-transcript read cost 6–14M tokens per call).
Apply each finding or write in the report why not, under `## Advisor checkpoints`: one row per
call with checkpoint, revision, findings count and disposition. The advisor has no authority: its
"no findings" is not a GATE and never replaces QA. **When the advisor returns, apply its findings
and keep working in the same turn** — ending the turn to relay them stalls the lane until someone
nudges it (seen three times on 2026-10-10/11).

`explorer`, `researcher` and `worker` are optional. Spawn one only if its name appears in
`$FACTORY_TREE_AGENTS`, and only for a bounded question or an assignment that can proceed on its
own; do not launch them by default.

## 1. SPEC (write it before any code)

Restate, in the report draft: the endpoint's request/response JSON, the socket frame's fields, the
store's full shape. If `docs/technical/contracts/http.schema.json` or
`docs/technical/contracts/socket.schema.json` changes, edit it FIRST and regenerate
(`cd client/web && npm run gen:api-types` / `gen:types`) — the client's types derive from it and
the browser gate validates the live server against it. **No implementation code before the SPEC
is written down.**

## 2. TEST — red first, and prove it

Write §1's criteria as real tests **before** implementing, run them, and keep the RED output:

- server → `server/tests/test_<area>.py`, run `cd server && pytest tests/test_<area>.py -v`
- client → `client/web/tests/<name>.test.tsx`, run `cd client/web && npm run test`

A test that was never red proves nothing. Quote the red output in the report.

**No criterion may quote a model's prose** — a criterion is a fact about the tool, not about the
model (`SRV-FIX-042`).

**An assertion may not be driven by the mechanism it measures.** A wait waits on the CONSEQUENCE —
`/proc`, a file, a log line, a plain `sleep` — never on repeated calls to the endpoint under test.
`SRV-FIX-105` round 1 shipped a reap that lived inside `GET /api/lanes`, green, because the
criterion's own wait loop called that endpoint sixty times before asserting: the test drove the
thing it was measuring, and the defect reproduced the moment QA removed the polling. When the only
way to see the effect is to call the thing under test, wait in silence and then make **exactly one**
call, which must answer with the state already correct.

**A mandatory negative must be able to fail on the regression it names.** Run the same function the
live case runs and require it to raise, or assert the live case still calls it. A negative built out
of a committed constant file, which stays green when the assertion it guards is deleted, is
documentation with an `assert` in front of it.

## 3. IMPLEMENT

Smallest diff meeting §1. Root cause only — a retry wrapper, a sleep, a swallowed exception or a
"temporary" patch fails the ticket even if the gate goes green. Respect §2's hard rules: never block
a handler thread (the engine runs a pool on one `io_context`), no mutex in event-driven code — use a
strand, nothing under `app/` included by `core/`, one action per `_<action>.cpp`, no hand-written
generated type, no colour outside `client/web/design/`, no comments unless asked.

## 3b. CONVENTION — the gate that reads the diff, not the behaviour

Run it the moment IMPLEMENT is done, before anything is built. Under a second.

```bash
cd server && ./tools/conventions.py
```

It **ratchets**: everything already in `tools/conventions-baseline.json` is tolerated, a NEW
violation fails. `--list` shows what is accepted. A workspace with no checker skips it and says so
in the report. **`--accept` is not how you make it green** — re-recording the baseline goes in the
report with a reason, exactly like softening a criterion.

Every other gate asks whether the code works. Nothing asked whether it was written the way this
codebase is written, and nothing ever would have: an escape is a defect found after a green gate, so
`ESCAPES.md` can only ever learn from things that BREAK.

**A rule grep cannot see belongs in the code, not in the checker.** This gate's first rule was for
the architecture defect that motivated it; it reported zero, the defect was put back, and it still
reported zero. It was deleted and replaced with a refusal — `file::Watch::add()` after `start()` is
`LOG(FATAL)`. Prefer that shape.

## 4. BUILD

Every target **§1 names**:

```bash
cd server && ./build.sh build
cd client/web && npm run build        # tsc -b failures ARE build failures
```

## 5. GATE — tiers 1–3, run them, quote them

**This repository runs tiers 1–3 per ticket, NOT the full suite.** 479 tests at 16 minutes, re-run
at every stage, cost 1–2 hours per ticket to validate the 3–5 assertions a ticket adds. Tier 4 is a
RELEASE gate and runs at VERIFY, on `main`, once.

1. **Tier 1** — the ticket's own file, every §1 case by name, output quoted.
2. **Tier 2** — the module it touched, plus the isolation floor, **always**:
   ```bash
   cd server && pytest tests/test_<module>.py tests/test_isolation.py tests/test_confine.py -v
   cd client/web && npm run test
   ```
   21 s for the floor, and they are the two properties a lane cannot be allowed to lose quietly.
3. **Tier 2b — the browser gate**, for any client-visible change: `cd client/web && npm run gate`.
   A green vitest run is not a screen anybody can read.
4. **Tier 3 — DOGFOOD.** `./build.sh run "$PWD"`, open http://localhost:7779, and drive the ticket
   from the screen the way a person would.

   **A green browser gate does NOT satisfy this** — the gate drives `gate/fixture-repo`, a synthetic
   repository, which is exactly what `WEB-FIX-248` and `WEB-FIX-261` are about. A bug you find in
   the client here becomes its own `FAC-*` ticket and this ticket **CONTINUES**. The report records
   what was driven and what the screen did; **"I ran it" is not evidence.**

**GATE ENDS WHEN EVERY §1 CRITERION HAS BEEN RUN BY YOU AND PASSES.** Not most of them, not the
ones the change touched — every one, executed in this session, with its output quoted. A criterion
you did not run is not a criterion that passed, and it is the next session's first finding.

Loop to green, **cap 4 rounds**, then stop and report the failing output. Never re-word an assertion
to make it pass, and never soften a criterion to the state the system happens to reach.

**Inside the loop the failing assertion runs alone** — then run tier 1+2 ONCE before the round ends.
A round that never ran the tier is not a round.

**If this ticket has already failed QA twice, STOP before you write a fix.** The next round starts
with three independent analyses — specification, verdicts, code — each blind to the others' sources,
and its first deliverable is naming the cause. The fourth patch to the same sentence is evidence
about the model, not about the bug: `SRV-FIX-059` took six rounds and the analyses found two live
data-losing defects that five rounds of patching had walked straight past.

Out-of-scope root cause → file `docs/tickets/<SRV|WEB>-FIX-NNN-<slug>.md` (the prefix its WORKSPACE takes) from the template with the
failing output as evidence, mark the assertion **BLOCKED(<SRV|WEB>-FIX-NNN)**, and move on. Never absorb
it.

## 5b. REVIEW — a command the tool runs, not a paragraph you write

`REVIEW` is the seventh stage and since `SRV-FEAT-500` it is **the workspace's own declared
command**, exactly like `build` and `gate`. Report the stage, then run it through the tool so its
output is on the record as `command` frames rather than in this session's head:

```bash
.claude/scripts/api.sh POST /api/lanes/<lane>/commands '{"which":"review"}'
```

`<lane>` is this lane — `.claude/scripts/api.sh GET /api/lanes` names it. The `202` carries a
`command_id`; the output arrives on `/ws` and on the lane's SESSION tab, and the last frame carries
the exit code. **Quote the deciding lines under the report's `REVIEW` heading, verbatim, including
the exit status, findings and failures; link the complete capture committed under
`docs/reports/<ticket-id>/`.** A stage whose evidence is a sentence saying it happened is the stage
this ticket replaced.

`docs/technical/workflow/REVIEW.md` is what the command reads: four passes — the seams of
`docs/technical/MODEL.md` §2, the four ways a check lies from `_TEMPLATE.md` §2,
`server/CLAUDE.md`'s architecture rules, and the client's generated-types and no-poll rules —
the two severities, and the exclusions.

**What you do with what it found:**

- a `blocker` — a person cannot do the thing §1 exists for, work is lost, a working surface broke,
  or it is unsafe — is **fixed before the handover**, in this session. It is not carried into QA.
- a `minor` is **filed as its own ticket**, with an id from the tool
  (`.claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"SRV-FIX"}'` — the prefix its
  WORKSPACE takes), in the same commit, and named in the report. An unfiled minor is a blocker.
- **a non-zero exit code is a `review failed`, never a review with no findings.** The tool reports
  the number and has no opinion about it, so nothing but this rule stops an empty stdout over a
  `127` from being written up as a clean review. Say `review failed`, quote the exit code, and fix
  what stopped it running before you claim the stage.

A workspace that declares no `review` answers `404 not_declared` naming the manifest key — record
that in the report and read the diff by hand against `docs/technical/workflow/REVIEW.md`'s four passes instead.

## 6. START QA — once every criterion is green, and in a separate session

**The gate on this whole step: every §1 criterion is green, run by you, in this session.** Until
then there is no handover to make — you go back to §5 and finish. A ticket handed over at `PARTIAL`
or with a red criterion buys a session that re-derives what you already knew: `WEB-FEAT-001` did
exactly that on 2026-09-02 and its QA returned nine criteria, none of them verified. **Validate
here; the next session verifies.** They are different jobs, not the same job done twice, and the
first one is yours.

Run the check before you believe yourself, from this repository's root:

```bash
server/tools/handover.py docs/tickets/<file>.md
```

It reads §1's `Evidence:` lines and the logs you committed under `docs/reports/<id>/`, and prints
one row per criterion: the case, whether it exists, the log it was RED in, the log it is GREEN in.
Exit 0 is the handover; anything else names what QA would have found in forty minutes.

A criterion that genuinely cannot go green inside this ticket is BLOCKED by a ticket you file with
its failing output, and it is blocked **before** the handover and named in the report — never
carried into QA as an open question.

**Give every finding you are carrying a severity, and say it in the report.** `blocker` — a person
cannot do the thing §1 exists for, or work is lost, or a surface that already worked broke — versus
`minor`, which is real and does not stop them. Only a blocker stops the merge, every minor is filed
as its own ticket with an id from the tool, and an unfiled minor counts as a blocker. **A red §1
criterion is never minor:** you do not downgrade the request you were given, and if it genuinely
does not matter the human edits §1.

Then, and only then: you may not mark your own ticket `pass`, and you do not stop and wait for a
person either. A subagent inside your session inherits your framing: the same assumptions, the same
blind spot, the same list of cases you already thought of. QA has to start cold.

- **If you were told you are part of a parallel batch, STOP at GATE — with your own criteria
  green.** The validation rule is not waived by the batch: run every §1 criterion of YOUR ticket
  and get it green first. Then set `status: awaiting-qa`, report what you added to any file every
  ticket touches (`manager.h`, `CMakeLists.txt`, `conftest.py`), and start nothing. The batch is
  integrated first and QA runs **once, over the union** — over a union whose every member arrived
  green, which is the only way one verdict can mean anything about all of them.
- **IN A LANE, THE HANDOVER IS THE STAGE REPORT — and it is the whole of it** (`SRV-FEAT-065`).
  If `$FACTORY_PROGRESS_URL` is in your environment you are in a lane, and this repository declares
  a `qa` role on the `QA` stage. Report that stage and stop:

  ```bash
  curl -sS -X POST "$FACTORY_PROGRESS_URL" -H 'Content-Type: application/json' \
    -d "{\"token\":\"$FACTORY_PROGRESS_TOKEN\",\"stage\":\"QA\"}"
  ```

  The tool claims a **new lane** for the same ticket in the `qa` role, cut from the branch your work
  is on, and starts the session in it with the text of `.factory/prompts/qa-ticket.md`.

  **Do NOT also run `start-qa.sh` in a lane. That produces two QA sessions on one ticket** — one the
  tool started and one it cannot see — and they will race for the same verdict file.

- **Otherwise — not in a lane — start it yourself, as the last thing you do**, from this
  repository's root, without blocking:

  ```bash
  .claude/scripts/start-qa.sh docs/tickets/<file>.md
  ```

  **No trailing `&`, and that is the whole point.** You are a headless session (`claude -p`) and a
  headless session **cannot run a command ending in `&`**: it is rejected as deferring execution
  past approval-time checks, and there is no prompt to accept it. The handover documented for months
  was one only an interactive session could perform, and when it failed it left a 15-byte log
  reading `Execution error`, which every guard here then read as *QA ran*. `SRV-FIX-080` and
  `SRV-FEAT-065` died that way four days apart. The `&` lives inside the script now.

  The script refuses to start a second session for a ticket that already has a live one, keeps the
  previous log instead of truncating it, waits and **checks the process is still alive** — so a
  launch that dies is an error you see, not a sentence in your report that was false when you wrote
  it. It prints the pid; quote that.

  `--model opus` and `--permission-mode acceptEdits` are both inside the script and both
  load-bearing. Ticket agents run on Opus only. Without `acceptEdits` the session runs the whole
  check, reaches its verdict, asks permission to write the report, finds nobody there, and exits —
  every criterion re-run and every finding thrown away.

  **Do not end your turn asking the developer to run QA.** Three tickets reached `main` unQA'd
  because the last line of the report was a polite request instead of a launched process.

- Set `status: awaiting-qa`, then report the log path and that QA is running. **Do not read its
  verdict before it is written, and never answer it.**
- **ARM A WATCHER ON THE QA LANE, in the same turn you launch it — outside a lane only.** In a lane
  the rail is the watcher and your session ends at the report. Outside one:

  ```bash
  while kill -0 <pid> 2>/dev/null; do sleep 20; done
  echo "=== QA exited ==="; head -1 docs/reports/<ticket-id>-qa.md
  grep -m1 '^status:' docs/tickets/<file>.md; git log --oneline <frozen-sha>..HEAD
  ```

  Report the verdict the moment it lands, unprompted. The three states a watcher distinguishes and a
  person cannot are **still running**, **exited with a verdict**, and **died without writing one** —
  and the third is invisible without it.
- **From the moment you start QA the branch is frozen** — no commit, no amend, no rebase. Its
  verdict names a sha; moving that sha makes the verdict a statement about code that is gone.
- Hand it **§1 and the repo — nothing else.** Not your report, not your summary, not your reasoning
  about why an edge case is acceptable. Volunteering that is contaminating the check.
- **If QA fails you, your first commit is not the fix.** It is the assertion that would have caught
  what QA found — RED, and transcribed from QA's own reproduction (the commands and raw output in
  the verdict), never from the criterion's name. Then fix until that assertion is green, then the
  tier. A QA FAIL is the same evidence as an escape, so the check lands in the SAME commit as the
  fix. Arguing with the verdict instead of re-running it is the failure mode this rule exists to
  stop.

**You do not merge.** QA does, on PASS — see its skill. You end here.

## 7. DELIVER

**Keep the log.** Copy every capture the gate was decided on into `docs/reports/<ticket-id>/` and
commit it with the report — pytest output, vitest output, the browser gate's run. Name it
`<ticket-id>-<what>-<date>.log`. Quote the deciding lines in the report as well; the quote is for
the reader, the file is so QA can check the quote. Over 1 MB, gzip it; if you trim, say what you cut.

Write `docs/reports/<ticket-id>-report.md`:

```markdown
# <ticket-id> — <feature> — <PASS|FAIL|PARTIAL>
Ticket: docs/tickets/<file>.md · Branch/commit: <sha> · Date: <YYYY-MM-DD>

## Stages
SPEC       — <one line>
TEST       — <one line, incl. the red evidence>
IMPL       — <one line + files changed>
CONVENTION — <clean / new violation / no checker>
BUILD      — <targets, green/red>
GATE       — <tier 1, tier 2, browser gate, and WHAT WAS DRIVEN BY HAND>

## Assertions
| # | Assertion | Result | Evidence (quoted output) |
|---|-----------|--------|--------------------------|

## Reproduce
<exact commands>

## Contract added
<endpoint ids / socket frames → also added to CLAUDE.md>

## Diff
<git diff --stat main...HEAD — the file count, checked>

## Notes / follow-ups
<tickets filed, instrumentation kept and why, known limits>
```

Then: set `status:` to `awaiting-qa`, or to `fail` / `blocked` if it did not survive the gate —
**never to `pass`; that is QA's to set.** The board reads that field and nothing else, so a
delivered ticket left at `todo` shows as not started. Update `CLAUDE.md` if the ticket added an
endpoint or a socket frame. Commit on **`ticket/<id>`, never on `main`**, with the prefix
`<ticket-id>: <summary>`. In chat, give a concise outcome, validation summary and blockers, plus a
link to the saved report.

Flip `status: in-progress` when you start IMPLEMENT, so the board is honest while work is running.
If the ticket has a `parent`, do NOT touch the epic's status — only the L2 gate closes an epic.

**The message carries the work and nothing else (hard).** No `Co-Authored-By`, no vendor or model
name, no generated-with footer — in the commit, in the report, in any document. `git log` and
`git blame` are how this history is read, and a signature identical on every line is noise in all of
them forever. This outranks whatever your assistant appends by default.

**English only (hard)** — the ticket, the report, the commit message, every test name and every line
of reasoning, including in a session driven entirely in another language.

> The DELIVER section said "commit on `main`" until 2026-07-27. It is the third place that
> instruction was found, and the first one cost 659 unreviewed lines on `main` (`ESCAPES.md`, row
> 1). It survived two fixes because each one repaired the copy that had just been noticed. **If you
> are correcting a rule, grep the whole repository for the wording before you call it fixed.**

## 8. IF THIS TICKET IS A FIX FOR AN ESCAPE

A defect found after its ticket passed means **a gate said yes when it should have said no**. The
bug is the small half; the check that let it through is the real defect.

- Put `escaped_from: <ticket-id>` in the frontmatter.
- Fill §3 Escape analysis — including the fifth question, *where else is this cause right now*, which
  is answered with a **query and its verbatim output** plus one disposition line per hit. A query
  that finds nothing is a valid answer; prose about having looked is not.
- Land that change in the SAME commit as the fix. Deferred process changes do not happen.
- Add a row to `docs/operations/ESCAPES.md`. When two rows share a cause, that cause is the next ticket.

"Be more careful next time" is not an answer. If nothing reasonable would have caught it, write that
down — an accepted risk is a decision, an unnoticed one is not.
