#!/usr/bin/env bash
# WEB-FIX-101 — the gate's own exit assertions.
#
# The ticket this belongs to was filed on a false premise: that `npm run gate` exits 0 when the
# build it depends on failed. It does not, and never did — `run-gate.sh` has `set -euo pipefail`
# and the EXIT trap only overrides the status when the reap fails. The `exit=0` in the original
# ticket came from reading a COMPOUND command's status:
#
#     $ bash -c 'false > /dev/null 2>&1; echo "exit=$?"; echo done | tail -1'
#     exit=1
#     done
#       ^ the compound overall exits 0, and that is what was quoted as "the gate's exit code"
#
# What is genuinely wrong is one line. A run that fails at the build ends:
#
#     tests/baselines.test.ts(196,9): error TS6133: 'quotesIn' is declared but ...
#       PASS  the gate reaped its fixture agent (pid 195685)
#
# The last line of a FAILED run reads `PASS`. The reap message is honest — the agent was reaped,
# and SRV-FIX-038 put that line there because a leaked fixture agent was invisible — but a reader
# scanning the tail of a log sees a pass at the bottom of a failure.
#
# So this asserts three things, and NOT the exit code's machinery, which §1 pre-decided is out of
# scope: any diff touching `set -e` or the trap's status handling is evidence the ticket was
# misread a second time.
#
#   1. a failing run's LAST line states the failure and its exit code
#   2. NEGATIVE — a green run's last line does not say failed, and its status is still 0
#   3. NEGATIVE — the reap line is still there on the failing path (SRV-FIX-038 stays closed)
#
# WEB-FIX-380 adds three more, about what a KILLED gate leaves behind. Every case is a function
# named the way §1 names it, so `bash gate/test-run-gate.sh <case> [<case>…]` runs exactly the
# cases a criterion cites; with no argument it runs all of them, which is what `npm run
# test:gate-exit` does.
#
# WEB-FEAT-379 adds the two the SCOPE owes, by the same pattern:
#
#   4. a SCOPED run photographs exactly what it was given and its exit status is decided by the
#      checks it RAN — §1 pre-decided that a case about the summary may use the scoped gate
#   5. NEGATIVE — an unknown surface or theme exits 2 BEFORE the browser lock is taken, naming it
#      and listing the declared names, with nothing photographed
#   6. every run says what each phase cost, in seconds, with a total — quantified over EVERY run
#      this file makes, because §1 criterion 4 is quantified over *any* gate run
#
# Run: cd factory/client/web && bash gate/test-run-gate.sh
set -uo pipefail

CLIENT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CLIENT"

VICTIM="tests/baselines.test.ts"
BACKUP="$(mktemp)"
FAIL_LOG="$(mktemp)"
GREEN_LOG="$(mktemp)"
failures=0
FOREIGN_PID=""

# ── EVERY CASE RUNS ALONE (WEB-FIX-405) ───────────────────────────────────────────────────────
# `bash gate/test-run-gate.sh <case>` is how §1 cites a criterion, and two of this file's cases ran
# at the top level instead of being cases — so naming one still cost a whole scoped gate run, and
# the phase summary at the bottom read `$LOCK_LOG` whether or not the case that writes it had run.
# Under `set -u` that is an unbound-variable death AFTER the checks, which turns a green single-case
# run into a non-zero exit with no summary.
#
# So every log this file's cases write is declared here, empty, and the summary below is quantified
# over the runs that actually happened rather than over the five it knows how to name.
LOCK_LOG=""; EARLY_LOG=""; SCOPED_LOG=""; BAD_LOG=""; THEME_LOG=""
SCOPED_STATUS=""

# ── EVERY GATE RUN THIS INVOCATION MADE, RECORDED WHERE IT WAS MADE ──────────────────────────
# `WEB-FEAT-379`'s phase-cost rule is quantified over *any gate run*, and it was asked of five
# logs named at the bottom of this file — so the two runs `two_gates_do_not_run_at_once` makes and
# the two `two_consecutive_runs_are_both_green` makes were outside the quantifier the day they
# were written. A list of logs typed at the bottom is bounded by what its author remembered, which
# is `_TEMPLATE.md` §2's first way a check lies, one level below the check it lies about.
GATE_RUNS=()
record_run() { GATE_RUNS+=("$1:$2"); }

restore() {
  # -s, not -f: `BACKUP` is an empty mktemp file until the case that injects a type error fills
  # it, and a restore that copies the empty one over the victim deletes a test file. It could not
  # happen while the injecting case was always the first thing the script did; it can now that a
  # case can be run on its own.
  [ -s "$BACKUP" ] && cp "$BACKUP" "$VICTIM" && rm -f "$BACKUP"
  # WEB-FIX-380 — the foreign preview of `a_foreign_preview_survives_the_gates_cleanup` is this
  # script's own child, and a leak check that leaks is not one.
  [ -n "$FOREIGN_PID" ] && kill -KILL -- "-$FOREIGN_PID" 2>/dev/null
  return 0
}
trap restore EXIT

# ── THE GATE'S LAST LINE, AND NOT NPM'S (WEB-FIX-405) ─────────────────────────────────────────
# `WEB-FIX-101`'s criterion is about what the GATE prints last. These cases run `npm run gate`,
# because that is what a person types, and npm appends its own chatter to the same stream: an
# update notice ("New major version of npm available!") landed after the banner on 2026-09-12 and
# turned `and its LAST line says the gate failed` red with `last line was: npm notice`.
#
# So the narrowing is by CATEGORY, and the category is *lines npm emits about itself*: `npm notice`
# and `npm warn`, which npm prefixes for exactly this reason. Nothing the gate prints carries
# either prefix. `the_last_line_helper_still_sees_a_missing_banner` is the case that keeps this
# from quietly swallowing a real failure.
gate_last_line() {              # <log> — the last line the GATE printed
  grep -v '^[[:space:]]*$' "$1" | grep -vE '^npm (notice|warn)\b' | tail -1
}

check() {   # check <name> <condition-result> <detail>
  if [ "$2" = 0 ]; then
    echo "  PASS  $1"
  else
    echo "  FAIL  $1"
    echo "        $3"
    failures=$((failures + 1))
  fi
}

