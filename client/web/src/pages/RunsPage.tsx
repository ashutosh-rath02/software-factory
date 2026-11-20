import { useCallback } from 'react'
import { useEndpoint } from '../services/factory'
import { getAllRuns } from '../services/http'
import type { RunIndexRow } from '../types/api'

/**
 * `Runs` — `factory.pen`, frame `WnVfm`.
 *
 * Every run this tool started, across every ticket. The OUTCOME column prints the schema's own
 * six words and no others: `running`, `ended cleanly`, `killed by you`, `completed`, `crashed`,
 * `orphaned`. A surface that paraphrases an outcome is a surface inventing a seventh.
 */
export function RunsPage() {
  const load = useCallback(() => getAllRuns(), [])
  const { result, loading } = useEndpoint('runs', load, () => false, f => f.m === 'run')
  const data = result?.ok ? result.data : undefined

  return (
    <div className="page" data-testid="runs-page">
      <section className="block">
        <header className="block-head">
          <h2>Runs</h2>
          {data && <span className="pill">{data.total}</span>}
          <span className="block-note">
            every run this tool started, across every ticket — the outcomes are the schema's own
            six words
          </span>
        </header>

        {loading && !data && <p className="block-empty">Reading the run history…</p>}
        {data && data.runs.length === 0 && (
          <p className="block-empty" data-testid="runs-empty">
            No agent has run in this repository yet. A run appears here the moment one starts.
          </p>
        )}
        {data && data.runs.length > 0 && (
          <table className="tickets">
            <thead>
              <tr>
                <th>STARTED</th><th>TICKET</th><th>LANE</th>
                <th>STAGE</th><th>DUR</th><th>COST</th><th>OUTCOME</th>
              </tr>
            </thead>
            <tbody>{data.runs.map(r => <RunRow key={r.run_id} r={r} />)}</tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function RunRow({ r }: { r: RunIndexRow }) {
  return (
    <tr>
      <td className="mono dim">{clock(r.started_iso)}</td>
      <td>
        <a className="mono" href={`#/tickets/${encodeURIComponent(r.ticket_id)}`}>{r.ticket_id}</a>
        <span className="run-title">{r.ticket_title}</span>
      </td>
      <td className="mono dim">{r.lane}</td>
      <td className="mono dim">{r.kind.toUpperCase()}</td>
      <td className="mono dim">{duration(r.duration_s)}</td>
      <td className="mono dim">{money(r.cost_usd)}</td>
      <td><span className={`state state-${outcomeClass(r.outcome)}`}>{r.outcome}</span></td>
    </tr>
  )
}

/** `18:42:07` — the design prints a wall clock, not a date. */
function clock(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toISOString().slice(11, 19)
}

/** `3m 12s`, the design's own shape. */
function duration(s: number): string {
  if (!s && s !== 0) return '—'
  const m = Math.floor(s / 60)
  return `${m}m ${String(Math.floor(s % 60)).padStart(2, '0')}s`
}

const money = (v: number) => (typeof v === 'number' && v > 0 ? `$${v.toFixed(2)}` : '—')

/** The six outcomes map onto the design's state colours; nothing else is coloured. */
function outcomeClass(o: RunIndexRow['outcome']): string {
  switch (o) {
    case 'running': return 'running'
    case 'ended cleanly': case 'completed': return 'done'
    case 'crashed': return 'crashed'
    case 'killed by you': return 'silent'
    case 'orphaned': return 'queued'
    default: return 'unknown'
  }
}
