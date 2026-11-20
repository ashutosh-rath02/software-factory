# Architecture

[Documentation](../INDEX.md) / [Technical](README.md)

The boundary below is stated here as *description* and in `MODEL.md` §2 as an *invariant a ticket is
checked against* — S1 is this one. Module ownership lives here; what may not be contradicted lives
there.

The whole design turns on one boundary:

> The factory knows **agents, lanes, resources and sessions**.
> It does not know what a board is, what a seam is, or what the repo it is pointed at builds.

Everything product-shaped enters through one door — the manifest — and everything else in the
codebase is downstream of it. When that door is the only way in, pointing the tool at `airpurifier`
instead of `acme` is a config change rather than a fork.

## Layout

```
factory/
  docs/                     product wiki and records in the owning repository
    README.md  INDEX.md  DOCUMENTS.md    reading entry points and complete catalog navigation
    overview/  product/    goals, progress, roadmap and behavior specification
    technical/            MODEL.md, ARCHITECTURE.md and implementation references
      contracts/          manifest.schema.json, http.schema.json, socket.schema.json
      workflow/           shared development and review process
    operations/           runbook, limitations, escape ledger and readiness registries
    design/  samples/  notes/  archive/
    epics/  tickets/  reports/    FAC-EPIC-* · SRV-* · WEB-* · a future DSK-*
    catalog/  tools/      generated file catalogs and their offline navigation checker

  .cache/                  ignored generated output, owned by each checkout
    server/               build/, build-tsan/ and pytest/
    client/               TypeScript incremental build state
    python/               bytecode from configured Python runners

  server/                   C++17
    CLAUDE.md  design/      the server's own instructions, and its module decisions
    core/                   vendored framework — engine, component, worker, http, ws, db
    app/
      manifest/             read and validate .factory/factory.json; the ONLY module that
                            knows a target repo has a shape
      catalog/              read epics, tickets, reports from the paths the manifest gave
      delivery/             what git and the process table say became of a ticket's work
      land/                 validate recorded PASS evidence and integrate in scratch space
      lane/                 worktrees, branches, the exclusive-resource queue, confinement
      routing/              finite command plans, durable receipts and escalation checkpoints
      agent/                supervise a process, own its stdin/stdout, parse the transcript
      permissions/          standing policy, pending grants and retained permission rules
      metrics/              derive workflow measures from supplied run and delivery facts
      error/                shared refusal and remedy vocabulary
      api/                  HTTP and WebSocket surface, one action per _<action>.cpp
    tests/
    build.sh  CMakeLists.txt

  client/web/               React + TS + Vite   (client/desktop, client/mobile later)
    CLAUDE.md  design/      the surfaces, the tokens, BRIEF.md
    src/
      services/             one http client, one socket client — nothing else talks to the wire
      pages/                requirements · requirement · ticket
      components/           transcript, composer, lane card, stage sequence, state chips
      types/                generated from http.schema.json and socket.schema.json
    tests/
```

`server/core` and `server/app`, `client/services` and `client/pages` are deliberately the same shape
as this repo's `server/` and `web/`. There is one set of conventions to learn, not three.

## Dependency direction

```
api ──▶ catalog ──┐
 │                │
 ├───▶ delivery ──┼──▶ manifest ──▶ core
 ├───▶ land ──────┤
 ├───▶ lane ──────┤
 ├───▶ agent ─────┤
 ├───▶ routing ───┤
 ├───▶ permissions┤
 └───▶ metrics ───┘
```

Arrows point one way. Nothing under `app/` may be included by `core/`, and no module may reach
sideways into a sibling's internals — `api` composes, the others do not know about each other.

Two consequences worth stating because they are the ones that decay first:

- **`manifest` is the only module that knows the target repo has a shape.** If `catalog` starts
  hardcoding `docs/tickets`, or `lane` starts knowing a resource is called a board, the boundary is
  gone and nobody will notice until the second product.
- **`agent` owns provider CLI launch and wire formats.** Claude stream-json and Codex app-server
  transports are translated inside this module into the tool's own vocabulary. A named profile
  and model selection enter through the manifest and API; provider-specific parsing stays behind
  the agent boundary. See `server/design/agent-session.md`.

## Threads, and the rule that stands on them

`core::Engine` runs a **pool** on one `io_context` — four threads, `ENGINE_THREADS` — and
`core::Worker` runs two more of its own. There is no "the engine thread": two handlers can be
executing at the same instant on different threads of the same pool.

Two rules follow, and the second one is only safe because of the first:

- **Long work belongs in a `core::Worker`; an io thread is never blocked.** Blocking one of four
  still stalls everything queued behind it. **The result arrives on the queue as the completion's
  parameter** — `queue.offload(work, done)` runs `work` on the pool and `done` on that queue with
  `work`'s return value, so a caller neither allocates a slot to carry the answer nor re-posts its
  own completion. The two-argument `core::Worker::post`, whose completion landed on the raw engine
  instead, is private to `core::Queue` and the hand-written hop no longer has a type
  (`SRV-FIX-476`); `server/design/core-queue.md` has the reasoning. What may be offloaded is what
  WAITS: a syscall that returns without waiting belongs on the queue.
