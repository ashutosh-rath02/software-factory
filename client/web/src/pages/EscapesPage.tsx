import { useCallback } from 'react'
import { useEndpoint } from '../services/factory'
import { getEscapes } from '../services/http'
import type { EscapeRow } from '../types/api'

/**
 * `Escapes` — `factory.pen`, frame `BmOwy`.
 *
 * The ledger, read off disk on every request. Nothing here is sorted, ranked, dated or scored:
 * the file's order is the repository's statement about its own history, and a surface that
 * reordered it would be making a claim of its own.
 *
 * The columns are the FILE's, whatever they are. This surface does not know what `Escaped from`
 * means and does not need to — `E28` sends the header row verbatim, and a cell carries
 * `ticket_id` when the server resolved one, so the link is the server's answer and not a regex
 * run in the browser.
 */
export function EscapesPage() {
  const load = useCallback(() => getEscapes(), [])
  const { result, loading } = useEndpoint('escapes', load, () => true)
  const data = result?.ok ? result.data : undefined
  const refused = result && !result.ok ? result : undefined

  return (
    <div className="page" data-testid="escapes-page">
      <section className="block">
        <header className="block-head">
          <h2>Escapes</h2>
          {data && <span className="pill">{data.total}</span>}
          <span className="block-note">
            every gate that said yes when it should have said no. when two rows share a cause,
            that cause is the next ticket.
          </span>
        </header>

        {refused && (
          <p className="block-empty" data-testid="escapes-refused">
            This repository declares no ledger, so there is nothing to read. The manifest is
            the door, and it is a person's to edit.
          </p>
        )}
        {loading && !data && !refused && <p className="block-empty">Reading the ledger…</p>}
        {data?.warnings?.map(w => (
          <p key={w.path ?? w.code} className="block-empty" data-testid="escapes-warning">
            The manifest declares <code>{w.path}</code> and nothing is there.
          </p>
        ))}
        {data && data.rows.length === 0 && !data.warnings?.length && (
          <p className="block-empty" data-testid="escapes-empty">
            Nothing has got past a gate yet. That is a state, not a mistake.
          </p>
        )}

        {data && data.rows.length > 0 && (
          <div className="scroll-x">
          <table className="tickets escapes">
            <thead>
              <tr>{data.columns.map(c => <th key={c}>{c.toUpperCase()}</th>)}</tr>
            </thead>
            <tbody>{data.rows.map((r, i) => <Row key={i} row={r} />)}</tbody>
          </table>
          </div>
        )}
      </section>
    </div>
  )
}

function Row({ row }: { row: EscapeRow }) {
  return (
    <tr>
      {row.cells.map((c, i) => (
        <td key={i} className={i === 0 ? 'mono dim' : undefined}>
          {/* The id LINKS the cell; it does not replace it. A cell whose text is a sentence about
              an escape and whose `ticket_id` is the ticket it escaped from must show the
              sentence — swapping one for the other loses the row's whole content. */}
          {c.ticket_id
            ? <a href={`#/tickets/${encodeURIComponent(c.ticket_id)}`}
                 className={c.text === c.ticket_id ? 'mono' : 'escape-link'}>{c.text}</a>
            : <span className="escape-cell">{c.text}</span>}
        </td>
      ))}
    </tr>
  )
}
