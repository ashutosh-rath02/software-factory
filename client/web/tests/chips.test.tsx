import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { lane, mountApp, settle, ticketDoc, ticketRoutes } from './helpers'

/**
 * F17 · SPEC.md §3.2 and §4.5 — three chips, in order, never merged.
 *
 * The prototype flattened them: a stale `quiet` wrote over a live `running`, and a lane that was
 * working looked like a lane that had stopped. This is the case that would see that come back.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** Running, silent for 130 s, and claiming done with nothing behind it — all three at once. */
const THREE_FACTS = lane({
  lane: 'lane/01', ticket_id: 'TKT-142', needs_you: true,
  state: {
    process: 'running',
    motion: { value: 'silent', silent_for_s: 130, since: '2026-07-28T09:57:50Z' },
    evidence: 'claimed_unproven', blocked: null, alarm: true,
  },
})

describe('4.5 the three state chips', () => {
  it('three_chips_never_merged', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), [THREE_FACTS]))
    await settle()
    await settle()
    screen.getByTestId('ticket-header')

    const chips = screen.getAllByTestId('state-chip')
    expect(chips).toHaveLength(3)

    // one per axis, in SPEC.md §4.5's order, and never merged
    expect(chips.map((c) => c.getAttribute('data-axis')))
      .toEqual(['process', 'motion', 'evidence'])

    // all three facts are readable at once — a lane can be running, going nowhere, and claiming
    // done, and a person needs all three of those words, not the loudest one
    expect(chips[0]).toHaveTextContent('PROCESS · RUNNING')
    expect(chips[1]).toHaveTextContent('MOTION · SILENT 2m 10s')
    expect(chips[2]).toHaveTextContent('EVIDENCE · CLAIMED · NO PROOF')

    // no chip says anything about another axis
    expect(chips[0].textContent).not.toMatch(/MOTION|EVIDENCE/)
    expect(chips[1].textContent).not.toMatch(/PROCESS|EVIDENCE/)
    expect(chips[2].textContent).not.toMatch(/PROCESS|MOTION/)

    // and there is no combined status ANYWHERE in the DOM: no element's own text — the text it
    // owns rather than the text its children own — joins two axes into one word
    const own = (el: Element) => Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3).map((n) => n.textContent ?? '').join(' ').trim()
    const merged: string[] = []
    for (const el of Array.from(document.querySelectorAll('body *'))) {
      const text = own(el).toLowerCase()
      if (!text) continue
      const axes = [/\brunning\b/, /\bsilent\b/, /claimed/].filter((re) => re.test(text))
      // the state LINE on a lane card is the design's own two-word phrase and is not a chip;
      // the chips are what §4.5 forbids merging, so the header is where this must hold
      if (axes.length > 1 && el.closest('[data-testid="ticket-header"]')) merged.push(text)
    }
    expect(merged).toEqual([])
  })
})
