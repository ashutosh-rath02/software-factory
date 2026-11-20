import { act, render } from '@testing-library/react'
import { Profiler, type ProfilerOnRenderCallback, type ReactElement } from 'react'
import { vi } from 'vitest'

import { App } from '../src/App'
import { clearRunHistory } from '../src/services/runs'

/**
 * A socket we drive by hand, so a test proves the client's behaviour and not a library's.
 * Same shape as the one in socket.test.ts; kept here because these tests mount the whole app.
 */
export class FakeSocket {
  static all: FakeSocket[] = []
  static get last() { return FakeSocket.all[FakeSocket.all.length - 1] }
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  /**
   * Whether the server on the other end is still answering.
   *
   * A live server answers every `ping` with a bare `pong` at once, and SPEC.md §4.1 makes the chip
   * a fact about that: green while a frame arrived within 15 s. A fake that stayed mute would be a
   * fake of a DEAD connection — so a ten-minute case against one would be asserting that a client
   * ignores a server that has stopped talking, which is the defect, not the rule. Setting this
   * false is how a case cuts the network: the socket does not close, it simply goes quiet.
   */
  answers = true
  close = vi.fn()
  send = (data: string) => {
    this.sent.push(data)
    if (this.answers && JSON.parse(data).m === 'ping') {
      this.deliver({ m: 'pong', ts: '2026-07-28T10:00:00Z', seq: null })
    }
  }
  /**
   * Frames a listening client actually took off this socket.
   *
   * A case whose verdict is *nothing happened* is satisfied by nothing arriving, so the burst it
   * swept has to be counted somewhere the client's own handler decides (WEB-FIX-396, and
   * `docs/operations/ESCAPES.md` 2026-08-22 — the row that asked for exactly this and never landed it).
   */
  delivered = 0
  constructor() { FakeSocket.all.push(this) }

  frames(): Array<Record<string, unknown>> { return this.sent.map((s) => JSON.parse(s)) }
  deliver(frame: unknown) {
    if (!this.onmessage) return
    this.delivered += 1
    this.onmessage({ data: JSON.stringify(frame) })
  }
}

export interface Reply { status?: number; body: unknown }
/**
 * A handler may answer late, or never. A stub that always answers within the same tick is a
 * stub no assertion can be timed against: it hides every wait that is really a race (WEB-FIX-031).
 *
 * `init` arrived with WEB-FEAT-044: `/api/lanes` is E9 *and* E12, one path and two verbs, so a
 * route that cannot see the method cannot answer them differently — and the body of a write is
 * the thing its assertion is about.
 */
export type Handler = Reply | ((url: URL, init?: RequestInit) => Reply | Promise<Reply>)
export type Routes = Record<string, Handler>

export interface Call { url: string; method: string; body?: string }

export const HEALTH = {
  factory: '0.1', manifest: 'ok',
  repo: { name: 'fixture', root: '/tmp/fixture' }, uptime_s: 3,
}

export const MANIFEST = {
  factory: '0.1',
  repo: { name: 'fixture', root: '/tmp/fixture' },
  path: '/tmp/fixture/.factory/factory.json',
  workspaces: { core: { path: '.' }, edge: { path: 'edge' } },
  lanes: { host: 3 },
  docs: { epics: 'docs/requirements', tickets: 'docs/tickets', reports: 'docs/reports' },
  stages: ['SPEC', 'IMPL', 'GATE', 'DELIVER'],
  display: { ticket_fields: ['contract'] },
  agent: { command: 'claude' },
  resolved: { paths: {} },
  warnings: [],
}

/** SPEC.md §3.5's object, in the shape every row carries it. Nothing has happened by default. */
export function delivery(over: Record<string, unknown> = {}) {
  return {
    state: 'not_started', detail: '', claimed: 'todo', disagrees: false, needs_you: false,
    ...over,
  }
}

export function requirement(over: Record<string, unknown> = {}) {
  return {
    id: 'REQ-014', title: 'Never ship an artifact the working tree cannot reproduce',
    status: 'in progress', updated_iso: '2026-07-27T09:00:00Z',
    path: '/tmp/fixture/docs/requirements/REQ-014.md',
    workspaces: ['core', 'edge'],
    state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
    delivery: delivery(),
    needs_you: { count: 0, label: '' }, ticket_count: 3,
    ...over,
  }
}

