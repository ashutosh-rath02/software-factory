#!/usr/bin/env bash
# WEB-FEAT-006 — build the client, point the real server at the fixture repository, and drive
# the built bundle in a headless browser.
#
#   factory/client/web/gate/run-gate.sh [--surfaces a,b] [--themes dark|light|dark,light] \
#                                       [screenshot-dir]
#   npm run gate                                          # every declared surface, both themes
#   npm run gate -- --surfaces tickets,ticket-md --themes dark
#
# WEB-FEAT-379 — the SCOPE. With none given nothing changes and this is the whole sweep, which is
# what `VERIFY` runs. A scope is `GATE`'s tier 2b: the surfaces the ticket's §1 names, in one
# theme, in a few minutes instead of ten. `FACTORY_GATE_SURFACES` and `FACTORY_GATE_THEMES` say the
# same thing and the argument wins. The names are the first column of `gate/surfaces.mjs`'s
# `SURFACES` and `LIVE_SURFACES`; an unknown one is refused, by name, before anything starts.
#
# The inventory, the vitest suite and the build are NEVER scoped — they are the client's real gate
# and they cost about a minute together.
#
# Everything is on ports chosen for this run. A gate that reaches a server left over from another
# session passes for the wrong reason — it has happened twice in this repo — so the health check
# below asserts WHICH repository the process it reached is serving before a single assertion runs.
set -euo pipefail

# ── THE TRAP GOES FIRST (WEB-FIX-101 QA finding 1, WEB-FIX-155) ───────────────────────────────
# `cleanup` used to be installed eighty-two lines down, after the browser lock and the port probes.
# Every non-zero exit above it — `gave up waiting for $LOCK` at exit 2, a `set -e` death in
# `free_port` or `mktemp -d` — left without a word, and the pre-trap deaths printed nothing at all.
# The criterion is quantified over EVERY non-zero exit of this script, so the trap has to be armed
# before the first thing that can take one.
#
# Everything it touches is declared empty here for that reason, and `set -u` is what keeps this
# honest: a variable the trap reads and nobody declared is an error rather than a silent skip.
SERVER_PID=""
PREVIEW_PID=""
AGENT_PID=""
FG_PID=""
WORK=""
AGENT_CONFIG_ROOT=""
reaped_ok=1

# ── WHAT EACH PHASE COST (WEB-FEAT-379) ───────────────────────────────────────────────────────
# `FAC-EPIC-374` measured GATE at a median of 22 minutes per implementing run without any run ever
# saying where the time went — the numbers in its §1 had to be reconstructed from transcript
# timestamps. Every run now prints it, so the next measurement is a grep.
#
# Declared here, beside everything else the trap reads, and printed FROM the trap: a run that dies
# at the build is the run somebody most wants the number for, and it is the run that never reaches
# the bottom of this script. `set -u` is what keeps that honest.
RUN_STARTED=$(date +%s)
PHASES=""
PHASE_NAME=""
PHASE_AT=0

phase_begin() { PHASE_NAME="$1"; PHASE_AT=$(date +%s); }
phase_end() {
  [ -n "$PHASE_NAME" ] || return 0
  PHASES="$PHASES $PHASE_NAME $(( $(date +%s) - PHASE_AT ))"
  PHASE_NAME=""
}
duration() { [ "$1" -lt 60 ] && echo "${1}s" || echo "$(( $1 / 60 ))m $(( $1 % 60 ))s"; }

