import { describe, it, expect, afterEach, vi } from 'vitest'
import { screen, cleanup, fireEvent, act } from '@testing-library/react'

import { delivery, mountApp, callsTo, settle } from './helpers'

const last = (path: string) => { const c = callsTo(path); return c[c.length - 1].url }

/**
 * The window `design/BRIEF.md` gives the SEARCH row: *the field asks the server once the value has
 * stood still for 300 ms, and never otherwise.* It lives here as the DESIGN's number and not as
 * the module's — a case importing the implementation's own constant would agree with whatever the
 * implementation chose.
 */
const WINDOW_MS = 300
/** ≈100 WPM. "Ordinary typing speed" in criterion 1 is a gap well inside the window. */
const KEY_GAP_MS = 120

/**
 * WEB-FEAT-332 · the tickets surface can be searched — `factory.pen` frame `DAiio`, SEARCH row.
 *
 * `E29` has searched since it shipped and no screen offered it: 329 tickets, 50 to a page, and
 * finding one by id meant clicking `next →` six times. The search goes to the SERVER — a client
 * filtering its own page would search 50 of 329 and report a total for all of them.
 */
const row = (over: Record<string, unknown> = {}) => ({
  id: 'SRV-FEAT-021', title: 'Lane recovery', workspace: 'factory-server',
  status: 'in-progress', provenance: 'unshipped', delivery: delivery(),
  stage: { index: 8, total: 10, round: 1, of: 1, source: 'ticket' },
  lane: null, owner: { name: 'congnguyen', source: 'commits' }, ...over,
})

const index = (tickets: Array<Record<string, unknown>>, over: Record<string, unknown> = {}) => ({
  groups: tickets.length
    ? [{ parent: 'FAC-EPIC-001', title: 'The factory', count: tickets.length,
         merged: 0, pushed: 0, tickets }]
    : [],
  total: tickets.length, from: tickets.length ? 1 : 0, to: tickets.length,
  page: 1, pages: 1, page_size: 50,
  facets: { provenance: [], stage: [] },
  applied: { q: '', provenance: 'all', stage: '' }, ...over,
})

const ALL = [row(), row({ id: 'WEB-FIX-329', title: 'Every committed baseline predates the nav',
                          workspace: 'factory-client' }),
             row({ id: 'WEB-FIX-031', title: 'A stalled lane says so' })]

/** The server answers the query it was given — which is the whole point of asking it. */
const searching = (all = ALL) => ({
  '/api/tickets': (url: URL) => {
    const q = url.searchParams.get('q') ?? ''
    const hit = q === '' ? all : all.filter(t => (t.id as string).includes(q))
    return { body: index(hit, { applied: { q, provenance: url.searchParams.get('provenance') ?? 'all',
                                           stage: url.searchParams.get('stage') ?? '' } }) }
  },
  '/api/lanes': { body: null },
})

/**
 * Put a whole value in the field and let it SETTLE.
 *
 * WEB-FIX-333 put a 300 ms pause between the last keystroke and the request, so a case that
 * changed the field and only flushed microtasks would be asserting about a request nobody has
 * made yet. Every assertion in this block is unchanged; what changed is that the field is now
 * given the pause the design says it asks after.
 */
