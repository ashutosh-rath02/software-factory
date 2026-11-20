#!/usr/bin/env bash
# Start a gate that OUTLIVES the session that started it, and RETURN — SRV-FIX-269 round 2.
#
#   .claude/scripts/run-gate.sh '<command>' <logfile>
#
# Why this file exists instead of a line in a skill.
#
# Round 1 of `SRV-FIX-269` told a QA session to start a long gate with `run_in_background` and
# wait for the completion notification. That works for a session running inside a factory which
# re-invokes it. A headless one-shot session gets ONE turn: when the model stops emitting, the
# process exits and everything still in its process group goes with it. Measured twice — a sealed
# sandbox and this ticket's own QA pass 2 — each following the rule exactly, each leaving a suite
# log ending at `[  8%]` with no summary and no verdict. The instruction was correct in spirit and
# unexecutable by the session type it was handed to, which is `SRV-FIX-124`'s defect one mechanism
# over.
#
# `setsid` is what fixes it: the gate becomes its own session leader, outside the caller's process
# group, so killing the caller's group does not reach it. And the `&` lives INSIDE this file, so
# the command a session types has none — the same reason `start-qa.sh` is a script (`SRV-FIX-124`).
#
# The gate therefore survives every session that starts it. A session that dies mid-wait costs a
# relaunch and nothing else: the next one finds the log already complete and quotes it at once.
set -u

CMD="${1:?usage: run-gate.sh '<command>' <logfile>}"
LOG="${2:?usage: run-gate.sh '<command>' <logfile>}"

mkdir -p "$(dirname "$LOG")"

# `GATE_EXIT=` is appended after the command finishes, so a reader can tell three states apart:
# no line yet (running), a line with 0 (green), a line with anything else (red). A log that simply
# stops — which is what a killed gate leaves — has no line at all, and that is the state this
# script exists to make impossible for the ordinary case.
GATE_CMD="$CMD" GATE_LOG="$LOG" setsid nohup bash -c '
  { eval "$GATE_CMD"; } > "$GATE_LOG" 2>&1
  echo "GATE_EXIT=$?" >> "$GATE_LOG"
' < /dev/null > /dev/null 2>&1 &

echo "gate started, detached from this session"
echo "  command: $CMD"
echo "  log:     $LOG"
echo "It survives this session. Poll the log for its summary line, or relaunch and read it there:"
echo "  grep -E 'GATE_EXIT=' $LOG    # absent = still running"
