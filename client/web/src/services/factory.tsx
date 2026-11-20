import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from 'react'

import { connect, type FactorySocket, type LaneState, type SocketState } from './socket'
import { getHealth, getManifest, type Fetched } from './http'
import type { CatalogChangedFrame, ClientFrame, Scope, ServerFrame } from '../types/socket'
import type { ErrorBody, Health, Manifest } from '../types/api'

/** The contract this client was generated against. A server on another one says so, loudly, once. */
export const CONTRACT = '0.1'

const WS_URL = () =>
  `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`

export interface Factory {
  socketState: SocketState
  /** the number of times the socket has come back after a drop; a surface reloads on a change */
  reconnects: number
  health?: Health
  manifest?: Manifest
  /**
   * E2's REFUSAL — WEB-FEAT-179.
   *
   * This endpoint was read for its success alone, so `invalid_manifest` and its four siblings —
   * the most precise sentences this server can produce, naming the exact JSON pointer that failed
   * — reached no surface at all. It is held here rather than on a page because a manifest that did
   * not load is the one failure that makes the whole tool unusable rather than one lane, and it
   * belongs beside `versionMismatch`, which is the other fact the shell draws on every screen.
   */
  manifestError?: ErrorBody['error'] | null
  /**
   * What `reload_manifest` came back with — E2's own body, once more.
   *
   * The remedy's control makes the call (`components/Remedy.tsx` builds it from the remedy) and
   * hands the answer here, so the banner clears because the SERVER said the file is valid now.
   * Nothing polls to find out and nothing reloads the page: `no-poll.test.tsx` covers the first
   * and the half-typed filter box in `refusals.test.tsx` covers the second.
   */
  applyManifestAnswer(answer: Fetched<unknown>): void
  /** the version the server greeted with, when it disagrees with ours */
  versionMismatch?: string
  subscribe(scope: Scope): void
  /**
   * WEB-FIX-051 — what this client already holds for a lane, for a surface that has just mounted.
   *
   * A remount on a LIVE socket misses nothing: the module-level lane map outlived the component
   * and still has every entry. Without this the surface reset itself to empty and waited for a
   * round trip that, on a caught-up connection, correctly carried zero frames — so it waited
   * forever, which is what the user saw. Reading it is not a fetch and not a poll; it is this
   * client reading its own memory.
   */
  held(lane: string): LaneState | null
  /**
   * Send one frame the socket already knows how to write (M14/M15/M16).
   *
   * The transcript needs it for exactly one thing: `resume` after a GAP, which `subscribe` cannot
   * express — the socket's own resume carries ITS last seq, and a client that saw 4 then 7 holds
   * a different number from the one that arrived last. §4.6's resync has to name the last
   * CONTIGUOUS sequence or the hole is asked about and then kept.
   */
  send(frame: ClientFrame): void
  onCatalogChange(listener: (change: CatalogChangedFrame) => void): () => void
  /**
   * Every frame, as it arrives. `onCatalogChange` is the document half of this; the lane set
   * (`services/lanes.tsx`) is the other, and it patches rows from `M2`, `M3` and `M4` rather than
   * reading a document again. Both are subscriptions to the socket the provider already owns —
   * a second connection per surface would be a second thing to reconnect.
   */
  onFrame(listener: (frame: ServerFrame) => void): () => void
}

const Context = createContext<Factory | null>(null)

export function useFactory(): Factory {
  const value = useContext(Context)
  if (!value) throw new Error('useFactory outside FactoryProvider')
  return value
}

/**
 * Owns the one socket and the two documents the shell reads (E1, E2).
 *
 * Nothing here is on a timer. The socket's 10 s ping is the only interval in the system, it
 * carries no state, and it never causes a render. Everything else moves because the server said
 * something moved — `M13 catalog.changed` — which is what makes "the documents are the database"
 * true rather than aspirational, and what stops a rebuild eating a half-typed message.
 */
