import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, within } from '@testing-library/react'

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import {
  calls, callsTo, home, lane, laneSet, mountApp, needsYouRow, settle, ticketRoutes, unpushedRow,
} from './helpers'
import { HOME_COLLECTION, emptyBlockFaults, homeEmptyTitle } from '../src/vocabulary'

/**
 * SPEC.md §4.18 — the first screen, and the only one whose question is *what should I do next*.
 *
 * WEB-FEAT-001. Three panels out of ONE read: `E30` exists so the counts in the first panel and
 * the rows in the second cannot disagree, and a second request here reintroduces the defect it was
 * built to prevent.
 *
 * **Criteria 1–4 and 8 of §1 are BLOCKED(SRV-FEAT-363) and are not in this file.** They are about
 * §3.2b's nine cells, whose predicates read `lane · process · role · verdict · delivery · ledger`
 * per ticket. `E30` serves `delivery` aggregated and no per-ticket row; no endpoint anywhere serves
 * a QA verdict or the `ESCAPES.md` ledger, so four of those nine cells have no source to be
 * computed from. `SRV-FEAT-363` serves the axes in this same one read and those criteria land with
 * it. What is here is the row §4.18 specifies and the two panels under it.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-09-02T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** Three of §3.5's seven states occupied, four empty — the shape the strip is read against. */
const OCCUPIED = [
  { state: 'not_started', count: 4 },
  { state: 'running', count: 1 },
  { state: 'stalled', count: 0 },
  { state: 'unshipped', count: 2 },
  { state: 'waiting', count: 0 },
  { state: 'done', count: 0 },
  { state: 'unknown', count: 0 },
]

const cells = () => screen.getAllByTestId('strip-cell')
const cellFor = (state: string) =>
  cells().find((c) => c.getAttribute('data-state') === state)

describe('4.18 · where the work is', () => {
  it('test_the_delivery_strip_draws_every_state_including_the_empty_ones', async () => {
    await mountApp('#/', { '/api/home': { body: home({ delivery: OCCUPIED }) } })
    await settle()

    // Every state E30 sent, in the order it sent them — which is §3.5's table order. A row of
    // counts that omits its empty members is a row whose shape changes under the reader.
    expect(cells().map((c) => c.getAttribute('data-state'))).toEqual(OCCUPIED.map((d) => d.state))
    expect(cells().map((c) => c.querySelector('[data-testid="strip-count"]')?.textContent))
      .toEqual(['4', '1', '0', '2', '0', '0', '0'])
  })

  /**
   * MANDATORY NEGATIVE — the strip is the server's answer, never a list this client holds.
   *
   * A strip built from a client-side constant renders seven cells whatever arrives, so it stays
   * green while the two halves drift; this case hands it three states and requires exactly three.
   * Delete the mapping over `home.delivery` and hard-code §3.5's values and this goes red, which
   * is the only thing that makes the case above mean what it says.
   */
  it('test_the_strip_may_not_draw_a_state_the_server_did_not_send', async () => {
    const three = [
      { state: 'running', count: 2 },
      { state: 'unshipped', count: 1 },
      { state: 'done', count: 9 },
    ]
    await mountApp('#/', { '/api/home': { body: home({ delivery: three }) } })
    await settle()

    expect(cells()).toHaveLength(3)
    expect(cells().map((c) => c.getAttribute('data-state')))
      .toEqual(['running', 'unshipped', 'done'])
    expect(cellFor('not_started')).toBeUndefined()
    expect(cellFor('stalled')).toBeUndefined()
  })

  /**
   * §4.18 — "The cells §3.3 counts are tinted", which is the design's own sentence: *tinted stages
   * are waiting on a person, not on an agent*. §3.3's two delivery clauses are `stalled` and
   * `unshipped`, and they are the two `DELIVERY` already marks `alarm` for.
   */
  it('test_the_two_states_that_need_a_person_are_tinted_and_the_others_are_not', async () => {
    await mountApp('#/', {
      '/api/home': { body: home({ delivery: OCCUPIED.map(
        (d) => (d.state === 'stalled' ? { ...d, count: 3 } : d)) }) },
    })
    await settle()

    expect(cellFor('stalled')?.getAttribute('data-tinted')).toBe('true')
    expect(cellFor('unshipped')?.getAttribute('data-tinted')).toBe('true')
    for (const quiet of ['not_started', 'running', 'waiting', 'done', 'unknown']) {
      expect(cellFor(quiet)?.getAttribute('data-tinted')).toBe('false')
    }
  })
})

