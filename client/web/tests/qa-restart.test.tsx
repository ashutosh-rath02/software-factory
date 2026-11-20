import { describe, expect, it, afterEach } from 'vitest'
import { screen, cleanup, fireEvent, act } from '@testing-library/react'

import { mountApp, callsTo, settle, ticketDoc } from './helpers'

/**
 * WEB-FEAT-350 — starting QA from the screen.
 *
 * The operator released a QA lane and asked how to start it again from the web. They could not:
 * `lanes.tsx`'s `claim(ticketId, workspace)` takes no role, so the client can only ever claim
 * `role: "run"`, and the server's role auto-start fires once per ticket
 * (`store.cpp:1794` → `already_started`). Release was a one-way door offered as an ordinary
 * button, whose tooltip was true about the branch it keeps and silent about what it costs.
 *
 * `factory.pen` frame `maEaE` gains a `Start QA` control in the identity row — where the ticket's
 * status already is, which is where a person looks when the status says `awaiting-qa`.
 */
const TICKET = ticketDoc({
  id: 'SRV-FIX-379', title: 'A ticket waiting for a verdict', workspace: 'factory-server',
  status: 'awaiting-qa', parent: 'FAC-EPIC-371',
  path: '/tmp/fixture/docs/tickets/SRV-FIX-379.md',
})

const routes = (over: Record<string, unknown> = {}) => ({
  '/api/tickets/SRV-FIX-379': { body: TICKET },
  '/api/tickets/SRV-FIX-379/report': { status: 404, body: { error: { code: 'not_found' } } },
  '/api/lanes': { body: { lanes: [], resources: [], seats: { host: { total: 10, used: 0 } } } },
  ...over,
})

const laneRow = (role: string, process = 'running') => ({
  lane: 'lane/09', ticket_id: 'SRV-FIX-379', workspace: 'factory-server', role,
  branch: 'ticket/SRV-FIX-379', worktree: '/tmp/w', needs_you: false,
  state: { process, motion: { value: 'producing', silent_for_s: 0, since: null },
           evidence: 'nothing_claimed', stopped: null, blocked: null, alarm: false },
  stage: { index: 8, total: 10, round: 1 }, progress_pct: 80, elapsed_s: 10, cost_usd: 0,
  run_id: 'run-1', session_id: 's1', resources_held: [], detail: null, waiting_for: null,
})

afterEach(cleanup)

describe('WEB-FEAT-350 · QA can be started from the ticket', () => {
  it('test_a_ticket_awaiting_qa_offers_to_start_it', async () => {
    await mountApp('#/tickets/SRV-FIX-379', routes())
    await settle()
    const control = screen.queryByTestId('start-qa')
    expect(control, 'a ticket at awaiting-qa with no QA lane offers no way to start QA').toBeTruthy()
    expect(control!.textContent).toMatch(/start qa/i)
  })

  it('test_start_qa_claims_a_qa_lane_and_starts_a_run_in_it', async () => {
    await mountApp('#/tickets/SRV-FIX-379', routes())
    await settle()
    await act(async () => { fireEvent.click(screen.getByTestId('start-qa')) })
    await settle()

    const claim = callsTo('/api/lanes').find(c => c.method === 'POST')
    expect(claim, 'no lane was claimed').toBeTruthy()
    // the role is the whole point: a `run` lane would be handed the run-ticket prompt and would
    // re-run the round it is supposed to be verifying
    expect(JSON.parse(claim!.body ?? '{}')).toMatchObject({
      ticket_id: 'SRV-FIX-379', role: 'qa',
    })
  })

  /**
   * MANDATORY NEGATIVE — the screen may not offer what the server would refuse. A ticket that
   * already holds a QA lane gets `already_started` back, and an action that answers with an error
   * a person cannot act on is worse than no action.
   */
  it('test_the_control_is_absent_when_qa_is_already_running', async () => {
    await mountApp('#/tickets/SRV-FIX-379', routes({
      '/api/lanes': { body: { lanes: [laneRow('qa')], resources: [],
                              seats: { host: { total: 10, used: 1 } } } },
    }))
    await settle()
    expect(screen.queryByTestId('start-qa'),
           'the screen offers to start QA while a QA lane is already running').toBeNull()
  })

  it('test_the_control_is_absent_when_the_ticket_is_not_awaiting_qa', async () => {
    await mountApp('#/tickets/SRV-FIX-379', routes({
      '/api/tickets/SRV-FIX-379': { body: { ...TICKET, status: 'in-progress' } },
    }))
    await settle()
    expect(screen.queryByTestId('start-qa'),
           'the screen offers to start QA on a ticket that has not asked for it').toBeNull()
  })
})
