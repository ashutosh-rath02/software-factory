# Workflow evidence and native usage

SRV-FEAT-563 owns the declared REVIEW transport and the offline usage tools. The shared process
remains in `docs/technical/workflow/WORKFLOW.md`; provider adapters refer to that process rather than define another
one.

`review.sh` keeps a complete changed-path inventory. Report hunks remain on disk, with their
paths listed in the prompt. Ordinary actionable diffs are inline. If the prompt exceeds 900,000
bytes, the reviewer receives instructions and inventory and must read every actionable path with
read-only Git commands. If that smaller prompt also exceeds the bound, the command fails before
starting the reviewer. This avoids the observed CLI input limit without changing review scope.
An empty actionable diff, an unstarted or failed reviewer, an unreadable answer, and a completed
review with no findings are distinct outcomes.

`review_token_eval.py` compares pinned source inputs and the selected head's instruction bytes,
retaining both prompt artifacts and native events. It accepts the historical `docs/REVIEW.md`
location and the current `docs/technical/workflow/REVIEW.md` location; disagreeing documents are
an error. During stubbed prompt capture, missing locations temporarily receive byte-identical
instruction aliases so historical scripts remain unchanged. Those aliases and directories are
removed on success or failure before the clone's cleanliness check or either live review arm.
The live reviewer sees the original committed head snapshot. Only complete native usage can
support a token comparison.
`ticket_token_usage.py` groups Factory records by native session and uses cumulative usage once
for resumed sessions. Missing usage and model mismatches stay explicit. Cached input remains a
separate component of input, and token totals are not prices.

Smaller initial prompts do not establish smaller complete runs. The three preserved REVIEW pairs
in this pilot increased total tokens. Required gates, independent QA and post-merge VERIFY remain
separate executions; same-session capture reuse requires unchanged relevant inputs and recorded
provenance.
