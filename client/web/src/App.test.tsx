import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup, fireEvent, act, waitFor } from '@testing-library/react'

import { mountApp, requirement, type Reply } from '../tests/helpers'

afterEach(cleanup)

const LIST = {
  '/api/requirements': {
    body: { requirements: [requirement()], total: 1, in_flight: 0, warnings: [] },
  },
}

const TICKET = {
  '/api/tickets/TKT-142': {
    body: {
      id: 'TKT-142', title: 'A ticket', workspace: 'core', status: 'todo', parent: 'REQ-014',
      path: '/tmp/fixture/docs/tickets/TKT-142.md', updated_iso: '2026-07-27T09:00:00Z',
      stages: ['SPEC'], stage: { index: 0, total: 1, round: 1, of: 1, source: 'manifest' },
      extras: [], body_markdown: '# A ticket\n', lane: null,
      runs: { count: 0, duration_s: 0, cost_usd: 0 },
      report: { exists: false, path: '/x.md', evidence: 'nothing_claimed' },
      needs_you: false,
      state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
    },
  },
  '/api/tickets/TKT-142/report': { status: 404, body: {} },
}

/**
 * The count line arrived, with a count in it (WEB-FIX-031).
 *
 * The wait condition IS the assertion, and that is the whole point. `count-line` is rendered
 * unconditionally and is EMPTY until E3 answers, so `findByTestId` — which waits for an element
 * to exist — returns the moment the surface mounts and hands back a box with nothing in it.
 * Whether the count had landed by then was a race between the reply and the one `setTimeout(0)`
 * of grace that testing-library allows after a query resolves; it fitted about nineteen times in
 * twenty. Waiting on the text cannot pass before the data it is about has arrived, and cannot
 * hang either: `waitFor` times out.
 */
const seesCount = (text: string, timeout?: number) =>
  waitFor(() => expect(screen.getByTestId('count-line')).toHaveTextContent(text), { timeout })

describe('4.1 app shell', () => {
  it('is_the_frame_every_surface_hangs_from', async () => {
    await mountApp('#/requirements', LIST)

    const bar = screen.getByTestId('top-bar')
    expect(bar).toHaveTextContent('FACTORY')
    // SPEC.md §4.1: E1 is the version and the repository name. The design's bar drew neither.
    expect(bar).toHaveTextContent('fixture')
    expect(bar).toHaveTextContent('0.1')
    expect(bar).toHaveTextContent('VOCABULARY')
    expect(screen.getByTestId('socket-chip')).toHaveTextContent('socket live')

    // Empty — no lanes: the `needs you` chip is ABSENT, not a zero. Nothing can need you
    // until lanes exist, and a control that does nothing teaches the user it is broken.
    expect(screen.queryByTestId('needs-you-chip')).toBeNull()
    expect(bar.textContent).not.toMatch(/need you/)
    // `PERMISSIONS` was asserted ABSENT here until WEB-FEAT-018, because the screen it reaches did
    // not exist and a control that does nothing teaches the user it is broken. It exists now, so
    // the assertion inverts rather than disappearing: §8 D4's one addition to the design's chrome
    // is the reason §4.12's revocable list is reachable at all.
    expect(bar.textContent).toMatch(/PERMISSIONS/)
  })

  it('the_breadcrumb_says_where_you_are_and_gets_you_back', async () => {
    await mountApp('#/tickets/TKT-142', { ...TICKET, ...LIST })

    const crumbs = await screen.findByTestId('breadcrumb')
    expect(crumbs).toHaveTextContent('requirements')
    expect(crumbs).toHaveTextContent('REQ-014')
    expect(crumbs).toHaveTextContent('TKT-142')

    await act(async () => { fireEvent.click(screen.getByRole('link', { name: 'requirements' })) })
    await seesCount('1 requirements')
  })

  it('the_breadcrumb_waits_for_a_list_that_answers_late', async () => {
    // The flake, made deterministic and kept. Ten milliseconds is less than any real server and
    // more than the grace tick, so a wait on the element EXISTING reads the count line while it
    // is still empty — every time, in one run, instead of once in twenty full-suite runs.
    await mountApp('#/tickets/TKT-142', {
      ...TICKET,
      '/api/requirements': async () => {
        await new Promise((r) => setTimeout(r, 10))
        return { body: { requirements: [requirement()], total: 1, in_flight: 0, warnings: [] } }
      },
    })

    await act(async () => { fireEvent.click(screen.getByRole('link', { name: 'requirements' })) })
    await seesCount('1 requirements')
  })

  it('the_breadcrumb_stays_red_while_the_list_never_arrives', async () => {
    // The negative the positive is worthless without: a wait that cannot time out is a sleep
    // with better manners. The list is requested and never answered, the count line renders
    // and stays empty, and the wait must end in a failure rather than in a pass or a hang.
    await mountApp('#/tickets/TKT-142', {
      ...TICKET,
      '/api/requirements': () => new Promise<Reply>(() => {}),
    })

    await act(async () => { fireEvent.click(screen.getByRole('link', { name: 'requirements' })) })
    await expect(seesCount('1 requirements', 250)).rejects.toThrow()
    expect(screen.getByTestId('count-line')).toBeEmptyDOMElement()
  })
})