# ── 1 + 3 · the failing path ──────────────────────────────────────────────────────────────────
# A TS6133 (declared but never read) passes vitest and fails `tsc -b`, so the gate dies at the
# build step — after the fixture agent has been started, which is what makes case 3 reachable.
a_failed_build_says_so_on_its_last_line() {
  echo "── injecting a type error and running the gate ────────────────────────────────"
  cp "$VICTIM" "$BACKUP"
  printf '\nconst har_fix_101_unused = 1\n' >> "$VICTIM"

  npm run gate > "$FAIL_LOG" 2>&1
  FAIL_STATUS=$?
  record_run "a broken build" "$FAIL_LOG"
  restore

  FAIL_LAST="$(gate_last_line "$FAIL_LOG")"
  echo "  gate exit   = $FAIL_STATUS"
  echo "  last line   = $FAIL_LAST"

  [ "$FAIL_STATUS" -ne 0 ]
  check "a broken build makes the gate exit non-zero" $? \
        "exit was $FAIL_STATUS — §1 says this already worked; if it is 0 the premise changed"

  grep -q 'GATE FAILED' <<<"$FAIL_LAST"
  check "the LAST line of a failed run says the gate failed" $? \
        "last line was: $FAIL_LAST"

  grep -q "exit $FAIL_STATUS" <<<"$FAIL_LAST"
  check "and that line carries the exit code" $? \
        "expected 'exit $FAIL_STATUS' in: $FAIL_LAST"

  # NEGATIVE (mandatory) — SRV-FIX-038 exists because a leaked fixture agent was invisible. Tidying
  # the tail by suppressing the reap line would re-open it, so the line must survive on this path.
  grep -qE '(PASS|FAIL)  the gate (reaped|leaked) its fixture agent' "$FAIL_LOG"
  check "the reap line is still present on the failing path (SRV-FIX-038)" $? \
        "no reap line in the failing run's output at all"
}

# ── 1b · the SAME class, above the trap (WEB-FIX-101 QA finding 1 / WEB-FIX-155) ──────────────
# Criterion 1 is quantified over every non-zero exit of run-gate.sh and case 1 asserts it at one of
# them — a TS6133 that dies at the build step, line 202 of a 220-line script, deep inside the
# trap's region. The eighty-two lines ABOVE `trap cleanup EXIT` are where the quantifier is false,
# and QA reached them from the script alone. Two points in the class, not one.
#
# Both cases below cost seconds: neither reaches vitest.
# ── WEB-FIX-384 · the case must HOLD a lock, or it is measuring something else ────────────────
# Run in a lane, `mktemp -u /tmp/...` could not create the holder's lock file at all, so `flock`
# never took anything, `run-gate.sh` died at `exec 9>"$LOCK"` — a different exit, above the trap,
# that happens to be non-zero and print the same banner — and both checks passed having exercised
# nothing about the lock. `${TMPDIR:-/tmp}` is the spelling `tests/gate-scripts.test.ts` enforces
# over this whole directory.
#
# The fixture is asserted before the gate is run: the holder is required to be HOLDING, and the
# gate's output is required to carry the two lines only a real queue produces — `another browser
# gate holds …` and `gave up waiting for …`. A non-zero exit on its own is what let this pass.
lock_fixture_problem() {        # <lock path> — why this case cannot measure what it is named for
  : > "$1" 2>/dev/null || { echo "the lock file $1 could not be created"; return 0; }
  flock -n "$1" true 2>/dev/null || { echo "$1 cannot be locked from this session"; return 0; }
  return 0
}

the_lock_give_up_exits_above_the_trap_and_says_so() {
  echo
  echo "── the lock give-up, which exits above the trap ───────────────────────────────"
  LOCKF="$(mktemp -u "${TMPDIR:-/tmp}/web-fix-101-lock.XXXXXX")"
  LOCK_LOG="$(mktemp)"
  LOCK_FIXTURE="$(lock_fixture_problem "$LOCKF")"
  flock "$LOCKF" sleep 30 &
  HOLDER=$!
  sleep 0.4
  # The holder is HOLDING, asserted rather than assumed: `flock -n` from here must now fail.
  HELD=1; flock -n "$LOCKF" true 2>/dev/null && HELD=0
  FACTORY_GATE_LOCK="$LOCKF" FACTORY_GATE_LOCK_WAIT=2 npm run gate > "$LOCK_LOG" 2>&1
  LOCK_STATUS=$?
  record_run "the lock give-up" "$LOCK_LOG"
  kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null; rm -f "$LOCKF"
  LOCK_LAST="$(gate_last_line "$LOCK_LOG")"
  echo "  gate exit   = $LOCK_STATUS"
  echo "  last line   = $LOCK_LAST"

  [ -z "$LOCK_FIXTURE" ] && [ "$HELD" = 1 ]
  check "this case is holding a real lock on $LOCKF" $? \
        "${LOCK_FIXTURE:-flock -n still succeeded while the holder was supposed to be holding} \
— WEB-FIX-384: a case that cannot make its fixture measures the gate's unrelated failure"

  [ "$LOCK_STATUS" -ne 0 ]
  check "giving up on the browser lock exits non-zero" $? \
        "exit was $LOCK_STATUS"

  grep -q 'another browser gate holds' "$LOCK_LOG"
  check "the gate announced that it was QUEUEING behind the held lock" $? \
        "it never said it was waiting: $(tail -4 "$LOCK_LOG" | tr '\n' '|')"

  grep -q 'gave up waiting for' "$LOCK_LOG"
  check "and it gave up on the wait, rather than failing to open the file" $? \
        "no give-up line: $(tail -4 "$LOCK_LOG" | tr '\n' '|')"

  grep -q 'GATE FAILED' <<<"$LOCK_LAST"
  check "and its LAST line says the gate failed" $? \
        "last line was: $LOCK_LAST"
}

# ── WEB-FIX-405 D-NEG · the case above must FAIL when it cannot hold a lock ───────────────────
# `WEB-FIX-384` is a case that passed on a lock that never existed. This is the case that says the
# repair has teeth: with the lock directory made unwritable, the fixture check must report the path
# and refuse, rather than the pair of exit-code assertions passing on the gate's own death.
the_lock_give_up_fails_when_it_cannot_hold_a_lock() {
  echo
  echo "── WEB-FIX-405 · NEGATIVE · the give-up case with nowhere to put its lock ─────"
  local dir problem
  dir="$(mktemp -d)"
  chmod 500 "$dir"
  problem="$(lock_fixture_problem "$dir/impossible.lock")"
  chmod 700 "$dir"; rm -rf "$dir"

  [ -n "$problem" ]
  check "the_lock_give_up_fails_when_it_cannot_hold_a_lock" $? \
        "a lock file that CANNOT be created was reported as a usable fixture, so \
the_lock_give_up_exits_above_the_trap_and_says_so would pass on the gate's unrelated failure"
  echo "  said        = ${problem:-(nothing — the fixture check saw no problem)}"

  # …and it is not simply always red: a writable directory is a usable fixture.
  local fine ok_dir
  ok_dir="$(mktemp -d)"
  fine="$(lock_fixture_problem "$ok_dir/fine.lock")"
  rm -rf "$ok_dir"
  [ -z "$fine" ]
  check "and a lock it CAN hold is not reported as a problem" $? \
        "a usable lock path was refused: $fine"
}

