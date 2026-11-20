#!/usr/bin/env bash
# Everything working on this repository right now, in one place — lanes AND the sessions the tool
# cannot see.
#
#   .claude/scripts/status.sh
#
# The Lanes board answers for lanes and is right about them. It cannot answer for a session started
# outside one, because the server only records a run it started itself (`FAC-EPIC-372`): on
# 2026-09-06 a QA session judged a ticket for eleven minutes while the board was empty, and every
# verdict that day was produced that way. This is the stopgap, and it should be deleted the day
# that epic lands.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$REPO"
API=http://127.0.0.1:7777

echo "── lanes ─────────────────────────────────────────────────────────────────"
if LANES=$(curl -s --max-time 5 "$API/api/lanes" 2>/dev/null) && [ -n "$LANES" ]; then
  printf '%s' "$LANES" | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d['seats']['host']
lanes = d.get('lanes', [])
print(f\"   seats {s['used']}/{s['total']}\")
if not lanes:
    print('   no lane is claimed')
for l in lanes:
    st = l.get('state') or {}
    stop = st.get('stopped') or {}
    why = stop.get('reason', '')
    age = f\"{stop.get('seconds')}s\" if stop.get('seconds') is not None else ''
    print('   %-9s %-15s %-4s %-9s %-12s %-8s \$%.2f' % (
        l['lane'], l['ticket_id'], l.get('role') or '', st.get('process') or '',
        why, age, l.get('cost_usd', 0)))
"
else
  echo "   the server is not answering on :7777 — nothing here can be trusted"
fi

echo "── agents the tool did not start (FAC-EPIC-372) ──────────────────────────"
found=0
while read -r pid etime rest; do
  [ -z "${pid:-}" ] && continue
  ticket=$(printf '%s' "$rest" | grep -oE '(run-ticket|qa-ticket) [^ ]+' | sed 's|docs/tickets/||' || true)
  [ -z "$ticket" ] && continue
  printf '   pid %-8s %-9s %s\n' "$pid" "$etime" "$ticket"
  found=1
done < <(ps -eo pid,etime,args --no-headers 2>/dev/null | grep '[c]laude -p' || true)
[ "$found" -eq 0 ] && echo "   none"

echo "── branches holding unlanded work ────────────────────────────────────────"
git fetch -q origin 2>/dev/null || true
any=0
for b in $(git branch --format='%(refname:short)' | grep -v '^main$'); do
  n=$(git rev-list --count origin/main.."$b" 2>/dev/null || echo 0)
  [ "$n" = "0" ] && continue
  id=$(printf '%s' "$b" | grep -oE '[A-Z]{2,}(-[A-Z]+)*-[0-9]+' | head -1)
  st=""
  if [ -n "$id" ]; then
    f=$(ls docs/tickets/"$id"-*.md 2>/dev/null | head -1)
    [ -n "$f" ] && st=$(sed -n 's/^status:[[:space:]]*//p' "$f" | head -1)
  fi
  printf '   %-32s %-3s commits  %s\n' "$b" "$n" "$st"
  any=1
done
[ "$any" -eq 0 ] && echo "   none — the tree is clean"
exit 0
