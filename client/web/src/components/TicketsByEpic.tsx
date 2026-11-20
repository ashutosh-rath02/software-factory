import { useCallback, useRef, useState } from 'react'
import { useEndpoint } from '../services/factory'
import { getTickets } from '../services/http'
import { useSettled } from '../services/settle'
import type { TicketIndexRow, TicketGroup } from '../types/api'

/**
 * `Dashboard · Tickets by epic` — `factory.pen`, frame `DAiio`.
 *
 * Six columns, two filter rows, grouped by the requirement each ticket names as parent. The
 * filters go to the SERVER: the counts beside each control are the server's facets, and a client
 * filtering its own rows would draw counts for one set and rows for another.
 */
const STAGES = ['SPEC', 'TEST', 'IMPL', 'CONVENTION', 'DESIGN', 'BUILD', 'GATE', 'REVIEW', 'QA',
                'MERGE', 'VERIFY'] as const

export function TicketsByEpic() {
  const [provenance, setProvenance] = useState('all')
  const [stage, setStage] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  // `query` is what the field DRAWS and `settled` is what the surface ASKS with. They were one
  // value, and `query` is a segment of the key below, so eleven characters were eleven full
  // sweeps of a catalog that has no index by design — including `q=3` and `q=32`, the two
  // broadest scans this field can produce, on the way to an id nobody had finished typing.
  const settled = useSettled(query)
  const load = useCallback(() => getTickets(provenance, stage, settled, page),
                           [provenance, stage, settled, page])
  const { result, loading } = useEndpoint(`tickets:${provenance}:${stage}:${settled}:${page}`, load,
                                          () => true)

  const data = result?.ok ? result.data : undefined
  const groups = data?.groups ?? []
  const facet = (name: 'provenance' | 'stage', value: string) =>
    data?.facets?.[name]?.find(f => f.value === value)?.count

  // How big the backlog is, as opposed to how much of it this query matched. `total` is the
  // MATCHED count, so a searching screen that read the repository's size off it would answer
  // `0 of 0` for a miss. The unfiltered size comes from the server too — the one answer whose
  // own `applied` says it filtered nothing — which the first load always is.
  const whole = useRef<number | undefined>(undefined)
  const a = data?.applied
  if (data && a && a.q === '' && a.provenance === 'all' && a.stage === '') whole.current = data.total
  const backlog = whole.current ?? data?.total ?? 0

  return (
    <section className="block" data-testid="tickets-by-epic">
      <header className="block-head">
        <h2>Tickets</h2>
        {data && <span className="pill">{data.total}</span>}
        <span className="block-note">grouped by the requirement each names as parent</span>
      </header>

      <div className="filters">
        <span className="filters-label">SEARCH</span>
        <input className="filter-input" data-testid="tickets-search" spellCheck={false}
               aria-label="search tickets" placeholder="id, title, owner or lane…"
               value={query} onChange={e => { setQuery(e.target.value); setPage(1) }} />
        {settled !== '' && data && (
          <span className="filters-note" data-testid="search-result">
            {data.total} of {backlog} · matches id, title, owner and lane
          </span>
        )}
      </div>

      <div className="filters" data-testid="tickets-filters">
        <span className="filters-label">PROVENANCE</span>
        {['all', 'not_started', 'running', 'stalled', 'unshipped', 'waiting', 'done'].map(v => (
          <button key={v} type="button"
                  className={'chip' + (provenance === v ? ' is-on' : '')}
                  aria-pressed={provenance === v}
                  onClick={() => { setProvenance(v); setPage(1) }}>
            {v.replace('_', ' ')}
            {facet('provenance', v) !== undefined && <em>{facet('provenance', v)}</em>}
          </button>
        ))}
      </div>
      <div className="filters">
        <span className="filters-label">STAGE</span>
        <button type="button" className={'chip' + (stage === '' ? ' is-on' : '')}
                aria-pressed={stage === ''} onClick={() => { setStage(''); setPage(1) }}>all</button>
        {STAGES.map(v => (
          <button key={v} type="button" className={'chip' + (stage === v ? ' is-on' : '')}
                  aria-pressed={stage === v} onClick={() => { setStage(v); setPage(1) }}>
            {v}{facet('stage', v) !== undefined && <em>{facet('stage', v)}</em>}
          </button>
        ))}
      </div>

      {loading && !data && <p className="block-empty">Reading the backlog…</p>}
      {data && groups.length === 0 && (
        <p className="block-empty" data-testid="tickets-empty">
          {settled === ''
            ? `No ticket matches this filter. The repository holds ${backlog} in all.`
            : `No ticket matches “${settled}”. The repository holds ${backlog} in all.`}
        </p>
      )}

      {groups.map(g => <EpicGroup key={g.parent} group={g} />)}

      {/* `E29` pages at 50. Without this, 279 of 329 tickets have no address on any screen — a
          group that says it holds 42 and shows none is only honest if the rest are REACHABLE. */}
      {data && data.pages > 1 && (
        <div className="pager" data-testid="pager">
          <button type="button" disabled={data.page <= 1}
                  onClick={() => setPage(p => Math.max(1, p - 1))}>← previous</button>
          <span className="pager-at">
            {data.from}–{data.to} of {data.total} · page {data.page} of {data.pages}
          </span>
          <button type="button" disabled={data.page >= data.pages}
                  onClick={() => setPage(p => Math.min(data.pages, p + 1))}>next →</button>
        </div>
      )}
    </section>
  )
}

