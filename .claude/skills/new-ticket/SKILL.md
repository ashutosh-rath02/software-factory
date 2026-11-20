---
name: new-ticket
description: Turn a one-line feature/bug request into a fully specified factory ticket in docs/tickets/ — allocates the id from the tool, fills §1 from the real code, writes executable pass criteria. Use for "/new-ticket ...", "make a ticket for X", or before running any work worth a report.
---

# Write a factory ticket

Produces `docs/tickets/FAC-TYPE-NNN-<slug>.md` — §1 filled from the REAL code, §2 a one-line pointer
to `docs/tickets/_TEMPLATE.md` §2. The value of a ticket is entirely in §1: an assertion somebody can
grep beats a paragraph of intent.

**Every path in this file is relative to this repository — the factory.** There is one project here
and one `docs/` tree. `FAC-*` ids belong to it and to nothing else.

## 1. Right-size first

If the request is a one-line fix, a copy change or a single CSS tweak → say so and do it inline; do
not create a ticket. Tickets are for work worth a report: new behaviour, a change across both
halves, a change to a schema the client generates from, anything that touches lane isolation.

## 2. Allocate the id — ASK THE TOOL, do not count files

```bash
.claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"SRV-FIX"}'
# → {"id":"SRV-FIX-368","prefix":"SRV-FIX","series":"SRV","number":368}
```

**THE PREFIX IS THE WORKSPACE.** `SRV-*` for `factory-server`, `WEB-*` for `factory-client`, and
`FAC-EPIC` for a requirement that owns children in both — `FAC-` reaches nothing else, and
`test_component_scheme.py` refuses any other pairing. There is no third component prefix: work
spanning both is an epic with children, because `WORKFLOW.md` line 1 is *one product, two
components*.

`prefix` is therefore `SRV-FEAT`, `SRV-FIX`, `WEB-FEAT`, `WEB-FIX` or `FAC-EPIC`, written exactly
as it will appear. Use the `id` the
tool returns and no other. `422 unknown_prefix` means a typo: it lists the prefixes in use, and the
tool will not start a numbering series for a mistyped one.

**Never `ls docs/tickets/ | tail` and add one.** You are in a lane; a lane is a checkout cut at claim
time and nothing merges into a live one, so the highest id YOU can see is not the highest id. On
2026-08-21 two lanes did exactly that within an hour and both filed `SRV-FIX-185` — different
defects, one id, and `409 ambiguous_ticket_id` then made both of them unreachable (`SRV-FIX-188`).
Both answers were right; the question was wrong.

If the tool is not running, **stop and say so** — do not fall back to counting.

**Point the instance at THIS repository.** `cd . && ./build.sh run "$PWD"`. An instance pointed at a
repository the factory is *working on* answers `404 not_found` for a `FAC-*` id by design
(`SRV-FEAT-244`) — that is the wrong instance, not a missing ticket.

## 3. Investigate before writing (this is the work)

Never write §1 from the request alone. Ground every field in the code:

- **Read `docs/technical/MODEL.md` §2 FIRST.** Name the seams the work touches and put their ids in `seams:`
  alongside the endpoint ids. Cite the invariant; never paraphrase it. If the ticket cannot be
  written without contradicting or extending a seam — or if a decision in §4 is marked OPEN —
  **stop and say so**: it is a model change, and writing it as a ticket is the exact failure the
  model exists to prevent.
- Which half actually changes? `server/app/<module>` or `client/src`? Grep the module and the
  component before claiming either.
- Does a **generated** contract change? `docs/technical/contracts/http.schema.json`,
  `docs/technical/contracts/socket.schema.json`, `docs/technical/contracts/manifest.schema.json`,
  `client/web/design/`. If so it changes FIRST and is regenerated — the client's types and tokens
  derive from it, and the browser gate validates the live server against it.
- Which endpoint ids and socket frames exist already (`docs/product/SPEC.md` §5–6) — extend or add?
- **Grep `docs/operations/ESCAPES.md`** for the rows this subject already has, and for the rows the
  `escaped_from:` target already has. Cite the row in §1 and set `recurrence_of:`. 55% of that
  ledger is a source leaking a second time, because the earlier row is never opened.
- For a FIX: reproduce first, or at minimum quote the failing output verbatim as evidence in §1.
- What is the FULL shape of any store or response this adds to? Design it once, here — the NO-PATCH
  rule forbids widening it field-by-field across tickets.

## 4. Fill §1 — the rules that make it runnable

- **Behaviour, not implementation.** What a person can do afterwards that they cannot do now.
- **Pass criteria must be EXECUTABLE**, one block per workspace touched:
  - `factory-server` → named pytest cases in `server/tests/test_<area>.py`
  - `factory-client` → named vitest cases asserting the operator COMPLETES the task, in the used
    state; plus the browser-gate surface if the change is visible
- **No criterion may quote a model's prose.** A criterion is a fact about the tool — a count, an
  order, an id, an exit status, a file that exists. `"the model said one"` goes red about one run in
  three, and then a green run and a red run describe the same correct behaviour (`SRV-FIX-042`). If
  the fact you need is not observable on the wire, **say so** — the absence is the finding.
- **A value AGGREGATED from a collection is asserted on a collection with >1 member whose members
  DISAGREE.** `min`, `max`, `first`, `any` and `all` are indistinguishable on a singleton.
  `SRV-FEAT-165` spread three delivery states across three requirements of one ticket each; the
  defect lived inside a requirement, all three cases passed, and QA found it in one call
  (`SRV-FIX-166`).
- **A claim about a collection is asserted in the same case as the collection** — the count and the
  rows in one assertion, never a case for each (`WEB-FIX-091`).
- **A criterion distinguishing two possible sources forces them apart first** — `HEAD` or a branch,
  the manifest or the filesystem, the cache or the disk. A fixture where both hold the same value
  proves only that the code read one of them (`SRV-FIX-184`).
- **Every workspace block needs a NEGATIVE test** — the wrong, unauthorised or malformed input the
  system must refuse correctly. A criterion that cannot fail is not a criterion.
- **A record that stores a lane name owes a case that releases the lane and then asks the record.**
  Lane names are reused (`SRV-FIX-247`, `SRV-FIX-256`).
- **Safety** defaults to this repository only: no writes outside it, no deleting a lane or a run
  history, no `main` push before QA. Override explicitly.
- **Pre-decided** — dump every judgement call you already made, so the run session never asks.
- Scope it to ONE deliverable. If it splits naturally, write two tickets and say which is
  critical-path.

## 5. Point at §2 — do not copy it

End the ticket with exactly this line (SRV-FIX-580 — a 665-line copy in every ticket was re-read
on every pass):

    ## 2. Standing process — `docs/tickets/_TEMPLATE.md` §2, fixed; read it once per session.

§2 is fixed and the run session follows it verbatim; it lives in one place.

## 6. Hand off

Report the ticket path plus a 3-line summary (what / which workspace / how it will be gated) and the
one-liner to run it:

    Run ticket docs/tickets/<file>.md

For ≥2 tickets at once, hand them all off to the `parallel-tickets` skill instead — it decides what
parallelises and keeps the shared-resource gates sequential.
