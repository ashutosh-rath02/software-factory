import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup, within } from '@testing-library/react'

import { mountApp } from './helpers'

afterEach(cleanup)

const ticket = (id: string, total: number) => ({
  id, title: `${id} does a thing`, status: 'todo',
  state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
  stage: { index: 2, total, round: 1, of: 1, source: 'ticket' },
  needs_you: false, lane: null, cost_usd: 0,
})

const DETAIL = {
  id: 'REQ-014', title: 'Never ship an artifact the working tree cannot reproduce',
  status: 'in progress', path: '/tmp/fixture/docs/requirements/REQ-014.md',
  updated_iso: '2026-07-27T09:00:00Z',
  summary: 'Seven tickets across three workspaces.',
  body_markdown: '# REQ-014\n\nSeven tickets across three workspaces.\n',
  needs_you: { count: 2, total: 4, label: '2 need you' },
  state: { process: 'not_started', motion: null, evidence: 'claimed_unproven' },
  groups: [
    {
      workspace: 'core',
      stage_sequence: ['SPEC', 'TEST', 'IMPL', 'BUILD', 'FLASH', 'GATE', 'QA', 'DELIVER'],
      stage_sequence_source: 'ticket', count: 2,
      tickets: [ticket('TKT-142', 8), ticket('TKT-139', 8)],
    },
    {
      workspace: 'web',
      stage_sequence: ['SPEC', 'IMPL', 'TEST', 'DELIVER'],
      stage_sequence_source: 'ticket', count: 1,
      tickets: [ticket('TKT-151', 4)],
    },
    {
      workspace: 'unassigned',
      stage_sequence: ['SPEC', 'IMPL', 'GATE', 'DELIVER'],
      stage_sequence_source: 'manifest', count: 1,
      tickets: [ticket('TKT-900', 4)],
    },
  ],
}

describe('4.3 requirement detail', () => {
  it('groups_carry_their_own_sequence', async () => {
    await mountApp('#/requirements/REQ-014', {
      '/api/requirements/REQ-014': { body: DETAIL },
    })

    const groups = await screen.findAllByTestId('workspace-group')
    expect(groups).toHaveLength(3)

    // each group carries the sequence its own tickets declared, as data and not as a printed line
    // (WEB-FEAT-411: the ticket row's N/M and the ticket page's strip already say it) …
    expect(within(groups[0]).getByTestId('group-name')).toHaveTextContent('core')
    expect(groups[0].getAttribute('data-sequence'))
      .toBe('SPEC → TEST → IMPL → BUILD → FLASH → GATE → QA → DELIVER')
    expect(within(groups[1]).getByTestId('group-name')).toHaveTextContent('web')
    expect(groups[1].getAttribute('data-sequence')).toBe('SPEC → IMPL → TEST → DELIVER')
    expect(within(groups[0]).queryByTestId('group-sequence')).toBeNull()

    // … so N/M is only ever read against its own group's total. The tool never compares 4/8 to 2/4.
    for (const row of within(groups[0]).getAllByTestId('ticket-row')) {
      expect(row).toHaveTextContent('2/8')
    }
    expect(within(groups[1]).getAllByTestId('ticket-row')[0]).toHaveTextContent('2/4')

    expect(within(groups[0]).getByTestId('group-count')).toHaveTextContent('2 tickets')
    expect(within(groups[1]).getByTestId('group-count')).toHaveTextContent('1 ticket')

    // a ticket naming a workspace the manifest does not declare goes in a FINAL group,
    // and is not dropped off the screen
    expect(within(groups[2]).getByTestId('group-name')).toHaveTextContent('unassigned')
    expect(within(groups[2]).getAllByTestId('ticket-row')[0]).toHaveTextContent('TKT-900')

    // the header, and its own pill
    expect(screen.getByTestId('requirement-header')).toHaveTextContent('REQ-014')
    expect(screen.getByTestId('requirement-header'))
      .toHaveTextContent('Never ship an artifact the working tree cannot reproduce')
    expect(screen.getByTestId('needs-pill')).toHaveTextContent('2 of 4 need you')
  })

  it('empty_requirement_says_so', async () => {
    await mountApp('#/requirements/REQ-999', {
      '/api/requirements/REQ-999': {
        body: { ...DETAIL, id: 'REQ-999', groups: [], needs_you: { count: 0, total: 0, label: '' } },
      },
    })
    expect(await screen.findByTestId('empty'))
      .toHaveTextContent('No tickets reference this requirement yet.')
  })
})

describe('4.3 child requirements (WEB-FEAT-411)', () => {
  const child = (id: string, repo: string, tickets: ReturnType<typeof ticket>[]) => ({
    id, title: `${id} carries the work in ${repo}`, repo, status: 'draft',
    own_ticket_count: tickets.length, ticket_count: tickets.length,
    delivery: { state: 'not_started', detail: '', claimed: 'todo', disagrees: false, needs_you: false },
    tickets: tickets.length === 0 ? [] : [{
      workspace: repo, stage_sequence: ['SPEC', 'IMPL', 'GATE', 'QA'], stage_sequence_source: 'ticket',
      count: tickets.length, tickets,
    }],
  })

  it('a_parent_with_children_shows_them_with_their_tickets_before_its_own_groups', async () => {
    await mountApp('#/requirements/PRD-1', {
      '/api/requirements/PRD-1': { body: {
        ...DETAIL, id: 'PRD-1', title: 'the product epic', groups: [],
        children: [child('SW-1', 'software', [ticket('SW-T-1', 4)]), child('ME-1', 'mechanical', [])],
      } },
    })
    const section = await screen.findByTestId('child-requirements')
    expect(within(section).getByTestId('children-count')).toHaveTextContent('2 requirements')
    const children = within(section).getAllByTestId('child-requirement')
    expect(children).toHaveLength(2)
    // each child names its repo and its count, and links to its own page
    expect(within(children[0]).getByTestId('child-requirement-row')).toHaveTextContent('SW-1')
    expect(within(children[0]).getByTestId('child-requirement-row').getAttribute('href')).toContain('SW-1')
    expect(within(children[0]).getByTestId('child-ticket-count')).toHaveTextContent('1 ticket')
    // and its tickets are grouped exactly as its own page would group them
    expect(within(children[0]).getAllByTestId('ticket-row')).toHaveLength(1)
    expect(within(children[0]).getByTestId('ticket-row')).toHaveTextContent('SW-T-1')
    expect(within(children[1]).queryByTestId('ticket-row')).toBeNull()
    // a parent whose own groups are empty is not "NO TICKETS" while its children carry work
    expect(screen.queryByText('No tickets reference this requirement yet.')).toBeNull()
  })

  it('negative_a_requirement_without_children_renders_exactly_as_before', async () => {
    await mountApp('#/requirements/REQ-014', {
      '/api/requirements/REQ-014': { body: { ...DETAIL, children: [] } },
    })
    expect(await screen.findAllByTestId('workspace-group')).toHaveLength(3)
    expect(screen.queryByTestId('child-requirements')).toBeNull()
  })

  it('negative_no_children_and_no_groups_is_still_no_tickets', async () => {
    await mountApp('#/requirements/REQ-015', {
      '/api/requirements/REQ-015': { body: { ...DETAIL, id: 'REQ-015', children: [], groups: [] } },
    })
    expect(await screen.findByText('No tickets reference this requirement yet.')).toBeTruthy()
    expect(screen.queryByTestId('child-requirements')).toBeNull()
  })
})