a_pre_trap_death_says_so_rather_than_dying_silently() {
  echo
  echo "── a pre-trap set -e death, which prints nothing of its own ───────────────────"
  SHIM="$(mktemp -d)"; printf '#!/bin/sh\nexit 1\n' > "$SHIM/python3"; chmod 755 "$SHIM/python3"
  EARLY_LOG="$(mktemp)"
  PATH="$SHIM:$PATH" bash gate/run-gate.sh > "$EARLY_LOG" 2>&1
  EARLY_STATUS=$?
  record_run "a pre-trap death" "$EARLY_LOG"
  rm -rf "$SHIM"
  EARLY_LAST="$(gate_last_line "$EARLY_LOG")"
  echo "  gate exit   = $EARLY_STATUS"
  echo "  last line   = $EARLY_LAST"

  [ "$EARLY_STATUS" -ne 0 ]
  check "a pre-trap failure exits non-zero" $? \
        "exit was $EARLY_STATUS"

  grep -q 'GATE FAILED' <<<"$EARLY_LAST"
  check "and it says so, rather than dying silently" $? \
        "last line was: ${EARLY_LAST:-(no output at all)}"
}

# ── WEB-FEAT-379's two cases run HERE, above the green path, and the order is load-bearing ────
# An unscoped sweep leaves 70 files in `gate/shots/`, and eight of them differ from the committed
# corpus on any checkout whose fixture path is a different length — so the NEXT run's vitest phase
# is red before it reaches a browser. That is `WEB-FIX-381`, filed with its own evidence and not
# this ticket's to fix; putting these two above the green path is how they are measured against
# the gate rather than against that defect.
# ── 4 · WEB-FEAT-379 · a SCOPED run, and what it says each phase cost ─────────────────────────
# §1 criterion 4 asks every run to print what each phase cost, and §1 pre-decided that a case
# about the SUMMARY may run the scoped gate rather than the full one. The phase names are read off
# `run-gate.sh` itself — a list typed here would be bounded by what this file's author remembered,
# which is `_TEMPLATE.md` §2's first way a check lies, and a sixth phase added tomorrow would go
# unasserted.
a_scoped_run_photographs_what_it_was_given() {
  echo
  echo "── a scoped gate run (two surfaces, one theme) ────────────────────────────────"
  SCOPED_LOG="$(mktemp)"
  SCOPED_START=$(date +%s)
  npm run gate -- --surfaces tickets,ticket-md --themes dark > "$SCOPED_LOG" 2>&1
  SCOPED_STATUS=$?
  record_run "the scoped run" "$SCOPED_LOG"
  SCOPED_ELAPSED=$(( $(date +%s) - SCOPED_START ))
  SCOPED_LAST="$(gate_last_line "$SCOPED_LOG")"
  echo "  gate exit   = $SCOPED_STATUS"
  echo "  elapsed     = ${SCOPED_ELAPSED}s"
  echo "  last line   = $SCOPED_LAST"

  # The gate's vitest phase is flaky on a loaded machine — `docs/operations/ESCAPES.md`, 2026-08-22, is the
  # same class — and everything below is about what the SWEEP did, so the run has to have reached it.
  # Said as its own failure, naming the phase it died in, rather than as six assertions each blaming
  # the summary for a run that never got there.
  if grep -qE '^=== [0-9]+/[0-9]+ checks passed' "$SCOPED_LOG"; then

    grep -q '  scope: 2 surfaces × 1 theme' "$SCOPED_LOG"
    check "the run says what its scope was" $? \
          "no scope line: $(grep -i scope "$SCOPED_LOG" | head -3 | tr '\n' '|')"

    grep -qE '^=== .*skipped\(scope\): [1-9][0-9]*$' "$SCOPED_LOG"
    check "and counts the checks it did not make, rather than passing them" $? \
          "$(grep '^=== ' "$SCOPED_LOG")"

    SCOPED_SHOTS="$(cd gate/shots && ls *.png 2>/dev/null | sort | tr '\n' ' ')"
    SCOPED_SHOTS="${SCOPED_SHOTS% }"
    [ "$SCOPED_SHOTS" = "ticket-md-dark.png tickets-dark.png" ]
    check "and photographed exactly the two surfaces it was given, in the one theme" $? \
          "gate/shots holds: ${SCOPED_SHOTS:-nothing}"

    SCOPED_RAN_FAILED="$(grep -oE '^=== [0-9]+/[0-9]+ checks passed, [0-9]+ failed' "$SCOPED_LOG" \
      | grep -oE '[0-9]+ failed' | grep -oE '^[0-9]+')"
    SCOPED_SKIPPED="$(grep -oE 'skipped\(scope\): [0-9]+' "$SCOPED_LOG" | grep -oE '[0-9]+$')"
    if [ "${SCOPED_RAN_FAILED:-none}" = 0 ]; then [ "$SCOPED_STATUS" = 0 ]; else
      [ -n "${SCOPED_RAN_FAILED:-}" ] && [ "$SCOPED_STATUS" != 0 ]; fi
    check "a scoped run's exit status is decided by the checks it RAN, never by the ones it skipped" \
          $? "${SCOPED_RAN_FAILED:-no summary line} of the checks it ran failed, \
  ${SCOPED_SKIPPED:-no} were skipped by the scope, and the gate exited $SCOPED_STATUS"

    [ "$SCOPED_ELAPSED" -lt 300 ]
    check "and it finished in under five minutes (WEB-FEAT-379 §1 criterion 5)" $? \
          "took ${SCOPED_ELAPSED}s"
  else
    check "the scoped run reached its sweep, so what the sweep did can be judged" 1 \
          "it died after $(grep -oE '^  phase [a-z]+' "$SCOPED_LOG" | awk '{print $2}' | tr '\n' ' ')\
  — tail: $(tail -4 "$SCOPED_LOG" | tr '\n' '|')"
  fi
}

