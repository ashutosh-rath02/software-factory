import { pathToFileURL } from 'node:url'

/**
 * WHICH SURFACES THE BROWSER GATE OPENS, and which destinations it does not reach yet.
 *
 * It lives in its own module for the reason `sweep.mjs` does: so `tests/gate-surfaces.test.ts` can
 * assert on the set this gate ACTUALLY sweeps rather than on the gate's summary line. A hand-written
 * list nobody checks is `_TEMPLATE.md` §2's first way a check lies, and `WEB-FIX-337` is what it
 * cost — `#/tickets` shipped a SEARCH row that no photograph in either theme has ever shown,
 * because a surface absent from this file is not a red row anywhere. It is silence.
 */

/**
 * The query `#/tickets` is photographed under (WEB-FIX-337).
 *
 * `hash` is a PROPER, NON-EMPTY subset of `gate/fixture-repo`: it matches TKT-133, TKT-139 and
 * TKT-147 — three of nine, under two different requirements — so the matched count and the backlog
 * are forced apart in the frame and the grouping is in it. A query matching everything would leave
 * the two numbers on the result line as one number, which is a screen that cannot be wrong.
 */
export const TICKETS_QUERY = 'hash'

/**
 * `#/tickets` with a query live — the state the SEARCH row exists for (WEB-FIX-337).
 *
 * `TicketsByEpic` draws the result line only while the box has something in it, so a shot of the
 * resting surface is a shot of the one state this row does not have. The wait is on the line
 * appearing rather than on a length of time: `useEndpoint` keys on the query, so the previous
 * answer cannot be on screen under a new one and the line's presence IS the query having landed.
 */
export async function withTheSearchBoxFilled(page) {
  // No `.catch(() => {})` here, deliberately, and it is the one place in this file that wants a
  // throw: a wait that gives up quietly leaves the sweep photographing the RESTING surface and
  // calling it prepared, and section 11 — which asks only whether a file was written — would pass
  // over it. A run that cannot put the query in the box has to end loudly rather than produce a
  // picture of the state this surface is not being photographed for.
  await page.waitForSelector('[data-testid="tickets-search"]', { timeout: 10000 })
  await page.fill('[data-testid="tickets-search"]', TICKETS_QUERY)
  await page.waitForSelector('[data-testid="search-result"]', { timeout: 10000 })
}

/**
 * `#/wiki` with a page open — the state the surface exists for (WEB-FIX-344).
 *
 * `WikiPage` draws an index of the repository's pages always and the PAGE ITSELF only after a row
 * is clicked, so a shot of the resting surface is a shot of the one state a wiki is not for. Same
 * shape and same reason as `withTheSearchBoxFilled`, including the deliberate absence of a
 * `.catch(() => {})`: a run that cannot open a page has to end loudly rather than photograph the
 * index and call the surface covered.
 */
export async function withAPageOpen(page) {
  await page.waitForSelector('.wiki-row', { timeout: 10000 })
  await page.click('.wiki-row')
  await page.waitForSelector('[data-testid="wiki-open"]', { timeout: 10000 })
}

/**
 * `#/metrics` over the window the fixture's history is actually in (WEB-FEAT-398).
 *
 * `E32` reduces the run records under `runs_dir` that started inside the window, and
 * `gate/fixture-repo` carries two, dated where they were written. The default window is 30 days
 * and a scoped run does not reach the sections that start a run, so the resting surface here is
 * the one state §4.21 exists to REPLACE — and a photograph of an empty state is what
 * `gate-surfaces.test.ts` refuses as a picture of a destination.
 *
 * So the widest window the control offers is the one this is photographed under. It also drives
 * the control itself, which is half of what the surface is. When the fixture's history finally
 * ages past 90 days the shot shows the empty state and the destination guard goes RED naming it,
 * which is the failure anybody wants: loud, not quiet.
 */
