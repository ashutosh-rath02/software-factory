import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import {
  callsTo, lane, mountApp, requirement, settle, ticketDoc, ticketRoutes, type Routes,
} from './helpers'
import { keyFromHint, press } from './keys'
import { docLane, idle } from './session'
import { NEEDS_YOU_KEY_CODE, NEEDS_YOU_KEY_HINT } from '../src/components/TopBar'

/**
 * SPEC.md §4.1 — `N lanes need you`, and the two rules that make it worth looking at.
 *
 * The count is the SERVER's: `needs_you` is derived per lane from §3.3's seven clauses, and a
 * browser that recomputed it would be a second implementation of the definition that matters
 * most. And it is ABSENT at zero, never a `0` — a chip reading `0 lanes need you` teaches a
 * person to stop looking at that corner of the screen.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const TWO_OF_FIVE = [
  lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: true }),
  lane({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: false }),
  lane({ lane: 'lane/03', ticket_id: 'TKT-151', needs_you: true }),
  lane({ lane: 'lane/04', ticket_id: 'TKT-139', needs_you: false }),
  lane({ lane: 'lane/05', ticket_id: 'TKT-133', needs_you: false }),
]

describe('4.1 the `needs you` chip', () => {
  it('the_counter_is_the_servers_number', async () => {
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), TWO_OF_FIVE))
    await settle()
    await settle()
    screen.getByTestId('needs-you-chip')

    // E9's own count, verbatim — two of five, and neither the five nor a recomputation
    expect(screen.getByTestId('needs-you-chip')).toHaveTextContent('2 lanes need you')

    // M2 is what keeps it live; nothing polls for it
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:01Z', seq: null,
        lane: 'lane/02', ticket_id: 'TKT-147',
        state: {
          process: 'idle', motion: { value: null, silent_for_s: 0, since: null },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        waiting_for: null, needs_you: true,
      })
    })
    await settle()
    expect(screen.getByTestId('needs-you-chip')).toHaveTextContent('3 lanes need you')

    // socket down: the count stays at its last known value and DIMS, because a stale count that
    // admits it is stale is more useful than a blank
    await act(async () => { app.socket().onclose?.() })
    await settle()
    const dimmed = screen.getByTestId('needs-you-chip')
    expect(dimmed).toHaveTextContent('3 lanes need you')
    expect(dimmed.getAttribute('data-dim')).toBe('true')
  })

  it('at_zero_it_is_absent_not_a_zero', async () => {
    const quiet = [
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
      lane({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: false }),
    ]
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), quiet))
    await settle()
    await settle()
    screen.getByTestId('lane-rail')

    expect(screen.queryByTestId('needs-you-chip')).toBeNull()
    expect(screen.getByTestId('top-bar').textContent).not.toMatch(/need you/)
  })

  /**
   * WEB-FEAT-079 — a lane drawn UNDER another one is still one of the lanes this chip counts.
   *
   * The chip is the one thing on any screen that can say a lane needs a person somewhere else, and
   * `needs_you` is the server's own word over `E9`'s whole set (§3.3). A nesting rule that dropped
   * a role lane out of that set, or out of the destination it navigates to, would take the count
   * and the screen apart — which is the disagreement §4.4 says this product exists to prevent.
   */
  it('a_nested_role_lane_is_still_counted_and_still_reachable', async () => {
    const nested = [
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
      lane({ lane: 'lane/04', ticket_id: 'TKT-142', role: 'qa', needs_you: true }),
    ]
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), nested))
    await settle()
    await settle()

    const chip = screen.getByTestId('needs-you-chip')
    expect(chip).toHaveTextContent('1 lane needs you')
    expect(chip.getAttribute('href')).toBe('#/tickets/TKT-142')

    // and the lane it counted is on that screen, drawn under the run lane, marked as needing one
    const card = document.querySelector('[data-testid="lane-card"][data-lane="lane/04"]')!
    expect(card.closest('[data-testid="lane-children"]')!.getAttribute('data-parent'))
      .toBe('lane/01')
    expect(card.getAttribute('data-alarm')).toBe('true')
    expect(screen.getByTestId('lane-count')).toHaveTextContent('2')
  })
})

