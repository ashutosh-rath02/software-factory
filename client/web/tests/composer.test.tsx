import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent } from '@testing-library/react'

import {
  calls, callsTo, lane, mountApp, settle, ticketDoc, ticketRoutes, type Routes,
} from './helpers'
import {
  crashed, deliver, docLane, idle, live, LANE, measure, running, silent, snapshot,
} from './session'

/**
 * SPEC.md §4.7 — the composer. One control, three jobs, one position (§8 D1).
 *
 * And the mandatory negative, which is the reason this whole architecture exists: the prototype
 * rebuilt the DOM every eight seconds and ate half-typed messages. A green suite that never put a
 * draft in the box and then made a hundred entries arrive would be a suite that could not have
 * caught it.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const MESSAGES = `/api/lanes/${LANE}/messages`
const INTERRUPT = `/api/lanes/${LANE}/interrupt`

/** The two writes §4.7 makes, with the bodies SPEC.md E14 and E15 declare. */
function writes(over: Routes = {}): Routes {
  return {
    [MESSAGES]: { body: { status: 'delivered', held_count: 0, entry_seq: 9 } },
    [INTERRUPT]: { body: { interrupted: true, delivered: 0 } },
    ...over,
  }
}

async function mount(rows: Array<Record<string, unknown>>, over: Routes = {}) {
  const app = await mountApp('#/tickets/TKT-142/session',
    { ...ticketRoutes(ticketDoc({ lane: docLane(rows) }), rows), ...writes(over) })
  await settle()
  await settle()
  return app
}

const box = () => screen.getByTestId('composer-draft') as HTMLTextAreaElement
const button = () => screen.getByTestId('composer-button')
const posts = (path: string) => calls.filter((c) => c.method === 'POST' && c.url === path)

async function type(text: string) {
  await act(async () => { fireEvent.change(box(), { target: { value: text } }) })
  await settle()
}

