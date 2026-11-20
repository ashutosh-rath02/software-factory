import { act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { lane, mountApp, settle, ticketDoc, ticketRoutes, type Mounted } from './helpers'

/**
 * The fixtures the two SESSION cases share: a lane with a live run, the frames that carry a
 * transcript into it, and §4.6's own table read off `SPEC.md`.
 *
 * The kind list is parsed from the specification rather than imported from the client, on purpose.
 * A test that asks the component which kinds exist can only prove the component agrees with
 * itself — the assertion `every_kind_renders` is worth anything only if the list comes from the
 * document the table lives in.
 */

const HERE = dirname(fileURLToPath(import.meta.url))
export const SPEC = readFileSync(join(HERE, '../../../docs/product/SPEC.md'), 'utf8')

/** Every `kind` named in the first column of §4.6's table, in the order the table lists them. */
export function kindsInSpec(): string[] {
  const start = SPEC.indexOf('### 4.6 Ticket — SESSION tab')
  const end = SPEC.indexOf('### 4.7 Ticket — composer')
  const out: string[] = []
  for (const line of SPEC.slice(start, end).split('\n')) {
    const row = /^\|\s*`([a-z_]+)`/.exec(line)
    if (row && !out.includes(row[1])) out.push(row[1])
  }
  return out
}

export const LANE = 'lane/01'
export const RUN = 'run-4'

/** A lane running THIS ticket, mid-turn — the state the composer's `Stop` reads (§4.7). */
export function running(over: Record<string, unknown> = {}) {
  return lane({
    lane: LANE, ticket_id: 'TKT-142', run_id: RUN, session_id: 'sess-1', cost_usd: 0.74,
    state: {
      process: 'running',
      motion: { value: 'producing', silent_for_s: 0, since: null },
      evidence: 'nothing_claimed', blocked: null, alarm: false,
    },
    ...over,
  })
}

/**
 * Mid-turn and SILENT — the state a message is sent in and comes back `held` (E14).
 *
 * The design's own demonstration is explicit that `Stop` belongs to the streaming state and
 * `Send` stays in place while the agent is quiet: a composer that showed `Stop` for the whole
 * turn would make the held-and-interrupt mechanism unreachable.
 */
export function silent(over: Record<string, unknown> = {}) {
  return running({
    state: {
      process: 'running',
      motion: { value: 'silent', silent_for_s: 252, since: '2026-07-28T09:55:48Z' },
      evidence: 'nothing_claimed', blocked: null, alarm: true,
    },
    ...over,
  })
}

/** The same lane between turns: nothing in flight, so the button reads `Send`. */
export function idle(over: Record<string, unknown> = {}) {
  return running({
    state: {
      process: 'idle',
      motion: { value: null, silent_for_s: 0, since: null },
      evidence: 'nothing_claimed', blocked: null, alarm: false,
    },
    ...over,
  })
}

/**
 * The lane a person is looking at when they want to run the thing AGAIN — WEB-FIX-061.
 *
 * `crashed` is not in `vocabulary.ALIVE`, so `hasLiveSession` is false and a Send from here is
 * E18, not E14. It is the state the whole ticket is about: the lane keeps its seat, its worktree
 * and its branch, the server is perfectly happy to start a run in it, and until this ticket the
 * button in front of the person could not reach that.
 */
export function crashed(over: Record<string, unknown> = {}) {
  return running({
    state: {
      process: 'crashed',
      motion: { value: null, silent_for_s: 0, since: null },
      evidence: 'nothing_claimed', blocked: null, alarm: false,
    },
    ...over,
  })
}

export interface Entry { seq: number; ts: string; kind: string; [k: string]: unknown }

/**
 * WEB-FIX-275 §3 — NOT EXPORTED, and that is the structural change.
 *
 * The class this ticket escaped from is *a fixture that constructs the end state instead of
 * replaying the sequence that produces it*. A test that can write an `Entry` down can write one the
 * wire never sends — a `tool` complete in a single object — and the two-frame merge path is then
 * never exercised. It was not, for weeks, while eleven blank rows sat on the first live lane
 * anybody watched. So there are exactly two doors into this component, both of them frames:
 * `live()` for a transcript frame and `snapshot()` for a snapshot, and neither takes an `Entry`.
 */
function entry(seq: number, kind: string, payload: Record<string, unknown> = {}): Entry {
  return { seq, ts: '2026-07-28T10:00:00Z', kind, ...payload }
}

/** One entry of a snapshot, as the three things the server writes: `[seq, kind, payload]`. */
export type Row = [number, string, Record<string, unknown>?]

/**
 * `M1 snapshot` for a lane scope — E8's body, which is what backfills the column.
 *
 * Takes ROWS, not entries: a snapshot legitimately carries finished entries — that is what a
 * snapshot IS — but the test may not hand one to any other door.
 */
export function snapshot(rows: Row[], over: Record<string, unknown> = {}) {
  const entries = rows.map(([seq, kind, payload]) => entry(seq, kind, payload))
  return {
    m: 'snapshot', ts: '2026-07-28T10:00:00Z', seq: null, scope: { lane: LANE },
    data: {
      run_id: RUN, entries,
      next_after_seq: entries.length ? entries[entries.length - 1].seq : 0,
      complete: true,
    },
    ...over,
  }
}

/**
 * `M5 transcript` — one entry as it is parsed out of the run's output.
 *
 * `runId` is a parameter because a lane outlives its runs and the sequence does not: a run
 * numbers its own entries from 1, so the second run in a lane is the case where a frame's `seq`
 * says less than the one before it and means more.
 */
/**
 * Fields that IDENTIFY an entry rather than complete it. The wire never re-sends these in a patch
 * — a finished tool call arrives as `{seq, kind, ok}` and nothing more — so a fixture that puts
 * them in a patch is not replaying the sequence, it is constructing the finished entry and then
 * asserting against its own construction.
 *
 * That is exactly how `WEB-FIX-275` hid: the case above delivered a patch re-sending `tool` and
 * `summary`, so `entries[at] = arrived` looked correct while it was destroying both on every real
 * run. This refuses at the fixture, because a convention the helper can refuse beats one a script
 * can grep.
 */
const IDENTIFYING = ['tool', 'summary', 'input', 'since']

/**
 * The kinds the server patches — `app/agent/transcript.cpp:241,284`, `edit ? "file_edit" : "tool"`.
 * The schema's prose names two of the three; that is `SRV-FIX-289`, not this list.
 */
const TOOL_KINDS = ['tool', 'file_edit']

export function live(seq: number, kind: string, payload: Record<string, unknown> = {},
                    patch?: true, runId: string = RUN) {
  if (patch) {
    const resent = IDENTIFYING.filter((k) => k in payload)
    if (resent.length) {
      throw new Error(
        `live(${seq}, '${kind}', …, patch) re-sends ${resent.join(', ')}. A patch frame carries the `
        + `field it ADDS and nothing else (socket.schema.json: "one entry becoming complete"). `
        + `Re-sending identity makes a replacing client look correct — WEB-FIX-275.`)
    }
  }
  // The OTHER half of the same rule, and the half round 1 left out. A call frame for a tool is
  // emitted with `"ok":null,"duration_s":0` (`transcript.cpp:248-251`) — always, without exception,
  // because the outcome is not known yet. A fixture that delivers a settled `ok` in the call frame
  // describes a wire that does not exist, and it is precisely the fixture under which a REPLACING
  // client looks correct: there is no second frame to lose anything to.
  if (!patch && TOOL_KINDS.includes(kind) && payload.ok !== null && payload.ok !== undefined) {
    throw new Error(
      `live(${seq}, '${kind}', …) carries ok=${JSON.stringify(payload.ok)} on a CALL frame. The `
      + `server emits "ok":null on the call and settles it in a patch (transcript.cpp:248-251). `
      + `A one-frame tool call is a wire that does not exist — WEB-FIX-275.`)
  }

  const { seq: _s, ts, ...rest } = entry(seq, kind, payload)
  return {
    m: 'transcript', ts, seq, lane: LANE, run_id: runId,
    entry: rest, ...(patch ? { patch: true } : {}),
  }
}

/**
 * The document's own `lane:` for a set of rows.
 *
 * `TicketPage` reads the running lane first and falls back to what the document said, so a
 * fixture whose rows hold no lane for this ticket must not leave one written in the document —
 * that would be testing a screen nobody sees.
 */
export const docLane = (rows: Array<Record<string, unknown>>) =>
  (rows.some((r) => r.ticket_id === 'TKT-142') ? LANE : null)

/** Mount the SESSION tab of a ticket whose lane is running. */
export async function mountSession(rows: Array<Record<string, unknown>> = [running()]) {
  const app = await mountApp('#/tickets/TKT-142/session',
    ticketRoutes(ticketDoc({ lane: docLane(rows) }), rows))
  await settle()
  await settle()
  return app
}

export async function deliver(app: Mounted, ...frames: unknown[]) {
  await act(async () => { for (const frame of frames) app.socket().deliver(frame) })
  await settle()
}

/** jsdom reports every box as zero, so a scroll rule has to be given something to read. */
export function measure(el: HTMLElement,
                        box: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  Object.defineProperty(el, 'scrollHeight', { value: box.scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: box.clientHeight, configurable: true })
  el.scrollTop = box.scrollTop
}
