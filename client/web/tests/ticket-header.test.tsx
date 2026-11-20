import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { mountApp, settle, ticketDoc, ticketRoutes } from './helpers'

/**
 * WEB-FIX-328 — the header drew two chips naming different people.
 *
 * `E5` can carry TWO claims about who owns a ticket: what git says (`owner.source: commits`) and
 * what the repository's own document declares, which comes back in `extras` when the manifest
 * names `owner` in `display.ticket_fields`. They are different claims and CAN disagree — the gate
 * fixture has git saying `Gate` and the document saying `core team`.
 *
 * Suppressing one is not the fix: §1 asks for both readable. What it forbids is two chips reading
 * `owner X` and `owner Y` side by side, which tells a reader nothing about which is which.
 */
const owners = () =>
  [...screen.getByTestId('ticket-identity').querySelectorAll('.tag')]
    .filter(el => el.textContent?.trim().toLowerCase().startsWith('owner'))

afterEach(cleanup)

describe('WEB-FIX-328 · two claims about one ticket', () => {
  it('a_document_owner_and_a_derived_owner_are_distinguishable', async () => {
    const doc = ticketDoc({
      owner: { name: 'Gate', source: 'commits' },
      extras: [{ label: 'owner', value: 'core team' }, { label: 'contract', value: 'v0.1.0' }],
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(doc, []))
    await settle()

    const chips = owners()
    expect(chips, 'two chips reading `owner X` and `owner Y` is what this ticket forbids')
      .toHaveLength(1)
    // BOTH readable — the derived name, its source, and the document's declaration
    expect(chips[0]).toHaveTextContent('Gate')
    expect(chips[0]).toHaveTextContent('commits')
    expect(chips[0]).toHaveTextContent('core team')
    // the repository's other declared field is untouched
    expect(screen.getAllByTestId('ticket-extra').map(e => e.textContent?.trim()))
      .toEqual(['contract v0.1.0'])
  })

  it('a_repository_with_no_owner_field_draws_one_chip', async () => {
    const doc = ticketDoc({
      owner: { name: 'Gate', source: 'commits' },
      extras: [{ label: 'contract', value: 'v0.1.0' }],
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(doc, []))
    await settle()
    const chips = owners()
    expect(chips).toHaveLength(1)
    expect(chips[0]).toHaveTextContent('owner Gate · commits')
    expect(chips[0].textContent).not.toContain('declared')
  })

  it('two_owner_chips_naming_different_people_is_refused', async () => {
    // MANDATORY NEGATIVE — the exact payload the gate fixture produces. Before the fix this drew
    // `owner core team` beside `owner Gate · commits`; the first case must go red on it.
    const doc = ticketDoc({
      owner: { name: 'Gate', source: 'commits' },
      extras: [{ label: 'owner', value: 'core team' }],
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(doc, []))
    await settle()
    const text = owners().map(c => c.textContent?.trim()).join(' | ')
    expect(owners(), `the header drew: ${text}`).toHaveLength(1)
    expect(text).toMatch(/core team/)
    expect(text).toMatch(/Gate/)
  })
})