# SRV-FIX-038 — the fixture agent is REAPED, and the gate says so rather than being read as
# saying so. It is a process named `/run-ticket TKT-155` and it lived for ten minutes of wall
# clock; every server suite that overlapped a client gate saw it, and `probe.cpp` matched it as a
# substring of `TKT-1`. The prefix match is fixed at the source, but a gate that leaks a process
# named after a ticket is its own defect: the next thing to read a command line has the same
# problem, and nothing here would have told anybody.
#
# On EXIT, so it holds when the gate FAILS — which is the run that leaks, because the happy path
# was the only one anyone watched. And on INT, TERM and HUP, because a signal ends bash WITHOUT
# running an EXIT trap (WEB-FIX-380) — `timeout` expiring, a session ending, a Ctrl-C. Thirty-three
# `vite preview` processes from six lanes and the main checkout were alive on the developer's
# machine on 2026-09-09, the oldest 5.6 days old, each one still holding the lock on fd 9.
#
# ── AND IT SIGNALS THE GROUP, NEVER THE LEADER (WEB-FIX-380) ──────────────────────────────────
# `kill $PREVIEW_PID` reaches the `npx vite preview` shell; the node process that HOLDS THE PORT is
# its grandchild and is never told. The fixture agent is the same shape — `sh -c "sleep 600"` forks
# the sleep — so this script leaked a process on its GREEN path for as long as it has had one,
# while the reap line below said PASS about the pid it happened to know. That is the shape
# `docs/operations/ESCAPES.md` records on 2026-07-27: a supervisor's own bookkeeping cannot describe what it
# has lost track of, and counting what you already knew about always comes out even.
#
# So every child is started under `set -m` — which makes its pid its process-GROUP id — and
# everything below signals the group. Monitor mode is on for the launch, where it does nothing but
# `setpgid`, and off again before the next foreground command.
group_alive() {                 # <pid> — anything in that group still running. A corpse this shell
  [ -n "$1" ] || return 1       # has not reaped yet is not a leak, so zombies do not count.
  ps -eo pgid=,stat= 2>/dev/null \
    | awk -v g="$1" '$1 == g && $2 !~ /^Z/ { alive = 1 } END { exit !alive }'
}

any_group_alive() {
  local pid
  for pid in "$@"; do
    group_alive "$pid" && return 0
  done
  return 1
}

signal_group() {                # <signal> <pid> — the group, or the pid if it never got a group
  [ -n "$2" ] || return 0
  kill -"$1" -- "-$2" 2>/dev/null || kill -"$1" "$2" 2>/dev/null || true
  return 0
}

reap_children() {                # <deciseconds to wait for a TERM to be enough>
  local pid _
  for pid in "$FG_PID" "$PREVIEW_PID" "$SERVER_PID" "$AGENT_PID"; do
    signal_group TERM "$pid"
  done
  # TERM everything, then ONE wait for all of it. Four waits in series is a cleanup that outlives
  # the patience of whatever killed the gate.
  #
  # The grace differs by path and the difference is the whole of it: `factory-server` answers
  # SIGTERM with `Graceful shutdown — draining connections (10s)`, so a run ENDING normally waits
  # long enough for that to finish, exactly as `wait` did before this ticket, and a run that was
  # KILLED cannot — §1 gives it five seconds and something is holding its breath for them.
  for _ in $(seq 1 "$1"); do
    any_group_alive "$FG_PID" "$PREVIEW_PID" "$SERVER_PID" "$AGENT_PID" || break
    sleep 0.1
  done
  for pid in "$FG_PID" "$PREVIEW_PID" "$SERVER_PID" "$AGENT_PID"; do
    signal_group KILL "$pid"
  done
  sleep 0.2
  wait 2>/dev/null || true

  if [ -n "$AGENT_PID" ]; then
    if group_alive "$AGENT_PID"; then
      echo "  FAIL  the gate leaked its fixture agent (pid $AGENT_PID, /run-ticket TKT-155)"
      reaped_ok=0
    else
      echo "  PASS  the gate reaped its fixture agent (pid $AGENT_PID)"
    fi
  fi
  [ -n "$WORK" ] && rm -rf "$WORK"
  [ -n "$AGENT_CONFIG_ROOT" ] && rm -rf "$AGENT_CONFIG_ROOT"
  return 0
}

# WEB-FIX-101 — the LAST line of a failed run says so.
#
# The reap line above is honest and stays exactly where it is: `SRV-FIX-038` exists because a
# leaked fixture agent was invisible, and tidying the tail by moving or suppressing it would
# re-open that. The defect was only that on a FAILED run the reap line is the last thing on the
# screen, so a reader scanning the tail of a build log sees `PASS` at the bottom of a failure.
#
# Conditional, and that is the whole of it. Printing this banner unconditionally is the cheap
# fix `gate/test-run-gate.sh`'s green-path negative refuses: a gate that says FAILED at the end
# of every run has stopped saying anything.
#
# Nothing here touches the exit code, which §1 pre-decided is out of scope and already correct —
# `set -euo pipefail` and this trap were both right, and the `exit=0` that started this ticket
# was a compound command's status being read as the gate's. A signalled run reaches it too: its
# criterion is quantified over EVERY non-zero exit, and 128+signum is one.
banner() {                      # <status>
  [ "$1" -eq 0 ] \
    || echo "── GATE FAILED · exit $1 ────────────────────────────────────────────────"
}

