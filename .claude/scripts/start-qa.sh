#!/usr/bin/env bash
# Start the QA session for a factory ticket, and RETURN — SRV-FIX-124.
#
#   .claude/scripts/start-qa.sh <docs/tickets/TICKET.md>
#
# Why this file exists instead of a line in the skill.
#
# `run-ticket` told every agent to hand over with `nohup claude … &`. Every one of those agents is
# headless (`claude -p`), and a headless session cannot run a command with a trailing `&`: it is
# rejected as deferring execution past approval-time checks, and there is no prompt to accept it.
# So the documented handover only worked from the one session type that never performs it, and when
# it failed it left a 15-byte log behind — `Execution error` — which every guard in the repository
# then read as "QA ran". Two tickets died that way four days apart.
#
# The `&` is INSIDE a script now. The command the agent runs has none, so it is runnable from the
# session type the skill prescribes, and it prints the pid it started so the report can name a
# number that was true when it was written.
#
# This repository is the factory and holds ONE project: reports go to `docs/reports/`, always.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TICKET="${1:?usage: start-qa.sh <docs/tickets/TICKET.md>}"
[ -f "$TICKET" ] || { echo "no such ticket: $TICKET" >&2; exit 2; }

ID="$(sed -n 's/^id:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$TICKET" | head -1)"
[ -n "$ID" ] || { echo "no \`id:\` in $TICKET" >&2; exit 2; }

REPORTS="$REPO/docs/reports"
mkdir -p "$REPORTS"
LOG="$REPORTS/$ID-qa.log"

# A retry must not destroy the evidence of why the first attempt failed. The recovery path for a
# dead launch is *run this again*, and `>` would truncate the `Execution error` — or a real stack
# trace — before writing a byte of the new session's output. The old log is kept beside the new
# one, stamped, so both are readable (SRV-FIX-124 QA, F4).
if [ -s "$LOG" ]; then
  KEPT="$REPORTS/$ID-qa.$(date +%Y%m%dT%H%M%S).log"
  mv "$LOG" "$KEPT"
  echo "kept the previous log at $KEPT" >&2
fi

# A LIVE session is the only reason to refuse. Not the log — a dead launch leaves one, and treating
# that as "already running" is the defect this script was written for — and not a verdict either: a
# ticket that FAILED QA is re-submitted after the fix, which is the ordinary flow. Round 1 of this
# script refused on the verdict and blocked its own round 2, one commit after being written.
if pgrep -af "qa-ticket" | grep -q -- "$ID"; then
  echo "a QA session for $ID is already running:" >&2
  pgrep -af "qa-ticket" | grep -- "$ID" >&2
  exit 3
fi

# ── the mechanism, and why it is this one (SRV-FIX-124 QA round 1, finding 3) ────────────────
#
#   the CLI's own detach flag        TRIED, AND REJECTED ON EVIDENCE. `claude --bg` returns at once
#                                    and the job is inspectable — but it refuses to combine with
#                                    `-p`, so the session it starts is INTERACTIVE, and an
#                                    interactive session that meets a tool needing approval waits
#                                    for a prompt that will never come. Measured: a QA session
#                                    started this way sat at `status=waiting state=blocked` for
#                                    twenty-one minutes having written nothing. Strictly worse than
#                                    the defect this fixes: the `&` form died loudly and left a
#                                    corpse a check can see; this one hangs quietly while every
#                                    liveness probe reports a healthy process.
#   `nohup claude -p … &`, inside    CHOSEN. `-p` cannot block: with no prompt available it REFUSES
#     this file                      and says so, which is the property a handover needs. The `&` is
#                                    not an evasion here: it is inside a file, the command an agent
#                                    runs has none, and `.claude/settings.json` names this script,
#                                    which is a human's sanction rather than a way around one.
#   the factory's own lane facility  REJECTED. It starts agents inside a confined lane: no network,
#                                    no credentials, a filesystem assembled from nothing. A QA
#                                    session must read the whole repository, run `git` and push to
#                                    `main`, and can do none of those there. It would also make QA
#                                    depend on the factory server being up — the tool under test
#                                    standing surety for its own verdict.
#
# NOTE: inside a lane you do NOT run this script at all. Report the `QA` stage and stop; the tool
# claims a qa-role lane itself. Running both produces two QA sessions racing for one verdict file.
# WHAT A QA SESSION MAY RUN, GRANTED AT THE LAUNCH RATHER THAN BY A PROJECT FILE.
#
# `--permission-mode acceptEdits` sanctions the WRITE of the verdict and says nothing about Bash.
# WEB-FEAT-001's QA wrote a thorough verdict about a product it could not execute one command
# against — nine of nine criteria refused for want of output — and under default-refuse that is a
# FAIL whatever the code does. The failure is silent in the direction that matters: the session
# still produces a document, so "nothing verified" reads exactly like "verified".
#
# NOT `.claude/settings.json`. A project settings file is ignored until the workspace is TRUSTED,
# and trust is a per-machine flag in `~/.claude.json`. Measured: a real session printed
# `Ignoring 35 permissions.allow entries … this workspace has not been trusted` and refused
# everything, while the same session with `--allowedTools` ran `npm --version` and returned
# `10.8.2`. A settings file would make this repository's gates depend on somebody having clicked
# a dialog on whichever machine the clone lands on.
#
# The list is the command vocabulary the workflow documents, and
# `test_session_permissions.py::test_the_allow_list_covers_every_command_the_workflow_documents`
# derives that vocabulary from `_TEMPLATE.md` §2, `WORKFLOW.md` and `.factory/prompts/*.md` rather
# than from this line — so a workflow that grows a command and not a rule turns red here instead of
# being discovered inside a verdict.
#
# A `:` IS NOT A WORD BOUNDARY. `Bash(npm run gate:*)` grants `npm run gate` and
# `npm run gate <args>` and NOT `npm run gate:inventory` — the CLI's prefix match ends at
# whitespace. Measured at the boundary: with only that rule, `npm run gate:inventory --help` is
# `This command requires approval` while `npm run build --help` runs. So the colon-suffixed script
# gets its own rule. `npm run gate:inventory` is half of the merge gate `qa-ticket/SKILL.md`
# prescribes, so without this a QA session on a client ticket cannot run its own pre-merge step.
#
# `npm run dev` IS DELIBERATELY NOT HERE and must not be added. A dev server never exits, so a
# headless session that starts one hangs until its timeout having done nothing — a live process
# that has stopped working, which is `SRV-FIX-124`'s class and the thing this ticket closes.
# `test_a_command_that_never_terminates_is_not_granted` holds that.

