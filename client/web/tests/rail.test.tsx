import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, within } from '@testing-library/react'

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import {
  calls, callsTo, lane, laneSet, mountApp, settle, ticketDoc, ticketRoutes,
} from './helpers'

/**
 * SPEC.md §4.4 — the lane rail, rendering E9's answer ABOUT THE TICKET THIS SCREEN IS ABOUT.
 *
 * WEB-FEAT-050 rewrote §4.4. The rail used to list every lane in the repository, and the file this
 * one was renamed from (`lane-rail.test.tsx`) asserted exactly that. What the global rail was
 * carrying — a lane that needs a person is visible from wherever you are — is now §4.1's chip and
 * §4.14's surface, and the last case in this file is the mandatory negative that says so.
 *
 * The clock is frozen in every case: a card's elapsed time is a subtraction against `now`, and a
 * case measuring it against the real clock measures how long the test took.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** Silent since 10:00:00 less 130 s — past SPEC.md §3.3's 120 s. */
const SILENT_SINCE = '2026-07-28T09:57:50Z'

/**
 * Four lanes over THREE tickets, and two of them on one ticket.
 *
 * The shape is the point. A set where every ticket has exactly one lane cannot tell a rail that
 * filters from a rail that shows its first row, and a set where only one ticket has any cannot
 * tell either of those from a rail that still shows everything.
 */
const ROWS = [
  lane({
    lane: 'lane/01', ticket_id: 'TKT-142', elapsed_s: 724, cost_usd: 0.74, progress_pct: 50,
    stage: { index: 4, total: 8, round: 1 },
    state: {
      process: 'running',
      motion: { value: 'silent', silent_for_s: 130, since: SILENT_SINCE },
      evidence: 'nothing_claimed', blocked: null, alarm: true,
    },
    needs_you: true,
  }),
  lane({
    lane: 'lane/02', ticket_id: 'TKT-147', elapsed_s: 252, cost_usd: 0.22, progress_pct: 75,
    stage: { index: 6, total: 8, round: 1 },
    needs_you: false,
  }),
  lane({
    lane: 'lane/03', ticket_id: 'TKT-151', elapsed_s: 1868, cost_usd: 1.04, progress_pct: 25,
    stage: { index: 2, total: 8, round: 1 },
    state: {
      process: 'idle',
      motion: { value: null, silent_for_s: 0, since: null },
      evidence: 'nothing_claimed', blocked: null, alarm: false,
    },
    needs_you: true,
  }),
  // F31 — a role started a lane of its own on a ticket that already had one
  lane({
    lane: 'lane/04', ticket_id: 'TKT-142', role: 'reviewer', elapsed_s: 300, cost_usd: 0.31,
    progress_pct: 75, stage: { index: 6, total: 8, round: 1 },
    needs_you: false,
  }),
]

const laneNames = () =>
  screen.queryAllByTestId('lane-card').map((c) => c.getAttribute('data-lane'))

/** One card by the lane it draws — the rail's cards carry `data-lane` and always have. */
const cardFor = (name: string) =>
  document.querySelector<HTMLElement>(`[data-testid="lane-card"][data-lane="${name}"]`)!

