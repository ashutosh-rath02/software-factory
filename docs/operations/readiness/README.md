# Repository readiness registries

[Documentation](../../INDEX.md) / [Operations](../README.md) / Readiness

| File | Role |
|---|---|
| [workflow-causes.json](workflow-causes.json) | Canonical registry of reviewed causes and their resolution evidence |
| [workflow-causes.example.json](workflow-causes.example.json) | Example registry for configuring the readiness process |

The [readiness design](../../../server/design/ticket-readiness.md) explains probes, recorded causes,
resolution receipts and confined registry access. Follow the
[shared readiness process](../../technical/workflow/WORKFLOW.md#known-failures-and-run-readiness)
when recording or resolving a cause. An example entry or passing probe alone does not establish
that a cause has been resolved.

Use the [escape ledger](../ESCAPES.md) for escaped defects and the [runbook](../RUNBOOK.md) for
operational diagnosis. Registry locking is local runtime state; a generated lock file is not a
second source of product knowledge.
