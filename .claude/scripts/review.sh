#!/bin/sh
# The REVIEW stage, as a command the tool runs — SRV-FEAT-500.
#
#   .claude/scripts/review.sh [base]      # base defaults to `main`
#   FACTORY_REVIEW_DRIVER=codex FACTORY_REVIEW_MODEL=<model> CODEX_HOME=<config> ...
#
# Reads `docs/technical/workflow/REVIEW.md` — the passes, the two severities and the exclusions —
# hands it to the agent CLI together with `git diff <base>...HEAD`, and prints what came back as
# one line per finding, blockers first:
#
#   blocker server/app/lane/command.cpp:63 the enum lists two names, not three.
#
# EXIT 0 WHEN THE REVIEW RAN — whatever it found. Findings do not approve and do not block: `E19`
# reports an exit code and the tool has no opinion about it, so grading the diff into an exit
# status would be this script inventing a verdict the workflow does not give it.
#
# A NON-ZERO EXIT MEANS THE REVIEW DID NOT RUN, and that distinction is the whole of the second
# half of this file. Driving `SRV-FEAT-500` by hand on 2026-09-11 found the first version of this
# script printing NOTHING and exiting 0 in a lane with no `claude` on its PATH — byte-identical
# to a clean review, because `claude … 2>/dev/null || true` threw away both the diagnosis and the
# status. `SRV-FIX-221` is the same shape one level up: a declared command whose PROGRAM resolves
# and whose RUN cannot. So the reviewer's own stderr is passed through, its status is kept, and
# "there was nothing to review" is said out loud rather than inferred from an empty stdout.
set -u

BASE="${1:-main}"
DRIVER="${FACTORY_REVIEW_DRIVER:-claude}"
MODEL="${FACTORY_REVIEW_MODEL:-}"
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || ROOT=$(pwd)
INSTRUCTIONS="$ROOT/docs/technical/workflow/REVIEW.md"

if [ ! -r "$INSTRUCTIONS" ]; then
  echo "review.sh: no $INSTRUCTIONS to read — the review has no instructions" >&2
  exit 2
fi

WORK=$(mktemp -d) || exit 2
trap 'rm -rf "$WORK"' EXIT
PROMPT="$WORK/prompt"
HEADER="$WORK/header"
RAW="$WORK/raw"
DIFF="$WORK/diff"
INVENTORY="$WORK/inventory"
OMITTED="$WORK/omitted"
PROMPT_MAX_BYTES=900000

cd "$ROOT" || exit 2
if ! git diff --name-status "$BASE...HEAD" > "$INVENTORY" 2>"$WORK/git.err" ||
   ! git diff --name-status "$BASE...HEAD" -- docs/reports/ > "$OMITTED" 2>"$WORK/git.err" ||
   ! git diff "$BASE...HEAD" -- . ':(top,exclude)docs/reports/**' > "$DIFF" 2>"$WORK/git.err"; then
  echo "review: outcome=no_base" >&2
  echo "review: \`git diff $BASE...HEAD\` failed — there is no base to review against, which is" >&2
  echo "review: a REVIEW THAT DID NOT RUN and not a clean diff. git said:" >&2
  sed 's/^/review:   /' "$WORK/git.err" >&2
  exit 2
fi

if [ -s "$OMITTED" ]; then
  echo "review: omitted evidence hunks from docs/reports/ (REVIEW.md's documented exclusion)." >&2
fi

if [ ! -s "$DIFF" ]; then
  echo "review: outcome=nothing_to_review" >&2
  echo "review: no actionable diff remains after excluding docs/reports/; nothing was reviewed." >&2
  echo "review: this is not the same fact as a diff with nothing wrong in it." >&2
  exit 0
fi

{
  cat "$INSTRUCTIONS"
  echo
  echo "---"
  echo
  echo "Review the diff below against every pass above. Answer with NOTHING but finding lines,"
  echo "one per line, in the form:"
  echo
  echo "    <blocker|minor> <file>:<line> <one sentence>"
  echo
  echo "No preamble, no summary, no blank verdict line. If you find nothing, print nothing."
  echo
  echo "This is a read-only REVIEW of this diff; it does not change a ticket or execute its workflow."
  echo "Apply repository instructions relevant to that role and all four review passes above."
  echo "Read the cited policy sections and adjacent source as needed; search for the relevant section"
  echo "before opening a whole document. Ticket implementation, QA, and reporting instructions apply"
  echo "when performing those tasks; do not start them during this review."
  echo
  echo "Changed files (complete inventory, including excluded evidence):"
  cat "$INVENTORY"
  echo
  echo "Evidence omitted from the inline diff: docs/reports/ and everything under it (REVIEW.md §3)."
  if [ -s "$OMITTED" ]; then cat "$OMITTED"; else echo "No changed evidence files."; fi
  echo "Repository root: $ROOT"
  echo "Read evidence by its repository-relative path only when needed to assess a source or test finding."
  echo "Use read-only git show or git diff on listed paths when a historical version is needed."
  echo "Evidence files remain on disk; do not modify them."
} > "$HEADER"

