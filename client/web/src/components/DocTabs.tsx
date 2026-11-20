import { useCallback } from 'react'
import { FileDashed } from '@phosphor-icons/react'

import { EmptyBlock, Placeholder } from './Blocks'
import { DocPane } from './DocPane'
import { tintFor, tone } from './Chips'
import { getReport, type Fetched } from '../services/http'
import { useEndpoint } from '../services/factory'
import { TONE, axisValue } from '../vocabulary'
import type { Report } from '../types/api'
import type { TabProps } from './tabs'

/**
 * 4.8 TICKET.MD and 4.9 REPORT.MD — two tabs, each a component in the registry.
 *
 * They were inside `TicketPage` until `WEB-FEAT-013`. They are out here now because the page must
 * not know what a tab is: four later tickets add one each, in parallel, and a page that named its
 * tabs would be four tickets editing one file.
 */

/** 4.8 — the ticket document, read only, at the path it came from. */
export function TicketDocumentTab({ ticketId, ticket }: TabProps) {
  if (ticket.loading) return <Placeholder what="reading the ticket…" />

  if (!ticket.result?.ok) {
    const path = (ticket.result && !ticket.result.ok
      && (ticket.result.error?.detail as { path?: string } | undefined)?.path) || ''
    return (
      <EmptyBlock title="NO TICKET DOCUMENT">
        <code>{ticketId}</code> is referenced but no document exists at <code>{path}</code>.
      </EmptyBlock>
    )
  }

  const doc = ticket.result.data
  return <DocPane path={doc.path} source={doc.body_markdown} />
}

/**
 * 4.9 — the delivery report, or the design's empty state.
 *
 * E6's 404 is the NORMAL case: it is what this empty state renders, and the client must not log
 * it as a failure. Nothing here writes to a console.
 *
 * `ticket` is the page's already-read E5, and `report.exists` off it keeps 4.9's loading promise
 * — "the tab header shows the report exists before its body arrives; no flicker between empty and
 * present." A ticket that says it has a report waits with a placeholder; one that says it has
 * none renders the empty state at once, and neither ever swaps to the other.
 */
export function ReportDocumentTab({ ticketId, ticket }: TabProps) {
  const load = useCallback(() => getReport(ticketId), [ticketId])
  const report = useEndpoint<Report>(`report:${ticketId}`, load,
    (change) => change.id === ticketId && change.kind === 'report')

  const expected = ticket.result?.ok ? ticket.result.data.report : undefined
  return <ReportBody state={report} expected={expected} />
}

function ReportBody({ state, expected }: {
  state: { result?: Fetched<Report>; loading: boolean }
  expected?: { exists: boolean }
}) {
  if (state.loading) {
    return expected?.exists === false ? <ReportEmpty /> : <Placeholder what="reading the report…" />
  }

  if (!state.result?.ok) {
    if (state.result && state.result.status !== 404) {
      return (
        <EmptyBlock title="THE REPORT COULD NOT BE READ">
          {state.result.error?.message ?? `The server answered ${state.result.status}.`}
        </EmptyBlock>
      )
    }
    return <ReportEmpty />
  }

  const report = state.result.data
  return (
    <DocPane path={report.path} source={report.body_markdown}
      banner={<EvidenceBanner verdict={report.evidence.verdict} runId={report.evidence.run_id} />} />
  )
}

/** The design's copy, verbatim — it is a product decision, and paraphrasing it is editing it. */
function ReportEmpty() {
  return (
    <div className="report-empty">
      <div className="report-empty-inner" data-testid="report-empty">
        <FileDashed size={26} />
        <div className="report-empty-title">NO DELIVERY REPORT</div>
        <div className="report-empty-body">
          The agent has not claimed this ticket done. A report appears here the moment it does —
          with or without the evidence to back it.
        </div>
      </div>
    </div>
  )
}

/**
 * The evidence verdict, above the report (SPEC.md §4.9).
 *
 * The design never drew a report body, so there is no drawing to copy. This is the design's own
 * state chip (Ticket.dc.html) in the evidence value's colour, taken from the shared
 * vocabulary table — which is what §3.2 already asks of it: "Two combinations are alarms and
 * carry a border; nothing else does."
 */
export function EvidenceBanner({ verdict, runId }: { verdict: string; runId?: string }) {
  const entry = axisValue('evidence', verdict)
  const alarm = verdict === 'claimed_unproven'
  const toneValue = entry?.tone ?? TONE.none
  return (
    <div className="evidence-banner" data-testid="evidence-banner" data-alarm={String(alarm)}
      style={tone(toneValue, tintFor(toneValue))}>
      <span className="chip-dot" style={tone(toneValue)} />
      {alarm ? 'claims done · cites no run' : `cites run ${runId ?? '—'}`}
    </div>
  )
}
