import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { connect } from '../src/services/socket'
// Plain ESM, so `npm run gen:types` needs no build step of its own.
import { generate } from '../scripts/gen-socket-types.mjs'

const here = (rel: string) => fileURLToPath(new URL(rel, import.meta.url))

/** A socket we drive by hand, so the test proves the client's behaviour and not a library's. */
class FakeSocket {
  static all: FakeSocket[] = []
  static get last() { return FakeSocket.all[FakeSocket.all.length - 1] }
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  send = (data: string) => { this.sent.push(data) }
  constructor() { FakeSocket.all.push(this) }

  /** what the client sent, as frames */
  frames() { return this.sent.map((s) => JSON.parse(s)) }
  deliver(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }) }
}

const iso = '2026-07-27T10:00:00Z'
const entry = (seq: number, text: string) => ({
  m: 'transcript', ts: iso, seq, lane: 'lane/01', run_id: 'run-1',
  entry: { kind: 'prose', text },
})

beforeEach(() => { FakeSocket.all = [] })

describe('the socket contract, client half', () => {
  it('generated_types_match_the_schema', () => {
    const schema = JSON.parse(readFileSync(here('../../../docs/technical/contracts/socket.schema.json'), 'utf8'))
    const checkedIn = readFileSync(here('../src/types/socket.ts'), 'utf8')

    // Byte-identical, not "equivalent". Drift is a red build, not a discussion.
    expect(generate(schema)).toBe(checkedIn)

    // and the file is really generated from THIS schema, not a stale copy that happens to parse
    expect(checkedIn).toContain("m: 'catalog.changed'")
    expect(checkedIn).toContain('replaces_tail?: boolean')
  })

  it('reconnect_resumes', () => {
    vi.useFakeTimers()
    const sock = connect('ws://test/ws', {
      factory: () => new FakeSocket() as unknown as WebSocket,
    })
    sock.subscribe({ lane: 'lane/01' })

    let s = FakeSocket.last
    s.onopen?.()
    expect(s.frames()).toEqual([{ m: 'subscribe', scope: { lane: 'lane/01' } }])

    s.deliver({ m: 'snapshot', ts: iso, seq: null, scope: { lane: 'lane/01' },
                data: { lane: 'lane/01', run_id: null, entries: [], next_after_seq: 0,
                        complete: true } })
    s.deliver(entry(1, 'one'))
    s.deliver(entry(2, 'two'))
    s.deliver(entry(3, 'three'))
    expect(sock.lane('lane/01').entries.map((e) => e.seq)).toEqual([1, 2, 3])
    expect(sock.lane('lane/01').lastSeq).toBe(3)

    // the socket drops; the client reconnects and asks for exactly what it is missing
    s.onclose?.()
    expect(sock.state()).toBe('lost')
    vi.advanceTimersByTime(1000)
    s = FakeSocket.last
    s.onopen?.()
    expect(s.frames()).toEqual([
      { m: 'resume', lane: 'lane/01', after_seq: 3 },
    ])

    // it receives only what it missed, and its tail is untouched
    s.deliver(entry(4, 'four'))
    s.deliver(entry(5, 'five'))
    expect(sock.lane('lane/01').entries.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5])
    // WEB-FIX-051 — ONE shape in this map. A `M5` frame is flattened on the way in, so an entry
    // that arrived live and an entry that arrived in a snapshot read identically here. It used to
    // be `e.entry.text` for one and `e.text` for the other, in an array typed as neither.
    expect(sock.lane('lane/01').entries.map((e) => e.text))
      .toEqual(['one', 'two', 'three', 'four', 'five'])

    // on the fallback path it discards its tail rather than merging into a hole
    s.onclose?.()
    vi.advanceTimersByTime(1000)
    s = FakeSocket.last
    s.onopen?.()
    expect(s.frames()).toEqual([{ m: 'resume', lane: 'lane/01', after_seq: 5 }])

    s.deliver({ m: 'snapshot', ts: iso, seq: 12, scope: { lane: 'lane/01' },
                replaces_tail: true,
                data: { lane: 'lane/01', run_id: 'run-1', next_after_seq: 12, complete: false,
                        entries: [{ seq: 11, ts: iso, kind: 'prose', text: 'eleven' },
                                  { seq: 12, ts: iso, kind: 'prose', text: 'twelve' }] } })
    expect(sock.lane('lane/01').entries.map((e) => e.seq)).toEqual([11, 12])
    expect(sock.lane('lane/01').lastSeq).toBe(12)

    sock.close()
    vi.useRealTimers()
  })
})

/**
 * `WEB-FIX-073` — `seq` is monotonic PER RUN, and this module compared it per LANE.
 *
 * `SPEC.md` §4.6: a run numbers its entries from 1. `apply()` dropped any frame whose `seq` was not
 * ahead of the lane's `lastSeq`, with no look at `run_id` — and then wrote `state.runId` on the
 * line after the drop, so the run was tracked and never consulted. Every early entry of a SECOND
 * run in the same lane was discarded as a replay, and `WEB-FIX-051` made this module the seed for
 * every mount, so what it holds is what a surface comes back to.
 *
 * Measured against the real server before choosing the fix (see the report): `SequenceLog::
 * beginRun` clears the lane's log and pushes a `snapshot … replaces_tail`, so a subscribed client
 * IS corrected and a stale `after_seq` on `resume` answers `not coverable` → snapshot. This is one
 * bug and not two: the wire self-corrects, and the module's own record does not.
 */