{
  cat "$HEADER"
  echo "All other changed-file hunks follow in full."
  echo
  echo "\$ git diff $BASE...HEAD -- . ':(top,exclude)docs/reports/**'"
  cat "$DIFF"
} > "$PROMPT"

PROMPT_BYTES=$(wc -c < "$PROMPT") || exit 2
if [ "$PROMPT_BYTES" -gt "$PROMPT_MAX_BYTES" ]; then
  FULL_PROMPT_BYTES=$PROMPT_BYTES
  {
    cat "$HEADER"
    echo "Bounded prompt mode: the actionable diff is too large to include inline."
    echo "Every changed file above remains in review scope; no source, test, process, or generated"
    echo "file is excluded because its hunk is absent from this prompt. Apply all four passes to"
    echo "the complete actionable diff, inspecting each changed path with read-only git commands:"
    echo
    echo "    git diff --no-ext-diff $BASE...HEAD -- <repository-relative-path>"
    echo
    echo "Quote each path safely. The full actionable diff is available in this checkout with:"
    echo
    echo "    git diff --no-ext-diff $BASE...HEAD -- . ':(top,exclude)docs/reports/**'"
    echo
    echo "Read every actionable changed path before answering; do not infer a clean review from"
    echo "the omitted inline hunks. Evidence remains available by repository-relative path."
  } > "$PROMPT"
  PROMPT_BYTES=$(wc -c < "$PROMPT") || exit 2
  if [ "$PROMPT_BYTES" -gt "$PROMPT_MAX_BYTES" ]; then
    echo "review: outcome=prompt_too_large" >&2
    echo "review: instructions and complete inventory exceed $PROMPT_MAX_BYTES bytes even in" >&2
    echo "review: bounded mode ($PROMPT_BYTES bytes); no reviewer was started." >&2
    exit 2
  fi
  echo "review: bounded prompt mode: inline prompt was $FULL_PROMPT_BYTES bytes" >&2
  echo "review: limit $PROMPT_MAX_BYTES; complete inventory retained in $PROMPT_BYTES bytes." >&2
  echo "review: full actionable hunks remain readable on disk with read-only git diff." >&2
fi

case "$DRIVER" in
  claude)
    claude --model "${MODEL:-claude-opus-5}" -p < "$PROMPT" > "$RAW"
    ;;
  codex)
    set -- codex exec --sandbox read-only --ephemeral --json --color never --output-last-message "$RAW"
    if [ -n "$MODEL" ]; then set -- "$@" --model "$MODEL"; fi
    "$@" - < "$PROMPT" > "$WORK/stdout"
    ;;
  *)
    echo "review: outcome=reviewer_failed" >&2
    echo "review: unsupported FACTORY_REVIEW_DRIVER: $DRIVER" >&2
    exit 2
    ;;
esac
STATUS=$?
if [ "$STATUS" -ne 0 ]; then
  echo "review: outcome=reviewer_failed" >&2
  echo "review: the reviewer exited $STATUS — this is a REVIEW THAT DID NOT RUN, not a review" >&2
  echo "review: with no findings. \`$DRIVER\` must be on the lane's PATH: declare where it is in" >&2
  echo "review: workspaces.<name>.toolchain (S4)." >&2
  exit "$STATUS"
fi

if [ ! -f "$RAW" ]; then
  echo "review: outcome=unreadable_answer" >&2
  echo "review: the reviewer produced no final-answer file — this is not a clean review" >&2
  exit 3
fi

# Blockers first, and within a severity the order the reviewer gave them — a review that reorders
# what it was told inside a severity is a review reporting something nobody said.
FOUND="$WORK/found"
{
  grep -E '^blocker [^ ]+:[0-9]+ .' "$RAW" || true
  grep -E '^minor [^ ]+:[0-9]+ .' "$RAW" || true
} > "$FOUND"
cat "$FOUND"

# A reviewer that answered in some other shape is not a reviewer that found nothing. Dropping the
# lines it did write and exiting 0 is the third appearance of this file's one defect — silence
# reading as clean — so an answer nothing could be made of is said out loud and is not an exit 0.
if [ ! -s "$FOUND" ] && [ -s "$RAW" ]; then
  echo "review: outcome=unreadable_answer" >&2
  echo "review: the reviewer answered and not one line of it is a finding — this is a REVIEW" >&2
  echo "review: WHOSE ANSWER COULD NOT BE READ, not a review with no findings. It said:" >&2
  sed 's/^/review:   /' "$RAW" >&2
  exit 3
fi

echo "review: outcome=ran" >&2
exit 0