# `curl` IS NOT GRANTED AT ALL. §1 allows it "against localhost … Nothing broader", and
# `--allowedTools` cannot say that: every host-scoped form is refused because the `:` in the rule
# breaks its own prefix parse, and the only rule that works is `Bash(curl:*)`, which reaches any
# host — QA round 2 measured a session reaching 192.0.2.1 under it. So the bound moved out of the
# rule syntax and into a program: `.claude/scripts/api.sh` takes a PATH, never a URL, and the host
# is compiled into it.

# `git push` IS GRANTED AS TWO EXACT FORMS, not `Bash(git push:*)`. `--allowedTools` is allow-only:
# there is no deny list to put `--force` on, and a prefix rule would match it. The workflow
# documents exactly two pushes — `git push origin main` and `git push origin HEAD:main` — so those
# two are the grant, and every other push, force included, is refused for want of a rule.

# `git fetch` IS GRANTED THE SAME WAY, and for the same reason — SRV-FIX-361. `Bash(git fetch:*)`
# is a prefix rule whose ARGUMENT may be any URL, and a session under it reached
# http://192.0.2.1:9418/x.git while `curl` to the same address was refused: the bound SRV-FIX-351
# moved into `api.sh` was held for the command that ticket was looking at and for no other. The
# workflow documents `git fetch` and `git fetch origin`, so those two are the grant.
#
# THE RULE THIS IS AN INSTANCE OF: a bound the rule syntax cannot express is asserted over EVERY
# rule that could defeat it, derived from the grant, never over the one command that produced the
# finding. `git fetch` was the third occurrence of that cause in one grant.
ALLOWED_TOOLS="Bash(./build.sh build:*) Bash(./tools/conventions.py:*) Bash(tools/conventions.py:*) \
Bash(server/tools/handover.py:*) \
Bash(./tools/docgate.py:*) Bash(tools/docgate.py:*) \
Bash(python3 docs/tools/doc_inventory.py) Bash(python3 docs/tools/doc_inventory.py --check) \
Bash(server/tests/soak.sh:*) Bash(tests/soak.sh:*) Bash(./tests/soak.sh:*) \
Bash(design/ref/extract.py:*) Bash(.claude/scripts/run-gate.sh:*) \
Bash(.claude/scripts/start-qa.sh:*) Bash(.claude/scripts/api.sh:*) \
Bash(.claude/scripts/landed.sh:*) Bash(git add:*) Bash(git checkout:*) \
Bash(git commit:*) Bash(git diff:*) Bash(git fetch) Bash(git fetch origin) Bash(git log:*) \
Bash(git merge:*) Bash(git merge-base:*) Bash(git status:*) Bash(git push origin main) Bash(git push origin HEAD:main) \
Bash(npm --version) Bash(npm run build:*) Bash(npm run gate:*) Bash(npm run gate:inventory:*) \
Bash(npm run gen:api-types:*) Bash(npm run gen:tokens:*) Bash(npm run gen:types:*) \
Bash(npm run test:*) Bash(pytest:*) Bash(python3 -m pytest:*)"

setsid nohup claude -p --model claude-opus-5 --permission-mode acceptEdits \
  --allowedTools "$ALLOWED_TOOLS" \
  -- "/qa-ticket $TICKET" > "$LOG" 2>&1 < /dev/null &
PID=$!
disown 2>/dev/null || true

# Prove it is alive rather than reporting that it was started. A launch that dies immediately is
# exactly what this script exists to stop being invisible.
sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
  echo "QA for $ID died within three seconds of launch. Its log holds:" >&2
  sed -n '1,20p' "$LOG" >&2
  exit 4
fi

echo "QA for $ID running: pid=$PID $(ps -o etime= -p "$PID" | tr -d ' ') cwd=$(git -C "$REPO" rev-parse --show-toplevel)"
echo "launcher output kept at: $LOG"
