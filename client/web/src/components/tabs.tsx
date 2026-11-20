import type { ComponentType } from 'react'

import { DiffTab, diffLabel } from './DiffTab'
import { ReportDocumentTab, TicketDocumentTab } from './DocTabs'
import { RunsTab, runsLabel } from './RunsTab'
import { SessionTab } from './SessionTab'
import type { Endpoint } from '../services/factory'
import type { RunsState } from '../services/runs'
import type { Ticket } from '../types/api'

/**
 * The tab registry — one table, `id → {label, component}`.
 *
 * The design draws the tab row as data (`Ticket.dc.html`) and this is that row's data. It is
 * a registry rather than a list of JSX in the page because of what is queued behind it: SESSION
 * (`WEB-FEAT-014`), RUNS (`016`), DIFF (§8 D3) and the permissions surface (`018`) each add one
 * tab, and they are meant to be built at the same time in different worktrees. Adding a tab has
 * to be ONE ROW in ONE FILE, or those four tickets are four conflicting edits to `TicketPage.tsx`.
 *
 * So the page maps this table and knows nothing else — not a label, not an id, not a component —
 * and `tab-registry.test.tsx::a_tab_is_one_row_and_one_component` greps the page to keep it that
 * way. The router does not know either: it carries the third path segment through as an opaque
 * string, and the FIRST ROW of this table is what an unknown or absent one falls back to.
 */

/**
 * What every tab is given, designed whole rather than grown one field per ticket.
 *
 * `ticket` is the page's ALREADY-READ E5, not an id to fetch again: two tabs both reading the
 * document would be two requests for one screen, and the second would race the first.
 */
export interface TabProps {
  ticketId: string
  ticket: Endpoint<Ticket>
  /** the lane this ticket is running in, when one is — `null` is the ordinary case */
  lane: string | null
  /**
   * The screen's run history — read once by the page (E7, then `M11`), because §4.5's header,
   * this row's `RUNS · N` and §4.10's table are three readers of one answer. A tab that wants
   * the runs takes them from here rather than asking again (`WEB-FIX-091`).
   */
  runs: RunsState
}

export interface TabDefinition {
  /**
   * What the tab says. A function when it counts something — the design's own row reads
   * `RUNS · 4`, and §8 D3's fifth tab reads `DIFF · N files`, so a label that could only be a
   * constant would send `016` back into the page to compute one.
   */
  label: string | ((props: TabProps) => string)
  component: ComponentType<TabProps>
}

export const TABS: Record<string, TabDefinition> = {
  session: { label: 'SESSION', component: SessionTab },
  ticket: { label: 'TICKET.MD', component: TicketDocumentTab },
  report: { label: 'REPORT.MD', component: ReportDocumentTab },
  runs: { label: runsLabel, component: RunsTab },
  diff: { label: diffLabel, component: DiffTab },
}

/** The declared order, which is the order they are drawn in. There is no second list. */
export function tabIds(): string[] {
  return Object.keys(TABS)
}

/** An id off the address bar, or the first row — a hash from an older build is not a blank page. */
export function resolveTab(id: string | undefined): string {
  return id && id in TABS ? id : Object.keys(TABS)[0]
}

export function labelOf(id: string, props: TabProps): string {
  const label = TABS[id].label
  return typeof label === 'function' ? label(props) : label
}
