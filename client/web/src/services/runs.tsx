import { useEffect, useSyncExternalStore } from 'react'

import { getRuns, getTranscript, detailPath } from './http'
import { useFactory } from './factory'
import type { RunHistory, RunRow, TranscriptEntry } from '../types/api'
import type { ServerFrame } from '../types/socket'

/**
 * Which run you are looking at — SPEC.md §4.10.
 *
 * Two facts live here and they belong together: the ticket's run rows (E7, patched by `M11`), and
 * the ONE run whose transcript is being read back (E8). They are set on the RUNS tab and read on
 * the SESSION tab, which are never mounted at the same time, so the selection is module state
 * rather than a context: a provider would have to go in `App.tsx`, and four tickets are adding
 * tabs to that page in parallel.
 *
 * The rule this file exists to keep is the one in §4.10's second paragraph and in this ticket's
 * mandatory negative: **a replayed run is not the live one.** `M5 transcript` is the live run's
 * stream and it never reaches a replay — see `applyFrame` below, which is the whole guard.
 */

export interface Selection {
  ticketId: string
  runId: string
  n: number
  startedIso: string
  /** the wire outcome, kept fresh from `M11` while the run is being read */
  outcome: string
  /** §3.4 — the delivery report cites THIS run, which is what makes `completed` proven */
  proven?: boolean
}

export interface Replay extends Selection {
  entries: TranscriptEntry[]
  /** E8's own answer: false when even the endpoint's maximum was not the whole run */
  complete: boolean
  loading: boolean
  /** where the transcript was expected, when the run's log has been cleaned (E8's 404) */
  missing?: string
}

interface Store {
  selection: Selection | null
  entries: TranscriptEntry[]
  complete: boolean
  loading: boolean
  missing?: string
}

let store: Store = { selection: null, entries: [], complete: true, loading: false }
const listeners = new Set<() => void>()