describe('4.4 the lane rail', () => {
  it('the_rail_shows_only_this_tickets_lanes', async () => {
    // TKT-147 has one lane of its own and the repository holds four. The count beside LANES is
    // ONE, and the three cards belonging to other tickets are not on this screen at all.
    await mountApp('#/tickets/TKT-147',
      ticketRoutes(ticketDoc({ id: 'TKT-147', title: 'Propagate the hash to deploy' }), ROWS))
    await settle()
    await settle()
    screen.getByTestId('lane-rail')

    expect(laneNames()).toEqual(['lane/02'])
    expect(screen.getByTestId('lane-count')).toHaveTextContent('1')
    // and not the total, which is the number this used to read
    expect(screen.getByTestId('lane-count').textContent).not.toBe('4')

    const rail = screen.getByTestId('lane-rail')
    expect(rail).not.toHaveTextContent('TKT-142')
    expect(rail).not.toHaveTextContent('TKT-151')
    expect(rail).toHaveTextContent('TKT-147')

    // the one card it does show is this ticket's, so it is the selected one
    const card = screen.getByTestId('lane-card')
    expect(card.getAttribute('data-selected')).toBe('true')
  })

  it('two_lanes_on_one_ticket_both_render', async () => {
    // The filter is BY TICKET and not "the one lane": a role starts a second lane on a ticket that
    // already has one (F31), and a rail that reduced to the first row would hide it.
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()

    expect(laneNames()).toEqual(['lane/01', 'lane/04'])       // E9's order, both of them
    expect(screen.getByTestId('lane-count')).toHaveTextContent('2')
    expect(screen.queryByTestId('rail-empty')).toBeNull()
  })

  it('a_ticket_with_no_lane_still_offers_to_start_one', async () => {
    // THE CASE A NAIVE FILTER BREAKS. The empty block used to be chosen by `lanes.length === 0`
    // over the GLOBAL set, so on a repository holding four lanes this ticket would have shown
    // somebody else's card and no way to start its own. The emptiness is a fact about the TICKET.
    // A ticket NOBODY HAS RUN — `ticketDoc`'s default carries four runs, and this case asserts
    // §4.6's "Nothing has run on this ticket yet." verbatim, which is a sentence about runs
    // (`WEB-FIX-088`). It was asserting it on a ticket the sentence was false about, and the
    // assertion is unchanged; the fixture is now a ticket it is true of.
    // On a non-SESSION tab the rail owns the one shared claim control.
    await mountApp('#/tickets/TKT-160/ticket',
      ticketRoutes(ticketDoc({ id: 'TKT-160', title: 'An orphan with no requirement',
                               runs: { count: 0, duration_s: 0, cost_usd: 0 } }), ROWS))
    await settle()
    await settle()

    expect(screen.queryAllByTestId('lane-card')).toHaveLength(0)
    const empty = screen.getByTestId('rail-empty')
    expect(empty).toHaveTextContent('NO LANE ASSIGNED')
    expect(empty).toHaveTextContent('Nothing has run on this ticket yet.')
    // and the control that fixes it, which is the whole reason the block is worth keeping
    within(empty).getByTestId('claim-lane')
    expect(screen.getByTestId('lane-count')).toHaveTextContent('0')
  })

  it('a_second_lane_card_opens_that_lane', async () => {
    // Two lanes on one ticket (a `qa` lane beside the `run` lane): each card links to its OWN
    // lane, keeping the open tab, and the address's lane is the one drawn selected.
    await mountApp('#/tickets/TKT-142/session?lane=lane%2F04', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()
    const cards = screen.getAllByTestId('lane-card')
    expect(cards.map((c) => c.getAttribute('data-selected'))).toEqual(['false', 'true'])
    const links = cards.map((c) => c.closest('a')?.getAttribute('href'))
    expect(links).toEqual(['#/tickets/TKT-142/session?lane=lane%2F01',
      '#/tickets/TKT-142/session?lane=lane%2F04'])
  })

  it('renders_one_card_per_lane_from_E9', async () => {
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()
    screen.getByTestId('lane-rail')

    const cards = screen.getAllByTestId('lane-card')
    expect(cards).toHaveLength(2)

    // everything the design draws on a card (Dashboard.dc.html)
    const first = cards[0]
    expect(first).toHaveTextContent('lane/01')
    expect(first).toHaveTextContent('TKT-142')
    expect(first).toHaveTextContent('running · silent')
    expect(within(first).getByTestId('lane-elapsed')).toHaveTextContent('12m 04s')
    expect(within(first).getByTestId('lane-cost')).toHaveTextContent('$0.74')
    expect(within(first).getByTestId('lane-bar').getAttribute('data-pct')).toBe('50')

    // the stage NAME, because every card in this rail now belongs to the open ticket and the rail
    // has read that ticket's own sequence. SPEC.md §4.3's rule is why it still does not guess
    // anywhere else: 4/8 is never compared against 2/4.
    expect(within(first).getByTestId('lane-stage')).toHaveTextContent('BUILD')
    expect(within(cards[1]).getByTestId('lane-stage')).toHaveTextContent('REVIEW')

    // a lane that needs a person is tinted; so is one that is this ticket's, which is the design's
    // own second reason for the same treatment
    expect(cards.map((c) => c.getAttribute('data-alarm'))).toEqual(['true', 'true'])
    // only the lane the screen is SHOWING is selected — the first, with no `?lane=` in the address
    expect(cards.map((c) => c.getAttribute('data-selected'))).toEqual(['true', 'false'])

    // ── the rail patches in place on M2 …
    const before = calls.length
    const node = cards[1]
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:01Z', seq: null,
        lane: 'lane/04', ticket_id: 'TKT-142',
        state: {
          process: 'crashed',
          motion: { value: null, silent_for_s: 0, since: null },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        waiting_for: null, needs_you: true,
      })
    })
    await settle()

    const patched = screen.getAllByTestId('lane-card')
    expect(patched[1]).toBe(node)                       // the same element, not a rebuilt list
    expect(patched[1]).toHaveTextContent('crashed mid-turn')
    expect(patched[1].getAttribute('data-alarm')).toBe('true')

    // … and on M4, and neither of them refetched anything
    await act(async () => {
      app.socket().deliver({
        m: 'lane.metrics', ts: '2026-07-28T10:00:02Z', seq: null,
        lane: 'lane/04', elapsed_s: 300, cost_usd: 0.31, turn: { n: 2, cost_usd: 0.09 },
      })
    })
    await settle()
    expect(within(patched[1]).getByTestId('lane-cost')).toHaveTextContent('$0.31')
    expect(within(patched[1]).getByTestId('lane-elapsed')).toHaveTextContent('5m 00s')
    expect(calls.length).toBe(before)
    expect(callsTo('/api/lanes')).toHaveLength(1)
  })

  it('the_clock_ticks_without_the_socket', async () => {
    // SPEC.md §4.4 and F19's client half. A counter that needs a frame per second dies the
    // moment the socket does — which is the exact moment a person needs to read it.
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()
    screen.getByTestId('lane-rail')

    const first = screen.getAllByTestId('lane-card')[0]
    expect(within(first).getByTestId('lane-elapsed')).toHaveTextContent('12m 04s')
    expect(within(first).getByTestId('lane-silence')).toHaveTextContent('2m 10s')

    const framesBefore = app.socket().sent.length
    const fetchesBefore = calls.length

    // sixty seconds, and NOT ONE FRAME arrives
    await act(async () => { vi.advanceTimersByTime(60_000) })
    await settle()

    expect(within(first).getByTestId('lane-elapsed')).toHaveTextContent('13m 04s')
    expect(within(first).getByTestId('lane-silence')).toHaveTextContent('3m 10s')
    expect(calls.length).toBe(fetchesBefore)

    // the only thing the client sent in that minute is the socket's own liveness ping — no
    // frame CARRIED the new numbers, which is the whole of what this case asserts
    const sent = app.socket().frames().slice(framesBefore)
    expect(sent.every((f) => f.m === 'ping')).toBe(true)
  })

  it('nothing_is_rebuilt_on_a_timer', async () => {
    // MANDATORY NEGATIVE. Ten minutes, no frames: zero fetches, and no component re-rendered
    // except the client-side clocks — whose TEXT may change while their IDENTITY does not.
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()
    screen.getByTestId('lane-rail')

    const before = Array.from(document.querySelectorAll('body *'))
    const textBefore = new Map(before.map((n) => [n, n.textContent]))
    const fetchesBefore = calls.length
    const commitsBefore = app.commits()

    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved — otherwise this case passes for the wrong reason. The socket's
    // 10 s liveness ping is the only OTHER timer in the system and it carries no state.
    const pings = app.socket().frames().filter((f) => f.m === 'ping')
    expect(pings.length).toBeGreaterThanOrEqual(59)

    // zero fetches. The prototype polled every eight seconds: seventy-five in this window.
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/lanes')).toHaveLength(1)
    expect(callsTo('/api/tickets/TKT-142')).toHaveLength(1)

    // nothing was remounted: the same DOM nodes, in the same order
    const after = Array.from(document.querySelectorAll('body *'))
    expect(after).toEqual(before)

    // and the only text that moved belongs to a clock, or to something containing one
    const moved = after.filter((n) => n.textContent !== textBefore.get(n))
    const unexplained = moved.filter(
      (n) => !(n.matches('[data-clock]') || n.querySelector('[data-clock]')))
    expect(unexplained.map((n) => `${n.tagName}.${n.className}`)).toEqual([])
    expect(moved.some((n) => n.matches('[data-clock]'))).toBe(true)

    // one shared ticker, one batched commit per second — nothing renders oftener than that
    expect(app.commits() - commitsBefore).toBeLessThanOrEqual(610)
  })

  /**
   * SECOND MANDATORY NEGATIVE — and it is the one that matters.
   *
   * It asserts what the filter is ALLOWED TO COST. Filtering the rail takes away the only thing on
   * this screen that could ever have shown a lane belonging to a ticket nobody has open; §4.1's
   * chip is what picks that job up. If this case can still pass with the chip deleted, then the
   * filter took something away and gave nothing back, and the change is wrong.
   */
  it('a_lane_that_needs_you_on_another_ticket_is_still_reachable', async () => {
    // TKT-147's own lane needs nobody. TKT-142's and TKT-151's both do, and neither is on screen.
    await mountApp('#/tickets/TKT-147',
      ticketRoutes(ticketDoc({ id: 'TKT-147', title: 'Propagate the hash to deploy' }), ROWS))
    await settle()
    await settle()

    // the cost: this rail cannot say it
    expect(laneNames()).toEqual(['lane/02'])
    expect(screen.getByTestId('lane-rail')).not.toHaveTextContent('TKT-151')

    // what pays for it: the chip, present, carrying the SERVER's count over EVERY lane
    const chip = screen.getByTestId('needs-you-chip')
    expect(chip).toHaveTextContent('2 lanes need you')
    expect(chip.tagName).toBe('A')
    // two of them, so the chip cannot name one — it goes to the surface that can name both
    expect(chip.getAttribute('href')).toBe('#/lanes')

    cleanup()

    // and with exactly one, it goes straight there rather than to a list of one
    await mountApp('#/tickets/TKT-147',
      ticketRoutes(ticketDoc({ id: 'TKT-147', title: 'Propagate the hash to deploy' }),
                   [ROWS[1], ROWS[2]]))
    await settle()
    await settle()

    const single = screen.getByTestId('needs-you-chip')
    expect(single).toHaveTextContent('1 lane needs you')
    expect(single.getAttribute('href')).toBe('#/tickets/TKT-151')
  })

  /**
   * WEB-FEAT-079 — §4.4's second lane is drawn UNDER the first.
   *
   * `two_lanes_on_one_ticket_both_render` above is everything the product claimed until now: both
   * cards are on the screen, in E9's order, carrying the same ticket id, and nothing anywhere says
   * which one is the work and which one is the check on it.
   *
   * Asserted by CONTAINMENT and not by a class name. A class says a stylesheet was edited; the
   * parent node says the tree is the tree, and a stylesheet that later stops indenting it cannot
   * make this case lie about where the card is.
   */
  it('a_role_lane_is_drawn_under_the_run_lane_of_its_ticket', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()

    // both still on the screen, still in E9's order
    expect(laneNames()).toEqual(['lane/01', 'lane/04'])

    const child = cardFor('lane/04')
    const under = child.closest('[data-testid="lane-children"]')
    expect(under).not.toBeNull()
    expect(under!.getAttribute('data-parent')).toBe('lane/01')

    // the `run` lane is under nothing, and it OWNS the branch its role lane sits in
    const parent = cardFor('lane/01')
    expect(parent.closest('[data-testid="lane-children"]')).toBeNull()
    expect(parent.closest('[data-testid="lane-branch"]')!.contains(child)).toBe(true)

    // nesting is presentation: the count beside LANES is still every lane of this ticket
    expect(screen.getByTestId('lane-count')).toHaveTextContent('2')
  })

  it('the_child_card_names_its_role_and_the_run_lane_does_not', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
    await settle()
    await settle()

    const child = cardFor('lane/04')
    expect(child.getAttribute('data-role')).toBe('reviewer')
    expect(within(child).getByTestId('lane-role')).toHaveTextContent('reviewer')
    // and it KEEPS its ticket id. An orphan role lane draws at top level with no parent above it
    // to carry one, so a card that dropped the id would be a card that explains nothing exactly
    // when there is nothing else to read it off.
    expect(child).toHaveTextContent('TKT-142')

    // `run` is the tool's own default, and a tag on every card is a tag that says nothing
    const parent = cardFor('lane/01')
    expect(parent.getAttribute('data-role')).toBe('run')
    expect(within(parent).queryByTestId('lane-role')).toBeNull()
  })

  /**
   * THIRD MANDATORY NEGATIVE — the case a `groupBy(parent)` drops on the floor.
   *
   * `E13` releases ONE lane at a time. A `run` lane released while the check on it is still
   * working is exactly the moment that check is the only thing worth looking at, and a nesting
   * that hid a lane because its parent left would be worse than no nesting at all.
   */
  it('an_orphan_role_lane_is_drawn_at_top_level', async () => {
    const ORPHANED = ROWS.filter((row) => row.lane !== 'lane/01')
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ORPHANED))
    await settle()
    await settle()

    expect(laneNames()).toEqual(['lane/04'])
    expect(screen.getByTestId('lane-count')).toHaveTextContent('1')
    // drawn, and drawn at the top: no container, and not the empty block either
    expect(document.querySelector('[data-testid="lane-children"]')).toBeNull()
    expect(screen.queryByTestId('rail-empty')).toBeNull()
    expect(cardFor('lane/04')).toHaveTextContent('TKT-142')
  })
})

