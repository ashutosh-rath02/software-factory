import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent, within } from '@testing-library/react'

import {
  calls, callsTo, lane, laneSet, mountApp, resource, settle, ticketDoc, ticketRoutes,
  type Reply, type Routes,
} from './helpers'

/**
 * WEB-FEAT-044 — SPEC.md E12 and E13, and the two empty states that become controls.
 *
 * The client has read lanes since WEB-FEAT-013 and has never made one. §4.4's `NO LANE ASSIGNED`
 * and §4.7's `Start a lane to send a message.` were both dead ends: they named the action and
 * offered no way to take it, which is the shape of a tool nobody can use without a second terminal.
 *
 * Two rules are load-bearing here and both are asserted rather than described:
 *
 *   1. **E12's `201` is E9's own lane object**, so the rail shows the new lane off the RESPONSE.
 *      There is no refetch, no `M11` to wait for, and nothing on a timer — a claim that needed a
 *      second request to become visible would be a claim that looks broken on a slow socket.
 *   2. **A held exclusive resource is not an error.** SPEC.md E12 says the lane is created `queued`
 *      with `waiting_for` set, and `LaneCard` has drawn that sub-line since WEB-FEAT-013 without
 *      anything ever producing one. This is the case that produces one.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

/** The name the server gives the lane it creates — E9's own spelling, with the slash in it. */
const CLAIMED = 'lane/07'
const RUNS = `/api/lanes/${CLAIMED}/runs`

