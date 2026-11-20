import { useCallback } from 'react'
import { useEndpoint, useFactory } from '../services/factory'
import type { ManifestRepo } from '../types/api'
import { getTickets } from '../services/http'

/**
 * `Orchard` — `factory.pen`, frame `I55XQ8`.
 *
 * A monorepo is a SET of repositories — `manifest.schema.json`'s `repos` — each carrying its own
 * documents. The tool has modelled that since the beginning and no surface ever showed it: a
 * manifest declaring four repositories looked exactly like one declaring a single repository.
 *
 * Counts come from `E29` grouped by workspace, and workspaces name their repo in `E2`. The design
 * also draws a `role: tool` row for a vendored factory that is behind its own pin — **that row is
 * not here**, because nothing in this tool compares a pinned SHA against a working one, and a
 * `1 commit behind` nobody computed is a claim, not a status.
 */
export function OrchardPage() {
  const { manifest } = useFactory()
  const load = useCallback(() => getTickets('all', ''), [])
  const { result } = useEndpoint('orchard', load, () => true)
  const tickets = result?.ok ? result.data : undefined

  // `E2` types its manifest sub-objects as `Record<string, unknown>` — the manifest is the
  // target repository's document and the tool declines to model its shape twice. The generated
  // `ManifestRepo` is the schema's word for one entry, so the cast is to the contract and not
  // around it.
  const repos = (manifest?.repos ?? {}) as Record<string, ManifestRepo>
  const workspaces = (manifest?.workspaces ?? {}) as Record<string, { repo?: string }>

  // `E29` PAGES. `groups` is the page and `total` is every ticket matching the filter, so a
  // count summed from the rows is the page size wearing a repository's name — 50 where the
  // repository holds 323. The repo total is `total`; a per-WORKSPACE count would need a facet
  // `E29` does not serve, so none is shown rather than one that is a page deep.
  const total = tickets?.total

  const names = Object.keys(repos)

  return (
    <div className="page" data-testid="orchard-page">
      <section className="block">
        <header className="block-head">
          <h2>Orchard</h2>
          {names.length > 0 && <span className="pill">{names.length}</span>}
          <span className="block-note">
            a monorepo is a SET of repositories — each carries its own documents
          </span>
        </header>

        {names.length === 0 && (
          <p className="block-empty" data-testid="orchard-empty">
            The manifest declares no repositories yet.
          </p>
        )}

        <div className="lane-grid">
          {names.map(name => {
            const repo = repos[name]
            const mine = Object.entries(workspaces).filter(([, w]) => (w.repo ?? name) === name)
            return (
              <article key={name} className="lane-card" data-testid="orchard-repo">
                <div className="lane-card-top">
                  <span className="mono lane-name">{name}</span>
                  <span className="role">{repo.path === '.' ? 'ROOT' : repo.path}</span>
                </div>
                <p className="lane-detail">
                  {total === undefined
                    ? 'counting…'
                    : `${total} ${total === 1 ? 'ticket' : 'tickets'}`}
                </p>
                <div className="orchard-ws">
                  {mine.map(([ws]) => (
                    <span key={ws} className="chip">{ws}</span>
                  ))}
                </div>
                <div className="lane-card-foot">
                  <span className="mono dim">{repo.docs.tickets}</span>
                </div>
              </article>
            )
          })}
        </div>
      </section>
    </div>
  )
}
