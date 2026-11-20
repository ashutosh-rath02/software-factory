import { afterEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, cleanup, fireEvent, screen } from '@testing-library/react'

import { delivery, mountApp, settle } from './helpers'

/**
 * WEB-FIX-334 — the SEARCH row was implemented against a design change that was never made.
 *
 * `WEB-FEAT-332` said the design led and had already changed. It had — on the pen.dev CANVAS,
 * which is not the file. The editor held the document in a buffer under
 * `~/.config/Code/.../highagency.pencildev/` and `client/web/design/factory.pen` was last written
 * at 10:23, hours before the row was drawn. Every verification I ran that round read the canvas:
 * `Export` renders the app's document, so the PNG I checked the row against was true and the
 * repository was untouched. A screenshot of a design is not the design.
 *
 * This reads the FILE, off disk, as JSON — the artefact `design/BRIEF.md` enumerates and the one a
 * later ticket opens to learn what this surface is.
 */
const DESIGN = join(__dirname, '..', 'design', 'factory.pen')
const TICKETS_FRAME = 'DAiio'          // `Dashboard · Tickets by epic`

type Node = { id?: string; name?: string; type?: string; content?: string; children?: Node[] }

const parse = (source: string) => JSON.parse(source) as { children: Node[] }
const frame = (doc: { children: Node[] }, id: string) =>
  doc.children.find((c) => c.id === id)

function copy(n: Node | undefined): string[] {
  if (!n) return []
  const here = n.type === 'text' && n.content ? [n.content] : []
  return here.concat(...(n.children ?? []).map(copy))
}

/** The predicate itself, so the positive and the negative below run the SAME code. */
function searchRow(source: string) {
  const tickets = frame(parse(source), TICKETS_FRAME)
  const rows = tickets?.children ?? []
  const at = rows.findIndex((r) => copy(r).some((t) => t.trim() === 'SEARCH'))
  return {
    present: at >= 0,
    index: at,
    provenanceAt: rows.findIndex((r) => copy(r).some((t) => t.trim() === 'PROVENANCE')),
    text: at >= 0 ? copy(rows[at]) : [],
  }
}

describe('WEB-FIX-334 — the design holds the row the product ships', () => {
  it('test_the_design_holds_the_search_row', () => {
    const row = searchRow(readFileSync(DESIGN, 'utf8'))
    expect(row.present, `frame ${TICKETS_FRAME} draws no SEARCH row`).toBe(true)

    // above PROVENANCE, which is where §1 put it and where the screen draws it
    expect(row.index).toBeLessThan(row.provenanceAt)

    // a label, a field showing a query, and the result line — the row is not a bare heading
    expect(row.text[0].trim()).toBe('SEARCH')
    expect(row.text.length).toBeGreaterThanOrEqual(3)

    // and the result line names what `catalog/render.cpp` MATCHES. The design said `workspace`,
    // which the server does not search; a screen naming a field the server ignores teaches a
    // person to type something that cannot match.
    const result = row.text.find((t) => t.includes('matches')) ?? ''
    expect(result).toContain('id')
    expect(result).toContain('title')
    expect(result).toContain('owner')
    expect(result).toContain('lane')
    expect(result).not.toContain('workspace')
  })

  /**
   * MANDATORY NEGATIVE, and criterion 3 — executable rather than remembered.
   *
   * `8ab659e` is `WEB-FEAT-332: awaiting-qa`, the commit the round was handed to QA at. The same
   * predicate, run against the design as it stood there, must say the row is absent. A check that
   * passes on the design that has no SEARCH row is checking nothing.
   */
  it('test_the_check_fails_on_the_design_that_had_no_search_row', () => {
    const before = execFileSync('git', ['show', '8ab659e:client/web/design/factory.pen'],
                                { cwd: join(__dirname, '..', '..', '..'), maxBuffer: 32 << 20 })
    const row = searchRow(before.toString('utf8'))
    expect(row.present, 'the design at 8ab659e must NOT hold a SEARCH row').toBe(false)
    // the frame was there all along — it is the row that was missing, not the surface
    expect(row.provenanceAt).toBeGreaterThanOrEqual(0)
  })
})

