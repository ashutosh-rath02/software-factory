import type {
  ClientFrame, Scope, ServerFrame, SnapshotFrame, TranscriptFrame,
} from '../types/socket'

import { isRunChange } from '../vocabulary'

export type { Scope, ServerFrame } from '../types/socket'

export type SocketState = 'connecting' | 'open' | 'lost'

/**
 * One transcript line as this module keeps it — SPEC.md E8's shape, which is also what a
 * `M1 snapshot` carries. `WEB-FIX-051`.
 *
 * It used to be declared `TranscriptFrame[]` and it never held only frames: `applySnapshot` put
 * E8's **entries** in and `apply` pushed whole `M5` **frames** beside them, so the array held two
 * shapes and the type named one. Nothing outside this file read it, which is the only reason that
 * survived — and `SRV-FIX-049` is the same confusion one layer down, where a snapshot carried
 * frames where the contract said entries and every transcript on every screen drew nothing.
 * Flattened on the way IN, once, so there is one shape here and one seam that converts.
 */
export interface LaneEntry {
  seq: number
  ts?: string
  kind: string
  [key: string]: unknown
}

/** What this client holds of one lane's transcript, and where it would resume from. */
/**
 * One command a person started in this lane — `M10`, E19 (`WEB-FIX-072`).
 *
 * It lives HERE, beside the lane's entries, for the reason `WEB-FIX-051` put the entries here: a
 * surface that mounts fresh is rebuilt from this module, and anything the module does not hold is
 * gone the moment a person leaves the tab. A command carries `seq: null` by contract and is
 * outside the run's sequence log, so no `snapshot` and no `resume` replays it — **nothing on the
 * wire can bring it back**, which is what makes losing it permanent rather than a flicker.
 *
 * Measured before choosing this over a server-side record, as §1 required: the server keeps no
 * command output at all (`lane/command.h` streams `Event`s and retains nothing) and there is no
 * endpoint to ask for it, so the alternative was a new server record AND a new endpoint. This is
 * where the client already keeps what a remount is seeded from.
 */
export interface LaneCommand {
  /** negative, counted down per lane — a React key, never compared with the run's sequence */
  seq: number
  ts: string
  which: string
  output: string
  exitCode?: number
}

export interface LaneState {
  entries: LaneEntry[]
  lastSeq: number
  /** every command started in this lane, keyed by `command_id`, in arrival order */
  commands: Map<string, LaneCommand>
  /**
   * The run those entries belong to — WEB-FIX-051 QA, F2.
   *
   * It is kept HERE because the entries do not carry it: a `M5` frame has `run_id`, and the entry
   * inside it does not, so a surface seeding itself from `entries` reads `undefined` however
   * carefully it looks. The first fix did exactly that and always seeded `null`, which makes a NEW
   * run in the same lane look like a replay — every one of its entries is `seq <= lastSeq` — until
   * a snapshot happens to arrive. The comment beside it explained why `lastSeq` must travel with
   * the entries and then took the wrong field on the next line.
   */
  runId: string | null
}

export interface FactorySocket {
  /**
   * A SURFACE has appeared (M14) — which is not the same event as a CONNECTION coming back (M15),
   * and conflating them is `WEB-FIX-051`. Returns what this module already holds for the scope's
   * lane, so a surface that mounts on a live socket draws immediately instead of blanking until a
   * round trip lands. `null` for a scope that is not a lane's.
   */
  subscribe(scope: Scope): LaneState | null
  send(frame: ClientFrame): void
  lane(name: string): LaneState
  state(): SocketState
  close(): void
}

export interface Options {
  onFrame?: (frame: ServerFrame) => void
  onState?: (state: SocketState) => void
  factory?: (url: string) => WebSocket
  retryMs?: number
  pingMs?: number
  /**
   * SPEC.md §4.1 — the connection is live while a frame arrived within this window.
   *
   * The ping exists to keep it inside that window (M16), so the two numbers are one rule: a ping
   * every 10 s, answered at once by a bare `pong`, means silence longer than 15 s is a connection
   * that is not there.
   */
  silenceMs?: number
}

const scopeKey = (scope: Scope): string =>
  'requirements' in scope ? 'requirements'
    : 'ticket' in scope ? `ticket:${scope.ticket}`
      : `lane:${scope.lane}`

const laneOf = (scope: Scope): string | null => ('lane' in scope ? scope.lane : null)

