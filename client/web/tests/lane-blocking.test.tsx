import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, within } from '@testing-library/react'

import { lane, laneSet, mountApp, resource, ticketDoc } from './helpers'

/**
 * WEB-FIX-277 — the lane that is BLOCKING is the one told nothing.
 *
 * `waiting_for` puts the whole story on the waiter's card: the resource, its description, who
 * holds it, the place in the queue. The holder's card says `idle`, which is the vocabulary's
 * "asked, then stopped" and reads as *nothing to do here* — while releasing it is the only thing
 * that ends the wait, and the release button is on that card and no other.
 *
 * E9 already answers `resources[].queue`, so nothing on the wire changes: the client is given the
 * list of lanes waiting on each resource and drew it on none of them.
 *
 * The clock is frozen: a card's elapsed time is a subtraction against `now`, and a case measuring
 * it against the real clock measures how long the test took.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** The holder — the `run` lane, between turns, holding the resource its own `qa` lane needs. */
const HOLDER = lane({
  lane: 'lane/01', ticket_id: 'TKT-142', role: 'run', elapsed_s: 724, cost_usd: 0.74,
  state: {
    process: 'idle', motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  resources_held: ['local-ports'], waiting_for: null,
})

/** The waiter — the `qa` lane the tool started on the same ticket, queued behind the holder. */
const WAITER = lane({
  lane: 'lane/02', ticket_id: 'TKT-142', role: 'qa', elapsed_s: 12, cost_usd: 0,
  state: {
    process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  resources_held: [],
  waiting_for: { resource: 'local-ports', held_by: 'lane/01', holder_alive: true, position: 1 },
})

const PORTS = 'the two ports the local server binds'

function routes(rows: Array<Record<string, unknown>>,
                resources: Array<Record<string, unknown>>) {
  return {
    '/api/tickets/TKT-142': { body: ticketDoc() },
    '/api/tickets/TKT-142/report': { status: 404, body: { error: { code: 'not_found' } } },
    '/api/lanes': { body: laneSet(rows, resources) },
  }
}

/** One card by the lane it draws — the rail's cards carry `data-lane` and always have. */
const cardFor = (name: string) =>
  document.querySelector<HTMLElement>(`[data-testid="lane-card"][data-lane="${name}"]`)!

describe('4.4 the holder of a resource somebody is waiting for', () => {
  it('test_a_lane_holding_a_resource_others_want_says_so', async () => {
    await mountApp('#/tickets/TKT-142', routes([HOLDER, WAITER], [
      resource({ name: 'local-ports', description: PORTS,
                 held_by: 'lane/01', holder_alive: true, queue: ['lane/02'] }),
    ]))

    // The holder's own card names what is waiting on it and what the wait is FOR. Both facts are
    // needed: the lane, because that is what a person goes and looks at, and the resource, because
    // a lane can hold more than one and only one of them is the reason anybody is stuck.
    const sub = within(cardFor('lane/01')).getByTestId('lane-sub')
    expect(sub).toHaveTextContent('lane/02')
    expect(sub).toHaveTextContent('local-ports')

    // ...and the state line still says what the process is doing. The blocking fact is added to
    // the card, it does not replace the axis the card is coloured by.
    expect(cardFor('lane/01')).toHaveTextContent('idle · waiting for you')
  })

  it('test_a_lane_holding_a_resource_nobody_wants_says_nothing', async () => {
    // MANDATORY NEGATIVE. The cheap fix for the case above is a badge on every lane that holds
    // anything — which is noise on the ordinary case (a lane holds the board for its whole run,
    // alone, every run) and trains the reader to ignore the one time it means something.
    //
    // The body is the same one with an empty queue, and the waiter is running rather than queued:
    // nobody waits for a resource whose queue is empty, and a fixture that said both would be a
    // fixture of a server that cannot exist.
    await mountApp('#/tickets/TKT-142', routes(
      [HOLDER, lane({ lane: 'lane/02', ticket_id: 'TKT-142', role: 'qa', waiting_for: null })],
      [resource({ name: 'local-ports', description: PORTS,
                  held_by: 'lane/01', holder_alive: true, queue: [] })],
    ))

    expect(within(cardFor('lane/01')).queryByTestId('lane-sub')).toBeNull()
    // `local-ports` is the resource, and the card must not name it while nobody wants it. The
    // state line's own words are NOT asserted against: `idle · waiting for you` is the vocabulary's
    // and contains "waiting" on every idle card in the product.
    expect(cardFor('lane/01')).not.toHaveTextContent('local-ports')
  })

  it('test_the_waiter_still_says_what_it_waits_for', async () => {
    // This ticket adds a second reader; it does not move the message. WEB-FEAT-044's sub-line on
    // the queued card is unchanged, description and holder and position included.
    await mountApp('#/tickets/TKT-142', routes([HOLDER, WAITER], [
      resource({ name: 'local-ports', description: PORTS,
                 held_by: 'lane/01', holder_alive: true, queue: ['lane/02'] }),
    ]))

    const sub = within(cardFor('lane/02')).getByTestId('lane-sub')
    expect(sub).toHaveTextContent('queued for local-ports')
    expect(sub).toHaveTextContent(PORTS)
    expect(sub).toHaveTextContent('held by lane/01')
    expect(sub).toHaveTextContent('#1')
  })
})

describe('4.14 every lane in the repository', () => {
  it('test_the_blocking_line_is_drawn_on_both_surfaces', async () => {
    // §4.14 draws its own card props off the same two functions the rail uses, and a line that
    // only the rail has is a line the surface built for looking at every lane does not have —
    // which is the surface a person opens when they do not know which ticket is stuck.
    await mountApp('#/lanes', routes([HOLDER, WAITER], [
      resource({ name: 'local-ports', description: PORTS,
                 held_by: 'lane/01', holder_alive: true, queue: ['lane/02'] }),
    ]))

    const sub = within(cardFor('lane/01')).getByTestId('lane-sub')
    expect(sub).toHaveTextContent('lane/02')
    expect(sub).toHaveTextContent('local-ports')
    expect(screen.getAllByTestId('lane-card').length).toBeGreaterThan(1)
  })
})
