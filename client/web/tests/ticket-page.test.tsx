import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import { delivery, mountApp, settle, ticketDoc, ticketRoutes } from './helpers'

/**
 * SPEC.md §4's three standing rules, on the surface this ticket built: empty, and socket down.
 *
 * The strip is the interesting half. It comes from the DOCUMENT, so a dropped socket must not
 * touch it — while the chips come from a lane and must dim and hold. Two sources, two behaviours,
 * and a screen that dimmed both would be telling the reader the ticket file had gone stale.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** A ticket that has never run: no lane anywhere, and stage 0 of its eight. */
const NEVER_RAN = ticketDoc({
  status: 'todo',
  stage: { index: 0, total: 8, round: 1, of: 1, source: 'manifest' },
  state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
})

describe('4.4 / 4.5 · empty and socket down', () => {
  it('empty_and_socket_down', async () => {
    const app = await mountApp('#/tickets/TKT-142', ticketRoutes(NEVER_RAN, []))
    await settle()
    await settle()
    screen.getByTestId('ticket-header')

    // ── empty · the three chips read their not_started value, and there is no fourth
    const chips = screen.getAllByTestId('state-chip')
    expect(chips).toHaveLength(3)
    expect(chips[0]).toHaveTextContent('PROCESS · NOT STARTED')
    expect(chips[1]).toHaveTextContent('MOTION · —')
    expect(chips[2]).toHaveTextContent('EVIDENCE · NOTHING CLAIMED')

    // ── empty · every stage chip is a ghost: none done, none active
    const stages = Array.from(
      screen.getByTestId('stage-strip').querySelectorAll('.stage-chip'))
    expect(stages).toHaveLength(8)
    expect(stages.map((s) => s.getAttribute('data-done'))).toEqual(stages.map(() => 'false'))
    expect(stages.map((s) => s.getAttribute('data-active'))).toEqual(stages.map(() => 'false'))

    // ── empty · the rail is the design's own block, with the action it names
    const empty = screen.getByTestId('rail-empty')
    expect(screen.getByTestId('lane-rail').contains(empty)).toBe(true)
    expect(empty).toHaveTextContent('NO LANE ASSIGNED')

    // and nothing that would send anything to an agent is on this surface at all
    expect(screen.getByTestId('surface').querySelectorAll('textarea')).toHaveLength(0)

    // ── socket down
    const stripBefore = screen.getByTestId('stage-strip').innerHTML
    const captionBefore = screen.getByTestId('stage-caption').textContent
    await act(async () => { app.socket().onclose?.() })
    await settle()

    expect(screen.getByTestId('reconnect')).toHaveTextContent('SOCKET LOST · RECONNECTING')

    // the chips dim and keep their last value …
    const after = screen.getAllByTestId('state-chip')
    expect(after.map((c) => c.getAttribute('data-dim'))).toEqual(['true', 'true', 'true'])
    expect(after[0]).toHaveTextContent('PROCESS · NOT STARTED')
    expect(after[1]).toHaveTextContent('MOTION · —')
    expect(after[2]).toHaveTextContent('EVIDENCE · NOTHING CLAIMED')

    // … while the strip is untouched, because it comes from the document
    expect(screen.getByTestId('stage-strip').innerHTML).toBe(stripBefore)
    expect(screen.getByTestId('stage-strip').getAttribute('data-dim')).toBeNull()
    expect(screen.getByTestId('stage-caption').textContent).toBe(captionBefore)
  })
})

/**
 * §3.5 on the surface a person opens to look at ONE ticket.
 *
 * `DeliveryBadge` was rendered by `RequirementsPage` and `RequirementPage` and by nothing else, so
 * the ticket page showed the document's claim and hid the tool's disagreement with it. `FEAT-040`
 * in this repository is what that costs: six commits and a PASSing QA verdict in the lane's
 * worktree, `status: todo` in the document on disk, E5 answering
 * `unshipped · claimed todo · disagrees: true` — and a page reading `NOT STARTED` with an unlit
 * strip. Every part of that was working as built. Nobody drew the one field that told the truth.
 */
