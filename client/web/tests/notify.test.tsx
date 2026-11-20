import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { cleanup, act } from '@testing-library/react'

import { lane, mountApp, settle, ticketDoc, ticketRoutes } from './helpers'
import { refuseOverBudget, suiteTimeoutMs } from './suite-budget'

/**
 * SPEC.md §4.13 · F24 — reach a person whose tab is in the background.
 *
 * Three events reach out, and exactly three: a permission request, a lane going idle or blocked,
 * and a crash. A stage completing does not, a run finishing normally does not, and silence does
 * not. The last case in this file is the one that decides whether the feature is worth having: a
 * channel that fires on frequent events is a channel that gets muted, and once muted the three
 * that matter are lost with it.
 */

/** index.html's own icon, so the dot is tested against the link the product really ships. */
const PLAIN_ICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16"
  + " 16'%3E%3Crect width='16' height='16' rx='3' fill='%23161826'/%3E%3Crect x='4' y='4'"
  + " width='8' height='8' rx='2' fill='none' stroke='%239184d9' stroke-width='1.5'/%3E%3C/svg%3E"

class FakeNotification {
  static permission: string = 'granted'
  static requestPermission = vi.fn(async () => FakeNotification.permission)
  static raised: Array<{ title: string; body?: string }> = []
  constructor(title: string, options?: { body?: string }) {
    FakeNotification.raised.push({ title, body: options?.body })
  }
}

/** The tab is in the background — which is the entire subject of §4.13. */
function hide() {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
  document.dispatchEvent(new Event('visibilitychange'))
}

/** The person came back and looked. */
function look() {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  document.dispatchEvent(new Event('visibilitychange'))
  window.dispatchEvent(new Event('focus'))
}

const icon = () => document.querySelector('link[rel="icon"]') as HTMLLinkElement | null
const dotted = () => (icon()?.getAttribute('href') ?? '').includes('circle')

const ROWS = [
  lane({ lane: 'lane/01', ticket_id: 'TKT-142' }),
  lane({ lane: 'lane/02', ticket_id: 'TKT-147' }),
]

const at = (s: number) => `2026-07-28T10:00:${String(s).padStart(2, '0')}Z`

const permissionRequest = () => ({
  m: 'permission.request', ts: at(1), seq: null,
  lane: 'lane/01', request_id: 'req-1', tool: 'Bash',
  input_summary: 'rm -rf build/', rule_that_would_be_written: 'Bash(rm -rf build/)',
  expires_at: null,
})

