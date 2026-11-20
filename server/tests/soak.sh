#!/bin/bash
#
# soak.sh — run a test (or the whole suite) many times UNDER LOAD, and report what was not
# consistently green.  SRV-FIX-034.
#
# Why this exists.  `test_agent.py::test_kill_wedged_process` was load-sensitive for two days and
# was counted as evidence by eight merged tickets while it was.  Its first recorded failure is in
# `docs/reports/SRV-FIX-030/SRV-FIX-030-red-2026-07-28.log` — committed, in a merged ticket, a day
# before anybody noticed, and invisible because that log was SUPPOSED to be red and nothing
# distinguished "red because the feature is not built yet" from "red for an unrelated reason".
# It was finally caught by a human reading a subagent's log and recognising a name.
#
# A green run of a load-sensitive test is not evidence, and the reason nobody knew is that there
# was no way to ask.  Asking is now one command:
#
#   ./tests/soak.sh                                   # whole suite x20, 4 concurrent suites
#   ./tests/soak.sh -n 100 tests/test_agent.py::test_kill_wedged_process
#   ./tests/soak.sh -n 30 -l 8 tests/test_agent.py    # heavier load, one module
#
# The load is what a parallel batch actually does to a laptop: N more copies of the suite running
# beside the one being measured.  Nothing here is a timeout, a retry or a sleep — the load exists
# to make a latent race appear, not to be waited out.
#
# Exit status is 0 only when every run was green.  Every failing run's output is kept.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$(dirname "$TESTS_DIR")"
export PYTHONPYCACHEPREFIX="$(dirname "$SERVER_DIR")/.cache/python"

RUNS=20
LOAD=4
BURN=$(( $(nproc) * 3 ))
OUT=""

usage() {
  cat >&2 <<EOF
usage: $0 [-n RUNS] [-l CONCURRENT_SUITES] [-c BURNERS] [-o OUTDIR] [pytest-target ...]

  -n RUNS    how many times to run the target        (default $RUNS)
  -l LOAD    copies of the full suite alongside it   (default $LOAD; 0 = none)
  -c BURNERS busy loops oversubscribing the CPU      (default $BURN = 3x nproc; 0 = none)
  -o OUTDIR  where to keep the logs                  (default a mktemp -d)

  pytest-target defaults to the whole suite.  A soak of the whole suite also reports every
  individual test id that failed in at least one run.

  BOTH kinds of load matter, and that is a finding rather than a guess.  SRV-FIX-034's race is a
  window between a fork and the exec that follows it, so what widens it is scheduling latency:
  four concurrent suites alone reproduced nothing in 12 runs, and the same four with the CPU
  oversubscribed turned the same test red 24 times in 25.  A soak with -c 0 is a much weaker
  question than it looks.
EOF
  exit 2
}

while getopts ":n:l:c:o:h" opt; do
  case "$opt" in
    n) RUNS=$OPTARG ;;
    l) LOAD=$OPTARG ;;
    c) BURN=$OPTARG ;;
    o) OUT=$OPTARG ;;
    h) usage ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))

TARGET=("$@")
[ ${#TARGET[@]} -eq 0 ] && TARGET=("tests")

[ -n "$OUT" ] || OUT=$(mktemp -d -t factory-soak-XXXXXX)
mkdir -p "$OUT"
RUNNING="$OUT/.running"

cleanup() {
  rm -f "$RUNNING"
  # The load loops check for that file between runs; the pytest they are inside has to be told.
  pkill -P $$ >/dev/null 2>&1
  wait 2>/dev/null
}
trap cleanup EXIT INT TERM

cd "$SERVER_DIR" || exit 2
[ -x ../.cache/server/build/factory-server ] || {
  echo "build first — cd factory/server && ./build.sh build" >&2
  exit 2
}

echo "soak: ${TARGET[*]}"
echo "      $RUNS run(s), $LOAD concurrent suite(s) + $BURN CPU burner(s) as load"
echo "      logs in $OUT"
echo

if [ "$LOAD" -gt 0 ] || [ "$BURN" -gt 0 ]; then
  touch "$RUNNING"
  for i in $(seq 1 "$LOAD"); do
    (
      while [ -f "$RUNNING" ]; do
        python3 -m pytest tests -q -p no:cacheprovider > "$OUT/load-$i.log" 2>&1
      done
    ) &
  done
  for i in $(seq 1 "$BURN"); do
    ( while [ -f "$RUNNING" ]; do :; done ) &
  done
  # Let the load reach steady state, so run 1 is measured under the same conditions as run N.
  # This is not a synchronisation sleep: nothing below depends on it having been long enough.
  sleep 10
fi

fails=0
for n in $(seq 1 "$RUNS"); do
  log="$OUT/run-$n.log"
  if python3 -m pytest "${TARGET[@]}" -q -p no:cacheprovider > "$log" 2>&1; then
    printf 'run %3d/%d  PASS\n' "$n" "$RUNS"
    rm -f "$log"
  else
    fails=$((fails + 1))
    printf 'run %3d/%d  FAIL  %s\n' "$n" "$RUNS" "$log"
    grep -E "^(FAILED|ERROR) " "$log" | sed 's/^/            /'
  fi
done

echo
if [ "$fails" -eq 0 ]; then
  echo "=== ${TARGET[*]}: $RUNS/$RUNS green under $LOAD concurrent suite(s) + $BURN burner(s) ==="
else
  echo "=== ${TARGET[*]}: $fails of $RUNS runs FAILED under $LOAD concurrent suite(s) + $BURN burner(s) ==="
  echo
  echo "tests that were not green in every run:"
  grep -hE "^(FAILED|ERROR) " "$OUT"/run-*.log 2>/dev/null \
    | awk '{print $2}' | sort | uniq -c | sort -rn | sed 's/^/  /'
  echo
  echo "A test that is green most of the time is not a gate. Read the kept logs in $OUT."
fi

exit $((fails > 0))