describe('4.18 · needs you', () => {
  /**
   * §1 criterion 5. The row says WHY, in the server's own sentence, and carries the action that
   * ends the state — §3.6's remedy, whose `label` is the server's words rendered verbatim.
   */
  it('test_needs_you_draws_the_clause_and_its_remedy', async () => {
    // THE SHAPE `E30` ACTUALLY SENDS (QA F5). `render.cpp:565` emits one remedy and only one —
    // `remedy::openLane(lane)` when a lane exists, `null` when it does not — and `remedy.cpp:61`
    // builds it with `{}` for the cost, so `destroys` is `[]` on every `needs_you` row this
    // server can produce. A fixture carrying `destroys: ['worktree']` here would be a fixture of
    // a server that does not exist; the conditional half of criterion 5 is asserted below,
    // against the component whose contract it actually is.
    const row = needsYouRow({
      ticket_id: 'TKT-151',
      title: 'Rollback on boot fail',
      clauses: ['crashed', 'unshipped'],
      detail: 'it died and nobody has looked; work exists that nobody else can see',
      lane: 'lane/03',
      remedy: {
        action: 'open_lane', target: { lane: 'lane/03' },
        label: 'Open lane/03', destroys: [],
      },
    })
    await mountApp('#/', {
      '/api/home': { body: home({ needs_you: { total: 1, rows: [row] } }) },
    })
    await settle()

    const drawn = screen.getByTestId('needs-you-row')
    expect(drawn).toHaveTextContent('TKT-151')

    // both clauses, by the names §3.3 gives them
    expect(drawn.getAttribute('data-clauses')).toBe('crashed unshipped')

    // the SERVER's sentence, verbatim — not one composed here out of the clause names
    expect(screen.getByTestId('needs-you-detail').textContent).toBe(row.detail)

    // §3.6 — the server's label, rendered as given
    expect(screen.getByTestId('needs-you-remedy')).toHaveTextContent('Open lane/03')

    // and nothing is said about a cost, because there is none: a control that announced an empty
    // price would teach a person to stop reading the line that matters
    expect(screen.queryByTestId('needs-you-remedy-cost')).toBeNull()
  })

  /**
   * Criterion 5's conditional half — *"when `remedy.destroys` is non-empty the destroyed nouns are
   * drawn BEFORE the control"* — asserted where the condition can actually hold.
   *
   * QA F5 is right that `E30` cannot send a costed remedy today, so a row-level fixture carrying
   * one asserts nothing about this product. What IS true and worth holding is the component's
   * contract: §3.6 makes `destroys` a member of every remedy, §9.2's verb set contains costed ones
   * (`kill_run` carries the turn, `release_lane` the worktree), and the row renders whatever
   * arrives. So the rule is asserted against `RemedyControl` itself, which is the thing that has
   * to obey it, and it will still be obeyed on the day `E30` serves one.
   */
  it('test_a_remedy_that_costs_something_says_so_before_the_control', async () => {
    const { RemedyControl } = await import('../src/components/Remedy')
    const { render } = await import('@testing-library/react')
    render(
      <RemedyControl testId="needs-you-remedy" announceCost
        remedy={{ action: 'open_lane', target: { lane: 'lane/03' },
                  label: 'Open lane/03', destroys: ['worktree'] }}
        addressFor={() => '#/tickets/TKT-151'} />)

    const order = [...document.querySelectorAll(
      '[data-testid="needs-you-remedy-cost"], [data-testid="needs-you-remedy"]')]
      .map((e) => e.getAttribute('data-testid'))
    expect(order).toEqual(['needs-you-remedy-cost', 'needs-you-remedy'])
    expect(screen.getByTestId('needs-you-remedy-cost')).toHaveTextContent('the lane’s checkout')

    // and with nothing to lose it says nothing — the same component, the other branch
    cleanup()
    render(
      <RemedyControl testId="quiet" announceCost
        remedy={{ action: 'open_lane', target: { lane: 'lane/03' },
                  label: 'Open lane/03', destroys: [] }}
        addressFor={() => '#/tickets/TKT-151'} />)
    expect(screen.queryByTestId('quiet-cost')).toBeNull()
    expect(screen.getByTestId('quiet')).toHaveTextContent('Open lane/03')
  })

  /**
   * The count above the rows is counted from the same answer as the rows (`WEB-FIX-091`, and the
   * `2026-08-10` row of `ESCAPES.md` — "the count above a table was not counted from the table").
   * `total` deliberately disagrees with the number of rows here: the panel must read `total`.
   */
  it('test_the_panel_counts_are_the_servers_and_not_the_rows_length', async () => {
    await mountApp('#/', {
      '/api/home': {
        body: home({
          needs_you: { total: 9, rows: [needsYouRow()] },
          unpushed: { total: 4, rows: [unpushedRow()] },
        }),
      },
    })
    await settle()

    expect(screen.getByTestId('needs-you-count')).toHaveTextContent('9')
    expect(screen.getByTestId('unpushed-count')).toHaveTextContent('4')
    expect(screen.getAllByTestId('needs-you-row')).toHaveLength(1)
  })

  /** §4.18 — an empty `needs you` panel is a different and much better state, in its own words. */
  it('test_nothing_needing_you_says_so_in_its_own_words', async () => {
    await mountApp('#/', {
      '/api/home': { body: home({ delivery: OCCUPIED }) },
    })
    await settle()

    expect(screen.getByTestId('needs-you-empty')).toHaveTextContent('Nothing needs you.')
    expect(screen.queryByTestId('needs-you-row')).toBeNull()
  })
})