cleanup() {
  # WEB-FIX-101 — the status this run is ending WITH, captured before anything below can change
  # it. First line of the trap or it is somebody else's `$?`.
  local status=$?
  reap_children 120
  [ "$reaped_ok" = 1 ] || status=1
  # WEB-FEAT-379 — one line per phase and one line carrying all of them and the total. The
  # phase that was still open when the run died is closed here, so a failed run says how far it
  # got rather than omitting the one phase anybody would ask about.
  phase_end
  local line=""
  set -- $PHASES
  while [ "$#" -ge 2 ]; do
    echo "  phase $1 ${2}s"
    line="$line$1 ${2}s · "
    shift 2
  done
  echo "  ${line}total $(duration $(( $(date +%s) - RUN_STARTED )))"
  banner "$status"
  exit "$status"
}

on_signal() {                   # <signal name> — WEB-FIX-380
  local sig="$1"
  trap - EXIT INT TERM HUP
  reap_children 30
  banner "$((128 + $(kill -l "$sig")))"
  # Re-raised, not exited: whatever killed this gate sees the signal it sent rather than a status
  # this script invented, and a caller can still tell a killed gate from a failed one.
  kill -s "$sig" "$$"
}
trap cleanup EXIT
trap 'on_signal INT'  INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP'  HUP

# ── ONE BROWSER AT A TIME, ON THIS MACHINE (WEB-FIX-037) ──────────────────────────────────────
# Five client lanes ran in parallel and all five were told NOT to run this gate, because five
# headless Chromes on one laptop make each other flaky. Every one of them merged on a green vitest
# run and their gate assertions were executed for the first time at integration, where four were
# red. The instruction was reasonable and the cost was four escaped defects, so the constraint
# belongs to the gate rather than to whoever remembers to say it: a lane runs `npm run gate`
# whenever it likes, and this queues it. Held on a file descriptor, so a lane that dies releases it
# — there is no stale lock to clear. The wait is announced, because a gate that looks hung is a
# gate somebody kills.
CLIENT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── THE SCOPE, AND IT IS READ BEFORE THE LOCK (WEB-FEAT-379) ───────────────────────────────────
# `gate/surfaces.mjs` run directly IS the resolver the sweep uses, so `--surfaces nosuch` is
# refused by one implementation rather than by two that agree. It happens HERE — above the lock,
# above every port probe, above `mktemp` — because a run that cannot start must not queue behind
# somebody else's browser for an hour first, and must not leave a lock behind when it dies.
SCOPE_ARGS=("$@")
SHOTS_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --surfaces|--themes) shift; [ "$#" -gt 0 ] && shift || true ;;
    *) [ -n "$SHOTS_ARG" ] || SHOTS_ARG="$1"; shift ;;
  esac
done
node "$CLIENT/gate/surfaces.mjs" ${SCOPE_ARGS[@]+"${SCOPE_ARGS[@]}"} > /dev/null || exit 2

# WEB-FIX-405 — the lock path is RESOLVED, not written down. It used to be a literal under `/tmp`,
# which a lane mounts read-only, so `exec 9>"$LOCK"` killed the gate on its ninth line of work and
# no client ticket running in a lane ever ran its own tier 2b. `gate/gate-lock.sh` carries the
# reasoning and the refusal; the one thing that matters here is that it never answers with a path
# private to one lane, because a private lock is the shape this defect gets closed in twice.
LOCK="${FACTORY_GATE_LOCK:-$("$CLIENT/gate/gate-lock.sh" "$CLIENT")}" || exit 2
exec 9>"$LOCK" || { echo "gave up: the browser lock at $LOCK cannot be opened"; exit 2; }
# Every child inherits fd 9, and a child that outlives this script holds the lock forever — which
# turns "one Chrome at a time" into "no gate ever again". A leaked `vite preview` did exactly that
# within an hour of the lock landing, after `timeout` killed a gate hard enough to skip cleanup.
# So every background child is started with 9 closed: the lock belongs to this shell alone.
if ! flock -n 9; then
  echo "── another browser gate holds $LOCK · waiting (one Chrome at a time) ──────────"
  flock -w "${FACTORY_GATE_LOCK_WAIT:-3600}" 9 \
    || { echo "gave up waiting for $LOCK — is a gate wedged?"; exit 2; }