# ── 5 · MANDATORY NEGATIVE (§1 criterion 6) · an unknown scope, refused above the lock ────────
# Two points, not one. The exit code alone is satisfied by a script that takes the browser lock,
# waits an hour behind another gate and then refuses; §1 says *before any phase runs*, and the lock
# is the first thing that can block. So the refusal is measured with the lock HELD by somebody
# else: a run that queued would sit there for `FACTORY_GATE_LOCK_WAIT` seconds and say so.
an_unknown_scope_is_refused_before_the_lock() {
  echo
  echo "── an unknown surface, with the browser lock held by somebody else ────────────"
  SHOTS_BEFORE="$(ls gate/shots 2>/dev/null | wc -l)"
  SCOPE_LOCK="$(mktemp -u "${TMPDIR:-/tmp}/web-feat-379-lock.XXXXXX")"
  flock "$SCOPE_LOCK" sleep 25 &
  SCOPE_HOLDER=$!
  sleep 0.4
  BAD_LOG="$(mktemp)"
  BAD_START=$(date +%s)
  FACTORY_GATE_LOCK="$SCOPE_LOCK" FACTORY_GATE_LOCK_WAIT=25 \
    npm run gate -- --surfaces nosuch > "$BAD_LOG" 2>&1
  BAD_STATUS=$?
  BAD_ELAPSED=$(( $(date +%s) - BAD_START ))
  kill "$SCOPE_HOLDER" 2>/dev/null; wait "$SCOPE_HOLDER" 2>/dev/null; rm -f "$SCOPE_LOCK"
  SHOTS_AFTER="$(ls gate/shots 2>/dev/null | wc -l)"
  echo "  gate exit   = $BAD_STATUS"
  echo "  elapsed     = ${BAD_ELAPSED}s (the lock was held for 25)"
  echo "  said        = $(grep -m1 unknown "$BAD_LOG" | cut -c1-120)"

  [ "$BAD_STATUS" = 2 ]
  check "an_unknown_scope_exits_2_before_the_lock_is_taken" $? \
        "exit was $BAD_STATUS, not 2 — $(tail -3 "$BAD_LOG" | tr '\n' '|')"

  [ "$BAD_ELAPSED" -lt 20 ]
  check "and it refused at once rather than queueing behind the held lock" $? \
        "took ${BAD_ELAPSED}s while another process held the lock for 25"

  ! grep -q 'another browser gate holds' "$BAD_LOG"
  check "and it never announced a wait, because it never asked for the lock" $? \
        "$(grep 'another browser gate holds' "$BAD_LOG")"

  grep -q 'unknown surface "nosuch"' "$BAD_LOG"
  check "and the refusal names the surface nobody declared" $? \
        "$(tail -3 "$BAD_LOG" | tr '\n' '|')"

  # …and lists what a person may say instead, derived from the gate's own table rather than typed:
  # a refusal naming three of twenty-six is a refusal somebody has to read the source to act on.
  DECLARED_MISSING=""
  for n in $(node -e "import('./gate/surfaces.mjs').then((m) => console.log(
    [...m.SURFACES, ...m.LIVE_SURFACES].map(([s]) => s).join(' ')))"); do
    grep -q "$n" "$BAD_LOG" || DECLARED_MISSING="$DECLARED_MISSING $n"
  done
  [ -z "$DECLARED_MISSING" ]
  check "and lists every declared surface beside it" $? "not named in the refusal:$DECLARED_MISSING"

  ! grep -qE '^  phase ' "$BAD_LOG"
  check "and no phase ran — not the inventory, not vitest, not the build" $? \
        "$(grep -E '^  phase ' "$BAD_LOG" | tr '\n' '|')"

  [ "$SHOTS_BEFORE" = "$SHOTS_AFTER" ]
  check "and nothing was photographed" $? \
        "$SHOTS_BEFORE files in gate/shots before, $SHOTS_AFTER after"

  echo
  echo "── and an unknown theme, the same way ─────────────────────────────────────────"
  THEME_LOG="$(mktemp)"
  npm run gate -- --themes blue > "$THEME_LOG" 2>&1
  THEME_STATUS=$?
  echo "  gate exit   = $THEME_STATUS"
  echo "  said        = $(grep -m1 unknown "$THEME_LOG" | cut -c1-120)"

  [ "$THEME_STATUS" = 2 ]
  check "an unknown theme exits 2 as well" $? "exit was $THEME_STATUS"

  grep -q 'unknown theme "blue" — declared: dark · light' "$THEME_LOG"
  check "and names the theme and the two that exist" $? "$(tail -3 "$THEME_LOG" | tr '\n' '|')"

  # The refusal is about names the gate does not know, not about every name:
  # `a_scoped_run_photographs_what_it_was_given` ran `--surfaces tickets,ticket-md --themes dark` to
  # completion. Without that half this pair passes on a script that refuses everything — so when
  # that case did not run in this invocation, the comparison is made here rather than skipped.
  if [ -z "$SCOPED_STATUS" ]; then
    ACCEPTED_LOG="$(mktemp)"
    npm run gate -- --surfaces nosuch --themes dark > "$ACCEPTED_LOG" 2>&1
    SCOPED_STATUS=$?
    node gate/surfaces.mjs --surfaces tickets,ticket-md --themes dark > /dev/null 2>&1
    ACCEPTED=$?
    [ "$ACCEPTED" = 0 ]
    check "while a scope naming declared surfaces is not refused" $? \
          "the gate's own resolver refused --surfaces tickets,ticket-md --themes dark with $ACCEPTED"
    SCOPED_STATUS=""
  else
    [ "$SCOPED_STATUS" != 2 ]
    check "while a scope naming declared surfaces is not refused" $? \
          "the scoped run exited $SCOPED_STATUS, which is the refusal's own code"
  fi
}

# ── 2 · NEGATIVE (mandatory) · the green path ─────────────────────────────────────────────────
# The cheap fix for case 1 is to print the banner unconditionally. This is what refuses it.
a_green_run_exits_zero_and_does_not_say_failed() {
  echo
  echo "── running the gate green (this is the slow one) ──────────────────────────────"
  npm run gate > "$GREEN_LOG" 2>&1
  GREEN_STATUS=$?
  record_run "the whole sweep" "$GREEN_LOG"

  GREEN_LAST="$(gate_last_line "$GREEN_LOG")"
  echo "  gate exit   = $GREEN_STATUS"
  echo "  last line   = $GREEN_LAST"

  [ "$GREEN_STATUS" = 0 ]
  check "a green run still exits 0" $? \
        "exit was $GREEN_STATUS — see $GREEN_LOG"

  ! grep -qi 'GATE FAILED\|failed' <<<"$GREEN_LAST"
  check "a green run's last line does not say failed" $? \
        "last line was: $GREEN_LAST"
}

# ── WEB-FIX-380 · a killed gate leaves nothing behind ─────────────────────────────────────────
# `trap cleanup EXIT` is installed for one signal. SIGTERM, SIGINT and SIGHUP — `timeout`
# expiring, a session ending, Ctrl-C — end bash without running an EXIT trap, so the gate's
# preview, its server and its fixture agent stayed. Thirty-three `vite preview` processes were
# alive on the developer's machine on 2026-09-09, the oldest 5.6 days old, and each one still held
# the browser lock's file descriptor, which turns "one Chrome at a time" into "no gate ever again".
#
# Three things every case below does, and each of them is a lesson this repository has already
# paid for:
#
#   · the preview PORT comes from the gate's own announcement (`vite preview on 127.0.0.1:NNNNN`)
#     and never from a scan of the machine. A case that cannot find that line FAILS — it does not
#     skip, and it does not go looking for some other gate's port;
#   · the assertion is over the killed gate's whole descendant TREE, recorded from the process
#     table at the instant before the signal, and not over the four pids the script happens to
#     write down. `docs/operations/ESCAPES.md` 2026-07-27: `killGroup` asserted the one pid it had a number
#     for and leaked the one it never had, and "counting what you already knew about always comes
#     out even";
#   · each case runs the gate on a lock of its own (`FACTORY_GATE_LOCK`), so `flock -n` afterwards
#     is a statement about THIS gate rather than about whatever else is on the machine.

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }

descendants() {                 # every live pid below $1, read off the process table
  local kid
  for kid in $(pgrep -P "$1" 2>/dev/null); do
    echo "$kid"
    descendants "$kid"
  done
}

alive_of() {                    # of the pids on stdin, those still alive, with their command line
  local pid
  while read -r pid; do
    [ -n "$pid" ] || continue
    ps -o pid=,args= -p "$pid" 2>/dev/null
  done
  return 0
}