/* --------------------------------------------------------------------------------------------- *
 * WEB-FEAT-365 — one key, and it goes where the chip goes.
 *
 * §4.1's rule is the whole specification and it is unchanged: the count and the destination come
 * out of ONE answer, `E9`'s lane set in `E9`'s order. The key is a second TRIGGER of that one
 * derivation and never a second derivation, so every case below reads the destination off the
 * anchor the chip already renders rather than restating §4.1's table in an assertion that could
 * only agree with the component.
 * --------------------------------------------------------------------------------------------- */

/**
 * One of five, and it is the THIRD row.
 *
 * The lane that needs a person is deliberately not the one at the top of `E9`'s answer, so *the
 * destination* and *the first lane in the list* are forced apart before anything is asserted: a
 * fixture in which they are the same row passes identically whichever of the two the code read.
 */
const ONE_OF_FIVE = [
  lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
  lane({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: false }),
  lane({ lane: 'lane/03', ticket_id: 'TKT-151', needs_you: true }),
  lane({ lane: 'lane/04', ticket_id: 'TKT-139', needs_you: false }),
  lane({ lane: 'lane/05', ticket_id: 'TKT-133', needs_you: false }),
]

/** The same five, three of which need a person — a collection whose members disagree. */
const THREE_OF_FIVE = [
  lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: true }),
  lane({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: false }),
  lane({ lane: 'lane/03', ticket_id: 'TKT-151', needs_you: true }),
  lane({ lane: 'lane/04', ticket_id: 'TKT-139', needs_you: false }),
  lane({ lane: 'lane/05', ticket_id: 'TKT-133', needs_you: true }),
]

/** `Alt+N` as §1 writes it. The one place these cases type the binding rather than read it. */
const ALT_N: KeyboardEventInit = {
  code: 'KeyN', key: 'n', altKey: true, ctrlKey: false, metaKey: false, shiftKey: false,
}

/** The ticket the destination can land on, so a key that arrives arrives on a real surface. */
const routesFor = (rows: Array<Record<string, unknown>>, over: Routes = {}): Routes => ({
  ...ticketRoutes(ticketDoc(), rows),
  '/api/tickets/TKT-151': { body: ticketDoc({ id: 'TKT-151', title: 'The lane that needs you' }) },
  '/api/tickets/TKT-151/report': { status: 404, body: { error: { code: 'not_found' } } },
  ...over,
})

const chip = () => screen.getByTestId('needs-you-chip')
const destination = () => chip().getAttribute('href')
const ticketOf = (rows: Array<Record<string, unknown>>) =>
  String(rows.filter((row) => row.needs_you)[0]?.ticket_id)