const type = async (value: string) => {
  await act(async () => {
    fireEvent.change(screen.getByTestId('tickets-search'), { target: { value } })
  })
  await act(async () => { await new Promise(r => setTimeout(r, WINDOW_MS + 50)) })
}

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('WEB-FEAT-332 · the tickets surface can be searched', () => {
  it('test_typing_an_id_finds_the_ticket', async () => {
    await mountApp('#/tickets', searching())
    await settle()
    expect(screen.getAllByRole('row')).toHaveLength(4)          // header + three

    await type('329')
    await settle()

    const ids = [...screen.getByTestId('epic-group').querySelectorAll('tbody tr td:first-child')]
      .map(td => td.textContent)
    expect(ids).toEqual(['WEB-FIX-329'])
    // `1 of 3` — the matched count is this answer's `total`, and `3` is the repository's, taken
    // from the answer whose `applied` carried no query at all. A screen that recomputed either
    // from its own rows would report the page it drew, not the backlog it searched.
    expect(screen.getByTestId('search-result')).toHaveTextContent(
      '1 of 3 · matches id, title, owner and lane')
  })

  it('test_the_query_reaches_the_server_and_resets_the_page', async () => {
    await mountApp('#/tickets', {
      ...searching(),
      '/api/tickets': (url: URL) => {
        const q = url.searchParams.get('q') ?? ''
        const hit = q === '' ? ALL : ALL.filter(t => (t.id as string).includes(q))
        return { body: index(hit, { pages: q === '' ? 4 : 1, page: Number(url.searchParams.get('page')),
                                    total: q === '' ? 170 : hit.length,
                                    applied: { q, provenance: 'unshipped', stage: 'QA' } }) }
      },
    })
    await settle()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^next/ })) })
    await settle()
    expect(last('/api/tickets')).toContain('page=2')

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'unshipped' })) })
    await settle()
    await type('329')
    await settle()

    const asked = new URL(last('/api/tickets'), 'http://factory.test')
    expect(asked.searchParams.get('q')).toBe('329')
    expect(asked.searchParams.get('provenance')).toBe('unshipped')
    // page 4 of a one-page answer is nowhere: a new query starts at the top, as the filters do
    expect(asked.searchParams.get('page')).toBe('1')
  })

  it('test_a_query_matching_nothing_says_what_it_searched', async () => {
    await mountApp('#/tickets', searching())
    await settle()
    await type('ZZZ-999')
    await settle()

    expect(screen.queryByTestId('epic-group')).toBeNull()
    const said = screen.getByTestId('tickets-empty').textContent ?? ''
    expect(said).toContain('ZZZ-999')
    // not "the repository holds 0" — 0 is what the QUERY found, and the backlog still holds three
    expect(said).toContain('3')
  })

  it('test_the_client_draws_what_the_server_returned', async () => {
    // MANDATORY NEGATIVE. The server decides what matches; a client that re-filtered the rows it
    // was sent would draw one set and count another — the same split the facet counts avoid.
    await mountApp('#/tickets', {
      '/api/tickets': { body: index(ALL) },
      '/api/lanes': { body: null },
    })
    await settle()
    await type('nothing-here-matches-this')
    await settle()

    const ids = [...screen.getByTestId('epic-group').querySelectorAll('tbody tr td:first-child')]
      .map(td => td.textContent)
    expect(ids).toEqual(['SRV-FEAT-021', 'WEB-FIX-329', 'WEB-FIX-031'])
  })
})


/**
 * WEB-FIX-333 · the field asks once, when the typing has settled.
 *
 * `WEB-FEAT-332` above put the query on the wire and asked for it once per keystroke: `query` is a
 * segment of the `useEndpoint` key, so eleven characters were eleven full sweeps of a catalog that
 * has no index by design, and the two nobody wanted most — `q=3`, `q=32` — are the broadest scans
 * the field can produce.
 *
 * `design/BRIEF.md`, "The SEARCH row asks once, when the typing has settled", is what these cases
 * are written from: *the field asks the server once the value has stood still for 300 ms, and
 * never otherwise.* The number lives here as the DESIGN's, not as the module's — a case importing
 * the implementation's constant would agree with whatever the implementation chose.
 */

/** Type the way a person does: one character at a time, with a gap between each. */
const typeChars = async (value: string, gapMs = KEY_GAP_MS) => {
  const field = screen.getByTestId('tickets-search')
  for (let i = 1; i <= value.length; i += 1) {
    await act(async () => {
      fireEvent.change(field, { target: { value: value.slice(0, i) } })
      vi.advanceTimersByTime(gapMs)
    })
  }
}

/** One keystroke, and the gap after it. */
const press = async (value: string, gapMs = KEY_GAP_MS) => {
  await act(async () => {
    fireEvent.change(screen.getByTestId('tickets-search'), { target: { value } })
    vi.advanceTimersByTime(gapMs)
  })
}