/* ------------------------------------------------------------------------------------------- *
 * WEB-FIX-336 — one frame, two backlog sizes.
 *
 * `WEB-FIX-334` corrected the number in the copy it wrote and left the number one line above it,
 * so frame `DAiio` states the backlog is 135 in its header and 340 in the SEARCH row's result
 * line. Its check asserts the row's presence, its position and its result-line copy; nothing asks
 * whether the frame as a whole describes a state the surface can be in.
 * ------------------------------------------------------------------------------------------- */

const ROOT = join(__dirname, '..', '..', '..')
const NAV_FRAME = 'yKLSZ'                   // `Shell · Nav`
const ORCHARD_FRAME = 'I55XQ8'              // `Orchard`
const BEFORE = 'ae43d78'                    // `WEB-FIX-334: GATE — report, and awaiting-qa`

const nodes = (n: Node | undefined): Node[] =>
  n === undefined ? [] : [n, ...(n.children ?? []).flatMap(nodes)]

/** The SMALLEST descendant frame holding `tag` as a whole text and a ticket count — the card. */
function card(root: Node | undefined, tag: string): Node | undefined {
  const cards = nodes(root).filter(
    (n) => n.type === 'frame' && copy(n).some((t) => t.trim() === tag)
                             && copy(n).some((t) => /^\d+\s+tickets$/.test(t.trim())))
  return cards.sort((a, b) => copy(a).length - copy(b).length)[0]
}

interface Claim { where: string; value: number }

/**
 * Every place the design states HOW MANY TICKETS THIS REPOSITORY HOLDS.
 *
 * Located by POSITION, never by the number they happen to carry today: a site that stops being
 * found yields `NaN` and fails the agreement below by name, so a renamed or deleted layer is a
 * red rather than a sweep that quietly shrank.
 *
 * Excluded by category, with the reason: the Orchard's other cards — `FW`, `SRV`, `WEB`, `HW` —
 * are OTHER repositories in the same view, so their counts are not this repository's backlog and
 * must NOT agree with it. `FAC` is this one; its tag row reads `tool` where theirs read `product`.
 */
function backlogClaims(source: string): Claim[] {
  const doc = parse(source)
  const digits = (n: Node | undefined) =>
    Number(copy(n).map((t) => t.trim()).find((t) => /^\d+$/.test(t)) ?? NaN)

  // the frame's own two, read by the SAME locators the state below uses — two readers of one
  // layer are two things that can disagree, which is the shape of the defect this ticket is about
  const state = frameState(source)

  const navRow = nodes(frame(doc, NAV_FRAME)).find(
    (n) => n.type === 'frame' && (n.children ?? []).some((c) => c.content?.trim() === 'Tickets'))
  const har = card(frame(doc, ORCHARD_FRAME), 'FAC')

  return [
    // the header pill — `TicketsByEpic.tsx:44` draws it, and §1 reads it as a backlog size
    { where: `${TICKETS_FRAME} · Head pill`, value: Number(state.header) },
    // the second number of `<matched> of <backlog>` — the row WEB-FIX-334 added
    { where: `${TICKETS_FRAME} · SEARCH result line`, value: state.backlog },
    // the left rail's count beside `Tickets`
    { where: 'Shell · Nav · Tickets count', value: digits(navRow) },
    // the orchard card for THIS repository
    { where: 'Orchard · FAC tickets', value: Number(/^(\d+)\s+tickets$/
        .exec(copy(har).map((t) => t.trim()).find((t) => /^\d+\s+tickets$/.test(t)) ?? '')?.[1]
        ?? NaN) },
  ]
}

/** The frame's SEARCH row as a state: what is typed, what is drawn beside it, what is above it. */
function frameState(source: string) {
  const doc = parse(source)
  const tickets = frame(doc, TICKETS_FRAME)
  const head = (tickets?.children ?? []).find((r) => copy(r).some((t) => t.trim() === 'Tickets'))
  const row = searchRow(source)
  const result = row.text.find((t) => /\d+\s+of\s+\d+/.test(t)) ?? ''
  const line = /(\d+)\s+of\s+(\d+)/.exec(result)
  return {
    header: copy(head).map((t) => t.trim()).find((t) => /^\d+$/.test(t)) ?? '',
    query: row.text.find((t) => t.trim() !== 'SEARCH' && t !== result)?.trim() ?? '',
    result,
    matched: Number(line?.[1] ?? NaN),
    backlog: Number(line?.[2] ?? NaN),
  }
}

