import { describe, it, expect, afterEach } from 'vitest'
import { screen, fireEvent, act, cleanup } from '@testing-library/react'

import { mountApp, callsTo, delivery, requirement, MANIFEST, settle } from './helpers'
import { FILTERS } from '../src/vocabulary'

afterEach(cleanup)

const THREE = [
  requirement(),
  requirement({
    id: 'REQ-011', title: 'Every delivery report cites the run that produced it',
    state: { process: 'not_started', motion: null, evidence: 'claimed_unproven' },
    needs_you: { count: 1, label: 'unproven claim' },
  }),
  requirement({ id: 'REQ-009', title: 'Agents cannot write outside their own lane checkout' }),
]

describe('4.2 requirements list', () => {
  it('filter_is_server_side', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': (url) => {
        const filter = url.searchParams.get('filter')
        if (filter === 'needs') {
          // `total` deliberately disagrees with the number of rows. The count line must read 4:
          // that is the only way to prove it comes from the response and is not rows.length.
          return { body: { requirements: [THREE[1]], total: 4, in_flight: 2, warnings: [] } }
        }
        return { body: { requirements: THREE, total: 3, in_flight: 1, warnings: [] } }
      },
    })

    expect(await screen.findAllByTestId('requirement-row')).toHaveLength(3)
    expect(screen.getByTestId('count-line')).toHaveTextContent('3 requirements · 1 in flight')
    const before = callsTo('/api/requirements').length
    expect(before).toBe(1)
    expect(callsTo('/api/requirements')[0].url).toContain('filter=all')

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'needs you' })) })
    await settle()

    // ONE request, and the filter is in it — not a filter applied to rows already in the browser
    const after = callsTo('/api/requirements')
    expect(after).toHaveLength(before + 1)
    expect(after[after.length - 1].url).toContain('filter=needs')

    // exactly what came back, and the count line from `total`
    const rows = screen.getAllByTestId('requirement-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('REQ-011')
    expect(screen.getByTestId('count-line')).toHaveTextContent('4 requirements · 2 in flight')
  })

  it('empty_state_names_the_path', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': (url) => ({
        body: { requirements: [], total: 0, in_flight: 0, warnings: [] },
        ...(url.searchParams.get('q') ? {} : {}),
      }),
    })

    const noneAtAll = (await screen.findByTestId('empty')).textContent ?? ''
    expect(noneAtAll).toContain('No requirements found under')
    // the overwhelmingly likely cause is a manifest pointing at the wrong directory, so say which
    expect(noneAtAll).toContain(MANIFEST.docs.epics)

    await act(async () => {
      fireEvent.change(screen.getByPlaceholderText('filter requirements…'),
        { target: { value: 'zzz' } })
    })
    await settle()

    const noMatch = screen.getByTestId('empty').textContent ?? ''
    // Two different causes may not share one empty state.
    expect(noMatch).not.toBe(noneAtAll)
    expect(noMatch).toMatch(/clear the filter/i)
    expect(noMatch).not.toContain(MANIFEST.docs.epics)

    // and the offer is a control that does what it says
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /clear the filter/i })) })
    await settle()
    expect((screen.getByPlaceholderText('filter requirements…') as HTMLInputElement).value).toBe('')
    expect(screen.getByTestId('empty').textContent).toBe(noneAtAll)
  })
})

/**
 * WEB-FEAT-041 — the row the server appends for tickets whose parent is nobody's.
 *
 * The server decides whether the row exists and where it sits (last), so these cases are about
 * the one thing the browser owns: that the row is legible as the group it is, with the count of
 * what is in it — a row reading `Standalone` and nothing else is a row nobody can act on.
 */
