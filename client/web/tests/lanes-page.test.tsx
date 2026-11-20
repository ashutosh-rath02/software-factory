import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, within } from '@testing-library/react'

import {
  calls, callsTo, lane, laneSet, mountApp, settle, ticketDoc, ticketRoutes,
} from './helpers'

/**
 * SPEC.md §4.14 — every lane in the repository, on a surface whose whole job is that answer.
 *
 * WEB-FEAT-050. §4.4's rail stopped being the place a person sees everywhere else, and this is
 * where that went. It is E9's whole answer, drawn with §4.4's own `LaneCard`, grouped by workspace
 * for §4.3's reason — progress is only comparable within a group.
 *
 * The clock is frozen throughout: a card's elapsed is a subtraction against `now`.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const SILENT_SINCE = '2026-07-28T09:57:50Z'

const state = (over: Record<string, unknown>) => ({
  process: 'running',
  motion: { value: null, silent_for_s: 0, since: null },
  evidence: 'nothing_claimed', blocked: null, alarm: false,
  ...over,
})

/**
 * Six lanes, six tickets, three workspaces — and one lane in each of the six states the
 * vocabulary sheet draws (SPEC.md §4.11). `web` is deliberately NOT declared by the test
 * manifest, which is how the "undeclared workspaces come last" rule gets an assertion.
 */
const ROWS = [
  lane({
    lane: 'lane/01', ticket_id: 'TKT-142', workspace: 'core', needs_you: true,
    elapsed_s: 724, cost_usd: 0.74, progress_pct: 50, stage: { index: 4, total: 8, round: 1 },
    state: state({ motion: { value: 'silent', silent_for_s: 130, since: SILENT_SINCE } }),
  }),
  lane({
    lane: 'lane/02', ticket_id: 'TKT-147', workspace: 'core', needs_you: false,
    elapsed_s: 252, cost_usd: 0.22, progress_pct: 75, stage: { index: 3, total: 4, round: 1 },
    state: state({ motion: { value: 'producing', silent_for_s: 0, since: null } }),
  }),
  lane({
    lane: 'lane/03', ticket_id: 'TKT-151', workspace: 'edge', needs_you: true,
    elapsed_s: 1868, cost_usd: 1.04, progress_pct: 25, stage: { index: 1, total: 4, round: 1 },
    state: state({ process: 'idle' }),
  }),
  lane({
    lane: 'lane/04', ticket_id: 'TKT-139', workspace: 'web', needs_you: true,
    elapsed_s: 1367, cost_usd: 1.91, progress_pct: 100, stage: { index: 8, total: 8, round: 1 },
    state: state({ process: 'finished', evidence: 'claimed_unproven' }),
  }),
  lane({
    lane: 'lane/05', ticket_id: 'TKT-133', workspace: 'edge', needs_you: true,
    elapsed_s: 535, cost_usd: 0.48, progress_pct: 50, stage: { index: 2, total: 4, round: 1 },
    state: state({ process: 'crashed' }),
  }),
  lane({
    lane: 'lane/06', ticket_id: 'TKT-128', workspace: 'web', needs_you: false,
    elapsed_s: 1042, cost_usd: 0.96, progress_pct: 100, stage: { index: 8, total: 8, round: 1 },
    state: state({ process: 'finished', evidence: 'claimed_proven' }),
  }),
]

const LANES_ONLY = { '/api/lanes': { body: laneSet(ROWS) } }

const cardNames = () =>
  screen.queryAllByTestId('lane-card').map((c) => c.getAttribute('data-lane'))

const namesIn = (group: HTMLElement) =>
  within(group).queryAllByTestId('lane-card').map((c) => c.getAttribute('data-lane'))

const groupsByName = () => {
  const out: Record<string, HTMLElement> = {}
  for (const group of screen.getAllByTestId('lane-group')) {
    out[within(group).getByTestId('group-name').textContent ?? ''] = group
  }
  return out
}

/**
 * WEB-FEAT-079 — a role lane on `TKT-142`, which `lane/01` already runs in `core`.
 *
 * `workspace` is the parameter, because the whole of the second case below is what happens when it
 * is NOT the run lane's.
 */
