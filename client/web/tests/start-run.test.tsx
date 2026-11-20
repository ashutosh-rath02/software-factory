import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import {
  calls, callsTo, lane, laneSet, mountApp, settle, ticketDoc, ticketRoutes, MANIFEST,
  type Reply, type Routes,
} from './helpers'
import { deliver, LANE } from './session'

/**
 * WEB-FEAT-045 — SPEC.md E18. The composer's first Send starts a run.
 *
 * A claimed lane is a worktree, a branch and a seat with NOTHING RUNNING IN IT. Until this ticket
 * the composer's only verb was E14, which answers `409 wrong_state` on exactly that lane — so the
 * one action that turns a claimed lane into a working one was unreachable from the browser, and
 * WEB-FEAT-044 shipped a control that could create a lane nobody could use.
 *
 * Which endpoint a Send goes to is decided from §3.2's PROCESS axis and nothing else:
 * `running`, `idle` and `blocked` are the three values `lane::state::processOf` reports when a
 * process is alive (`app/lane/state.cpp:50-62`), so anything else means there is no other end for a
 * message and the first Send starts one. A client that guessed from `run_id` would send E14 into a
 * lane whose run crashed an hour ago and call the 409 a bug.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const RUNS = `/api/lanes/${LANE}/runs`
const MESSAGES = `/api/lanes/${LANE}/messages`
const HISTORY = '/api/tickets/TKT-142/runs'
const AGENTS = { body: { ...MANIFEST, default_agent: 'claude', agents: {
  claude: { driver: 'claude', command: 'claude', default_model: 'claude-test', models: ['claude-test'] },
  codex: { driver: 'codex', command: 'codex', default_model: 'gpt-test', models: ['gpt-test', 'gpt-other'] },
} } }
const STARTED: Reply = { status: 201, body: { run_id: 'run-9', session_id: 'sess-9', pid: 4242 } }

interface PenNode { name?: string; content?: string; children?: PenNode[] }
const pen = JSON.parse(readFileSync(join(__dirname, '../design/factory.pen'), 'utf8')) as PenNode
const named = (node: PenNode, name: string): PenNode | undefined => {
  if (node.name === name) return node
  for (const child of node.children ?? []) {
    const match = named(child, name)
    if (match) return match
  }
}
const copy = (node: PenNode): string[] => [
  ...(node.content ? [node.content] : []),
  ...(node.children ?? []).flatMap(copy),
]

/** A lane WEB-FEAT-044 has just claimed: a worktree and a branch, and nothing running in it. */
const claimed = (over: Record<string, unknown> = {}) => lane({
  lane: LANE, ticket_id: 'TKT-142', workspace: 'core', branch: 'ticket/TKT-142',
  state: {
    process: 'not_started', motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  needs_you: false, stage: { index: 0, total: 8, round: 1 }, progress_pct: 0,
  elapsed_s: 0, cost_usd: 0, run_id: null, session_id: null, waiting_for: null,
  ...over,
})

/** The same lane once a run is live in it — what E9 says after E18 has answered. */
const live = (over: Record<string, unknown> = {}) => claimed({
  state: {
    process: 'running', motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  run_id: 'run-4', session_id: 'sess-1',
  ...over,
})

const box = () => screen.getByTestId('composer-draft') as HTMLTextAreaElement
const composerClaim = () =>
  within(screen.getByTestId('composer')).getByTestId('claim-lane') as HTMLButtonElement
const button = () => screen.getByTestId('composer-button')
const posts = (path: string) => calls.filter((c) => c.method === 'POST' && c.url === path)
const bodyOf = (path: string) =>
  posts(path).map((c) => JSON.parse(String(c.body ?? '{}')) as Record<string, unknown>)

async function type(text: string) {
  await act(async () => { fireEvent.change(box(), { target: { value: text } }) })
  await settle()
}

/** The SESSION tab of a ticket whose lane is whatever the case says it is. */
async function mountSession(rows: Array<Record<string, unknown>>, over: Routes = {}) {
  const app = await mountApp('#/tickets/TKT-142/session', {
    ...ticketRoutes(ticketDoc({ workspace: 'core', lane: LANE }), rows),
    [RUNS]: STARTED,
    [MESSAGES]: { body: { status: 'delivered', held_count: 0, entry_seq: 9 } },
    ...over,
  })
  await settle()
  await settle()
  return app
}

describe('E18 — starting a run in a claimed lane', () => {
  it('the_design_frame_and_start_controls_share_the_same_states', async () => {
    const frameCopy = copy(named(pen, 'Ticket · Empty')!)
    expect(frameCopy).toEqual(expect.arrayContaining([
      'AGENT', 'MODEL',
      'UNAVAILABLE · Select an available agent and model before starting. Start disabled.',
      'LAUNCH REFUSED · selection and draft stay in place for retry',
      'RESUMED SESSION · agent/model are fixed; selectors are no longer shown',
    ]))

    await mountSession([claimed()], { '/api/manifest': AGENTS })
    expect(screen.getByRole('group', { name: 'Run with' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Agent' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Model' })).toBeVisible()
  })

  it('selects_agent_and_model_before_start', async () => {
    await mountSession([claimed()], { '/api/manifest': AGENTS })
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'codex' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Model' }), { target: { value: 'gpt-other' } })
    await type('go with GPT')
    await act(async () => { fireEvent.click(button()) })
    await settle()
    expect(bodyOf(RUNS)).toEqual([{ resume: true, prompt: 'go with GPT', agent: 'codex', model: 'gpt-other' }])
    expect(screen.queryByRole('combobox', { name: 'Agent' })).toBeNull()
  })

  it('renders_one_no_lane_selector_and_shares_it_with_the_rail_path', async () => {
    await mountApp('#/tickets/TKT-142/session', {
      ...ticketRoutes(ticketDoc({ workspace: 'core', lane: null }), []),
      '/api/manifest': AGENTS,
      '/api/lanes': { body: laneSet([]) },
    })
    await settle()
    await settle()

    expect(screen.getAllByRole('combobox', { name: 'Agent' })).toHaveLength(1)
    expect(screen.getAllByTestId('claim-lane')).toHaveLength(1)
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'codex' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Model' }), { target: { value: 'gpt-other' } })

    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/ticket'
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    await settle()

    expect(screen.getAllByRole('combobox', { name: 'Agent' })).toHaveLength(1)
    expect(screen.getAllByTestId('claim-lane')).toHaveLength(1)
    expect(within(screen.getByTestId('rail-empty')).getByRole('combobox', { name: 'Agent' }))
      .toHaveValue('codex')
    expect(within(screen.getByTestId('rail-empty')).getByRole('combobox', { name: 'Model' }))
      .toHaveValue('gpt-other')
  })

  it('changing_agent_resets_model', async () => {
    await mountSession([claimed()], { '/api/manifest': AGENTS })
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'codex' } })
    expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('gpt-test')
    expect(screen.queryByRole('option', { name: 'claude-test' })).toBeNull()
    fireEvent.change(screen.getByRole('combobox', { name: 'Model' }), { target: { value: 'gpt-other' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'claude' } })
    expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('claude-test')
  })

  it('selection_survives_start_refusal', async () => {
    await mountSession([claimed()], { '/api/manifest': AGENTS,
      [RUNS]: { status: 502, body: { error: { code: 'agent_start_failed', message: 'not ready', detail: {} } } },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'codex' } })
    await type('keep this prompt')
    await act(async () => { fireEvent.click(button()) })
    await settle()
    expect(box()).toHaveValue('keep this prompt')
    expect(screen.getByRole('combobox', { name: 'Agent' })).toHaveValue('codex')
    expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('gpt-test')
  })

  it('invalid_selection_cannot_start', async () => {
    await mountSession([claimed()], { '/api/manifest': AGENTS })
    fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: '' } })
    await type('must not start')
    await act(async () => { fireEvent.click(button()) })
    await settle()
    expect(posts(RUNS)).toHaveLength(0)
    expect(box()).toHaveValue('must not start')
  })

  it('test_start_sends_the_prompt_as_the_first_message', async () => {
    await mountSession([claimed()])

    // §4.7's own placeholder, and the box is there to type into: a claimed lane is somewhere a
    // message can GO, which is the whole difference between this and a ticket with no lane at all
    expect(box().placeholder).toBe('Message the agent…')
    expect(box().disabled).toBe(false)
    expect(button()).toHaveTextContent('Send')

    await type('start with the failing test in tests/test_guard.py')
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()

    // E18, with the typed text as `prompt` and `resume` on — and NOT E14, which would have been
    // a `409 wrong_state` and a message a person had typed and lost
    expect(posts(RUNS)).toHaveLength(1)
    expect(posts(MESSAGES)).toHaveLength(0)
    expect(bodyOf(RUNS)[0]).toEqual({
      resume: true, prompt: 'start with the failing test in tests/test_guard.py',
    })
    expect(box().value).toBe('')
    expect(screen.queryByTestId('composer-refused')).toBeNull()
  })

  it('test_the_composer_enables_when_the_session_exists', async () => {
    const app = await mountSession([claimed()])
    const control = button()

    await type('go')
    await act(async () => { fireEvent.click(control) })
    await settle()
    expect(posts(RUNS)).toHaveLength(1)

    // enabled, and the SAME control in the same seat, still reading `Send` — E18's 201 carries a
    // pid, so the row holds a process from here on and the composer is in its ordinary state
    expect(button()).toBe(control)
    expect(button()).toHaveTextContent('Send')
    expect(button()).not.toBeDisabled()
    expect(box().disabled).toBe(false)

    // and the ordinary state means the ordinary endpoint: the SECOND message is E14, not a second
    // run. Nothing was re-read to learn that — the response said it.
    await type('and now the implementation')
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()
    expect(posts(RUNS)).toHaveLength(1)
    expect(posts(MESSAGES)).toHaveLength(1)
    expect(callsTo('/api/lanes').filter((c) => c.method === 'GET')).toHaveLength(1)

    // …and `Stop` still arrives the only way it ever did, on M2
    await deliver(app, {
      m: 'lane.state', ts: '2026-07-28T10:00:30Z', seq: null, lane: LANE, ticket_id: 'TKT-142',
      state: {
        process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
        evidence: 'nothing_claimed', blocked: null, alarm: false,
      },
      waiting_for: null, needs_you: false,
    })
    expect(button()).toBe(control)
    expect(button()).toHaveTextContent('Stop')
  })

  it('test_the_run_appears_in_runs_without_a_refetch', async () => {
    // The whole chained action, on the tab that shows runs: `Start a lane` claims (E12) and starts
    // (E18) in one click — §1's pre-decision, so a person never meets a claimed-but-empty lane —
    // and then M11 puts the row in the table. No reload, and no second read of E7.
    const rows: Array<Record<string, unknown>> = []
    const app = await mountApp('#/tickets/TKT-142/runs', {
      ...ticketRoutes(ticketDoc({ workspace: 'core', lane: null }), []),
      '/api/lanes': (_url, init) => {
        if ((init?.method ?? 'GET') !== 'POST') return { body: laneSet(rows) }
        rows.push(claimed())
        return { status: 201, body: claimed() }
      },
      [RUNS]: STARTED,
      [HISTORY]: { body: { runs: [] } },
    })
    await settle()
    await settle()

    expect(screen.getByTestId('runs-empty')).toHaveTextContent('No runs recorded for this ticket.')
    expect(callsTo(HISTORY)).toHaveLength(1)

    await act(async () => { fireEvent.click(screen.getByTestId('claim-lane')) })
    await settle()

    // one click, both writes, in that order
    expect(callsTo('/api/lanes').filter((c) => c.method === 'POST')).toHaveLength(1)
    expect(posts(RUNS)).toHaveLength(1)
    expect(bodyOf(RUNS)[0]).toEqual({ resume: true })     // nothing typed ⇒ no `prompt` field

    // M11, which is the run frame — and the row arrives on it
    await deliver(app, {
      m: 'run', ts: '2026-07-28T10:00:02Z', seq: null, lane: LANE, run_id: 'run-9', n: 1,
      outcome: 'running', started_iso: '2026-07-28T10:00:02Z', ended_iso: null,
      stage: 0, duration_s: 0, cost_usd: 0,
    })

    const table = screen.getAllByTestId('run-row')
    expect(table).toHaveLength(1)
    expect(table[0]).toHaveTextContent('#1')
    expect(table[0]).toHaveTextContent('running')
    expect(screen.queryByTestId('runs-empty')).toBeNull()

    // NOTHING was read again: not E7, not E9
    expect(callsTo(HISTORY)).toHaveLength(1)
    expect(callsTo('/api/lanes').filter((c) => c.method === 'GET')).toHaveLength(1)
  })

  it('test_start_is_refused_while_a_run_is_live_and_says_which_run_holds_the_lane', async () => {
    // ── MANDATORY NEGATIVE ────────────────────────────────────────────────────────────────────
    //
    // Two windows on one lane. This one read E9 while the lane was claimed and empty; the other
    // started a run. Nothing tells this window — the server broadcasts no lane frame for a claim,
    // and a `run` frame for a lane it holds patches no run id — so its Send chooses E18 and the
    // server refuses it.
    //
    // E18's `409 wrong_state` detail is `{lane, state:"running"}` (`app/agent/session.cpp:343`) and
    // does NOT name the run. §1 requires the message to. The refusal is proof this row is stale, so
    // E9 is read again ON THAT EVENT — not on a clock — and the run it comes back with is named.
    let reads = 0
    await mountSession([claimed()], {
      '/api/lanes': () => {
        reads += 1
        return { body: laneSet([reads === 1 ? claimed() : live()]) }
      },
      [RUNS]: {
        status: 409,
        body: {
          error: {
            code: 'wrong_state', message: 'this lane already holds a live run',
            detail: { lane: LANE, state: 'running' },
          },
        },
      },
    })

    await type('the words I do not want to type twice')
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()
    await settle()

    // ONE attempt, and no second run spawned
    expect(posts(RUNS)).toHaveLength(1)
    expect(posts(MESSAGES)).toHaveLength(0)

    // the message names the run that holds the lane
    const refused = screen.getByTestId('composer-refused')
    expect(refused).toHaveTextContent('run-4')
    expect(refused).toHaveTextContent('already')

    // and the words are back in the box, because a message the server refused did not happen
    expect(box().value).toBe('the words I do not want to type twice')

    // E9 was read exactly twice: once at mount, once because the refusal proved it was stale
    expect(reads).toBe(2)
  })

  it('test_a_queued_lane_is_not_started_and_says_what_it_is_waiting_for', async () => {
    // §1's third E18 clause. `lane::Store::startGate` answers `409 resource_busy` with the holder
    // and the queue position, and it is the one refusal that is not a mistake: the lane is doing
    // exactly what it should. So the message says what it is waiting for, and nothing is retried.
    await mountSession([claimed({
      state: {
        process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
        evidence: 'nothing_claimed', blocked: null, alarm: false,
      },
      waiting_for: { resource: 'climate-chamber', held_by: 'lane/02', position: 1 },
    })], {
      [RUNS]: {
        status: 409,
        body: {
          error: {
            code: 'resource_busy',
            message: 'an exclusive resource this lane needs is held by another lane',
            detail: { lane: LANE, resource: 'climate-chamber', held_by: 'lane/02', position: 1 },
          },
        },
      },
    })

    await type('start')
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()

    expect(posts(RUNS)).toHaveLength(1)
    const refused = screen.getByTestId('composer-refused')
    expect(refused).toHaveTextContent('waiting for climate-chamber')
    expect(refused).toHaveTextContent('lane/02')
    expect(box().value).toBe('start')
    // a queued lane is not a stale row, so nothing is re-read for it
    expect(callsTo('/api/lanes').filter((c) => c.method === 'GET')).toHaveLength(1)
  })

  it('test_a_queued_claim_does_not_chain_into_a_run', async () => {
    // The other half of §1's one-click rule: "no intermediate claimed-but-empty state UNLESS the
    // lane is queued on a resource". A chain that started a run anyway would put an error in front
    // of a person for a lane that is behaving correctly — and would do it on every claim of a
    // workspace that needs the board.
    const rows: Array<Record<string, unknown>> = []
    const queued = claimed({
      state: {
        process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
        evidence: 'nothing_claimed', blocked: null, alarm: false,
      },
      waiting_for: { resource: 'climate-chamber', held_by: 'lane/02', position: 1 },
    })
    await mountApp('#/tickets/TKT-142/session', {
      ...ticketRoutes(ticketDoc({ workspace: 'core', lane: null }), []),
      '/api/lanes': (_url, init) => {
        if ((init?.method ?? 'GET') !== 'POST') return { body: laneSet(rows) }
        rows.push(queued)
        return { status: 201, body: queued }
      },
      [RUNS]: STARTED,
    })
    await settle()
    await settle()

    // the composer's seat: on a ticket with no lane at all, §4.4's block and §4.7's are both on
    // the screen and both carry the control (WEB-FEAT-044)
    await act(async () => { fireEvent.click(composerClaim()) })
    await settle()

    expect(callsTo('/api/lanes').filter((c) => c.method === 'POST')).toHaveLength(1)
    expect(posts(RUNS)).toHaveLength(0)
    expect(screen.getAllByTestId('lane-card')).toHaveLength(1)
    expect(screen.queryByTestId('claim-error')).toBeNull()
  })
})