export async function withTheWidestWindow(page) {
  // No `.catch(() => {})`, for `withTheSearchBoxFilled`'s reason: a run that cannot widen the
  // window must end loudly rather than photograph the resting surface and call it prepared.
  await page.waitForSelector('[data-testid="metrics-window-90"]', { timeout: 10000 })
  await page.click('[data-testid="metrics-window-90"]')
  await page.waitForSelector('[data-testid="metric-runs"]', { timeout: 10000 })
}

/**
 * `[name, route]`, and optionally a third element: what to do to the surface after the navigation
 * and before the photograph. A row that needs no preparation stays two elements long.
 */
export const SURFACES = [
  // §4.18 (WEB-FEAT-001). It is the bare hash and it is what the client opens on, so it is first
  // here for the same reason it is first there.
  ['home', '#/'],
  ['shell-and-requirements', '#/requirements'],
  ['requirement-detail', '#/requirements/REQ-014'],
  // the same surface for the group no document backs (WEB-FEAT-041)
  ['requirement-standalone', '#/requirements/standalone'],
  // §4.17 (WEB-FEAT-332, WEB-FIX-337). The rail's `Tickets` destination, which this sweep opened
  // only as `#/tickets/<id>/…` — the ticket DETAIL routes — for its whole life. The whole filters
  // block (SEARCH, PROVENANCE, STAGE), the epic groups and the pager had no picture in either
  // theme, so neither this gate nor `baselines.test.ts` could say a word about any of them.
  ['tickets', '#/tickets', withTheSearchBoxFilled],
  // The tab is named from WEB-FEAT-014 onwards: SESSION is the registry's first row, so a hash
  // with no tab now opens the transcript. The screenshot of TICKET.MD has to ask for it.
  ['ticket-session', '#/tickets/TKT-142/session'],
  ['ticket-md', '#/tickets/TKT-142/ticket'],
  ['report-md-present', '#/tickets/TKT-139/report'],
  ['report-md-unproven', '#/tickets/TKT-128/report'],
  ['report-md-absent', '#/tickets/TKT-142/report'],
  ['runs', '#/tickets/TKT-139/runs'],
  ['diff', '#/tickets/TKT-142/diff'],
  ['vocabulary', '#/vocabulary'],
  ['permissions', '#/permissions'],
  // §4.14 (WEB-FEAT-050). It is populated by the lanes section 8 claims, and
  // section 9 runs after that — a shot of an empty one would prove nothing.
  ['all-lanes', '#/lanes'],

  // ── WEB-FIX-344 · the six the rail offered and no gate run ever opened ────────────────────
  //
  // Every one of them was a live link a person could click, with no picture in either theme, and
  // therefore outside the contrast sweep and outside `baselines.test.ts` as well. Two of them —
  // `#/wiki` and `#/escapes` — could not even be ASKED about: `gate/fixture-repo` declared no
  // `docs.wiki` and no `docs.escapes`, so section 6b was red on `main` over `E27` and `E28` for
  // as long as those nodes have existed. The fixture declares both now and holds real pages
  // behind them, which is what makes these shots pictures of a surface rather than of a refusal.
  ['delivery', '#/delivery'],
  // `all-runs`, NOT `runs`: `runs` above is `#/tickets/TKT-139/runs`, a TAB on one ticket, and
  // `RunsPage` is a different component on a different address. One word shared by a shot name
  // and a component is the 2026-08-26 row in `docs/operations/ESCAPES.md`, not coverage.
  ['all-runs', '#/runs'],
  ['wiki', '#/wiki', withAPageOpen],
  ['escapes', '#/escapes'],
  // §4.21 (WEB-FEAT-398). `E32` is computed on request from the run records the fixture holds,
  // so what this photographs is whatever the sections above left behind — which is the point:
  // the numbers on this screen are a reduction over the gate's own history, not over a file.
  ['metrics', '#/metrics', withTheWidestWindow],
  ['seams', '#/seams'],
  ['orchard', '#/orchard'],
]

/**
 * The surfaces photographed a SECOND time while a lane is actually running (WEB-FEAT-083).
 *
 * Section 10 claims one lane over `E12`, starts a real run in it, and re-photographs these while it
 * is in flight — the process axis, photographed while it is true.
 */
