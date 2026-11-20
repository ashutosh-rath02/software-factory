import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { mountApp, requirement, ticket } from './helpers'

afterEach(cleanup)

/**
 * SRV-FEAT-028 · F30 — the row shows what happened, not what the document claims.
 *
 * The document's `status:` is a claim written by a session that may not have survived to write it
 * truthfully. Three of the first four QA runs on this repository left one behind that was not
 * true, and in the worst case it said `pass`. So the row renders the DERIVED state, and where the
 * two disagree it says so — that disagreement is the most valuable thing on the row, and a
 * component that renders the claim instead has nothing to offer the person who woke up.
 */

const delivery = (over: Record<string, unknown> = {}) => ({
  state: 'unshipped', detail: '2 commits on ticket/TKT-142, on no remote',
  claimed: 'pass', disagrees: true, needs_you: true, ...over,
})

describe('F30 delivery badge', () => {
  it('the_row_shows_the_derived_state', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': {
        body: {
          requirements: [requirement({
            status: 'pass',
            delivery: delivery(),
            needs_you: { count: 1, label: '1 need you' },
          })],
          total: 1, in_flight: 0, warnings: [],
        },
      },
    })

    const badge = await screen.findByTestId('delivery-badge')
    expect(badge).toHaveAttribute('data-state', 'unshipped')
    expect(badge).toHaveTextContent('unshipped')

    // the claim is named as a disagreement, never as the row's state
    expect(badge).toHaveAttribute('data-disagrees', 'true')
    expect(badge.textContent).toMatch(/document says pass/i)

    const row = screen.getByTestId('requirement-row')
    const state = row.querySelector('[data-testid="delivery-badge"]')
    expect(state?.getAttribute('data-state')).toBe('unshipped')
    expect(state?.getAttribute('data-state')).not.toBe('pass')
  })

  it('stalled_and_unshipped_carry_the_alarm_border', async () => {
    await mountApp('#/requirements/REQ-014', {
      '/api/requirements/REQ-014': {
        body: {
          id: 'REQ-014', title: 'Never ship what cannot be reproduced', status: 'in progress',
          path: '/tmp/f/REQ-014.md', updated_iso: '2026-07-27T09:00:00Z',
          summary: 'One line.', body_markdown: '# REQ-014',
          needs_you: { count: 2, total: 5, label: '2 need you' },
          state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
          delivery: delivery({ state: 'stalled' }),
          groups: [{
            workspace: 'core', stage_sequence: ['SPEC', 'IMPL'], stage_sequence_source: 'manifest',
            count: 5,
            tickets: [
              ticket({ id: 'TKT-1', delivery: delivery({ state: 'stalled', claimed: 'awaiting-qa' }) }),
              ticket({ id: 'TKT-2', delivery: delivery({ state: 'unshipped' }) }),
              ticket({ id: 'TKT-3', delivery: delivery({ state: 'done', claimed: 'pass', disagrees: false, needs_you: false }) }),
              ticket({ id: 'TKT-4', delivery: delivery({ state: 'waiting', claimed: 'pass', disagrees: false, needs_you: false }) }),
              ticket({ id: 'TKT-5', delivery: delivery({ state: 'running', claimed: 'in-progress', disagrees: false, needs_you: false }) }),
            ],
          }],
        },
      },
    })

    const badges = await screen.findAllByTestId('delivery-badge')
    const byState = new Map(badges.map((b) => [b.getAttribute('data-state'), b]))

    // all five states a person needs at breakfast render, and each is itself
    expect([...byState.keys()].sort())
      .toEqual(['done', 'running', 'stalled', 'unshipped', 'waiting'])

    expect(byState.get('stalled')).toHaveAttribute('data-alarm', 'true')
    expect(byState.get('unshipped')).toHaveAttribute('data-alarm', 'true')
    expect(byState.get('done')).toHaveAttribute('data-alarm', 'false')
    expect(byState.get('waiting')).toHaveAttribute('data-alarm', 'false')
    expect(byState.get('running')).toHaveAttribute('data-alarm', 'false')
  })
})
