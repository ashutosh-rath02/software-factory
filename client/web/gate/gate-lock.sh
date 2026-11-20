#!/usr/bin/env bash
# WEB-FIX-405 — WHERE THE ONE-CHROME LOCK LIVES, and it is not a path anybody typed into a script.
#
#   gate/gate-lock.sh <dir>        prints the lock path for a checkout, or exits 2 saying why not
#
# `WEB-FIX-037` bought one guarantee — one headless Chrome at a time on this machine — after five
# parallel client lanes were each told not to run the browser gate, all five merged on a green
# vitest, and four gate assertions reached `main` having never once executed. The lock is how that
# instruction became the gate's own business instead of something a person has to remember.
#
# It was `/tmp/factory-browser-gate.lock`, and a lane's `/tmp` is a read-only tmpfs
# (`app/lane/confine.cpp`: `--tmpfs /tmp` … `--remount-ro /tmp`). So `exec 9>"$LOCK"` failed on the
# ninth line of `run-gate.sh` and no ticket running in a lane could run its own tier 2b at all —
# `WEB-FIX-368`, `WEB-FIX-376`, and `docs/operations/ESCAPES.md`'s 2026-09-08 row.
#
# ── THE WORKAROUND IS THE REASON THIS FILE EXISTS ─────────────────────────────────────────────
# `FACTORY_GATE_LOCK=$TMPDIR/…` makes the gate start, and `$TMPDIR` inside a lane is that lane's
# OWN tmpfs — a fresh private mount per lane. Every lane then takes an uncontended lock and *one
# Chrome at a time on this machine* becomes *one Chrome per lane, all at once*, silently, with
# every check still green. `docs/operations/ESCAPES.md` carries that exact sentence twice on 2026-08-22.
#
# So the lock has to be, at once, WRITABLE where a lane runs and THE SAME FILE for two lanes of one
# repository. Measured from inside a lane, `/proc/self/mountinfo` gives exactly one directory that
# is both: the git common directory's object store. Every worktree of a repository shares it, the
# confinement binds it read-write because a lane has to be able to commit, and git reads `objects/`
# for two-hex-character directories, `info/` and `pack/` — `git count-objects -v` reports
# `garbage: 0` for anything else in it.
#
# ── AND THERE IS NO PRIVATE FALLBACK ──────────────────────────────────────────────────────────
# A gate that cannot find a shared lock REFUSES, here, above `run-gate.sh`'s trap, naming what it
# tried. Falling back to a private path is the defect this file is named after, and a fallback is
# indistinguishable from the fix on every green run.
set -uo pipefail

DIR="${1:-$PWD}"

fail() { echo "$*" >&2; exit 2; }

[ -d "$DIR" ] || fail "gate-lock: $DIR is not a directory"

COMMON="$(git -C "$DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" \
  || fail "gate-lock: $DIR is not inside a git checkout, so there is no directory two lanes of one
         repository share. The browser gate takes a machine-wide lock and will not take a private
         one; set FACTORY_GATE_LOCK to a path every checkout can reach if you mean something else."

[ -n "$COMMON" ] || fail "gate-lock: git named no common directory for $DIR"

OBJECTS="$COMMON/objects"
[ -d "$OBJECTS" ] || fail "gate-lock: $OBJECTS does not exist — this checkout has no object store"
[ -w "$OBJECTS" ] || fail "gate-lock: $OBJECTS is not writable from here, so this checkout cannot
         join the machine's browser queue. Inside a lane this directory is bound read-write; if it
         is not, the lane was built without it and that is a confinement question, not a gate one."

echo "$OBJECTS/factory-browser-gate.lock"