describe('4.18 · unpushed work', () => {
  /**
   * §1 criterion 7. `age_s` is how long the OLDEST commit has been sitting here — `unpushedRow`'s
   * own sentence is *"the panel's job is that nobody discovers a week of work living on one
   * laptop, and the newest commit's age cannot answer that"*. Two rows whose counts and ages both
   * differ, so a row read off the wrong index fails.
   */
  it('test_unpushed_says_how_old_the_oldest_commit_is', async () => {
    await mountApp('#/', {
      '/api/home': {
        body: home({
          unpushed: {
            total: 2,
            rows: [
              unpushedRow({ ticket_id: 'TKT-142', branch: 'ticket/TKT-142',
                            commits: 7, age_s: 9 * 86400 }),
              unpushedRow({ ticket_id: 'TKT-160', branch: 'ticket/TKT-160',
                            commits: 1, age_s: 6 * 3600 }),
            ],
          },
        }),
      },
    })
    await settle()

    const rows = screen.getAllByTestId('unpushed-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('TKT-142')
    expect(rows[0]).toHaveTextContent('ticket/TKT-142')
    expect(rows[0]).toHaveTextContent('7 commits')
    expect(rows[0]).toHaveTextContent('9d')
    expect(rows[1]).toHaveTextContent('1 commit')
    expect(rows[1]).toHaveTextContent('6h')

    // it NAMES them and does not push them (§3.5's read-only property, §4.18)
    expect(screen.getByTestId('unpushed-panel').querySelectorAll('button')).toHaveLength(0)
  })
})

describe('4.18 · one request', () => {
  /**
   * §1 criterion 6. The shell's own three reads happen on every surface — `E1` and `E2` in
   * `FactoryProvider`, `E9` in `LanesProvider` for §4.1's counter — so they are measured on a
   * surface that has nothing to do with this one and then required to be the ONLY company `E30`
   * keeps. A dashboard that fetched `E29` or `E9` for itself fails here.
   */
  it('test_the_screen_makes_exactly_one_request', async () => {
    await mountApp('#/vocabulary', {})
    await settle()
    const shell = calls.map((c) => new URL(c.url, 'http://factory.test').pathname).sort()
    expect(shell).toEqual(['/api/health', '/api/lanes', '/api/manifest'])

    cleanup()
    await mountApp('#/', { '/api/home': { body: home() } })
    await settle()

    expect(callsTo('/api/home')).toHaveLength(1)
    expect(calls.map((c) => new URL(c.url, 'http://factory.test').pathname).sort())
      .toEqual(['/api/health', '/api/home', '/api/lanes', '/api/manifest'])

    // and it stays one: ten minutes of clock with no frame and no input (§6, no polling)
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()
    expect(callsTo('/api/home')).toHaveLength(1)
  })

  /**
   * §4.18's data line — `E30`, then `M13 catalog.changed`. The surface subscribes to the
   * `requirements` scope, which every lane frame is routed to (§6.2 M14), so a lane started on a
   * ticket nobody has open still moves this screen.
   */
  it('test_a_document_changing_on_disk_reloads_the_one_read', async () => {
    const app = await mountApp('#/', { '/api/home': { body: home({ delivery: OCCUPIED }) } })
    await settle()
    expect(callsTo('/api/home')).toHaveLength(1)
    expect(app.socket().frames().some(
      (f) => f.m === 'subscribe'
        && (f.scope as Record<string, unknown>)?.requirements === true)).toBe(true)

    await act(async () => {
      app.socket().deliver({
        m: 'catalog.changed', ts: '2026-09-02T10:00:01Z', seq: null,
        kind: 'report', id: 'TKT-142', path: '/tmp/fixture/docs/reports/TKT-142-report.md',
        change: 'created',
      })
    })
    await settle()
    expect(callsTo('/api/home')).toHaveLength(2)
  })
})

describe('4.18 · the first screen is reachable, and reaches', () => {
  /**
   * Transcribed from `WEB-FEAT-001-qa.md` F2's reproduction, which is two halves and says the
   * second is the load-bearing one: *"the first screen being unreachable from the rest of the
   * product is worse than the first screen having no outbound link."*
   *
   * `#/` is where the client opens and where every unknown hash resolves, so making it the
   * landing route without an inbound edge left a person who followed a needs-you row to a ticket
   * with no way back but the address bar.
   */
  it('test_the_first_screen_is_reachable_from_elsewhere_and_reaches_the_list', async () => {
    // outbound: from the dashboard, a click that reaches §4.2's list
    await mountApp('#/', { '/api/home': { body: home({ delivery: OCCUPIED }) } })
    await settle()
    const out = [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(out).toContain('#/requirements')

    // inbound: from a surface that is NOT the dashboard, a click that reaches `#/`
    cleanup()
    await mountApp('#/vocabulary', {})
    await settle()
    const back = [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(back).toContain('#/')

    // and it is the brand, which is the one inbound edge that moves no approved pixel:
    // `.bar-brand` sets its own colour and `a { text-decoration: none }` is global
    expect(screen.getByTestId('top-bar').querySelector('.bar-brand')?.getAttribute('href'))
      .toBe('#/')
  })
})

describe('4.18 · the states every surface owes', () => {
  /**
   * Pre-decided — the `Dashboard · Empty` frame. A repository the tool can read with nothing in it
   * is a `200` with every count zero and both lists empty, and that is a STATE, not an error.
   *
   * The 2026-08-11 row of `ESCAPES.md` is why the condition is asserted beside the copy: a block
   * whose sentence was true of nothing was drawn for two years because every case asserted the
   * words and none asserted when they appear.
   */
  it('test_a_repository_with_nothing_in_it_draws_the_designs_empty_block', async () => {
    await mountApp('#/', { '/api/home': { body: home() } })
    await settle()

    expect(screen.getByTestId('home-empty'))
      .toHaveTextContent('No tickets in this repository.')

    // The block names the collection that was COUNTED, and `E30` counts tickets: `renderHome`
    // builds every member of its answer from `Store::instance().tickets()` and never reads a
    // requirement. A repository holding twelve epics and no tickets — the state of any repository
    // between `/split-requirement` and the first `/new-ticket` — satisfies this condition, and a
    // block telling it that it has no requirements is a false sentence about a collection this
    // answer does not contain. Asserted as the ABSENCE of the wrong noun, because the condition
    // and the copy passing together while describing different collections is exactly the defect.
    const block = screen.getByTestId('home-empty').textContent ?? ''
    expect(block).toMatch(/docs\/tickets/)
    expect(block).not.toMatch(/requirement/i)
    expect(block).not.toMatch(/docs\/epics/)
    // the strip is not drawn as a row of zeros beside it — one statement, not two
    expect(screen.queryByTestId('strip-cell')).toBeNull()

    // and one ticket anywhere is enough for the block to be wrong: it goes
    cleanup()
    await mountApp('#/', { '/api/home': { body: home({ delivery: OCCUPIED }) } })
    await settle()
    expect(screen.queryByTestId('home-empty')).toBeNull()
    expect(cells()).toHaveLength(7)
  })

  /**
   * §4.18 — "counts and rows hold their last value and dim". A stale count that admits it is stale
   * is more useful than a blank, and nothing is refetched behind the user's back.
   */
  it('test_the_socket_dropping_dims_the_counts_and_keeps_them', async () => {
    const app = await mountApp('#/', {
      '/api/home': { body: home({ delivery: OCCUPIED, needs_you: { total: 2, rows: [needsYouRow()] } }) },
    })
    await settle()
    expect(screen.getByTestId('needs-you-count')).toHaveTextContent('2')

    await act(async () => { app.socket().onclose?.() })
    await settle()

    expect(screen.getByTestId('reconnect')).toBeTruthy()
    expect(screen.getByTestId('home-panels').getAttribute('data-dim')).toBe('true')
    expect(screen.getByTestId('needs-you-count')).toHaveTextContent('2')
    expect(cells()).toHaveLength(7)
    expect(callsTo('/api/home')).toHaveLength(1)
  })
})

/**
 * WEB-FIX-002 — `escaped_from: WEB-FEAT-001`.
 *
 * Every case below is transcribed from `WEB-FEAT-001-qa.md`'s own reproduction rather than from
 * the finding's title, because the finding's title is the thing the last gate already agreed with.
 *
 * The escape is F4 and it is a defect in the CHECK: `dashboard.test.tsx` asserted the empty copy
 * in the same expression as the condition that produces it — §2's rule, and the right shape — and
 * the condition is over `delivery` + `needs_you` + `unpushed` while the copy said *requirements*.
 * Both halves agreed with each other and neither was the collection the sentence named, so the
 * case passed and the screen was false about any repository between `/split-requirement` and the
 * first `/new-ticket`.
 *
 * So the rule these cases enforce is the one the old one was missing: **the collection asserted
 * must be the one the copy NAMES**, and a claim about a collection no endpoint in the response
 * carries is a claim the surface may not make. It lives in `emptyBlockFaults`, one function, called
 * by the live case and by the mandatory negative — a negative that cannot call the shipped rule is
 * documentation with an `assert` in front of it.
 */

/** `SPEC.md` §4.18, read off disk — the document this surface is checked against, not a copy. */
export function section418(): string {
  const spec = readFileSync(join(__dirname, '..', '..', '..', 'docs', 'product/SPEC.md'), 'utf8')
  const from = spec.indexOf('### 4.18 Home')
  const to = spec.indexOf('### 4.19', from)
  if (from < 0 || to < 0) throw new Error('SPEC.md has no §4.18 — the case cannot branch on it')
  return spec.slice(from, to)
}

/**
 * Does §4.18 claim, TODAY, that the strip's cells are links?
 *
 * Taking the section as a string rather than reading it here is what lets the case exercise both
 * branches on text it owns: a regex that silently stopped matching would otherwise answer `false`
 * for every document forever, and the case would go green on a document nobody was reading.
 */
export function specClaimsCellsAreLinks(section: string): boolean {
  if (!/a cell is a link|each a link|cells are links/i.test(section)) return false
  return !/once §4\.17 has a surface|carry no link/i.test(section)
}

describe('4.18 · the empty state names the collection it counted', () => {
  /** §1 criterion 1. */
  it('test_the_empty_state_names_the_collection_it_counted', async () => {
    // `delivery` all zero, `needs_you.rows` and `unpushed.rows` empty — the fixture §1 names.
    const response = home()
    expect(response.delivery.every((c) => c.count === 0)).toBe(true)
    expect(response.needs_you.rows).toEqual([])
    expect(response.unpushed.rows).toEqual([])

    await mountApp('#/', { '/api/home': { body: response } })
    await settle()

    const block = screen.getByTestId('home-empty').textContent ?? ''

    // ONE expression, the response and the copy together. `emptyBlockFaults` is the rule: the
    // block is drawn only on an answer that IS empty, it names the collection `E30` counts, and
    // it names no other. Asserting the words alone is what WEB-FEAT-001 did.
    expect(emptyBlockFaults(block, response)).toEqual([])

    // and the two halves of that, said out loud, because a rule that returns `[]` for the wrong
    // reason is a rule nobody can read
    expect(block).toContain(HOME_COLLECTION)
    expect(block).toMatch(/docs\/tickets/)
    expect(block).not.toMatch(/requirement/i)
    expect(block).not.toMatch(/epic/i)
  })

  /**
   * §1 criterion 6 — MANDATORY NEGATIVE.
   *
   * `WEB-FEAT-001`'s condition was exactly the one that ships: emptiness from `delivery` +
   * `needs_you` + `unpushed`. Only the noun differed. So the negative restores the noun and
   * requires the rule to refuse it — against the SAME response the live case passed with, which is
   * the whole point: the two conditions are indistinguishable to any check that reads only the
   * words, and that is why the old case could not tell them apart.
   *
   * It calls `emptyBlockFaults`, the function the live case calls. Delete the noun rule and this
   * goes red with it.
   */
  it('test_the_empty_state_case_fails_when_the_condition_is_over_the_wrong_collection', async () => {
    const response = home()

    // WEB-FEAT-001's copy, verbatim from `020fdb0`'s message and from the frame it retracted
    const wasShipped = 'No requirements in this repository. docs/epics is declared and readable, '
      + 'and holds no document the tool counts.'
    const faults = emptyBlockFaults(wasShipped, response)
    expect(faults).not.toEqual([])
    expect(faults.join(' · ')).toMatch(/requirements/)

    // and it is not refusing everything: the shipped sentence still passes the same call
    expect(emptyBlockFaults(homeEmptyTitle(), response)).toEqual([])

    // the live case's rule is the shipped screen's rule — the block a person sees is the block
    // this function approves, so breaking one breaks the other
    await mountApp('#/', { '/api/home': { body: response } })
    await settle()
    expect(screen.getByTestId('home-empty').textContent).toContain(homeEmptyTitle())

    // the second half of the class: the same sentence drawn on an answer that is NOT empty
    expect(emptyBlockFaults(homeEmptyTitle(), home({ delivery: OCCUPIED }))).not.toEqual([])
  })
})

describe('4.18 · the strip cells, and the sentence that describes them', () => {
  /**
   * §1 criterion 3. The case reads §4.18 and BRANCHES on it, so the code and the document cannot
   * drift apart again — which is exactly how F3 arose: `SPEC.md` said each cell is a link into
   * §4.17 and the cells were `<div>`s, in the same commit.
   *
   * Both branches assert something. A case that only checks the links when the document claims
   * them is green on a document that claims nothing and a screen that links nowhere — which is
   * half the drift it exists to stop.
   */
  it('test_every_strip_cell_is_a_link_or_the_spec_does_not_say_it_is', async () => {
    const claims = specClaimsCellsAreLinks(section418())

    // the branch logic itself, on text this case owns — so a regex that stopped matching cannot
    // quietly answer `false` for every document forever
    expect(specClaimsCellsAreLinks('**A cell is a link into §4.17 filtered to it.**')).toBe(true)
    expect(specClaimsCellsAreLinks(
      '**A cell is a link into §4.17 filtered to it — once §4.17 has a surface.** '
      + 'While that holds the cells carry no link.')).toBe(false)

    await mountApp('#/', { '/api/home': { body: home({ delivery: OCCUPIED }) } })
    await settle()
    const drawn = cells()
    expect(drawn).toHaveLength(7)

    const linked = drawn.map((c) => c.closest('a')?.getAttribute('href') ?? null)
    if (claims) {
      expect(linked.filter((h) => h === null)).toEqual([])
    } else {
      // the document says they carry no link today, so they may not; and the panel carries the
      // one destination that does exist, which is what §4.18 puts in the clause's place
      expect(linked).toEqual(drawn.map(() => null))
      expect([...document.querySelectorAll('a')].map((a) => a.getAttribute('href')))
        .toContain('#/requirements')
    }
  })
})

describe('4.18 · the first screen has both edges', () => {
  /**
   * §1 criterion 4, transcribed from F2's reproduction: *"today the hrefs are `#/vocabulary`,
   * `#/permissions`, `#/lanes` and ticket hrefs, and no `#/`."*
   *
   * `#/` is where the client opens and where every unknown hash resolves, so a landing route with
   * no inbound edge left a person who followed a `needs you` row to a ticket with no way back but
   * the address bar.
   */
  it('test_the_first_screen_has_an_inbound_edge', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes())
    await settle()

    const hrefs = [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).toContain('#/')
  })

  /**
   * §1 criterion 5 — and it is asserted in BOTH of this surface's states.
   *
   * The outbound link lives in the strip's header, and the strip is exactly what an empty
   * repository does not draw. Asserting it on the populated state alone leaves the first screen a
   * dead end on the one repository where a person most needs to be told where to go: the state
   * between `/split-requirement` and the first `/new-ticket`.
   */
  it('test_the_first_screen_has_an_outbound_edge', async () => {
    await mountApp('#/', { '/api/home': { body: home({ delivery: OCCUPIED }) } })
    await settle()
    expect(screen.getByRole('link', { name: /requirements/i }).getAttribute('href'))
      .toBe('#/requirements')

    cleanup()
    await mountApp('#/', { '/api/home': { body: home() } })
    await settle()
    expect(screen.getByTestId('home-empty')).toBeTruthy()
    expect(screen.getByRole('link', { name: /requirements/i }).getAttribute('href'))
      .toBe('#/requirements')
  })
})

/* ------------------------------------------------------------------------------------------- *
 * WEB-FEAT-366 · criterion 3 — the lanes table's STATE cell carries the lane's last words.
 *
 * `DashboardLanes.tsx` is what §1 names as drawing the lanes table, and `factory.pen`'s
 * `Dashboard · Lanes` (`mTLsb`) is its frame — the `lane/01` card's words line sits between the
 * state line and the `IMPL 4/10` foot, and the `lane/02` (queued) card has none. That pair is the
 * criterion: a lane with words has a two-line state cell, a lane with `null` has a one-line one.
 * ------------------------------------------------------------------------------------------- */

const blockCards = () =>
  Array.from(screen.getByTestId('dashboard-lanes').querySelectorAll<HTMLElement>('.lane-card'))

describe('4.20 · the lanes table draws the last words', () => {
  it('the_state_cell_carries_the_lanes_last_words', async () => {
    // Two lanes that DISAGREE about whether anything has been said. One row each way is the whole
    // criterion: a fixture where both rows had words cannot tell "drawn when there are words"
    // from "always drawn", and one where neither had any cannot tell it from "never drawn".
    const ROWS = [
      lane({
        lane: 'lane/01', ticket_id: 'WEB-FEAT-004', workspace: 'core',
        state: { process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
                 evidence: 'nothing_claimed', blocked: null, alarm: false },
        last_words: {
          kind: 'file_edit', text: 'Edit server/app/lane/store.cpp',
          at: '2026-09-02T09:59:59Z',
        },
      }),
      lane({
        lane: 'lane/02', ticket_id: 'SRV-FEAT-021', workspace: 'core', role: 'qa',
        state: { process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
                 evidence: 'nothing_claimed', blocked: null, alarm: false },
        detail: { waiting_for: 'board-master' },
        last_words: null,
      }),
    ]
    await mountApp('#/', {
      '/api/home': { body: home({ delivery: OCCUPIED }) },
      '/api/lanes': { body: laneSet(ROWS) },
    })
    await settle()
    await settle()

    const [running, queued] = blockCards()
    expect(blockCards()).toHaveLength(3)          // two lanes and the design's free seat

    // ── the row with words: a SECOND line under the state line, carrying the server's string
    const words = within(running).getByTestId('lane-words')
    expect(words.textContent).toBe('Edit server/app/lane/store.cpp')
    // it is the state cell's second line — under the state line, above the foot
    const lines = Array.from(running.children)
    expect(lines.indexOf(words)).toBe(lines.findIndex(
      (n) => n.classList.contains('lane-detail')) + 1)
    expect(lines.indexOf(words)).toBeLessThan(lines.findIndex(
      (n) => n.classList.contains('lane-card-foot')))
    // and the state line it sits under is still there, unreplaced
    expect(running.querySelector('.lane-detail')).toHaveTextContent('a turn is in flight')

    // ── the row with `null`: a ONE-LINE cell. No element, not an empty one.
    expect(within(queued).queryByTestId('lane-words')).toBeNull()
    // and its state line is untouched — E9's own `evidence`, which is what this block draws for
    // any lane carrying one. The words line was ADDED beside it, never folded into it.
    expect(queued.querySelector('.lane-detail')).toHaveTextContent('nothing_claimed')
    expect(queued.querySelectorAll('.lane-detail')).toHaveLength(1)

    // one element on the whole surface, and it belongs to the lane that spoke
    expect(screen.getAllByTestId('lane-words')).toHaveLength(1)
    expect(screen.getByTestId('lane-words')).toBe(words)
  })
})
