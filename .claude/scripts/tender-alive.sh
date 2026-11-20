#!/usr/bin/env bash
# Is the lane tender actually running? Answered from its HEARTBEAT, never from a process list.
#
# `pgrep -f lane-tender.sh` matches the shell that runs the grep, and `$!` after `setsid ... &`
# holds the wrapper's pid, not the script's. Both reported a healthy process three times while the
# script was dead. A timestamp the loop writes each pass cannot do that.
set -euo pipefail
BEAT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.factory/night/tender.beat"
[ -f "$BEAT" ] || { echo "DEAD — no heartbeat file at all"; exit 1; }
AGE=$(( $(date +%s) - $(cat "$BEAT") ))
if [ "$AGE" -le 180 ]; then echo "alive — last beat ${AGE}s ago"; else
  echo "DEAD — last beat ${AGE}s ago (the loop sleeps 60s; over 180s means it stopped)"; exit 1
fi
