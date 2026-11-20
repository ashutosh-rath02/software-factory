import { useCallback, useRef, useState } from 'react'
import { useEndpoint } from '../services/factory'
import { getMetrics } from '../services/http'
import type { Metrics, StageMinutes } from '../types/api'

/**
 * `Metrics` — `factory.pen`, frame `Kj8y5`. SPEC.md §4.21.
 *
 * Eight numbers and a table of stages, each drawn beside the collection `E32` computed it over,
 * because a metric whose derivation is not on the screen beside it is a number somebody will
 * argue about instead of act on.
 *
 * THREE RULES DECIDE EVERYTHING HERE, AND ALL THREE ARE ABOUT COLLECTIONS.
 *
 * 1. The `n` beside a value is a FIELD OF THE SAME RESPONSE, never something this file derived.
 *    `first_pass_qa_rate`'s population is `tickets_with_verdict`, which `E32` serves precisely so
 *    that a surface cannot draw a rate over one collection beside a count over another
 *    (`WEB-FIX-091`).
 * 2. Where `E32` serves no population — `rounds_per_ticket` and `rework_after_build.share` are
 *    reduced over the tickets with a round in the window, and no field carries that size — the
 *    tile draws a dash and says so. Dividing a numerator by a share to recover one would be
 *    drawing a collection nobody served.
 * 3. `null` is not `0`. `null` means nothing was measured and draws `—`; `0` means measured and
 *    none qualified and draws `0%`. A screen that renders both the same way reports a failing
 *    process over a repository that has simply not started.
 *
 * And nothing here ticks. A metric is a reduction over a month, so the surface asks once per
 * window and `catalog.changed` is not subscribed.
 */

/** §1's pre-decision, and `E32`'s own default is the middle one. */
const WINDOWS = [7, 30, 90]
const DASH = '—'

const count = (v: number) => String(v)
const rate = (v: number | null) => (v === null ? DASH : `${Math.round(v * 100)}%`)
const minutes = (v: number | null) => (v === null ? DASH : `${v.toFixed(1)}m`)
const hours = (v: number | null) => (v === null ? DASH : `${v.toFixed(1)}h`)
/**
 * A count of rounds, to one decimal, with a whole number left whole.
 *
 * `E32` serves a double: `1.56818181818` rounds per ticket is exact and nobody can read it, and
 * `1.0` is a unit nobody measures. The design frame draws `2` and `mean 2.4`.
 */
const plain = (v: number | null) => (v === null ? DASH : String(Math.round(v * 10) / 10))
const population = (v: number | null) => `n ${v === null ? DASH : v}`

interface TileProps {
  wire: string
  value: string
  n: string
  second?: string
  note: string
}

function Tile({ wire, value, n, second, note }: TileProps) {
  return (
    <div className="metric-tile" data-testid={`metric-${wire}`}>
      <span className="metric-tile-label" data-testid="metric-label">{wire.toUpperCase()}</span>
      <span className="metric-tile-value" data-testid="metric-value">{value}</span>
      {second !== undefined
        && <span className="metric-tile-second" data-testid="metric-second">{second}</span>}
      <span className="metric-tile-n" data-testid="metric-n">{n}</span>
      <span className="metric-tile-note" data-testid="metric-note">{note}</span>
    </div>
  )
}

function tilesFor(data: Metrics): TileProps[] {
  return [
    { wire: 'runs', value: count(data.runs), n: population(data.runs),
      note: 'every run record whose started_iso is inside the window' },
    { wire: 'tickets_with_verdict', value: count(data.tickets_with_verdict),
      n: population(data.tickets_with_verdict),
      note: "tickets with at least one role run inside the window — the rate's own "
        + 'denominator' },
    { wire: 'first_pass_qa_rate', value: rate(data.first_pass_qa_rate),
      n: population(data.tickets_with_verdict),
      note: 'of those, the share whose first role run was their only one and whose verdict says '
        + 'pass' },
    { wire: 'rounds_per_ticket', value: plain(data.rounds_per_ticket.median),
      second: `mean ${plain(data.rounds_per_ticket.mean)}`, n: population(null),
      note: 'the runs nobody declared a role for, per ticket — E32 serves no count for this' },
    { wire: 'active_minutes', value: minutes(data.active_minutes.median),
      second: `mean ${minutes(data.active_minutes.mean)}`, n: population(data.active_minutes.n),
      note: "the sum of a run's turn durations, never wall clock" },
    { wire: 'spec_to_land_hours', value: hours(data.spec_to_land_hours.median),
      n: population(data.spec_to_land_hours.n),
      note: "the first frame naming the ticket's own first stage, to the commit that landed it" },
    { wire: 'escapes_in_window', value: count(data.escapes_in_window),
      n: population(data.escapes_in_window),
      note: "rows of the ledger's first table whose date cell falls inside the window" },
    { wire: 'rework_after_build', value: rate(data.rework_after_build.share),
      second: `${data.rework_after_build.tickets} `
        + `ticket${data.rework_after_build.tickets === 1 ? '' : 's'}`, n: population(null),
      note: 'tickets whose document was committed after their first round began — E32 serves '
        + 'no count for this' },
  ]
}

