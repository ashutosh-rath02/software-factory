#!/usr/bin/env bash
# Work a queue of tickets unattended, ONE PER BRANCH, squash-merging each that passes.
#
#   .claude/scripts/night-run.sh [queue-file]        default: .factory/night/queue.txt
#
# WHY A BRANCH PER TICKET, which is the whole reason this script exists rather than a loop of
# `start-qa.sh` calls. `WEB-FEAT-332` and `WEB-FIX-334` each landed as six and four commits
# straight onto `main`: no branch was ever cut, so `git merge --squash` had nothing to squash and
# the one-commit-per-ticket rule was bypassed at the START of the round rather than skipped at its
# end. Ten tickets run that way would put fifty commits on `main` in the same shape. The branch is
# cut here, before the agent starts, so the rule is structural instead of remembered.
#
# The allow-list is NOT duplicated here. `start-qa.sh` owns it and
# `test_session_permissions.py` derives it from the workflow's own documents; a second copy would
# drift silently, which is the defect class this repository keeps paying for.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
QUEUE="${1:-$REPO/.factory/night/queue.txt}"
[ -f "$QUEUE" ] || { echo "no queue: $QUEUE" >&2; exit 2; }

NIGHT="$REPO/.factory/night"
mkdir -p "$NIGHT/logs"
LEDGER="$NIGHT/ledger.tsv"
RUN_TIMEOUT="${RUN_TIMEOUT:-5400}"     # 90 min for a round
QA_TIMEOUT="${QA_TIMEOUT:-3600}"       # 60 min for a verdict

# The grant, read from the one file that owns it. A sweep that finds nothing has not run, so an
# empty extraction is a hard stop rather than a session launched with no tools.
ALLOWED_TOOLS="$(sed -n '/^ALLOWED_TOOLS="/,/"$/p' "$REPO/.claude/scripts/start-qa.sh" \
                 | sed '1s/^ALLOWED_TOOLS="//; $s/"$//' | tr -d '\\' | tr '\n' ' ')"
case "$ALLOWED_TOOLS" in
  *"Bash("*) : ;;
  *) echo "could not read the allow-list out of start-qa.sh — refusing to launch" >&2; exit 2 ;;
esac
# a branch per ticket needs two verbs a QA session is deliberately not given
ALLOWED_TOOLS="$ALLOWED_TOOLS Bash(git checkout:*) Bash(git switch:*) Bash(git merge:*)"

say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$NIGHT/night.log"; }
ledger() { printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "$1" "$2" "$3" >> "$LEDGER"; }

status_of() { sed -n 's/^status:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$1" | head -1; }

# The merge the workflow prescribes: one commit, the BRANCH's author kept, every other author in a
# trailer. Git writes the merger as both author and committer otherwise, and a member's work
# becomes the merger's permanently (SRV-FIX-376).
squash_merge() {
  local id="$1" branch="$2"
  local author trailers=()
  author="$(git log --no-merges --format='%an <%ae>' "main..$branch" | sort | uniq -c | sort -rn \
            | head -1 | sed 's/^ *[0-9]* //')"
  [ -n "$author" ] || return 1
  while read -r who; do
    [ -n "$who" ] && trailers+=(--trailer "Co-Authored-By: $who")
  done < <(git log --no-merges --format='%an <%ae>' "main..$branch" | sort -u | grep -vxF "$author")

  git checkout -q main || return 1
  git merge -q --squash "$branch" || { git merge --abort 2>/dev/null; return 1; }
  local subject
  subject="$(sed -n 's/^title:[[:space:]]*//p' "$(ls docs/tickets/$id-*.md | head -1)" | head -1)"
  git commit -q --author="$author" "${trailers[@]}" \
    -m "$id: $subject" \
    -m "$(git log --reverse --no-merges --format='- %s' "main@{1}..$branch" 2>/dev/null | head -20)" \
    -m "Squashed from $(git rev-list --count --no-merges main@{1}..$branch 2>/dev/null) commits on $branch." \
    || return 1
}

TOTAL=0; PASSED=0; FAILED=0
while read -r ID; do
  [ -z "$ID" ] && continue
  case "$ID" in \#*) continue;; esac
  TOTAL=$((TOTAL+1))

  TICKET="$(ls docs/tickets/$ID-*.md 2>/dev/null | head -1)"
  [ -f "$TICKET" ] || { say "$ID  SKIP — no ticket file"; ledger "$ID" skip "no ticket file"; continue; }

  # never start on a dirty tree: the previous round's leftovers would be swept into this one's
  # commits, which has already happened once in this repository.
  if [ -n "$(git status --porcelain)" ]; then
    say "$ID  STOP — working tree is dirty, refusing to start a round on it"
    ledger "$ID" stop "dirty tree"; break
  fi

  git checkout -q main
  BRANCH="ticket/$ID"
  git rev-parse --verify -q "$BRANCH" >/dev/null && git branch -q -D "$BRANCH"
  git checkout -q -b "$BRANCH" || { say "$ID  SKIP — cannot cut $BRANCH"; continue; }
  say "$ID  START on $BRANCH — $(sed -n 's/^title: *//p' "$TICKET" | cut -c1-60)"

  LOG="$NIGHT/logs/$ID-run.log"
  timeout "$RUN_TIMEOUT" claude -p --model claude-opus-5 --permission-mode acceptEdits \
    --allowedTools "$ALLOWED_TOOLS" -- "/run-ticket $TICKET" > "$LOG" 2>&1 < /dev/null
  RC=$?
  ST="$(status_of "$TICKET")"
  say "$ID  run exited rc=$RC status=$ST"

  if [ "$ST" != "awaiting-qa" ]; then
    say "$ID  NOT HANDED TO QA (status=$ST) — branch kept, moving on"
    ledger "$ID" "no-qa" "rc=$RC status=$ST"; FAILED=$((FAILED+1)); git checkout -q main; continue
  fi

  # commit anything the round left uncommitted rather than carrying it into the next ticket
  [ -n "$(git status --porcelain)" ] && git add -A && \
    git commit -q -m "$ID: the round's uncommitted remainder, committed by the night runner"

  QLOG="$NIGHT/logs/$ID-qa.log"
  timeout "$QA_TIMEOUT" claude -p --model claude-opus-5 --permission-mode acceptEdits \
    --allowedTools "$ALLOWED_TOOLS" -- "/qa-ticket $TICKET" > "$QLOG" 2>&1 < /dev/null
  QRC=$?
  ST="$(status_of "$TICKET")"
  say "$ID  qa exited rc=$QRC status=$ST"

  [ -n "$(git status --porcelain)" ] && git add -A && \
    git commit -q -m "$ID: QA's uncommitted remainder, committed by the night runner"

  if [ "$ST" = "pass" ]; then
    if squash_merge "$ID" "$BRANCH"; then
      say "$ID  PASS — squashed onto main as $(git rev-parse --short HEAD)"
      ledger "$ID" pass "$(git rev-parse --short HEAD)"; PASSED=$((PASSED+1))
      git branch -q -D "$BRANCH"
    else
      say "$ID  PASS but the squash failed — branch kept for a person"
      ledger "$ID" "pass-unmerged" "squash failed"; FAILED=$((FAILED+1)); git checkout -q main
    fi
  else
    say "$ID  $ST — branch $BRANCH kept, not merged"
    ledger "$ID" "$ST" "qa rc=$QRC"; FAILED=$((FAILED+1)); git checkout -q main
  fi
done < "$QUEUE"

git checkout -q main
say "NIGHT DONE — $TOTAL attempted, $PASSED merged, $FAILED left for a person"