# Start a real `npm run gate`, wait until its preview answers, record what it has running, and
# send the SCRIPT — not npm, not the process group — a SIGTERM. It drives the product and asserts
# nothing; every judgment stays in the cases, which is the difference between a driver and a test
# that checks its own helper.
GATE_LOG=""; GATE_LOCK=""; GATE_PORT=""; GATE_AGENT=""; GATE_FIXTURE=""; GATE_TREE=""
GATE_NPM_PID=""; GATE_TREE_ARGS=""; GATE_NPM_STATUS=""; GATE_SCRIPT=""
start_a_gate_and_sigterm_the_script() {
  GATE_LOG="$(mktemp)"
  # ${TMPDIR:-/tmp} and not a literal /tmp: inside a lane /tmp is a read-only tmpfs, and a lock
  # this case cannot create is a case that measures nothing (docs/operations/ESCAPES.md, 2026-08-22).
  GATE_LOCK="$(mktemp -u "${TMPDIR:-/tmp}/web-fix-380-lock.XXXXXX")"
  GATE_PORT=""; GATE_AGENT=""; GATE_FIXTURE=""; GATE_TREE=""; GATE_TREE_ARGS=""
  GATE_NPM_STATUS=""

  FACTORY_GATE_LOCK="$GATE_LOCK" npm run gate > "$GATE_LOG" 2>&1 &
  GATE_NPM_PID=$!
  echo "  gate started · npm pid $GATE_NPM_PID · log $GATE_LOG"

  local _ script=""
  for _ in $(seq 1 6000); do    # ten minutes: this run runs vitest and builds before it previews
    kill -0 "$GATE_NPM_PID" 2>/dev/null || break
    GATE_PORT="$(sed -n 's/^vite preview on 127\.0\.0\.1:\([0-9]*\).*/\1/p' "$GATE_LOG" | head -1)"
    if [ -n "$GATE_PORT" ] && curl -sf "http://127.0.0.1:$GATE_PORT/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done

  GATE_AGENT="$(sed -n 's/^fixture at .* agent pid \([0-9]*\).*/\1/p' "$GATE_LOG" | head -1)"
  GATE_FIXTURE="$(sed -n 's/^fixture at \([^ ]*\) .*/\1/p' "$GATE_LOG" | head -1)"

  # `npm run gate` is npm → `sh -c 'bash gate/run-gate.sh'` → bash, and the `sh` in the middle
  # carries the same words in its argv. The wrapper is not the script: signalling it kills npm and
  # leaves run-gate.sh running as an orphan, which measures a different thing by accident. The
  # walk is depth-first from npm, so the first descendant that is actually a `bash` running that
  # path is the script itself and not one of the subshells it forks later.
  for script in $(descendants "$GATE_NPM_PID"); do
    if [ "$(ps -o comm= -p "$script" 2>/dev/null)" = bash ] \
       && ps -o args= -p "$script" 2>/dev/null | grep -q 'run-gate\.sh'; then
      break
    fi
    script=""
  done

  if [ -z "$GATE_PORT" ] || [ -z "$script" ]; then
    echo "  the gate never got as far as announcing a preview port it answers on:"
    tail -5 "$GATE_LOG" | sed 's/^/        /'
    kill -KILL "$GATE_NPM_PID" 2>/dev/null
    return 1
  fi

  GATE_SCRIPT="$script"
  GATE_TREE="$(descendants "$script")"
  GATE_TREE_ARGS="$(printf '%s\n' "$GATE_TREE" | alive_of)"
  echo "  preview answers on $GATE_PORT · run-gate.sh is pid $script · $(printf '%s\n' "$GATE_TREE" | grep -c .) live descendants"

  kill -TERM "$script"
  return 0
}

a_gate_killed_by_sigterm_leaves_no_child_and_frees_the_lock() {
  echo
  echo "── WEB-FIX-380 · a gate killed by SIGTERM mid-sweep ───────────────────────────"
  if ! start_a_gate_and_sigterm_the_script; then
    check "a_gate_killed_by_sigterm_leaves_no_child_and_frees_the_lock" 1 \
          "the gate never announced a preview port — a case that cannot find it fails"
    return
  fi

  # The sweep this case is quantified over, asserted non-empty BEFORE the kill: a tree with
  # nothing in it, or one without the preview node in it, makes every assertion below vacuous.
  [ -n "$GATE_TREE" ]
  check "the killed gate had live children to lose" $? \
        "run-gate.sh had no descendants at all at the moment of the signal"

  grep -q "vite preview --port $GATE_PORT" <<<"$GATE_TREE_ARGS"
  check "and the node that holds port $GATE_PORT is one of them" $? \
        "descendants at the signal were: $GATE_TREE_ARGS"

  # §1: within 5 seconds. The script itself is part of the wait — it holds the lock on fd 9 until
  # it is gone, and a lock still held by a gate that has not finished dying is not a leak.
  local _ port_held preview_left server_left tree_left agent_args lock_free
  for _ in $(seq 1 50); do
    port_held="$(ss -ltnH "sport = :$GATE_PORT" 2>/dev/null)"
    tree_left="$(printf '%s\n' "$GATE_TREE" | alive_of)"
    if [ -z "$port_held" ] && [ -z "$tree_left" ] && ! kill -0 "$GATE_SCRIPT" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done

  port_held="$(ss -ltnH "sport = :$GATE_PORT" 2>/dev/null)"
  preview_left="$(pgrep -af "vite preview --port $GATE_PORT" 2>/dev/null)"
  server_left="$(pgrep -af "factory-server --repo $GATE_FIXTURE" 2>/dev/null)"
  tree_left="$(printf '%s\n' "$GATE_TREE" | alive_of)"
  agent_args="$(ps -o args= -p "$GATE_AGENT" 2>/dev/null)"
  lock_free=1; flock -n "$GATE_LOCK" true 2>/dev/null && lock_free=0

  [ -z "$port_held" ]
  check "5 s after SIGTERM nothing holds the preview port $GATE_PORT" $? \
        "ss -ltn still reports: $port_held"

  [ -z "$preview_left" ]
  check "no 'vite preview --port $GATE_PORT' process is left" $? \
        "pgrep -af still reports: $preview_left"

  [ -z "$server_left" ]
  check "the gate's factory-server is gone" $? \
        "pgrep -af still reports: $server_left"

  ! grep -q '/run-ticket TKT-155' <<<"$agent_args"
  check "the fixture agent (pid $GATE_AGENT) is gone" $? \
        "ps -o args= still reports: $agent_args"

  [ -z "$tree_left" ]
  check "nothing of the killed gate's whole descendant tree is left" $? \
        "still alive: $tree_left"

  [ "$lock_free" = 0 ]
  check "flock -n on the gate's lock succeeds" $? \
        "$GATE_LOCK is still held — a leaked child inherited fd 9"

  wait "$GATE_NPM_PID" 2>/dev/null
  GATE_NPM_STATUS=$?
  [ "$GATE_NPM_STATUS" -ne 0 ]
  check "the caller of a killed gate sees a non-zero status, not 0" $? \
        "npm run gate exited $GATE_NPM_STATUS after its script was killed"
}