export function FactoryProvider({ children }: { children: ReactNode }) {
  const [socketState, setSocketState] = useState<SocketState>('connecting')
  const [reconnects, setReconnects] = useState(0)
  const [health, setHealth] = useState<Health>()
  const [manifest, setManifest] = useState<Manifest>()
  const [manifestError, setManifestError] = useState<ErrorBody['error'] | null>(null)
  const [versionMismatch, setVersionMismatch] = useState<string>()

  const socket = useRef<FactorySocket | null>(null)
  /** scopes a surface asked for before the socket ref existed — see the drain below (WEB-FIX-051) */
  const waiting = useRef<Scope[]>([])
  const listeners = useRef(new Set<(change: CatalogChangedFrame) => void>())
  const frameListeners = useRef(new Set<(frame: ServerFrame) => void>())
  const wasLost = useRef(false)

  useEffect(() => {
    let alive = true
    const take = <T,>(result: Fetched<T>, set: (v: T) => void) => {
      if (alive && result.ok) set(result.data)
    }
    void getHealth().then((r) => take(r, setHealth))
    // E2 is read for BOTH answers. Keeping only the success is what left every manifest refusal
    // off every screen (WEB-FEAT-179).
    void getManifest().then((r) => {
      if (!alive) return
      if (r.ok) { setManifest(r.data); setManifestError(null) } else setManifestError(r.error)
    })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const s = connect(WS_URL(), {
      onState: (next) => {
        setSocketState(next)
        if (next === 'lost') wasLost.current = true
        // A document scope has no sequence to resume from, so anything M13 said while the
        // socket was down is simply gone. Coming back is the one moment a surface must read
        // again — an event, not a clock.
        if (next === 'open' && wasLost.current) {
          wasLost.current = false
          setReconnects((n) => n + 1)
        }
      },
      onFrame: (frame: ServerFrame) => {
        for (const listener of frameListeners.current) listener(frame)
        if (frame.m === 'hello' && frame.factory !== CONTRACT) setVersionMismatch(frame.factory)
        if (frame.m === 'catalog.changed') {
          for (const listener of listeners.current) listener(frame)
        }
        // M2 — SPEC.md §3.5's derived state moved for one ticket. It reaches the same surfaces
        // a change to that ticket's document reaches and by the same route, because it patches
        // exactly the same rows. Nothing here polls for it: the server looked at the world on a
        // worker and said so, which is the whole reason this is a frame and not a timer.
        //
        // ONLY the ticket-shaped frame. M2 has two shapes and one name: the TICKET one carries
        // §3.5's `delivery` with `lane: null`, and it is the one no endpoint but E5 can answer.
        // The LANE one carries §3.2's three axes, and `services/lanes.tsx` has already patched
        // them into the row every surface draws them from — re-reading the whole document for
        // them turned the highest-rate state frame in the contract into an HTTP request per
        // change, which is a poll with a socket for a clock. It also breaks the rule the frame
        // exists to keep: a state change reaches the screen because a frame carried it.
        if (frame.m === 'lane.state' && frame.ticket_id && frame.delivery) {
          const change: CatalogChangedFrame = {
            m: 'catalog.changed', ts: frame.ts, seq: null,
            kind: 'ticket', id: frame.ticket_id, path: '', change: 'modified',
          }
          for (const listener of listeners.current) listener(change)
        }
      },
    })
    socket.current = s
    // WEB-FIX-051 — every scope a surface asked for before this ref existed.
    //
    // A child's effect runs BEFORE its provider's, so on the very first mount `socket.current` is
    // still null and a `subscribe()` from a surface reached nothing at all — not even the scope
    // map. `useSubscription` covered that by depending on `socketState`, so the effect re-ran when
    // the connection opened; that worked, and it also re-ran on every RECONNECT, which is now the
    // one moment a surface must NOT subscribe (`restore()` resumes). Holding the asks here instead
    // separates the two: this is about a ref that did not exist yet, not about a connection.
    for (const scope of waiting.current) s.subscribe(scope)
    waiting.current = []
    return () => { s.close(); socket.current = null }
  }, [])

  const subscribe = useCallback((scope: Scope) => {
    if (socket.current) socket.current.subscribe(scope)
    else waiting.current.push(scope)
  }, [])

  const applyManifestAnswer = useCallback((answer: Fetched<unknown>) => {
    if (answer.ok) { setManifest(answer.data as Manifest); setManifestError(null) }
    else setManifestError(answer.error)
  }, [])

  const held = useCallback((lane: string) => socket.current?.lane(lane) ?? null, [])

  const send = useCallback((frame: ClientFrame) => { socket.current?.send(frame) }, [])

  const onCatalogChange = useCallback((listener: (c: CatalogChangedFrame) => void) => {
    listeners.current.add(listener)
    return () => { listeners.current.delete(listener) }
  }, [])

  const onFrame = useCallback((listener: (frame: ServerFrame) => void) => {
    frameListeners.current.add(listener)
    return () => { frameListeners.current.delete(listener) }
  }, [])

  const value = useMemo<Factory>(
    () => ({ socketState, reconnects, health, manifest, manifestError, applyManifestAnswer,
             versionMismatch, subscribe, held, send, onCatalogChange, onFrame }),
    [socketState, reconnects, health, manifest, manifestError, applyManifestAnswer,
     versionMismatch, subscribe, held, send,
     onCatalogChange,
     onFrame])

  return <Context.Provider value={value}>{children}</Context.Provider>
}

