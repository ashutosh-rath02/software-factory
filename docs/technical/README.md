# Factory technical documentation

[Documentation](../INDEX.md) / Technical

Technical documents explain implementation and its constraints. Start with the [overview](../overview/README.md),
then the source for the area being changed. The [index](../INDEX.md) and [inventory](../DOCUMENTS.md)
locate the rest.

The app's current Wiki reads this directory's immediate Markdown files, preserving access to
the model used by Seams. Product and process pages remain available through the repository index;
the app needs separate nested-document support to browse those categories.

| Topic | Authoritative source |
|---|---|
| Boundaries and decisions | [MODEL.md](MODEL.md) |
| Modules, dependencies and concurrency | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Contracts and behavior | [SPEC.md](../product/SPEC.md), [contract catalog](contracts/README.md), [HTTP](contracts/http.schema.json), [socket](contracts/socket.schema.json), [manifest](contracts/manifest.schema.json) |
| Server implementation rationale | [server/design](../../server/design/) |
| Server conventions/build | [Server instructions](../../server/CLAUDE.md), [CMake](../../server/CMakeLists.txt), [build script](../../server/build.sh) |
| Client conventions/generated contracts | [Client instructions](../../client/web/CLAUDE.md), [package scripts](../../client/web/package.json), [generators](../../client/web/scripts/) |
| Framework provenance | [VENDORED.md](../../server/core/VENDORED.md) |
| Engineering checks | [Shared process](workflow/README.md), [WORKFLOW.md](workflow/WORKFLOW.md), [REVIEW.md](workflow/REVIEW.md), [test sources](../../server/tests/) |
| Documentation navigation | [Inventory and link checks](../tools/README.md) |
| Operation and recovery | [Operations](../operations/README.md) |
| System diagrams | [Design](../design/README.md) |

Keep a technical decision with its owning subsystem. This directory owns the model, architecture,
contracts and shared process, with one canonical source for each. A proposal states its owner, affected contracts, alternatives,
compatibility and evidence; it remains a draft until the applicable decision process accepts it.

Browse [all technical references](../DOCUMENTS.md#technical), or continue to
[product requirements](../product/README.md), [system flows](../design/SYSTEM-FLOWS.md) and
[local operation](../operations/RUNBOOK.md).
