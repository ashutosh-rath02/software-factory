import type { ReactNode } from 'react'

import { Markdown } from './Markdown'

/**
 * A document, rendered, read-only, with its path (Ticket.dc.html).
 *
 * The two words matter as much as the path does: this surface has no control that could write
 * to the file, and saying so is cheaper than making a reader discover it. SPEC.md §1.2 —
 * requirements, tickets and reports are written by an editor or by an agent inside a lane, and
 * the state model only survives if there is exactly one writer.
 */
export function DocPane({ path, banner, source }:
{ path: string; banner?: ReactNode; source: string }) {
  return (
    <div className="doc">
      <div className="doc-inner">
        <div className="doc-path" data-testid="doc-path">{path} · read only</div>
        {banner}
        <Markdown source={source} />
      </div>
    </div>
  )
}
