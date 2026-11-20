import { afterEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'

import { callsTo, mountApp, settle } from './helpers'

/**
 * WEB-FEAT-398 — §4.21, the Metrics surface.
 *
 * `SRV-FEAT-501` serves `E32`; until this ticket nothing drew it. Three rules from `_TEMPLATE.md`
 * §2 shape every case below, and each is why the case is written the way it is rather than the
 * shorter way:
 *
 * 1. **The design leads and it is ON DISK.** `design-search-row.test.ts` and
 *    `design-lane-words.test.ts` read `client/web/design/factory.pen` as JSON because
 *    `WEB-FIX-334` found a round that had changed the CANVAS and not the file. So does this.
 * 2. **A design criterion is about the FRAME, not the layer the ticket added** (`WEB-FIX-336`).
 *    The frame is asserted as a STATE — its numbers agree with each other — and then the REAL
 *    component is driven with the frame's own numbers and required to render the frame's own
 *    copy.
 * 3. **A claim about a collection is asserted in the same case as the collection**
 *    (`WEB-FIX-091`, `WEB-FIX-088`). The empty-state SENTENCE and `runs` are one expression, and
 *    the rate is asserted against the count of the population it is a share OF.
 *
 * And the set of numbers this surface owes is the CONTRACT's, read out of `docs/technical/contracts/http.schema.json`
 * — never a list typed here. A tuple of eight names agrees with whatever its author remembered,
 * which is `_TEMPLATE.md` §2's first way a check lies; a field added to `E32` that this surface
 * does not draw has to be red here.
 */

const CLIENT = join(__dirname, '..')
const DESIGN = join(CLIENT, 'design', 'factory.pen')
const SCHEMA = join(CLIENT, '..', '..', 'docs', 'technical/contracts/http.schema.json')
const METRICS_FRAME = 'Kj8y5' // `Metrics`

type Node = { id?: string; name?: string; type?: string; content?: string; fill?: string
              children?: Node[] }

const parse = (source: string) => JSON.parse(source) as { children: Node[] }
const frame = (doc: { children: Node[] }, id: string) => doc.children.find((c) => c.id === id)
const nodes = (n: Node | undefined): Node[] =>
  n === undefined ? [] : [n, ...(n.children ?? []).flatMap(nodes)]

function copy(n: Node | undefined): string[] {
  if (!n) return []
  const here = n.type === 'text' && n.content ? [n.content] : []
  return here.concat(...(n.children ?? []).map(copy))
}

/** Every field `E32` is contracted to serve — the schema's own `required`, not a list typed here. */
function e32Fields(): string[] {
  const schema = JSON.parse(readFileSync(SCHEMA, 'utf8'))
  return schema.responses.E32.required as string[]
}

/**
 * The INVERSE of the surface's formatter, written from §4.21's words rather than imported.
 *
 * `_TEMPLATE.md` §2: a test that reuses the code's own helper is testing the helper. So this
 * reads the string the DESIGN draws and says what number `E32` must have carried for the
 * surface to have drawn it — `75%` is `0.75`, `38.5m` is `38.5`, `6.4h` is `6.4`, `—` is
 * nothing measured. If the component formatted `0.75` as `0.75`, every assertion below goes red.
 */
function wireOf(drawn: string): number | null {
  const text = drawn.trim()
  if (text === '—') return null
  const percent = /^(\d+(?:\.\d+)?)%$/.exec(text)
  if (percent) return Number(percent[1]) / 100
  const measure = /^(\d+(?:\.\d+)?)[mh]$/.exec(text)
  if (measure) return Number(measure[1])
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text)
  throw new Error(`the frame draws "${drawn}", which is not a value any §4.21 tile can carry`)
}

/** `n 24` → 24 · `n —` → null, the population `E32` served for that tile, or none. */
function nOf(drawn: string): number | null {
  const text = drawn.trim()
  if (text === 'n —') return null
  const m = /^n (\d+)$/.exec(text)
  if (!m) throw new Error(`"${drawn}" is not a population line`)
  return Number(m[1])
}