/** Every `q` this client actually put on the wire, in order — the whole evidence for "once". */
const asked = () => callsTo('/api/tickets')
  .map(c => new URL(c.url, 'http://factory.test').searchParams.get('q') ?? '')

const drawnIds = () =>
  [...screen.getByTestId('epic-group').querySelectorAll('tbody tr td:first-child')]
    .map(td => td.textContent)

describe('WEB-FIX-333 · the field asks once, when the typing has settled', () => {
  it('test_typing_asks_the_server_once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    await mountApp('#/tickets', searching())
    await settle()
    expect(asked()).toEqual([''])                     // the mount's own unfiltered read

    const ID = 'WEB-FIX-329'
    await typeChars(ID)
    await act(async () => { vi.advanceTimersByTime(WINDOW_MS) })
    await settle()

    // exactly one query request, carrying the whole id
    expect(asked().filter(q => q !== '')).toEqual([ID])

    // and not one prefix on the way to it. The set is DERIVED from the value that was typed —
    // a hand-written list of the prefixes I happened to think of is bounded by what I thought of.
    const prefixes = [...ID].map((_, i) => ID.slice(0, i + 1)).slice(0, -1)
    expect(prefixes).toHaveLength(ID.length - 1)      // the sweep is not empty
    expect(asked().filter(q => prefixes.includes(q))).toEqual([])

    // eleven characters, two requests in all: the mount's, and the settled query's
    expect(callsTo('/api/tickets')).toHaveLength(2)
    expect(drawnIds()).toEqual([ID])
  })

  it('test_the_settled_query_arrives_on_its_own', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    await mountApp('#/tickets', searching())
    await settle()
    expect(screen.getAllByRole('row')).toHaveLength(4)          // header + three

    await typeChars('329')
    await settle()

    // Inside the window the keystroke has asked nothing — so whatever asks next was provoked by
    // the pause and not by the key. This is the line that tells the two sources apart, and the
    // result line is silent with it: it is a claim about the answer on screen, and there is no
    // answer to the typed query yet.
    expect(asked().filter(q => q !== '')).toEqual([])
    expect(screen.getAllByRole('row')).toHaveLength(4)
    expect(screen.queryByTestId('search-result')).toBeNull()

    // Nothing else happens. No blur, no Enter, no click — only time passing.
    await act(async () => { vi.advanceTimersByTime(WINDOW_MS) })
    await settle()

    expect(asked().filter(q => q !== '')).toEqual(['329'])
    expect(drawnIds()).toEqual(['WEB-FIX-329'])
    // the count and the rows it counts, in one assertion: `1` is what this answer matched and
    // `3` is the backlog it searched
    expect(screen.getByTestId('search-result')).toHaveTextContent(
      `${drawnIds().length} of 3 · matches id, title, owner and lane`)
  })

  it('test_an_abandoned_query_is_never_asked', async () => {
    // MANDATORY NEGATIVE. A debounce that fires the intermediate query late is worse than none:
    // the stale answer lands after the screen has moved on.
    vi.useFakeTimers({ shouldAdvanceTime: false })
    await mountApp('#/tickets', searching())
    await settle()
    expect(asked()).toEqual([''])
    expect(drawnIds()).toEqual(['SRV-FEAT-021', 'WEB-FIX-329', 'WEB-FIX-031'])

    // `3`, `32`, then both deleted — four events, none more than 60 ms after the last
    await typeChars('32', 60)
    await press('3', 60)
    await press('', 60)
    await settle()

    // and then ten minutes, so "never asked" means never rather than not yet
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    expect(asked()).not.toContain('3')
    expect(asked()).not.toContain('32')
    expect(asked()).toEqual([''])                     // the mount's read, and nothing since
    expect(callsTo('/api/tickets')).toHaveLength(1)

    // the surface is the unfiltered backlog it started as, and says nothing about a query
    expect(drawnIds()).toEqual(['SRV-FEAT-021', 'WEB-FIX-329', 'WEB-FIX-031'])
    expect(screen.queryByTestId('search-result')).toBeNull()
    expect((screen.getByTestId('tickets-search') as HTMLInputElement).value).toBe('')
  })
})
