# factory/client/web — the browser half

React + TypeScript + Vite. Serves **:7779** and proxies `/api` and `/ws` to the server on :7777, so
the browser sees one origin and CORS never applies.

```bash
npm run dev                # :7779, proxying to the server
npm run build              # tsc -b && vite build — type errors ARE build failures
npm run test               # vitest
npm run gate               # THE GATE — Chrome headless against the real C++ server (VERIFY)
npm run gate -- --surfaces tickets,ticket-md --themes dark    # scoped — GATE, tier 2b
npm run gen:types          # ../../docs/technical/contracts/socket.schema.json → src/types/socket.ts
npm run gen:api-types      # ../../docs/technical/contracts/http.schema.json   → src/types/api.ts
npm run gen:tokens         # design/nocturne.css + design/tokens.json → src/styles/tokens.css
design/import.sh <zip>     # pull a Claude Design export in, and gate the palette on arrival
```

Ticket prefix: **`WEB-*`**. Tickets and reports live in the repository root's `docs/` — a
requirement's children must sit in the same repository as the requirement.

## Nothing generated is ever hand-written

`src/types/socket.ts`, `src/types/api.ts` and `src/styles/tokens.css` are outputs. Editing one is
caught by its own test, and the three sources sit outside this workspace on purpose:

| source | generates | asserted by |
|---|---|---|
| `../../docs/technical/contracts/socket.schema.json` | `src/types/socket.ts` | `socket.test.ts` |
| `../../docs/technical/contracts/http.schema.json` | `src/types/api.ts` | `api-schema.test.ts`, and the browser gate against the LIVE server |
| `design/tokens.json` + `design/nocturne.css` | `src/styles/tokens.css` | `theme.test.tsx` byte-equality, both themes |

## Design first, then implementation

**A change to a surface starts with a change to `design/`.** A screen implemented first has no
specification to be checked against — the code becomes its own criterion. A `WEB-*` ticket cites
the frame it implements and its first commit touches `design/`.

`design/BRIEF.md` enumerates the frames the design owes. `design/README.md` has the loop and why
the repository, not the canvas, is the source of truth.

**The canvas is not the file, and the DESIGN stage ends at the file.** The pen.dev editor holds the
document in a buffer and does not always write `factory.pen`; `Export` and every MCP read answer
about that buffer, so a PNG can show a row the repository does not have. `WEB-FEAT-332` drew a row,
exported it, judged it, implemented against it and shipped — `git diff -- design/` was empty
(`WEB-FIX-334`).

    git status --porcelain client/web/design/     # after every .pen edit, before claiming DESIGN

If the tool has not written the file, the editor's buffer is at
`~/.config/Code/User/workspaceStorage/*/highagency.pencildev/` — copy it over `factory.pen` and
check it is the only frame that differs. Then **assert the design off disk**, as
`design-search-row.test.ts` does: parse `factory.pen` as JSON and read the frame. A screenshot of a
design is not the design.

**And the criterion is about the FRAME, not the layer you added.** A check scoped to the row a
ticket drew passes on a frame that contradicts itself one line above it: `WEB-FIX-334` fixed the
backlog size in the copy it wrote and left a different one in the header pill, which
`TicketsByEpic.tsx:44` draws from the MATCHED count and can never hold (`WEB-FIX-336`). Assert the
frame as a state — its numbers agree with each other, and the real component, driven with the
frame's own numbers, draws the frame's own copy.

**No colour may be written anywhere but `design/`.** `state-tones.test.ts` asks the question no
pixel diff can — whether two states are distinguishable *from each other*, in OKLab, threshold
0.05. It caught `--st-done` collapsing into `--t3`.

## The client never polls

HTTP loads a surface; `catalog.changed` reloads exactly the document it names. `no-poll.test.tsx`
fails on a timer-driven fetch or render.

## `npm run gate` is the real gate, not vitest

It builds the bundle, **starts the compiled C++ server** (`../../.cache/server/build/factory-server`)
against `gate/fixture-repo`, serves the build through `vite preview`, and drives Chrome headless:
rows, groups, markdown, the socket chip, the schema, contrast, and a screenshot of every surface in
both themes. A green vitest run is not a screen anybody can read.

**And a green gate is still not the human pass.** It drives `gate/fixture-repo`, a synthetic
repository — which is what `WEB-FIX-248` and `WEB-FIX-261` are about. Tier 3 is opening
http://localhost:7779 and running the ticket from the screen.

**It takes a scope, and which one you give it says which stage you are in** (`WEB-FEAT-379`).
`--surfaces a,b --themes dark` — or `FACTORY_GATE_SURFACES` / `FACTORY_GATE_THEMES`, with the
argument winning — sweeps those surfaces in that theme and skips the rest by name; unscoped it
sweeps every declared surface in both themes, which is ten to twelve minutes and belongs to VERIFY.
The names are the first column of `gate/surfaces.mjs`'s `SURFACES` and `LIVE_SURFACES`; an unknown
one exits 2 before any phase runs. **A check the scope left out is printed and counted —
`skipped(scope): n` — never passed**, because a way to run less than everything is *"don't run the
browser gate"* with a nicer interface, and that instruction cost this repository four escaped
defects on 2026-07-29. The inventory, the vitest suite and the build are never scoped, and every
run ends with what each phase cost.

## Layout

```
design/    the .pen / .dc.html surfaces, the tokens, BRIEF.md, import.sh
src/       services (one http client, one socket client — nothing else talks to the wire)
           pages · components · types (generated) · styles
tests/     vitest
gate/      run-gate.sh · browser-gate.mjs · fixture-repo · baselines · shots
scripts/   the three generators
```