interface Tile { key: string; value: string; n: string; second?: string; note: string }

/**
 * The frame's tiles, located by the WIRE NAME each one draws as its heading.
 *
 * By the heading and not by the layer name: the heading is on the screen, a layer name is not,
 * and `WEB-FIX-336` asserted a row that existed beside a frame that contradicted it.
 */
function tilesOf(source: string): Map<string, Tile> {
  const metrics = frame(parse(source), METRICS_FRAME)
  const out = new Map<string, Tile>()
  for (const node of nodes(metrics)) {
    if (node.type !== 'frame') continue
    const texts = (node.children ?? []).filter((c) => c.type === 'text')
    if (texts.length < 3) continue
    const head = (texts[0].content ?? '').trim()
    if (!/^[A-Z_]+$/.test(head)) continue
    const rest = texts.slice(1).map((t) => (t.content ?? '').trim())
    const n = rest.find((t) => t.startsWith('n '))
    if (n === undefined) continue
    out.set(head.toLowerCase(), {
      key: head.toLowerCase(),
      value: rest[0],
      // [value, n, note] is a tile with no second line; [value, second, n, note] has one.
      second: rest.length > 3 ? rest[1] : undefined,
      n,
      note: rest[rest.length - 1],
    })
  }
  return out
}

/** The `stage_minutes` table the frame draws: one row per stage, with its own `n`. */
function stageRowsOf(source: string): { name: string; median: string; mean: string; n: string }[] {
  const metrics = frame(parse(source), METRICS_FRAME)
  // Located by its COLUMN HEADINGS rather than by a layer name or an index: the table is the one
  // frame whose rows are `STAGE · MEDIAN · MEAN · N`, so a renamed layer or a row inserted above
  // it yields nothing and the sweep-is-non-empty assertion goes red rather than reading the
  // window control as a stage.
  const HEADINGS = 'STAGE|MEDIAN|MEAN|N'
  const cellsOf = (n: Node | undefined) =>
    ((n?.children ?? []).filter((c) => c.type === 'text')).map((c) => (c.content ?? '').trim())
  const table = nodes(metrics).find(
    (n) => n.type === 'frame'
      && (n.children ?? []).some((c) => cellsOf(c).join('|') === HEADINGS))
  return (table?.children ?? [])
    .filter((r) => cellsOf(r).join('|') !== HEADINGS)
    .map((r) => {
      const cells = cellsOf(r)
      return { name: cells[0], median: cells[1], mean: cells[2], n: cells[3] }
    })
}

/** The window control the frame draws: every offered window, and the one that is selected. */
function windowOf(source: string) {
  const metrics = frame(parse(source), METRICS_FRAME)
  const row = nodes(metrics).find(
    (n) => n.type === 'frame' && (n.children ?? []).some(
      (c) => c.type === 'text' && c.content?.trim() === 'WINDOW'))
  const chips = (row?.children ?? []).filter((c) => c.type === 'frame')
  const days = (c: Node) => Number(/^(\d+) days$/.exec(copy(c)[0]?.trim() ?? '')?.[1] ?? NaN)
  // `$c-raise` is the token the design paints the chosen control with — the same one the
  // `STATUS` row's `all` chip carries. The selection is a FILL in the frame and `.is-on` in the
  // client, so neither reads the other's spelling.
  return {
    offered: chips.map(days),
    selected: chips.filter((c) => c.fill === '$c-raise').map(days),
  }
}

/** The head's own statement of the window every tile below it was taken over. */
function headWindowOf(source: string): number {
  const metrics = frame(parse(source), METRICS_FRAME)
  const head = (metrics?.children ?? []).find(
    (c) => copy(c).some((t) => t.trim() === 'Metrics'))
  const said = copy(head).map((t) => t.trim()).find((t) => /^\d+ days$/.test(t)) ?? ''
  return Number(/^(\d+) days$/.exec(said)?.[1] ?? NaN)
}