fi

SERVER="$CLIENT/../../server"
FIXTURE_SRC="$CLIENT/gate/fixture-repo"
SHOTS="${SHOTS_ARG:-$CLIENT/gate/shots}"
BINARY="$CLIENT/../../.cache/server/build/factory-server"

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }

# ── A LONG CHILD IS RUN IN THE BACKGROUND AND WAITED ON (WEB-FIX-380) ─────────────────────────
# Two reasons, and they are the same reason. Bash does not run a trap while it waits for a
# FOREGROUND command — it runs it when that command finishes — so a gate killed during the four
# minutes of vitest or of the browser sweep used to clean up four minutes late, long after
# whatever killed it had stopped waiting. `wait` is the one builtin a trapped signal interrupts at
# once. And a foreground child is a child nobody wrote down: vitest, tsc, node and the Chrome it
# starts all inherit fd 9, so one of them outliving this shell holds the browser lock for good.
fg_run() {                      # <command> — its own process group, fd 9 closed, status kept
  set -m
  eval "$1" 9>&- &
  FG_PID=$!
  set +m
  local status=0
  wait "$FG_PID" || status=$?
  FG_PID=""
  return "$status"
}

API_PORT="$(free_port)"
WEB_PORT="$(free_port)"
# NOT under /tmp, and that is not a preference (SRV-FIX-137).
#
# `agent.command` resolves to a file in here, and `lane/store.cpp` puts its directory into
# `hostReadOnly`, which `confine.cpp` binds read-only — at line 388, BEFORE `--tmpfs /tmp` at line
# 428. A bind placed before the filesystem that covers it is a bind nothing can see, which the
# file says in those words about the toolchain binds two paragraphs further down. So while this
# directory was a `mktemp -d` under /tmp, every agent the gate ever spawned died at exec:
#
#   bwrap: execvp /tmp/factory-gate-UQOnAJ/claude: No such file or directory
#
# recorded as a `crashed` run, with no assertion looking at the transcript that says why. That is
# a defect in the tool and it is filed as SRV-FIX-137; this line is what lets THIS gate photograph
# a run that is actually running, which is what WEB-FIX-091 needs from its fixture.
#
# ── AND IT IS THE SAME PATH ON EVERY RUN (WEB-FIX-405) ────────────────────────────────────────
# It was `mktemp -d …/.work-XXXXXX`, and that suffix is DRAWN: `ticket-md` and `report-md` render
# the document's path — `docs/reports/WEB-FEAT-085/corpus/ticket-md-dark.txt:43` is
# `/tmp/factory-gate-4qO92g/fixture-repo/docs/tickets/…` — so two runs of the same tree could not
# produce the same bytes by construction, and the reproduction property the committed corpus
# exists for was unreachable rather than merely unchecked (`WEB-FIX-381`).
#
# One fixed path per checkout, emptied and rebuilt each run. It is safe to be fixed because the
# browser lock above makes one gate at a time per repository, and it is per CHECKOUT rather than
# per machine because two checkouts must not share a fixture they both rewrite.
WORK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.work"
rm -rf "$WORK"
mkdir -p "$WORK"
AGENT_CONFIG_ROOT="$(mktemp -d -t factory-web-gate-agent-config.XXXXXX)"
FIXTURE="$WORK/fixture-repo"


# ── the fixture, with a history ───────────────────────────────────────────────────────────────
# SPEC.md §3.5's states are facts about git and the process table, so a fixture that is only a
# directory of documents cannot hold one of each. This builds the repository the gate needs, in
# a temporary directory: `gate/fixture-repo` stays a set of documents, and a nested `.git`
# inside this repository would be a submodule nobody asked for.
g() { git -C "$FIXTURE" -c user.name=Gate -c user.email=gate@example.invalid "$@" >/dev/null; }