/**
 * The only thing in the client that talks to the socket.
 *
 * It reconnects on its own, because the tab is open for hours and a laptop sleeping is not an
 * error worth showing a person. What it does not do is hide the fact that it dropped: `onState`
 * fires so the surface can say so and keep whatever the user had typed.
 *
 * On coming back it does not refetch. It sends `resume` with the last sequence it holds and the
 * server replays only what was missed — or, when the log cannot cover the gap, answers with a
 * snapshot carrying `replaces_tail`, and this client then DISCARDS its tail instead of merging
 * into a hole. Merging is undetectable afterwards, which is why the decision is the server's and
 * the obedience is here.
 *
 * **A CUT CONNECTION DOES NOT CLOSE.** `onclose` is what a server hanging up looks like; it is not
 * what a cut wire, a sleeping laptop or a wedged server looks like. Those stop answering, the
 * pings pile up in a buffer the machine can no longer flush, and `onclose` never fires — so a
 * client whose only evidence is `onclose` reports `socket live` for ever over a wire nothing can
 * arrive on. Every live state on the screen then freezes at its last value while claiming to be
 * current, which is a composer still offering `Stop` on a lane that went quiet an hour ago.
 *
 * So silence is the evidence, exactly as SPEC.md §4.1 states it: a frame within `silenceMs` or the
 * connection is gone. That is what the ping was always for (M16) — it was sent and its answer was
 * never read.
 */