/** The frame's empty state: the caption naming the condition, and the sentence it draws. */
function emptyOf(source: string) {
  const metrics = frame(parse(source), METRICS_FRAME)
  const block = nodes(metrics).find(
    (n) => n.type === 'frame' && (n.children ?? []).some(
      (c) => c.type === 'text' && (c.content ?? '').trim().startsWith('EMPTY')))
  const lines = copy(block).map((t) => t.trim())
  return { caption: lines[0] ?? '', sentence: lines[1] ?? '' }
}

/**
 * `E32`'s body, built from what the FRAME draws — the fixture is the design's, not this file's.
 *
 * This is the half of `WEB-FIX-336` that makes the design a specification rather than a picture:
 * a frame whose numbers no response can carry cannot produce a fixture, and a component that
 * renders something else fails against the frame's own copy.
 */
function responseFromTheFrame(source: string) {
  const tiles = tilesOf(source)
  const tile = (key: string) => {
    const found = tiles.get(key)
    if (!found) throw new Error(`frame ${METRICS_FRAME} draws no ${key.toUpperCase()} tile`)
    return found
  }
  const rounds = tile('rounds_per_ticket')
  const active = tile('active_minutes')
  const rework = tile('rework_after_build')
  return {
    window_days: headWindowOf(source),
    runs: wireOf(tile('runs').value),
    tickets_with_verdict: wireOf(tile('tickets_with_verdict').value),
    first_pass_qa_rate: wireOf(tile('first_pass_qa_rate').value),
    rounds_per_ticket: {
      median: wireOf(rounds.value),
      mean: wireOf((rounds.second ?? '').replace(/^mean /, '')),
    },
    stage_minutes: stageRowsOf(source).map((r) => ({
      name: r.name, median: wireOf(r.median), mean: wireOf(r.mean), n: Number(r.n),
    })),
    active_minutes: {
      median: wireOf(active.value),
      mean: wireOf((active.second ?? '').replace(/^mean /, '')),
      n: nOf(active.n),
    },
    spec_to_land_hours: {
      median: wireOf(tile('spec_to_land_hours').value), n: nOf(tile('spec_to_land_hours').n),
    },
    escapes_in_window: wireOf(tile('escapes_in_window').value),
    rework_after_build: {
      tickets: Number(/^(\d+) tickets$/.exec(rework.second ?? '')?.[1] ?? NaN),
      share: wireOf(rework.value),
    },
  }
}

/** A repository `E32` answers for, with every rate measured — the default every case starts from. */
const measured = (over: Record<string, unknown> = {}) => ({
  window_days: 30,
  runs: 61,
  tickets_with_verdict: 12,
  first_pass_qa_rate: 0.75,
  rounds_per_ticket: { median: 2, mean: 2.4 },
  stage_minutes: [{ name: 'SPEC', median: 14, mean: 16.2, n: 9 }],
  active_minutes: { median: 38.5, mean: 44.2, n: 24 },
  spec_to_land_hours: { median: 6.4, n: 11 },
  escapes_in_window: 3,
  rework_after_build: { tickets: 5, share: 0.25 },
  ...over,
})

/** The window the surface asked for, off the URL — never off the component's own state. */
const windowsAsked = () =>
  callsTo('/api/metrics').map((c) => new URL(c.url, 'http://factory.test').searchParams.get('days'))

const metricsRoutes = (answer: (days: string) => { status?: number; body: unknown }) => ({
  '/api/metrics': (url: URL) => answer(url.searchParams.get('days') ?? ''),
})

