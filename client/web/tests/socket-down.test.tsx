import { describe, it, expect, afterEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import { mountApp, callsTo, requirement, settle } from './helpers'

afterEach(() => { cleanup(); vi.useRealTimers() })

const ROWS = [
  requirement(),
  requirement({ id: 'REQ-011', title: 'Every delivery report cites the run that produced it' }),
]

describe('§4 preamble · socket down', () => {
  it('nothing_is_cleared', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const app = await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: ROWS, total: 2, in_flight: 0, warnings: [] } },
    })

    await settle()
    expect(screen.getAllByTestId('requirement-row')).toHaveLength(2)
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('socket live')
    const before = callsTo('/api/requirements').length

    await act(async () => { app.socket().onclose?.() })
    await settle()

    // the reconnect block pins to the top of the affected surface, in the design's own words
    const block = screen.getByTestId('reconnect')
    expect(block).toHaveTextContent('SOCKET LOST · RECONNECTING')
    expect(block).toHaveTextContent(
      'The transcript below is what arrived before the drop. Nothing has been discarded; '
      + 'your draft is intact.')
    const surface = screen.getByTestId('surface')
    expect(surface.firstElementChild).toBe(block)

    // rows keep their values, and dim
    const rows = screen.getAllByTestId('requirement-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('REQ-014')
    expect(rows[1]).toHaveTextContent('REQ-011')
    expect(screen.getByTestId('rows').getAttribute('data-dim')).toBe('true')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('reconnecting')

    // and nothing is refetched behind the user's back — not on the drop, not while it is down
    await act(async () => { vi.advanceTimersByTime(30_000) })
    await settle()
    expect(callsTo('/api/requirements')).toHaveLength(before)
    expect(screen.getAllByTestId('requirement-row')).toHaveLength(2)
  })
})
