import { useCallback, useEffect } from 'react'

import { Placeholder, ReconnectBlock } from '../components/Blocks'
import { LaneRail } from '../components/LaneRail'
import { TicketHeader } from '../components/TicketHeader'
import { TABS, labelOf, resolveTab, tabIds, type TabProps } from '../components/tabs'
import { getTicket } from '../services/http'
import { useEndpoint, useFactory } from '../services/factory'
import { useLanes } from '../services/lanes'
import { runTotals, useRunHistory } from '../services/runs'
import { useSubscription } from '../services/subscribe'
import { href } from '../routes'
import type { Ticket } from '../types/api'

/**
 * The ticket screen — SPEC.md §4.4's rail, §4.5's header, and a tab row that is a registry.
 *
 * **This file names no tab.** Not a label, not an id, not a component: it maps
 * `components/tabs.tsx` and renders whatever is in it. That is the seam four later tickets run
 * against in parallel, and `tab-registry.test.tsx` greps this file to keep it true — the moment a
 * tab's name appears here, adding the fifth means editing the file the other four are editing.
 */
export function TicketPage({ id, tab, laneParam, onParent }: {
  id: string
  /** whatever the address bar's third segment held; the registry decides what it means */
  tab: string | undefined
  /** `?lane=` — which of this ticket's lanes to show; absent or stale falls back to the first */
  laneParam?: string
  onParent(parent: string | undefined): void
}) {
  const { socketState, manifest } = useFactory()
  const { lanes, anchors, loaded } = useLanes()
  useSubscription({ ticket: id })

  const loadTicket = useCallback(() => getTicket(id), [id])
  const ticket = useEndpoint<Ticket>(`ticket:${id}`, loadTicket,
    (change) => change.id === id && change.kind === 'ticket')

  const doc = ticket.result?.ok ? ticket.result.data : undefined
  const parent = doc?.parent
  useEffect(() => { onParent(parent) }, [parent, onParent])

  // E5's `lane` is a document's answer and the lane set is the running one's; the running one
  // wins, because a lane exists whether or not a document ever mentioned it.
  //
  // A ticket can hold more than one lane (a `qa` lane beside its `run` lane), so the address says
  // which one this screen is showing; without it every rail card linked to the same screen.
  const mine = lanes.filter((held) => held.ticket_id === id)
  const row = mine.find((held) => held.lane === laneParam) ?? mine[0]
  const live = socketState === 'open'

  const active = resolveTab(tab)
  // E7 for the whole screen rather than for one tab: §4.5's header states a count of runs above
  // every tab, and a count read from somewhere other than the rows is a count that can disagree
  // with them — which is what `WEB-FIX-091` found over a live lane. This file still names no tab.
  const lane = row?.lane ?? doc?.lane ?? null
  // Every lane running this ticket, not just the one the header names: a run in the `qa` lane is
  // a run on this ticket (`WEB-FIX-091` round 3, QA F1).
  const runs = useRunHistory(id, mine.map((h) => h.lane))
  const props: TabProps = { ticketId: id, ticket, lane, runs }
  const Active = TABS[active].component

  return (
    <div className="ticket" data-testid="surface">
      {socketState === 'lost' && <ReconnectBlock />}

      <div className="ticket-body">
        <LaneRail lanes={lanes} anchors={anchors} loaded={loaded} ticketId={id}
          workspace={doc?.workspace ?? ''} runCount={runTotals(doc, runs)?.count}
          stages={doc?.stages ?? []} manifestStages={manifest?.stages ?? []} live={live}
          showClaim={active !== 'session'} selectedLane={row?.lane} tab={tab} />

        <div className="ticket-main">
          {doc
            ? <TicketHeader ticket={doc} lane={row} live={live} runs={runs} />
            : <Placeholder what="reading the ticket…" />}

          <div className="tabs" role="tablist">
            {tabIds().map((tabId) => (
              <a key={tabId} className="tab" role="tab" data-tab={tabId}
                aria-selected={tabId === active}
                href={href({ name: 'ticket', id, tab: tabId, lane: laneParam })}>{labelOf(tabId, props)}</a>
            ))}
          </div>

          <Active {...props} />
        </div>
      </div>
    </div>
  )
}
