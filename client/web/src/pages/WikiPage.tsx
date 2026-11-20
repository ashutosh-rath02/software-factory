import { useCallback, useState } from 'react'
import { useEndpoint } from '../services/factory'
import { getWiki } from '../services/http'
import { Markdown } from '../components/Markdown'
import type { WikiSection } from '../types/api'

/**
 * `Wiki` — `factory.pen`, frame `AqvHA`.
 *
 * The documents this repository governs itself by, read off disk on every request. The design
 * groups them under THE MODEL / THE PROCESS / THE CONTRACTS; those groups are the CLIENT's, and
 * they are a presentation choice rather than a claim about the repository — a page the tool has
 * never heard of appears under `Other`, whole, rather than being dropped for not fitting.
 */
const GROUPS: Array<{ label: string; ids: string[] }> = [
  { label: 'THE MODEL', ids: ['MODEL', 'SPEC', 'ARCHITECTURE'] },
  { label: 'THE PROCESS', ids: ['WORKFLOW', 'ESCAPES', 'KNOWN-LIMITATIONS', 'DOCS'] },
  { label: 'THE CONTRACTS', ids: ['http.schema', 'socket.schema', 'manifest.schema'] },
]

export function WikiPage() {
  const load = useCallback(() => getWiki(), [])
  const { result, loading } = useEndpoint('wiki', load, () => true)
  const [open, setOpen] = useState<string>()

  const data = result?.ok ? result.data : undefined
  const refused = result && !result.ok ? result : undefined
  const sections = data?.sections ?? []
  const claimed = new Set(GROUPS.flatMap(g => g.ids))
  const groups = [
    ...GROUPS.map(g => ({ label: g.label, rows: g.ids
      .map(id => sections.find(s => s.id === id))
      .filter((s): s is WikiSection => Boolean(s)) })),
    { label: 'OTHER', rows: sections.filter(s => !claimed.has(s.id)) },
  ].filter(g => g.rows.length > 0)

  const shown = sections.find(s => s.id === open)

  return (
    <div className="page" data-testid="wiki-page">
      <section className="block">
        <header className="block-head">
          <h2>Wiki</h2>
          {data && <span className="pill">{data.total}</span>}
          <span className="block-note">
            the documents this repository governs itself by, read off disk on every request
          </span>
        </header>

        {refused && (
          <p className="block-empty" data-testid="wiki-refused">
            This repository declares no pages, so there is nothing to read. The manifest is the
            door, and it is a person's to edit.
          </p>
        )}
        {loading && !data && !refused && <p className="block-empty">Reading the pages…</p>}
        {data?.warnings?.map(w => (
          <p key={w.path ?? w.code} className="block-empty" data-testid="wiki-warning">
            The manifest declares <code>{w.path}</code> and nothing is there.
          </p>
        ))}
        {data && sections.length === 0 && !data.warnings?.length && (
          <p className="block-empty" data-testid="wiki-empty">
            No page here yet. A document appears the moment one is written.
          </p>
        )}

        <div className="wiki">
          <div className="wiki-index">
            {groups.map(g => (
              <div key={g.label} className="wiki-group">
                <h3>{g.label}</h3>
                {g.rows.map(s => (
                  <button key={s.id} type="button"
                          className={'wiki-row' + (open === s.id ? ' is-on' : '')}
                          aria-pressed={open === s.id}
                          onClick={() => setOpen(open === s.id ? undefined : s.id)}>
                    <span className="wiki-name">{s.path.split('/').pop()}</span>
                    <span className="wiki-bytes">{kb(s.bytes)}</span>
                    {s.parse_error && <span className="wiki-broken">frontmatter</span>}
                  </button>
                ))}
              </div>
            ))}
          </div>

          {shown && (
            <article className="wiki-page" data-testid="wiki-open">
              <h3>{shown.title}</h3>
              <p className="wiki-path mono dim">{shown.path} · {kb(shown.bytes)}</p>
              {shown.parse_error && (
                <p className="wiki-broken-note">
                  Its frontmatter would not parse — {shown.parse_error}. The page itself is below,
                  whole.
                </p>
              )}
              {shown.front.length > 0 && (
                <dl className="wiki-front">
                  {shown.front.map(f => (
                    <div key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></div>
                  ))}
                </dl>
              )}
              <Markdown source={shown.body_markdown} />
            </article>
          )}
        </div>
      </section>
    </div>
  )
}

/** The design prints a size beside each page — `E27`'s own `bytes`, so the cost is visible. */
function kb(bytes: number): string {
  return bytes >= 1024 ? `${Math.round(bytes / 1024)} kB` : `${bytes} B`
}