const tileOnScreen = (key: string) => screen.getByTestId(`metric-${key}`)
const partOf = (key: string, part: string) =>
  within(tileOnScreen(key)).getByTestId(`metric-${part}`).textContent?.trim() ?? ''

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('WEB-FEAT-398 §1.1 — the design is the specification, and it is on disk', () => {
  it('the_frame_is_a_state_the_component_can_be_in', async () => {
    const source = readFileSync(DESIGN, 'utf8')
    const tiles = tilesOf(source)
    const fields = e32Fields()

    // The sweep says how much it swept: a locator that has stopped finding tiles turns every
    // agreement below green over nothing (`_TEMPLATE.md` §2, third way a check lies).
    expect(fields.length, 'no required field read out of http.schema.json for E32')
      .toBeGreaterThanOrEqual(10)
    expect(tiles.size, `frame ${METRICS_FRAME} yielded no tile at all`).toBeGreaterThan(0)

    // ── the frame draws every field E32 is contracted to serve ──────────────────────────────
    // Two of them are not tiles and each is asserted where it IS drawn rather than excused:
    // `window_days` is the CONTROL and the head's figure, `stage_minutes` is a table with one
    // row per stage. §4.21 says both in those words.
    const drawn = new Map<string, boolean>([
      ...fields.map((f) => [f, tiles.has(f)] as [string, boolean]),
      ['window_days', Number.isInteger(headWindowOf(source))],
      ['stage_minutes', stageRowsOf(source).length > 0],
    ])
    const missing = fields.filter((f) => drawn.get(f) !== true)
    expect(missing, `frame ${METRICS_FRAME} draws nothing for ${missing.join(', ')}`).toEqual([])

    const control = windowOf(source)
    expect(control.offered, 'the frame offers a window set §1 did not pre-decide')
      .toEqual([7, 30, 90])
    expect(control.selected, 'the frame draws more or fewer than one selected window')
      .toHaveLength(1)
    expect(control.selected[0], 'the head states one window and the control selects another')
      .toBe(headWindowOf(source))

    // ── and the numbers it draws agree with EACH OTHER (WEB-FIX-336) ────────────────────────
    // The rate and the count of the population it is a share OF, in one expression — the frame
    // side of WEB-FIX-091.
    const rate = tiles.get('first_pass_qa_rate') as Tile
    const denominator = tiles.get('tickets_with_verdict') as Tile
    expect(nOf(rate.n), `the frame draws ${rate.value} over ${rate.n} and states `
      + `${denominator.value} tickets with a verdict`).toBe(wireOf(denominator.value))
    expect(nOf(denominator.n), 'the count disagrees with its own population').toBe(
      wireOf(denominator.value))
    const share = wireOf(rate.value) as number
    const population = nOf(rate.n) as number
    expect(Number.isInteger(share * population),
      `${rate.value} of ${population} tickets is ${share * population} tickets`).toBe(true)

    // every stage row's n is bounded by the runs the frame says were measured
    const rows = stageRowsOf(source)
    expect(rows.length, 'the frame draws no stage_minutes row').toBeGreaterThan(1)
    const activeN = nOf((tiles.get('active_minutes') as Tile).n) as number
    for (const row of rows) {
      expect(Number(row.n), `stage ${row.name} was measured on ${row.n} runs and only `
        + `${activeN} runs were working`).toBeLessThanOrEqual(activeN)
    }
    expect(activeN, `${activeN} working runs out of ${wireOf(tiles.get('runs')!.value)}`)
      .toBeLessThanOrEqual(wireOf(tiles.get('runs')!.value) as number)

    // ── and it is a state the SHIPPED component renders, in the frame's own copy ────────────
    const body = responseFromTheFrame(source)
    await mountApp('#/metrics', metricsRoutes(() => ({ body })))
    await settle()

    expect(screen.getByTestId('metrics-window-' + control.selected[0]))
      .toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('metrics-days').textContent?.trim())
      .toBe(`${headWindowOf(source)} days`)
    for (const [key, tile] of tiles) {
      expect(partOf(key, 'label'),
        `${key} is not on the screen under the name the frame gives it`).toBe(key.toUpperCase())
      expect(partOf(key, 'value'), `${key}: the frame draws "${tile.value}"`).toBe(tile.value)
      expect(partOf(key, 'n'), `${key}: the frame draws "${tile.n}"`).toBe(tile.n)
      expect(within(tileOnScreen(key)).getByTestId('metric-note').textContent?.trim(),
        `${key}: the frame's derivation line`).toBe(tile.note)
      if (tile.second !== undefined) {
        expect(partOf(key, 'second'), `${key}: the frame draws "${tile.second}"`).toBe(tile.second)
      }
    }
    for (const row of stageRowsOf(source)) {
      const drawn = within(screen.getByTestId(`metric-stage-${row.name}`))
      expect(drawn.getByTestId('stage-median').textContent?.trim()).toBe(row.median)
      expect(drawn.getByTestId('stage-mean').textContent?.trim()).toBe(row.mean)
      expect(drawn.getByTestId('stage-n').textContent?.trim()).toBe(row.n)
    }
  })
})