function StageRow({ row }: { row: StageMinutes }) {
  return (
    <tr data-testid={`metric-stage-${row.name}`}>
      <td className="mono">{row.name}</td>
      <td className="mono" data-testid="stage-median">{minutes(row.median)}</td>
      <td className="mono" data-testid="stage-mean">{minutes(row.mean)}</td>
      <td className="mono dim" data-testid="stage-n">{count(row.n)}</td>
    </tr>
  )
}

export function MetricsPage() {
  const [days, setDays] = useState(30)
  const load = useCallback(() => getMetrics(days), [days])
  const { result, loading } = useEndpoint(`metrics:${days}`, load)

  // The last answer that WAS an answer. `useEndpoint` keys on the window, so asking for a window
  // the server refuses clears the fresh result — and §4.21 says a refusal about the window
  // somebody asked for may not blank the numbers they already had. A ref rather than state: the
  // numbers on screen do not change when this is written, so a render caused by writing it would
  // be a render nothing asked for.
  const held = useRef<Metrics>()
  if (result?.ok) held.current = result.data
  const data = result?.ok ? result.data : held.current
  const refused = result && !result.ok ? result.error : undefined

  return (
    <div className="page" data-testid="metrics-page">
      <section className="block">
        <header className="block-head">
          <h2>Metrics</h2>
          {data && data.runs > 0
            && <span className="pill" data-testid="metrics-days">{data.window_days} days</span>}
          <span className="block-note">
            how the process itself is doing. every number is E32&rsquo;s, drawn beside the
            collection it was computed over.
          </span>
        </header>

        <div className="filters" data-testid="metrics-window">
          <span className="filters-label">WINDOW</span>
          {WINDOWS.map(w => (
            <button key={w} type="button" data-testid={`metrics-window-${w}`}
                    aria-pressed={w === days}
                    className={'chip' + (w === days ? ' is-on' : '')}
                    onClick={() => setDays(w)}>{w} days</button>
          ))}
          <span className="filters-note">the control asks E32 again; nothing here ticks</span>
        </div>

        {refused && (
          <p className="block-empty" data-testid="metrics-refused">{refused.message}</p>
        )}
        {loading && !data && !refused && <p className="block-empty">Computing…</p>}

        {data && data.runs === 0 && (
          <p className="block-empty" data-testid="metrics-empty">
            No run has been recorded in this repository.
          </p>
        )}

        {data && data.runs > 0 && (
          <>
            <div className="metric-tiles">
              {tilesFor(data).map(tile => <Tile key={tile.wire} {...tile} />)}
            </div>

            {/* No row and no empty state: `E32` gives a stage no row unless a run reported it,
                so an absent section IS the statement, and `active_minutes` says `n 0` beside it.
                A table drawn over no members would be a claim about a collection that has none. */}
            {data.stage_minutes.length > 0 && <>
            <header className="block-head metric-stages-head">
              <span className="metric-tile-label">STAGE_MINUTES</span>
              <span className="block-note">
                where a round&rsquo;s time goes — one row per stage a run reported, the
                repository&rsquo;s own names
              </span>
            </header>
            <div className="scroll-x">
              <table className="tickets metric-stages">
                <thead>
                  <tr><th>STAGE</th><th>MEDIAN</th><th>MEAN</th><th>N</th></tr>
                </thead>
                <tbody>
                  {data.stage_minutes.map(row => <StageRow key={row.name} row={row} />)}
                </tbody>
              </table>
            </div>
            </>}
          </>
        )}
      </section>
    </div>
  )
}
