# Design brief — what `factory.pen` must carry

## Two roles, one product

> **employee** — owns their own lanes. *What is my agent doing, and what does it need from me?*
> **manager** — owns the people and the product. *Will the sprint land, who is holding it, and is
> it safe to cut?*

**One navigation, not two.** The same eight destinations for both; a `Mine` / `Everyone` switch
changes WHAT IS COUNTED, never what exists. A small team cannot afford two products, and
role-partitioned navigation has a specific failure: two people describe the same screen differently
and cannot reconcile it. This way the words are the same and a number that differs says why.

## The design is checked, not asserted

```bash
python3 ref/extract.py                       # design -> ref/<screen>.inventory.json
python3 ref/check.py factory.pen ref/*.json  # every structural identity is drawn, or exit 1
```

`extract.py` reads the design's own `const` blocks — the sets are the DESIGN's, never the drawing
agent's, which is what keeps the check able to fail. Sets are marked **structural** (nav, stages,
filters, entry kinds — every identity must be drawn) or **sample** (ticket titles and lane names a
design illustrates rather than enumerates); asserting the sample sets reported 34 misses that were
not misses, and a gate that cries wolf is a gate nobody runs.

The canonical list of frames and their element sets. **This is the inventory before the
inventory**: `docs/design/ref/<surface>.inventory.json` is transcribed from the `.pen` frames named
here, and `check-inventory` compares those sets against what the client source actually renders.

Written 2026-09-02, when the design moved from `.dc.html` to `.pen` so that an implementation could
be checked for **completeness** and not only for colour. The format it replaced carried exactly one
enumerable name in 86 KB (`viewport`); nothing could ask *"is every component built?"*, which is why
components went missing and stayed missing.

## The rule this design exists to serve

> **Design first, then implementation.** A `factory-client` ticket's first commit touches
> `client/web/design/`, and the ticket cites the frame it implements.

A screen implemented first has no specification to be checked against — the code becomes its own
criterion, which is the failure mode this repository already keeps a ledger about.

## Frame naming — load-bearing

Every top-level frame is named exactly as the table below spells it. `check-inventory` resolves a
spec to a frame **by name**, so a rename is a broken gate, and a frame that is not in this table is
not in the design. One frame per surface; sub-frames carry the component name.

## The frames

### A · Shell and the eight destinations

The nav the board draws — `Dashboard · Tickets · Epics · Releases · Lanes · Runs · Escapes · Seams`.

| frame | spec | notes |
|---|---|---|
| `Shell · Top bar` | §4.1 | brand · breadcrumb · repository · VOCABULARY · socket chip · needs-you chip and its `alt N` key hint · theme toggle |
| `Shell · Nav` | §4.18 | the eight destinations, each with its count |

### B · The board (`Dashboard`)

| frame | spec | element set the inventory will assert |
|---|---|---|
| `Dashboard · Pipeline strip` | §4.18 | the stage cells, in order, each with its count and its tint |
| `Dashboard · Lanes` | §4.20, §4.4 | one card per lane: name · state · role tag · ticket ref · title · state line · elapsed · cost |
| `Dashboard · Needs you` | §4.18 | one row per reason, each with its **action** — the actions are the feature, so each is a named element |
| `Dashboard · Tickets by epic` | §4.17 | stage filter chips · provenance filter chips · search · the table's columns |
| `Dashboard · Empty` | §4.18 | what the board says when the repository has no work — **and the one
destination that exists from it**, because the panel carrying `all requirements →` is the panel this
state replaces |

#### The SEARCH row asks once, when the typing has settled

The `SEARCH` row in `Dashboard · Tickets by epic` draws a **settled** query — `329`, and the
result line `1 of 340` beside it. It draws no intermediate state because there is none to draw:
the field asks the server for the query a person **stopped typing**, not for each character on
the way to it.

That is a design statement and not an implementation detail, because it decides what the row is
for. A field that asks per keystroke is a field whose broadest, least wanted queries are the ones
it always runs — `q=3` and `q=32` on the way to `329`, each a full sweep of a catalog that has no
index by design — and a query a person deletes before finishing is a query the server is asked
anyway, whose answer arrives after the screen has moved on.

So the row has one temporal rule, and the rest of the design follows from it:

> **The field asks the server once the value has stood still for 300 ms, and never otherwise.**
> No prefix on the way to a settled query is asked. A query abandoned inside that window is
> never asked at all. Nothing waits for a blur or an `Enter` — the answer arrives on its own.

**300 ms is the design's number, and the field draws no spinner for it.** The previous answer
stays on screen while the new one is in flight, as it does for every other filter on this
surface; §4's preamble is "never a layout that moves when data lands", and a placeholder drawn
for a third of a second is that layout moving twice.

**This is a timer, and this client does not poll.** The two are separated by what provokes them,
never by a name on an allow-list: a poll fires because time passed, a debounce fires because a
person typed and then stopped. A list of permitted timers is where the next poll is added, so
`no-poll.test.tsx` asserts the tickets surface — the one surface that now owns a timer — sits
mounted and untouched for ten minutes and fetches nothing.

