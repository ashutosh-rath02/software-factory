import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import { callsTo, FakeSocket, mountApp, requirement, settle } from './helpers'

/**
 * WEB-FIX-037 · SPEC.md §4.1, §4.13, M16 — the socket chip and the title tell the truth.
 *
 * §4.1 states the rule in one line: *"The socket chip is client-local: green while a frame arrived
 * within 15 s, otherwise the crash colour and the word `reconnecting`."* M16 gives the mechanism —
 * a `ping` every 10 s of idleness, "so a dead connection is detected in 15 s rather than at the
 * next user action".
 *
 * A connection that is CUT does not close. The pings go into a buffer the machine can no longer
 * flush, no `pong` comes back, and `onclose` never fires — so a client whose only evidence is
 * `onclose` shows `socket live` for ever over a wire nothing can arrive on. Every live state on
 * the screen then freezes at its last value while claiming to be current, which is the composer
 * still reading `Stop` on a lane that went quiet an hour ago.
 */

const ROWS = [requirement(), requirement({ id: 'REQ-011', title: 'Second' })]

beforeEach(() => {
  document.title = 'Factory'
  vi.useFakeTimers({ shouldAdvanceTime: false })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

async function mounted() {
  const app = await mountApp('#/requirements', {
    '/api/requirements': { body: { requirements: ROWS, total: 2, in_flight: 0, warnings: [] } },
  })
  await settle()
  return app
}

describe('4.1 · 4.13 the socket chip and the title', () => {
  it('the_title_follows_the_socket_down_and_back', async () => {
    const app = await mounted()
    expect(document.title).toBe('Factory')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('socket live')

    // ── a server that is answering. A minute of pings, all answered, and nothing moves.
    await act(async () => { vi.advanceTimersByTime(60_000) })
    await settle()
    expect(app.socket().frames().filter((f) => f.m === 'ping').length).toBeGreaterThanOrEqual(5)
    expect(document.title).toBe('Factory')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('socket live')

    // ── the network is cut. The socket does NOT close: it stops answering, which is what a cut
    //    wire looks like from inside a browser.
    const cut = app.socket()
    cut.answers = false
    const reads = callsTo('/api/requirements').length

    await act(async () => { vi.advanceTimersByTime(16_000) })
    await settle()

    expect(document.title).toBe('(offline) Factory')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('reconnecting')
    // §4 preamble — nothing is cleared and nothing is refetched behind the user's back
    expect(screen.getAllByTestId('requirement-row')).toHaveLength(2)
    expect(callsTo('/api/requirements')).toHaveLength(reads)

    // ── and back. The client reconnects on its own; the prefix goes, and the drop is the one
    //    moment a document scope must read again, because M13 said nothing while it was down.
    await act(async () => { vi.advanceTimersByTime(2000) })
    const back = FakeSocket.last
    expect(back).not.toBe(cut)
    await act(async () => { back.onopen?.() })
    await settle()

    expect(document.title).toBe('Factory')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('socket live')
    expect(callsTo('/api/requirements')).toHaveLength(reads + 1)

    // ── and it is answering again: another minute changes nothing back
    await act(async () => { vi.advanceTimersByTime(60_000) })
    await settle()
    expect(document.title).toBe('Factory')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('socket live')
  })
})
