import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react'

import {
  claimLane, getLanes, releaseLane, startRun, type AgentSelection, type Fetched, type Released, type Started,
} from './http'
import { useFactory } from './factory'
import type { Lane, Resource } from '../types/api'
import type { ServerFrame } from '../types/socket'

/**
 * The lane set — E9, read once and shared.
 *
 * It is §4.1's data (the `N lanes need you` chip) and §4.4's (the rail), and those two are on the
 * screen at the same time. Two readers of one endpoint are two answers that can disagree, and the
 * one they would disagree about is a count of the lanes that need a person — the number this whole
 * product exists to put in front of somebody.
 *
 * After the first read it is patched, never re-read on a schedule:
 *
 *   `M2 lane.state`   the three axes of one lane, and whether it needs a person
 *   `M3 stage`        where it is in its sequence; the bar is `index/total` and nothing else
 *   `M4 lane.metrics` elapsed and cost, which is also when a card's clock is re-anchored
 *
 * A frame naming a lane this set does not hold means a lane was claimed or released while the
 * page was open, and E9 is read again — ON THAT EVENT. That is the only refetch, and there is no
 * timer anywhere in this file.
 *
 * **WEB-FEAT-044 made it the only place a lane is created or destroyed.** E12 and E13 change the
 * membership of this set, and the set is what §4.1's counter and §4.4's rail both draw, so the
 * mutation belongs beside the read: a control that claimed a lane and then asked E9 what happened
 * would be a refetch on a click, and the same control in a second seat would be a second answer.
 * E12's `201` IS a lane row, so the claim is applied to the list directly — nothing is re-read and
 * nothing waits for a frame. A claim in flight is visible here too (`claiming`), because the seat
 * and the branch it is about to take are one resource and two controls can reach it.
 */

export interface LaneSet {
  selections: Record<string, AgentSelection>
  selectAgent(ticket: string, selection: AgentSelection): void
  lanes: Lane[]
  /**
   * E9's exclusive resources, patched by `M8 resource`.
   *
   * The rail needs exactly one thing from them and it is the one thing `waiting_for` does not
   * carry: the `description`. `queued for climate-chamber` names a key in a manifest; `one
   * chamber, one run at a time` is what a person needs to know to decide whether to wait.
   */
  resources: Resource[]
  /** how many lanes the SERVER said need a person — never recomputed here */
  needsYou: number
  /** the host's seat total, as the SERVER declared it. `null` until E9 has answered once. */
  seats: number | null
  /** true once E9 has answered, however it answered */
  loaded: boolean
  /** when each row's `elapsed_s` was last true, so a card's clock counts from the right moment */
  anchors: Record<string, number>
  /**
   * when each row's `remaining_s` was last true — WEB-FIX-428. Not `anchors`: M3 carries a fresh
   * `remaining_s` and no `elapsed_s`, so one anchor for both would restart the elapsed clock from
   * a number that is no longer true.
   */
  leftAnchors: Record<string, number>
  /** a claim is in flight; every control that could start a second one is disabled while it is */
  claiming: boolean
  /**
   * E12 — claim a lane for a ticket. On `201` the row is in this set before this resolves.
   *
   * Refuses outright while another claim is in flight and answers `null` for that: a seat, a
   * branch and a worktree are allocated together, and a second request produces either a second
   * lane nobody asked for or a `409 lane_exists` that arrives after the directory exists.
   */
  claim(ticketId: string, workspace: string, role?: string): Promise<Fetched<Lane> | null>
  /** E13 — release a lane. On `200` the row is out of this set before this resolves. */
  release(lane: string, force?: boolean, discardIgnored?: boolean): Promise<Fetched<Released>>
  /**
   * E18 — start a run in a lane that holds none. On `201` the row holds it before this resolves.
   *
   * WEB-FEAT-045. It is here rather than in the composer for the same reason `claim` is: the row
   * every surface draws has to learn about the run, and a caller who forgot to say so would leave
   * the composer starting a second one.
   */
  start(lane: string, prompt?: string, selection?: AgentSelection): Promise<StartOutcome>
}

/**
 * E18's answer, plus the one thing its refusal does not carry.
 *
 * `409 wrong_state` says `{lane, state:"running"}` (`app/agent/session.cpp:343`) and never names the
 * run holding the lane — but that refusal is PROOF this client's row is stale, so E9 is read again
 * on it and `heldByRun` is what came back. §1 requires the message to name the run; this is where
 * the name comes from, and it is an event-driven read, not a poll.
 */
export type StartOutcome = Fetched<Started> & { heldByRun?: string }