const roleLane = (workspace: string) => lane({
  lane: 'lane/07', ticket_id: 'TKT-142', workspace, role: 'qa', needs_you: false,
  elapsed_s: 300, cost_usd: 0.31, progress_pct: 75, stage: { index: 6, total: 8, round: 1 },
  state: state({ motion: { value: 'producing', silent_for_s: 0, since: null } }),
})

describe('4.14 all lanes', () => {
  it('every_lane_is_on_the_surface', async () => {
    await mountApp('#/lanes', LANES_ONLY)
    await settle()
    await settle()
    screen.getByTestId('surface')

    // one card per lane in E9's answer — including the four whose ticket nobody has open
    expect(cardNames())
      .toEqual(['lane/01', 'lane/02', 'lane/03', 'lane/05', 'lane/04', 'lane/06'])

    // grouped by workspace, the manifest's declared order first and the undeclared one after
    const groups = screen.getAllByTestId('lane-group')
    expect(groups.map((g) => within(g).getByTestId('group-name').textContent))
      .toEqual(['core', 'edge', 'web'])
    expect(groups.map((g) => within(g).getByTestId('group-count').textContent))
      .toEqual(['2 lanes', '2 lanes', '2 lanes'])
    expect(within(groups[1]).queryAllByTestId('lane-card').map((c) => c.getAttribute('data-lane')))
      .toEqual(['lane/03', 'lane/05'])

    // all six of §3.3's states the sheet draws, each in the card's own words and no others
    expect(screen.getAllByTestId('lane-card').map(
      (c) => c.querySelector('.lane-state')?.textContent)).toEqual([
      'running · silent', 'running · producing', 'idle · waiting for you',
      'crashed mid-turn', 'finished · claimed done', 'finished · proof attached',
    ])

    // the card is §4.4's, so a lane that needs a person is marked here exactly as it is there
    expect(screen.getAllByTestId('lane-card').map((c) => c.getAttribute('data-alarm')))
      .toEqual(['true', 'false', 'true', 'true', 'true', 'false'])
    // and nothing is SELECTED, because nothing is open
    expect(screen.getAllByTestId('lane-card').every(
      (c) => c.getAttribute('data-selected') === 'false')).toBe(true)
    // it reads: E13 belongs on the ticket whose branch it is about
    expect(screen.queryAllByTestId('lane-release')).toHaveLength(0)

    // the stage is `index/total` and not a guessed name: this surface has read no ticket
    // document, and SPEC.md §4.3's rule is that 4/8 is never compared against 2/4. `lane/02` is
    // the exception that proves it — its total matches the manifest's own four-stage sequence.
    expect(within(screen.getAllByTestId('lane-card')[0]).getByTestId('lane-stage'))
      .toHaveTextContent('4/8')
    expect(within(screen.getAllByTestId('lane-card')[1]).getByTestId('lane-stage'))
      .toHaveTextContent('GATE')

    // one request for the whole surface, and it is E9
    expect(callsTo('/api/lanes')).toHaveLength(1)
  })

  it('a_card_navigates_to_its_ticket', async () => {
    // The two surfaces are one path: a card is a link to the ticket its lane is running, and the
    // rail there is §4.4's — which now shows that lane and nothing else.
    await mountApp('#/lanes', {
      ...LANES_ONLY,
      ...ticketRoutes(ticketDoc({ id: 'TKT-151', title: 'Provenance in the ledger' }), ROWS),
    })
    await settle()
    await settle()

    const card = document.querySelector('[data-testid="lane-card"][data-lane="lane/03"]')!
    const link = card.closest('a')!
    expect(link.getAttribute('href')).toBe('#/tickets/TKT-151')

    // jsdom performs a fragment navigation on a QUEUED TASK, and this file freezes the clock, so
    // the task has to be let through explicitly. Nothing about the product is asynchronous here.
    await act(async () => { (link as HTMLElement).click() })
    await act(async () => { vi.advanceTimersByTime(1) })
    await settle()
    await settle()

    expect(location.hash).toBe('#/tickets/TKT-151')
    screen.getByTestId('lane-rail')
    expect(cardNames()).toEqual(['lane/03'])
    expect(screen.getByTestId('lane-count')).toHaveTextContent('1')
  })

  it('the_surface_is_live', async () => {
    // ONE M2 frame changes ONE card, with no refetch and no reload. The frame reaches this
    // surface because a lane's state is routed to the `requirements` scope as well as to its own
    // and its ticket's (`laneStateScopes`), and this surface subscribes to that scope.
    const app = await mountApp('#/lanes', LANES_ONLY)
    await settle()
    await settle()

    const subscribed = app.socket().frames()
      .filter((f) => f.m === 'subscribe')
      .map((f) => JSON.stringify((f as { scope: unknown }).scope))
    expect(subscribed).toContain('{"requirements":true}')

    const before = screen.getAllByTestId('lane-card')
    const node = before[2]                                   // lane/03, in `edge`
    expect(node.querySelector('.lane-state')?.textContent).toBe('idle · waiting for you')
    const fetchesBefore = calls.length

    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:01Z', seq: null,
        lane: 'lane/03', ticket_id: 'TKT-151',
        state: {
          process: 'running',
          motion: { value: 'producing', silent_for_s: 0, since: null },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        waiting_for: null, needs_you: false,
      })
    })
    await settle()

    const after = screen.getAllByTestId('lane-card')
    expect(after[2]).toBe(node)                              // patched, not rebuilt
    expect(node.querySelector('.lane-state')?.textContent).toBe('running · producing')
    expect(node.getAttribute('data-alarm')).toBe('false')
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/lanes')).toHaveLength(1)
  })

  /**
   * WEB-FEAT-079 — §4.4's nesting, on this surface, INSIDE the group each lane already belongs to.
   */
  it('a_role_lane_nests_under_its_run_lane_inside_its_workspace_group', async () => {
    await mountApp('#/lanes', { '/api/lanes': { body: laneSet([...ROWS, roleLane('core')]) } })
    await settle()
    await settle()

    // the branch is drawn where its run lane was, and the rest of E9's order is untouched
    expect(cardNames())
      .toEqual(['lane/01', 'lane/07', 'lane/02', 'lane/03', 'lane/05', 'lane/04', 'lane/06'])

    const core = groupsByName().core
    const child = core.querySelector('[data-testid="lane-card"][data-lane="lane/07"]')!
    expect(child.closest('[data-testid="lane-children"]')!.getAttribute('data-parent'))
      .toBe('lane/01')

    // and the group still counts what it holds: nesting is presentation, not a filter
    expect(within(core).getByTestId('group-count')).toHaveTextContent('3 lanes')
    expect(namesIn(core)).toEqual(['lane/01', 'lane/07', 'lane/02'])
  })

  /**
   * MANDATORY NEGATIVE — the nesting rule may not reach across a group boundary.
   *
   * §4.3's rule is that progress is only comparable within a group: a lane card carries a bar and a
   * stage, and `4/8` is never compared against `2/4`. A rule keyed on the ticket alone would pull
   * this card out of the group whose count claims it and hang it under a card in another one.
   */
  it('nesting_never_moves_a_card_out_of_its_workspace_group', async () => {
    await mountApp('#/lanes', { '/api/lanes': { body: laneSet([...ROWS, roleLane('edge')]) } })
    await settle()
    await settle()

    const groups = groupsByName()
    // it is in `edge`, at top level, and it is not in `core` at all
    expect(namesIn(groups.core)).toEqual(['lane/01', 'lane/02'])
    expect(namesIn(groups.edge)).toEqual(['lane/03', 'lane/05', 'lane/07'])
    expect(groups.core.querySelector('[data-lane="lane/07"]')).toBeNull()

    const child = groups.edge.querySelector('[data-testid="lane-card"][data-lane="lane/07"]')!
    expect(child.closest('[data-testid="lane-children"]')).toBeNull()

    expect(within(groups.core).getByTestId('group-count')).toHaveTextContent('2 lanes')
    expect(within(groups.edge).getByTestId('group-count')).toHaveTextContent('3 lanes')
  })

  /**
   * MANDATORY NEGATIVE.
   *
   * The cheap way to build a "show me everything" screen is to poll E9, and a poll is the thing
   * this architecture exists not to have. Ten minutes of fake time, zero frames: no request, no
   * remount, and the only text that moves belongs to a clock — which is the one timer §4.4 and
   * §4.14 both sanction, because a counter that needs a frame per second dies with the socket.
   */
  it('no_poll', async () => {
    const app = await mountApp('#/lanes', LANES_ONLY)
    await settle()
    await settle()
    expect(screen.getAllByTestId('lane-card')).toHaveLength(6)

    const before = Array.from(document.querySelectorAll('body *'))
    const textBefore = new Map(before.map((n) => [n, n.textContent]))
    const fetchesBefore = calls.length
    const commitsBefore = app.commits()

    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved — otherwise this case passes for the wrong reason
    const pings = app.socket().frames().filter((f) => f.m === 'ping')
    expect(pings.length).toBeGreaterThanOrEqual(59)

    // zero fetches, and E9 was read exactly once for the whole ten minutes
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/lanes')).toHaveLength(1)

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
})