/** One ticket row inside a workspace group (E4). */
export function ticket(over: Record<string, unknown> = {}) {
  return {
    id: 'TKT-142', title: 'Guard against flashing a stale build artifact', status: 'todo',
    state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
    delivery: delivery(),
    stage: { index: 0, total: 4, round: 1, of: 1, source: 'manifest' },
    needs_you: false, lane: null, cost_usd: 0,
    ...over,
  }
}

/**
 * One lane, in E9's own shape (SPEC.md §4.4).
 *
 * `state.motion` is an OBJECT here and a string on a row — two nouns, two shapes. The lane card's
 * clock needs `since`, and a fixture that flattened it would be testing a screen nobody ships.
 */
export function lane(over: Record<string, unknown> = {}) {
  return {
    lane: 'lane/01', ticket_id: 'TKT-142', workspace: 'core', role: 'run',
    branch: 'ticket/TKT-142', worktree: '/tmp/fixture/.factory/worktrees/lane/01',
    state: {
      process: 'running',
      motion: { value: 'producing', silent_for_s: 0, since: null },
      evidence: 'nothing_claimed', blocked: null, alarm: false,
    },
    needs_you: false, detail: null,
    stage: { index: 4, total: 8, round: 1 },
    progress_pct: 50, remaining_s: null, elapsed_s: 724, cost_usd: 0.74,
    // FAC-EPIC-373. REQUIRED and nullable in `docs/technical/contracts/http.schema.json:575`, so `null` is the
    // default rather than the member being absent: a fixture missing a member the schema
    // REQUIRES is a fixture of a server that does not exist (WEB-FIX-200, and the 2026-09-05
    // ledger row this ticket answers).
    last_words: null,
    run_id: null, session_id: null, resources_held: [], waiting_for: null,
    ...over,
  }
}

/**
 * One exclusive resource, in E9's own shape.
 *
 * The `description` is the only place a person is told what `climate-chamber` IS, and a queued
 * card that names the key without it is a card that explains nothing (WEB-FEAT-044).
 */
export function resource(over: Record<string, unknown> = {}) {
  return {
    name: 'climate-chamber', exclusive: true,
    description: 'one chamber, one run at a time', held_by: null, queue: [],
    // E9 always answers this — `render.cpp` renders it beside every resource, and `null` is what
    // it renders when nobody holds the thing. A fixture missing a member the schema REQUIRES is a
    // fixture of a server that does not exist (WEB-FIX-200).
    holder_alive: null,
    ...over,
  }
}

/** E9's body around whatever rows a case wants. */
export function laneSet(rows: Array<Record<string, unknown>> = [],
                        resources: Array<Record<string, unknown>> = []) {
  return { lanes: rows, resources, seats: { host: { total: 3, used: rows.length } } }
}

/** E5, the ticket document, in full — the identity row reads `runs` and the strip reads `stage`. */
export function ticketDoc(over: Record<string, unknown> = {}) {
  return {
    id: 'TKT-142', title: 'Guard against flashing a stale build artifact',
    workspace: 'core', status: 'in-progress', parent: 'REQ-014',
    path: '/tmp/fixture/docs/tickets/TKT-142.md', updated_iso: '2026-07-27T09:00:00Z',
    stages: ['SPEC', 'TEST', 'IMPL', 'BUILD', 'GATE', 'REVIEW', 'QA', 'DELIVER'],
    stage: { index: 4, total: 8, round: 1, of: 1, source: 'manifest' },
    extras: [{ label: 'contract', value: 'factory-v0.1.0' }],
    body_markdown: '# Guard against flashing a stale build artifact\n\nThe body.\n',
    lane: null, runs: { count: 4, duration_s: 1120, cost_usd: 1.86 },
    report: { exists: false, path: '/tmp/fixture/docs/reports/TKT-142-report.md',
              evidence: 'nothing_claimed' },
    needs_you: false,
    state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
    delivery: delivery(),
    ...over,
  }
}

/** The three routes every ticket surface needs: the document, its absent report, and the lanes. */
export function ticketRoutes(doc: Record<string, unknown> = ticketDoc(),
                            rows: Array<Record<string, unknown>> = []): Routes {
  const id = String(doc.id)
  return {
    [`/api/tickets/${id}`]: { body: doc },
    [`/api/tickets/${id}/report`]: { status: 404, body: { error: { code: 'not_found' } } },
    '/api/lanes': { body: laneSet(rows) },
  }
}

