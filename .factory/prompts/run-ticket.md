Run ticket {ticket_path}.

Read the ticket in full. §1 is the request, §2 is its standing process, and Pre-decided choices are
closed. Resolve the owning repository and document root from the ticket path and
`.factory/factory.json`: `docs/tickets/X.md` uses this repository; `<repo>/docs/tickets/X.md`
uses the declared nested repository. Keep reports, tests, builds, and gates in that repository;
do not write a literal checkout-root `docs/` for a nested ticket. Read skills from that git
repository's root, not an unrelated sibling.

If the owning repository has `docs/technical/workflow/WORKFLOW.md`, follow it as the canonical
workflow for every ticket. For a repository using the older layout, use its `docs/WORKFLOW.md`.
Otherwise follow that repository's `CLAUDE.md`, the workflow it links, and the ticket's §2.
Use `.claude/skills/run-ticket/SKILL.md` as the execution guide from the owning repository,
or the checkout root when the owning repository has no copy. Read `CLAUDE.md`, `docs/technical/MODEL.md` §2,
and `docs/technical/ARCHITECTURE.md` where present, plus context required by the applicable workflow.
The shared workflow takes precedence over an older skill example when they conflict.

Do the required work in order: write SPEC before code, make each §1 assertion a real test and
capture its RED before implementing, then make the smallest fix. Run every named build target
and the owning workspace's real GATE, including required negative and independent boundary checks.
GATE is green only when every §1 criterion has run on the final relevant source and passed. Run
the declared REVIEW command when required; a reviewer failure is not a clean review. Record
findings as blocker or minor, fix blockers, and file each minor as the applicable process requires.
File an owning fix ticket with raw failure evidence for a genuine dependency before marking a
criterion BLOCKED.

Follow the applicable workflow's context and evidence rules: read stable context once per session,
search relevant sections, retain complete raw captures on disk, quote deciding output and exit
status in the report, and reuse a capture only while its relevant inputs remain unchanged. Save
the report and full logs under the owning repository's `docs/reports/` on this branch. Do not
commit to `main` from a lane.

After a passing handover, set `status: awaiting-qa` and hand the ticket to a separate independent QA
session with §1 and the repository only. Only QA may set `status: pass`; the implementing session
does not grade its own work. In a Factory lane, report the QA stage and let the tool start its QA
role; do not start another QA session yourself.

If both `FACTORY_PROGRESS_URL` and `FACTORY_PROGRESS_TOKEN` are present, report each stage on entry
using the names in the ticket's `stages:` line and log non-202 responses. Their absence does not
block a run. Keep the token out of reports and logs. Add no model, vendor, or tool attribution to
commits, reports, or ticket documents.