describe('4.2 the standalone row', () => {
  const STANDALONE = requirement({
    id: 'standalone', title: 'Standalone', status: '', path: '', updated_iso: '',
    workspaces: ['core', 'edge'], ticket_count: 3,
  })

  it('test_standalone_row_renders_when_orphan_tickets_exist', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: [...THREE, STANDALONE], total: 4, in_flight: 0, warnings: [] } },
    })

    const rows = await screen.findAllByTestId('requirement-row')
    expect(rows).toHaveLength(4)

    // last, because that is where the server put it, and named
    const last = rows[rows.length - 1]
    expect(last).toHaveTextContent('Standalone')
    expect(last.getAttribute('data-standalone')).toBe('true')
    // the count of what is inside it: a group whose size is invisible is a group nobody opens
    expect(last).toHaveTextContent('3 tickets')
    // and it goes where a requirement row goes
    expect(last.getAttribute('href')).toBe('#/requirements/standalone')

    // a real row is untouched: its status column is still the document's word
    expect(rows[0].getAttribute('data-standalone')).toBeNull()
    expect(rows[0]).toHaveTextContent('in progress')
    expect(rows[0]).not.toHaveTextContent('tickets')
  })

  it('test_standalone_row_absent_when_no_orphans', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: THREE, total: 3, in_flight: 0, warnings: [] } },
    })

    const rows = await screen.findAllByTestId('requirement-row')
    expect(rows).toHaveLength(3)
    expect(rows.some((r) => r.getAttribute('data-standalone') === 'true')).toBe(false)
    expect(screen.getByTestId('rows')).not.toHaveTextContent('Standalone')
  })

  it('negative_an_error_from_E3_is_the_error_state_not_a_blank_list', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': {
        status: 500,
        body: { error: { code: 'internal', message: 'the repository could not be read', detail: {} } },
      },
    })
    await settle()

    const shown = (await screen.findByTestId('empty')).textContent ?? ''
    expect(shown).toContain('THE LIST COULD NOT BE READ')
    expect(shown).toContain('the repository could not be read')
    expect(screen.queryByTestId('rows')).toBeNull()
  })
})

/**
 * SRV-FEAT-165 — the fifth chip.
 *
 * `done` is the question the four before it cannot ask: not what needs a person, but what has
 * actually landed. The server answers it (E3), so what the browser owns is that the chip ASKS —
 * a filter applied to rows already here would be a second answer, and the count line would then
 * be about a different list than the rows.
 */
describe('4.2 the done filter', () => {
  const LANDED = requirement({
    id: 'REQ-014', title: 'Never ship an artifact the working tree cannot reproduce',
    delivery: delivery({ state: 'done', detail: 'on main' }),
  })

  it('test_the_done_chip_asks_the_server', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': (url) => (url.searchParams.get('filter') === 'done'
        ? { body: { requirements: [LANDED], total: 1, in_flight: 0, warnings: [] } }
        : { body: { requirements: THREE, total: 3, in_flight: 1, warnings: [] } }),
    })

    expect(await screen.findAllByTestId('requirement-row')).toHaveLength(3)
    const before = callsTo('/api/requirements').length

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'done' })) })
    await settle()

    const after = callsTo('/api/requirements')
    expect(after).toHaveLength(before + 1)
    expect(after[after.length - 1].url).toContain('filter=done')

    const rows = screen.getAllByTestId('requirement-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('REQ-014')
    expect(rows[0]).toHaveTextContent('done')
    expect(screen.getByTestId('count-line')).toHaveTextContent('1 requirements · 0 in flight')
  })

  it('test_a_done_filter_that_matches_nothing_offers_to_clear_itself', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': (url) => (url.searchParams.get('filter') === 'done'
        ? { body: { requirements: [], total: 0, in_flight: 0, warnings: [] } }
        : { body: { requirements: THREE, total: 3, in_flight: 0, warnings: [] } }),
    })

    expect(await screen.findAllByTestId('requirement-row')).toHaveLength(3)

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'done' })) })
    await settle()

    // a filter that matches nothing is not an empty repository, and may not borrow its sentence
    const empty = screen.getByTestId('empty').textContent ?? ''
    expect(empty).toMatch(/clear the filter/i)
    expect(empty).not.toContain(MANIFEST.docs.epics)

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /clear the filter/i })) })
    await settle()

    expect(screen.getAllByTestId('requirement-row')).toHaveLength(3)
    expect(callsTo('/api/requirements').pop()?.url).toContain('filter=all')
  })

  it('negative_the_chip_list_is_the_vocabulary_table_and_nothing_else', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: THREE, total: 3, in_flight: 0, warnings: [] } },
    })
    await screen.findAllByTestId('requirement-row')

    // §4.11's rule applied to the filter bar: a chip written into the page by hand, or one of the
    // table's dropped from it, fails here. Order is the table's order.
    const chips = Array.from(document.querySelectorAll('.filter-chip'))
      .map((c) => c.textContent)
    expect(chips).toEqual(FILTERS.map((f) => f.label))
    expect(chips).toContain('done')
  })
})