describe('WEB-FEAT-398 §1.2 — every number on screen is the response’s', () => {
  it('every_number_is_the_responses_and_null_is_a_dash', async () => {
    // ── the answer, and every number on the screen asserted against THIS object ─────────────
    const answer = measured({
      stage_minutes: [
        { name: 'SPEC', median: 14, mean: 16.2, n: 9 },
        { name: 'GATE', median: 33.5, mean: 38.9, n: 7 },
      ],
    })
    const wider = measured({
      window_days: 90, runs: 140, tickets_with_verdict: 0, first_pass_qa_rate: null,
      rounds_per_ticket: { median: 3, mean: 3.5 },
      active_minutes: { median: 40, mean: 41, n: 90 },
      spec_to_land_hours: { median: null, n: 0 },
      escapes_in_window: 9, rework_after_build: { tickets: 0, share: null },
    })
    const served: Record<string, unknown> = { 7: null, 30: answer, 90: wider }

    await mountApp('#/metrics', metricsRoutes((days) => ({ body: served[days] })))
    await settle()

    // the default window is E32's own, and the request carried it
    expect(windowsAsked()).toEqual(['30'])

    // ONE expression per tile, with the response object that produced it — never with a number
    // this file computed a second time.
    expect(partOf('runs', 'value')).toBe(String(answer.runs))
    expect(partOf('runs', 'n')).toBe(`n ${answer.runs}`)
    expect(partOf('tickets_with_verdict', 'value')).toBe(String(answer.tickets_with_verdict))
    expect(partOf('first_pass_qa_rate', 'value'))
      .toBe(`${Math.round((answer.first_pass_qa_rate as number) * 100)}%`)
    // the rate's `n` is the OTHER field E32 serves for exactly this — WEB-FIX-091
    expect(partOf('first_pass_qa_rate', 'n')).toBe(`n ${answer.tickets_with_verdict}`)
    expect(partOf('rounds_per_ticket', 'value')).toBe(String(answer.rounds_per_ticket.median))
    expect(partOf('rounds_per_ticket', 'second')).toBe(`mean ${answer.rounds_per_ticket.mean}`)
    expect(partOf('active_minutes', 'value')).toBe(`${answer.active_minutes.median.toFixed(1)}m`)
    expect(partOf('active_minutes', 'n')).toBe(`n ${answer.active_minutes.n}`)
    expect(partOf('spec_to_land_hours', 'value'))
      .toBe(`${(answer.spec_to_land_hours.median as number).toFixed(1)}h`)
    expect(partOf('spec_to_land_hours', 'n')).toBe(`n ${answer.spec_to_land_hours.n}`)
    expect(partOf('escapes_in_window', 'value')).toBe(String(answer.escapes_in_window))
    expect(partOf('rework_after_build', 'value'))
      .toBe(`${Math.round((answer.rework_after_build.share as number) * 100)}%`)
    expect(partOf('rework_after_build', 'second'))
      .toBe(`${answer.rework_after_build.tickets} tickets`)
    // …and one ticket is one ticket. `1 tickets` is what the real fixture drew on the first
    // capture of this surface, in both themes, before anybody read the picture.
    expect(answer.rework_after_build.tickets).toBeGreaterThan(1)
    for (const row of answer.stage_minutes) {
      const drawn = within(screen.getByTestId(`metric-stage-${row.name}`))
      expect(drawn.getByTestId('stage-median').textContent?.trim()).toBe(`${row.median.toFixed(1)}m`)
      expect(drawn.getByTestId('stage-n').textContent?.trim()).toBe(String(row.n))
    }

    // ── the window control asks for the window it names, and the screen redraws ─────────────
    await act(async () => { fireEvent.click(screen.getByTestId('metrics-window-90')) })
    await settle()
    expect(windowsAsked()).toEqual(['30', '90'])
    expect(screen.getByTestId('metrics-days').textContent?.trim())
      .toBe(`${wider.window_days} days`)
    expect(partOf('runs', 'value')).toBe(String(wider.runs))

    // ── a rate that was never measured is a DASH with its n of 0, never `0%` ────────────────
    // `null` and `0` are different facts about a repository (§4.21), and this is the pair of
    // assertions that keeps them apart: the same tile, in the same case, over both answers.
    expect(wider.first_pass_qa_rate).toBeNull()
    expect(partOf('first_pass_qa_rate', 'value')).toBe('—')
    expect(partOf('first_pass_qa_rate', 'n')).toBe(`n ${wider.tickets_with_verdict}`)
    expect(partOf('spec_to_land_hours', 'value')).toBe('—')
    expect(partOf('spec_to_land_hours', 'n')).toBe('n 0')
    expect(partOf('rework_after_build', 'value')).toBe('—')
    expect(partOf('first_pass_qa_rate', 'value')).not.toBe('0%')

    // and a MEASURED zero is still a number — the other half of the same fact
    await act(async () => { fireEvent.click(screen.getByTestId('metrics-window-30')) })
    await settle()
    await act(async () => { fireEvent.click(screen.getByTestId('metrics-window-90')) })
    await settle()
    expect(partOf('first_pass_qa_rate', 'value')).toBe('—')
  })

  /**
   * Found in TIER 3, on this repository, and transcribed from what the screen actually drew:
   *
   *   ROUNDS_PER_TICKET  1  n —  mean 1.56818181818
   *
   * `E32` serves a `double` and the first draft drew it with `String()`, which is exact and
   * unreadable. The design frame draws `mean 2.4` — one decimal — and `String(2.4)` is `2.4`,
   * so the frame case agreed with the defect. A number nobody can read is a number nobody acts
   * on, which is the whole of what §4.21 is for.
   */
  it('a_median_or_a_mean_is_drawn_to_one_decimal', async () => {
    const long = measured({ rounds_per_ticket: { median: 1, mean: 1.56818181818 } })
    await mountApp('#/metrics', metricsRoutes(() => ({ body: long })))
    await settle()
    expect(partOf('rounds_per_ticket', 'second')).toBe('mean 1.6')
    // …and a whole number stays whole: `1.0` rounds per ticket is a unit nobody measures
    expect(partOf('rounds_per_ticket', 'value')).toBe('1')
  })

  it('one_ticket_is_one_ticket', async () => {
    const single = measured({ rework_after_build: { tickets: 1, share: 1 } })
    await mountApp('#/metrics', metricsRoutes(() => ({ body: single })))
    await settle()
    expect(partOf('rework_after_build', 'second')).toBe('1 ticket')
    expect(partOf('rework_after_build', 'value')).toBe('100%')
  })

  it('a_measured_zero_is_drawn_as_zero_and_not_as_a_dash', async () => {
    const none = measured({
      tickets_with_verdict: 8, first_pass_qa_rate: 0,
      rework_after_build: { tickets: 0, share: 0 },
    })
    await mountApp('#/metrics', metricsRoutes(() => ({ body: none })))
    await settle()
    expect(partOf('first_pass_qa_rate', 'value')).toBe('0%')
    expect(partOf('first_pass_qa_rate', 'n')).toBe('n 8')
    expect(partOf('rework_after_build', 'value')).toBe('0%')
  })

  /**
   * §1.2's last clause, as its own assertion rather than as a note about another suite.
   *
   * `no-poll.test.tsx` asserts the surfaces that existed when it was written; a surface added
   * afterwards is outside it and stays green while polling. So the rule is asserted HERE, on
   * this surface, in the same shape: mounted, untouched, ten minutes of fake time, and not one
   * more request than the mount made.
   */
  it('the_metrics_surface_does_not_poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await mountApp('#/metrics', metricsRoutes(() => ({ body: measured() })))
    await settle()
    const asked = windowsAsked().length
    expect(asked).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60 * 1000) })
    await settle()
    expect(windowsAsked().length, 'the surface asked again with nobody touching it').toBe(asked)
  })
})

