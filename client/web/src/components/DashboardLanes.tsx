import type { ReactNode } from 'react'

import { laneStageLabel, laneTimeLeft } from './LaneCard'
import { useFactory } from '../services/factory'
import { useLanes } from '../services/lanes'
import type { Lane } from '../types/api'

/**
 * `Dashboard · Lanes` — `factory.pen`, frame `mTLsb`.
 *
 * One card per seat, including the FREE ones. The design draws `lane/03` as an empty seat with
 * `Worktree clean, no branch checked out.` rather than leaving it out, and that is the point of
 * the block: a seat nobody can see is a seat nobody knows is available.
 *
 * IT FETCHES NOTHING. `LanesProvider` already reads `E9` shell-wide for §4.1's counter, so this
 * block reads that instead — `dashboard.test.tsx::test_the_screen_makes_exactly_one_request` says
 * in as many words that *a dashboard that fetched `E29` or `E9` for itself fails here*, and it is
 * right: the surface answers one question from one read, and a block that opens its own connection
 * makes the counts on one screen come from two moments.
 */
export function DashboardLanes() {
  // The manifest's sequence is the only one this surface has read. `laneStageLabel` matches BY
  // LENGTH and falls back to `4/10` rather than indexing a sequence that is the wrong shape —
  // SPEC.md §4.3: progress is comparable only inside one sequence, and `4/8` is never `2/4`.
  const { manifest, socketState } = useFactory()
  const sequence = manifest?.stages
  const { lanes, loaded, leftAnchors } = useLanes()
  const live = socketState === 'open'
  if (!loaded) return null

  const used = lanes.length
  const seats = Math.max(used, 3)
  const free = Math.max(0, seats - used)

  return (
    <section className="block" data-testid="dashboard-lanes">
      <header className="block-head">
        <h2>Lanes</h2>
        <span className="pill">{used} of {seats} seats</span>
        <span className="block-note">
          a seat is a worktree. an exclusive resource is queued for, never shared.
        </span>
      </header>
      <div className="lane-grid">
        {lanes.map(l => <LaneCardBlock key={l.lane} lane={l} sequence={sequence}
          left={laneTimeLeft(l, leftAnchors[l.lane] ?? null, live)} />)}
        {Array.from({ length: free }, (_, i) => <FreeSeat key={`free-${i}`} />)}
      </div>
    </section>
  )
}

function LaneCardBlock({ lane, sequence, left }: { lane: Lane; sequence?: string[]; left: ReactNode }) {
  // `state` is an OBJECT — `{process, motion, evidence}` — not a string. `process` is what the
  // design prints on the chip (`running`, `queued`); `motion` is whether a turn is in flight.
  const process = lane.state?.process ?? 'unknown'
  const stage = `${laneStageLabel(lane, sequence)} ${lane.stage.index}/${lane.stage.total}`
  const producing = lane.state?.motion?.value === 'producing'
  return (
    <article className="lane-card" data-testid="lane-card">
      <div className="lane-card-top">
        <span className="mono lane-name">{lane.lane}</span>
        <span className={`state state-${process}`}>{process}</span>
        <span className="role">{lane.role?.toUpperCase()}</span>
      </div>
      <a className="mono lane-ticket" href={`#/tickets/${encodeURIComponent(lane.ticket_id)}`}>
        {lane.ticket_id}
      </a>
      <p className="lane-detail">
        {producing ? 'a turn is in flight' : (lane.state?.evidence || detailOf(lane))}
      </p>
      {/* The design's own placement (`mTLsb`): the STATE cell's second line, between the state
          line and the `IMPL 4/10` foot, and absent entirely on a lane that has said nothing —
          FAC-EPIC-373. The server's string verbatim; the `›` is a CSS `::before`. */}
      {lane.last_words
        ? <p className="lane-words" data-testid="lane-words">{lane.last_words.text}</p>
        : null}
      <div className="lane-card-foot">
        <span className="mono dim">{stage}</span>
        <span className="mono dim">{elapsed(lane.elapsed_s)}</span>
        {left ? <span className="mono dim" data-testid="lane-remaining">{left}</span> : null}
        <span className="mono dim">{money(lane.cost_usd)}</span>
      </div>
    </article>
  )
}

/** The design's third card. A seat is drawn whether or not anything holds it. */
function FreeSeat() {
  return (
    <article className="lane-card is-free" data-testid="lane-free">
      <div className="lane-card-top">
        <span className="mono lane-name dim">free</span>
        <span className="state state-not_started">FREE</span>
      </div>
      <p className="lane-detail">Worktree clean, no branch checked out.</p>
    </article>
  )
}

function detailOf(lane: Lane): string {
  const waiting = lane.detail && typeof lane.detail === 'object'
    ? (lane.detail as Record<string, unknown>).waiting_for
    : undefined
  return typeof waiting === 'string' ? `waiting for ${waiting}` : (lane.state?.process ?? '')
}

/** `3m 12s` — the design's own shape. Never a bare number of seconds. */
function elapsed(s: number | undefined): string {
  if (!s && s !== 0) return '—'
  const m = Math.floor(s / 60)
  return m ? `${m}m ${String(Math.floor(s % 60)).padStart(2, '0')}s` : `${Math.floor(s)}s`
}

function money(v: number | undefined): string {
  return typeof v === 'number' && v > 0 ? `$${v.toFixed(2)}` : '—'
}