describe('4.5 · the ticket surface says what actually happened', () => {
  it('a_document_the_disk_disagrees_with_says_so_on_the_ticket_page', async () => {
    const stranded = ticketDoc({
      status: 'todo',
      stage: { index: 0, total: 8, round: 1, of: 1, source: 'manifest' },
      state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
      needs_you: true,
      delivery: delivery({
        state: 'unshipped', claimed: 'todo', disagrees: true, needs_you: true,
        detail: 'uncommitted changes in /repo/.factory/worktrees/lane/01',
      }),
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(stranded, []))
    await settle()
    await settle()

    const badge = screen.getByTestId('delivery-badge')
    expect(badge.getAttribute('data-state')).toBe('unshipped')
    expect(badge.getAttribute('data-disagrees')).toBe('true')
    // the disagreement is the valuable half: the derived state AND the claim it contradicts
    expect(screen.getByTestId('delivery-disagreement')).toHaveTextContent('document says todo')
    // ...and the reason a person can act on is reachable without another click
    expect(badge.getAttribute('title'))
      .toContain('uncommitted changes in /repo/.factory/worktrees/lane/01')

    // F17 is untouched: the badge is not a fourth axis chip
    expect(screen.getAllByTestId('state-chip')).toHaveLength(3)
  })

  it('a_ticket_with_nothing_to_report_gets_no_badge_at_all', async () => {
    // NEGATIVE (mandatory). The cheap version of this change is a badge that is always there,
    // which makes `not_started` render as a permanent grey decoration on every ordinary ticket
    // and teaches a reader to stop looking at it. A state with no entry in the vocabulary draws
    // nothing — the same rule the axis chips follow.
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), []))
    await settle()
    await settle()

    screen.getByTestId('ticket-header')
    expect(screen.queryByTestId('delivery-badge')).toBeNull()
    expect(screen.queryByTestId('delivery-disagreement')).toBeNull()
    expect(screen.getAllByTestId('state-chip')).toHaveLength(3)
  })
})

/**
 * §4.5 — a live run that has told the tool nothing is not the empty state.
 *
 * `FEAT-040` produced output for minutes with `PROCESS · RUNNING` beside ten grey chips and the
 * caption `stage 0/10 · ticket sequence` — byte for byte what a ticket nobody has ever claimed
 * shows. Two different situations, one picture, and the person watching cannot tell which they
 * are in.
 */
describe('4.5 · a running lane that has reported no stage says so', () => {
  const RUNNING_LANE = {
    lane: 'lane/02', ticket_id: 'TKT-142', workspace: 'core', role: 'run',
    branch: 'ticket/TKT-142', worktree: '/tmp/fixture/.factory/worktrees/lane/02',
    state: { process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
             evidence: 'nothing_claimed', blocked: null, alarm: false },
    needs_you: false, detail: null,
    stage: { index: 0, total: 8, round: 1, of: 1, source: 'manifest' },
    elapsed_s: 28, cost_usd: 0,
  }

  it('a_running_lane_with_no_report_is_not_drawn_as_never_started', async () => {
    const doc = ticketDoc({
      stage: { index: 0, total: 8, round: 1, of: 1, source: 'manifest' },
      state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(doc, [RUNNING_LANE]))
    await settle()
    await settle()

    const caption = screen.getByTestId('stage-caption').textContent ?? ''
    expect(caption).not.toBe('stage 0/8 · manifest sequence')
    expect(caption.toLowerCase()).toContain('no stage reported')
    // ...and it still says how long the sequence is, because that fact did not go away
    expect(caption).toContain('8')
  })

  it('no_chip_is_tinted_for_a_stage_nobody_reported', async () => {
    // NEGATIVE (mandatory). The cheap fix is to light the first chip while a run is live, which
    // would claim SPEC was entered on the word of a process that never said so — the one thing
    // `lane/progress.h` forbids in writing. The words may explain; the strip may not invent.
    const doc = ticketDoc({
      stage: { index: 0, total: 8, round: 1, of: 1, source: 'manifest' },
      state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(doc, [RUNNING_LANE]))
    await settle()
    await settle()

    const chips = Array.from(
      screen.getByTestId('stage-strip').querySelectorAll('.stage-chip'))
    expect(chips.map((c) => c.getAttribute('data-active'))).toEqual(chips.map(() => 'false'))
    expect(chips.map((c) => c.getAttribute('data-done'))).toEqual(chips.map(() => 'false'))
  })

  it('a_ticket_nobody_is_running_keeps_the_plain_caption', async () => {
    // The change must not leak into the empty state it exists to be distinguished FROM.
    await mountApp('#/tickets/TKT-142', ticketRoutes(NEVER_RAN, []))
    await settle()
    await settle()
    expect(screen.getByTestId('stage-caption').textContent).toBe('stage 0/8 · manifest sequence')
  })
})