- **State reached from more than one handler belongs on a strand — never a mutex.** asio's answer
  to shared state is an executor: `boost::asio::make_strand(core::Engine::instance().service())`,
  every handler `bind_executor`'d to it, every other caller posting to it, including `stop()`.
  `api::Socket` and `catalog::Store` each own one. A mutex here would block an io thread in order
  to protect state, which breaks the first rule to satisfy the second.

The precondition is the load-bearing half. "Never a mutex in event-driven code" arrived from the
product `core/` was vendored from, and this document restated it for months underneath the phrase
*"the engine thread"*, singular — an engine that does not exist. A rule stated without the fact
that makes it true gets applied where it does not hold, and the shared state in `catalog` went
unguarded for exactly that reason (`ESCAPES.md`, `SRV-FIX-024`). So the thread count is **asserted**
in `server/tests/test_concurrency.py` rather than described here: a change to it turns a build red
instead of quietly making this paragraph wrong.

## What each module owns

**`manifest`** — parse, validate against the schema, refuse a major version it does not know rather
than guessing. Everything downstream receives a typed value, never a path string it invented.

**`catalog`** — the target repo's documents are the database. No copy, no index, no cache that can
disagree with the files; git is the state, and a page load reads what is on disk. It reads only the
frontmatter it was told to read, and passes unknown fields through for display rather than dropping
them — that is how `seams` reaches the UI without the tool knowing what a seam is.

**`delivery`** — what git and the process table say became of a ticket's work (`SPEC.md` §3.5). It
never opens a document and `catalog` never spawns a subprocess; `api` composes the two. Every git
invocation is read-only, carries `--no-optional-locks`, and is refused unless its subcommand is in
the allowlist the tool serves at `E1`. `git` is a subprocess and subprocesses are slow, so the whole
repository is probed once per pass on a `core::Worker` and the result is published on a strand —
a request reads a snapshot, never a `fork`.

**`lane`** — a lane is one worktree, one branch, one agent seat, and possibly one exclusive resource.
This module owns the confinement: an agent must not be able to reach outside its worktree, and
enforcement is its job, not a rule the agent is asked to follow. It also owns the queue, because
"one board" and "one staging database" are the same problem.

**`land`** — read the candidate's committed verdict and recorded independent role-session evidence,
integrate in scratch worktrees, preserve authorship, and move the base without overwriting the
operator's checkout. It owns Git writes for automatic landing. Landing does not certify a release
or execute the post-merge full VERIFY gate; `server/design/land.md` owns the mechanism and S7 owns
the checkout constraint.

**`routing`** — own finite explicit command plans, intent journals, complete captures, source-bound
receipts, opt-in reuse and stable repeated-error checkpoints. One component queue serializes
admission and callbacks; blocking journal I/O runs on workers. `api` supplies read-only source
fingerprints and composes the existing lane command executor. The module neither launches agents
nor judges acceptance. [The routing contract](../../server/design/ticket-routing.md) owns its wire
and restart behavior.

**`permissions`** — own standing grants, in-memory pending requests and retained permission rules.
Retained rules persist under manifest-resolved paths; pending requests are not a durable restart
record. `api` composes permission outcomes with agent/lane actions.

**`metrics`** — reduce supplied run, ticket, stage, delivery and ledger facts into workflow measures.
`api` collects those inputs; the module does not invent deployment or release events.

**`agent`** — spawn, write stdin, stream stdout, interrupt a turn, end a session, reap the exit. It
keeps every session rather than overwriting it, so a run can be read back after the fact. Long work
belongs in a `core::Worker` and **no io thread is ever blocked** — see the rules above; process
supervision is precisely where both of them get broken.

**`api`** — HTTP for actions and reads, WebSocket for everything live. The client never polls: a
poll that rebuilds a page is what ate half-typed messages in the prototype, and the architecture
should make that mistake hard rather than forbidden by convention.

## The client

`services` is the only place that speaks to the server; a page that fetches is a page that will
drift. The socket delivers deltas and components patch in place — **nothing is rebuilt on a timer**.
Types come from the HTTP and socket schemas by generation, so a field added on the server cannot silently
disagree with the field the UI reads.

Visual design is not decided here. `client/web/design/BRIEF.md` is the brief; the component
vocabulary it returns is what `components/` implements.

## Documentation entry points

[INDEX.md](../INDEX.md) locates the overview, roadmap, technical, product, operations, samples, design
and notes categories. The generated [DOCUMENTS.md](../DOCUMENTS.md) is an offline file inventory, not
runtime product state. Existing contracts, module decisions and design exports keep their owning
paths; category guides link to them. The [system flow diagrams](../design/SYSTEM-FLOWS.md) are
descriptive, with the proposed release layer labeled separately.

## Things deliberately not done

- **No database.** The documents in the target repo are the state. Adding one creates a second
  source of truth that will disagree with git, and disagreeing with git is the failure this whole
  process exists to prevent.
- **No remote execution, no auth, no multi-user.** One developer, one workstation. Every one of those
  would change the security model, and none of them is needed.
- **`core/` is vendored, not shared — for now.** Extracting it into a library both this and Acme's
  server depend on is the right end state and is not attempted while the factory still lives inside
  Acme's repo. The copy records the commit it came from so the two can be reconciled later.