/* ------------------------------------------------------------------------------------------- *
 * WEB-FEAT-366 · criteria 2, 4 and 5 — the line moves on the socket, a new run clears it, and a
 * lane that has said nothing draws no line at all.
 *
 * `docs/operations/ESCAPES.md` 2026-09-05 (`SRV-FEAT-415` / `WEB-FIX-349`) is the row this ticket answers: a
 * server field that no screen read for three days while the board drew a fixed string. A line
 * that is only ever asserted at the mount is indistinguishable from a constant, so the cases
 * below all move it AFTER the screen exists and require the DOM to follow.
 * ------------------------------------------------------------------------------------------- */

/**
 * Two lanes on TKT-142 whose words DISAGREE — the rail draws both, and a frame is about one.
 *
 * `lane/03` belongs to another ticket and carries words too: a frame for a lane that is not on
 * this surface is criterion 5's third clause, and a fixture where the absent lane had no words
 * could not tell *ignored* from *there was nothing to draw*.
 */
const RAIL_WORDS = [
  lane({
    lane: 'lane/01', ticket_id: 'TKT-142', elapsed_s: 724, cost_usd: 0.74, progress_pct: 50,
    stage: { index: 4, total: 8, round: 1 },
    last_words: { kind: 'prose', text: 'Reading the ticket', at: '2026-07-28T09:59:58Z' },
  }),
  lane({
    lane: 'lane/04', ticket_id: 'TKT-142', role: 'reviewer', elapsed_s: 300, cost_usd: 0.31,
    progress_pct: 75, stage: { index: 6, total: 8, round: 1 },
    last_words: { kind: 'tool', text: 'Bash', at: '2026-07-28T09:59:59Z' },
  }),
  lane({
    lane: 'lane/09', ticket_id: 'TKT-999', elapsed_s: 60, cost_usd: 0.05, progress_pct: 25,
    stage: { index: 2, total: 8, round: 1 },
    last_words: { kind: 'prose', text: 'A lane nobody has this ticket open for',
                  at: '2026-07-28T09:59:57Z' },
  }),
]

