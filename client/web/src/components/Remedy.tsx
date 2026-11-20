import { useCallback, useState } from 'react'

import { tone } from './Chips'
import { performRemedy, remedyRequest, type Fetched } from '../services/http'
import { destroysLine, remedyTone } from '../vocabulary'
import type { Remedy } from '../types/api'

/**
 * SPEC.md §3.6 — one remedy, rendered as the control that ends the state.
 *
 * WEB-FEAT-179 needs this to render anything at all, and `WEB-FEAT-176` owns widening it to every
 * surface and every verb. What is here is deliberately the narrow half: the verbs the two families
 * of refusal this ticket is about actually use, and the four rules that must not drift when the
 * table grows.
 *
 *   1. **The label is the SERVER's**, rendered verbatim. The client owns tone and placement, not
 *      text — otherwise the sentence a person reads depends on which bundle they loaded.
 *   2. **No remedy, no control.** Not a greyed button: a state that cannot be ended must not wear
 *      the costume of one that can, and a client that inferred a verb from `error.code` would be
 *      the drift `WEB-FEAT-176` exists to prevent.
 *   3. **A verb this client cannot turn into a call renders nothing either.** That is the
 *      version-skew case — a server ahead of this bundle degrades to today's behaviour.
 *   4. **`destroys` is said BEFORE it fires**, in §9.3's own nouns, and a control that costs
 *      something does not fire on the click that reveals what it costs.
 */
export function RemedyControl({ remedy, disabled, testId = 'remedy', announceCost = false,
                                addressFor, onRetry, onDone }: {
  remedy?: Remedy | null
  disabled?: boolean
  testId?: string
  /**
   * Say what it costs WITHOUT being clicked first — SPEC.md §4.18's rows, where the design draws
   * the cost line above the control on every row that has one.
   *
   * Rule 4 is about a click that reveals a price, and there is none to reveal here: the nouns are
   * already on the screen, so the control fires on the click that follows them.
   */
  announceCost?: boolean
  /**
   * `open_lane` is an ADDRESS, not a request (SPEC.md §9.2 — "§4.4 · E10"). The remedy's target
   * names the lane and the surface that shows the refusal is the one that knows which ticket that
   * lane belongs to, so the resolution is passed in rather than guessed here. Without it the verb
   * has no call and renders no control, which is rule 3 and not a special case.
   */
  addressFor?: (lane: string) => string | undefined
  /** `retry` is "re-issue the request that just failed, unchanged" — only the caller holds it. */
  onRetry?: () => void | Promise<void>
  onDone?: (answer: Fetched<unknown>) => void
}) {
  const [asked, setAsked] = useState(false)
  const [busy, setBusy] = useState(false)

  const target = (remedy?.target ?? {}) as Record<string, unknown>
  const address = remedy?.action === 'open_lane'
    ? addressFor?.(typeof target.lane === 'string' ? target.lane : '')
    : undefined

  const fire = useCallback(async () => {
    if (!remedy) return
    if (remedy.action === 'retry') { await onRetry?.(); return }
    const call = performRemedy(remedy)
    if (!call) return
    setBusy(true)
    try {
      onDone?.(await call)
    } finally {
      setBusy(false)
      setAsked(false)
    }
  }, [remedy, onRetry, onDone])

  if (!remedy) return null

  // Rule 3 — is there a call at all? Each verb answers for itself, and an unknown one answers no.
  const callable = remedy.action === 'open_lane'
    ? Boolean(address)
    : remedy.action === 'retry'
      ? Boolean(onRetry)
      : remedyRequest(remedy) !== null
  if (!callable) return null

  const destroys = remedy.destroys ?? []
  const costs = destroysLine(destroys)
  const style = tone(remedyTone(destroys))
  const announced = announceCost && destroys.length > 0
  const cost = (
    <span className="remedy-cost" data-testid={`${testId}-cost`}>
      {asked && remedy.confirm ? `${remedy.confirm} ` : ''}{costs}
    </span>
  )

  // An address is a link and not a button: it is where a person GOES, and a browser already knows
  // how to open one in a second tab.
  if (address) {
    const link = (
      <a className="empty-action" data-testid={testId} data-destroys={destroys.join(' ')}
        href={address} style={style}>{remedy.label}</a>
    )
    if (!announced) return link
    return (
      <span className="remedy" data-testid={`${testId}-armed`} style={style}>{cost}{link}</span>
    )
  }

  if (destroys.length > 0 && !asked && !announced) {
    return (
      <button type="button" className="empty-action" data-testid={testId}
        data-destroys={destroys.join(' ')} disabled={disabled || busy} style={style}
        onClick={() => setAsked(true)}>{remedy.label}</button>
    )
  }

  return (
    <span className="remedy" data-testid={`${testId}-armed`} style={style}>
      {(asked || announced) && cost}
      <button type="button" className="empty-action" data-testid={testId}
        data-destroys={destroys.join(' ')} disabled={disabled || busy} style={style}
        onClick={() => { void fire() }}>
        {busy ? 'Working…' : remedy.label}
      </button>
    </span>
  )
}