/* ------------------------------------------------------------------------------------------- *
 * WEB-FEAT-366 · criterion 1 — every drawing of a lane carries THAT lane's last words.
 *
 * `docs/operations/ESCAPES.md` 2026-09-05 (`SRV-FEAT-415` / `WEB-FIX-349`): a server field no screen read,
 * for three days, while the board kept drawing a fixed string. The shape of that miss is a check
 * that reads ONE row — a single card carrying one string cannot tell a screen reading `E9` from a
 * screen drawing a constant, because both are right about the one row on it. So the fixture puts
 * three lanes on the surface whose words DISAGREE, and the count and all three texts are asserted
 * together (`_TEMPLATE.md` §2: a claim about a collection is asserted in the same case as it).
 * ------------------------------------------------------------------------------------------- */

/** M18's own three kinds, one lane each, and no two texts the same. */
const SAID = [
  { lane: 'lane/01', ticket: 'TKT-142',
    words: { kind: 'prose', text: 'Reading the ticket', at: '2026-07-28T09:59:58Z' } },
  { lane: 'lane/02', ticket: 'TKT-147',
    words: { kind: 'file_edit', text: 'Edit server/app/lane/store.cpp',
             at: '2026-07-28T09:59:59Z' } },
  { lane: 'lane/03', ticket: 'TKT-151',
    words: { kind: 'tool', text: 'Bash', at: '2026-07-28T10:00:00Z' } },
]