const railWords = (name: string) => within(cardFor(name)).queryByTestId('lane-words')

/** M18, exactly as `docs/technical/contracts/socket.schema.json:738` closes it — `seq` is null and it is never empty. */
const wordsFrame = (name: string, ticket: string, kind: string, text: string, at: string) => ({
  m: 'lane.words', ts: at, seq: null, lane: name, ticket_id: ticket,
  last_words: { kind, text, at },
})

describe('4.4 · the words line', () => {
  it('the_words_line_follows_the_socket_without_a_refetch', async () => {
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), RAIL_WORDS))
    await settle()
    await settle()

    expect(laneNames()).toEqual(['lane/01', 'lane/04'])
    expect(railWords('lane/01')).toHaveTextContent('Reading the ticket')
    expect(railWords('lane/04')).toHaveTextContent('Bash')

    const fetchesBefore = calls.length
    const e9Before = callsTo('/api/lanes').length
    const node = railWords('lane/04')

    await act(async () => {
      app.socket().deliver(wordsFrame('lane/04', 'TKT-142', 'file_edit',
        'Edit server/app/lane/store.cpp', '2026-07-28T10:00:01Z'))
    })
    await settle()

    // the frame's own text, verbatim, on the frame's own lane
    expect(railWords('lane/04')).toHaveTextContent('Edit server/app/lane/store.cpp')
    expect(railWords('lane/04')!.textContent).toBe('Edit server/app/lane/store.cpp')
    // patched in place — the same element, not a rebuilt list
    expect(railWords('lane/04')).toBe(node)
    // and the OTHER card is untouched, which is what makes the line a fact about one lane
    expect(railWords('lane/01')!.textContent).toBe('Reading the ticket')

    // NOTHING WAS REFETCHED. E9 was read once, at the mount, and the frame added no read.
    expect(callsTo('/api/lanes')).toHaveLength(e9Before)
    expect(calls.length).toBe(fetchesBefore)
  })

  it('a_new_run_clears_the_words_until_it_speaks', async () => {
    // E9 answers with words the first time and with `null` the second — the two answers must
    // DISAGREE, or a case cannot tell a surface that re-read from one that never did.
    //
    // The two answers are forced APART, and in both members that matter: the first carries words
    // on lanes that are `running`, the second carries `null` on lanes whose run has ENDED. A
    // fixture whose second answer differed only in the words could not tell the M2 half of this
    // rule from the E9 half, because the lane would already be in the state the frame announces
    // and `running → running` is not a new run.
    let reads = 0
    const ended = (row: Record<string, unknown>) => ({
      ...row,
      last_words: null,
      state: { ...(row.state as Record<string, unknown>), process: 'idle',
               motion: { value: null, silent_for_s: 0, since: null } },
    })
    const app = await mountApp('#/tickets/TKT-142', {
      ...ticketRoutes(ticketDoc(), RAIL_WORDS),
      '/api/lanes': () => {
        reads += 1
        return { body: laneSet(reads === 1 ? RAIL_WORDS : RAIL_WORDS.map(ended)) }
      },
    })
    await settle()
    await settle()
    expect(railWords('lane/01')).toHaveTextContent('Reading the ticket')
    expect(railWords('lane/04')).toHaveTextContent('Bash')

    // ── M17 `claimed` for a lane this set does not hold re-reads E9, and the fresh rows carry
    // `null` — the epic's `null`-after-E18 path, which reaches the client this way.
    await act(async () => {
      app.socket().deliver({
        m: 'lane.membership', ts: '2026-07-28T10:00:01Z', seq: null,
        lane: 'lane/07', ticket_id: 'TKT-142', workspace: 'core', change: 'claimed',
      })
    })
    await settle()
    await settle()

    expect(reads).toBe(2)
    expect(railWords('lane/01')).toBeNull()
    expect(railWords('lane/04')).toBeNull()
    expect(screen.queryAllByTestId('lane-words')).toHaveLength(0)

    // ── and the OTHER half of the same rule: M2 carrying `process: running` for a lane that has
    // words clears the line until the next `lane.words`. The lane's run has ended and it speaks
    // once more — a lane that stopped still shows what it last said — so the clearing below is
    // measured against a line that was really there, and against a lane E18 is really starting.
    await act(async () => {
      app.socket().deliver(wordsFrame('lane/01', 'TKT-142', 'prose', 'Writing the test',
        '2026-07-28T10:00:02Z'))
    })
    await settle()
    expect(railWords('lane/01')).toHaveTextContent('Writing the test')

    const e9Before = callsTo('/api/lanes').length
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:03Z', seq: null,
        lane: 'lane/01', ticket_id: 'TKT-142',
        state: {
          process: 'running',
          motion: { value: 'producing', silent_for_s: 0, since: null },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        waiting_for: null, needs_you: false,
      })
    })
    await settle()

    expect(railWords('lane/01')).toBeNull()
    expect(callsTo('/api/lanes')).toHaveLength(e9Before)   // cleared, not refetched

    // until it speaks
    await act(async () => {
      app.socket().deliver(wordsFrame('lane/01', 'TKT-142', 'tool', 'Bash',
        '2026-07-28T10:00:04Z'))
    })
    await settle()
    expect(railWords('lane/01')).toHaveTextContent('Bash')

    // A SECOND M2 that does not ENTER `running` may not clear it. Without this the rule is
    // "any M2 blanks the line" — and a working lane's motion axis flips between `producing` and
    // `silent` every few seconds, so the line §1 asks to MOVE while the agent works would spend
    // most of its life blank. The epic says the transition; this is the case that pins it.
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:05Z', seq: null,
        lane: 'lane/01', ticket_id: 'TKT-142',
        state: {
          process: 'running',
          motion: { value: 'silent', silent_for_s: 130, since: SILENT_SINCE },
          evidence: 'nothing_claimed', blocked: null, alarm: true,
        },
        waiting_for: null, needs_you: true,
      })
    })
    await settle()
    expect(railWords('lane/01')).toHaveTextContent('Bash')
  })

  /**
   * MANDATORY NEGATIVE — nothing said, nothing drawn.
   *
   * Three clauses, one case, because they are one rule: no element rather than an empty one, the
   * server's string byte for byte however long it is, and a frame about a lane that is not here
   * changing nothing and throwing nothing.
   */
  it('a_lane_that_has_said_nothing_draws_no_words_line', async () => {
    // The server's own clip, reproduced exactly: `transcript.cpp:88` takes 160 CODE POINTS of the
    // first non-empty line and appends `…`, so the string on the wire is 161 code points. Built
    // here rather than typed, and asserted, so the fixture cannot drift from the rule it stands
    // for — and it carries a multi-byte character, which is what makes code points not bytes.
    const LONG = [...('Reading the ticket §1 — every drawing of a lane carries the agent’s '
      + 'last words, and draws nothing when it has said nothing, which is the whole of what this '
      + 'line is for and then some')].slice(0, 160).join('') + '…'
    expect([...LONG]).toHaveLength(161)
    expect([...LONG].pop()).toBe('…')

    const ROWS_ONE_SPEAKS = [
      // said nothing — E9 serves `last_words: null`, which is REQUIRED and nullable
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', last_words: null }),
      lane({ lane: 'lane/04', ticket_id: 'TKT-142', role: 'reviewer',
             last_words: { kind: 'prose', text: LONG, at: '2026-07-28T09:59:59Z' } }),
    ]
    const app = await mountApp('#/tickets/TKT-142',
      ticketRoutes(ticketDoc(), ROWS_ONE_SPEAKS))
    await settle()
    await settle()
    expect(laneNames()).toEqual(['lane/01', 'lane/04'])

    // ── NO ELEMENT, not an empty one
    expect(within(cardFor('lane/01')).queryByTestId('lane-words')).toBeNull()
    // and the sub-line is a different fact and is unaffected either way (WEB-FIX-277)
    expect(screen.queryAllByTestId('lane-words')).toHaveLength(1)

    // ── the server's string, byte for byte. The client never clips and never re-clips.
    const spoke = railWords('lane/04')!
    expect(spoke.textContent).toBe(LONG)
    expect([...spoke.textContent!]).toHaveLength(161)

    // and it is CSS that clips what overflows — the rule is read off the stylesheet, because
    // jsdom applies no external sheet and a `toHaveStyle` here would assert nothing
    const css = readFileSync(join(__dirname, '..', 'src', 'styles', 'app.css'), 'utf8')
    const rule = /\.lane-words\s*\{([^}]*)\}/.exec(css)?.[1] ?? ''
    expect(rule, 'src/styles/app.css declares no .lane-words rule').not.toBe('')
    expect(rule.replace(/\s+/g, ' ')).toContain('text-overflow: ellipsis')
    expect(rule.replace(/\s+/g, ' ')).toContain('overflow: hidden')
    expect(rule.replace(/\s+/g, ' ')).toContain('white-space: nowrap')

    // ── a frame for a lane that is not on this surface changes nothing and throws nothing
    const fetchesBefore = calls.length
    const before = document.body.innerHTML
    await act(async () => {
      app.socket().deliver(wordsFrame('lane/04', 'TKT-142', 'prose', LONG,
        '2026-07-28T09:59:59Z'))                      // the same words: a frame that changes none
      app.socket().deliver(wordsFrame('lane/77', 'TKT-999', 'tool', 'Bash',
        '2026-07-28T10:00:01Z'))
    })
    await settle()

    expect(document.body.innerHTML).toBe(before)
    expect(screen.queryAllByTestId('lane-words')).toHaveLength(1)
    expect(railWords('lane/04')!.textContent).toBe(LONG)
    // one refetch for the unknown lane is E9's own guard and is not this ticket's; what may not
    // happen is a card appearing for a lane nobody has this ticket open for
    expect(calls.length).toBeLessThanOrEqual(fetchesBefore + 1)
    expect(laneNames()).toEqual(['lane/01', 'lane/04'])
  })
})