/**
 * How many tickets this repository holds, by `catalog::Store::isDocument`'s rule and in the
 * directory the MANIFEST declares — not a path this file typed.
 */
const isDocument = (name: string) =>
  name.endsWith('.md') && !name.startsWith('.') && !name.startsWith('_') && name !== 'README.md'

function ticketsDir(): string {
  const manifest = JSON.parse(readFileSync(join(ROOT, '.factory', 'factory.json'), 'utf8'))
  return manifest.docs.tickets as string
}

const git = (...args: string[]) =>
  execFileSync('git', args, { cwd: ROOT, maxBuffer: 32 << 20 }).toString('utf8')

const ticketsOnDisk = () => readdirSync(join(ROOT, ticketsDir())).filter(isDocument).length

const ticketsAt = (sha: string) =>
  git('ls-tree', '--name-only', `${sha}:${ticketsDir()}`).split('\n').filter(isDocument).length

/** The commit that first put this design in the repository — the oldest size it can have meant. */
const designWasBorn = () =>
  git('log', '--format=%H', '--reverse', '--', 'client/web/design/factory.pen')
    .split('\n').filter(Boolean)[0] ?? ''

/** One page of E4, for a server holding `total` tickets that matched this query. */
const ticketRow = (i: number) => ({
  id: `SRV-FEAT-${String(i).padStart(3, '0')}`, title: 'A ticket the design does not name',
  workspace: 'factory-server', status: 'in-progress', provenance: 'unshipped',
  delivery: delivery(),
  stage: { index: 8, total: 10, round: 1, of: 1, source: 'ticket' },
  lane: null, owner: { name: 'congnguyen', source: 'commits' },
})

function page(total: number, q: string) {
  const size = 50
  const rows = Array.from({ length: Math.min(size, total) }, (_, i) => ticketRow(i + 1))
  return {
    groups: rows.length
      ? [{ parent: 'FAC-EPIC-001', title: 'The factory', count: total,
           merged: 0, pushed: 0, tickets: rows }]
      : [],
    total, from: rows.length ? 1 : 0, to: rows.length,
    page: 1, pages: Math.max(1, Math.ceil(total / size)), page_size: size,
    facets: { provenance: [], stage: [] },
    applied: { q, provenance: 'all', stage: '' },
  }
}

/**
 * A server holding `backlog` tickets, of which `query` matches `matched`.
 *
 * The numbers are the FRAME's, so the question this stub asks is the one criterion 2 asks: given
 * a repository in the state the frame draws, is what `TicketsByEpic` puts on screen what the
 * frame says? A stub built from this repository's own count instead would go red every time a
 * ticket was added, which is a fact about the backlog and not about the design.
 */
const serverHolding = (backlog: number, query: string, matched: number) => ({
  '/api/tickets': (url: URL) => {
    const q = url.searchParams.get('q') ?? ''
    return { body: page(q === '' ? backlog : (q === query ? matched : 0), q) }
  },
  '/api/lanes': { body: null },
})

/**
 * Put a value in the field and let it SETTLE.
 *
 * WEB-FIX-333 put a 300 ms pause between the last keystroke and the request the surface makes with
 * it, so a case that changes the field and only flushes microtasks asserts about an answer nobody
 * has asked for yet. Every assertion below is unchanged and so are the frame's numbers; what is
 * added is the pause the design says the field asks after. The window is the DESIGN's number, read
 * from `BRIEF.md` in the same spirit as the frame — not imported from `services/settle.ts`, which
 * would agree with whatever the implementation chose.
 */
const SETTLE_WINDOW_MS = 300

const type = async (value: string) => {
  await act(async () => {
    fireEvent.change(screen.getByTestId('tickets-search'), { target: { value } })
  })
  await act(async () => { await new Promise((r) => setTimeout(r, SETTLE_WINDOW_MS + 50)) })
}

afterEach(cleanup)