const WORDS_ROWS = SAID.map((row) =>
  lane({ lane: row.lane, ticket_id: row.ticket, workspace: 'core', last_words: row.words }))

const wordsOf = (name: string) =>
  within(document.querySelector<HTMLElement>(`[data-testid="lane-card"][data-lane="${name}"]`)!)
    .queryByTestId('lane-words')

describe('4.14 · the lane says what it last did', () => {
  it('a_card_says_what_its_own_lane_last_did', async () => {
    await mountApp('#/lanes', { '/api/lanes': { body: laneSet(WORDS_ROWS) } })
    await settle()
    await settle()

    // the collection and the claim about it, in one assertion
    expect(cardNames()).toEqual(['lane/01', 'lane/02', 'lane/03'])
    expect(screen.getAllByTestId('lane-words').map((n) => n.textContent)).toEqual([
      'Reading the ticket',
      'Edit server/app/lane/store.cpp',
      'Bash',
    ])

    // and each one is on ITS OWN card — the assertion above holds for a surface that drew three
    // lines in the right order under the wrong cards
    expect(wordsOf('lane/01')).toHaveTextContent('Reading the ticket')
    expect(wordsOf('lane/02')).toHaveTextContent('Edit server/app/lane/store.cpp')
    expect(wordsOf('lane/03')).toHaveTextContent('Bash')

    // VERBATIM — `textContent` is the server's string and nothing else. The design's `›` is a
    // CSS `::before`, so it is not in the DOM text, and `kind` is drawn nowhere.
    for (const row of SAID) {
      const node = wordsOf(row.lane)!
      expect(node.textContent).toBe(row.words.text)
      expect(node.textContent).not.toContain('›')
      expect(node.textContent).not.toContain(row.words.kind)
    }

    // it is its own element and never the sub-line — WEB-FIX-277: two facts, two elements
    for (const row of SAID) {
      expect(wordsOf(row.lane)).not.toBe(
        within(document.querySelector<HTMLElement>(
          `[data-testid="lane-card"][data-lane="${row.lane}"]`)!).queryByTestId('lane-sub'))
    }
  })
})