/**
 * WEB-FIX-428 — the bar is the server's number, and the card says how long is left.
 *
 * `SRV-FIX-599` made `progress_pct` the stages FINISHED and `100` once the ticket is done, and gave
 * E9, M3 and M4 a `remaining_s`. The client re-derived the bar from `stage` on every M3
 * (`Math.round(index * 100 / total)`), so a ticket set to `pass` on disk drew 70 % while E9 said
 * 100 — measured in SRV-FIX-599's tier 3. Three lanes of ONE ticket, so the rail draws all of them.
 */
describe('WEB-FIX-428 the bar and the time left', () => {
  const THREE = [
    lane({ lane: 'lane/01', ticket_id: 'TKT-142', progress_pct: 60, remaining_s: null,
           stage: { index: 7, total: 10, round: 1 } }),
    lane({ lane: 'lane/04', ticket_id: 'TKT-142', role: 'qa', progress_pct: 33, remaining_s: null,
           stage: { index: 2, total: 3, round: 1 } }),
    lane({ lane: 'lane/05', ticket_id: 'TKT-142', role: 'reviewer', progress_pct: 0,
           remaining_s: null, stage: { index: 0, total: 10, round: 1 } }),
  ]

  const stageFrame = (laneName: string, index: number, total: number, pct: number,
                      left: number | null) => ({
    m: 'stage', ts: '2026-07-28T10:00:01Z', seq: null, lane: laneName, ticket_id: 'TKT-142',
    stage: { index, total, name: 'QA', round: 1, of: 1, source: 'ticket' },
    progress_pct: pct, remaining_s: left,
  })

  const bars = () => screen.getAllByTestId('lane-card').map((c) => {
    const bar = within(c).getByTestId('lane-bar')
    return [c.getAttribute('data-lane'), bar.getAttribute('data-pct'),
            (bar.firstElementChild as HTMLElement).style.width]
  })

  it('the_bar_is_the_servers_percent_not_one_derived_from_the_stage', async () => {
    // Numbers the old formula turns into OTHER numbers: 7/10 → 70, 3/3 → 100, 0/10 → 0.
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), THREE))
    await settle()
    await settle()
    await act(async () => {
      app.socket().deliver(stageFrame('lane/01', 7, 10, 100, 0))
      app.socket().deliver(stageFrame('lane/04', 3, 3, 66, 120))
      app.socket().deliver(stageFrame('lane/05', 0, 10, 0, null))
    })
    await settle()

    expect(bars()).toEqual([['lane/01', '100', '100%'], ['lane/04', '66', '66%'],
                            ['lane/05', '0', '0%']])
  })

  it('a_stage_frame_saying_done_fills_the_bar_and_clears_time_left', async () => {
    const rows = [lane({ lane: 'lane/01', ticket_id: 'TKT-142', progress_pct: 60, remaining_s: 900,
                         stage: { index: 7, total: 10, round: 1 } })]
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), rows))
    await settle()
    await settle()
    const card = cardFor('lane/01')
    const before = [within(card).getByTestId('lane-bar').getAttribute('data-pct'),
                    within(card).queryByTestId('lane-remaining')?.textContent ?? null]
    const fetches = callsTo('/api/lanes').length

    await act(async () => { app.socket().deliver(stageFrame('lane/01', 7, 10, 100, 0)) })
    await settle()

    expect([before, within(card).getByTestId('lane-bar').getAttribute('data-pct'),
            within(card).queryByTestId('lane-remaining'), callsTo('/api/lanes').length])
      .toEqual([['60', '~15m left'], '100', null, fetches])
  })

  it('time_left_counts_down_with_the_elapsed_clock_and_resets_on_metrics', async () => {
    const rows = [lane({ lane: 'lane/01', ticket_id: 'TKT-142', remaining_s: 750 })]
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), rows))
    await settle()
    await settle()
    const left = () => within(cardFor('lane/01')).getByTestId('lane-remaining').textContent
    const read = [left()]

    await act(async () => { vi.advanceTimersByTime(120_000) })
    await settle()
    read.push(left())

    await act(async () => {
      app.socket().deliver({
        m: 'lane.metrics', ts: '2026-07-28T10:02:00Z', seq: null, lane: 'lane/01',
        elapsed_s: 900, cost_usd: 0.9, remaining_s: 900, turn: { n: 3, cost_usd: 0.1 },
      })
    })
    await settle()
    read.push(left())

    await act(async () => { app.socket().deliver(stageFrame('lane/01', 4, 8, 37, 45)) })
    await settle()
    read.push(left())

    await act(async () => { app.socket().deliver(stageFrame('lane/01', 4, 8, 37, 3900)) })
    await settle()
    read.push(left())

    expect(read).toEqual(['~12m left', '~10m left', '~15m left', '<1m left', '~1h 05m left'])
  })

  it('no_time_left_is_drawn_when_the_server_has_none', async () => {
    // MANDATORY NEGATIVE — nothing measured is not a dash, and done is not "0m left".
    const rows = [
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', remaining_s: null }),
      lane({ lane: 'lane/04', ticket_id: 'TKT-142', role: 'qa', remaining_s: 0 }),
      lane({ lane: 'lane/05', ticket_id: 'TKT-142', role: 'reviewer', remaining_s: 300 }),
    ]
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), rows))
    await settle()
    await settle()

    const drawn = screen.getAllByTestId('lane-card').map((c) =>
      [c.getAttribute('data-lane'), within(c).queryAllByTestId('lane-remaining')
        .map((n) => n.textContent)])
    expect([drawn, screen.getAllByTestId('lane-remaining').length])
      .toEqual([[['lane/01', []], ['lane/04', []], ['lane/05', ['~5m left']]], 1])
  })
})