describe('WEB-FEAT-398 §1.3 — the rail offers it and the gate opens it', () => {
  it('the_rail_offers_metrics_and_the_gate_sweeps_it', async () => {
    const nav = readFileSync(join(CLIENT, 'src', 'components', 'SideNav.tsx'), 'utf8')
    const destinations = [...new Set([...nav.matchAll(/hash:\s*'(#\/[^']*)'/g)].map((m) => m[1]))]
    expect(destinations.length, 'the derivation has stopped seeing the rail').toBeGreaterThan(8)
    expect(destinations, 'the rail offers no #/metrics').toContain('#/metrics')
    // after Escapes, which is where §1 pre-decided it goes
    expect(destinations.indexOf('#/metrics')).toBe(destinations.indexOf('#/escapes') + 1)

    // the gate's OWN list, imported rather than re-typed — `gate-surfaces.test.ts`'s rule
    const { SURFACES } = await import('../gate/surfaces.mjs')
    const swept = new Map((SURFACES as unknown[][]).map((row) => [String(row[1]), String(row[0])]))
    expect(swept.get('#/metrics'), '#/metrics is in the rail and in no gate surface').toBe('metrics')

    // and it is reachable: the route resolves to the surface rather than falling back to home
    await mountApp('#/metrics', metricsRoutes(() => ({ body: measured() })))
    await settle()
    expect(screen.getByTestId('metrics-page')).toBeInTheDocument()
  })
})

describe('WEB-FEAT-398 §1.4 — NEGATIVE: the empty state is a claim about `runs`', () => {
  /**
   * `WEB-FIX-088` drew *"Nothing has run on this ticket yet."* on a ticket with two runs, because
   * the suite asserted that copy verbatim against a fixture carrying four. So the sentence and
   * the collection are ONE expression here, and the same case drives BOTH sides of it: a window
   * with no runs, and a window with one.
   */
  it('the_empty_state_is_asserted_against_the_collection', async () => {
    // The sentence is the FRAME's, read off disk — §1.1 requires the design's empty state to be
    // this copy, and a string typed here would let the two drift without anything going red.
    const design = emptyOf(readFileSync(DESIGN, 'utf8'))
    expect(design.caption, 'the frame does not say which condition its empty state draws')
      .toBe('EMPTY \u00b7 runs = 0')
    const SENTENCE = design.sentence
    expect(SENTENCE, 'the frame draws no empty-state sentence')
      .toBe('No run has been recorded in this repository.')
    // ── THE TWO COLLECTIONS ARE FORCED APART, IN BOTH DIRECTIONS ───────────────────────────
    //
    // `WEB-FEAT-001` drew *"No requirements in this repository."* beside a condition built
    // entirely out of the TICKET set: both halves agreed, neither was the collection the
    // sentence named, and the case shared the mistake with the code (`WEB-FIX-002`). A fixture
    // where `runs` and every other count are zero together cannot tell which one is read.
    //
    // So: a repository with NO run and three escapes on the ledger, and a repository with one
    // run and NO ticket that has reached a verdict. A surface reading `tickets_with_verdict`,
    // or `escapes_in_window`, or `active_minutes.n`, draws the sentence on the wrong one of
    // these two and this case says so by name.
    const nothing = measured({
      runs: 0, tickets_with_verdict: 0, first_pass_qa_rate: null,
      rounds_per_ticket: { median: null, mean: null }, stage_minutes: [],
      active_minutes: { median: null, mean: null, n: 0 },
      spec_to_land_hours: { median: null, n: 0 },
      escapes_in_window: 3, rework_after_build: { tickets: 0, share: null },
    })
    const one = measured({
      window_days: 90, runs: 1, tickets_with_verdict: 0, first_pass_qa_rate: null,
      stage_minutes: [], active_minutes: { median: null, mean: null, n: 0 },
      spec_to_land_hours: { median: null, n: 0 },
      escapes_in_window: 0, rework_after_build: { tickets: 0, share: null },
    })
    // the derivation, stated: on each side, `runs` and the next-likeliest collection disagree
    expect(nothing.runs === 0).not.toBe(nothing.escapes_in_window === 0)
    expect(one.runs === 0).not.toBe(one.tickets_with_verdict === 0)
    const served: Record<string, unknown> = { 30: nothing, 90: one }

    await mountApp('#/metrics', metricsRoutes((days) => ({ body: served[days] })))
    await settle()

    const said = () => screen.queryByTestId('metrics-empty')?.textContent?.trim()
    const shownFor = (body: { runs: number }) => (body.runs === 0 ? SENTENCE : undefined)

    expect(said(), `runs is ${nothing.runs}`).toBe(shownFor(nothing))
    // and NO number: not a tile, not the head's own figure
    expect(screen.queryAllByTestId(/^metric-/), 'the empty state drew a number').toHaveLength(0)
    expect(screen.queryByTestId('metrics-days'), 'the head drew a figure over an empty window')
      .toBeNull()

    await act(async () => { fireEvent.click(screen.getByTestId('metrics-window-90')) })
    await settle()
    expect(said(), `runs is ${one.runs}`).toBe(shownFor(one))
    expect(screen.getByTestId('metric-runs')).toBeInTheDocument()
  })
})

describe('WEB-FEAT-398 §1.5 — NEGATIVE: a refused window is shown and nothing retries', () => {
  it('a_refused_window_is_shown_and_nothing_retries', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const answer = measured()
    const refusal = {
      error: {
        code: 'bad_window',
        message: '`days` is a whole number of days between 1 and 3650',
        detail: { param: 'days', days: '90' },
      },
    }
    await mountApp('#/metrics', metricsRoutes((days) =>
      (days === '30' ? { body: answer } : { status: 400, body: refusal })))
    await settle()
    expect(partOf('runs', 'value')).toBe(String(answer.runs))

    await act(async () => { fireEvent.click(screen.getByTestId('metrics-window-90')) })
    await settle()

    // the error's own message, in the surface's error slot
    expect(screen.getByTestId('metrics-refused').textContent)
      .toContain(refusal.error.message)
    // …and the numbers that were already there are still there
    expect(partOf('runs', 'value'), 'the refusal blanked the screen').toBe(String(answer.runs))
    expect(screen.getByTestId('metrics-days').textContent?.trim())
      .toBe(`${answer.window_days} days`)

    // …and nothing asks again. A retry is invisible to an assertion taken in the same tick, so
    // ten minutes of fake time pass before the count is read (`SRV-FIX-034`: wait on a fact, and
    // never read a race one tick after starting it).
    const asked = windowsAsked()
    expect(asked).toEqual(['30', '90'])
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60 * 1000) })
    await settle()
    expect(windowsAsked(), 'the surface retried a window the server refused').toEqual(asked)
  })
})