const laneState = (over: Record<string, unknown> = {}) => ({
  m: 'lane.state', ts: at(2), seq: null, lane: 'lane/01', ticket_id: 'TKT-142',
  state: {
    process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  waiting_for: null, needs_you: false,
  ...over,
})

const process = (value: string, over: Record<string, unknown> = {}) => laneState({
  state: {
    process: value, motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  needs_you: value === 'idle' || value === 'blocked',
  ...over,
})

const run = (outcome: string, over: Record<string, unknown> = {}) => ({
  m: 'run', ts: at(3), seq: null, lane: 'lane/01', run_id: 'run-1', n: 1,
  outcome, started_iso: at(0), ended_iso: outcome === 'running' ? null : at(9),
  stage: 4, duration_s: 12, cost_usd: 0.1,
  ...over,
})

const stage = (index: number) => ({
  m: 'stage', ts: at(4), seq: null, lane: 'lane/01', ticket_id: 'TKT-142',
  stage: { index, total: 8, round: 1, of: 1, source: 'manifest' },
})

/**
 * The budget this case runs under — the SUITE's, read off `vite.config.ts`, so the assertion below
 * has a number it can be about and it is a number this case did not choose.
 *
 * `WEB-FIX-396` wrote `5000` here and asserted the config declared no `testTimeout`, to hold one
 * property: this case may not buy itself room the rest of the suite does not have. `WEB-FIX-405`
 * gives the suite a stated budget — 5 s is what `WEB-FIX-327`, `WEB-FIX-372` and `WEB-FIX-389`
 * were each filed about — and the property is kept by DERIVING the number instead of typing it.
 */
const BUDGET_MS = suiteTimeoutMs()

/**
 * The frames one busy run is made of: the run starting, 300 transcript entries, six bursts of
 * stage + metrics + lane state, and the two frames a run ends with.
 */
const BURST_FRAMES = 1 + 300 + 6 * 3 + 1 + 1

interface Burst { elapsed_ms: number; delivered: number }

/**
 * §4.13's busy run, driven through the mounted app and its socket, measured.
 *
 * `notify_at` puts a frame that DOES reach out in the middle of it, so the negative is this same
 * run with one frame added and nothing else changed.
 */
async function busyRun(opts: { notify_at?: number } = {}): Promise<Burst> {
  const started = performance.now()
  const app = await mounted()
  hide()
  // THIS run's socket, held rather than looked up again. `app.socket()` is whichever fake was made
  // last, so a case vitest has already timed out goes on delivering into the NEXT case's socket
  // and inflates its count — which is how the first RED capture read 459 frames instead of 322.
  const socket = app.socket()
  const before = socket.delivered
  // The heartbeat answers itself through the same path, and on a loaded machine it fires during
  // the burst — so what the BURST delivered is what arrived less what the socket replied to, read
  // off the socket's own record of what it sent rather than assumed to be nothing.
  const pinged = () => socket.frames().filter((f) => f.m === 'ping').length
  const pings = pinged()

  // ONE act around the whole burst, not 321 of them. Every decision §4.13 makes about a frame is
  // taken synchronously inside `onmessage` — `reachOut`, the `seen` map, `new Notification` — so
  // what a React flush between frames buys is 321 flushes, not a different answer. The claim is
  // unchanged and it is the same claim about the same feature; what goes is a per-frame cost that
  // measured the machine rather than the client.
  await act(async () => {
    socket.deliver(run('running'))

    for (let n = 0; n < 300; n += 1) {
      socket.deliver({
        m: 'transcript', ts: at(5), seq: n, lane: 'lane/01', run_id: 'run-1',
        entry: { kind: 'assistant', text: `line ${n}` },
      })
      if (n === opts.notify_at) socket.deliver(permissionRequest())
      if (n % 50 === 0) {
        socket.deliver(stage(Math.floor(n / 50)))
        socket.deliver({
          m: 'lane.metrics', ts: at(6), seq: null, lane: 'lane/01',
          elapsed_s: n * 2, cost_usd: n / 100, turn: { cost_usd: 0.01 },
        })
        socket.deliver(laneState({ ts: at(6) }))
      }
    }

    // it ends the way runs usually end
    socket.deliver(run('ended cleanly'))
    socket.deliver(process('finished'))
  })
  await settle()

  return {
    elapsed_ms: performance.now() - started,
    delivered: socket.delivered - before - (pinged() - pings),
  }
}

beforeEach(() => {
  document.title = 'Factory'
  const link = document.createElement('link')
  link.rel = 'icon'
  link.href = PLAIN_ICON
  document.head.appendChild(link)
  FakeNotification.permission = 'granted'
  FakeNotification.raised = []
  FakeNotification.requestPermission = vi.fn(async () => FakeNotification.permission)
  vi.stubGlobal('Notification', FakeNotification)
})

afterEach(() => {
  cleanup()
  icon()?.remove()
  look()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function mounted() {
  const app = await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), ROWS))
  await settle()
  await settle()
  return app
}

describe('4.13 the background tab', () => {
  it('three_events_and_no_more', async () => {
    const app = await mounted()
    hide()

    // ── the three that reach out ────────────────────────────────────────────────────────────
    await act(async () => { app.socket().deliver(permissionRequest()) })
    await settle()
    expect(document.title).toBe('(1) Factory')

    await act(async () => { app.socket().deliver(process('idle')) })
    await settle()
    expect(document.title).toBe('(2) Factory')

    await act(async () => { app.socket().deliver(run('crashed')) })
    await settle()
    expect(document.title).toBe('(3) Factory')

    expect(FakeNotification.raised).toHaveLength(3)
    expect(dotted()).toBe(true)

    // ── and the three that must not ─────────────────────────────────────────────────────────
    // a stage completing — the frequent event, and the one that would mute the channel
    await act(async () => { app.socket().deliver(stage(5)) })
    // a run finishing normally
    await act(async () => { app.socket().deliver(run('ended cleanly')) })
    await act(async () => { app.socket().deliver(run('completed', { run_id: 'run-2' })) })
    // silence crossing its threshold — already visible on the rail, and it resolves itself
    await act(async () => {
      app.socket().deliver(laneState({
        ts: at(8),
        state: {
          process: 'running', motion: { value: 'silent', silent_for_s: 130, since: at(0) },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        needs_you: true,
      }))
    })
    await settle()

    expect(document.title).toBe('(3) Factory')
    expect(FakeNotification.raised).toHaveLength(3)
  })

  it('the_title_prefix_always_happens', async () => {
    // The browser refused. The title is the channel that always works.
    FakeNotification.permission = 'denied'
    const app = await mounted()
    hide()

    await act(async () => { app.socket().deliver(permissionRequest()) })
    await settle()
    expect(document.title).toBe('(1) Factory')
    expect(dotted()).toBe(true)
    expect(FakeNotification.raised).toHaveLength(0)

    // and a browser with no Notification API at all is still reached
    vi.stubGlobal('Notification', undefined)
    await act(async () => { app.socket().deliver(run('crashed')) })
    await settle()
    expect(document.title).toBe('(2) Factory')
    expect(dotted()).toBe(true)
  })

  it('permission_is_asked_when_a_lane_starts', async () => {
    FakeNotification.permission = 'default'
    const app = await mounted()

    // not on page load. A page that asks before it has done anything is a page people refuse.
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()

    await act(async () => { app.socket().deliver(run('running')) })
    await settle()
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1)

    // and once. A second lane starting is not a second prompt.
    await act(async () => { app.socket().deliver(run('running', { lane: 'lane/02', run_id: 'r2' })) })
    await settle()
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1)
  })

  it('the_count_clears_on_focus', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const app = await mounted()
    hide()

    await act(async () => { app.socket().deliver(permissionRequest()) })
    await act(async () => { app.socket().deliver(run('crashed')) })
    await settle()
    expect(document.title).toBe('(2) Factory')

    // not on a timer: ten minutes in the background change nothing
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()
    expect(document.title).toBe('(2) Factory')
    expect(dotted()).toBe(true)

    // the moment the tab is focused, and only then
    await act(async () => { look() })
    await settle()
    expect(document.title).toBe('Factory')
    expect(dotted()).toBe(false)
  })

  it('socket_down_says_offline', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const app = await mounted()
    expect(document.title).toBe('Factory')

    await act(async () => { app.socket().onclose?.() })
    await settle()
    // the absence of notifications otherwise reads as `nothing is happening`, which is exactly wrong
    expect(document.title).toBe('(offline) Factory')
  })

  it('test_a_busy_run_is_silent_finishes_well_inside_its_budget', async () => {
    // MANDATORY NEGATIVE. A run producing hundreds of transcript entries, crossing several stages
    // and finishing normally raises ZERO notifications. This is the case that decides whether the
    // feature is worth having.
    const burst = await busyRun()

    expect(burst.delivered).toBe(BURST_FRAMES)
    expect(FakeNotification.raised).toEqual([])
    expect(document.title).toBe('Factory')
    expect(dotted()).toBe(false)

    refuseOverBudget(burst.elapsed_ms, BUDGET_MS)
  }, BUDGET_MS)

  it('test_the_negative_still_refuses_a_run_that_notifies', async () => {
    // Whatever makes the case above faster may not make it weaker. One frame of the burst is
    // swapped for one that DOES reach out, and every expression the silent case is made of has to
    // go red on it.
    const burst = await busyRun({ notify_at: 150 })

    expect(burst.delivered).toBe(BURST_FRAMES + 1)
    expect(FakeNotification.raised).toHaveLength(1)

    expect(() => expect(FakeNotification.raised).toEqual([])).toThrow()
    expect(() => expect(document.title).toBe('Factory')).toThrow()
    expect(() => expect(dotted()).toBe(false)).toThrow()
  }, BUDGET_MS)

  it('test_the_case_fails_when_the_budget_is_the_only_thing_that_changed', async () => {
    // THE BUDGET IS THE THING THIS CASE GUARDS, and it is guarded absolutely.
    //
    // Watched red first as the boundary triple below and nothing else — three expressions around
    // one real measurement — and that version came back 8 of 8 GREEN with `BUDGET_MS` raised to
    // 600000 and the 321-act burst restored, at 10058 ms a case. Every expression in it was a
    // RATIO, so none of them could see the budget move. The log is
    // `docs/reports/WEB-FIX-396/WEB-FIX-396-negative-budget-raised-red-2026-09-11.log`.
    //
    // So the number is pinned, and pinned to something outside this file as well: 5000 ms is
    // vitest's default and therefore what every other case in this suite runs under. This case
    // may not buy itself room the rest of the suite does not have, in either direction — room
    // taken for this case alone fails the second expectation, room taken for the whole suite
    // fails the first.
    const config = readFileSync(join(__dirname, '..', 'vite.config.ts'), 'utf8')
    const declared = /\btestTimeout:\s*(\d+)/.exec(config)
    expect(declared, 'the suite declares no budget, so this case is about a number nobody stated')
      .not.toBeNull()
    expect(BUDGET_MS, 'this case runs under a budget the rest of the suite does not have')
      .toBe(Number(declared![1]))
    // …and no case in this file may take a budget of its own. The sweep is every third argument
    // an `it(…)` in this file passes, read off the file rather than remembered: a numeric literal
    // there is room this case bought and the rest of the suite does not have.
    const own = readFileSync(join(__dirname, 'notify.test.tsx'), 'utf8')
    const perCase = [...own.matchAll(/^\s*\}, ([A-Za-z0-9_]+)\)$/gm)].map((m) => m[1])
    expect(perCase.length, 'no per-case budget in this file at all — the sweep is empty')
      .toBeGreaterThan(0)
    expect(perCase.filter((arg) => arg !== 'BUDGET_MS'),
      'a case in this file took room the suite does not give every other case').toEqual([])

    // and the guard has teeth on a real measurement: one burst, three budgets, the fixture fixed.
    const burst = await busyRun()
    expect(burst.delivered).toBe(BURST_FRAMES)

    expect(() => refuseOverBudget(burst.elapsed_ms, BUDGET_MS)).not.toThrow()
    expect(() => refuseOverBudget(burst.elapsed_ms, 2 * burst.elapsed_ms + 1)).not.toThrow()
    expect(() => refuseOverBudget(burst.elapsed_ms, 2 * burst.elapsed_ms - 1)).toThrow()
  }, BUDGET_MS)
})