export const LIVE_SURFACES = [
  // §4.18 (WEB-FEAT-001 QA F7). The dashboard has a live state — a lane running moves the
  // strip's `running` cell and the `needs you` panel — so a static-only shot of it is a
  // photograph of the one state that needed no watching.
  ['home-live', '#/'],
  ['requirements-live', '#/requirements'],
  ['requirement-detail-live', '#/requirements/REQ-014'],
  ['ticket-live', '#/tickets/TKT-142/session'],
  ['lane-rail-live', '#/lanes'],
]

/**
 * The rail's destinations this sweep does NOT open, each naming the ticket that owes it.
 *
 * `const LATER = new Set([…])` turned inside out, the same way `sweep.mjs` turned the schema
 * sweep's deferrals inside out: a set kept privately is a set nobody can test and one that grows by
 * one line. `tests/gate-surfaces.test.ts` derives the rail's destinations from `SideNav.tsx` and
 * requires every one of them to be either swept above or written here against a real ticket id — a
 * ticket that EXISTS on disk since WEB-FIX-344, because a debt owed to `WEB-FIX-999` is the set
 * kept privately with more syllables. So a destination that quietly stops being photographed is
 * red, and a debt cannot be paid by deleting its row.
 *
 * EMPTY, and it stays a declaration rather than being deleted: the rule is what makes the next
 * destination added to the rail a red row instead of silence, and an empty debt is the state this
 * repository wants to be able to see it is in.
 */
/** @type {Record<string, string>} */
export const NOT_PHOTOGRAPHED_YET = {}

/**
 * THE TWO THEMES THIS CLIENT SHIPS, AND THE ONE PLACE THE PAIR IS WRITTEN (WEB-FEAT-379).
 *
 * `browser-gate.mjs` held `['dark', 'light']` as an array literal in nine places, so "the gate
 * sweeps both themes" was nine independent facts that happened to agree. A tenth could be added
 * without going through anything, and a run could not be asked for one theme at all.
 *
 * `tests/gate-scope.test.ts` asserts the count of those literals in `browser-gate.mjs` is ZERO,
 * which is what makes *one source* a fact rather than a convention: at nine it was a convention,
 * and a convention is how the pair came to be written in nine places.
 */
export const THEMES = ['dark', 'light']

/**
 * One axis of a scope — `null` when nobody named it, otherwise the declared names it selects.
 *
 * A FLAG present with nothing usable after it is refused, and an EMPTY ENVIRONMENT VARIABLE is
 * absent. They are different acts: `--surfaces ''` is somebody asking for a scope of nothing —
 * a browser gate that photographs nothing and exits 0 — while `FACTORY_GATE_SURFACES` unset or
 * empty is the ordinary state of a shell.
 *
 * The result is in DECLARED order, never in the order the names were typed, so the sweep a scope
 * produces does not depend on how the flag was written.
 *
 * @param {string} what
 * @param {string} flag
 * @param {string} envKey
 * @param {string[]} declared
 * @param {string[]} argv
 * @param {Record<string, string | undefined>} env
 * @returns {string[] | null}
 */
function axis(what, flag, envKey, declared, argv, env) {
  let raw = null
  const at = argv.lastIndexOf(flag)
  if (at !== -1) {
    const next = argv[at + 1]
    raw = typeof next === 'string' && !next.startsWith('--') ? next : ''
  } else if (typeof env[envKey] === 'string' && env[envKey].trim() !== '') {
    raw = env[envKey]
  }
  if (raw === null) return null

  const asked = [...new Set(raw.split(',').map((s) => s.trim()).filter(Boolean))]
  if (asked.length === 0) {
    throw new Error(`${flag} named no ${what} — declared: ${declared.join(' · ')}`)
  }
  for (const name of asked) {
    if (!declared.includes(name)) {
      throw new Error(`unknown ${what} "${name}" — declared: ${declared.join(' · ')}`)
    }
  }
  return declared.filter((n) => asked.includes(n))
}

