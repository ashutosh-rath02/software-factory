# Factory product documentation

[Documentation](../INDEX.md) / Product

Product documents describe the operator's problem, the behavior that solves it and how acceptance
is demonstrated. The [overview](../overview/README.md) gives purpose and current capability state; the
[roadmap](../overview/ROADMAP.md) gives milestones and proposed priorities.

| Document type | Current source | Coverage |
|---|---|---|
| PRD and product requirements | [SPEC scope](SPEC.md#1-what-this-specifies--and-what-it-does-not), [model target](../technical/MODEL.md#1-target), [epics](../epics/README.md) | Requirements exist across these sources; a separate consolidated PRD is not established |
| Product specification | [SPEC.md](SPEC.md) | Vocabulary, surfaces, contracts and executable behaviors |
| User stories | [Epic user journeys](../epics/README.md) and [ticket behavior criteria](../tickets/README.md) | Acceptance prose exists; a separate story collection is not established |
| Features | [SPEC features](SPEC.md#7-features-and-their-acceptance-criteria), [overview capability map](../overview/README.md#current-capability-state) | Required behavior and reviewed implementation state have separate owners |
| Business case | [Epic why-now measurements](../epics/README.md) and [captured round/cost evidence](../reports/README.md) | A consolidated business case is not established; savings require measurement |
| Product acceptance | Epic L2 journey, child verdicts and [reports](../reports/README.md) | [WORKFLOW.md](../technical/workflow/WORKFLOW.md) defines closure; child PASS alone is insufficient |
| Release scope | [Roadmap proposal](../overview/ROADMAP.md#release-completion-to-define) | No versioned release model is established |

Trace the problem or business case to a requirement, user journey, implementing tickets, independent
acceptance, integrated verification and eventual release evidence. Link IDs and evidence. Record
missing evidence explicitly.

Planning should identify priority, accountable owner, dependencies, target release, success measure
and exclusions. These are proposed planning coverage, not semantics the runtime already enforces.
Frontmatter changes must respect existing manifest/catalog contracts.

Use the reusable [PRD](../samples/_PRD.md), [story](../samples/_USER-STORY.md),
[feature](../samples/_FEATURE.md), [specification](../samples/_SPEC.md) and
[business case](../samples/_BUSINESS-CASE.md) forms for missing context. Engineering epics and
tickets continue to use their established [epic](../epics/_TEMPLATE.md) and
[ticket](../tickets/_TEMPLATE.md) forms and allocation workflow. Link authoritative clauses instead
of copying them into competing normative documents.

Browse [all product documents](../DOCUMENTS.md#product), [requirements](../epics/README.md),
[implementation work](../tickets/README.md) and [acceptance evidence](../reports/README.md).