const NO_CLAIM = async () => null
const NO_WRITE = async (): Promise<Fetched<never>> => ({ ok: false, status: 0, error: null })

const EMPTY: LaneSet = {
  selections: {}, selectAgent: () => {},
  lanes: [], resources: [], seats: null, needsYou: 0, loaded: false, anchors: {}, leftAnchors: {},
  claiming: false, claim: NO_CLAIM, release: NO_WRITE, start: NO_WRITE,
}

const Context = createContext<LaneSet | null>(null)

export function useLanes(): LaneSet {
  return useContext(Context) ?? EMPTY
}

interface Held {
  lanes: Lane[]
  resources: Resource[]
  /**
   * E9's own seat count. Carried rather than inferred: the rail used to draw
   * `Math.max(lanes.length, 3)`, which is a number the CLIENT invented — it read `0/3` on a host
   * declaring ten, and would have read `7/7` on the same host with seven lanes. A seat total is
   * the manifest's, and a screen that computes one is a screen that can disagree with the tool.
   */
  seats: number | null
  loaded: boolean
  anchors: Record<string, number>
  leftAnchors: Record<string, number>
}

/** Every row's clock re-anchored to now — what E9 just said is true as of this moment. */
function stamp(lanes: Lane[], at: number): Record<string, number> {
  const out: Record<string, number> = {}
  for (const lane of lanes) out[lane.lane] = at
  return out
}

