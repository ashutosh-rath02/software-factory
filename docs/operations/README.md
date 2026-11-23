# Operations documentation

[Documentation](../INDEX.md) / Operations

This section answers how to build, run, inspect and recover the current local Factory. It also
names the operating capabilities that still need implementation or an agreed procedure. Start
with the [runbook](RUNBOOK.md); use the [documentation index](../INDEX.md) for product context.

## Pages in this section

| Page | Use for |
|---|---|
| [Local runbook](RUNBOOK.md) | Prerequisites, build/start, inspection, restart, diagnosis and draft backup/restore |
| [Known limitations](KNOWN-LIMITATIONS.md) | Recorded operating constraints and their workarounds; confirm their current evidence |
| [Escape ledger](ESCAPES.md) | Recorded defects and the cases intended to prevent recurrence |
| [Readiness registries](readiness/README.md) | Canonical open causes, example configuration and the operating process |

## Operational coverage

| Area | Available today | Source and remaining work |
|---|---|---|
| Build and local deployment | C++ server build, web bundle build and a Linux development runner | [Root build script](../../build.sh), [server build](../../server/build.sh), [web scripts](../../client/web/package.json). The runner serves the client through Vite development mode. A versioned package, artifact manifest and installed release layout are not defined. |
| Runbook | Start, stop, inspect identity, inspect lanes and diagnose common local failures | [RUNBOOK.md](RUNBOOK.md), [known limitations](KNOWN-LIMITATIONS.md), [manifest design](../../server/design/manifest.md). This runbook describes source behavior; it is not evidence of a live operational drill. |
| Incidents | Historical escapes, structured readiness causes and linked captures | [Escape ledger](ESCAPES.md), [cause registry](readiness/workflow-causes.json), [readiness design](../../server/design/ticket-readiness.md), [reports](../reports/README.md). A dedicated operational incident procedure, severity/owner policy and recovery targets are not established here. |
| Backup and restore | Git history for committed code and documents; local runtime records on disk | [State inventory and draft checklist](RUNBOOK.md#backup-and-restore-draft). There is no approved retention policy, automated backup job or demonstrated restore drill. Git alone does not preserve local run history or uncommitted lane work. |
| Monitoring | Health, lane/resource state, transcripts, socket state and process metrics | [API contract](../technical/contracts/http.schema.json), [socket contract](../technical/contracts/socket.schema.json), [metrics design](../../server/design/metrics.md). External alerting, availability targets and a retention policy remain to be defined. Process metrics do not establish service availability. |
| Restart and update | Lane registry recovery and worktree adoption on startup | [Recovery design](../../server/design/lane-recovery.md). A health-checked release activation and automatic rollback are not implemented as a release system. Recovery of a lane record is not proof that its active run or exclusive resource ownership survived. |

The current product boundary remains one local developer and workstation. Hosting, remote access
or multiple operators would require a decision in the [product model](../technical/MODEL.md), rather than an
operational shortcut. The [roadmap](../overview/ROADMAP.md) tracks proposed release work; recording a
proposal there does not make it available in the running tool.

Browse [all operations references](../DOCUMENTS.md#operations), [system flows](../design/SYSTEM-FLOWS.md)
and [validation and incident evidence](../reports/README.md).