echo "── the fixture repository, with one of each delivery state ────────────────────"
cp -r "$FIXTURE_SRC" "$FIXTURE"
# SRV-FIX-059 round 5 — a repository that ignores its gate's own captures, like every real one.
# Committed at the root so EVERY lane worktree inherits it: that is what makes a lane which ran its
# build once hold content `git status` cannot see, which is the shape of leftover four rounds of
# this ticket never put in front of a browser.
printf 'evidence/\n' > "$FIXTURE/.gitignore"
g init -b main
g add -A
g commit -m "the repository as it stands" --no-gpg-sign

# done      · TKT-139 and TKT-128 — their reports are tracked on main (committed above)
# unshipped · TKT-142 — commits on its branch that no remote has
g checkout -b ticket/TKT-142
echo "work nobody outside this machine can see" > "$FIXTURE/stale-guard.txt"
g add -A
g commit -m "TKT-142: the guard" --no-gpg-sign
g checkout main

# waiting   · TKT-151 — pushed to a remote, and this fixture has no forge to ask
git init --bare "$WORK/origin.git" >/dev/null
g remote add origin "$WORK/origin.git"
g checkout -b ticket/TKT-151
echo "provenance in the ledger" > "$FIXTURE/ledger.txt"
g add -A
g commit -m "TKT-151: provenance" --no-gpg-sign
g push -q origin ticket/TKT-151
g checkout main

# stalled   · TKT-147 — its document says `in progress` and nothing is running or committed
# running   · TKT-155 — a live process running the agent the manifest declares
#
# `probe.cpp` matches an agent by argv[0]'s BASE NAME, so this copy carries the declared name and
# lives where PATH cannot find it: the stand-in the TOOL spawns is the script below, and these two
# must not be the same file.
mkdir "$WORK/fixture-agent"
cp /bin/sh "$WORK/fixture-agent/claude"
set -m
"$WORK/fixture-agent/claude" -c "sleep 600  # /run-ticket TKT-155" 9>&- &
AGENT_PID=$!
set +m

# ── the stand-in agent the TOOL spawns (WEB-FEAT-045, then WEB-FIX-091) ───────────────────────
#
# It used to be a copy of `/bin/sh`, which exits at once when handed the CLI's argv — a spawn the
# tool records honestly and a run that costs nothing. That is right for every section except one:
# a run that has already ended cannot be photographed IN FLIGHT, so the live sweep held its lane
# with `POST /api/_test/lane-process` instead — a bare process attached to a lane, belonging to no
# run. The screen then showed a lane the tool called `running` above `0 runs`, and the ticket about
# that sentence could not tell its own defect from its fixture (WEB-FIX-091, QA F1).
#
# So: the same instant exit, unless the gate has asked THIS section for a run that stays alive.
# `/usr` is bound read-only inside a lane and `/bin` is a symlink into it, so the shebang and
# `sleep` both resolve in the sandbox; `$0.hold` sits beside this file, in the directory the tool
# already binds because the agent binary is in it.
cat > "$WORK/claude" <<'STANDIN'
#!/bin/sh
[ -f "$0.hold" ] && exec sleep 600
exit 0
STANDIN
chmod 755 "$WORK/claude"
ln -s "$WORK/claude" "$WORK/codex"
echo "fixture at $FIXTURE · agent pid $AGENT_PID"
echo

echo "── the design's inventory: every identity the design declares is drawn ────────"
# The rung that catches what a pixel diff and a geometry probe both sail past: an element that
# was simply never drawn. You cannot measure or diff what is not there.
#
# The sets come from `design/ref/extract.py`, which reads the DESIGN's own `const` blocks — never
# from whoever drew the .pen. A set the drawing agent wrote is bounded by that agent's
# imagination and agrees with the drawing every time; generating it is what keeps this able to
# fail. It measured 70/112 on a .pen its author had just called a conversion.
#
# It runs FIRST and it is cheap (~0.2s): a browser sweep over a design missing a third of its
# vocabulary is four minutes spent photographing the wrong screen.
phase_begin inventory
inventory_status=0
fg_run 'cd "$CLIENT" && npm run --silent gate:inventory' || inventory_status=$?
phase_end

