QA ticket {ticket_path}.

This is an independent QA session. Read §1 of the ticket and the owning repository, then reach
your own verdict from checks you run. Do not read the implementer's report, chat summary, or diff
narrative before writing that verdict. Open the report afterward only for the excerpt audit.

Resolve the owning repository and document root from the ticket path and `.factory/factory.json`:
`docs/tickets/X.md` uses this repository; `<repo>/docs/tickets/X.md` uses the declared nested
repository. Put the verdict and captures under its `docs/reports/`; run its workspace gates and
read skills from its git root, not an unrelated sibling. If it has
`docs/technical/workflow/WORKFLOW.md`, follow that canonical workflow for every ticket. For a
repository using the older layout, use its `docs/WORKFLOW.md`. Otherwise follow its `CLAUDE.md`,
the workflow it links, and ticket §2.
Use `.claude/skills/qa-ticket/SKILL.md` as the QA procedure from the owning repository, or the
checkout root when the owning repository has no copy. The applicable workflow and the landing
boundary below take precedence over an older skill ship example.

Run the handover check, copy every §1 criterion into your verdict, and verify each criterion
yourself. Run the applicable test tiers, including the workspace's declared gate and any surface
that a person must drive. Keep complete raw captures on disk and cite decisive lines and exit
statuses. Record every finding with its severity, blocker or minor, and apply the workflow's filing
and verdict rules. A criterion unrun or unsupported by your own evidence does not pass.

Follow the applicable workflow's context and evidence rules: read required shared documents once
in this QA session, search relevant sections, and reuse a capture only when its relevant inputs
are unchanged. Reuse never substitutes the implementer's capture for your independent QA run.
Preserve the complete capture even when you display only a short excerpt.

If both `FACTORY_PROGRESS_URL` and `FACTORY_PROGRESS_TOKEN` are present, report entry into QA and
log a non-202 response; their absence does not block QA. Keep the token out of reports and logs.

Commit your verdict and ticket status on this role branch as the skill directs. In any Factory
lane, the tool lands a passed branch for every ticket prefix: never use a skill's manual ship
command, create a PR, check out `main`, or push `HEAD:main` from the lane. For a legacy repository
without the canonical workflow, preserve the enclosing repository's merge safety on PASS: fetch
and merge `origin/main` into this branch, resolve conflicts, rerun the merged build and gate,
then push this branch by its own name for the tool to land. Its QA tier 2 includes touched modules
and `tests/test_isolation.py` plus `tests/test_confine.py`; drive the web client for tier 3 when
that is the ticket's surface. An operator outside a lane reads the skill's **On PASS** hand path
and follows its PR or manual landing route as applicable. A FAIL merges nothing. Add no model,
vendor, or tool attribution to commits, verdicts, or reports.
