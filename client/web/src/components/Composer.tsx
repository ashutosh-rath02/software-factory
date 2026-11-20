import { useCallback, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ClockCountdown, PaperPlaneRight, Stop } from '@phosphor-icons/react'

import { tone } from './Chips'
import { TONE, formatCost } from '../vocabulary'

/**
 * SPEC.md §4.7 — the composer. One control, three jobs, one position (§8 D1).
 *
 * The three jobs are `Send` idle, `Stop` mid-turn in the crash colour, and `Send again` when
 * something is held — and they are one `<button>`, always the same element in the same seat, so
 * that a person whose hand is already there never has to look for it. There is no second button:
 * `End session` and `Kill` live in the lane menu in the header, because interrupt is frequent and
 * belongs under the hand while a kill next to `Send` is how a session dies by accident.
 *
 * **THE DRAFT.** It lives in this component's own `useState` and this component is declared at
 * module level, so an arriving entry re-renders it and never remounts it: React keeps the state,
 * never writes the DOM value, and the caret does not move. That is the whole of the fix for the
 * failure that killed the prototype — a poll that rebuilt the DOM every eight seconds and ate
 * half-typed messages — and `composer.test.tsx::a_hundred_entries_do_not_touch_the_draft` is the
 * case that was watched failing before it was made to pass.
 */

export type Mode = 'send' | 'stop' | 'send-again'

export interface ComposerProps {
  /** the lane a message would go to, or null: a ticket nobody opened a lane for has no session */
  lane: string | null
  /**
   * The agent is PRODUCING output (M2's `motion`), which is what turns `Send` into `Stop`.
   *
   * Not merely "mid-turn". The design's own demonstration makes `Stop` appear while it is
   * streaming and leaves `Send` in place while it is mid-turn and SILENT — and it has to, because
   * a mid-turn message is exactly the thing that comes back `held` (E14), and a composer that
   * offered no way to send one would make the whole held-and-interrupt mechanism unreachable.
   */
  producing: boolean
  /** how many messages the server says are queued behind the current turn */
  heldCount: number
  sessionCost: number
  /** the socket is open — a send whose outcome cannot be observed is worse than no send at all */
  live: boolean
  /**
   * E14, or E18 on the first Send into a lane with no live session (WEB-FEAT-045).
   *
   * Resolves when the server has answered — `false` if it refused, so the words go back in the box
   * — and it resolves before the interrupt goes out, so `Send again` cannot reorder the queue it is
   * about to deliver.
   *
   * A STRING is a refusal with its own sentence, and it exists because the two endpoints refuse for
   * different reasons: E14's `409` has one cause, while E18's says either "a run is already live in
   * this lane" (naming which) or "this lane is waiting for the board". One generic line for all
   * three would tell a person the one thing they already know — that it did not go — and none of
   * the thing they need, which is what to do next.
   */
  onSend(text: string): boolean | string | void | Promise<boolean | string | void>
  onInterrupt(): void
  /**
   * WEB-FEAT-044 — the way OUT of the empty state, rendered by whoever knows the ticket.
   *
   * A node and not a callback: the control is `ClaimControl`, it owns its own disabled state and
   * its own refusal line, and there is exactly one of it in the client. The composer's job here is
   * the seat, which is the same job it does for its own one button.
   */
  claim?: ReactNode
  /**
   * WEB-FIX-061 — a Send from here STARTS a run, so an empty box is a whole instruction.
   *
   * The lane has no live session (`vocabulary.hasLiveSession` is false: crashed, finished, killed,
   * not_started), which is what makes `onSend` E18 instead of E14 — and E18 with no prompt runs the
   * repository's own words (`agent.start_prompt_file`, SRV-FIX-048), so the agent begins on the
   * ticket's own content. `WEB-FEAT-045` §1 decided that and `onSend` was written for it; the
   * `if (!text) return` below made it unreachable, and a person looking at a crashed lane pressed
   * Send and got silence.
   *
   * The guard was CORRECT when it was written, before Send could mean start. It became wrong
   * without being edited, so this flag is what tells it which world it is in rather than a
   * condition removed.
   */
  startsARun?: boolean
}

