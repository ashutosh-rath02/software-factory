import { describe, it, expect, afterEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent } from '@testing-library/react'

import { mountApp, calls, callsTo, delivery, lane, laneSet, requirement, settle } from './helpers'

afterEach(() => { cleanup(); vi.useRealTimers() })

const ROWS = [requirement(), requirement({ id: 'REQ-011', title: 'Second' })]

/** SPEC.md §4.14 — the surface a poll would be easiest to hide in (WEB-FEAT-050). */
const LANES = [
  lane({ lane: 'lane/01', ticket_id: 'TKT-142', workspace: 'core' }),
  lane({ lane: 'lane/02', ticket_id: 'TKT-147', workspace: 'edge', needs_you: true }),
]

/**
 * WEB-FIX-333 — `#/tickets`, the one surface in this client that now owns a timer.
 *
 * The index in the shape `E29` answers it, with one row: enough for the table to draw and for a
 * query to match or miss.
 */
const TICKET_ROW = {
  id: 'WEB-FIX-329', title: 'Every committed baseline predates the nav',
  workspace: 'factory-client', status: 'in-progress', provenance: 'unshipped',
  delivery: delivery(), stage: { index: 8, total: 10, round: 1, of: 1, source: 'ticket' },
  lane: null, owner: { name: 'congnguyen', source: 'commits' },
}

const ticketIndex = (q: string) => {
  const hit = q === '' || TICKET_ROW.id.includes(q) ? [TICKET_ROW] : []
  return {
    groups: hit.length
      ? [{ parent: 'FAC-EPIC-001', title: 'The factory', count: 1, merged: 0, pushed: 0,
           tickets: hit }]
      : [],
    total: hit.length, from: hit.length ? 1 : 0, to: hit.length,
    page: 1, pages: 1, page_size: 50,
    facets: { provenance: [], stage: [] },
    applied: { q, provenance: 'all', stage: '' },
  }
}

const TICKETS_ROUTES = {
  '/api/tickets': (url: URL) => ({ body: ticketIndex(url.searchParams.get('q') ?? '') }),
  '/api/lanes': { body: null },
}

