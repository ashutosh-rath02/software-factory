import { describe, it, expect, afterEach, vi } from 'vitest'
import { screen, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { mountApp } from './helpers'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const TICKET = {
  id: 'TKT-142', title: 'Guard against flashing a stale build artifact',
  workspace: 'core', status: 'todo', parent: 'REQ-014',
  path: '/tmp/fixture/docs/tickets/TKT-142.md', updated_iso: '2026-07-27T09:00:00Z',
  stages: ['SPEC', 'IMPL', 'GATE', 'DELIVER'],
  stage: { index: 0, total: 4, round: 1, of: 1, source: 'manifest' },
  extras: [], body_markdown: '# TKT-142\n', lane: null,
  runs: { count: 0, duration_s: 0, cost_usd: 0 },
  report: { exists: false, path: '/tmp/fixture/docs/reports/TKT-142-report.md',
            evidence: 'nothing_claimed' },
  needs_you: false,
  state: { process: 'not_started', motion: null, evidence: 'nothing_claimed' },
}

const report = (verdict: string, runId?: string) => ({
  ticket_id: 'TKT-142',
  path: '/tmp/fixture/docs/reports/TKT-142-report.md',
  updated_iso: '2026-07-27T09:30:00Z',
  body_markdown: '# TKT-142 — delivered\n\nAll four criteria pass.\n',
  evidence: runId ? { verdict, run_id: runId } : { verdict },
})

const routes = (reply: { status?: number; body: unknown }) => ({
  '/api/tickets/TKT-142': { body: TICKET },
  '/api/tickets/TKT-142/report': reply,
})

describe('4.9 REPORT.MD', () => {
  it('absent_is_not_an_error', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await mountApp('#/tickets/TKT-142/report', routes({
      status: 404,
      body: { error: { code: 'not_found', message: 'no delivery report for TKT-142',
                       detail: { path: '/tmp/fixture/docs/reports/TKT-142-report.md' } } },
    }))

    // the design's copy, verbatim
    const empty = await screen.findByTestId('report-empty')
    expect(empty).toHaveTextContent('NO DELIVERY REPORT')
    expect(empty).toHaveTextContent(
      'The agent has not claimed this ticket done. A report appears here the moment it does — '
      + 'with or without the evidence to back it.')

    // this 404 is the normal case. It is not logged as a failure.
    expect(error).not.toHaveBeenCalled()
    expect(warn).not.toHaveBeenCalled()
    expect(screen.queryByTestId('evidence-banner')).toBeNull()
  })

  it('a_present_report_carries_its_evidence_banner', async () => {
    await mountApp('#/tickets/TKT-142/report',
      routes({ body: report('claimed_proven', 'run-2026-07-27-3') }))

    const banner = await screen.findByTestId('evidence-banner')
    expect(banner).toHaveTextContent('cites run run-2026-07-27-3')
    expect(banner.getAttribute('data-alarm')).toBe('false')
    expect(screen.getByTestId('doc-body').querySelector('h1'))
      .toHaveTextContent('TKT-142 — delivered')
    expect(screen.getByTestId('doc-path'))
      .toHaveTextContent('/tmp/fixture/docs/reports/TKT-142-report.md')
  })

  it('an_unproven_verdict_carries_the_alarm_styling', async () => {
    await mountApp('#/tickets/TKT-142/report', routes({ body: report('claimed_unproven') }))

    const banner = await screen.findByTestId('evidence-banner')
    expect(banner).toHaveTextContent('claims done · cites no run')
    expect(banner.getAttribute('data-alarm')).toBe('true')

    // and the alarm attribute is not decoration: the stylesheet gives it the border
    // SPEC.md §3.2 promises. "Two combinations are alarms and carry a border."
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '../src/styles/app.css'), 'utf8')
    const rule = css.match(/\[data-alarm=['"]true['"]\][^}]*\{[^}]*\}/)
    expect(rule, 'app.css must give [data-alarm="true"] its border').not.toBeNull()
    expect(rule![0]).toMatch(/border/)
  })
})