describe('4.2 the requirement tree (WEB-FEAT-411)', () => {
  const tree = () => [
    requirement({ id: 'R1', title: 'the product epic', parent: '', children: ['T1', 'T2'],
      ticket_count: 5, own_ticket_count: 1 }),
    requirement({ id: 'R2', title: 'another product epic', parent: '', children: [], ticket_count: 0 }),
    requirement({ id: 'T1', title: 'R1 in software', parent: 'R1', children: ['T3'],
      ticket_count: 3, own_ticket_count: 2, workspaces: ['software'] }),
    requirement({ id: 'T2', title: 'R1 in sdk', parent: 'R1', children: [], ticket_count: 1,
      workspaces: ['sdk'] }),
    requirement({ id: 'T3', title: 'a grandchild', parent: 'T1', children: [], ticket_count: 1 }),
  ]

  it('a_child_is_shown_only_when_its_parent_is_opened', async () => {
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: tree(), total: 5, in_flight: 0, warnings: [] } },
    })
    // closed by default: the two top-level rows and nothing else — T1, T2 and T3 are not rows
    const rows = await screen.findAllByTestId('requirement-row')
    expect(rows.map((r) => r.textContent)).toEqual(expect.arrayContaining([expect.stringContaining('R1')]))
    expect(rows).toHaveLength(2)
    expect(screen.queryByText('R1 in software')).toBeNull()
    // the closed parent says how much is beneath it, from the server's roll-up
    expect(screen.getByTestId('requirement-children-count')).toHaveTextContent('2 children · 5 tickets')

    // open R1: its two direct children appear beneath it, indented; the grandchild stays hidden
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'show the children of R1' })) })
    const opened = screen.getAllByTestId('requirement-row')
    expect(opened.map((r) => r.textContent?.slice(0, 40))).toEqual([
      expect.stringContaining('R1'), expect.stringContaining('T1'),
      expect.stringContaining('T2'), expect.stringContaining('R2'),
    ])
    expect(opened[1].getAttribute('data-depth')).toBe('1')
    expect(opened[1].getAttribute('data-parent')).toBe('R1')
    expect(screen.queryByText('a grandchild')).toBeNull()

    // open T1: the grandchild appears at depth 2
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'show the children of T1' })) })
    expect(screen.getAllByTestId('requirement-row')).toHaveLength(5)
    expect(screen.getByText('a grandchild').closest('[data-testid="requirement-row"]')?.getAttribute('data-depth')).toBe('2')

    // close R1 again: everything beneath it goes, including the opened grandchild
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'hide the children of R1' })) })
    expect(screen.getAllByTestId('requirement-row')).toHaveLength(2)
  })

  it('negative_a_child_whose_parent_is_not_in_the_answer_stays_a_top_level_row', async () => {
    // a filtered answer, or an `unknown_parent` warning, can hand the page a child alone
    await mountApp('#/requirements', {
      '/api/requirements': {
        body: { requirements: [tree()[3]], total: 1, in_flight: 0, warnings: [] },
      },
    })
    const rows = await screen.findAllByTestId('requirement-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('T2')
    expect(rows[0].getAttribute('data-depth')).toBe('0')
    expect(screen.queryByTestId('requirement-toggle')).toBeNull()
  })
})
