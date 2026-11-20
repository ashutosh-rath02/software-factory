import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { delivery, mountApp, settle } from './helpers'

/**
 * WEB-FEAT-325 · §1 criterion 3 — the tickets table carries an OWNER column.
 *
 * `factory.pen` frame `DAiio` puts it between WORKSPACE and STAGE. Scanning *who owns what* is the
 * manager's view; the header chip answers it one ticket at a time.
 */
const row = (over: Record<string, unknown> = {}) => ({
  id: 'SRV-FEAT-021', title: 'Lane recovery', workspace: 'factory-server',
  status: 'in-progress', provenance: 'unshipped', delivery: delivery(),
  stage: { index: 8, total: 10, round: 1, of: 1, source: 'ticket' },
  lane: null, owner: { name: 'congnguyen', source: 'commits' }, ...over,
})

const INDEX = {
  groups: [{ parent: 'FAC-EPIC-001', title: 'The factory', count: 2, merged: 1, pushed: 0,
             tickets: [row(), row({ id: 'WEB-FIX-031', title: 'A stalled lane says so',
                                    workspace: 'factory-client', owner: null })] }],
  total: 2, from: 1, to: 2, page: 1, pages: 1, page_size: 50,
  facets: { provenance: [], stage: [] }, applied: { q: '', provenance: 'all', stage: '' },
}

afterEach(cleanup)

describe('WEB-FEAT-325 · the tickets table names the owner', () => {
  it('test_the_table_has_an_owner_column', async () => {
    await mountApp('#/tickets', { '/api/tickets': { body: INDEX }, '/api/lanes': { body: null } })
    await settle()
    const heads = [...screen.getByTestId('epic-group').querySelectorAll('thead th')]
      .map(th => th.textContent?.trim())
    expect(heads).toEqual(['TICKET', 'TITLE', 'WORKSPACE', 'OWNER', 'STAGE', 'DELIVERY', 'NEEDS YOU'])

    const rows = [...screen.getByTestId('epic-group').querySelectorAll('tbody tr')]
    expect(rows[0]).toHaveTextContent('congnguyen')
    // a ticket nobody has committed to shows nothing, not `unknown` — the same rule as the chip
    expect(rows[1].querySelectorAll('td')[3].textContent?.trim()).toBe('—')
  })
})

describe('WEB-FEAT-325 · a 200 that parsed to nothing may not blank the app', () => {
  it('test_a_null_lanes_body_does_not_take_the_shell_down', async () => {
    // `LanesProvider` reads E9 on EVERY surface, so an unguarded `.lanes` on a null body throws
    // inside the provider and every screen goes with it. Found by `npm run gate` on `main`:
    // `TypeError: Cannot read properties of null (reading 'lanes')` at services/lanes.tsx:127.
    await mountApp('#/tickets', { '/api/tickets': { body: INDEX }, '/api/lanes': { body: null } })
    await settle()
    expect(screen.getByTestId('epic-group')).toBeTruthy()
  })
})