describe('4.7 the composer', () => {
  it('one_control_three_jobs', async () => {
    // ── idle · `Send`
    const app = await mount([idle()])
    const control = button()
    expect(control).toHaveTextContent('Send')
    expect(control.getAttribute('data-mode')).toBe('send')

    const where = () => button().parentElement?.getAttribute('data-testid')
    const seat = where()

    // ── mid-turn · `Stop`, in the crash colour, and in the SAME SEAT. Not a second button
    //    beside the first, and not the first one moved: the same element, restyled.
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:01Z', seq: null,
        lane: LANE, ticket_id: 'TKT-142',
        state: {
          process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
          evidence: 'nothing_claimed', blocked: null, alarm: false,
        },
        waiting_for: null, needs_you: false,
      })
    })
    await settle()

    expect(screen.getAllByTestId('composer-button')).toHaveLength(1)
    expect(button()).toBe(control)                       // the same DOM node, not a replacement
    expect(button()).toHaveTextContent('Stop')
    expect(button().getAttribute('data-mode')).toBe('stop')
    expect(button().getAttribute('data-tone')).toBe('crash')
    expect(where()).toBe(seat)

    // ── held · the stream stops but the turn does not; a message sent into that quiet comes back
    //    held, and the control becomes `Send again` — still the same element, still the same seat
    await act(async () => {
      app.socket().deliver({
        m: 'lane.state', ts: '2026-07-28T10:00:02Z', seq: null,
        lane: LANE, ticket_id: 'TKT-142',
        state: {
          process: 'running',
          motion: { value: 'silent', silent_for_s: 252, since: '2026-07-28T09:55:48Z' },
          evidence: 'nothing_claimed', blocked: null, alarm: true,
        },
        waiting_for: null, needs_you: true,
      })
    })
    await settle()

    await deliver(app, snapshot([
      [1, 'human', { text: 'what are you waiting on?', mark: 'held' }],
      [2, 'held', { held_count: 1 }],
    ]))

    expect(screen.getAllByTestId('composer-button')).toHaveLength(1)
    expect(button()).toBe(control)
    expect(button()).toHaveTextContent('Send again')
    expect(button().getAttribute('data-mode')).toBe('send-again')
    expect(screen.getByTestId('composer-held'))
      .toHaveTextContent('1 message held · send again to interrupt the turn')

    // ── and the held rectangle is UNDER the message it belongs to, in the transcript
    const rendered = screen.getAllByTestId('entry')
    expect(rendered.map((n) => n.getAttribute('data-kind'))).toEqual(['human', 'held'])
    expect(rendered[0].compareDocumentPosition(rendered[1]) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy()

    // §8 D1 — `End session` and `Kill` are NOT in the composer. A kill next to Send is how a
    // session dies by accident.
    const composer = screen.getByTestId('composer')
    expect(composer.textContent).not.toMatch(/End session/)
    expect(composer.textContent).not.toMatch(/Kill/)
    // §8 D6 — `restart demo` resets the design's scripted demonstration and is not a feature
    expect(composer.textContent).not.toMatch(/restart demo/)
  })

  it('held_then_send_again_interrupts', async () => {
    // F14, and the design's own sentence: "A message sent while the agent is silent posts as held
    // with the rectangle above; sending again interrupts and delivers everything held, IN ORDER.
    // Nothing is silently dropped and nothing is rolled back behind your back."
    let held = 0
    const app = await mount([silent()], {
      [MESSAGES]: () => { held += 1; return { body: { status: 'held', held_count: held, entry_seq: held } } },
      [INTERRUPT]: { body: { interrupted: true, delivered: 2 } },
    })

    // ── the first message, sent into the quiet: E14, and it comes back held
    await type('one')
    await act(async () => { fireEvent.click(button()) })
    await settle()
    expect(posts(MESSAGES)).toHaveLength(1)
    expect(posts(INTERRUPT)).toHaveLength(0)
    expect(box().value).toBe('')

    await deliver(app,
      live(1, 'human', { text: 'one', mark: 'held' }),
      live(2, 'held', { held_count: 1 }))

    expect(button()).toHaveTextContent('Send again')
    expect(screen.getByTestId('composer-held'))
      .toHaveTextContent('1 message held · send again to interrupt the turn')

    // ── sending AGAIN. The second message joins the queue behind the first — posted before the
    //    interrupt, so the order the person typed them in is the order they are delivered in —
    //    and then E15 goes out, once.
    const before = calls.length
    await type('two')
    await act(async () => { fireEvent.click(button()) })
    await settle()

    expect(posts(MESSAGES)).toHaveLength(2)
    expect(posts(INTERRUPT)).toHaveLength(1)
    expect(calls.slice(before).filter((c) => c.method === 'POST').map((c) => c.url))
      .toEqual([MESSAGES, INTERRUPT])
    expect(box().value).toBe('')

    // and the transcript shows both held messages DELIVERED, in order. `patch:true` on the same
    // seq is how an entry changes; nothing is rebuilt and nothing is rolled back.
    await deliver(app,
      live(3, 'human', { text: 'two', mark: 'held' }),
      live(1, 'human', { text: 'one', mark: 'delivered' }, true),
      live(3, 'human', { text: 'two', mark: 'delivered' }, true))

    const humans = screen.getAllByTestId('entry')
      .filter((n) => n.getAttribute('data-kind') === 'human')
    expect(humans).toHaveLength(2)
    expect(humans[0]).toHaveTextContent('one')
    expect(humans[0]).toHaveTextContent('delivered')
    expect(humans[1]).toHaveTextContent('two')
    expect(humans[1]).toHaveTextContent('delivered')

    // the held line is gone the moment the server says nothing is held
    await deliver(app, live(4, 'turn', { n: 4, duration_s: 41, cost_usd: 0.86 }))
    expect(screen.queryByTestId('composer-held')).toBeNull()
  })

  it('socket_down_keeps_the_draft', async () => {
    // A send whose outcome cannot be observed is worse than a send that did not happen — so the
    // button goes, and the words stay. Losing what somebody typed because the wire blinked is the
    // failure this product is a reaction to.
    const app = await mount([idle()])
    await type('half-typed and not yet sen')

    await act(async () => { app.socket().onclose?.() })
    await settle()

    expect(screen.getByTestId('reconnect')).toHaveTextContent('SOCKET LOST · RECONNECTING')
    expect(box().value).toBe('half-typed and not yet sen')
    expect(box().disabled).toBe(false)
    expect(button()).toBeDisabled()

    // still editable, and Enter sends nothing while it cannot be observed
    await type('half-typed and not yet sent')
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()
    expect(box().value).toBe('half-typed and not yet sent')
    expect(posts(MESSAGES)).toHaveLength(0)
  })

  it('enter_sends_and_shift_enter_is_a_newline', async () => {
    await mount([idle()])
    await type('a message')

    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true }) })
    await settle()
    expect(posts(MESSAGES)).toHaveLength(0)

    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()
    expect(posts(MESSAGES)).toHaveLength(1)
    expect(box().value).toBe('')

    // nothing is sent on mount, on focus or on an empty Enter (§1 Safety)
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()
    expect(posts(MESSAGES)).toHaveLength(1)
  })

  it('a_hundred_entries_do_not_touch_the_draft', async () => {
    // ── MANDATORY NEGATIVE ────────────────────────────────────────────────────────────────────
    // The exact failure that killed the prototype: a poll rebuilt the DOM every eight seconds and
    // ate half-typed messages. Here nothing polls, so the hundred entries arrive as frames — and
    // the draft, the caret and the reader's scroll position are all untouched by them.
    const app = await mount([silent()])
    await deliver(app, snapshot([[1, 'prose', { text: 'the run so far' }]]))

    const DRAFT = 'the gate is red on the third assertion — look at '
    await type(DRAFT)
    const node = box()
    node.focus()
    node.setSelectionRange(20, 20)
    const caret = node.selectionStart

    // the reader is NOT at the bottom: they are reading something above it
    const column = screen.getByTestId('transcript')
    measure(column, { scrollHeight: 4000, clientHeight: 400, scrollTop: 120 })
    await act(async () => { fireEvent.scroll(column) })

    const commitsBefore = app.commits()
    const frames = []
    for (let i = 0; i < 100; i += 1) frames.push(live(2 + i, 'prose', { text: `line ${i}` }))
    await deliver(app, ...frames)

    expect(screen.getAllByTestId('entry')).toHaveLength(101)

    // ── byte-identical. Not "starts with", not "contains".
    expect(box().value).toBe(DRAFT)
    // ── the same element, so nothing remounted underneath the person typing
    expect(box()).toBe(node)
    // ── the caret has not moved
    expect(box().selectionStart).toBe(caret)
    expect(box().selectionEnd).toBe(caret)
    expect(document.activeElement).toBe(node)
    // ── and the scroll was not yanked to the bottom, because the reader was not there
    expect(column.scrollTop).toBe(120)

    // it really did re-render — otherwise this passes for the wrong reason
    expect(app.commits()).toBeGreaterThan(commitsBefore)

    // and the draft still sends, unchanged, when the person is ready
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()
    expect(posts(MESSAGES)).toHaveLength(1)
  })

  it('a_refused_send_gives_the_words_back', async () => {
    // E14's `409 wrong_state`. A message the server would not take is a message that did not
    // happen, and a composer that emptied the box anyway would be losing a draft on the one path
    // where a person is least likely to notice — which is the prototype's failure in a new coat.
    await mount([idle()], {
      [MESSAGES]: {
        status: 409,
        body: { error: { code: 'wrong_state', message: 'no live session', detail: {} } },
      },
    })

    await type('the words I do not want to type twice')
    await act(async () => { fireEvent.keyDown(box(), { key: 'Enter' }) })
    await settle()

    expect(posts(MESSAGES)).toHaveLength(1)
    expect(box().value).toBe('the words I do not want to type twice')
    expect(screen.getByTestId('composer-refused')).toHaveTextContent('not sent')
  })

  // ── WEB-FIX-037 · the button is a drawing of live state, and live state arrives on the socket ─
  //
  // `M2 lane.state` carries SPEC.md §3.2's three axes and the composer reads two of them. These
  // three cases are that path, end to end, through the whole app: one frame in, one word changed,
  // and NOTHING else moved — no reload, no refetch, no timer.

  /** One `M2 lane.state` for the lane under test, moving `motion` and nothing else. */
  const motion = (value: 'producing' | 'silent') => ({
    m: 'lane.state', ts: '2026-07-28T10:00:30Z', seq: null, lane: LANE, ticket_id: 'TKT-142',
    state: {
      process: 'running',
      motion: value === 'silent'
        ? { value, silent_for_s: 252, since: '2026-07-28T09:55:48Z' }
        : { value, silent_for_s: 0, since: null },
      evidence: 'nothing_claimed', blocked: null, alarm: value === 'silent',
    },
    waiting_for: null, needs_you: value === 'silent',
  })

  it('the_button_follows_the_lane_into_silence', async () => {
    // §1's own sentence: an agent stops producing output and the person watching wants to ask it
    // what happened. If the control still reads `Stop` they cannot.
    const app = await mount([running()])
    const control = button()
    expect(control).toHaveTextContent('Stop')
    expect(control.getAttribute('data-mode')).toBe('stop')

    const fetchesBefore = calls.length
    await deliver(app, motion('silent'))

    expect(button()).toHaveTextContent('Send')
    expect(button().getAttribute('data-mode')).toBe('send')
    // the SAME element: a reload or a remount would give a different node, and a person watching
    // a lane does not reload
    expect(button()).toBe(control)
    // nothing was read again — the frame carried the answer
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/lanes')).toHaveLength(1)

    // and no timer finishes the job the frame started
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()
    expect(button().getAttribute('data-mode')).toBe('send')
    expect(calls.length).toBe(fetchesBefore)
  })

  it('and_back_again', async () => {
    // The reverse transition, so the fix is a subscription that works rather than one direction
    // special-cased. A lane that starts talking again puts `Stop` back under the same hand.
    const app = await mount([silent()])
    const control = button()
    expect(control.getAttribute('data-mode')).toBe('send')

    const fetchesBefore = calls.length
    await deliver(app, motion('producing'))
    expect(button()).toBe(control)
    expect(button()).toHaveTextContent('Stop')
    expect(button().getAttribute('data-mode')).toBe('stop')
    expect(button().getAttribute('data-tone')).toBe('crash')

    await deliver(app, motion('silent'))
    expect(button()).toBe(control)
    expect(button()).toHaveTextContent('Send')
    expect(button().getAttribute('data-mode')).toBe('send')

    expect(calls.length).toBe(fetchesBefore)
  })

  it('no_frame_no_change', async () => {
    // ── MANDATORY NEGATIVE ────────────────────────────────────────────────────────────────────
    // The obvious cheap fix for a stale button is a poll, and a poll is the thing this
    // architecture exists to not have: it is what rebuilt the prototype's DOM every eight seconds
    // and ate half-typed messages. With NO frame carrying state, ten minutes must change nothing
    // at all — not the word on the button, not a fetch, not a single render.
    const app = await mount([running()])
    const control = button()
    const mode = control.getAttribute('data-mode')
    await type('what are you waiting on')

    const before = Array.from(document.querySelectorAll('body *'))
    const textBefore = new Map(before.map((n) => [n, n.textContent]))
    const fetchesBefore = calls.length

    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved — otherwise this case passes for the wrong reason. The 10 s liveness
    // ping is the only timer that talks to the server, it carries no state, and a live server
    // answers it with a bare `pong`.
    const frames = app.socket().frames()
    expect(frames.filter((f) => f.m === 'ping').length).toBeGreaterThanOrEqual(59)

    // the button has not moved a millimetre, and it is the same element
    expect(button()).toBe(control)
    expect(button().getAttribute('data-mode')).toBe(mode)
    // zero fetches. The prototype polled every eight seconds: seventy-five in this window.
    expect(calls.length).toBe(fetchesBefore)
    // nothing was remounted: the same DOM nodes, in the same order
    expect(Array.from(document.querySelectorAll('body *'))).toEqual(before)
    // and the only text that moved belongs to a clock — SPEC.md §4.4's counter is the one thing
    // allowed to change with no frame, and everything else changing would be the poll coming back
    const moved = before.filter((n) => n.textContent !== textBefore.get(n))
    expect(moved.filter((n) => !(n.matches('[data-clock]') || n.querySelector('[data-clock]')))
      .map((n) => `${n.tagName}.${String(n.className)}`)).toEqual([])
    // and the half-typed question is byte-identical
    expect(box().value).toBe('what are you waiting on')
  })

  it('no_lane_offers_no_box', async () => {
    // §4.7's empty state. The composer is present and says what to do; there is nothing to type
    // into because there is nothing on the other end of it.
    await mount([lane({ lane: 'lane/09', ticket_id: 'TKT-999' })])
    expect(screen.queryByTestId('composer-draft')).toBeNull()
    expect(screen.getByTestId('composer')).toHaveTextContent('Start a lane to send a message.')
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  // ── WEB-FIX-061 ════════════════════════════════════════════════════════════════════════════
  //
  // A lane whose run has crashed keeps its seat, its worktree and its branch, and the server is
  // perfectly happy to start a new run in it: `startGate` refuses only `not_found` and `queued`,
  // and `noteExit` already erased the dead run. `SessionTab` knows this and sends E18 rather than
  // E14 when there is no live session. THE BUTTON IN FRONT OF THE PERSON DID NOT REACH IT:
  // `if (!text) return`, so an empty Send made no request, showed no error and printed no line.
  //
  // `WEB-FEAT-045` §1 pre-decided the opposite — "the prompt is optional — if the textarea is
  // empty, E18 is called with no `prompt` field" — and `onSend` is written for exactly that. One
  // guard a layer up made it unreachable. The guard was CORRECT when it was written, before Send
  // could mean start; it became wrong without being edited.

  it('an_empty_send_into_a_lane_with_no_live_session_starts_a_run', async () => {
    const app = await mount([crashed()], { [`/api/lanes/${LANE}/runs`]: {
      status: 201, body: { run_id: 'run-9', session_id: 'sess-9', pid: 4242 } } })
    await deliver(app, snapshot([[1, 'prose', { text: 'the run that died' }]]))

    expect(box().value).toBe('')
    await act(async () => { fireEvent.click(button()) })
    await settle()

    const started = posts(`/api/lanes/${LANE}/runs`)
    expect(started, 'an empty Send on a crashed lane made no request at all').toHaveLength(1)
    expect(started[0].body ?? {}, 'an empty box must start with NO prompt — the agent begins on '
      + 'the ticket\'s own content').not.toHaveProperty('prompt')
    expect(posts(MESSAGES), 'an empty box sent a message instead of starting a run').toHaveLength(0)
  })

  it('a_crashed_lane_says_what_send_will_do', async () => {
    // A control whose behaviour changes with the lane's state and whose words do not is a control
    // a person has to guess at. The whole defect was silence; a button that now acts must say so.
    const app = await mount([crashed()], { [`/api/lanes/${LANE}/runs`]: {
      status: 201, body: { run_id: 'run-9', session_id: 'sess-9', pid: 4242 } } })
    await deliver(app, snapshot([[1, 'prose', { text: 'the run that died' }]]))

    const hint = screen.getByTestId('composer-hint').textContent ?? ''
    expect(hint.toLowerCase(), `the composer says "${hint}" on a lane with no live session`)
      .toContain('start')
  })

  it('a_crashed_lane_offers_the_way_back_in_words', async () => {
    // §1's criterion 2, under §1's own name. `a_crashed_lane_says_what_send_will_do` was shipped
    // in its place — renamed, moved to this file, and with the both-themes half dropped, which is
    // what QA failed the criterion on. The words are a token-driven surface like any other and a
    // hint that reads correctly in one theme and is invisible in the other is a control the
    // person cannot find, which is the defect this ticket is about said one layer up.
    const app = await mount([crashed()], { [`/api/lanes/${LANE}/runs`]: {
      status: 201, body: { run_id: 'run-9', session_id: 'sess-9', pid: 4242 } } })
    await deliver(app, snapshot([[1, 'prose', { text: 'the run that died' }]]))

    for (const theme of ['dark', 'light']) {
      await act(async () => { document.documentElement.setAttribute('data-theme', theme) })
      const hint = screen.getByTestId('composer-hint')
      const said = (hint.textContent ?? '').toLowerCase()
      expect(said, `the composer says "${said}" in the ${theme} theme`).toContain('start')
      expect(said).toContain('run')
      // present, in the flow, and not hidden by a rule that only fires in one theme
      expect(hint.hasAttribute('hidden')).toBe(false)
      expect(hint.getAttribute('aria-hidden')).not.toBe('true')
      expect((button() as HTMLButtonElement).disabled,
        `the button is disabled in the ${theme} theme`).toBe(false)
    }
    document.documentElement.setAttribute('data-theme', 'dark')
  })

  it('a_queued_lane_is_not_told_that_send_will_start_a_run', async () => {
    // REGRESSION this ticket introduced, found by QA. `startsARun` was computed from
    // `hasLiveSession` alone, and a QUEUED lane has no live session — so the composer promised
    // "Send starts a run", the empty box sent E18, and E18 answered `409 resource_busy` because
    // the lane is waiting its turn for an exclusive resource. `isQueued`'s own doc comment forbids
    // exactly this reading. A lane doing precisely what it should was made to look broken.
    const app = await mount([crashed({
      state: {
        process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
        evidence: 'nothing_claimed', blocked: null, alarm: false,
      },
      waiting_for: { resource: 'board-master', held_by: 'lane/02', position: 1 },
    })], { [`/api/lanes/${LANE}/runs`]: { status: 409, body: {
      error: { code: 'resource_busy', message: 'an exclusive resource this lane needs is held by '
                                            + 'another lane',
               detail: { resource: 'board-master', held_by: 'lane/02', position: 1 } } } } })
    await deliver(app, snapshot([]))

    const said = (screen.getByTestId('composer-hint').textContent ?? '').toLowerCase()
    expect(said, `a queued lane is told "${said}"`).not.toContain('starts a run')

    await act(async () => { fireEvent.click(button()) })
    await settle()
    expect(posts(`/api/lanes/${LANE}/runs`),
      'an empty Send on a QUEUED lane started a run the server was always going to refuse')
      .toHaveLength(0)
  })

  it('a_double_press_starts_one_run_not_two', async () => {
    // REGRESSION this ticket introduced, found by QA. The empty-box path gives no signal that the
    // first press landed — the box was already empty, so nothing clears and nothing changes — and
    // there was no in-flight guard, so a second press within the round trip started a SECOND run.
    // `WEB-FEAT-044` already learned this for claims (`test_a_second_claim_while_one_is_in_flight`)
    // and the lesson did not travel one component across.
    let release: (() => void) | null = null
    const held = new Promise<void>((resolve) => { release = resolve })
    const app = await mount([crashed()], { [`/api/lanes/${LANE}/runs`]: async () => {
      await held
      return { status: 201, body: { run_id: 'run-9', session_id: 'sess-9', pid: 4242 } }
    } })
    await deliver(app, snapshot([[1, 'prose', { text: 'the run that died' }]]))

    await act(async () => { fireEvent.click(button()) })
    expect((button() as HTMLButtonElement).disabled,
      'the control gives no sign the first press landed').toBe(true)
    await act(async () => { fireEvent.click(button()) })

    await act(async () => { release?.(); await settle() })
    await settle()

    expect(posts(`/api/lanes/${LANE}/runs`), 'a double press started two runs').toHaveLength(1)
  })

  it('an_empty_send_mid_session_still_sends_nothing', async () => {
    // NEGATIVE (mandatory). The cheap fix is to drop the guard, which turns every stray Enter in a
    // running session into an empty message — which is what the guard was put there for.
    const app = await mount([idle()])
    await deliver(app, snapshot([[1, 'prose', { text: 'mid session' }]]))

    expect(box().value).toBe('')
    await act(async () => { fireEvent.click(button()) })
    await settle()

    expect(posts(MESSAGES), 'an empty box sent an empty message mid-session').toHaveLength(0)
    expect(posts(`/api/lanes/${LANE}/runs`),
      'an empty box started a SECOND run in a lane that already has one').toHaveLength(0)
  })

})