a_gate_killed_by_timeout_leaves_no_child() {
  echo
  echo "── WEB-FIX-380 · the same under \`timeout\`, the incident run-gate.sh records ───"
  local log lock tpid tree last="" agent fixture status port
  log="$(mktemp)"
  lock="$(mktemp -u "${TMPDIR:-/tmp}/web-fix-380-timeout.XXXXXX")"

  FACTORY_GATE_LOCK="$lock" timeout 20 npm run gate > "$log" 2>&1 &
  tpid=$!
  while kill -0 "$tpid" 2>/dev/null; do
    tree="$(descendants "$tpid")"
    [ -n "$tree" ] && last="$tree"
    sleep 0.5
  done
  wait "$tpid" 2>/dev/null
  status=$?

  agent="$(sed -n 's/^fixture at .* agent pid \([0-9]*\).*/\1/p' "$log" | head -1)"
  fixture="$(sed -n 's/^fixture at \([^ ]*\) .*/\1/p' "$log" | head -1)"
  port="$(sed -n 's/^vite preview on 127\.0\.0\.1:\([0-9]*\).*/\1/p' "$log" | head -1)"
  echo "  timeout exit = $status · fixture ${fixture:-(none)} · agent pid ${agent:-(none)}"

  # Non-empty sweep, again: `timeout 20` lands wherever it lands, and a run killed before it
  # started anything would satisfy every assertion below by having had nothing to leak.
  [ -n "$agent" ] && [ -n "$last" ]
  check "the killed run had started its fixture agent and a live tree" $? \
        "agent=${agent:-(none)} · descendants at the last look=${last:-(none)}"

  local _ tree_left server_left lock_free port_held preview_left agent_args
  for _ in $(seq 1 50); do
    tree_left="$(printf '%s\n' "$last" | alive_of)"
    [ -z "$tree_left" ] && break
    sleep 0.1
  done

  tree_left="$(printf '%s\n' "$last" | alive_of)"
  server_left="$(pgrep -af "factory-server --repo $fixture" 2>/dev/null)"
  agent_args="$(ps -o args= -p "$agent" 2>/dev/null)"
  lock_free=1; flock -n "$lock" true 2>/dev/null && lock_free=0
  port_held=""; preview_left=""
  if [ -n "$port" ]; then
    port_held="$(ss -ltnH "sport = :$port" 2>/dev/null)"
    preview_left="$(pgrep -af "vite preview --port $port" 2>/dev/null)"
  fi

  [ -z "$tree_left" ]
  check "nothing of the timed-out gate's descendant tree is left" $? \
        "still alive: $tree_left"

  [ -z "$server_left" ]
  check "no factory-server of that run is left" $? \
        "pgrep -af still reports: $server_left"

  ! grep -q '/run-ticket TKT-155' <<<"$agent_args"
  check "the fixture agent (pid $agent) is gone" $? \
        "ps -o args= still reports: $agent_args"

  [ -z "$port_held$preview_left" ]
  check "no preview of that run holds a port${port:+ ($port)}" $? \
        "ss: $port_held · pgrep: $preview_left"

  [ "$lock_free" = 0 ]
  check "flock -n on that run's lock succeeds" $? \
        "$lock is still held — a leaked child inherited fd 9"
}

# MANDATORY NEGATIVE — cleanup kills what the gate started, by process group, never by name. A
# `pkill -f "vite preview"` passes every assertion of the two cases above and takes the developer's
# other work with it; this is the case that refuses it.
a_foreign_preview_survives_the_gates_cleanup() {
  echo
  echo "── WEB-FIX-380 · NEGATIVE · a preview that is not the gate's is left alone ────"
  local fdir fport _ answered=1
  fdir="$(mktemp -d)"
  mkdir -p "$fdir/dist"
  printf '<!doctype html><title>not the gate</title>\n' > "$fdir/dist/index.html"
  fport="$(free_port)"

  # In its own process group, for the same reason run-gate.sh puts its children in one: `npx`
  # is a shell above the node process that holds the port, and this script has to be able to end
  # both of them however this case leaves off.
  # `vite.config.ts` refuses to start a preview without FACTORY_API_PORT rather than defaulting to
  # a port — SRV-FIX-027, and it is right. Nothing ever asks this preview for /api, so any free
  # port answers the question it is asking.
  set -m
  FACTORY_API_PORT="$(free_port)" \
    npx vite preview --outDir "$fdir/dist" --port "$fport" --strictPort > "$fdir/preview.log" 2>&1 &
  FOREIGN_PID=$!
  set +m
  for _ in $(seq 1 300); do
    if curl -sf "http://127.0.0.1:$fport/" >/dev/null 2>&1; then answered=0; break; fi
    sleep 0.1
  done

  [ "$answered" = 0 ]
  check "a foreign 'vite preview --port $fport --strictPort' is running before the gate" $? \
        "it never answered — see $fdir/preview.log"
  if [ "$answered" != 0 ]; then return; fi

  if ! start_a_gate_and_sigterm_the_script; then
    check "a_foreign_preview_survives_the_gates_cleanup" 1 \
          "the gate never announced a preview port — a case that cannot find it fails"
    return
  fi

  # Wait for the gate to be GONE, not merely for its preview to go. A cleanup that kills by name
  # does it in its last few hundred milliseconds, after the group kills and after the port has
  # already been released — a case that judges the neighbour before then passes on the very fix it
  # exists to refuse. (Measured: it did, once, on the `pkill -f "vite preview"` break.)
  local gate_left foreign_left foreign_answers
  for _ in $(seq 1 100); do
    gate_left="$(pgrep -af "vite preview --port $GATE_PORT" 2>/dev/null)"
    if [ -z "$gate_left" ] && ! kill -0 "$GATE_SCRIPT" 2>/dev/null; then break; fi
    sleep 0.1
  done

  gate_left="$(pgrep -af "vite preview --port $GATE_PORT" 2>/dev/null)"
  foreign_left="$(pgrep -af "vite preview --outDir $fdir/dist --port $fport" 2>/dev/null)"
  foreign_answers=1; curl -sf "http://127.0.0.1:$fport/" >/dev/null 2>&1 && foreign_answers=0

  # One assertion over both previews: the gate's is gone AND the neighbour's is not. Split in two
  # it passes on a cleanup that kills everything and on one that kills nothing.
  [ -z "$gate_left" ] && [ -n "$foreign_left" ] && [ "$foreign_answers" = 0 ]
  check "the gate's preview ($GATE_PORT) died and the foreign one ($fport) did not" $? \
        "gate's preview: ${gate_left:-(gone)} · foreign: ${foreign_left:-(KILLED — cleanup went by name)} · answers: $foreign_answers"

  wait "$GATE_NPM_PID" 2>/dev/null
  kill -KILL -- "-$FOREIGN_PID" 2>/dev/null
  FOREIGN_PID=""
  rm -rf "$fdir"
}