echo "── the client's own suite (vitest) ────────────────────────────────────────────"
# WEB-FIX-091 round 4. This gate built the bundle and drove a browser, and never once ran the
# tests of the workspace it belongs to — so `npm run gate` could say GREEN with vitest RED, and
# it did: round 3 committed four PNGs that turned three cases of `tests/baselines.test.ts` red,
# passed the browser gate, and shipped. The ticket's own Definition of Done named "npm run gate
# green" and never named the client suite, which is a gate that cannot say no about the thing the
# ticket changes. It runs FIRST, because a browser sweep behind a red unit suite is four minutes
# spent confirming a build nobody should be looking at yet.
phase_begin vitest
vitest_status=0
# WEB-FIX-405 — `tests/baselines.test.ts` is EXCLUDED here and run once, after the sweep.
#
# It is the one file in this suite whose subject is the run currently happening. Run before the
# build it reads whatever the previous run left in `gate/shots/`: on a fresh checkout that is
# nothing, so it passed having compared nothing for a fortnight while the corpus was stale
# (`WEB-FIX-329`), and on a second run in the same tree it is a verdict about run one, which is
# why the gate answered differently depending on run order (`WEB-FIX-362`, `WEB-FIX-374`).
#
# And a red suite no longer cancels the pictures. The sweep and the corpus check are what a person
# ran this for; a unit failure is a verdict to be carried to the exit, not a reason to photograph
# nothing. `tests/gate-surfaces.test.ts::test_the_shots_are_taken_even_when_the_suite_is_red` is
# the case, and `build` and `server` stay fatal because the sweep consumes what they produce.
fg_run 'cd "$CLIENT" && npx vitest run --exclude "tests/baselines.test.ts"' || vitest_status=$?
phase_end

echo "── build the client (tsc -b && vite build) ────────────────────────────────────"
phase_begin build
fg_run 'cd "$CLIENT" && npm run build'
phase_end

[ -x "$BINARY" ] || { echo "build the server first: cd factory/server && ./build.sh build"; exit 2; }

echo
echo "── the real C++ server, pointed at the fixture repository ─────────────────────"
# FACTORY_TEST_HOOKS registers `/api/_test/lane-process` and `/api/_test/clock`, and nothing else
# changes. WEB-FEAT-013 needs them: SPEC.md §4.4's rail is a picture of lanes in several states at
# once, and a gate that drew an empty rail would be a gate for a screen nobody uses. `silent` is
# 120 s of nothing (§3.3) and the threshold is deliberately not configurable, so the CLOCK moves
# instead of the number — the mechanism under test is the one that ships and only the waiting is
# simulated. No developer and no shipped invocation sets this variable.
#
# ── PATH (WEB-FEAT-045) ───────────────────────────────────────────────────────────────────────
# `$WORK` holds the copy of `/bin/sh` named `claude` that this script already uses to fake the
# `running` lane, and it goes FIRST so that the fixture manifest's `agent.command: "claude"`
# resolves to it. Two things depend on that. E12 refuses to claim anything unless the agent binary
# was found at manifest load (`agent_binary_missing`), so until now this gate passed only on a
# machine with the real CLI installed — an accident, not a design. And WEB-FEAT-045 makes ONE CLICK
# start a run: the gate must not spawn a real agent, with real credentials, against a temporary
# repository. `sh` with the CLI's argv exits at once, which is a spawn the tool records honestly
# and a run that costs nothing.
phase_begin server
set -m
# FACTORY_AGENT_CONFIG_ROOT (WEB-FIX-405, from WEB-FIX-368) — the server roots each lane's agent
# configuration directory under `/tmp` unless told otherwise (`app/lane/store.cpp`), and `/tmp` in
# a lane is read-only. Twenty-seven checks — the whole live sweep, every `-live` screenshot and
# every schema row that needs a run to exist — failed behind one `confinement_unavailable`. The
# gate already chooses every other thing this server runs with, so it chooses this too; whether the
# SERVER's own default should move is a separate `SRV-*` question and this line does not answer it.
FACTORY_TEST_HOOKS=1 FACTORY_HTTP_PORT="$API_PORT" PATH="$WORK:$PATH" \
  FACTORY_AGENT_CONFIG_ROOT="$AGENT_CONFIG_ROOT" \
  FACTORY_GATE_AGENT_HOLD="$WORK/claude.hold" "$BINARY" --repo "$FIXTURE" \
  > "$CLIENT/gate/server.log" 2>&1 9>&- &
SERVER_PID=$!
set +m