### C · The detail (`Ticket`)

| frame | spec |
|---|---|
| `Ticket · Header` | §4.5 — state chips, the stage strip |
| `Ticket · Tabs` | §4.6–4.10 — the tab row as data: SESSION · TICKET.MD · REPORT.MD · RUNS · DIFF |
| `Ticket · Session` | §4.6 — the transcript entry kinds |
| `Ticket · Composer` | §4.7 |
| `Ticket · Document` | §4.8, §4.9 — read-only, with its path |
| `Ticket · Runs` | §4.10 — STARTED · STAGE · DUR · COST |
| `Ticket · Permission request` | §4.12 — the held rectangle, the rule above the buttons |
| `Ticket · Empty` | §4.6 — "nothing has run on this ticket yet" |

### D · The rest of the eight

| frame | spec |
|---|---|
| `Requirements · List` | §4.2 |
| `Requirements · Detail` | §4.3 |
| `Wiki` | §4.15 |
| `Escapes` | §4.16 |
| `Runs` | §4.19 |
| `Lanes · Table` | §4.20 |
| `Seams` | — **kept**, and drawn: MODEL.md's six invariants, each showing how many tickets cite it. §4 owes it a section |
| `Vocabulary sheet` | §4.11 — every state, its word and its colour, in one place |
| `Metrics` | §4.21 — every field `E32` serves, each beside the collection it was computed over; the window control; and the empty state, in one frame |

**`Releases` was removed.** It was in the delivered design's nav and is Acme's concept: this
repository has one developer and a passed ticket merges straight to `main`, so there is no release
train for a surface to draw. A destination that leads nowhere is worse than one that is missing.

**`Metrics` draws two named states in one frame, and says which is which.** The tiles are the
populated state; the block at the foot is captioned `EMPTY · runs = 0` and carries the sentence
that replaces every number above it. A frame drawing an empty state beside numbers with no caption
would be `WEB-FIX-336` again — two halves, each correct, forming a state the surface cannot be in.

**Two tiles draw `n —`, and that is the design, not an omission.** `E32` serves no count for the
collection `rounds_per_ticket` and `rework_after_build.share` are reduced over, so the frame draws
a dash where the population would be and says so in the derivation line. A surface that invented
one would be drawing a collection nobody served it — the `WEB-FEAT-001` half of *the collection
asserted must be the one the copy NAMES*.

**`Seams` was kept and drawn.** `docs/technical/MODEL.md` defines S1–S6 and every ticket cites the seam it may not
break; nothing else in the tool shows which invariant is under pressure. It still owes a `docs/product/SPEC.md`
§4 section.

## Every frame owes an empty state

A surface without one is a surface that has never been seen on a fresh repository.
`WEB-FIX-088` drew *"Nothing has run on this ticket yet."* on a ticket with two runs, because the
empty state was asserted as a string rather than against the collection. The design draws both.

## Tokens are variables in the `.pen`

Corrected 2026-09-02. An earlier draft of this file said `.pen` had no named-variable layer and that
the tokens had to stay in CSS. That was wrong: Pencil's `SetVariables` carries named variables with
first-class theming, so one name holds its light and dark value together —

```js
"st-done": {type:"color", value:[{value:"#006f75",theme:{mode:"light"}},
                                 {value:"#26acb2",theme:{mode:"dark"}}]}
```

`sumavmaster.pen` has 94 anonymous fills because it never used variables, not because the format
cannot. So the design is the single token source, and `state-tones.test.ts` keeps working — it asks
whether two STATES are distinguishable from each other, which needs names, and the names are here.

`gen-tokens.mjs` still reads the CSS. Until it reads the `.pen` instead, the two must not drift, and
the case that asserts they do not is owed by the ticket that moves the generator.

That test asks a question no pixel diff can: whether two states are *distinguishable from each
other*, in OKLab, threshold 0.05. It caught `--st-done` collapsing into `--t3`. A design format with
94 anonymous fills cannot be asked that question, so the names live where names work.

**Every fill in the `.pen` must be one of those token values.** The gate that enforces it is the
next thing to build after the inventory: a check that no frame carries a colour the token set does
not define.
## Agent/model selection — WEB-FEAT-412

`Ticket · Empty` draws the configurable, unavailable, launch-refused, and resumed states. Before
a lane starts, show labelled Agent and Model selects immediately above the start action. Choices
come only from the manifest. Changing Agent resets Model to that profile's default. Both controls
are disabled during startup; invalid or missing choices disable startup. Keep the choice and draft
after a refusal. The same selection is shared by the rail, composer, and QA start action for a
ticket. The rail and composer are two seats for the same no-lane claim path: never show both copies
on the same ticket page. The SESSION tab owns the control while visible; on every other tab the
rail owns it. A resumed session hides the controls because its provider cannot change. A legacy manifest
retains its existing single-agent start behaviour. Use existing form controls and palette tokens;
never place credentials in the UI.