# ── WEB-FIX-405 A3 · two consecutive runs in one checkout are both green ──────────────────────
# The property the corpus exists for, asserted as a person meets it: run the gate, then run it
# again, in the same tree, and both exit 0.
#
# It could not hold while the fixture lived at a `mktemp -d` path — that suffix is rendered on
# `ticket-md` and `report-md`, so run two's pixels differ from run one's by construction — and it
# could not be SEEN while the corpus comparison ran before the build, because then run two compared
# run one's leftovers and run one compared nothing at all (`WEB-FIX-362`, `WEB-FIX-374`,
# `WEB-FIX-381`).
#
# Both runs are unscoped and whole: a scoped run photographs a subset and the second run's
# comparison would then be about a different set from the first's.
two_consecutive_runs_are_both_green() {
  echo
  echo "── WEB-FIX-405 · two whole gate runs, back to back, same checkout ─────────────"
  local one two s1 s2 shots1 shots2
  one="$(mktemp)"; two="$(mktemp)"

  npm run gate > "$one" 2>&1
  s1=$?
  record_run "consecutive run 1" "$one"
  shots1="$(ls gate/shots/*.png 2>/dev/null | wc -l)"
  echo "  run 1 exit  = $s1 · $shots1 shots · $(grep -oE 'total ([0-9]+m )?[0-9]+s' "$one" | tail -1)"

  npm run gate > "$two" 2>&1
  s2=$?
  record_run "consecutive run 2" "$two"
  shots2="$(ls gate/shots/*.png 2>/dev/null | wc -l)"
  echo "  run 2 exit  = $s2 · $shots2 shots · $(grep -oE 'total ([0-9]+m )?[0-9]+s' "$two" | tail -1)"

  # Non-empty sweep: two runs that photographed nothing would satisfy every assertion below.
  [ "$shots1" -gt 0 ] && [ "$shots2" -gt 0 ]
  check "both runs photographed something" $? \
        "run 1 left $shots1 shots, run 2 left $shots2"

  [ "$s1" = 0 ]
  check "the first gate run is green" $? \
        "exit $s1 — tail: $(tail -6 "$one" | tr '\n' '|')"

  [ "$s2" = 0 ]
  check "two_consecutive_runs_are_both_green" $? \
        "the SECOND run exited $s2 in a tree the first one had already run in — tail: \
$(tail -6 "$two" | tr '\n' '|')"

  # …and the second run is the one that had a fresh corpus to compare against, so its baselines
  # phase must have COMPARED something rather than reporting a comparison of nothing.
  grep -qE 'baselines · compared [1-9][0-9]* of [0-9]+' "$two"
  check "and the second run compared the corpus against the run that had just happened" $? \
        "$(grep -E 'baselines ·' "$two" | tail -2 | tr '\n' '|')"

  echo "  logs        = $one · $two"
}

# ── WEB-FIX-405 D1 · two gates do not run at once ────────────────────────────────────────────
# The guarantee `WEB-FIX-037` bought, asserted at the DEFAULT lock — the one a lane gets with no
# environment variable and nothing to remember. Every other lock case in this file points
# `FACTORY_GATE_LOCK` somewhere private on purpose; this is the one that says the default is a
# lock at all.
#
# Two gates released together, never a loop: a sequential probe cannot tell a lock from a counter
# (`SRV-FIX-188`). Gate A is started and waited for until it announces a preview, at which point it
# is provably past the lock; gate B is started while A holds it and must ANNOUNCE the wait and
# reach no preview of its own until A is gone.
two_gates_do_not_run_at_once() {
  echo
  echo "── WEB-FIX-405 · two gates, the DEFAULT lock, released together ───────────────"
  local loga logb pa pb _ aport bport bwaited=1 bpreview_while_a=0
  loga="$(mktemp)"; logb="$(mktemp)"

  npm run gate -- --surfaces tickets --themes dark > "$loga" 2>&1 &
  pa=$!
  for _ in $(seq 1 6000); do
    kill -0 "$pa" 2>/dev/null || break
    aport="$(sed -n 's/^vite preview on 127\.0\.0\.1:\([0-9]*\).*/\1/p' "$loga" | head -1)"
    [ -n "$aport" ] && break
    sleep 0.1
  done

  [ -n "$aport" ]
  check "gate A got past the lock and announced a preview" $? \
        "it never did — tail: $(tail -5 "$loga" | tr '\n' '|')"
  if [ -z "$aport" ]; then kill -TERM "$pa" 2>/dev/null; wait "$pa" 2>/dev/null; return; fi

  npm run gate -- --surfaces tickets --themes dark > "$logb" 2>&1 &
  pb=$!
  for _ in $(seq 1 300); do
    grep -q 'another browser gate holds' "$logb" && { bwaited=0; break; }
    kill -0 "$pb" 2>/dev/null || break
    sleep 0.1
  done
  bport="$(sed -n 's/^vite preview on 127\.0\.0\.1:\([0-9]*\).*/\1/p' "$logb" | head -1)"
  [ -n "$bport" ] && bpreview_while_a=1

  [ "$bwaited" = 0 ]
  check "two_gates_do_not_run_at_once" $? \
        "gate B did not announce a wait while gate A held the DEFAULT lock — it either took a lock \
of its own or never asked: $(head -5 "$logb" | tr '\n' '|')"

  [ "$bpreview_while_a" = 0 ]
  check "and gate B started no browser of its own while gate A was running" $? \
        "gate B announced a preview on $bport with gate A alive on $aport"

  kill -TERM "$pa" 2>/dev/null
  wait "$pa" 2>/dev/null
  # …and once A is gone, B proceeds. A lock nobody can ever take is not a queue.
  for _ in $(seq 1 6000); do
    bport="$(sed -n 's/^vite preview on 127\.0\.0\.1:\([0-9]*\).*/\1/p' "$logb" | head -1)"
    [ -n "$bport" ] && break
    kill -0 "$pb" 2>/dev/null || break
    sleep 0.1
  done
  [ -n "$bport" ]
  check "and gate B went on once gate A released the lock" $? \
        "it never reached a preview after A ended — tail: $(tail -5 "$logb" | tr '\n' '|')"

  kill -TERM "$pb" 2>/dev/null
  wait "$pb" 2>/dev/null
  record_run "two gates · A" "$loga"
  record_run "two gates · B" "$logb"
  echo "  logs        = $loga · $logb"
}

# The narrowing above is one edit from swallowing the failure it was added to see past. Driven on
# fabricated logs, both ways, so it can fail: the gate's banner must survive npm's chatter, and a
# run that printed no banner at all must still come back without one.
the_last_line_helper_still_sees_a_missing_banner() {
  echo
  echo "── WEB-FIX-405 · NEGATIVE · npm's chatter is skipped and the gate's is not ───"
  local log said
  log="$(mktemp)"
  printf '  total 3s\n── GATE FAILED · exit 2 ────\nnpm notice\nnpm notice New major version\n' > "$log"
  said="$(gate_last_line "$log")"
  grep -q 'GATE FAILED' <<<"$said"
  check "npm's update notice does not become the gate's last line" $? \
        "gate_last_line answered: $said"

  printf '  total 3s\nnpm notice\n' > "$log"
  said="$(gate_last_line "$log")"
  ! grep -q 'GATE FAILED' <<<"$said"
  check "the_last_line_helper_still_sees_a_missing_banner" $? \
        "a run that printed NO banner was reported as having one: $said"
  rm -f "$log"
}