describe('4.1 · WEB-FEAT-365 the key that goes where the chip goes', () => {
  /**
   * Criterion 1 — one case, both collections, and the assertion is against the ANCHOR.
   *
   * `#/lanes` and `#/tickets/<id>` are two different answers out of one derivation, so a case
   * that saw only one of them could not tell a second derivation from the first.
   */
  it('alt_n_goes_where_the_chip_goes', async () => {
    await mountApp('#/tickets/TKT-142', routesFor(ONE_OF_FIVE))
    await settle()
    await settle()

    expect(chip()).toHaveTextContent('1 lane needs you')
    const one = destination()
    // §1's own words for this half: the one ticket whose lane needs them — read off the fixture,
    // never typed, so the row that needs a person is the row the address names
    expect(one).toBe(`#/tickets/${ticketOf(ONE_OF_FIVE)}`)

    const arrived = await press(document.body, ALT_N)
    expect(location.hash).toBe(one)
    expect(arrived.defaultPrevented).toBe(true)

    cleanup()

    // ── the same five, three of them needing a person: the chip cannot name them, and neither
    //    can the key
    await mountApp('#/tickets/TKT-142', routesFor(THREE_OF_FIVE))
    await settle()
    await settle()

    expect(chip()).toHaveTextContent('3 lanes need you')
    const many = destination()
    expect(many).toBe('#/lanes')

    await press(document.body, ALT_N)
    expect(location.hash).toBe(many)
  })

  /**
   * Criterion 2 — the hint on the chip IS the key that works.
   *
   * The event is built from the text the product DREW, so a hint that said one thing while the
   * handler bound another is a red rather than two green cases that never met.
   */
  it('the_hint_on_the_chip_is_the_key_that_works', async () => {
    await mountApp('#/tickets/TKT-142', routesFor(ONE_OF_FIVE))
    await settle()
    await settle()

    const drawn = screen.getByTestId('needs-you-key')
    const hint = (drawn.textContent ?? '').trim()

    // inside the chip's own anchor — a hint drawn elsewhere on the bar is a hint about nothing
    expect(drawn.closest('[data-testid="needs-you-chip"]')).toBe(chip())
    expect(hint).not.toBe('')

    // one exported constant, and the code it binds comes out of the same module
    expect(hint).toBe(NEEDS_YOU_KEY_HINT)
    expect(keyFromHint(hint).code).toBe(NEEDS_YOU_KEY_CODE)

    // discoverable without pressing anything, which is what the hover text is for
    expect(chip().getAttribute('title')).toContain('alt+N')

    // and the key the screen advertises is the key that arrives
    const where = destination()
    const arrived = await press(document.body, keyFromHint(hint))
    expect(location.hash).toBe(where)
    expect(arrived.defaultPrevented).toBe(true)
  })

  /**
   * Criterion 3 — it stays live, on `M2`, with no `E9` refetch.
   *
   * The chip's count has been live since WEB-FEAT-013; what this asserts is that the DESTINATION
   * is, because a key bound once to a value read once would send a person to a lane that stopped
   * needing them ten minutes ago.
   */
  it('alt_n_follows_the_socket', async () => {
    const rows = [
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
      lane({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: false }),
      lane({ lane: 'lane/03', ticket_id: 'TKT-151', needs_you: true }),
    ]
    const app = await mountApp('#/tickets/TKT-142', routesFor(rows))
    await settle()
    await settle()

    const reads = callsTo('/api/lanes').length
    expect(reads).toBeGreaterThan(0)
    expect(destination()).toBe('#/tickets/TKT-151')
    await press(document.body, ALT_N)
    expect(location.hash).toBe('#/tickets/TKT-151')

    // M2 — a SECOND lane turns, and it is the frame that changes where the key goes
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:01Z', seq: null,
        lane: 'lane/02', ticket_id: 'TKT-147',
        state: {
          process: 'idle', motion: { value: null, silent_for_s: 0, since: null },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        waiting_for: null, needs_you: true,
      })
    })
    await settle()

    expect(chip()).toHaveTextContent('2 lanes need you')
    expect(destination()).toBe('#/lanes')
    await press(document.body, ALT_N)
    expect(location.hash).toBe('#/lanes')

    // and E9 was never asked again — the frame is the whole of what changed
    expect(callsTo('/api/lanes').length).toBe(reads)
  })

  /**
   * Criterion 4 — MANDATORY NEGATIVE. In an editable element the key TYPES.
   *
   * Each half ends with the same key pressed from outside the box, which is the control: without
   * it, `the hash did not change` is satisfied by a feature that does not exist.
   */
  it('alt_n_in_an_editable_element_does_not_navigate', async () => {
    // ── a <textarea>: the composer, on the session tab of a ticket whose lane needs a person
    const rows = [idle({ needs_you: true })]
    await mountApp('#/tickets/TKT-142/session',
      { ...ticketRoutes(ticketDoc({ lane: docLane(rows) }), rows), ...routesFor(rows) })
    await settle()
    await settle()

    const box = screen.getByTestId('composer-draft')
    const where = destination()
    expect(where).toBe('#/tickets/TKT-142')
    expect(location.hash).toBe('#/tickets/TKT-142/session')

    const typed = await press(box, ALT_N)
    expect(location.hash).toBe('#/tickets/TKT-142/session')
    expect(typed.defaultPrevented).toBe(false)

    // the control: the same key, from outside the draft, on the same screen
    await press(document.body, ALT_N)
    expect(location.hash).toBe(where)

    cleanup()

    // ── an <input>: the requirements filter, which is the client's own
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: [requirement()], total: 1, in_flight: 0,
                                     warnings: [] } },
      ...routesFor(ONE_OF_FIVE),
    })
    await settle()
    await settle()

    const filter = screen.getByLabelText('filter requirements')
    const goes = destination()
    const inField = await press(filter, ALT_N)
    expect(location.hash).toBe('#/requirements')
    expect(inField.defaultPrevented).toBe(false)

    // ── and a contenteditable. This client ships none, so the element is a real one put into the
    //    live document: the handler reads `e.target`, and what it must refuse is the KIND.
    //
    //    jsdom does not implement `contenteditable` — the attribute is stored and
    //    `isContentEditable` is never defined — so the property is given the value a browser
    //    would compute, exactly as `session.tsx`'s `measure()` gives jsdom's zero-sized boxes a
    //    height to read. The REAL property is driven in a real Chrome at tier 3; this case
    //    asserts the product reads it, and the browser is what says the browser agrees.
    const editable = document.createElement('div')
    editable.setAttribute('contenteditable', 'true')
    Object.defineProperty(editable, 'isContentEditable', { value: true, configurable: true })
    document.body.appendChild(editable)
    const inProse = await press(editable, ALT_N)
    expect(location.hash).toBe('#/requirements')
    expect(inProse.defaultPrevented).toBe(false)
    editable.remove()

    // the control again, on this surface: nothing above was true because the key is dead
    await press(document.body, ALT_N)
    expect(location.hash).toBe(goes)
  })

  /**
   * Criterion 5 — MANDATORY NEGATIVE. With nothing needing anyone, nothing happens; and the key
   * is `Alt+N` and not `N`, `Ctrl+Alt+N` or `Meta+Alt+N`.
   *
   * The modifier half runs on the collection where the key DOES work, because that is the only
   * fixture on which a wrong-modifier press can be told from a dead binding.
   */
  it('alt_n_with_nothing_needing_you_does_nothing', async () => {
    const quiet = [
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
      lane({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: false }),
    ]
    await mountApp('#/tickets/TKT-142', routesFor(quiet))
    await settle()
    await settle()

    // §4.1's Empty rule — the chip is absent, so there is nowhere for the key to go
    expect(screen.queryByTestId('needs-you-chip')).toBeNull()
    const nothing = await press(document.body, ALT_N)
    expect(location.hash).toBe('#/tickets/TKT-142')
    expect(nothing.defaultPrevented).toBe(false)

    cleanup()

    // ── the same three presses on a screen where `Alt+N` DOES go somewhere
    await mountApp('#/tickets/TKT-142', routesFor(ONE_OF_FIVE))
    await settle()
    await settle()
    const where = destination()
    expect(where).toBe(`#/tickets/${ticketOf(ONE_OF_FIVE)}`)

    for (const [what, init] of [
      ['N with nothing held', { ...ALT_N, altKey: false }],
      ['Ctrl+Alt+N', { ...ALT_N, ctrlKey: true }],
      ['Meta+Alt+N', { ...ALT_N, metaKey: true }],
    ] as Array<[string, KeyboardEventInit]>) {
      const event = await press(document.body, init)
      expect(location.hash, what).toBe('#/tickets/TKT-142')
      expect(event.defaultPrevented, what).toBe(false)
    }

    // and the control, last: the binding these three missed is live
    await press(document.body, ALT_N)
    expect(location.hash).toBe(where)
  })
})
