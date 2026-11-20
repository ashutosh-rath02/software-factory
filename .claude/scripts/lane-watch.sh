#!/usr/bin/env bash
# Record what every lane is doing, once a minute, so "is it stuck again?" is answered from evidence.
#
#   .claude/scripts/lane-watch.sh &        → .factory/night/lane-watch.tsv  (+ .beat)
#
# This is the MONITOR for SRV-FEAT-415 and SRV-FIX-416. Tonight two claims were made about lanes
# that turned out to be wrong in opposite directions — "everything is stuck" (three agents were
# working, outside the lane set) and "the auto-release fired" (the server logged zero releases; a
# restart had emptied the registry). Both were guesses off one glance at a screen. A timestamped
# row per lane per minute is what makes the next answer checkable instead of remembered.
#
# It CHANGES NOTHING. No release, no start, no merge — only observation, because the last three
# scripts that acted on this data acted on it wrongly.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO"
API=http://127.0.0.1:7777
OUT="$REPO/.factory/night/lane-watch.tsv"
BEAT="$REPO/.factory/night/lane-watch.beat"
mkdir -p "$(dirname "$OUT")"
[ -s "$OUT" ] || printf 'iso\tlane\tticket\trole\tprocess\tstopped_reason\tstopped_s\tneeds_you\tcost\tseats_used\tlanes\n' > "$OUT"

while true; do
  date +%s > "$BEAT"
  SNAP="$(curl -s --max-time 10 "$API/api/lanes")"
  if [ -z "$SNAP" ]; then
    printf '%s\tSERVER\t-\t-\tunreachable\t-\t-\t-\t-\t-\t-\n' "$(date -Is)" >> "$OUT"
  else
    printf '%s' "$SNAP" | python3 -c "
import sys, json, datetime
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
seats = d.get('seats', {}).get('host', {})
lanes = d.get('lanes', [])
if not lanes:
    print('\t'.join([now, '(none)', '-', '-', '-', '-', '-', '-', '-',
                     str(seats.get('used', '-')), '0']))
for l in lanes:
    st = l.get('stopped') or {}
    s = l.get('state') or {}
    print('\t'.join([now, l.get('lane',''), l.get('ticket_id',''), l.get('role') or '',
                     s.get('process') or '', st.get('reason') or '-',
                     str(st.get('seconds', '-')), str(l.get('needs_you')),
                     str(l.get('cost_usd', 0)), str(seats.get('used','-')), str(len(lanes))]))
" >> "$OUT"
  fi
  sleep 60
done
