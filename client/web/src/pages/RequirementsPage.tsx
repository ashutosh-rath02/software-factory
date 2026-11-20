import { useCallback, useState } from 'react'

import { EmptyBlock, Placeholder, ReconnectBlock } from '../components/Blocks'
import { NeedsPill, WorkspaceTag, mark as markStyle, tone } from '../components/Chips'
import { DeliveryBadge } from '../components/DeliveryBadge'
import { getRequirements } from '../services/http'
import { useEndpoint, useFactory } from '../services/factory'
import { useSubscription } from '../services/subscribe'
import { FILTERS, STANDALONE, markTone, type FilterKey } from '../vocabulary'
import { href } from '../routes'
import type { RequirementRow } from '../types/api'

/**
 * 4.2 — the requirements list.
 *
 * Filtering and search are the SERVER's (E3 takes both), so the count line and the rows come
 * from one answer and cannot disagree. The count line is `total` off that answer and is never
 * `rows.length`: computing it here is how a list and its counter start telling two stories.
 */
export function RequirementsPage() {
  const { socketState, manifest } = useFactory()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<FilterKey>('all')
  const [open, setOpen] = useState<Record<string, boolean>>({})

  useSubscription({ requirements: true })

  const load = useCallback(() => getRequirements(query, filter), [query, filter])
  const { result, loading } = useEndpoint(
    `requirements?q=${query}&filter=${filter}`,
    load,
    // every document kind reaches this list: an epic is a row, and a ticket or a report changes
    // a row's needs-you count and its evidence
    () => true,
  )

  // `docs` is the implicit repository's and is absent once a manifest declares `repos`,
  // where there is no single epics path to name (SRV-FIX-181).
  const epicsPath = manifest?.docs?.epics ?? 'the manifest’s epics path'
  const rows: RequirementRow[] = result?.ok ? result.data.requirements : []
  const byId = new Map(rows.map((r) => [r.id, r]))
  const topLevel = rows.filter((r) => !r.parent || !byId.has(r.parent))
  const childrenOf = (r: RequirementRow): RequirementRow[] =>
    (r.children ?? []).map((id) => byId.get(id)).filter((c): c is RequirementRow => Boolean(c))
  const filtering = query.trim() !== '' || filter !== 'all'

  // A manifest that did not load is the reason the catalogue is empty, and the empty block's own
  // sentence would contradict it.
  const manifestFailed = Boolean(
    result?.ok && result.data.warnings?.some((w) => w.code === 'invalid_manifest'))

  function renderTree(row: RequirementRow, depth: number): JSX.Element {
    const mark = markTone(row.state, row.delivery)
    // The one row on this screen no document backs (WEB-FEAT-041): the server appends it
    // last, out of the tickets no requirement claims, and it has no status to print. Its
    // size goes in that column instead, because a group whose size is invisible is a group
    // nobody opens.
    const standalone = row.id === STANDALONE
    const kids = childrenOf(row)
    const isOpen = Boolean(open[row.id])
    return (
      <div key={row.id} data-testid="requirement-node">
        <a className="req-row" data-testid="requirement-row"
          data-standalone={standalone ? 'true' : undefined}
          data-depth={depth}
          data-parent={depth > 0 ? row.parent : undefined}
          href={href({ name: 'requirement', id: row.id })}
          style={{ ...markStyle(row.needs_you.count > 0 ? mark : 'transparent'),
            paddingLeft: `calc(var(--space-6) + ${depth * 22}px)` }}>
          {kids.length > 0
            ? (
              <button type="button" className="req-toggle" data-testid="requirement-toggle"
                aria-expanded={isOpen} aria-label={`${isOpen ? 'hide' : 'show'} the children of ${row.id}`}
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen({ ...open, [row.id]: !isOpen }) }}>
                {isOpen ? '▾' : '▸'}
              </button>
            )
            : <span className="req-toggle req-toggle-none" aria-hidden="true" />}
          <span className="req-id">{row.id}</span>
          <span className="dot" style={tone(mark)} />
          <span className="req-title">{row.title}</span>
          {kids.length > 0 && !isOpen && (
            <span className="req-children" data-testid="requirement-children-count">
              {kids.length} {kids.length === 1 ? 'child' : 'children'} · {row.ticket_count} {row.ticket_count === 1 ? 'ticket' : 'tickets'}
            </span>
          )}
          <DeliveryBadge delivery={row.delivery} />
          <NeedsPill label={row.needs_you.label} toneValue={mark} testId="row-needs-pill" />
          <span className="tags">
            {row.workspaces.map((w) => <WorkspaceTag key={w} name={w} />)}
          </span>
          <span className="req-status">
            {standalone
              ? `${row.ticket_count} ${row.ticket_count === 1 ? 'ticket' : 'tickets'}`
              : row.status}
          </span>
          <span className="req-updated">{ago(row.updated_iso)}</span>
        </a>
        {isOpen && kids.map((kid) => renderTree(kid, depth + 1))}
      </div>
    )
  }

  return (
    <div className="surface" data-testid="surface">
      {socketState === 'lost' && <ReconnectBlock />}

      <div className="filters">
        <input className="filter-input" placeholder="filter requirements…" spellCheck={false}
          value={query} onChange={(e) => setQuery(e.target.value)} aria-label="filter requirements" />
        {FILTERS.map((f) => (
          <button key={f.key} type="button" className="filter-chip"
            aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>{f.label}</button>
        ))}
        <span className="bar-spacer" />
        <span className="count-line" data-testid="count-line">
          {result?.ok ? `${result.data.total} requirements · ${result.data.in_flight} in flight` : ''}
        </span>
      </div>

      {loading && <Placeholder what="reading the repository…" />}

      {/* SRV-FIX-071 — why the list below is empty, when the answer is not "nothing is filed".
          E2's refusal reached `GET /api/manifest` and no surface a person opens; this page then
          explained an empty catalogue with `a directory that is not there`, confidently and
          wrongly, while the real reason was an unplugged board. `invalid_manifest` is the code
          that separates THE REASON the list is empty from a note about a populated one, so the
          block below is suppressed for it rather than printed beside a contradiction. */}
      {result?.ok && result.data.warnings?.map((warning) => (
        <div className="perms-warning" data-testid="catalogue-warning" key={warning.message}>
          {warning.message}
          {warning.pointer ? ` — ${warning.pointer}` : (warning.path ? ` — ${warning.path}` : '')}
        </div>
      ))}

      {result?.ok && rows.length === 0 && !manifestFailed && (filtering
        ? (
          <EmptyBlock title="NOTHING MATCHES"
            action={{ label: 'clear the filter', onClick: () => { setQuery(''); setFilter('all') } }}>
            No requirement matches this filter. Clear the filter to see everything again.
          </EmptyBlock>
        )
        : (
          <EmptyBlock title="NO REQUIREMENTS">
            No requirements found under <code>{epicsPath}</code>. That path comes from this
            repository’s manifest, and a directory that is not there is the usual reason.
          </EmptyBlock>
        ))}

      {result && !result.ok && (
        <EmptyBlock title="THE LIST COULD NOT BE READ">
          {result.error?.message ?? `The server answered ${result.status}.`}
        </EmptyBlock>
      )}

      {rows.length > 0 && (
        <div className="rows" data-testid="rows" data-dim={String(socketState === 'lost')}>
          {topLevel.map((row) => renderTree(row, 0))}
        </div>
      )}
    </div>
  )
}

/** The design's last-updated column is a duration, not a date (Dashboard.dc.html). */
export function ago(iso: string): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return '—'
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}
