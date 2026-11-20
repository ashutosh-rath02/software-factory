import { useCallback } from 'react'

import { EmptyBlock, Placeholder, ReconnectBlock } from '../components/Blocks'
import { WorkspaceTag, tintFor, tone } from '../components/Chips'
import { DeliveryBadge } from '../components/DeliveryBadge'
import { Markdown } from '../components/Markdown'
import { getRequirement } from '../services/http'
import { useEndpoint, useFactory } from '../services/factory'
import { useSubscription } from '../services/subscribe'
import { markTone, stateLabel } from '../vocabulary'
import { href } from '../routes'
import type { Group, RequirementChild } from '../types/api'

/**
 * 4.3 — one requirement, grouped by workspace.
 *
 * The grouping is the design's, and the reason is in its own copy: each workspace runs its own
 * stage sequence, SO PROGRESS IS ONLY COMPARABLE WITHIN A GROUP. Every `N/M` on this screen is
 * read against the sequence printed at the top of its own group, and the tool never puts a 4/8
 * and a 2/4 in one column to be compared.
 */
export function RequirementPage({ id }: { id: string }) {
  const { socketState } = useFactory()
  useSubscription({ requirements: true })

  const load = useCallback(() => getRequirement(id), [id])
  const { result, loading } = useEndpoint(`requirement:${id}`, load, () => true)

  if (loading) {
    return (
      <div className="surface" data-testid="surface">
        {socketState === 'lost' && <ReconnectBlock />}
        <Placeholder what="reading the requirement…" />
      </div>
    )
  }

  if (!result?.ok) {
    const path = (result && !result.ok
      && (result.error?.detail as { path?: string } | undefined)?.path) || ''
    return (
      <div className="surface" data-testid="surface">
        {socketState === 'lost' && <ReconnectBlock />}
        <EmptyBlock title="NO SUCH REQUIREMENT">
          Nothing with the id <code>{id}</code> was found{path ? <> under <code>{path}</code></> : null}.
        </EmptyBlock>
      </div>
    )
  }

  const req = result.data
  const headTone = markTone(req.state, req.delivery)

  return (
    <div className="surface" data-testid="surface">
      {socketState === 'lost' && <ReconnectBlock />}

      <div className="req-head" data-testid="requirement-header">
        <div className="req-head-row">
          <span className="req-head-id">{req.id}</span>
          <span className="req-head-title">{req.title}</span>
          <span className="bar-spacer" />
          <DeliveryBadge delivery={req.delivery} />
          {req.needs_you.count > 0 && (
            <span className="head-pill" data-testid="needs-pill"
              style={tone(headTone, tintFor(headTone))}>
              {req.needs_you.count} of {req.needs_you.total ?? req.needs_you.count} need you
            </span>
          )}
        </div>
        <div className="req-summary"><Markdown source={req.summary} /></div>
      </div>

      {(req.children ?? []).length > 0 && (
        <div className="groups" data-testid="child-requirements" data-dim={String(socketState === 'lost')}>
          <div className="group-head">
            <span className="group-name" data-testid="group-name">child requirements</span>
            <span className="group-rule" />
            <span className="group-count" data-testid="children-count">
              {req.children.length} {req.children.length === 1 ? 'requirement' : 'requirements'}
            </span>
          </div>
          <div className="child-list">
            {req.children.map((child) => <ChildRequirement key={child.id} child={child} />)}
          </div>
        </div>
      )}

      {req.groups.length === 0 && (req.children ?? []).length === 0
        ? <EmptyBlock title="NO TICKETS">No tickets reference this requirement yet.</EmptyBlock>
        : req.groups.length > 0 && (
          <div className="groups" data-testid="rows" data-dim={String(socketState === 'lost')}>
            {req.groups.map((group) => <WorkspaceGroup key={group.workspace} group={group} />)}
          </div>
        )}
    </div>
  )
}

function WorkspaceGroup({ group }: { group: Group }) {
  return (
    <div data-testid="workspace-group" data-sequence={group.stage_sequence.join(' → ')}>
      <div className="group-head">
        <span className="group-name" data-testid="group-name">{group.workspace}</span>
        <span className="group-rule" />
        <span className="group-count" data-testid="group-count">
          {group.count} {group.count === 1 ? 'ticket' : 'tickets'}
        </span>
      </div>
      <div className="group-box">
        {group.tickets.map((ticket) => {
          const rowTone = markTone(ticket.state, ticket.delivery)
          return (
            <a key={ticket.id} className="tkt-row" data-testid="ticket-row"
              href={href({ name: 'ticket', id: ticket.id })}>
              <span className="tkt-id">{ticket.id}</span>
              <span className="tkt-dot" style={tone(rowTone)} />
              <span className="tkt-title">{ticket.title}</span>
              <span className="tkt-state" style={tone(rowTone)}>{stateLabel(ticket.state)}</span>
              <DeliveryBadge delivery={ticket.delivery} />
              {/* N/M against THIS group's own sequence, and no other */}
              <span className="tkt-stage">{ticket.stage.index}/{ticket.stage.total}</span>
              <span className="tkt-lane">{ticket.lane ?? '—'}</span>
              <span className="tkt-cost">
                {ticket.cost_usd ? `$${ticket.cost_usd.toFixed(2)}` : '—'}
              </span>
            </a>
          )
        })}
      </div>
    </div>
  )
}

function ChildRequirement({ child }: { child: RequirementChild }) {
  return (
    <div data-testid="child-requirement" className="child-req">
      <a className="req-row child-head" data-testid="child-requirement-row"
        href={href({ name: 'requirement', id: child.id })}>
        <span className="req-id">{child.id}</span>
        <span className="req-title">{child.title}</span>
        <span className="tags"><WorkspaceTag name={child.repo} /></span>
        <DeliveryBadge delivery={child.delivery} />
        <span className="req-status">{child.status}</span>
        <span className="req-updated" data-testid="child-ticket-count">
          {child.ticket_count} {child.ticket_count === 1 ? 'ticket' : 'tickets'}
        </span>
      </a>
      <div className="child-groups">
        {child.tickets.map((group) => <WorkspaceGroup key={group.workspace} group={group} />)}
      </div>
    </div>
  )
}
