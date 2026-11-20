import { useCallback } from 'react'

import { Clock } from './Clock'
import { EmptyBlock, Placeholder } from './Blocks'
import { tone } from './Chips'
import { TABS, type TabProps } from './tabs'
import { getReport } from '../services/http'
import { useEndpoint, useFactory } from '../services/factory'
import { clearReplay, runTotals, selectRun, useReplay } from '../services/runs'
import { TONE, axisValue, formatCost, formatElapsed, type AxisName } from '../vocabulary'
import { href } from '../routes'
import type { Report, RunRow } from '../types/api'

/**
 * 4.10 RUNS — `RUN · STARTED · OUTCOME · STAGE · DUR · COST`, newest first.
 *
 * The design's own grid (Ticket.dc.html). Selecting a row loads that run's transcript
 * into the SESSION tab, read-only: reading a finished run back is why `agent` keeps every session
 * instead of overwriting it, and a table you cannot open is a list of things that happened to you.
 */

/**
 * The six outcomes §4.10 names, from the five words the wire carries.
 *
 * `completed` is one word and two facts, and §3.4 is the difference: a report that cites this run
 * makes it `finished · proof`, and a report that cites nothing makes it `finished · no proof`.
 * Flattening them would hide the one state this whole product exists to surface.
 *
 * Every colour is a lookup into the vocabulary table the sheet renders from — no new palette, and
 * a word that table never declared takes no colour rather than a wrong one.
 */
const OUTCOMES: Record<string, { label: string; axis: AxisName; value: string }> = {
  running: { label: 'running', axis: 'process', value: 'running' },
  'ended cleanly': { label: 'ended cleanly', axis: 'process', value: 'finished' },
  crashed: { label: 'crashed mid-turn', axis: 'process', value: 'crashed' },
  'killed by you': { label: 'killed by you', axis: 'process', value: 'killed' },
}

const PROVEN = { label: 'finished · proof', axis: 'evidence' as AxisName, value: 'claimed_proven' }
const UNPROVEN = { label: 'finished · no proof', axis: 'evidence' as AxisName,
                   value: 'claimed_unproven' }

export function outcomeOf(row: RunRow, provenRunId?: string): { label: string; tone: string } {
  const entry = row.outcome === 'completed'
    ? (provenRunId && provenRunId === row.run_id ? PROVEN : UNPROVEN)
    : OUTCOMES[row.outcome]
  if (!entry) return { label: row.outcome, tone: TONE.none }
  return { label: entry.label, tone: axisValue(entry.axis, entry.value)?.tone ?? TONE.none }
}

/**
 * `RUNS · 4` — counted off the same rows this tab draws, so the label and the table cannot say
 * two different things about one ticket (`WEB-FIX-091`). The page has already read them; the tab
 * still costs no request.
 */
export function runsLabel(props: TabProps): string {
  const doc = props.ticket?.result?.ok ? props.ticket.result.data : undefined
  const totals = runTotals(doc, props.runs)
  return totals ? `RUNS · ${totals.count}` : 'RUNS'
}

const COLUMNS = ['RUN', 'STARTED', 'OUTCOME', 'STAGE', 'DUR', 'COST']

/** The wall-clock time a run began, which is how a person finds the one they remember. */
function startedAt(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return '—'
  return [at.getHours(), at.getMinutes(), at.getSeconds()]
    .map((part) => String(part).padStart(2, '0')).join(':')
}

export function RunsTab({ ticketId, ticket, runs }: TabProps) {
  const { socketState } = useFactory()
  const { rows, loading, stamps, known } = runs
  const replay = useReplay()

  // §3.4 — which run, if any, the delivery report cites. E6's 404 is the ordinary answer.
  const load = useCallback(() => getReport(ticketId), [ticketId])
  const report = useEndpoint<Report>(`report:${ticketId}`, load,
    (change) => change.id === ticketId && change.kind === 'report')
  const proven = report.result?.ok ? report.result.data.evidence.run_id : undefined

  const stages = ticket?.result?.ok ? ticket.result.data.stages : []
  const live = socketState === 'open'

  const open = (row: RunRow) => {
    // The run that is still going is not a replay: it is the live session, and the SESSION tab
    // already shows it. A "replay" of it would be a second copy that stops updating.
    if (row.outcome === 'running') clearReplay()
    else {
      selectRun({ ticketId, runId: row.run_id, n: row.n, startedIso: row.started_iso,
                  outcome: row.outcome, proven: proven === row.run_id })
    }
    // Read on the SESSION tab, which `WEB-FEAT-014` owns. Until its row is in the registry there
    // is nowhere to send anybody, and a link to a tab that does not exist is worse than none.
    if ('session' in TABS) location.hash = href({ name: 'ticket', id: ticketId, tab: 'session' })
  }

  if (loading) return <Placeholder what="reading the run history…" />

  // §4.10 gives the empty sentence to a ticket with no runs and to a cleaned `.factory/runs/`. It
  // does not give it to a read that FAILED, and `known` is the fact that tells the two apart —
  // added for the header in round 1 and owed to this pane as much (QA round 2, F3). Whatever rows
  // are held here came from `M11` and are only what has happened since; saying so is what keeps
  // the count above the pane from contradicting the list inside it.
  const unread = !known && (
    <EmptyBlock title="RUN HISTORY UNREAD" testId="runs-unread">
      The run history could not be read. {rows.length > 0
        ? 'Below is only what has started since.'
        : 'This is not the same as a ticket with no runs.'}
    </EmptyBlock>
  )

  if (rows.length === 0) {
    return (
      <div className="runs-pane">
        {unread || (
          <EmptyBlock title="NO RUNS" testId="runs-empty">
            No runs recorded for this ticket.
          </EmptyBlock>
        )}
      </div>
    )
  }

  return (
    <div className="runs-pane">
      {unread}
      <div className="runs" data-testid="runs-table" role="table">
        {COLUMNS.map((name) => <div key={name} className="runs-head">{name}</div>)}

        {rows.map((row) => {
          const outcome = outcomeOf(row, proven)
          const running = row.outcome === 'running' && !row.ended_iso
          return (
            <div key={row.run_id} className="run-row" role="row" data-testid="run-row"
              tabIndex={0} aria-selected={replay?.runId === row.run_id}
              onClick={() => open(row)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') open(row) }}>
              <div className="run-cell run-n"><span data-testid="run-n">#{row.n}</span>
                {row.agent && <small className="run-agent">{row.agent} · {row.model}</small>}
              </div>
              <div className="run-cell run-dim" data-testid="run-started">
                {startedAt(row.started_iso)}
              </div>
              <div className="run-cell" data-testid="run-outcome" style={tone(outcome.tone)}>
                {outcome.label}
              </div>
              <div className="run-cell run-dim" data-testid="run-stage">
                {stages?.[row.stage] ?? '—'}
              </div>
              <div className="run-cell">
                <Clock testId="run-dur" baseS={row.duration_s}
                  sinceMs={running ? stamps[row.run_id] ?? null : null}
                  running={live} format={formatElapsed} />
              </div>
              <div className="run-cell" data-testid="run-cost"
                title={row.cost_known === false ? 'This agent does not report dollar cost' : undefined}>
                {row.cost_known === false ? '—' : formatCost(row.cost_usd)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