export function LanesProvider({ children }: { children: ReactNode }) {
  const [selections, setSelections] = useState<Record<string, AgentSelection>>({})
  const selectAgent = useCallback((ticket: string, selection: AgentSelection) => {
    setSelections((current) => ({ ...current, [ticket]: selection }))
  }, [])
  const { onFrame, reconnects } = useFactory()
  const [held, setHeld] = useState<Held>(
    { lanes: [], resources: [], seats: null, loaded: false, anchors: {}, leftAnchors: {} })
  const [claiming, setClaiming] = useState(false)

  // Returns what it read, because one caller needs the ANSWER and not only the effect: E18's
  // `409 wrong_state` is read again here to find the run that holds the lane (see `start`).
  const reload = useCallback(async () => {
    const answer = await getLanes()
    // `ok` and a null body is a 200 that parsed to nothing. It reaches the SHELL, so an unguarded
    // read here throws inside the provider and takes every surface with it — the same rule §5
    // states for documents: one unreadable answer may not blank a screen. Treated as no lanes.
    if (answer.ok && answer.data) {
      const at = Date.now()
      setHeld({ lanes: answer.data.lanes ?? [], resources: answer.data.resources ?? [],
                seats: answer.data.seats?.host?.total ?? null,
                loaded: true, anchors: stamp(answer.data.lanes ?? [], at),
                leftAnchors: stamp(answer.data.lanes ?? [], at) })
    } else {
      // A server without lanes is not an error state on this screen: §4.4's empty block is what
      // it renders, and it renders it as soon as it knows rather than sitting on a placeholder.
      setHeld((current) => ({ ...current, loaded: true }))
    }
    return answer
  }, [])

  // Two triggers, and neither is a clock: the surface mounted, and the socket came back after a
  // drop — the one moment anything M2 said while it was down is simply gone.
  useEffect(() => { void reload() }, [reload, reconnects])

  // Guarded so that a frame for an unknown lane refetches once rather than once per frame while
  // the answer is in flight.
  const refetching = useRef(false)
  useEffect(() => onFrame((frame: ServerFrame) => {
    // M8 — a resource was claimed, released, or its queue moved. It names no LANE, so it cannot
    // go through the unknown-lane guard below: it patches the resource table the queued cards read
    // their description out of, and a frame about a resource nobody is waiting for changes nothing.
    if (frame.m === 'resource') {
      setHeld((current) => {
        const index = current.resources.findIndex((row) => row.name === frame.name)
        // WEB-FIX-200 — M8 PATCHES this row, it does not construct one. `Resource` is E9's shape
        // and E9 is a read: `holder_alive` is derived server-side at read time (`render.cpp`)
        // because nothing delivers an event when a process somewhere else dies. M8 is an edge and
        // carries no liveness — `socket.schema.json` closes the frame — so the only thing that
        // ever said stays said while the frame names the SAME holder it was said about, and is
        // `null` (unknown) the moment the holder changes or the row is new. Never `true` by
        // invention: `holder_alive: false` is what tells a queue on a corpse from an ordinary
        // queue (SRV-FEAT-180), and a queue move must not erase it. Writing this row whole is
        // what stopped `main` compiling the day E9 grew a member M8 does not have.
        const known = index < 0 ? null : current.resources[index]
        const next: Resource = {
          name: frame.name, exclusive: frame.exclusive, description: frame.description ?? null,
          held_by: frame.held_by,
          // `queue` is `unknown[]` in the GENERATED socket types — the generator does not read
          // `items` — so it is narrowed here rather than asserted. E9's own type says `string[]`.
          queue: frame.queue.filter((entry): entry is string => typeof entry === 'string'),
          holder_alive: known !== null && known.held_by !== null
                        && known.held_by === frame.held_by
            ? known.holder_alive
            : null,
        }
        const resources = current.resources.slice()
        if (index < 0) resources.push(next)
        else resources[index] = next
        return { ...current, resources }
      })
      return
    }

    // M17 — the lane SET changed (SRV-FIX-080). The only frame that is not a patch: every other
    // one describes a row that exists, and this one says a row appeared or went away.
    //
    // A CLAIM goes through the unknown-lane guard below on purpose rather than round its side —
    // one path, one refetch guard, and a lane this client claimed itself is already in the set
    // (E12's `201` IS the row), so its own frame costs nothing. A RELEASE cannot: the row IS
    // held, so the guard would patch it with a frame that carries no state instead of removing
    // it. It is dropped here, with its clock anchor, so no card outlives the lane it draws.
    if (frame.m === 'lane.membership' && frame.change === 'released') {
      setHeld((current) => {
        const index = current.lanes.findIndex((row) => row.lane === frame.lane)
        if (index < 0) return current
        const lanes = current.lanes.slice()
        lanes.splice(index, 1)
        const anchors = { ...current.anchors }
        delete anchors[frame.lane]
        const leftAnchors = { ...current.leftAnchors }
        delete leftAnchors[frame.lane]
        // Nothing is done about §4.1's counter here, and nothing needs to be: it is a filter over
        // these same rows (`needsYou` below), each row's `needs_you` still the server's own word.
        // Removing the row removes it from the count, which is why there is one definition of it.
        return { ...current, lanes, anchors, leftAnchors }
      })
      return
    }

    const named = laneOf(frame)
    if (!named) return
    setHeld((current) => {
      const index = current.lanes.findIndex((row) => row.lane === named)
      if (index < 0) {
        if (!refetching.current) {
          refetching.current = true
          void reload().finally(() => { refetching.current = false })
        }
        return current
      }
      const patched = patch(current.lanes[index], frame)
      if (patched === current.lanes[index]) return current
      const lanes = current.lanes.slice()
      lanes[index] = patched
      const anchors = frame.m === 'lane.metrics'
        ? { ...current.anchors, [named]: Date.now() }
        : current.anchors
      const leftAnchors = frame.m === 'lane.metrics' || frame.m === 'stage'
        ? { ...current.leftAnchors, [named]: Date.now() }
        : current.leftAnchors
      return { ...current, lanes, anchors, leftAnchors }
    })
  }), [onFrame, reload])

  /**
   * E12, and the row it answers with, put straight into the set.
   *
   * `inFlight` is a ref and not the state below, because two clicks in one React batch see the
   * same state: the ref is what makes the second one a no-op rather than a second worktree. The
   * state exists so the controls can SAY they are busy — the guard and the drawing are two jobs.
   */
  const inFlight = useRef(false)
  // `role` reaches E12 because a QA lane and a run lane are different work in the same shape, and
  // the server chooses the agent's prompt from it (WEB-FEAT-350). Defaulted, so every existing
  // caller keeps claiming a run lane without saying so.
  const claim = useCallback(async (ticketId: string, workspace: string, role = 'run') => {
    if (inFlight.current) return null
    inFlight.current = true
    setClaiming(true)
    try {
      const answer = await claimLane(ticketId, workspace, role)
      if (answer.ok) {
        const row = answer.data
        const at = Date.now()
        setHeld((current) => (current.lanes.some((held) => held.lane === row.lane)
          ? current
          : { ...current, lanes: [...current.lanes, row], loaded: true,
              anchors: { ...current.anchors, [row.lane]: at },
              leftAnchors: { ...current.leftAnchors, [row.lane]: at } }))
      }
      return answer
    } finally {
      inFlight.current = false
      setClaiming(false)
    }
  }, [])

  /** E13. The row goes on the `200`, never on the click — a release that failed changes nothing. */
  const release = useCallback(async (lane: string, force = false, discardIgnored = false) => {
    const answer = await releaseLane(lane, force, discardIgnored)
    if (answer.ok) {
      setHeld((current) => ({
        ...current,
        lanes: current.lanes.filter((row) => row.lane !== lane),
      }))
    }
    return answer
  }, [])

  /**
   * E18, and the row learning it has a run — WEB-FEAT-045.
   *
   * On `201` the response carries a `run_id`, a `session_id` and a **pid**, so the process exists
   * by the time this reads it. The process axis is set to `running` because that is what E9 will
   * say about a lane with a live pid and an unfinished turn (`app/lane/state.cpp:56`), and the very
   * next `M2` replaces it — this is the row catching up with a fact the response stated, not the
   * client inventing a state.
   */
  const start = useCallback(async (lane: string, prompt?: string, selection?: AgentSelection): Promise<StartOutcome> => {
    const answer = await startRun(lane, prompt, selection)
    if (answer.ok) {
      const { run_id: runId, session_id: sessionId } = answer.data
      setHeld((current) => {
        const index = current.lanes.findIndex((row) => row.lane === lane)
        if (index < 0) return current
        const lanes = current.lanes.slice()
        lanes[index] = {
          ...lanes[index], run_id: runId, session_id: sessionId,
          state: { ...lanes[index].state, process: 'running' },
        }
        return { ...current, lanes }
      })
      return answer
    }
    // The one refusal that means THIS SCREEN IS WRONG: a run is live and this row said it was not.
    // So E9 is read again — on that event, once — and the run it names is handed back for the
    // message, because E18's own 409 does not carry it.
    if (answer.status === 409 && answer.error?.code === 'wrong_state') {
      const fresh = await reload()
      const row = fresh.ok
        ? (fresh.data.lanes ?? []).find((held) => held.lane === lane)
        : undefined
      return { ...answer, heldByRun: row?.run_id ?? undefined }
    }
    return answer
  }, [reload])

  const value = useMemo<LaneSet>(() => ({
    selections, selectAgent,
    lanes: held.lanes,
    resources: held.resources,
    needsYou: held.lanes.filter((row) => row.needs_you).length,
    seats: held.seats,
    loaded: held.loaded,
    anchors: held.anchors,
    leftAnchors: held.leftAnchors,
    claiming,
    claim,
    release,
    start,
  }), [held, claiming, claim, release, start, selections, selectAgent])

  return <Context.Provider value={value}>{children}</Context.Provider>
}

