import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, within } from '@testing-library/react'

import { callsTo, FakeSocket, mountApp, settle, ticketDoc, ticketRoutes } from './helpers'
import { deliver, docLane, LANE, live, running } from './session'

/**
 * WEB-FIX-037 · SPEC.md §6.2 M14 — two subscriptions on ONE connection.
 *
 * The ticket screen sends two `subscribe` frames: `TicketPage` asks for `{ticket:<id>}` and the
 * SESSION tab's `LiveSession` asks for `{lane:<name>}`. The schema says a surface "gets that
 * scope's deltas and no other scope's", which leaves open what a SECOND subscribe does to the
 * first — narrow it, replace it, or multiplex.
 *
 * The server multiplexes: `Connection::scopes_` is a set, `subscribe_` inserts into it and erases
 * nothing, and `Socket::publish*` walks the frame's scope keys and delivers once per connection
 * that holds any of them (`factory/server/app/api/socket.cpp`). Established from a real socket as
 * well as from the C++, and written up in this ticket's report.
 *
 * This case asserts the OUTCOME the two surfaces need, whatever the mechanism: a page holding both
 * scopes receives the frames of BOTH, in either order, and neither one starves the other.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const laneState = (value: 'producing' | 'silent') => ({
  m: 'lane.state', ts: '2026-07-28T10:00:30Z', seq: null, lane: LANE, ticket_id: 'TKT-142',
  state: {
    process: 'running',
    motion: value === 'silent'
      ? { value, silent_for_s: 252, since: '2026-07-28T09:55:48Z' }
      : { value, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: value === 'silent',
  },
  waiting_for: null, needs_you: value === 'silent',
})

const catalogChanged = {
  m: 'catalog.changed', ts: '2026-07-28T10:00:31Z', seq: null,
  kind: 'ticket', id: 'TKT-142', path: '/tmp/fixture/docs/tickets/TKT-142.md',
  change: 'modified',
}

describe('6.2 M14 · two scopes, one connection', () => {
  it('two_subscriptions_on_one_page_do_not_starve_each_other', async () => {
    const rows = [running()]
    const app = await mountApp('#/tickets/TKT-142/session',
      ticketRoutes(ticketDoc({ lane: docLane(rows) }), rows))
    await settle()
    await settle()

    // both scopes went out, on ONE socket — the order is React's (a child's effect runs before
    // its parent's) and is not what this case is about
    const subscribes = app.socket().frames().filter((f) => f.m === 'subscribe')
    expect(subscribes.map((f) => JSON.stringify(f.scope)))
      .toContain(JSON.stringify({ ticket: 'TKT-142' }))
    expect(subscribes.map((f) => JSON.stringify(f.scope)))
      .toContain(JSON.stringify({ lane: LANE }))
    expect(FakeSocket.all).toHaveLength(1)           // one connection, not one per surface

    const ticketReads = callsTo('/api/tickets/TKT-142').length

    // ── the LANE scope's delta reaches the composer …
    await deliver(app, laneState('silent'))
    expect(screen.getByTestId('composer-button').getAttribute('data-mode')).toBe('send')

    // … and the TICKET scope's delta still reaches the document, AFTER the lane subscribed
    await deliver(app, catalogChanged)
    expect(callsTo('/api/tickets/TKT-142')).toHaveLength(ticketReads + 1)

    // … and the lane scope is not starved by the ticket one either: the next lane frame lands
    await deliver(app, laneState('producing'))
    expect(screen.getByTestId('composer-button').getAttribute('data-mode')).toBe('stop')

    // … and the lane's own transcript, which only the lane scope carries, arrives with it
    await deliver(app, live(1, 'prose', { text: 'reading the build task now' }))
    expect(screen.getAllByTestId('entry')).toHaveLength(1)

    // one more of each, interleaved, so the assertion is about a steady state and not an order
    await deliver(app, catalogChanged, laneState('silent'))
    expect(callsTo('/api/tickets/TKT-142')).toHaveLength(ticketReads + 2)
    expect(screen.getByTestId('composer-button').getAttribute('data-mode')).toBe('send')
  })
})

/**
 * `WEB-FIX-088` — §4.6's Empty is a fact about RUNS, and it was drawn on the absence of a LANE.
 *
 * `SPEC.md` §4.6 gives the block to *"a lane with no run yet"*. The client draws it whenever there
 * is no lane — which is the ordinary end of every finished piece of work, because a released lane
 * leaves the runs on disk and no lane at all. The audit photographed the result: `2 runs` in the
 * identity row, `RUNS · 2`, `CRASHED MID-TURN` on the process chip, and underneath, in a dashed
 * block, *"Nothing has run on this ticket yet."*
 *
 * Every text assertion about that block passed. It renders, it has the right heading, it carries
 * §4.6's exact copy — and only a reader holding the count in the same glance can see that the
 * sentence is false. That is `_TEMPLATE.md` §2's rule about a number and the list it describes,
 * arriving from the other direction: here the LIST is right and the SENTENCE about it is wrong.
 */
describe('4.6 · the empty block is about runs, not about lanes', () => {
  const NO_LANE = 'Nothing has run on this ticket yet.'

  function mountNoLane(runs: unknown[], count: number) {
    return mountApp('#/tickets/TKT-142/session', {
      ...ticketRoutes(ticketDoc({ lane: null,
        runs: { count, duration_s: count * 60, cost_usd: count } }), []),
      '/api/tickets/TKT-142/runs': { body: { runs } },
    })
  }

  function aRun(over: Record<string, unknown> = {}) {
    return {
      run_id: 'run-1', n: 1, started_iso: '2026-07-28T11:20:44Z',
      ended_iso: '2026-07-28T11:22:00Z', outcome: 'completed', stage: 0,
      duration_s: 76, cost_usd: 0.62, lane: 'lane/01', session_id: 'sess-1', ...over,
    }
  }

  it('test_a_ticket_with_runs_and_no_lane_does_not_say_nothing_has_run', async () => {
    await mountNoLane([aRun(), aRun({ run_id: 'run-2', n: 2 })], 2)
    await settle()
    await settle()

    // The whole surface, not the block: the sentence is false wherever on the page it appears,
    // and the rail says it too.
    expect(screen.getByTestId('surface').textContent).not.toContain(NO_LANE)
    // …and it says what IS true, pointing at where the runs can be read.
    expect(screen.getByTestId('session-empty').textContent).toMatch(/2 runs/)
    expect(screen.getByTestId('session-empty').textContent).toMatch(/RUNS/)
    // the way out of the empty state is untouched — §4.7's claim control is still the point
    within(screen.getByTestId('composer')).getByTestId('claim-lane')
  })

  it('test_a_ticket_with_no_runs_still_says_nothing_has_run', async () => {
    // MANDATORY NEGATIVE. The cheap fix is to delete the sentence; this is what stops it. §4.6's
    // copy is asserted verbatim, on the ticket the sentence is TRUE about.
    await mountNoLane([], 0)
    await settle()
    await settle()

    expect(screen.getByTestId('session-empty')).toHaveTextContent('NO LANE ASSIGNED')
    expect(screen.getByTestId('session-empty').textContent).toContain(NO_LANE)
    expect(screen.getByTestId('rail-empty').textContent).toContain(NO_LANE)
  })
})