export function connect(url: string, options: Options = {}): FactorySocket {
  const { onFrame, onState, factory = (u) => new WebSocket(u), retryMs = 1000,
          pingMs = 10000, silenceMs = 15000 } = options

  let ws: WebSocket | null = null
  let closed = false
  let current: SocketState = 'connecting'
  let retry: ReturnType<typeof setTimeout> | null = null
  let heartbeat: ReturnType<typeof setInterval> | null = null
  let deadline: ReturnType<typeof setTimeout> | null = null

  const scopes = new Map<string, Scope>()
  const lanes = new Map<string, LaneState>()
  const laneState = (name: string): LaneState => {
    let state = lanes.get(name)
    if (!state) { state = { entries: [], lastSeq: 0, runId: null, commands: new Map() }; lanes.set(name, state) }
    return state
  }

  const setState = (next: SocketState) => { current = next; onState?.(next) }

  const send = (frame: ClientFrame) => {
    if (ws && current === 'open') ws.send(JSON.stringify(frame))
  }

  const stopTimers = () => {
    if (heartbeat) { clearInterval(heartbeat); heartbeat = null }
    if (deadline) { clearTimeout(deadline); deadline = null }
  }

  /** The connection is gone, however it went. One path, so `lost` and the retry cannot diverge. */
  const dropped = () => {
    stopTimers()
    if (closed) return
    setState('lost')
    retry = setTimeout(open, retryMs)
  }

  /**
   * Nothing has arrived for `silenceMs`, and a ping inside that window went unanswered.
   *
   * The handlers come off first and the reconnect does not wait on `close()`: closing a socket the
   * network has already abandoned starts a handshake that may never complete, and a client that
   * waited for it would stay silently `open` for exactly as long as the problem lasts.
   */
  const stalled = () => {
    const gone = ws
    ws = null
    if (gone) {
      gone.onopen = null
      gone.onmessage = null
      gone.onclose = null
      gone.onerror = null
      try { gone.close() } catch { /* it is unreachable; that is what this is about */ }
    }
    dropped()
  }

  /** A frame arrived, so this connection is alive for another `silenceMs` (SPEC.md §4.1). */
  const heard = () => {
    if (deadline) clearTimeout(deadline)
    deadline = setTimeout(stalled, silenceMs)
  }

  /** A reconnect is a resume where there is a tail, and a subscribe where there is not. */
  const restore = () => {
    for (const scope of scopes.values()) {
      const lane = laneOf(scope)
      const held = lane ? laneState(lane).lastSeq : 0
      if (lane && held > 0) send({ m: 'resume', lane, after_seq: held })
      else send({ m: 'subscribe', scope })
    }
  }

  const applySnapshot = (frame: SnapshotFrame) => {
    const lane = laneOf(frame.scope)
    if (!lane) return
    const data = frame.data as
        { entries?: LaneEntry[]; next_after_seq?: number; run_id?: string | null }
    const entries = data.entries ?? []
    const state = laneState(lane)
    // The only frame that replaces state. Everything else patches.
    state.entries = [...entries]
    state.lastSeq = frame.seq ?? data.next_after_seq ?? 0
    state.runId = data.run_id ?? null
  }

  /** The one seam — a `M5` frame flattened into the shape a snapshot entry already has. */
  const entryOf = (frame: TranscriptFrame): LaneEntry =>
    ({ ...(frame.entry as Record<string, unknown>), seq: frame.seq, ts: frame.ts } as LaneEntry)

  const apply = (frame: ServerFrame) => {
    if (frame.m === 'snapshot') applySnapshot(frame)
    else if (frame.m === 'command') {
      // Keyed by lane FIRST and `command_id` second. A store keyed by `command_id` alone is the
      // cheap version of this and it puts one lane's gate output in another lane's transcript —
      // `commands.test.tsx::a_command_from_another_lane_is_still_ignored` is what stops it.
      const state = laneState(frame.lane)
      let held = state.commands.get(frame.command_id)
      if (!held) {
        held = { seq: -(state.commands.size + 1), ts: frame.ts, which: frame.which, output: '' }
        state.commands.set(frame.command_id, held)
      }
      // Exactly one of the two is present, and the exit is the last thing a command says.
      if (frame.exit_code === undefined || frame.exit_code === null) held.output += frame.chunk ?? ''
      else held.exitCode = frame.exit_code
    }
    else if (frame.m === 'transcript') {
      const state = laneState(frame.lane)
      // `seq` is monotonic PER RUN — SPEC.md §4.6, and a run numbers its entries from 1. So it
      // only means anything WITHIN a run, and comparing it across two of them is comparing an
      // ordinal to a different list's ordinal: run B's entry 1 sits behind run A's 5 and looks
      // exactly like a replay. Every early entry of a second run in the same lane was dropped
      // that way, and `WEB-FIX-051` made this module the seed for every mount, so a surface came
      // back showing a FINISHED run while a live one was producing (`WEB-FIX-073`).
      //
      // The run is therefore consulted BEFORE the sequence, not written after it. A frame that
      // names no run (the permission path, the test hook) names no change and keeps the current
      // one — `SequenceLog::beginRun` treats an empty run id the same way, on the other side.
      // `isRunChange` is the ONE answer to "has the run changed", asked here and by the
      // component. Round 1 of this ticket got `undefined` right and `""` wrong; round 2 fixed
      // `""` and left `state.runId === null` — a lane whose snapshot carried no run, which the
      // server emits whenever `l.runId` is empty — so the next frame OF THE RUN ALREADY ON SCREEN
      // read as a change and wiped the record. Three values of *nothing*, three rounds, two
      // copies of the rule. There is one copy now (`vocabulary.ts`).
      if (isRunChange(frame.run_id, state.runId)) {
        // The lane's log starts over, which is what the server does with its own on `beginRun`.
        state.entries = []
        state.lastSeq = 0
      }
      // A frame that names a run always names the one this lane is on: naming no run leaves the
      // record as it was, and naming a different one has just started it over.
      if (frame.run_id) state.runId = frame.run_id
      if (frame.seq <= state.lastSeq) return       // a replay overlapping what we already had
      state.entries.push(entryOf(frame))
      state.lastSeq = frame.seq
    }
  }

  const open = () => {
    setState('connecting')
    ws = factory(url)
    ws.onopen = () => {
      setState('open')
      heard()
      restore()
      heartbeat = setInterval(() => send({ m: 'ping' }), pingMs)
    }
    ws.onmessage = (e) => {
      // Before the parse: a frame arriving is evidence about the CONNECTION, and one this client
      // cannot read still proves the wire is carrying traffic.
      heard()
      let frame: ServerFrame
      try {
        frame = JSON.parse(String(e.data)) as ServerFrame
      } catch {
        return   // a frame we cannot parse is the server's bug, not a reason to tear down
      }
      apply(frame)
      onFrame?.(frame)
    }
    ws.onclose = () => { dropped() }
    ws.onerror = () => ws?.close()
  }

  open()

  return {
    /**
     * WEB-FIX-051 — a SURFACE always subscribes. It never resumes.
     *
     * This used to be `restore()`'s body, copied: pick `resume` when the module holds a tail. That
     * is right for a reconnect and wrong here, and the difference is which thing was away. A
     * reconnect asks "what did I miss?" because the CONNECTION missed frames. A mount asks nothing
     * of the kind — the connection missed nothing, so the honest answer to `resume` is *zero
     * frames*, and the surface, which resets to EMPTY on mount, drew the zero. That is the user's
     * report: leave the SESSION tab, come back, and the transcript is gone until the next frame
     * arrives and its seq gap triggers the resync that should have been the first question.
     *
     * `restore()` keeps the old choice, deliberately: it really is a reconnect, and turning it
     * into a subscribe would throw away the replay path `SRV-FEAT-005` exists for.
     * `tests/socket.test.ts::reconnect_resumes` and `transcript.test.tsx::a_reconnect_still_resumes`
     * both fail if anybody ever "simplifies" the two into one again.
     *
     * The held state is RETURNED rather than pushed, because a surface knows when it is mounting
     * and this module does not: seeding from here would mean guessing which caller is new.
     */
    subscribe(scope) {
      scopes.set(scopeKey(scope), scope)
      const lane = laneOf(scope)
      send({ m: 'subscribe', scope })
      return lane ? laneState(lane) : null
    },
    send,
    lane: laneState,
    state: () => current,
    close() {
      closed = true
      if (retry) clearTimeout(retry)
      stopTimers()
      ws?.close()
    },
  }
}