/**
 * What the one control means, right now — WEB-FIX-061 QA round 2, F1.
 *
 * ONE function decides both what the button DOES and what the composer SAYS, because this ticket
 * has now been failed three times for the two disagreeing. Round 1 shipped `startsARun` beside
 * `modeOf` and the pair drifted immediately: a **queued** lane was told Send would start a run and
 * got `409 resource_busy`; a **crashed lane still holding a message** kept `heldCount > 0`, so
 * `modeOf` said `send-again` and `act()` interrupted a dead process while the hint went on saying
 * *"Nothing is running — Send starts a run"*. Each time the fix was one more condition on
 * `startsARun`, and each time the next axis was still open.
 *
 * So `startsARun` is an INPUT here and never a second opinion. A lane with no live session cannot
 * be producing and cannot be interrupted — there is no process to stop, and nothing an interrupt
 * could deliver — so it takes precedence over both.
 *
 * The design's own precedence (Ticket.dc.html) is unchanged for a lane that IS live:
 * while output is streaming the control is `Stop`, whatever is queued behind it; `Send again` is
 * what it says once the stream has stopped and something is still waiting.
 */
export function modeOf(producing: boolean, heldCount: number, startsARun = false): Mode {
  if (startsARun) return 'send'
  if (producing) return 'stop'
  return heldCount > 0 ? 'send-again' : 'send'
}

/** The sentence under the box, derived from the SAME call. One decision, said twice. */
export function hintFor(mode: Mode, startsARun: boolean): string {
  if (mode === 'stop') return 'Agent is producing — Stop halts this turn, nothing is reverted'
  if (startsARun) return 'Nothing is running — Send starts a run · an empty box runs the ticket'
  return 'Enter sends · Shift+Enter newline'
}

const LABELS: Record<Mode, string> = {
  send: 'Send', stop: 'Stop', 'send-again': 'Send again',
}

