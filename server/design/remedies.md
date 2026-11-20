# A remedy belongs to a SITE, not to an error code

The decision behind `remedy::none()` at `app/api/_transcript.cpp`, moved out of the code by
`SRV-FIX-343`. `docs/product/SPEC.md` §3.6 and §11 check 12 are the contract; this note is why the document
grew a third table rather than the change being one word.

## The state it is about

`E8 GET /api/runs/{run_id}/transcript` answers `404 not_found` for a run whose `transcript.jsonl`
is absent or has been cleaned. `SRV-FEAT-315` made that state deliberate: the run keeps its row in
`E7` and `E31` off `meta.json`, and only the log is gone. **The row is the record, the log is the
evidence, and losing the evidence is not losing the record.**

The `404` used to carry `remedy::reloadManifest()`. Reloading the manifest re-reads
`.factory/factory.json`; the tool never writes a run's log, so no reload could bring one back and
the button's only possible outcome was the same `404` again. `WEB-FEAT-176` decided that a remedy
**becomes the control that ends the state**, so a remedy that cannot end it is `SRV-FIX-193`'s
shape — a control reporting success having done nothing — one layer up.

## Why the code-level tables could not say it

Check 12's two tables are keyed by error code, and `not_found` is a code six other endpoints emit
at states a reload really does end (`E10`'s unclaimed lane among them). Both cheap moves lose
something real:

- exempting the **code** makes every other `not_found` a dead end, and takes `reload_manifest`
  off six refusals a person can actually act on;
- keeping the **code** remedied leaves the button on the one screen where it does nothing.

So the granularity was the defect. A code says what went wrong; whether anything can end it is a
property of **where** it went wrong. The site-exemption table is that third answer, and it is
narrow by construction — `test_spec.py::audit` refuses a row whose code is not in the remedy table,
whose code is already exempt everywhere, whose site is not an endpoint `docs/product/SPEC.md` §5 declares, or
whose reason is shorter than the bar a code-level exemption is held to.

## The member is present and null

`remedy::none()` emits `"remedy":null`, not nothing. An omitted member is indistinguishable from a
code nobody has decided about yet — the state `bad_filter` sat in for weeks (see
`catalog-rendering.md`) — so **an exemption is a claim and the body carries it.**
`test_errors.py::test_every_refusal_the_suite_provokes_carries_a_remedy` asserts the site-exempt
`null` the same way it asserts a code-exempt one, and it now also asserts that the corpus
**reaches** every site the document exempts: a site exemption nothing on the wire is judging is a
row that stopped being a check.

# `already_passed` — the tool may not act, so the remedy is `null`

`SRV-FIX-478`, and the other of check 12's two exemption shapes: not *the control that produced it
is already the way out*, but **the tool may not act**.

`E12` refuses a claim on a ticket whose own document says `status: pass`. The way out of that state
is a person filing a `FIX` or editing the status, and `docs/technical/MODEL.md` `S2` says the target repository's
documents are the only state about its work and **the tool writes none of them**. So every action
the tool could offer here is the tool deciding that finished work is unfinished — a `claim_lane`
that re-offers the refused call, or a write into the repository that `S2` forbids outright.
`reload_manifest` is worse than useless: the status is not in the manifest, and re-reading
`.factory/factory.json` returns the same `409` having done nothing, which is the shape
`SRV-FIX-193` and the `E8` note above are both about.

This is a **code-level** exemption and not a site one. `already_passed` is emitted at one site and
is terminal there; a site row is for a code that stays remediable somewhere else, and
`test_spec.py::audit` refuses a row whose code is in neither the remedy table nor an endpoint §5
declares. A code that is nowhere remediable belongs in the exemption table, with its reason.

What the body carries instead of an action is where to go: `detail.path` names the file the status
was read from, relative to the document root, and `detail.status` echoes the word the document
carries rather than restating it. That is the whole of what a person needs to open the one document
that ends this. The member is present and null, for the reason the section above gives —
`remedy::none()`, not an omitted key — and `test_errors.py`'s corpus provokes the refusal so the
`null` is judged on the wire rather than asserted about the source.

## Refusal coverage runs across module boundaries

SRV-FIX-570 makes every assertion in the actual HTTP refusal corpus a repository check. Ordinary
comparative docgate now executes the code inventory, error schema, §11 governance, targets, verbs,
nouns, destructive-cost and successful-response invariants even when a ticket's module-local list
names only its routing or agent tests. A real pytest collection probe derives the corpus's test
functions from its source and requires repository selection to collect them all. Its private copy
with that marker removed exposes the omitted selection.

Source inventory also discovers response factories from the argument actually encoded into
`error.code`, then reads literal callers at that parameter's position. Catalog's stream builder
takes code first; routing and preflight take status then code. Ordinary named return types and
aliases are discovered without a return-type whitelist. Qualified owner scope distinguishes a
factory's short-name calls from an unrelated namespace helper with the same name. Message/target
arguments and notices outside an error envelope are excluded. A newly named literal in a copied real routing call must
turn the actual corpus coverage guard red against the unchanged real HTTP responses. Named
constants, complex dynamic expressions, namespace aliases, suffix-qualified calls and C++ syntax
outside ordinary signatures/exact-qualified or same-owner bare literal calls remain the existing
SRV-FIX-185 limitation; this bounded correction does not claim a complete C++ data-flow analysis.

Pending, failed and timed-out preflights offer the existing `retry` action. That action only
reissues the caller's request; it does not interrupt a held preflight, release a lane or bypass
readiness. Routing's transient busy/missing-target refusals also use explicit retry after the
holder finishes or the caller establishes its missing target. Undeclared commands retain
`reload_manifest`. Malformed requests retain their existing null exemptions.

Routing's immutable-plan conflict and unresolved source/evidence/claim diagnostics retain null
with reasoned §11 governance. Choosing a corrected ID, reconstructing missing evidence or deciding
what an obsolete claim meant is main/operator work. No remedy can authorize automatic replay or
replace retained history. Actual held-command and tampered-capture negatives prove those effects
remain absent, and a successful readiness-repaired start proves retry admission still works.
