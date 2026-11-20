import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { mountApp, settle, ticketDoc, ticketRoutes } from './helpers'

/**
 * SPEC.md §8 D2 — the strip stays linear; the round lives in the caption and nowhere else.
 *
 * "The design wins on the drawing, the ticket wins on the data." A stage entered twice is a fact
 * the model carries; the only visual consequence anywhere is the right-hand caption. No new chip,
 * no branch drawn, no history visualisation.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const FIRST_TIME = ticketDoc({ stage: { index: 4, total: 8, round: 1, of: 1, source: 'manifest' } })
const SECOND_TIME = ticketDoc({ stage: { index: 4, total: 8, round: 2, of: 4, source: 'ticket' } })

describe('4.5 the stage strip', () => {
  it('round_lands_in_the_caption_only', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(FIRST_TIME))
    await settle()
    await settle()
    screen.getByTestId('stage-strip')

    // the design's own caption, for a stage entered once
    expect(screen.getByTestId('stage-caption'))
      .toHaveTextContent('stage 4/8 · manifest sequence')
    const linear = screen.getByTestId('stage-strip').innerHTML
    const chipsBefore = screen.getByTestId('stage-strip').querySelectorAll('.stage-chip').length
    expect(chipsBefore).toBe(8)

    cleanup()

    // the same stage, entered a second time
    await mountApp('#/tickets/TKT-142', ticketRoutes(SECOND_TIME))
    await settle()
    await settle()
    screen.getByTestId('stage-strip')

    expect(screen.getByTestId('stage-caption'))
      .toHaveTextContent('stage 4/8 · round 2 of 4 · ticket sequence')

    // and the strip itself is UNCHANGED — byte for byte, not merely the same number of chips
    expect(screen.getByTestId('stage-strip').innerHTML).toBe(linear)
    expect(screen.getByTestId('stage-strip').querySelectorAll('.stage-chip')).toHaveLength(8)
    expect(screen.getByTestId('stage-strip').textContent).not.toMatch(/round|2 of 4/)
  })
})
