import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import { callsTo, lane, laneSet, mountApp, settle, ticketDoc } from './helpers'

/**
 * SPEC.md §6.3 `M17 lane.membership` — SRV-FIX-080.
 *
 * Every other lane frame patches a row that already exists. This is the only one that says a row
 * appeared or went away, and it exists because the TOOL claims lanes nobody asked for (F31): a
 * person watching a ticket cannot be told to reload, and this client does not poll.
 *
 * The two directions are handled by two different mechanisms on purpose, and both are asserted
 * here: a claim goes through the same unknown-lane refetch every other frame uses, and a release
 * removes the row, because the row IS held and a patch would leave a card for a lane that is gone.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const RUN = lane({ lane: 'lane/01', ticket_id: 'TKT-142', role: 'run' })
const ROLE = lane({ lane: 'lane/07', ticket_id: 'TKT-142', role: 'qa', needs_you: true })

const membership = (over: Record<string, unknown>) => ({
  m: 'lane.membership', ts: '2026-07-28T10:00:01Z', seq: null,
  lane: 'lane/07', ticket_id: 'TKT-142', workspace: 'core', ...over,
})

/** E9 answering differently on the second read, which is the whole point of the refetch. */
function routes(rows: () => Array<Record<string, unknown>>) {
  const doc = ticketDoc()
  return {
    '/api/tickets/TKT-142': { body: doc },
    '/api/tickets/TKT-142/report': { status: 404, body: { error: { code: 'not_found' } } },
    '/api/lanes': () => ({ body: laneSet(rows()) }),
  }
}

const laneNames = () =>
  screen.queryAllByTestId('lane-card').map((c) => c.getAttribute('data-lane'))

describe('6.3 M17 lane.membership', () => {
  it('a_lane_the_tool_claimed_appears_with_no_reload', async () => {
    // The journey F31 puts a person on: they are looking at the ticket, and the tool starts a
    // check on it. Nothing here is clicked, and no page is reloaded.
    let rows = [RUN]
    const app = await mountApp('#/tickets/TKT-142', routes(() => rows))
    await settle()
    await settle()
    expect(laneNames()).toEqual(['lane/01'])
    expect(callsTo('/api/lanes')).toHaveLength(1)

    rows = [RUN, ROLE]
    await act(async () => { app.socket().deliver(membership({ change: 'claimed' })) })
    await settle()
    await settle()

    // it is on the screen, and it is drawn where WEB-FEAT-079 says it belongs
    expect(laneNames()).toEqual(['lane/01', 'lane/07'])
    const card = document.querySelector('[data-testid="lane-card"][data-lane="lane/07"]')!
    expect(card.closest('[data-testid="lane-children"]')?.getAttribute('data-parent'))
      .toBe('lane/01')
    // ONE extra read of E9, on the event — not a poll, and not one per frame
    expect(callsTo('/api/lanes')).toHaveLength(2)
    expect(screen.getByTestId('needs-you-chip')).toHaveTextContent('1 lane needs you')
  })

  it('a_released_lane_leaves_the_screen_and_the_count', async () => {
    // E13 pressed somewhere else — another tab, `curl`, or the tool abandoning a role it could
    // not start. The row is HELD here, so this is the direction a patch cannot serve.
    let rows = [RUN, ROLE]
    const app = await mountApp('#/tickets/TKT-142', routes(() => rows))
    await settle()
    await settle()
    expect(laneNames()).toEqual(['lane/01', 'lane/07'])
    expect(screen.getByTestId('needs-you-chip')).toHaveTextContent('1 lane needs you')

    rows = [RUN]
    await act(async () => { app.socket().deliver(membership({ change: 'released' })) })
    await settle()

    expect(laneNames()).toEqual(['lane/01'])
    expect(screen.getByTestId('lane-count')).toHaveTextContent('1')
    // the chip stops counting a lane that no longer exists, and it did so without a refetch
    expect(screen.queryByTestId('needs-you-chip')).toBeNull()
    expect(callsTo('/api/lanes')).toHaveLength(1)
  })

  it('a_claim_for_a_lane_already_held_changes_nothing', async () => {
    // MANDATORY NEGATIVE — the frame must not be a refetch trigger in its own right.
    //
    // The browser that POSTed E12 already has the row (`E12`'s 201 IS the row), and it is
    // subscribed to the scope its own claim is announced on. A handler that re-read E9 on every
    // membership frame would turn one click into a request per open tab, which is the polling
    // this architecture exists not to have, arriving by another door.
    const rows = [RUN, ROLE]
    const app = await mountApp('#/tickets/TKT-142', routes(() => rows))
    await settle()
    await settle()
    const before = callsTo('/api/lanes').length
    const nodes = screen.getAllByTestId('lane-card')

    await act(async () => { app.socket().deliver(membership({ change: 'claimed' })) })
    await settle()
    await settle()

    expect(callsTo('/api/lanes')).toHaveLength(before)
    expect(screen.getAllByTestId('lane-card')).toEqual(nodes)   // the same nodes, not a rebuild
    expect(laneNames()).toEqual(['lane/01', 'lane/07'])
  })
})
