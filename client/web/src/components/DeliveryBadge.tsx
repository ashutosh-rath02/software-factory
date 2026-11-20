import { deliveryValue } from '../vocabulary'
import { tintFor, tone } from './Chips'
import type { Delivery } from '../types/api'

/**
 * SPEC.md §3.5 — what actually happened, on the row.
 *
 * The document's `status:` is a claim written by a session that may not have survived to write
 * it truthfully. Three of the first four QA runs on this repository left one behind that was
 * not true, and in the worst case it said `pass`. So this renders the DERIVED state, and where
 * the document disagrees it says so in the same badge — that disagreement is the most valuable
 * thing on the row, and a badge that showed only one of the two would be either a lie or a
 * mystery.
 *
 * `stalled` and `unshipped` carry the border, because they are the two that need a person
 * (§3.3). `done` does not: a border on the ordinary outcome is a border nobody reads.
 *
 * A state with no entry in the vocabulary renders nothing at all — `not_started` has nothing to
 * report, and `unknown` is stated once for the whole screen in the top bar rather than repeated
 * on every row. A component cannot invent a state quietly here, which is the same rule the
 * axis chips follow.
 */
export function DeliveryBadge({ delivery }: { delivery?: Delivery }) {
  const value = deliveryValue(delivery?.state)
  if (!delivery || !value) return null

  const title = [delivery.detail, delivery.reason].filter(Boolean).join(' · ')
  return (
    <span className="delivery-badge" data-testid="delivery-badge"
      data-state={value.value}
      data-alarm={String(value.alarm)}
      data-disagrees={String(delivery.disagrees)}
      title={title || undefined}
      style={tone(value.tone, tintFor(value.tone))}>
      <span className="chip-dot" style={tone(value.tone)} />
      {value.label}
      {delivery.disagrees && delivery.claimed && (
        <span className="delivery-claim" data-testid="delivery-disagreement">
          · document says {delivery.claimed}
        </span>
      )}
    </span>
  )
}