/** A lane the server has just created for the open ticket: claimed, nothing started in it yet. */
const fresh = (over: Record<string, unknown> = {}) => lane({
  lane: CLAIMED, ticket_id: 'TKT-142', workspace: 'edge', branch: 'ticket/TKT-142',
  state: {
    process: 'not_started', motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  needs_you: false, stage: { index: 0, total: 8, round: 1 }, progress_pct: 0,
  elapsed_s: 0, cost_usd: 0, run_id: null, session_id: null, waiting_for: null,
  ...over,
})

/** The same claim, refused for want of a seat — E12's `409 no_seat`, verbatim from the C++. */
const NO_SEAT: Reply = {
  status: 409,
  body: {
    error: {
      code: 'no_seat',
      message: 'every host seat is taken; release a lane or raise lanes.host',
      detail: { total: 3, used: 3 },
    },
  },
}

interface World {
  /** what E9 answers with, mutated by the fake server exactly as the real one would be */
  rows: Array<Record<string, unknown>>
  resources: Array<Record<string, unknown>>
  /** every E12 body, parsed — the assertion is about what the client SENT */
  claims: Array<Record<string, unknown>>
  /** every E13, as `lane` and whether `?force=true` was on it */
  releases: Array<{ lane: string; force: boolean; discardIgnored: boolean }>
}

/**
 * The lane endpoints, as one fake server.
 *
 * `/api/lanes` is E9 and E12 at the same path, so the stub answers on the METHOD — a route that
 * could not tell them apart would make the read and the write share an answer, which is the one
 * thing this ticket is about.
 */
function lanes(world: World, claim: (body: Record<string, unknown>) => Reply | Promise<Reply>,
               release: Reply | ((force: boolean) => Reply) = { body: { lane: CLAIMED,
                 released: true, worktree_removed: true, branch_kept: true } }): Routes {
  return {
    '/api/lanes': (_url, init) => {
      if ((init?.method ?? 'GET') !== 'POST') {
        return { body: laneSet(world.rows, world.resources) }
      }
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
      world.claims.push(body)
      return claim(body)
    },
    [`/api/lanes/${CLAIMED}`]: (url, init) => {
      const force = url.searchParams.get('force') === 'true'
      const discardIgnored = url.searchParams.get('discard_ignored') === 'true'
      world.releases.push({ lane: CLAIMED, force, discardIgnored })
      if ((init?.method ?? 'GET') !== 'DELETE') return { status: 405, body: {} }
      return typeof release === 'function' ? release(force) : release
    },
    // E18 is WEB-FEAT-045's, and it is stubbed here because `Start a lane` chains to it: a claim
    // whose run could not be started must not make a CLAIM assertion fail for the wrong reason.
    [RUNS]: { status: 201, body: { run_id: 'run-1', session_id: 'sess-1', pid: 4242 } },
  }
}

const emptyWorld = (over: Partial<World> = {}): World =>
  ({ rows: [], resources: [], claims: [], releases: [], ...over })

async function mount(routes: Routes, doc: Record<string, unknown> = {}) {
  const app = await mountApp('#/tickets/TKT-142',
    { ...ticketRoutes(ticketDoc({ workspace: 'edge', lane: null, ...doc }), []), ...routes })
  await settle()
  await settle()
  return app
}

const reads = () => callsTo('/api/lanes').filter((c) => c.method === 'GET')
const writes = () => callsTo('/api/lanes').filter((c) => c.method === 'POST')
const cards = () => screen.queryAllByTestId('lane-card')

/**
 * The composer owns the shared no-lane control while SESSION is visible. Other tabs expose the
 * same component in the rail, but the two seats must never be visible together.
 */
const composerClaim = () =>
  within(screen.getByTestId('composer')).getByTestId('claim-lane') as HTMLButtonElement

describe('E12 · E13 — claiming a lane and releasing it', () => {
  it('test_claims_with_the_tickets_own_workspace', async () => {
    const world = emptyWorld()
    await mount(lanes(world, () => {
      // the real server holds it from this moment — and the client must show it WITHOUT asking
      world.rows.push(fresh())
      return { status: 201, body: fresh() }
    }))

    // §4.4's empty block and §4.7's composer share one no-lane action.
    expect(screen.getByTestId('rail-empty')).toHaveTextContent('NO LANE ASSIGNED')
    expect(screen.getByTestId('composer')).toHaveTextContent('Start a lane to send a message.')
    expect(screen.getAllByTestId('claim-lane')).toHaveLength(1)
    expect(within(screen.getByTestId('rail-empty')).queryByTestId('claim-lane')).toBeNull()
    expect(composerClaim()).toHaveTextContent('Start a lane')
    expect(writes()).toHaveLength(0)          // nothing is claimed on mount

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    // E12, with the ticket's OWN `workspace:` — not the manifest's first, and no chooser (§1)
    expect(world.claims).toEqual([{ ticket_id: 'TKT-142', workspace: 'edge', role: 'run' }])

    // the lane is in the rail, drawn from the 201 body …
    expect(cards()).toHaveLength(1)
    expect(cards()[0].getAttribute('data-lane')).toBe(CLAIMED)
    expect(cards()[0].getAttribute('data-selected')).toBe('true')
    expect(screen.getByTestId('lane-count')).toHaveTextContent('1')
    expect(screen.queryByTestId('rail-empty')).toBeNull()
    expect(screen.queryByTestId('claim-error')).toBeNull()

    // … and E9 was read ONCE, at mount, and never again
    expect(reads()).toHaveLength(1)
  })

  it('test_a_busy_resource_creates_a_queued_lane_not_an_error', async () => {
    // SPEC.md E12: "If a resource is held, the lane is created `queued` with `waiting_for` set — a
    // queued lane is a real lane, visible in the rail, not a failed request." So there is no
    // `409 resource_busy` on this endpoint to render, and a client that drew one would be drawing
    // a refusal the server never sent.
    const queued = fresh({
      state: {
        process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
        evidence: 'nothing_claimed', blocked: null, alarm: false,
      },
      waiting_for: { resource: 'climate-chamber', held_by: 'lane/02', position: 1 },
    })
    const world = emptyWorld({
      resources: [resource({ held_by: 'lane/02', queue: [CLAIMED] })],
    })
    await mount(lanes(world, () => {
      world.rows.push(queued)
      return { status: 201, body: queued }
    }))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    // NOT an error: a card
    expect(screen.queryByTestId('claim-error')).toBeNull()
    expect(cards()).toHaveLength(1)
    const card = cards()[0]
    expect(card.getAttribute('data-lane')).toBe(CLAIMED)
    expect(card).toHaveTextContent('queued')

    // and the sub-line `LaneCard` has drawn since WEB-FEAT-013 and nothing has ever produced:
    // the resource, WHAT IT IS, who is holding it and where this lane is in the line
    const sub = within(card).getByTestId('lane-sub')
    expect(sub).toHaveTextContent('queued for climate-chamber')
    expect(sub).toHaveTextContent('one chamber, one run at a time')
    expect(sub).toHaveTextContent('held by lane/02')
    expect(sub).toHaveTextContent('#1')

    // a lane waiting for a resource starts nothing — E18 would answer `409 resource_busy`, and
    // asking anyway is how a person gets an error message for a state that is working correctly
    expect(calls.filter((c) => c.url === RUNS)).toHaveLength(0)
    expect(reads()).toHaveLength(1)
  })

  it('test_release_confirms_and_says_what_it_kept', async () => {
    const row = fresh({ run_id: null })
    const world = emptyWorld({ rows: [row] })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await mount(lanes(world, () => ({ status: 500, body: {} })),
                { lane: CLAIMED })

    expect(cards()).toHaveLength(1)
    const release = within(cards()[0]).getByTestId('lane-release')

    await act(async () => { fireEvent.click(release) })
    await settle()

    // it asks first, and the sentence says what survives — E13 keeps the branch, always
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(confirm.mock.calls[0][0]).toBe('Release lane? The branch is kept.')

    // E13, on the lane's own path, with no `force`
    expect(world.releases).toEqual([{ lane: CLAIMED, force: false, discardIgnored: false }])
    const deletes = calls.filter((c) => c.method === 'DELETE')
    expect(deletes).toHaveLength(1)
    expect(deletes[0].url).toBe(`/api/lanes/${CLAIMED}`)

    // and it is gone from the rail, off the 200 — no refetch
    expect(cards()).toHaveLength(0)
    expect(screen.getByTestId('rail-empty')).toHaveTextContent('NO LANE ASSIGNED')
    expect(reads()).toHaveLength(1)
  })

  it('test_a_live_process_is_offered_force_and_the_release_says_so', async () => {
    // E13's `409 wrong_state`: "a live process, unless `force=true`, which kills it first". The
    // second question is a second question on purpose — a release that killed a running agent
    // because one dialog was clicked through is the failure this product exists to not have.
    const world = emptyWorld({ rows: [fresh({ run_id: 'run-4', session_id: 'sess-1' })] })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await mount(lanes(world, () => ({ status: 500, body: {} }), (force) => (force
      ? { body: { lane: CLAIMED, released: true, worktree_removed: false, branch_kept: true } }
      : {
        status: 409,
        body: {
          error: {
            code: 'wrong_state',
            message: 'a process is running in this lane; release it with force=true to kill it '
                   + 'first',
            detail: { state: 'running', lane: CLAIMED, pid: 4242 },
          },
        },
      })), { lane: CLAIMED })

    await act(async () => { fireEvent.click(within(cards()[0]).getByTestId('lane-release')) })
    await settle()

    expect(confirm).toHaveBeenCalledTimes(2)
    expect(confirm.mock.calls[1][0]).toBe('A process is still running. Force?')
    expect(world.releases).toEqual([
      { lane: CLAIMED, force: false, discardIgnored: false },
      { lane: CLAIMED, force: true, discardIgnored: false },
    ])
    expect(cards()).toHaveLength(0)
  })

  it('test_a_second_claim_while_one_is_in_flight_does_not_create_two_lanes', async () => {
    // ── MANDATORY NEGATIVE ────────────────────────────────────────────────────────────────────
    // A claim writes a branch and a worktree and takes a seat. Two of them for one ticket is two
    // directories, two seats and a `409 lane_exists` that arrives after the damage, so the guard
    // belongs in front of the request: the control is disabled while one is in flight, and the
    // seam refuses a second even if something clicks it anyway.
    let answer!: (reply: Reply) => void
    const inFlight = new Promise<Reply>((resolve) => { answer = resolve })
    const world = emptyWorld()
    await mount(lanes(world, () => inFlight))

    const start = composerClaim()
    await act(async () => { fireEvent.click(start) })
    await settle()

    // one request out, and BOTH seats say so rather than looking clickable — the seat and the
    // branch a claim is about to take are one resource, and there are two ways to reach it
    expect(writes()).toHaveLength(1)
    expect(start.disabled).toBe(true)
    expect(composerClaim().disabled).toBe(true)
    expect(start).toHaveTextContent('Starting…')

    // clicked again anyway — by a double tap, by an Enter the browser repeated, or from the seat
    // that is not the one the person started from
    await act(async () => { fireEvent.click(start) })
    await act(async () => { fireEvent.click(start) })
    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()
    expect(writes()).toHaveLength(1)

    world.rows.push(fresh())
    await act(async () => { answer({ status: 201, body: fresh() }) })
    await settle()

    // ONE lane, and one claim ever sent
    expect(cards()).toHaveLength(1)
    expect(world.claims).toHaveLength(1)
    expect(reads()).toHaveLength(1)
  })

  it('test_no_seat_names_the_host_limit_and_the_counts', async () => {
    // E12's other refusal, and the only one that is genuinely an error. `lanes.host` is a manifest
    // key, so the message has to name it: "wait" is not an action, and "raise lanes.host" is.
    const world = emptyWorld()
    await mount(lanes(world, () => NO_SEAT))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    const error = screen.getByTestId('claim-error')
    expect(error).toHaveTextContent('lanes.host')
    expect(error).toHaveTextContent('3 of 3')
    expect(cards()).toHaveLength(0)
    // and the control is live again — a refusal a person can act on is a refusal they can retry
    expect(composerClaim().disabled).toBe(false)
  })

  /**
   * SRV-FIX-059 QA, finding 2 — the refusal that carried its own next step, and no screen read it.
   *
   * After a restart the tool holds no lanes, so `GET /api/lanes` is `{"lanes":[]}` and there is no
   * lane row and no Release control anywhere on the screen. The only thing a person can press is
   * `Start a lane`, and E12 answers `409 worktree_in_the_way` — with `detail.clear_with` naming the
   * exact call that fixes it, in a body the client rendered `message` out of and nothing else. The
   * way out existed and was reachable only by somebody holding `curl`.
   */
  const IN_THE_WAY = (over: Record<string, unknown> = {}): Reply => ({
    status: 409,
    body: {
      error: {
        code: 'worktree_in_the_way',
        message: 'something is already at the path this lane would use',
        detail: {
          path: '/repo/.factory/worktrees/' + CLAIMED,
          git: "fatal: '...' already exists",
          ours: true, lane: CLAIMED, uncommitted_changes: 0,
          clear_with: `DELETE /api/lanes/${CLAIMED}?force=true`,
          ...over,
        },
      },
    },
  })

  it('test_a_leftover_in_the_way_can_be_cleared_from_the_screen', async () => {
    const world = emptyWorld()
    let refuseOnce = true
    await mount(lanes(world, () => {
      if (refuseOnce) { refuseOnce = false; return IN_THE_WAY() }
      world.rows = [fresh()]
      return { status: 201, body: fresh() }
    }))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    // the sentence from the user's screenshot, and now a control beside it
    expect(screen.getByTestId('claim-error'))
      .toHaveTextContent('something is already at the path this lane would use')
    const out = screen.getAllByTestId('claim-clear-leftover')[0] as HTMLButtonElement
    expect(out).toBeTruthy()
    expect(screen.getAllByTestId('claim-leftover-path')[0])
      .toHaveTextContent('/repo/.factory/worktrees/' + CLAIMED)

    await act(async () => { fireEvent.click(out) })
    await settle()
    await settle()

    // E13 with force, then E12 again — and the lane the person asked for
    expect(world.releases).toEqual([{ lane: CLAIMED, force: true, discardIgnored: false }])
    expect(world.claims).toHaveLength(2)
    expect(cards()).toHaveLength(1)
    expect(screen.queryAllByTestId('claim-clear-leftover')).toHaveLength(0)
  })

  it('test_a_leftover_holding_ignored_content_shows_the_names_and_asks_before_clearing', async () => {
    // SRV-FIX-059 round 5. The shape of leftover a working repository actually produces: a lane
    // that ran its declared build once, so the directory holds paths git was told to ignore.
    //
    // Round 4 made the clear refuse on those rather than delete them silently, which left the
    // person at a wall — and the claim refusal beside it still said `uncommitted_changes: 0`, an
    // affirmative statement that there was nothing to lose, about a directory holding a capture
    // that cannot be got back. The names ARE the answer: a person who can read them can decide.
    const world = emptyWorld()
    let refuseOnce = true
    await mount(lanes(world, () => {
      if (refuseOnce) {
        refuseOnce = false
        return IN_THE_WAY({
          ignored: ['evidence/agent_app_1.log', 'evidence/build/'],
          clear_with: `DELETE /api/lanes/${CLAIMED}?discard_ignored=true`,
        })
      }
      world.rows = [fresh()]
      return { status: 201, body: fresh() }
    }))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    // the paths themselves, not a count — `ignored_files: 3` is the answer that sent a person here
    const named = screen.getAllByTestId('claim-leftover-ignored')[0]
    expect(named).toHaveTextContent('evidence/agent_app_1.log')
    expect(named).toHaveTextContent('evidence/build/')

    // ...and the control that acts on them is its own, and says what it destroys
    const consent = screen.getAllByTestId('claim-discard-ignored')[0] as HTMLButtonElement
    expect(consent).toBeTruthy()
    expect(consent.textContent?.toLowerCase()).toMatch(/discard|delete|lose/)

    // WEB-FEAT-179 — the click that REVEALS what this costs is no longer the click that pays it.
    // §1: `discard_ignored` "stays behind a confirmation that names the files". The call this case
    // was written to assert is unchanged and still the one made; what moved is that it takes a
    // second, deliberate act, and the confirmation is where SPEC.md §9.3's nouns are said.
    await act(async () => { fireEvent.click(consent) })
    await settle()
    expect(world.releases).toEqual([])

    const confirm = screen.getByTestId('claim-discard-confirm')
    expect(confirm).toHaveTextContent('evidence/agent_app_1.log')
    expect(confirm).toHaveTextContent('evidence/build/')

    await act(async () => { fireEvent.click(within(confirm).getByTestId('claim-discard-fire')) })
    await settle()
    await settle()

    expect(world.releases).toEqual([{ lane: CLAIMED, force: false, discardIgnored: true }])
    expect(world.claims).toHaveLength(2)
    expect(cards()).toHaveLength(1)
  })

  it('test_no_ignored_content_is_cleared_without_a_deliberate_second_act', async () => {
    // NEGATIVE (mandatory). The cheap fix is to send `discard_ignored` from the ordinary way out,
    // so the wall goes away for everybody — including the person who never saw a list, because
    // there was nothing to list. One click would then answer a question nobody was asked.
    const world = emptyWorld()
    let refuseOnce = true
    await mount(lanes(world, () => {
      if (refuseOnce) { refuseOnce = false; return IN_THE_WAY() }   // clean leftover, no `ignored`
      world.rows = [fresh()]
      return { status: 201, body: fresh() }
    }))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    // nothing to read, so nothing to consent to, and no consenting control on the screen
    expect(screen.queryAllByTestId('claim-leftover-ignored')).toHaveLength(0)
    expect(screen.queryAllByTestId('claim-discard-ignored')).toHaveLength(0)

    await act(async () => { fireEvent.click(screen.getAllByTestId('claim-clear-leftover')[0]) })
    await settle()
    await settle()

    expect(world.releases.every((r) => r.discardIgnored === false)).toBe(true)
  })

  it('test_a_leftover_that_is_not_ours_is_not_offered_a_button', async () => {
    // NEGATIVE. E13 answers `409 not_ours` for a directory this tool did not create, so a button
    // here would be a control that cannot work — the screen would invite the click and the server
    // would refuse it. ESCAPES.md row 1 is about the tool touching what is not its own; this is
    // the same rule one layer up, where it decides what a person is even offered.
    const world = emptyWorld()
    await mount(lanes(world, () => IN_THE_WAY({ ours: false })))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    expect(screen.getByTestId('claim-error'))
      .toHaveTextContent('something is already at the path this lane would use')
    expect(screen.queryAllByTestId('claim-clear-leftover')).toHaveLength(0)
    expect(world.releases).toHaveLength(0)
  })

  it('test_a_leftover_holding_work_says_so_and_does_not_retry', async () => {
    // E13's whole job here is to refuse a leftover that holds work. Retrying the claim after that
    // refusal would replace the sentence explaining WHY with a second copy of the sentence that
    // sent the person here, and the reason the checkout survived would never reach the screen.
    const world = emptyWorld()
    await mount(lanes(world, () => IN_THE_WAY(), () => ({
      status: 409,
      body: {
        error: {
          code: 'unmerged_commits',
          message: "this lane's leftover checkout is on a branch carrying work no other branch "
                 + 'has; it will not be removed',
          detail: { lane: CLAIMED, ours: true, branch: 'ticket/TKT-142', commits: 2 },
        },
      },
    })))

    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()
    await act(async () => {
      fireEvent.click(screen.getAllByTestId('claim-clear-leftover')[0])
    })
    await settle()

    // WEB-FEAT-179 — the sentence is unchanged and it has moved into the refusal BLOCK, which is
    // where the rest of the body now goes too. `unmerged_commits` is one of the three E13 refusals
    // that arrived as `error.message` alone; `detail.branch` and `detail.commits` were in the same
    // body and reached no screen, and the whole of what a person decides here is WHICH branch and
    // HOW MUCH is on it.
    expect(screen.getByTestId('git-refusal'))
      .toHaveTextContent('carrying work no other branch has')
    expect(screen.getByTestId('git-refusal-branch')).toHaveTextContent('ticket/TKT-142')
    expect(screen.getByTestId('git-refusal-branch')).toHaveTextContent('2 commits')
    expect(world.claims).toHaveLength(1)
    expect(cards()).toHaveLength(0)
  })

  it('test_a_lost_socket_disables_the_claim', async () => {
    // SPEC.md §4.4's socket-down rule, in its own words: "cards keep their last state, clocks stop,
    // `Start a lane` is disabled". A claim allocates a seat, a branch and a worktree, and one made
    // over a dead socket allocates all three to a screen that will never be told about any of them.
    const world = emptyWorld()
    const app = await mount(lanes(world, () => ({ status: 500, body: {} })))
    expect(composerClaim().disabled).toBe(false)

    await act(async () => { app.socket().onclose?.() })
    await settle()

    expect(composerClaim().disabled).toBe(true)
    expect(composerClaim().disabled).toBe(true)
    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()
    expect(writes()).toHaveLength(0)
  })

  it('test_the_needs_you_chip_goes_to_the_first_lane_that_needs_a_person', async () => {
    // §4.1's chip has counted since WEB-FEAT-013 and gone nowhere. The count is the SERVER's and
    // stays so; what WEB-FEAT-044 added is that it is reachable — the first lane E9 reported as
    // needing a person, in E9's own order, because the number and the destination must come from
    // one answer.
    //
    // ── CHANGED BY WEB-FEAT-050, and deliberately ────────────────────────────────────────────
    // This case asserted `#/tickets/TKT-147` for a set where TWO lanes need a person. §4.1 now
    // specifies the destination as the answer to the question the chip asked: ONE lane and it goes
    // to that lane's ticket; more than one and it cannot name them, so it goes to §4.14, which can.
    // The behaviour the old line asserted — take me to the FIRST of several — is the one thing the
    // chip should not do now that it is the only route to a lane on a ticket nobody has open: it
    // would silently pick one of two and hide the other. Nothing here was lowered to match the
    // code; the second half of the case asserts WEB-FEAT-044's original rule where it still holds.
    const world = emptyWorld({
      rows: [
        fresh({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
        fresh({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: true }),
        fresh({ lane: 'lane/03', ticket_id: 'TKT-151', needs_you: true }),
      ],
    })
    await mount(lanes(world, () => ({ status: 500, body: {} })))

    const chip = screen.getByTestId('needs-you-chip')
    expect(chip).toHaveTextContent('2 lanes need you')
    expect(chip.tagName).toBe('A')
    expect(chip.getAttribute('href')).toBe('#/lanes')

    cleanup()

    // one lane, and it is still WEB-FEAT-044's own destination: the ticket, in E9's order
    const alone = emptyWorld({
      rows: [
        fresh({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
        fresh({ lane: 'lane/02', ticket_id: 'TKT-147', needs_you: true }),
      ],
    })
    await mount(lanes(alone, () => ({ status: 500, body: {} })))

    const single = screen.getByTestId('needs-you-chip')
    expect(single).toHaveTextContent('1 lane needs you')
    expect(single.getAttribute('href')).toBe('#/tickets/TKT-147')
  })
})