describe('WEB-FIX-073 · a new run is not a replay of the old one', () => {
  function entry(seq: number, runId: string, text: string) {
    return {
      m: 'transcript', ts: '2026-07-28T11:40:01Z', seq, lane: 'lane/01', run_id: runId,
      entry: { seq, ts: '2026-07-28T11:40:01Z', kind: 'prose', text },
    }
  }

  it('a_new_run_is_not_a_replay', () => {
    const socket = connect('ws://test/ws', {
      factory: () => new FakeSocket() as unknown as WebSocket,
    })
    socket.subscribe({ lane: 'lane/01' })
    FakeSocket.last.onopen?.()
    for (let seq = 1; seq <= 5; seq += 1) {
      FakeSocket.last.deliver(entry(seq, 'run-a', `a${seq}`))
    }
    expect(socket.lane('lane/01').entries).toHaveLength(5)
    expect(socket.lane('lane/01').lastSeq).toBe(5)

    // run B's FIRST entry — seq 1, behind the lane's 5, and the newest thing that has happened
    FakeSocket.last.deliver(entry(1, 'run-b', 'b1'))

    const held = socket.lane('lane/01')
    expect(held.runId).toBe('run-b')
    expect(held.lastSeq).toBe(1)
    expect(held.entries.map((e) => (e as { text?: string }).text)).toEqual(['b1'])
    socket.close()
  })

  it('a_frame_that_names_no_run_names_no_change', () => {
    // Round 1's own comment said this and round 1's code did not do it. `??` falls back on `null`
    // and `undefined` — NOT on the empty string, which is exactly what the wire carries: the
    // permission path publishes a transcript entry with `run_id: ""` when the lane has no run in
    // `runByLane_` (`permissions/store.cpp` → `socket.cpp`). So `""` read as a NEW run and erased
    // the lane's whole transcript, one line under a comment promising the opposite.
    //
    // `SequenceLog::beginRun` refuses an empty run id on the other side of the wire for the same
    // reason. Transcribed from QA round 1, PROBE_A.
    const socket = connect('ws://test/ws', {
      factory: () => new FakeSocket() as unknown as WebSocket,
    })
    socket.subscribe({ lane: 'lane/01' })
    FakeSocket.last.onopen?.()
    FakeSocket.last.deliver(entry(1, 'run-a', 'a1'))
    FakeSocket.last.deliver(entry(2, 'run-a', 'a2'))
    FakeSocket.last.deliver(entry(3, 'run-a', 'a3'))
    FakeSocket.last.deliver(entry(4, '', 'a permission decision'))

    const held = socket.lane('lane/01')
    expect(held.entries.map((e) => (e as { text?: string }).text))
      .toEqual(['a1', 'a2', 'a3', 'a permission decision'])
    expect(held.runId).toBe('run-a')
    expect(held.lastSeq).toBe(4)
    socket.close()
  })

  it('a_snapshot_with_no_run_does_not_turn_the_next_frame_into_a_wipe', () => {
    // Transcribed from QA round 2, PROBE_A. The server emits `"run_id": null` in a lane snapshot
    // whenever its `l.runId` is empty — `subscription.cpp` — and one permission entry published
    // with `run_id: ""` is enough to make that happen mid-run. The module then held `runId: null`
    // with a hundred entries, and the next frame OF THE RUN ALREADY ON SCREEN compared
    // `'run-a' !== null`, took the run-change branch and wiped the record.
    //
    // Three rounds, three values of *nothing*: `undefined`, `""`, and now `null` held on the
    // other side of the comparison. `isRunChange` is why there cannot be a fourth.
    const socket = connect('ws://test/ws', {
      factory: () => new FakeSocket() as unknown as WebSocket,
    })
    socket.subscribe({ lane: 'lane/01' })
    FakeSocket.last.onopen?.()
    FakeSocket.last.deliver({
      m: 'snapshot', ts: iso, seq: 3, scope: { lane: 'lane/01' },
      data: {
        lane: 'lane/01', run_id: null, next_after_seq: 3, complete: true,
        entries: [
          { seq: 1, ts: iso, kind: 'prose', text: 'one' },
          { seq: 2, ts: iso, kind: 'prose', text: 'two' },
          { seq: 3, ts: iso, kind: 'permission_request' },
        ],
      },
    })
    expect(socket.lane('lane/01').entries).toHaveLength(3)

    FakeSocket.last.deliver(entry(4, 'run-a', 'four'))       // the SAME run, still producing

    const held = socket.lane('lane/01')
    expect(held.entries.map((e) => (e as { text?: string }).text))
      .toEqual(['one', 'two', undefined, 'four'])
    expect(held.runId).toBe('run-a')
    expect(held.lastSeq).toBe(4)
    socket.close()
  })

  it('MANDATORY_NEGATIVE_a_real_replay_is_still_dropped', () => {
    // The cheap fix is to delete the `seq <= lastSeq` guard, which makes every overlapping frame
    // of the SAME run draw twice — which is exactly what `resume` produces by design.
    const socket = connect('ws://test/ws', {
      factory: () => new FakeSocket() as unknown as WebSocket,
    })
    socket.subscribe({ lane: 'lane/01' })
    FakeSocket.last.onopen?.()
    FakeSocket.last.deliver(entry(1, 'run-a', 'first'))
    FakeSocket.last.deliver(entry(2, 'run-a', 'second'))
    FakeSocket.last.deliver(entry(2, 'run-a', 'second'))     // the overlap a resume replays
    FakeSocket.last.deliver(entry(1, 'run-a', 'first'))

    const held = socket.lane('lane/01')
    expect(held.entries.map((e) => (e as { text?: string }).text)).toEqual(['first', 'second'])
    expect(held.lastSeq).toBe(2)
    socket.close()
  })
})