describe('WEB-FIX-336 — the frame states one backlog size, and one the surface can draw', () => {
  it('test_the_frame_states_one_backlog_size', () => {
    const state = frameState(readFileSync(DESIGN, 'utf8'))

    // §1, in its own words: the header count and the result line's second number, together in
    // one assertion, because two cases each reading one of them agree with themselves.
    expect(Number(state.header),
           `frame ${TICKETS_FRAME} states two backlog sizes: header ${state.header}, `
           + `result line "${state.result}"`).toBe(state.backlog)

    // and everywhere else the design states the same thing — the class, not the site
    const claims = backlogClaims(readFileSync(DESIGN, 'utf8'))
    expect(claims.length, 'the sweep found no backlog claim at all').toBeGreaterThan(0)
    const said = claims.map((c) => `${c.where}=${c.value}`).join(' · ')
    expect([...new Set(claims.map((c) => c.value))], said).toHaveLength(1)

    // and it is a size this repository has actually held. The band is derived, not typed: no
    // number below what it held when this design was first committed, and none above what it
    // holds now. A static frame cannot track a backlog that grows every ticket, so the check
    // that survives is that the number was MEASURED — `135` never was.
    const size = claims[0].value
    const now = ticketsOnDisk()
    const floor = ticketsAt(designWasBorn())
    expect(now, `no ticket document found under ${ticketsDir()}`).toBeGreaterThan(0)
    expect(floor, 'the design has no commit history to take a floor from').toBeGreaterThan(0)
    expect(size, `${said}; this repository held ${floor} tickets when the design was first `
                 + `committed and holds ${now} now`).toBeGreaterThanOrEqual(floor)
    expect(size, `${said}; this repository holds ${now} tickets`).toBeLessThanOrEqual(now)
  })

  it('test_the_frames_counts_are_a_state_the_component_renders', async () => {
    const state = frameState(readFileSync(DESIGN, 'utf8'))
    expect(state.query, 'the frame draws no query, so the result line beside it is a state '
                        + 'TicketsByEpic never renders').not.toBe('')

    // the REAL component, against a server in the state the frame draws
    await mountApp('#/tickets', serverHolding(state.backlog, state.query, state.matched))
    await settle()
    await type(state.query)
    await settle()

    expect(screen.getByTestId('search-result')).toHaveTextContent(state.result)
    const pill = screen.getByTestId('tickets-by-epic').querySelector('.block-head .pill')
    expect(pill?.textContent,
           `with "${state.query}" in the field the screen draws ${pill?.textContent} in the `
           + `header and the frame draws ${state.header}`).toBe(state.header)
  })

  it('test_the_search_rows_layers_are_named_for_what_they_are', () => {
    const doc = parse(readFileSync(DESIGN, 'utf8'))
    const rows = frame(doc, TICKETS_FRAME)?.children ?? []
    const rowWith = (label: string) => rows.find((r) => copy(r).some((t) => t.trim() === label))
    const layerNames = (r: Node | undefined) =>
      new Set(nodes(r).slice(1).map((n) => n.name ?? ''))

    const search = rowWith('SEARCH')
    const status = rowWith('STATUS')
    expect(search, 'no SEARCH row').toBeDefined()
    expect(status, 'no STATUS row').toBeDefined()

    // the row was copied from the STATUS row; a layer still carrying that row's name is what the
    // layers panel — the artefact `design/BRIEF.md` enumerates — hands the next author.
    const borrowed = [...layerNames(search)].filter((n) => layerNames(status).has(n))
    expect(borrowed, `the SEARCH row still carries the STATUS row's layer names: `
                     + borrowed.join(', ')).toEqual([])

    const label = nodes(search).find((n) => n.content?.trim() === 'SEARCH')
    expect(label?.name, 'the label layer is not named for what it says').toBe('SEARCH')
  })

  /**
   * MANDATORY NEGATIVE, and criterion 3 — executable rather than remembered.
   *
   * `ae43d78` is `WEB-FIX-334: GATE — report, and awaiting-qa`, the commit the frame stood at
   * when this was found. The same locators, run over that blob, must find claims that DISAGREE,
   * and the two numbers §1 names must be among them.
   */
  it('test_the_check_fails_on_the_frame_that_states_two_backlog_sizes', () => {
    const claims = backlogClaims(git('show', `${BEFORE}:client/web/design/factory.pen`))
    const said = claims.map((c) => `${c.where}=${c.value}`).join(' · ')
    expect(claims.length, said).toBeGreaterThan(0)
    expect([...new Set(claims.map((c) => c.value))].length, said).toBeGreaterThan(1)
    expect(claims.map((c) => c.value), said).toContain(135)
    expect(claims.map((c) => c.value), said).toContain(340)
  })
})