/** The lane a frame is about, or nothing — the four the rail listens to and no others. */
function laneOf(frame: ServerFrame): string | null {
  if (frame.m === 'lane.state' || frame.m === 'stage') return frame.lane ?? null
  if (frame.m === 'lane.metrics') return frame.lane
  // `claimed` only — a release is handled above, where the row is REMOVED rather than patched.
  if (frame.m === 'lane.membership') return frame.lane
  // M18 — FAC-EPIC-373. It goes through the same guard as every other patch, so a frame naming a
  // lane this set does not hold refetches once rather than constructing a row out of one line.
  if (frame.m === 'lane.words') return frame.lane
  return null
}

/** One row, with one frame applied. Returns the row itself when the frame changed nothing. */
function patch(row: Lane, frame: ServerFrame): Lane {
  if (frame.m === 'lane.state') {
    const state = (frame.state as unknown as Lane['state']) ?? row.state
    // FAC-EPIC-373 — a new run says nothing until it speaks. M18 never carries `null`, so the
    // `null` E18 leaves reaches a row already on screen only here and through the E9 re-read
    // M17 `claimed` triggers. ENTERING `running` and not being in it: a lane produces M2 on
    // every axis change it has, and clearing on all of them blanks a working lane's line.
    const started = state?.process === 'running' && row.state?.process !== 'running'
    return {
      ...row,
      state,
      needs_you: frame.needs_you,
      waiting_for: frame.waiting_for === undefined ? row.waiting_for : frame.waiting_for,
      last_words: started ? null : row.last_words,
    }
  }
  if (frame.m === 'lane.words') {
    return { ...row, last_words: frame.last_words as unknown as Lane['last_words'] }
  }
  if (frame.m === 'stage') {
    const stage = frame.stage as Partial<Lane['stage']> | undefined
    if (!stage) return row
    // The bar and the time left are the SERVER's (SRV-FIX-599) — never derived here. Deriving the
    // bar from `index / total` drew 70 % for a ticket the server had already called done.
    return {
      ...row,
      stage: { ...row.stage, ...stage },
      progress_pct: frame.progress_pct,
      remaining_s: frame.remaining_s,
    }
  }
  if (frame.m === 'lane.metrics') {
    return { ...row, elapsed_s: frame.elapsed_s, cost_usd: frame.cost_usd,
             remaining_s: frame.remaining_s }
  }
  return row
}