function EpicGroup({ group }: { group: TicketGroup }) {
  // `E29` PAGES, and a group's `count` is its WHOLE size while `tickets` is only what fits on
  // this page. Eight of nine groups arrive with a count and no rows, and rendering a header plus
  // a column row for each drew eight empty tables claiming 8, 2, 3, 4, 42, 17, 4 and 42 tickets.
  // A group that says it holds 42 and shows none is a screen contradicting itself in one glance.
  const onThisPage = group.tickets.length
  return (
    <div className="epic" data-testid="epic-group">
      <div className="epic-head">
        <a className="epic-id" href={`#/requirements/${encodeURIComponent(group.parent)}`}>
          {group.parent}
        </a>
        <span className="epic-title">{group.title}</span>
        <span className="epic-counts">
          {group.count} · {group.merged} merged · {group.pushed} pushed
        </span>
      </div>
      {onThisPage === 0 ? (
        <p className="epic-elsewhere" data-testid="epic-elsewhere">
          {group.count} {group.count === 1 ? 'ticket' : 'tickets'}, none on this page.
        </p>
      ) : (
      <div className="scroll-x">
      <table className="tickets">
        <thead>
          <tr>
            <th>TICKET</th><th>TITLE</th><th>WORKSPACE</th><th>OWNER</th>
            <th>STAGE</th><th>DELIVERY</th><th>NEEDS YOU</th>
          </tr>
        </thead>
        <tbody>{group.tickets.map(t => <Row key={t.id} t={t} />)}</tbody>
      </table>
      </div>
      )}
    </div>
  )
}

function Row({ t }: { t: TicketIndexRow }) {
  const stage = t.stage?.name
    ? `${t.stage.name} ${t.stage.index}/${t.stage.total}`
    : `— 0/${t.stage?.total ?? 10}`
  const state = t.delivery?.state ?? 'unknown'
  return (
    <tr>
      <td><a className="mono" href={`#/tickets/${encodeURIComponent(t.id)}`}>{t.id}</a></td>
      <td className="ticket-title">{t.title}</td>
      <td className="mono dim">{t.workspace}</td>
      <td className="mono dim">{t.owner ? t.owner.name : <span className="dim">—</span>}</td>
      <td className="mono dim">{stage}</td>
      <td><span className={`state state-${state}`}>{state.replace('_', ' ')}</span></td>
      <td>{t.delivery?.needs_you
        ? <span className={`state state-${state}`}>{state.replace('_', ' ')}</span>
        : <span className="dim">—</span>}</td>
    </tr>
  )
}