export function Composer(
  { lane, producing, heldCount, sessionCost, live, onSend, onInterrupt, claim,
    startsARun = false }: ComposerProps,
) {
  const [draft, setDraft] = useState('')
  const [refused, setRefused] = useState('')
  /**
   * One write at a time — WEB-FIX-061 QA.
   *
   * A press with text in the box clears it, so a second press sends nothing and the double is
   * invisible. The EMPTY-box press this ticket added clears nothing and changes nothing, so the
   * control looks exactly as it did before it was pressed, and a second press inside the round
   * trip started a SECOND run. `WEB-FEAT-044` already learned this for claims
   * (`test_a_second_claim_while_one_is_in_flight_does_not_create_two_lanes`) and the lesson did
   * not travel one component across.
   *
   * State and not a ref, because the button has to LOOK unavailable: a guard that silently drops
   * the second press is a guard the person cannot see, and they press again.
   */
  const [sending, setSending] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)

  const mode = modeOf(producing, heldCount, startsARun)

  /**
   * Post the text, and GIVE IT BACK if the server would not take it.
   *
   * The box empties at once, because a person who pressed `Enter` has finished with those words
   * and waiting on a round trip to clear the box is how a second copy gets typed. But a message
   * the server refused — no live session, `409 wrong_state` — is a message that did not happen,
   * and a client that emptied the box anyway would be losing a draft on the one path where the
   * user is least able to notice. So it is restored, and only if nothing has been typed since:
   * putting old words in front of new ones would be worse than losing them.
   */
  const post = useCallback(async (text: string): Promise<boolean> => {
    setDraft('')
    setRefused('')
    setSending(true)
    try {
      const accepted = await onSend(text)
      if (accepted !== false && typeof accepted !== 'string') return true
      setDraft((now) => (now === '' ? text : now))
      setRefused(typeof accepted === 'string' && accepted
        ? accepted
        : 'not sent — the lane refused it. Your message is still here.')
      return false
    } finally {
      setSending(false)
    }
  }, [onSend])

  const act = useCallback(() => {
    if (!live || !lane || sending) return
    const text = draft.trim()
    if (mode === 'stop') { onInterrupt(); return }
    if (mode === 'send-again') {
      // Everything held is delivered by the interrupt, IN ORDER — including this one, which is
      // posted first so that it joins the queue behind whatever is already in it rather than in
      // front. The interrupt waits for the message to be accepted rather than racing it: two
      // writes fired together arrive in whichever order the server happens to finish, and the
      // one thing this control promises is that nothing jumps the queue.
      if (!text) { onInterrupt(); return }
      void post(text).then((accepted) => { if (accepted) onInterrupt() })
      return
    }
    // An empty box is nothing to SAY and a whole instruction to START: E18 with no prompt runs
    // the repository's own words. Mid-session it is still nothing, which is what the guard was
    // put here for (WEB-FIX-061).
    if (!text && !startsARun) return
    void post(text)
  }, [live, lane, draft, mode, post, onInterrupt, startsARun, sending])

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>) => {
    // The design's own rule: Enter sends, Shift+Enter is a newline.
    if (e.key !== 'Enter' || e.shiftKey) return
    e.preventDefault()
    act()
  }, [act])

  // No session: the composer is present and says what to do, and offers nothing to type into —
  // there is no other end for a message to reach. WEB-FEAT-044 made "what to do" something a
  // person can DO from here: the sentence stayed, because it is still the reason the box is gone.
  if (!lane) {
    return (
      <div className="composer" data-testid="composer">
        <div className="composer-idle">Start a lane to send a message.</div>
        {claim}
      </div>
    )
  }

  const rows = Math.min(8, Math.max(2, draft.split('\n').length))
  const stopping = mode === 'stop'

  return (
    <div className="composer" data-testid="composer">
      {refused && (
        <div className="composer-refused" data-testid="composer-refused"
          style={tone(TONE.crash)}>{refused}</div>
      )}
      {heldCount > 0 && (
        <div className="composer-held" data-testid="composer-held" style={tone(TONE.silent)}>
          <ClockCountdown size={12} />
          {heldCount} message held · send again to interrupt the turn
        </div>
      )}

      <div className="composer-body">
        <textarea ref={box} className="composer-draft" data-testid="composer-draft"
          value={draft} rows={rows} spellCheck={false} placeholder="Message the agent…"
          onChange={(e) => setDraft(e.target.value)} onKeyDown={onKeyDown} />

        <div className="composer-foot">
          <span className="composer-hint" data-testid="composer-hint">
            {hintFor(mode, startsARun)}
          </span>
          <span style={{ flex: 1 }} />
          <span className="composer-cost" data-testid="composer-cost">
            session {formatCost(sessionCost)}
          </span>
          {/* ONE button. Its label and its colour change; its identity and its seat do not.

              SPEC.md §4.7 and §8 D1 are literal about the seat — `Stop` appears "exactly where
              your hand already is" — and a box that shrink-wraps its label is not that: `Stop` is
              three pixels narrower than `Send`, and against a right-hand edge those three pixels
              move the control every time the word changes. So the seat is as wide as the widest
              word it can ever hold. These are the button's own box, drawn with the button's own
              class so the two cannot drift, stacked in one grid cell, never shown, never reachable
              and never read aloud. Adding a fourth label needs nothing here. */}
          <div className="composer-seat" data-testid="composer-seat">
            {Object.values(LABELS).map((word) => (
              <span key={word} className="composer-button composer-ghost" aria-hidden="true">
                {word}
                <PaperPlaneRight size={13} />
              </span>
            ))}
            <button type="button" className="composer-button" data-testid="composer-button"
              data-mode={mode} data-tone={stopping ? 'crash' : 'accent'}
              disabled={!live || sending}
              style={tone(stopping ? TONE.crash : 'var(--color-accent)')}
              onClick={act}>
              {LABELS[mode]}
              {stopping ? <Stop size={13} /> : <PaperPlaneRight size={13} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