/** Every request the app made, in order — the evidence for "one request, with the filter in it". */
export let calls: Call[] = []

export function stubFetch(routes: Routes) {
  calls = []
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = String(input)
    const url = new URL(raw, 'http://factory.test')
    calls.push({ url: raw, method: init?.method ?? 'GET',
                 body: typeof init?.body === 'string' ? init.body : undefined })
    const handler = routes[url.pathname]
    const reply: Reply = handler === undefined
      ? { status: 404, body: { error: { code: 'not_found', message: 'no route', detail: {} } } }
      : (typeof handler === 'function' ? await handler(url, init) : handler)
    const status = reply.status ?? 200
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => reply.body,
    } as unknown as Response
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

export function callsTo(path: string) {
  return calls.filter((c) => new URL(c.url, 'http://factory.test').pathname === path)
}

export interface Mounted {
  socket(): FakeSocket
  commits(): number
  rerenderCount: { current: number }
}

/**
 * Mount the whole app at a route, against stubbed HTTP and a socket we own.
 *
 * The whole app on purpose: every §1 criterion is about a surface in its used state, and a page
 * rendered outside the shell is a page whose loading, empty and socket-down rules were never run.
 */
export async function mountApp(route: string, routes: Routes): Promise<Mounted> {
  FakeSocket.all = []
  // The run history is one reading held for the whole screen (`services/runs.tsx`), so it is
  // module state — and module state survives `cleanup()`. One case's rows are not the next
  // case's, and a mount is exactly where that line is drawn.
  clearRunHistory()
  stubFetch({ '/api/health': { body: HEALTH }, '/api/manifest': { body: MANIFEST }, ...routes })
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  window.location.hash = route

  let commits = 0
  const onRender: ProfilerOnRenderCallback = () => { commits += 1 }
  const tree: ReactElement = (
    <Profiler id="app" onRender={onRender}><App /></Profiler>
  )

  await act(async () => { render(tree) })
  await act(async () => { FakeSocket.last?.onopen?.() })
  await act(async () => { await Promise.resolve() })

  return {
    socket: () => FakeSocket.last,
    commits: () => commits,
    rerenderCount: { current: 0 },
  }
}

/** Let every pending promise settle inside act(), so the DOM is the settled one. */
export async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

/**
 * `E30`'s body — SPEC.md §4.18's three panels, at one instant (WEB-FEAT-001).
 *
 * The default is the repository a person can read that has nothing in it: every count zero, both
 * lists empty. That is the endpoint's own `Missing` case and it is a 200, so a fixture whose
 * default was populated would never exercise the state the surface most has to get right.
 *
 * `delivery` carries one entry per §3.5 value IN §3.5's TABLE ORDER, including the zeros — the
 * server renders it from `catalog::deliveryStates()` and a fixture that dropped its empty members
 * would be a fixture of a server that does not exist.
 */
export function home(over: Record<string, unknown> = {}) {
  return {
    delivery: [
      { state: 'not_started', count: 0 },
      { state: 'running', count: 0 },
      { state: 'stalled', count: 0 },
      { state: 'unshipped', count: 0 },
      { state: 'waiting', count: 0 },
      { state: 'done', count: 0 },
      { state: 'unknown', count: 0 },
    ],
    needs_you: { total: 0, rows: [] },
    unpushed: { total: 0, rows: [] },
    lanes: { occupied: 0, total: 3 },
    warnings: [],
    ...over,
  }
}

/** One row of §4.18's `needs you` panel: the clauses by name, the server's sentence, a remedy. */
export function needsYouRow(over: Record<string, unknown> = {}) {
  return {
    ticket_id: 'TKT-142', title: 'Guard against flashing a stale build artifact',
    clauses: ['idle'], detail: 'the agent asked and stopped',
    lane: 'lane/01',
    remedy: { action: 'open_lane', target: { lane: 'lane/01' },
              label: 'Open lane/01', destroys: [] },
    ...over,
  }
}

/** One row of §4.18's `unpushed` panel. `age_s` is the OLDEST commit's age, never the newest. */
export function unpushedRow(over: Record<string, unknown> = {}) {
  return {
    ticket_id: 'TKT-142', title: 'Guard against flashing a stale build artifact',
    branch: 'ticket/TKT-142', commits: 3, age_s: 2 * 86400,
    ...over,
  }
}
