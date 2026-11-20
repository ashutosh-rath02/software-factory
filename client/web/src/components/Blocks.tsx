import type { ReactNode } from 'react'

/**
 * The three blocks §4's preamble asks every surface for, drawn once.
 *
 * Their copy is the design's, verbatim where the design wrote it. A product decision expressed
 * as a sentence is still a product decision, and paraphrasing it is editing it.
 */

/**
 * The design's dashed rectangle, a mono caption and one sentence of what to do (:558-561).
 *
 * `testId` exists because the ticket screen carries two of these at once — the rail with no lanes
 * and the document that is not there — and a page with two `empty` boxes is a page no assertion
 * can name one of.
 */
export function EmptyBlock(
  { title, children, action, testId = 'empty' }:
  { title: string; children: ReactNode; testId?: string
    action?: { label: string; onClick: () => void } },
) {
  return (
    <div className="empty" data-testid={testId}>
      <div className="empty-title">{title}</div>
      <div className="empty-body">{children}</div>
      {action && (
        <button type="button" className="empty-action" onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  )
}

/**
 * Pinned at the top of the affected surface (:562-565).
 *
 * "Live counters freeze; nothing is cleared, nothing is refetched behind the user's back."
 * The copy below is the design's own and is not paraphrased — it is the promise the client keeps.
 */
export function ReconnectBlock() {
  return (
    <div className="reconnect" data-testid="reconnect">
      <div className="reconnect-title">SOCKET LOST · RECONNECTING</div>
      <div className="reconnect-body">
        The transcript below is what arrived before the drop. Nothing has been discarded;
        your draft is intact.
      </div>
    </div>
  )
}

/** A region that has never loaded. Never a spinner over a page. */
export function Placeholder({ what }: { what: string }) {
  return <div className="placeholder" data-testid="placeholder">{what}</div>
}
