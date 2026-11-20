import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { mountApp, calls } from './helpers'

afterEach(cleanup)

const BODY = [
  '# Guard against flashing a stale build artifact',
  '',
  'The flash stage writes whatever is in `build/`.',
  '',
  '## ACCEPTANCE',
  '',
  '1. The build stage decides freshness by hashing tracked sources.',
  '2. The flash stage refuses an artifact whose hash does not match.',
  '',
  '<b>bold</b> and <script>window.__pwned = 1</script>',
  '',
].join('\n')

const TICKET = {
  id: 'TKT-142', title: 'Guard against flashing a stale build artifact',
  workspace: 'core', status: 'todo', parent: 'REQ-014',
  path: '/tmp/fixture/docs/tickets/TKT-142.md',
  updated_iso: '2026-07-27T09:00:00Z',
  stages: ['SPEC', 'IMPL', 'GATE', 'DELIVER'],
  stage: { index: 0, total: 4, round: 1, of: 1, source: 'manifest' },
  extras: [{ label: 'contract', value: 'factory-v0.1.0' }],
  body_markdown: BODY,
  lane: null, runs: { count: 0, duration_s: 0, cost_usd: 0 },
  report: { exists: false, path: '/tmp/fixture/docs/reports/TKT-142-report.md',
            evidence: 'nothing_claimed' },
  needs_you: false,
  state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
}

/**
 * The address names the tab from `WEB-FEAT-014` onwards.
 *
 * These two cases opened `#/tickets/TKT-142` and relied on `TICKET.MD` being the registry's FIRST
 * row — which was true only while the SESSION tab did not exist. §4.6 calls SESSION "the heart of
 * it" and the design draws it first, so it is now the row an address with no tab falls back to.
 * Not one assertion below has changed; only the address they are made at, which is what
 * `report-md.test.tsx` has always done.
 */
describe('4.8 TICKET.MD', () => {
  it('read_only_with_path', async () => {
    await mountApp('#/tickets/TKT-142/ticket', {
      '/api/tickets/TKT-142': { body: TICKET },
      '/api/tickets/TKT-142/report': { status: 404, body: { error: { code: 'not_found' } } },
    })

    // the path, and the words, above the body
    const head = await screen.findByTestId('doc-path')
    expect(head).toHaveTextContent('/tmp/fixture/docs/tickets/TKT-142.md')
    expect(head).toHaveTextContent('read only')

    // the body is markdown, rendered
    const body = screen.getByTestId('doc-body')
    expect(body.querySelector('h1')).toHaveTextContent('Guard against flashing a stale build artifact')
    expect(body.querySelectorAll('ol li')).toHaveLength(2)
    expect(body.querySelector('code')).toHaveTextContent('build/')

    // the markdown decision, made executable: embedded HTML is ESCAPED — never executed,
    // never silently dropped. The reader sees exactly what is on disk.
    expect(body.querySelector('script')).toBeNull()
    expect(body.querySelector('b')).toBeNull()
    expect(body.textContent).toContain('<b>bold</b>')
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined()

    // there is no control anywhere on this surface that could write to the file
    const surface = screen.getByTestId('surface')
    expect(surface.querySelectorAll('textarea')).toHaveLength(0)
    expect(surface.querySelectorAll('input')).toHaveLength(0)
    expect(surface.querySelectorAll('[contenteditable]')).toHaveLength(0)
    for (const control of surface.querySelectorAll('button,a')) {
      expect(control.textContent ?? '').not.toMatch(/save|edit|write|delete|new |create/i)
    }

    // and the client is read-only on the wire: every request it made was a GET
    expect(calls.length).toBeGreaterThan(0)
    for (const call of calls) expect(call.method).toBe('GET')
  })

  it('a_ticket_with_no_document_names_the_path', async () => {
    await mountApp('#/tickets/TKT-404/ticket', {
      '/api/tickets/TKT-404': {
        status: 404,
        body: { error: { code: 'not_found', message: 'no ticket with id TKT-404',
                         detail: { path: '/tmp/fixture/docs/tickets' } } },
      },
      '/api/tickets/TKT-404/report': { status: 404, body: { error: { code: 'not_found' } } },
    })
    const empty = await screen.findByTestId('empty')
    expect(empty).toHaveTextContent('/tmp/fixture/docs/tickets')
    expect(empty.textContent).toMatch(/no document exists/i)
  })
})
