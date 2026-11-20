#!/usr/bin/env bash
# Run a queue of tickets to a verdict, one at a time, in the MAIN checkout.
#
#   TICKETS="WEB-FIX-354 WEB-FIX-329" .claude/scripts/finish-two.sh
#
# The main checkout, not a lane: a lane cannot reach MERGE (`FAC-EPIC-371`), cannot refresh its
# credentials, and cannot be recovered once released (`SRV-FIX-431`). Every ticket that finished
# tonight finished here.
#
# QA merges and pushes its own PASS — that is the workflow, and it was watched doing it for
# `WEB-FIX-344` and `WEB-FIX-333`. So this does not merge. It runs the round, hands to QA, and
# reports what the verdict was.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO"
LOG="$REPO/.factory/night/finish-two.log"
BEAT="$REPO/.factory/night/finish-two.beat"
mkdir -p "$(dirname "$LOG")"
say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

ALLOWED="$(sed -n '/^ALLOWED_TOOLS="/,/"$/p' "$REPO/.claude/scripts/start-qa.sh" \
           | sed '1s/^ALLOWED_TOOLS="//; $s/"$//' | tr -d '\\' | tr '\n' ' ')"
case "$ALLOWED" in *"Bash("*) : ;; *) say "no allow-list; refusing"; exit 2 ;; esac
ALLOWED="$ALLOWED Bash(git checkout:*) Bash(git switch:*) Bash(git merge:*) Bash(git branch:*)"

statusOf() { sed -n 's/^status:[[:space:]]*//p' "$1" | head -1; }

for ID in ${TICKETS:?set TICKETS}; do
  date +%s > "$BEAT"
  T="$(ls docs/tickets/$ID-*.md 2>/dev/null | head -1)"
  [ -f "$T" ] || { say "$ID  SKIP — no ticket file"; continue; }

  git checkout -q main 2>/dev/null
  git fetch -q origin 2>/dev/null; git merge -q --ff-only origin/main 2>/dev/null
  if [ -n "$(git status --porcelain)" ]; then
    say "$ID  STOP — the tree is dirty; a round started on it would sweep the leftovers"; break
  fi

  BRANCH="ticket/$ID"
  if git rev-parse --verify -q "$BRANCH" >/dev/null; then
    git checkout -q "$BRANCH" || { say "$ID  SKIP — cannot check out $BRANCH"; continue; }
    if ! git merge -q --no-edit origin/main >/dev/null 2>&1; then
      python3 "$REPO/.claude/scripts/union-escapes.py" >/dev/null 2>&1 || true
      git add -A >/dev/null 2>&1
      if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
        say "$ID  SKIP — main does not merge into its branch cleanly"
        git merge --abort 2>/dev/null; git checkout -q main; continue
      fi
      git commit -q --no-edit
    fi
  else
    git checkout -q -b "$BRANCH"
  fi
  say "$ID  START on $BRANCH (status $(statusOf "$T"))"

  date +%s > "$BEAT"
  timeout "${RUN_TIMEOUT:-7200}" claude -p --model claude-opus-5 --permission-mode acceptEdits \
    --allowedTools "$ALLOWED" -- "/run-ticket $T" \
    > "$REPO/.factory/night/$ID-run.log" 2>&1 < /dev/null
  say "$ID  run exited rc=$? status=$(statusOf "$T")"

  [ -n "$(git status --porcelain)" ] && git add -A && \
    git commit -q -m "$ID: the round's uncommitted remainder, committed by the runner"

  if [ "$(statusOf "$T")" = "awaiting-qa" ]; then
    date +%s > "$BEAT"
    timeout "${QA_TIMEOUT:-5400}" claude -p --model claude-opus-5 --permission-mode acceptEdits \
      --allowedTools "$ALLOWED" -- "/qa-ticket $T" \
      > "$REPO/.factory/night/$ID-qa.log" 2>&1 < /dev/null
    say "$ID  qa exited rc=$? status=$(statusOf "$T")"
    [ -n "$(git status --porcelain)" ] && git add -A && \
      git commit -q -m "$ID: QA's uncommitted remainder, committed by the runner"
  fi

  git fetch -q origin 2>/dev/null
  if [ "$(git log --oneline origin/main --grep="^$ID:" | wc -l)" -gt 0 ]; then
    say "$ID  LANDED on origin/main by its QA session"
  else
    say "$ID  $(statusOf "$T") — branch kept"
  fi
  git checkout -q main 2>/dev/null
done

date +%s > "$BEAT"
git checkout -q main 2>/dev/null
say "DONE — branches: $(git branch | wc -l)"