describe('MANDATORY NEGATIVE · the architecture makes polling hard', () => {
  it('nothing_is_rebuilt_on_a_timer', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })

    const app = await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: ROWS, total: 2, in_flight: 0, warnings: [] } },
    })
    await settle()
    expect(screen.getAllByTestId('requirement-row')).toHaveLength(2)

    // React finishes a mount on a setTimeout(0) of its own — one deferred commit that belongs to
    // the scheduler and not to this app. It is flushed here, in the open, and bounded: a client
    // that re-rendered repeatedly in its first second would fail this line rather than hide
    // inside it. The ten minutes that follow are then measured against a quiet baseline.
    const mountCommits = app.commits()
    await act(async () => { vi.advanceTimersByTime(1000) })
    await settle()
    expect(app.commits()).toBeLessThanOrEqual(mountCommits + 1)
    expect(callsTo('/api/requirements')).toHaveLength(1)

    const fetchesBefore = calls.length
    const commitsBefore = app.commits()
    const draft = screen.getByPlaceholderText('filter requirements…') as HTMLInputElement
    draft.value = 'half-typed'

    // ten minutes. No socket frames, no user input. The prototype polled every eight seconds,
    // which would be seventy-five rebuilds in this window.
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // The clock really moved — otherwise this case passes for the wrong reason. The socket's
    // 10 s liveness ping is the ONLY timer in the system (SRV-FEAT-005), it carries no state,
    // and it is what proves the timers advanced.
    const pings = app.socket().frames().filter((f) => f.m === 'ping')
    expect(pings.length).toBeGreaterThanOrEqual(59)

    // zero fetches …
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/requirements')).toHaveLength(1)
    // … and not one component re-rendered from a timer
    expect(app.commits()).toBe(commitsBefore)
    // … and the half-typed message is byte-identical
    expect(draft.value).toBe('half-typed')
  })

  it('a_catalog_change_is_what_makes_it_refetch_and_only_that_document', async () => {
    // The positive half of the same rule: the list is live because the SERVER said something
    // changed, not because a clock went round.
    const app = await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: ROWS, total: 2, in_flight: 0, warnings: [] } },
      '/api/tickets/TKT-1': { status: 404, body: {} },
    })
    await screen.findAllByTestId('requirement-row')
    expect(callsTo('/api/requirements')).toHaveLength(1)

    await act(async () => {
      app.socket().deliver({
        m: 'catalog.changed', ts: '2026-07-27T10:00:00Z', seq: null,
        kind: 'ticket', id: 'TKT-1', path: '/tmp/fixture/docs/tickets/TKT-1.md',
        change: 'modified',
      })
    })
    await settle()
    expect(callsTo('/api/requirements')).toHaveLength(2)

    // a frame for a document this surface does not show refetches nothing
    await act(async () => {
      app.socket().deliver({
        m: 'pong', ts: '2026-07-27T10:00:10Z', seq: null,
      })
    })
    await settle()
    expect(callsTo('/api/requirements')).toHaveLength(2)
  })

  it('the_surface_that_owns_a_timer_still_does_not_poll', async () => {
    // WEB-FIX-333. `#/tickets` debounces its search field, so this client now has a timer that
    // can reach the network — and this file is what stops that becoming normal. The two are told
    // apart by what PROVOKES them and never by a name on a list: a poll fires because time
    // passed, a debounce because a person typed and then stopped. A named exemption would have
    // passed an interval sitting next to the debounce without noticing.
    vi.useFakeTimers({ shouldAdvanceTime: false })

    const app = await mountApp('#/tickets', TICKETS_ROUTES)
    await settle()
    expect(screen.getAllByTestId('epic-group')).toHaveLength(1)
    expect(callsTo('/api/tickets')).toHaveLength(1)

    const mountCommits = app.commits()
    await act(async () => { vi.advanceTimersByTime(1000) })
    await settle()
    expect(app.commits()).toBeLessThanOrEqual(mountCommits + 1)

    const fetchesBefore = calls.length
    const commitsBefore = app.commits()

    // ten minutes, untouched. Nothing typed, so nothing has settled and no timer exists to fire.
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved — the socket's 10 s ping is what proves it
    expect(app.socket().frames().filter((f) => f.m === 'ping').length)
      .toBeGreaterThanOrEqual(59)
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/tickets')).toHaveLength(1)
    expect(app.commits()).toBe(commitsBefore)

    // Now provoke it the one way it may be provoked, and let it settle.
    await act(async () => {
      fireEvent.change(screen.getByTestId('tickets-search'), { target: { value: '329' } })
      vi.advanceTimersByTime(400)
    })
    await settle()
    expect(callsTo('/api/tickets')).toHaveLength(2)

    // and then ten more minutes of nothing. A debounce fires ONCE per pause; an interval sitting
    // where the debounce is would fire again, seventy-five times, in this window.
    const afterSettled = calls.length
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()
    expect(calls.length).toBe(afterSettled)
    expect(callsTo('/api/tickets')).toHaveLength(2)
  })

  it('the_all_lanes_surface_does_not_poll_either', async () => {
    // WEB-FEAT-050. §4.14 is a "show me everything" screen and the cheap way to build one is to
    // re-read E9 on a timer — so the rule this file exists for is asserted with that surface
    // MOUNTED, not only with the requirements list. The two cases fail together against one
    // `setInterval`, which is what an overlapping negative is for.
    vi.useFakeTimers({ shouldAdvanceTime: false })

    const app = await mountApp('#/lanes', { '/api/lanes': { body: laneSet(LANES) } })
    await settle()
    await settle()
    expect(screen.getAllByTestId('lane-card')).toHaveLength(2)
    expect(callsTo('/api/lanes')).toHaveLength(1)

    const fetchesBefore = calls.length

    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved
    expect(app.socket().frames().filter((f) => f.m === 'ping').length)
      .toBeGreaterThanOrEqual(59)
    // and E9 was read once, ten minutes ago
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/lanes')).toHaveLength(1)
  })
})
