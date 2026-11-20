# Technical contracts

[Documentation](../../INDEX.md) / [Technical](../README.md) / Contracts

These are the canonical schemas consumed by the server and client tooling. The
[product specification](../../product/SPEC.md) defines observable behavior; the schemas declare
the data that crosses each boundary.

| Contract | Source | Use |
|---|---|---|
| HTTP | [http.schema.json](http.schema.json) | Request and response shapes for declared API endpoints |
| Socket | [socket.schema.json](socket.schema.json) | Frames exchanged on the live connection |
| Manifest | [manifest.schema.json](manifest.schema.json) | Repository shape, workspaces, resources and configured commands |

Change the owning contract first, regenerate its client types where applicable, and validate it
through the [shared workflow](../workflow/WORKFLOW.md). The [architecture](../ARCHITECTURE.md)
describes module ownership and the [model](../MODEL.md) defines the invariants these contracts preserve.
