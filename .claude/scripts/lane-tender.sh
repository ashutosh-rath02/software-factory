#!/usr/bin/env bash
# Watch the lane set and do the three things the server does not yet do — SRV-FIX-386.
#
#   .claude/scripts/lane-tender.sh &
#
# On 2026-09-05 four lanes finished their rounds, set `awaiting-qa`, and sat for 4h54m at
# `needs_you: true` / `alarm: false` costing $68.59, because nothing claims a QA lane, nothing
# releases a finished one, and no alarm covers a lane that has STOPPED. This is the stopgap, not
# the fix: the fix is SRV-FIX-386 inside the server, and this file dies the day that lands.
#
#   idle + awaiting-qa   -> start QA in that lane's own worktree, against that lane's branch
#   pass                 -> squash-merge onto main, keeping the branch's author, release the lane
#   a free seat          -> start the next claimed-but-not-started lane
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO"
API=http://127.0.0.1:7777
LOG="$REPO/.factory/night/tender.log"
mkdir -p "$(dirname "$LOG")"
say() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*" >> "$LOG"; }
enc() { python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$1"; }


# A HEARTBEAT, because this script died three times and every liveness check I wrote said it was
# alive: `$!` holds the setsid WRAPPER's pid and the wrapper exits after forking, and
# `pgrep -f lane-tender.sh` matches the shell that greps for it. A file with a timestamp in it
# cannot lie about a process that is not running.
BEAT="$REPO/.factory/night/tender.beat"

while true; do
  date +%s > "$BEAT"
  SNAP="$(curl -s "$API/api/lanes")"
  [ -z "$SNAP" ] && { sleep 60; continue; }
  # A claimed lane ALREADY holds its seat, so starting it costs none. The real limit is this
  # machine: every round is a build and a test suite. MAX_BUSY caps agents, not seats.
  MAX_BUSY="${MAX_BUSY:-4}"
  BUSY="$(printf '%s' "$SNAP" | python3 -c "
import sys,json
print(sum(1 for l in json.load(sys.stdin)['lanes']
          if ((l.get('state') or {}).get('process')) == 'running'))")"
  # `pgrep -c` PRINTS 0 and EXITS 1 when nothing matches, so `|| echo 0` appended a SECOND zero
  # and $(( )) got "0\n0" — a syntax error that killed this script at startup, silently, three
  # times. The count is taken on its own line and defaulted, never inside an arithmetic expansion.
  QAN="$(pgrep -c -f qa-ticket 2>/dev/null)"; [ -n "${QAN//[!0-9]/}" ] || QAN=0
  BUSY=$(( BUSY + ${QAN//[!0-9]/} ))

  while IFS=$'\t' read -r LANE ID WT PROC ROLE; do
    [ -z "$LANE" ] && continue
    T="$(ls "$WT"/docs/tickets/$ID-*.md 2>/dev/null | head -1)"
    [ -f "$T" ] || continue
    ST="$(sed -n 's/^status: *//p' "$T" | head -1)"

    # A qa-role lane whose verdict is written is FINISHED, not waiting on anybody. `idle` means
    # "alive, turn over" (state.cpp:60), so a session that completed looks exactly like one that
    # stopped to ask — and the lane sits on the screen saying `waiting for you` until it is let go.
    if [ "$ROLE" = "qa" ] && [ "$PROC" = "idle" ]; then
      RUNST="$(sed -n 's/^status: *//p' docs/tickets/$ID-*.md 2>/dev/null | head -1)"
      case "$RUNST" in
        pass|fail)
          say "$LANE $ID  qa lane finished (verdict: $RUNST) — releasing, branch kept"
          curl -s -X DELETE "$API/api/lanes/$(enc "$LANE")?force=true" >/dev/null ;;
        *) : ;;
      esac
    elif [ "$PROC" = "idle" ] && [ "$ST" = "fail" ]; then
      grep -q "$ID  QA FAILED" "$LOG" 2>/dev/null || \
        say "$LANE $ID  QA FAILED — the round after a FAIL commits the missing assertion FIRST, red, from QA's own reproduction; left for a person"
    elif [ "$PROC" = "idle" ] && [ "$ST" = "awaiting-qa" ]; then
      if ! pgrep -af "qa-ticket" | grep -q -- "$ID"; then
        say "$LANE $ID  idle at awaiting-qa — starting QA in its own worktree"
        ( cd "$WT" && .claude/scripts/start-qa.sh "docs/tickets/$(basename "$T")" >/dev/null 2>&1 )
      fi
    elif [ "$ST" = "pass" ]; then
      # THIS DOES NOT MERGE, AND MUST NOT. There is ONE integration path and it is the agents':
      # they merge origin/main into the branch, land the round and push. A second path here
      # squash-merged the same work onto local main under a different hash, and every branch that
      # integrated from origin/main then hit add/add conflicts against those copies — forever.
      # A passed ticket is released; whether it reached origin/main is the agent's business.
      if [ "$(git log origin/main --oneline --grep="$ID:" | wc -l)" -gt 0 ]; then
        say "$LANE $ID  passed and on origin/main — releasing the lane"
        curl -s -X DELETE "$API/api/lanes/$(enc "$LANE")?force=true" >/dev/null
      else
        grep -q "$ID  passed, not yet pushed" "$LOG" 2>/dev/null || \
          say "$LANE $ID  passed, not yet pushed — left for its own session to push; not merging here"
      fi
    elif [ "$PROC" = "not_started" ] && [ "${BUSY:-99}" -lt "$MAX_BUSY" ]; then
      say "$LANE $ID  starting the round ($BUSY busy, cap $MAX_BUSY)"
      BUSY=$((BUSY + 1))
      curl -s -X POST "$API/api/lanes/$(enc "$LANE")/runs" -H 'content-type: application/json' \
           -d '{"resume":true}' >/dev/null
    fi
  done < <(printf '%s' "$SNAP" | python3 -c "
import sys,json
for l in json.load(sys.stdin)['lanes']:
    print('\t'.join([l['lane'], l['ticket_id'], l.get('worktree') or '',
                     (l.get('state') or {}).get('process') or '', l.get('role') or '']))")
  sleep 60
done
