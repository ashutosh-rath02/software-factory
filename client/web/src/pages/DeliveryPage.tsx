import { useCallback } from 'react'
import { useEndpoint } from '../services/factory'
import { useLanes } from '../services/lanes'
import { getEscapes, getAllRuns, getHome } from '../services/http'
import type { RunIndexRow } from '../types/api'

/**
 * `Delivery` — the manager's surface, and it is NOT one of the design's frames.
 *
 * The design draws `Sprint`, `Release` and `People`. All three need state the tool would own for
 * the repository it is pointed at — sprint dates, budgets, release tags, a roster — and
 * `MODEL.md` §1 is *one developer, one workstation, one repository, one tool process*, with §4:
 * inverting an exclusion is a model change and never a ticket.
 *
 * What a manager actually tracks is DORA's four and flow: deployment frequency, lead time, change
 * failure rate, time to recovery, WIP against its limit, and what is blocked and for how long.
 * **This tool already measures all of them under different names** — `ESCAPES.md` IS the change
 * failure rate, QA rounds ARE time to recovery, seats ARE the WIP limit — and it measures one
 * thing no human-team dashboard can: the dollar cost of every attempt.
 *
 * FOUR SOURCES, DELIBERATELY. §4.18's dashboard reads ONE endpoint because its blocks are three
 * views of ONE question and their counts must agree. These four blocks are four DIFFERENT
 * questions, so each names the endpoint it came from and no number here is summed across two.
 */
export function DeliveryPage() {
  const { lanes, resources, loaded } = useLanes()
  const home = useEndpoint('delivery-home', useCallback(() => getHome(), []), () => true)
  const esc = useEndpoint('delivery-escapes', useCallback(() => getEscapes(), []), () => true)
  const runs = useEndpoint('delivery-runs', useCallback(() => getAllRuns(), []), () => false,
                           f => f.m === 'run')

  const h = home.result?.ok ? home.result.data : undefined
  const e = esc.result?.ok ? esc.result.data : undefined
  const r = runs.result?.ok ? runs.result.data : undefined

  const seats = Math.max(lanes.length, 3)
  const queued = lanes.filter(l => l.state?.process === 'queued')
  const blocked = [
    ...(h?.needs_you.rows ?? []).map(row => ({
      id: row.ticket_id, title: row.title, why: row.detail ?? row.clauses.join(' · '), age: undefined as number | undefined,
    })),
    ...(h?.unpushed.rows ?? []).map(row => ({
      id: row.ticket_id, title: row.title,
      why: `${row.commits} ${row.commits === 1 ? 'commit' : 'commits'} nobody outside can see`,
      age: row.age_s,
    })),
  ]

  return (
    <div className="page" data-testid="delivery-page">
      <div className="deliv">
        <Block title="Flow" note="work in flight against the limit that is physical, not a policy"
               source="E9 · E30">
          <Metric label="in flight" value={loaded ? `${lanes.length}/${seats}` : '—'}
                  sub="lanes / seats" tone={lanes.length >= seats ? 'st-crash' : 'st-run'} />
          <Metric label="queued on a resource" value={String(queued.length)}
                  sub={queued.length ? queued.map(l => l.lane).join(', ') : 'nothing waiting'} />
          <Metric label="held resources" value={String(resources.length)}
                  sub="an exclusive resource is queued for, never shared" />
        </Block>

        <Block title="Blocked" note="visible and actionable — a blocked item still costs attention"
               source="E30">
          {blocked.length === 0
            ? <p className="block-empty">Nothing is blocked. Everything committed has left this machine.</p>
            : (
              <table className="tickets">
                <thead><tr><th>TICKET</th><th>WHY</th><th>AGE</th></tr></thead>
                <tbody>
                  {blocked.map((b, i) => (
                    <tr key={`${b.id}-${i}`}>
                      <td><a className="mono" href={`#/tickets/${encodeURIComponent(b.id)}`}>{b.id}</a></td>
                      <td>{b.why}</td>
                      <td className="mono dim">{b.age === undefined ? '—' : since(b.age)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </Block>

        <Block title="Quality" note="a gate that said yes when it should have said no" source="E28 · E31">
          <Metric label="escapes, all time" value={e ? String(e.total) : '—'}
                  sub="every one is a check that passed wrongly" tone="st-crash" />
          <Metric label="last 30 days" value={e ? String(recent(e.rows, 30)) : '—'}
                  sub="the change-failure signal, from the ledger's own dates" />
          <Metric label="QA rounds per ticket" value={r ? rework(r.runs) : '—'}
                  sub="rounds it takes to go from FAIL to pass — time to recovery" />
        </Block>

        <Block title="Spend" note="the dollar cost of every attempt — no human-team board has this"
               source="E31">
          <Metric label="total" value={r ? money(r.runs.reduce((n, x) => n + (x.cost_usd ?? 0), 0)) : '—'}
                  sub={r ? `over ${r.total} runs` : ''} />
          <Metric label="median run" value={r ? money(median(r.runs.map(x => x.cost_usd ?? 0))) : '—'}
                  sub="half of runs cost less than this" />
          <Metric label="crashed or orphaned" value={r ? String(wasted(r.runs)) : '—'}
                  sub="runs that produced nothing and were paid for anyway" tone="st-silent" />
        </Block>
      </div>
    </div>
  )
}

function Block({ title, note, source, children }:
  { title: string; note: string; source: string; children: React.ReactNode }) {
  return (
    <section className="block" data-testid={`deliv-${title.toLowerCase()}`}>
      <header className="block-head">
        <h2>{title}</h2>
        <span className="block-note">{note}</span>
        <span className="deliv-source mono">{source}</span>
      </header>
      <div className="deliv-body">{children}</div>
    </section>
  )
}

function Metric({ label, value, sub, tone }:
  { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="metric">
      <div className={'metric-value' + (tone ? ` ${tone}` : '')}>{value}</div>
      <div className="metric-label">{label}</div>
      {sub && <div className="metric-sub">{sub}</div>}
    </div>
  )
}

/** Rows whose first cell parses as a date within `days`. The ledger's own column, not a guess. */
function recent(rows: { cells: { text: string }[] }[], days: number): number {
  const cut = Date.now() - days * 86400_000
  return rows.filter(row => {
    const t = Date.parse(row.cells[0]?.text ?? '')
    return !Number.isNaN(t) && t >= cut
  }).length
}

/** QA runs per ticket that had any — one round is healthy, five is `SRV-FIX-351`. */
function rework(runs: RunIndexRow[]): string {
  const per = new Map<string, number>()
  for (const r of runs) {
    if (r.kind !== 'qa') continue
    per.set(r.ticket_id, (per.get(r.ticket_id) ?? 0) + 1)
  }
  if (per.size === 0) return '—'
  const total = [...per.values()].reduce((a, b) => a + b, 0)
  return (total / per.size).toFixed(1)
}

const wasted = (runs: RunIndexRow[]) =>
  runs.filter(r => r.outcome === 'crashed' || r.outcome === 'orphaned').length

function median(xs: number[]): number {
  const s = xs.filter(x => x > 0).sort((a, b) => a - b)
  return s.length === 0 ? 0 : s[Math.floor(s.length / 2)]
}

const money = (v: number) => (v > 0 ? `$${v.toFixed(2)}` : '—')

function since(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  if (d >= 1) return `${d}d`
  const h = Math.floor(seconds / 3600)
  return h >= 1 ? `${h}h` : `${Math.floor(seconds / 60)}m`
}