/**
 * WHAT THIS GATE RUN PHOTOGRAPHS (WEB-FEAT-379).
 *
 *   node gate/surfaces.mjs --surfaces tickets,ticket-md --themes dark
 *   FACTORY_GATE_SURFACES=tickets,ticket-md FACTORY_GATE_THEMES=dark
 *
 * Two axes, independent, and **the argument wins over the environment on its own axis**. An axis
 * nobody named is the whole declared set for that axis, so `resolveScope({})` is the run this gate
 * has always done and `inScope()` answers true to every name — including the eight surfaces
 * `browser-gate.mjs` photographs that are in neither table below. That last clause is the whole of
 * *unchanged*: a resolver answering `false` to a name it has never heard of would quietly stop
 * photographing them on a run nobody scoped.
 *
 * The names are the first column of `SURFACES` and `LIVE_SURFACES`, read off those tables rather
 * than listed here — `_TEMPLATE.md` §2, the first way a check lies. An unknown one is refused by
 * name with every declared name printed beside it, because `WEB-FIX-337` is what a surface the
 * sweep silently does not reach costs: it is not a red row anywhere, it is silence, and a scope is
 * a list with exactly that hazard.
 *
 * The ledger — `skip()`, `skipped`, `skipLine()` — is here rather than in the gate for the reason
 * `docs/operations/ESCAPES.md` gives on 2026-07-29: five client lanes were told not to run the browser gate,
 * all five merged on a green vitest run, and four gate assertions reached `main` having never once
 * executed. A way to run less than everything is that instruction with a nicer interface unless a
 * check that did not run is visibly a check that did not run.
 *
 * @param {{ argv?: string[], env?: Record<string, string | undefined> }} [given]
 */
export function resolveScope({ argv = [], env = {} } = {}) {
  const declaredSurfaces = [...SURFACES, ...LIVE_SURFACES].map(([name]) => String(name))
  const declaredThemes = [...THEMES]

  const surfaces = axis('surface', '--surfaces', 'FACTORY_GATE_SURFACES',
                        declaredSurfaces, argv, env)
  const themes = axis('theme', '--themes', 'FACTORY_GATE_THEMES', declaredThemes, argv, env)

  const chosenSurfaces = surfaces ?? declaredSurfaces
  const chosenThemes = themes ?? declaredThemes
  const surfacesScoped = surfaces !== null
  /** @type {{ name: string, surface: string }[]} */
  const skipped = []

  return {
    surfaces: chosenSurfaces,
    themes: chosenThemes,
    surfacesScoped,
    themesScoped: themes !== null,
    declared: { surfaces: declaredSurfaces, themes: declaredThemes },
    skipped,
    inScope: (/** @type {string} */ name) =>
      !surfacesScoped || chosenSurfaces.includes(name),
    skip: (/** @type {string} */ name, /** @type {string} */ surface) => {
      skipped.push({ name, surface })
    },
    skipLine: () => `skipped(scope): ${skipped.length}`,
    line: () => {
      const s = chosenSurfaces.length
      const t = chosenThemes.length
      const named = surfacesScoped ? chosenSurfaces.join(' · ') : 'every declared surface'
      return `scope: ${s} surface${s === 1 ? '' : 's'} × ${t} theme${t === 1 ? '' : 's'}`
        + ` (of ${declaredSurfaces.length} × ${declaredThemes.length})`
        + ` — ${named} · ${chosenThemes.join(' · ')}`
    },
  }
}

/**
 * Run directly, this module IS the scope check `run-gate.sh` makes before it takes the browser
 * lock: one JSON line on stdout and exit 0, or the refusal on stderr and **exit 2**.
 *
 * The script and the sweep therefore resolve a scope through the same function rather than through
 * two agreeing implementations of *the argument wins over the environment* — which is the shape
 * `_TEMPLATE.md` §2 calls a second source of truth waiting to disagree.
 */
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const scope = resolveScope({ argv: process.argv, env: process.env })
    process.stdout.write(`${JSON.stringify({
      surfaces: scope.surfaces, themes: scope.themes, line: scope.line(),
    })}\n`)
  } catch (e) {
    process.stderr.write(`${e instanceof Error ? e.message : String(e)}\n`)
    process.exit(2)
  }
}