CASES=(a_failed_build_says_so_on_its_last_line
       the_last_line_helper_still_sees_a_missing_banner
       a_scoped_run_photographs_what_it_was_given
       an_unknown_scope_is_refused_before_the_lock
       the_lock_give_up_exits_above_the_trap_and_says_so
       the_lock_give_up_fails_when_it_cannot_hold_a_lock
       two_gates_do_not_run_at_once
       a_pre_trap_death_says_so_rather_than_dying_silently
       a_gate_killed_by_sigterm_leaves_no_child_and_frees_the_lock
       a_gate_killed_by_timeout_leaves_no_child
       a_foreign_preview_survives_the_gates_cleanup
       a_green_run_exits_zero_and_does_not_say_failed
       two_consecutive_runs_are_both_green)
[ $# -gt 0 ] && CASES=("$@")
for one in "${CASES[@]}"; do "$one"; done


# ── 6 · WEB-FEAT-379 · what each phase cost, on EVERY run this file made ─────────────────────
# §1 criterion 4 is quantified over *any gate run*, so this is quantified over every run this file
# has captured — one that dies at the build, two that die above the trap with no phase at all, a
# scoped one and a whole one. A case that read only the green run would be a case about the happy
# path, and `WEB-FIX-101` is in this file precisely because that is where the last one lived.
#
# The property is a PREFIX, not a set: a run that never entered `build` has no build cost to print,
# and printing a line for it would be the lie. What must hold is that the phases named are the
# declared ones, in the declared order, from the first, each with an integer number of seconds —
# and that the last of the run's own lines is the one-line summary carrying them and a total.
echo
echo "── what each phase cost, on every run above ───────────────────────────────────"
# Anchored at the start of a STATEMENT, not of a line (WEB-FIX-405). `phase_begin baselines` sits
# inside an `if`, indented, so a `^phase_begin` derivation knew five of the six phases and every
# run that reached the sixth was reported as naming a phase the script does not declare. The rule
# is quantified over what `run-gate.sh` runs, and indentation is not a declaration.
DECLARED_PHASES="$(grep -oE '^[[:space:]]*phase_begin [a-z]+' gate/run-gate.sh | awk '{ print $2 }' \
  | tr '\n' ' ')"
echo "  phases declared by run-gate.sh: $DECLARED_PHASES"

# The derivation says how much it swept. A `grep` that stopped matching would make every prefix
# below vacuously true.
[ "$(echo $DECLARED_PHASES | wc -w)" -ge 5 ]
check "run-gate.sh declares the phases this case is quantified over" $? \
      "found: $DECLARED_PHASES"

PHASE_PROBLEMS=""
FULLEST=""
RUNS_SEEN=0
for pair in ${GATE_RUNS[@]+"${GATE_RUNS[@]}"}; do
  WHICH="${pair%%:*}"; LOG="${pair#*:}"
  # A run whose log is empty is a run that printed nothing at all, which is a problem about that
  # run rather than a row to skip — the gate always prints, even when it dies above its own trap.
  [ -n "$LOG" ] && [ -s "$LOG" ] \
    || { PHASE_PROBLEMS="$PHASE_PROBLEMS | $WHICH: the run printed nothing"; continue; }
  RUNS_SEEN=$((RUNS_SEEN + 1))
  PRINTED="$(grep -oE '^  phase [a-z]+ [0-9]+s$' "$LOG" | awk '{ print $2 }' | tr '\n' ' ')"
  case "$DECLARED_PHASES" in
    "$PRINTED"*) ;;
    *) PHASE_PROBLEMS="$PHASE_PROBLEMS | $WHICH: named [$PRINTED], which is not the start of \
[$DECLARED_PHASES]" ;;
  esac
  grep -qE '^  ([a-z]+ [0-9]+s · )*total ([0-9]+m )?[0-9]+s$' "$LOG" \
    || PHASE_PROBLEMS="$PHASE_PROBLEMS | $WHICH: no line carrying every phase and a total"
  LAST="$(gate_last_line "$LOG")"
  TAIL2="$(grep -v '^[[:space:]]*$' "$LOG" | grep -vE '^npm (notice|warn)\b' | tail -2 | head -1)"
  grep -qE 'total ([0-9]+m )?[0-9]+s$' <<<"$LAST" \
    || { grep -q 'GATE FAILED' <<<"$LAST" && grep -qE 'total ([0-9]+m )?[0-9]+s$' <<<"$TAIL2"; } \
    || PHASE_PROBLEMS="$PHASE_PROBLEMS | $WHICH: the summary is not the last thing it printed"
  [ "$(echo $PRINTED | wc -w)" -gt "$(echo $FULLEST | wc -w)" ] && FULLEST="$PRINTED"
done

if [ "$RUNS_SEEN" -eq 0 ]; then
  # Printed and counted, never passed. This invocation ran no case that starts a gate, so there is
  # no run for the rule to be about — which is a different sentence from `every run was fine`, and
  # a reader has to be shown which one they are looking at.
  echo "  SKIP(cases)  the_summary_names_every_phase_and_its_seconds — this invocation started no gate"
else
  [ -z "$PHASE_PROBLEMS" ]
  check "the_summary_names_every_phase_and_its_seconds" $? "${PHASE_PROBLEMS# | }"
fi
echo "  gate runs this invocation captured: $RUNS_SEEN"

# …and at least one of those runs reached the last phase, so the prefix above was not satisfied
# several times over by runs that printed nothing. Only asked of an invocation that ran a case
# capable of reaching the sweep: a single-case run of `the_lock_give_up_…` dies above the first
# phase by design, and demanding a full phase list of it would be demanding the case fail.
REACHED_SWEEP=0
for pair in ${GATE_RUNS[@]+"${GATE_RUNS[@]}"}; do
  LOG="${pair#*:}"
  [ -s "$LOG" ] && grep -qE '^  phase sweep ' "$LOG" && REACHED_SWEEP=1
done
if [ "$REACHED_SWEEP" = 1 ]; then
  [ "$FULLEST" = "$DECLARED_PHASES" ]
  check "and at least one of them named all of them, so the check above swept something" $? \
        "the fullest run named [$FULLEST] of [$DECLARED_PHASES]"
else
  echo "  SKIP(cases)  no run in this invocation reached the sweep, so there is no full phase list"
fi

echo
if [ "$failures" = 0 ]; then
  echo "── run-gate exit assertions: all passed ───────────────────────────────────────"
else
  echo "── run-gate exit assertions: $failures FAILED ─────────────────────────────────"
  echo "   failing-run log: $FAIL_LOG"
  echo "   green-run log:   $GREEN_LOG"
fi
exit "$failures"