function notify() {
  for (const listener of Array.from(listeners)) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

const snapshot = () => store

/** Read a finished run back. The RUNS tab calls this; nothing else may. */
export function selectRun(selection: Selection) {
  if (store.selection?.runId === selection.runId) return
  store = { selection, entries: [], complete: true, loading: true }
  notify()
}

/** Back to the live run — and the only way out of a replay. */
export function clearReplay() {
  if (!store.selection && store.entries.length === 0 && !store.loading) return
  store = { selection: null, entries: [], complete: true, loading: false }
  notify()
}

/**
 * Every frame, while any of this module's hooks is mounted.
 *
 * `M11` is a message about RUNS, so it patches the run being replayed: a run selected while it
 * was still going has to stop saying `running` when it stops running.
 *
 * `M5` is a message about ENTRIES, and it belongs to the live run. It is dropped here, on purpose
 * and in one line, because appending it to a replay is how a transcript starts lying about which
 * run did what — the two share every component on this screen, and a reader who cannot tell them
 * apart has no way to notice. `runs.test.tsx::MANDATORY_NEGATIVE_a_replayed_run_cannot_be_mistaken
 * _for_the_live_one` is what keeps this line here.
 */
function applyFrame(frame: ServerFrame) {
  if (frame.m === 'transcript') return
  if (frame.m !== 'run') return
  const selection = store.selection
  if (!selection || frame.run_id !== selection.runId) return
  if (selection.outcome === frame.outcome) return
  store = { ...store, selection: { ...selection, outcome: frame.outcome } }
  notify()
}

/** What is being replayed, or nothing at all — which is the ordinary case. */
export function useReplay(): Selection | null {
  return useSyncExternalStore(subscribe, snapshot, snapshot).selection
}

/**
 * The replayed run, loaded — and only on the ticket it was selected on.
 *
 * `WEB-FEAT-014`'s SESSION tab calls this and, when it answers, renders `<ReplayedRun>` instead of
 * its live column and its composer — two lines, and the only thing this ticket asks of that one.
 *
 * `ticketId` is what keeps the selection where it was made. The store has carried it since
 * `WEB-FEAT-016` and nothing read it, so a run selected on one ticket was replayed on the SESSION
 * tab of every OTHER ticket that reached this hook — a banner reading `reading run #3` over
 * another ticket's header, with that run's transcript under it. It was only reachable from a
 * ticket holding a lane while the replay lived inside `LiveSession`; `WEB-FIX-354` gives the
 * pane to the idle branch as well, which is every ticket, so the scope is part of that change
 * rather than a separate one.
 */
export function useReplayedRun(ticketId?: string): Replay | null {
  const { onFrame, reconnects } = useFactory()
  const held = useSyncExternalStore(subscribe, snapshot, snapshot)
  const selection = held.selection && (ticketId === undefined
                                       || held.selection.ticketId === ticketId)
    ? held.selection : null
  const runId = selection?.runId

  useEffect(() => onFrame(applyFrame), [onFrame])

  useEffect(() => {
    if (!runId) return undefined
    let alive = true
    void getTranscript(runId).then((answer) => {
      if (!alive || store.selection?.runId !== runId) return
      store = answer.ok
        ? { ...store, entries: answer.data.entries ?? [], complete: answer.data.complete,
            loading: false, missing: undefined }
        : { ...store, entries: [], complete: true, loading: false,
            missing: detailPath(answer) ?? '' }
      notify()
    })
    return () => { alive = false }
  }, [runId, reconnects])

  if (!selection) return null
  return {
    ...selection,
    entries: held.entries,
    complete: held.complete,
    loading: held.loading,
    missing: held.missing,
  }
}

export interface RunsState {
  rows: RunRow[]
  loading: boolean
  /**
   * Whether E7 was actually read. A request that failed is not a ticket with no runs, and the
   * difference decides what the header above the table may say: `false` means this module knows
   * nothing and the document's own count stands, `true` with no rows means there are none.
   */
  known: boolean
  /** when each row's `duration_s` was last true, so a live row's clock counts from the right moment */
  stamps: Record<string, number>
}

/**
 * The ticket screen's run history, held once for the whole screen.
 *
 * It is module state rather than a hook's own, because §4.5's header, §4.10's tab label and
 * §4.10's table are three readers of ONE answer. `WEB-FIX-091` is what two readings cost: the
 * table took E7 and `M11`, the two counts above it took E5, and a run starting moved one and not
 * the others — so a lane producing output sat under `0 runs · RUNS · 0`.
 *
 * `key` is the ticket and the reconnect generation together. Every reader's effect asks for the
 * same key, the first one starts the read and the rest join it, so a screen makes ONE request no
 * matter how many components want the answer.
 */
interface History extends RunsState { key: string }

let history: History = { key: '', rows: [], loading: true, known: false, stamps: {} }
let inFlight: string | null = null
const historyListeners = new Set<() => void>()

function notifyHistory() {
  for (const listener of Array.from(historyListeners)) listener()
}

function subscribeHistory(listener: () => void): () => void {
  historyListeners.add(listener)
  return () => { historyListeners.delete(listener) }
}

const historySnapshot = () => history

/** Test-only reset, so one case's ticket is not another case's rows. */
export function clearRunHistory() {
  history = { key: '', rows: [], loading: true, known: false, stamps: {} }
  inFlight = null
  notifyHistory()
}

/**
 * A ticket's runs — E7 once, then `M11` for as long as the screen is open.
 *
 * Three triggers and not one of them is a clock: the screen mounted, the socket came back after a
 * drop, and a frame said a run started or ended. A table that re-read itself every few seconds
 * would be the prototype's polling wearing a different hat.
 */
export function useRunHistory(ticketId: string, lanes: string[]): RunsState {
  const { onFrame, reconnects } = useFactory()
  const held = useSyncExternalStore(subscribeHistory, historySnapshot, historySnapshot)

  useEffect(() => {
    const key = `${ticketId}|${reconnects}`
    if (history.key !== key) {
      history = { key, rows: [], loading: true, known: false, stamps: {} }
      notifyHistory()
    } else if (!history.loading) {
      return                       // already read, and nothing has happened since
    }
    if (inFlight === key) return   // another reader on this screen is already asking
    inFlight = key

    void getRuns(ticketId).then((answer) => {
      if (inFlight !== key) return
      inFlight = null
      if (history.key !== key) return
      const served = answer.ok ? ((answer.data as RunHistory).runs ?? []) : []
      const at = Date.now()
      const stamps: Record<string, number> = { ...history.stamps }
      for (const row of served) stamps[row.run_id] = at

      // E7's answer is a photograph of the moment it was TAKEN, and a run that began after that
      // moment is not in it. Replacing the rows wholesale dropped exactly those — and `M11` fires
      // on start and on end, never on a timer, so a live run dropped here stayed dropped for its
      // whole life and the screen read `No runs recorded for this ticket.` over it. Newest first,
      // so what arrived while the request was in flight belongs on top of what it answered.
      const answered = new Set(served.map((row) => row.run_id))
      const newer = history.rows.filter((row) => !answered.has(row.run_id))
      history = { key, rows: [...newer, ...served], loading: false, known: answer.ok, stamps }
      notifyHistory()
    })
  }, [ticketId, reconnects])

  // A LIST of lanes, not one. A ticket holds as many lanes as it has roles — `run` and `qa` are
  // two — and the page resolves one of them for its header; a run started in the other is still a
  // run on this ticket. `M11` carries no `ticket_id` (`socket.schema.json`), so a frame cannot be
  // matched against the ticket directly: what makes a lane this ticket's is E9, which the page
  // already holds. Before E9 has answered there is nothing to match against and the frame is
  // taken, because it arrived on the scope this screen subscribed to.
  const owned = lanes.join('\u0000')
  useEffect(() => onFrame((frame: ServerFrame) => {
    if (frame.m !== 'run') return
    const mine = owned ? owned.split('\u0000') : []
    if (mine.length > 0 && !mine.includes(frame.lane)) return
    const at = Date.now()
    const index = history.rows.findIndex((row) => row.run_id === frame.run_id)
    const patch: Partial<RunRow> = {
      run_id: frame.run_id, n: frame.n, outcome: frame.outcome,
      agent: frame.agent, driver: frame.driver, model: frame.model, cost_known: frame.cost_known,
      started_iso: frame.started_iso, ended_iso: frame.ended_iso, stage: frame.stage,
      duration_s: frame.duration_s, cost_usd: frame.cost_usd, lane: frame.lane,
    }
    const rows = history.rows.slice()
    if (index < 0) {
      // A run that started while the screen was open. Newest first, so it goes on top — and it
      // arrives as a row rather than as a refetch, which is what keeps the no-polling rule.
      // Applied to the store rather than to one component's state, so the count in the header
      // and the row in the table are the same event. Two readers of one frame land here twice
      // and the second finds the row already present, which is why this is a patch by `run_id`
      // and not an append.
      rows.unshift({ session_id: '', ...patch } as RunRow)
    } else {
      rows[index] = { ...rows[index], ...patch }
    }
    // `known` is NOT set here. A frame is one run's news, not an answer about the ticket: after a
    // failed E7 the only thing this module knows is that one run exists, and promoting that into
    // the authoritative reading turned a ticket with twelve runs into `RUNS · 1`. Whether the
    // history was read is decided where it is read.
    history = { ...history, rows, loading: false,
                stamps: { ...history.stamps, [frame.run_id]: at } }
    notifyHistory()
  }), [onFrame, owned])

  return held
}

export interface RunTotals {
  count: number
  duration_s: number
  cost_usd: number
  /**
   * When each still-running row's `duration_s` was last true. The total is a sum of numbers the
   * server sent once, so a header holding it still while the row under it counts on is two
   * numbers about one run drifting apart on screen — the class this ticket exists to remove.
   */
  liveSince?: number[]
}

/**
 * `4 runs · 18m 40s · $1.86` — SPEC.md §4.5's identity row, and §4.10's `RUNS · 4`, from the rows
 * §4.10's table draws. F27's rule in one function: *the header reports the same number E7's RUNS
 * table does, from the same files.*
 *
 * The document's own count is the answer only while E7 has not given one — at mount, and when the
 * request failed. It was taken from the same directory a moment earlier, so it is the honest
 * stand-in; what it cannot do is survive a run starting, because a run does not change a document.
 *
 * `undefined` is "nobody has said" — a tab label that has no count says `RUNS`, not `RUNS · 0`.
 */
export function runTotals(doc: { runs?: RunTotals } | undefined,
                          runs: RunsState | undefined): RunTotals | undefined {
  if (runs && runs.known) {
    return {
      count: runs.rows.length,
      duration_s: runs.rows.reduce((sum, row) => sum + (row.duration_s ?? 0), 0),
      cost_usd: runs.rows.reduce((sum, row) => sum + (row.cost_usd ?? 0), 0),
      liveSince: runs.rows
        .filter((row) => row.outcome === 'running' && !row.ended_iso)
        .map((row) => runs.stamps[row.run_id])
        .filter((stamp): stamp is number => typeof stamp === 'number'),
    }
  }
  return doc?.runs
}
