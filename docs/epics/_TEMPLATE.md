---
id: FAC-EPIC-NNN
title: <REQUIREMENT NAME>
status: draft            # draft | frozen | in-progress | l2 | done | blocked
contract: factory-v0.1.0
seams:                   # S1 S2 S3 S4 S5 S6 — space separated, blank if none
owner:
created: YYYY-MM-DD
---

# FAC-EPIC-NNN — <REQUIREMENT NAME>

> Product-level requirement. Owns the contract and the product gate; owns NO code.
> Children live in `docs/tickets/` and carry `parent: FAC-EPIC-NNN`.
> Status rolls up here — this file IS the board.
>
> **This file is a form, not work.** `catalog::Store::isDocument` skips `_*.md` and `README.md`, so
> it does not appear as a requirement. `SRV-FIX-043` and `SRV-FIX-263` are what it cost to learn
> that: a blank form with a status, an age, a workspace chip and a child, counted by every filter
> and by the `needs you` totals.
>
> **The frontmatter above is what the tool reads — keep `status` current as work moves.**

---

## 1. Requirement (human, in product terms)

- **What the operator gets:** <observable behaviour on a screen or at an endpoint — no
  implementation, no module names>
- **Why now / trigger:** <the problem this removes. For the factory this is usually a measurement:
  what it costs today, in minutes or in escapes.>
- **Out of scope:** <the adjacent things this explicitly does NOT do>

## 2. Analysis (agent — grounded in the real code, cite file:line)

- **Behaviour decomposition:** <the requirement split into observable behaviours>
- **Ownership:** <behaviour → `factory-server` | `factory-client` | `factory`, with the reason.
  Remember `S1`: if a behaviour needs the tool to know what the target repository builds, it is the
  manifest's job, not a module's.>
- **Seams touched (`docs/technical/MODEL.md` §2):** <S1…S6 — for each, the invariant sentence quoted, and what
  this epic does that comes near it. A seam that must CHANGE is a model change and stops here.>
- **Surfaces touched (`docs/product/SPEC.md` §5–6):** <endpoint ids and socket frames — new or widened. A
  widened one names every consumer, per §2's widening rule.>
- **What already exists:** <the modules, endpoints and components this extends, cited — do not
  rebuild>

## 3. Contract freeze (BEFORE any child ticket is written)

The client's types are GENERATED from these files. Change them first, regenerate, and only then
write a child.

| File | What changes | Regenerate with |
|------|--------------|-----------------|
| `docs/technical/contracts/http.schema.json` | | `cd client && npm run gen:api-types` |
| `docs/technical/contracts/socket.schema.json` | | `cd client && npm run gen:types` |
| `docs/technical/contracts/manifest.schema.json` | | — (validated by the server) |
| `docs/design/` | | `cd client && npm run gen:tokens` |

- **Manifest compatibility:** <does a repository written against the current major still load? which
  fields are optional? does the major bump? The tool refuses an unknown major rather than guessing.>
- **Version bump:** `factory-vX.Y.Z` — <what changed>

## 4. Children (one row per ticket)

| Ticket | Workspace | Deliverable | Depends on | Wave |
|--------|-----------|-------------|------------|------|
| `SRV-FEAT-0NN` | factory-server | | — | 1 |
| `WEB-FEAT-0NN` | factory-client | | the server's endpoint | 2 |

- **Ordering:** <the server ships the surface before the client can draw it; say which child is
  critical-path and which can run beside it.>

## 5. L2 product gate (the only thing that closes this epic)

- **Journey:** <the operator path, end to end, across both halves — from the screen, not from a
  test>
- **Terminal state asserted:** <the state that means the requirement is MET, not an intermediate.
  "the lane is running and the transcript is on screen", not "the POST returned 201">
- **Driven by hand:** <`./build.sh run "$PWD"`, http://localhost:7779, and what a person does. A
  green browser gate does NOT close an epic: the gate drives `gate/fixture-repo`, a synthetic
  repository — `WEB-FIX-248` and `WEB-FIX-261`.>
- **Against the real repository:** <the journey run once with the tool pointed at a repository that
  is not the fixture. `SRV-FEAT-165` passed three green cases and a green browser gate, and QA found
  the defect in one call against the real thing.>
- **Negative:** <the failure the system must handle correctly, end to end>

## 6. Roll-up (updated as children land)

| Child | Tier 1–3 | QA verdict | Report |
|-------|----------|------------|--------|

**Epic DONE = every child QA PASS **and** the L2 gate green, driven by a person.**
All-children-green is not done.
