#!/usr/bin/env bash
# What has landed, and may therefore stop being kept — SRV-FIX-427.
#
#   .claude/scripts/landed.sh                 report only (the default, and the safe one)
#   .claude/scripts/landed.sh --json          the same answer, machine-readable
#   .claude/scripts/landed.sh --remove        delete the landed branches and their worktrees
#   .claude/scripts/landed.sh --base origin/main    which ref counts as landed (default origin/main)
#
# WHY THIS EXISTS. A release keeps the branch — always, and rightly: at release time nobody can
# know whether the work landed, and destroying commits on a guess is how a lane loses a round. The
# workflow never had the other half. Nothing said when a kept branch stops needing keeping, so they
# accumulated: 37 branches and 27 worktrees against zero lanes.
#
# WHY IT CANNOT ASK GIT. This repository merges with `git merge --squash`, so a landed branch's own
# commits are not ancestors of the base. `git branch -d` refuses them and `rev-list base..branch` is
# never empty — both of git's safety answers say *unmerged* about work that is on main. So landed is
# decided from the TICKET (`status: pass`) and the squashed subject (`<ID>: …`), which is the
# traceability rule this repository already keeps.
#
# WHY IT ASKS THE TOOL FIRST. `CLAUDE.md`: only `GET /api/lanes` can answer *is an agent running in
# it*. A branch backing a live lane is never a candidate, whatever its ticket says. If the server
# cannot be reached, --remove refuses rather than guessing.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="origin/main"
MODE="report"
while [ $# -gt 0 ]; do
  case "$1" in
    --json) MODE="json";;
    --remove) MODE="remove";;
    --base) BASE="$2"; shift;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
  shift
done
# Operate on the repository the caller is standing in, falling back to this script's own. A test
# runs it from a scratch repository; a person runs it from anywhere inside the factory.
git rev-parse --show-toplevel >/dev/null 2>&1 && cd "$(git rev-parse --show-toplevel)" || cd "$REPO"

git rev-parse --verify -q "$BASE" >/dev/null || { echo "no such base: $BASE" >&2; exit 2; }

# Branches backing a live lane, from the only thing that can answer it.
BUSY=""
LANES_KNOWN=0
if LANES=$(curl -s --max-time 5 http://127.0.0.1:7777/api/lanes 2>/dev/null) && [ -n "$LANES" ]; then
  BUSY=$(printf '%s' "$LANES" | python3 -c "
import sys, json
try: d = json.load(sys.stdin)
except Exception: sys.exit(0)
print(' '.join(l.get('branch','') for l in d.get('lanes', []) if l.get('branch')))" 2>/dev/null)
  LANES_KNOWN=1
fi

statusOf() {
  local f
  f=$(ls docs/tickets/"$1"-*.md 2>/dev/null | head -1)
  [ -n "$f" ] && sed -n 's/^status:[[:space:]]*//p' "$f" | head -1 || echo "no-ticket"
}

# EVERY local branch, not a prefix somebody typed. The first version swept `ticket/*` and left
# `squash/SRV-FIX-306`, `squash/SRV-FIX-344` and `qa-merge-SRV-FIX-378` behind — branches agents
# make during a round, carrying the same landed work under a different name. A sweep is only as
# good as the set it walks; the ticket id is read out of the branch name wherever it appears, and
# a branch naming no ticket is KEPT rather than guessed about.
CURRENT="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
BASENAME="${BASE##*/}"

LANDED=(); KEPT=(); REASONS=()
for b in $(git branch --format='%(refname:short)'); do
  [ "$b" = "$CURRENT" ] && continue
  [ "$b" = "$BASENAME" ] && continue
  [ "$b" = "main" ] && continue
  # An id is UPPERCASE segments ending in a number: SRV-FIX-378, WEB-FEAT-350, FAC-EPIC-371 —
  # and TKT-1, because a repository this tool is pointed at chooses its own id shape. Anchoring on
  # three segments was a guess about THIS repository's prefixes.
  id=$(printf '%s' "$b" | grep -oE '[A-Z]{2,}(-[A-Z]+)*-[0-9]+' | head -1)
  if [ -z "$id" ]; then
    KEPT+=("$b"); REASONS+=("its name carries no ticket id"); continue
  fi
  st=$(statusOf "$id")
  reason=""
  case " $BUSY " in *" $b "*) reason="a live lane holds it";; esac
  if [ -z "$reason" ] && [ "$st" != "pass" ] && [ "$st" != "superseded" ]; then
    reason="its ticket is ${st}, not pass"
  fi
  if [ -z "$reason" ] && [ "$(git log --oneline "$BASE" --grep="^${id}:" | wc -l)" -eq 0 ]; then
    # the trap: a ticket may say `pass` about a merge that never happened
    reason="its ticket says pass but no ${id} commit is on ${BASE}"
  fi
  if [ -n "$reason" ]; then KEPT+=("$b"); REASONS+=("$reason"); else LANDED+=("$b"); fi
done

if [ "$MODE" = "json" ]; then
  python3 - "$BASE" "${#LANDED[@]}" "${LANDED[@]+"${LANDED[@]}"}" -- "${KEPT[@]+"${KEPT[@]}"}" -- "${REASONS[@]+"${REASONS[@]}"}" <<'PY'
import json, sys
base = sys.argv[1]; n = int(sys.argv[2]); rest = sys.argv[3:]
cut = rest.index('--'); landed = rest[:cut]; rest = rest[cut+1:]
cut = rest.index('--'); kept = rest[:cut]; reasons = rest[cut+1:]
print(json.dumps({
    "base": base,
    "landed": [{"branch": b} for b in landed],
    "kept": [{"branch": b, "reason": r} for b, r in zip(kept, reasons)],
}, indent=2))
PY
  exit 0
fi

echo "base: $BASE   landed: ${#LANDED[@]}   kept: ${#KEPT[@]}"
for b in ${LANDED[@]+"${LANDED[@]}"}; do echo "  LANDED  $b"; done
i=0
for b in ${KEPT[@]+"${KEPT[@]}"}; do echo "  keep    $b — ${REASONS[$i]}"; i=$((i+1)); done

[ "$MODE" = "remove" ] || exit 0

if [ "$LANES_KNOWN" -eq 0 ]; then
  echo "refusing to remove: the server did not answer, and only GET /api/lanes can say whether an" >&2
  echo "agent is running in a checkout. Start it, or remove nothing." >&2
  exit 3
fi

for b in ${LANDED[@]+"${LANDED[@]}"}; do
  wt=$(git worktree list --porcelain | awk -v br="refs/heads/$b" '
        /^worktree /{w=$2} /^branch /{if ($2==br) print w}')
  if [ -n "$wt" ]; then
    git worktree remove --force "$wt" >/dev/null 2>&1 \
      && echo "  removed worktree $wt" || echo "  could not remove worktree $wt" >&2
  fi
  # -D, not -d: a squashed branch is not an ancestor, so -d refuses work that HAS landed. The
  # judgement was made above, from the ticket and the base branch, and it is made once.
  git branch -D "$b" >/dev/null 2>&1 && echo "  removed branch   $b" \
    || echo "  could not remove branch $b" >&2
done