for _ in $(seq 1 80); do
  sleep 0.1
  HEALTH="$(curl -s "http://127.0.0.1:$API_PORT/api/health" 2>/dev/null || true)"
  [ -n "$HEALTH" ] && break
done
echo "$HEALTH"

EXPECTED="$(cd "$FIXTURE" && pwd -P)"
echo "$HEALTH" | grep -q "\"root\":\"$EXPECTED\"" || {
  echo "WRONG SERVER: this process is not serving $EXPECTED"; exit 2; }
echo "identity checked: serving $EXPECTED"

echo
echo "── the BUILT bundle, served by vite preview, proxying /api and /ws ────────────"
# WEB-FIX-380 — the gate says which port it is about to hold, before it holds it. Anything that
# has to reason about this run's preview — `gate/test-run-gate.sh`, or a person reading a log of a
# gate that has already died — reads it here instead of scanning the machine for a `vite preview`,
# which on a machine running several lanes answers about somebody else's.
echo "vite preview on 127.0.0.1:$WEB_PORT"
set -m
( cd "$CLIENT" && FACTORY_API_PORT="$API_PORT" \
    npx vite preview --port "$WEB_PORT" --strictPort > "$CLIENT/gate/preview.log" 2>&1 ) 9>&- &
PREVIEW_PID=$!
set +m
for _ in $(seq 1 80); do
  sleep 0.1
  curl -sf "http://127.0.0.1:$WEB_PORT/" > /dev/null 2>&1 && break
done

phase_end

echo
phase_begin sweep
sweep_status=0
fg_run 'FACTORY_GATE_AGENT_HOLD="$WORK/claude.hold" \
  node "$CLIENT/gate/browser-gate.mjs" "http://127.0.0.1:$WEB_PORT" "$SHOTS" "$FIXTURE" \
    ${SCOPE_ARGS[@]+"${SCOPE_ARGS[@]}"}' || sweep_status=$?
phase_end

echo
echo "── the baselines, against the run that just happened ──────────────────────────"
# WEB-FIX-329. The client suite above runs BEFORE the sweep, so on a clean checkout
# `test_a_fresh_gate_run_reproduces_the_committed_corpus` reads a gitignored, absent `gate/shots/`,
# takes its early return and passes having compared nothing. It could only go red on a SECOND run
# in a tree still holding the first one's output — so the corpus was two days stale on `main`, the
# gate was green about it here, and another ticket's QA session found it instead.
#
# This is the only moment at which a fresh run exists to be compared. One file, ~1 s, and it is the
# difference between the reproduction property being asserted and being described.
#
# It runs whether or not the sweep passed, and that is the whole point of the ticket rather than a
# convenience: the corpus going stale is a fact about the corpus, and holding it behind an unrelated
# red check is how a two-day-old whole-corpus drift stayed unreported. A failing sweep still decides
# the exit code — this only ever adds a reason, never removes one.
#
# ── AND IT IS ABOUT THE RUN THAT JUST HAPPENED, WHATEVER THAT RUN WAS (WEB-FEAT-379) ──────────
# A scope photographs some surfaces and not others, and the six it did not take are not six
# surfaces the gate failed to photograph. The case reads `gate/shots/scope.json` — which the sweep
# writes for exactly this reason — compares the baselines the run DID take, and prints how many of
# the corpus it could not speak for. So a scoped run still catches a scoped surface drifting, and
# still cannot report a comparison of nothing as agreement.
#
# The one run it is NOT about is one photographing somewhere else: a shots directory given as an
# argument leaves `gate/shots/` holding some other run's output, and a comparison against that is a
# statement about a run nobody made here. It is skipped by name, out loud.
baselines_status=0
if [ "$SHOTS" = "$CLIENT/gate/shots" ]; then
  phase_begin baselines
  fg_run 'cd "$CLIENT" && npx vitest run tests/baselines.test.ts' || baselines_status=$?
  phase_end
else
  echo "baselines · NOT compared · this run photographed into $SHOTS, not gate/shots, so the"
  echo "            corpus has no run of its own to be checked against"
fi

# Every phase's verdict, in the order a reader wants them. A status recorded and not read here
# would be a gate that went green on a phase it had already judged red.
for phase_status in "$inventory_status" "$vitest_status" "$sweep_status" "$baselines_status"; do
  [ "$phase_status" -eq 0 ] || exit "$phase_status"
done
exit 0
