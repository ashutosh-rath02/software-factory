import { readFileSync } from 'node:fs'
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import { mountApp, settle, ticketDoc, ticketRoutes } from './helpers'
import { TABS, labelOf, type TabProps } from '../src/components/tabs'

/**
 * The tab row is a REGISTRY, and this is the case that keeps it one.
 *
 * Four later tickets add a tab each — SESSION (`WEB-FEAT-014`), RUNS (`016`), DIFF (§8 D3) and
 * whatever `018` needs — and they are meant to run in parallel. They can only do that if adding a
 * tab is one row in one table: two tickets editing `TicketPage.tsx` is a conflict, and three is a
 * merge nobody can review. So the assertion is not "the tabs render" — it is "adding one requires
 * no edit to any file another ticket is also editing".
 */

// Relative to the client, which is vitest's working directory. `import.meta.url` is not a file
// URL in the jsdom environment these cases run in.
const SOURCE = 'src/pages/TicketPage.tsx'
const ROUTES = 'src/routes.ts'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('the tab registry', () => {
  it('a_tab_is_one_row_and_one_component', async () => {
    // ── 1 · one row is exactly a label and a component. Nothing else, for anybody.
    for (const [id, definition] of Object.entries(TABS)) {
      expect(Object.keys(definition).sort(), `tab ${id}`).toEqual(['component', 'label'])
      expect(['string', 'function']).toContain(typeof definition.label)
      expect(typeof definition.component).toBe('function')
    }

    // ── 2 · the page renders whatever is in the table, in declaration order
    const declared = Object.keys(TABS)
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc()))
    await settle()
    await settle()
    screen.getByTestId('ticket-header')

    let tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.getAttribute('data-tab'))).toEqual(declared)
    // Against the props the PAGE passes, not against an empty object: a label may be a function
    // of them (`RUNS · 4`, `DIFF · N files` — the whole reason the field takes one), and comparing
    // it to what it says about nothing would assert that no label may ever count anything.
    const passed: TabProps = {
      ticketId: 'TKT-142', lane: null,
      ticket: { result: { ok: true, status: 200, data: ticketDoc() as never }, loading: false },
      // E7 is not stubbed on this route, so the screen's history could not be read — which is
      // NOT a ticket with no runs, and the label falls back to the document exactly as the page
      // does (`WEB-FIX-091`).
      runs: { rows: [], loading: false, known: false, stamps: {} },
    }
    expect(tabs.map((t) => t.textContent))
      .toEqual(declared.map((id) => labelOf(id, passed)))
    cleanup()

    // ── 3 · adding a tab is ONE ROW. No edit to TicketPage.tsx, no edit to routes.ts, no
    //        second list to keep in step — which is what lets four tickets add four tabs at once.
    const MARK = 'the fifth tab nobody edited a page for'
    TABS.spike = {
      label: 'SPIKE · 1',
      component: () => <div data-testid="spike-tab">{MARK}</div>,
    }
    try {
      await mountApp('#/tickets/TKT-142/spike', ticketRoutes(ticketDoc()))
      await settle()
      await settle()
      screen.getByTestId('ticket-header')

      tabs = screen.getAllByRole('tab')
      expect(tabs.map((t) => t.getAttribute('data-tab'))).toEqual([...declared, 'spike'])
      const added = tabs[tabs.length - 1]
      expect(added).toHaveTextContent('SPIKE · 1')
      expect(added.getAttribute('aria-selected')).toBe('true')
      expect(screen.getByTestId('spike-tab')).toHaveTextContent(MARK)
    } finally {
      delete TABS.spike
    }
    cleanup()

    // ── 4 · and the proof it is a seam rather than a table that happens to exist: neither the
    //        page nor the router knows a tab's label, or its component, or that it exists. A grep
    //        is the assertion, because a grep is what a reviewer would run.
    const page = readFileSync(SOURCE, 'utf8')
    const router = readFileSync(ROUTES, 'utf8')
    for (const id of declared) {
      const label = TABS[id].label
      const text = typeof label === 'function' ? label({} as TabProps) : label
      expect(page, `TicketPage.tsx must not carry the label of ${id}`).not.toContain(text)
      expect(router, `routes.ts must not carry the label of ${id}`).not.toContain(text)
      const component = TABS[id].component.name
      expect(component, `tab ${id}'s component must be named`).not.toBe('')
      expect(page, `TicketPage.tsx must not name ${component}`).not.toContain(component)
    }
    // the router carries the segment through as an opaque string; it decides nothing, and it
    // cannot decide anything, because it does not import the table that would let it
    expect(router).toMatch(/tab\??:\s*string/)
    expect(router).not.toMatch(/^import[^\n]*['"][^'"]*tabs['"]/m)
    expect(page).not.toMatch(/^import[^\n]*['"][^'"]*DocTabs['"]/m)
  })

  it('an_unknown_tab_falls_back_to_the_first_row_rather_than_a_blank_page', async () => {
    // A link from an older build, or a hand-typed hash. The table's first row is the default and
    // no id is written down anywhere to make that true.
    const first = Object.keys(TABS)[0]
    await mountApp('#/tickets/TKT-142/no-such-tab', ticketRoutes(ticketDoc()))
    await settle()
    await settle()
    screen.getByTestId('ticket-header')

    const selected = screen.getAllByRole('tab').filter((t) => t.getAttribute('aria-selected') === 'true')
    expect(selected).toHaveLength(1)
    expect(selected[0].getAttribute('data-tab')).toBe(first)
  })

  it('a_tab_reads_the_ticket_the_page_already_read', async () => {
    // TabProps carries the ALREADY-READ E5 endpoint, so a second tab is not a second request.
    // WEB-FEAT-014 and 016 both need the document; neither should fetch it again.
    const seen: TabProps[] = []
    TABS.spy = { label: 'SPY', component: (props: TabProps) => { seen.push(props); return null } }
    try {
      await mountApp('#/tickets/TKT-142/spy', ticketRoutes(ticketDoc(), []))
      await settle()
      await settle()
      screen.getByTestId('ticket-header')

      expect(seen.length).toBeGreaterThan(0)
      const last = seen[seen.length - 1]
      expect(last.ticketId).toBe('TKT-142')
      expect(last.ticket.result?.ok).toBe(true)
      expect(last.lane).toBe(null)
      await act(async () => {})
    } finally {
      delete TABS.spy
    }
  })
})
