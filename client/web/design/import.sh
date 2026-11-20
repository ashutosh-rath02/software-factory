#!/usr/bin/env bash
# Import a Claude Design export into client/web/design/, and prove nothing regressed.
#
#   client/web/design/import.sh <export.zip|dir>            what WOULD change (default: dry run)
#   client/web/design/import.sh <export.zip|dir> --apply    do it, then regenerate and gate
#   client/web/design/import.sh <export.zip|dir> --apply --prune
#
# Why this exists.
#
# THE REPOSITORY IS THE SOURCE OF TRUTH, NOT THE CANVAS (README.md). An import is a one-off —
# seeding the design from Claude Design, or taking a change somebody made there — and never the
# routine loop. Design changes are made HERE, by someone who can read `MODEL.md` and `ESCAPES.md`.
#
# The reason is measured. The 2026-09-02 export dropped `--st-none` and set `--st-done` to
# `var(--color-neutral-600)`, which is the same ink as `--t3` — the exact defect `WEB-FIX-169` had
# already fixed and `client/tests/state-tones.test.ts` exists to catch. The canvas cannot know
# that; it has never read the ledger. The check existed and nothing ran it at the moment a design
# entered the repository.
#
# So this script is the gate at that moment: it regenerates the tokens and runs the two cases
# that judge a palette, and it fails loudly rather than leaving a red build for someone else.
#
# It does NOT delete by default. `Factory.dc.html` was not in any export and was referenced by 43
# files — the token generator, `theme.test.tsx`, `SPEC.md`, three epics. A script that mirrored
# the export would take it out and break the build, so files the export does not carry are
# reported and kept. `--prune` removes them, and says what it removed.
set -euo pipefail

DESIGN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DESIGN_DIR/../../.." && pwd)"

# What the export owns. Everything else in client/web/design/ — README.md and the per-module design
# notes — is written here by hand and is never touched.
owned() { find "$1" -maxdepth 1 \( -name '*.dc.html' -o -name 'support.js' \
                         -o -name 'nocturne.css' -o -name '.thumbnail' \) -print0; }

SRC="${1:?usage: import.sh <export.zip|dir> [--apply] [--prune]}"
APPLY=""; PRUNE=""
for a in "${@:2}"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --prune) PRUNE=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done
[ -e "$SRC" ] || { echo "no such export: $SRC" >&2; exit 2; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
if [ -d "$SRC" ]; then cp -r "$SRC"/. "$WORK/"; else unzip -q "$SRC" -d "$WORK"; fi

# An export with no .dc.html is not an export — a wrong zip should not overwrite the design.
count=$(find "$WORK" -maxdepth 1 -name '*.dc.html' | wc -l)
if [ "$count" -eq 0 ]; then { echo "not a design export: no .dc.html at the top level of $SRC" >&2; exit 3; }; fi
echo "[+] export carries $count .dc.html file(s)"

echo
echo "── files the export brings ─────────────────────────────────────────────"
while IFS= read -r -d '' f; do
  n="$(basename "$f")"; t="$DESIGN_DIR/$n"
  if   [ ! -e "$t" ];        then printf "  NEW       %s\n" "$n"
  elif cmp -s "$f" "$t";     then printf "  same      %s\n" "$n"
  else printf "  CHANGED   %s  (%s → %s bytes)\n" "$n" "$(stat -c%s "$t")" "$(stat -c%s "$f")"; fi
done < <(owned "$WORK" | sort -z)
for d in _ds uploads; do
  if [ ! -d "$WORK/$d" ]; then continue; fi
  if diff -rq "$WORK/$d" "$DESIGN_DIR/$d" >/dev/null 2>&1; then printf "  same      %s/\n" "$d"
  else printf "  CHANGED   %s/\n" "$d"; fi
done

echo
echo "── in the repository, NOT in this export ───────────────────────────────"
missing=0
while IFS= read -r -d '' f; do
  n="$(basename "$f")"
  if [ -e "$WORK/$n" ]; then continue; fi
  missing=1
  refs=$(cd "$REPO" && grep -rl --exclude-dir=node_modules --exclude-dir=.cmake_build --exclude-dir=.cache \
           --exclude-dir=dist --exclude-dir=.git -F "$n" server client docs .factory 2>/dev/null \
         | grep -v '^client/web/design/' | grep -v '^docs/reports/' | wc -l)
  if [ -n "$PRUNE" ]; then printf "  DELETE    %s  (referenced by %s file(s))\n" "$n" "$refs"
  else                     printf "  kept      %s  (referenced by %s file(s)) — --prune to remove\n" "$n" "$refs"; fi
done < <(owned "$DESIGN_DIR" | sort -z)
if [ "$missing" = 0 ]; then echo "  (none)"; fi

if [ -z "$APPLY" ]; then
  echo
  echo "Dry run. Nothing was written. Re-run with --apply to import."
  exit 0
fi

echo
echo "[+] importing"
while IFS= read -r -d '' f; do cp -p "$f" "$DESIGN_DIR/$(basename "$f")"; done < <(owned "$WORK")
for d in _ds uploads; do
  if [ -d "$WORK/$d" ]; then rm -rf "${DESIGN_DIR:?}/$d"; cp -r "$WORK/$d" "$DESIGN_DIR/$d"; fi
done
if [ -n "$PRUNE" ]; then
  while IFS= read -r -d '' f; do
    n="$(basename "$f")"
    if [ ! -e "$WORK/$n" ]; then rm -f "$f"; echo "    removed $n"; fi
  done < <(owned "$DESIGN_DIR")
fi

# ── the gate, at the moment a design enters ──────────────────────────────────
echo
echo "[+] regenerating tokens"
( cd "$REPO/client" && npm run --silent gen:tokens )

echo "[+] the two cases that judge a palette"
if ( cd "$REPO/client" && npx --no-install vitest run tests/theme.test.tsx tests/state-tones.test.ts ); then
  echo
  echo "[+] design imported and green"
else
  echo
  echo "[!] THE IMPORTED DESIGN FAILS THE PALETTE GATE." >&2
  echo "    Fix it IN THE CANVAS and export again — the canvas is the source of truth." >&2
  echo "    To undo this import:  git checkout -- client/web/design/" >&2
  exit 1
fi

echo
echo "── what moved ──────────────────────────────────────────────────────────"
( cd "$REPO" && git status --short client/web/design client/web/src/styles/tokens.css )