export interface Endpoint<T> {
  /** the last answer for THIS key, kept while a reload is in flight */
  result?: Fetched<T>
  /** true only until this key has ever answered — a region, never a page */
  loading: boolean
}

/**
 * Read one endpoint, and read it again when the server says that document changed.
 *
 * The reload triggers are exactly four, and none of them is a clock:
 *   1. `key` changed — the surface is looking at a different document
 *   2. `M13 catalog.changed` named a document this surface shows
 *   3. the socket came back after a drop (see above)
 *   4. `reloadsOn` accepted a frame — for a surface whose answer is derived from something other
 *      than a document. §4.18's counts move when a LANE moves, and `M2` is the only word for
 *      that; recomputing §3.3 in the browser instead would be a second implementation of the
 *      definition that matters most, so the surface asks the server again.
 *
 * While a reload is in flight the previous answer stays on screen, so nothing blanks and no
 * layout moves. A stale answer that arrives after a newer request started is dropped by
 * generation, not by arrival — typing quickly must not leave the older reply on the screen.
 */
export function useEndpoint<T>(
  key: string,
  load: () => Promise<Fetched<T>>,
  watches?: (change: CatalogChangedFrame) => boolean,
  reloadsOn?: (frame: ServerFrame) => boolean,
): Endpoint<T> {
  const { onCatalogChange, onFrame, reconnects } = useFactory()
  const [state, setState] = useState<{ key: string; result: Fetched<T> } | null>(null)
  const [generation, setGeneration] = useState(0)

  const loadRef = useRef(load)
  loadRef.current = load
  const watchRef = useRef(watches)
  watchRef.current = watches
  const frameRef = useRef(reloadsOn)
  frameRef.current = reloadsOn

  useEffect(() => {
    let current = true
    void loadRef.current().then((result) => { if (current) setState({ key, result }) })
    return () => { current = false }
  }, [key, generation, reconnects])

  useEffect(() => onCatalogChange((change) => {
    if (watchRef.current?.(change)) setGeneration((n) => n + 1)
  }), [onCatalogChange])

  // A generation bump and not a key change: the previous answer stays on screen while the reload
  // is in flight, which is what §4's preamble means by "never a layout that moves when data lands".
  useEffect(() => onFrame((frame) => {
    if (frameRef.current?.(frame)) setGeneration((n) => n + 1)
  }), [onFrame])

  const fresh = state && state.key === key ? state.result : undefined
  return { result: fresh, loading: fresh === undefined }
}
