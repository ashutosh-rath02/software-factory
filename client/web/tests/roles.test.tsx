import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act } from '@testing-library/react'

import { lane, mountApp, settle, ticketDoc, ticketRoutes } from './helpers'

/**
 * SPEC.md §4.5 — the roles this ticket has started or queued, in the header.
 *
 * `SRV-FEAT-022` made the tool start a QA session by itself. A role waiting for a seat sits in
 * E5's `roles:[{…,"state":"pending"}]`, which is a queue nobody can see — the exact failure `022`
 * was written to end, one layer up. These cases are about the half a person reads.
 *
 * The one that matters most is `a_pending_role_is_not_an_alarm`. §3.3 is five lane clauses plus
 * §3.5's two, a pending role is none of them, and a sixth added here is how a counter becomes
 * noise everyone learns to ignore.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-29T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** E5's `roles`, in the shape SRV-FEAT-022's report and SPEC.md E5 both give it. */
const STARTED = { role: 'qa', stage: 'GATE', state: 'started', lane: 'lane/02', run_id: 'run-2-aa4e017b' }
const PENDING = { role: 'reviewer', stage: 'GATE', state: 'pending' }

const TWO_LANES = [
  lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: false }),
  lane({ lane: 'lane/02', ticket_id: 'TKT-142', role: 'qa', needs_you: false }),
]

const roleEls = () => screen.getAllByTestId('role')
/** The inline custom properties a component would have to write to take a state's colour. */
const paints = (el: Element) => {
  const style = (el as HTMLElement).style
  return [style.getPropertyValue('--tone'), style.getPropertyValue('--tint')].filter(Boolean)
}

describe('4.5 the roles row', () => {
  it('a_started_role_names_its_lane', async () => {
    const scrolled: Element[] = []
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true, writable: true,
      value: function scrollIntoView(this: Element) { scrolled.push(this) },
    })

    await mountApp('#/tickets/TKT-142',
      ticketRoutes(ticketDoc({ roles: [STARTED] }), TWO_LANES))
    await settle()
    await settle()

    // the row is inside the header, after the identity row — a role is an identity fact
    const header = screen.getByTestId('ticket-header')
    const row = screen.getByTestId('roles-row')
    expect(header.contains(row)).toBe(true)
    expect(row.previousElementSibling).toBe(screen.getByTestId('ticket-identity'))

    // the repository's own word for the role, the stage that started it, and the lane it is in
    const [role] = roleEls()
    expect(role.textContent).toContain('qa')
    expect(role.textContent).toContain('GATE')
    expect(role.textContent).toContain('lane/02')
    expect(role.getAttribute('data-state')).toBe('started')
    expect(role.getAttribute('data-lane')).toBe('lane/02')

    // clicking it selects that lane: the row says which one, and the rail brings its card over
    expect(role.getAttribute('aria-pressed')).toBe('false')
    await act(async () => { (role as HTMLElement).click() })
    await settle()
    expect(roleEls()[0].getAttribute('aria-pressed')).toBe('true')

    const card = document.querySelector('[data-testid="lane-card"][data-lane="lane/02"]')
    expect(card).not.toBeNull()
    expect(scrolled).toEqual([card])
  })

  it('a_pending_role_says_it_is_waiting', async () => {
    await mountApp('#/tickets/TKT-142',
      ticketRoutes(ticketDoc({ roles: [STARTED, PENDING] }), TWO_LANES))
    await settle()
    await settle()

    const [started, pending] = roleEls()
    expect(pending.getAttribute('data-state')).toBe('pending')

    // distinguishable in WORDS, which is the point: neither one paints itself, so colour cannot
    // be doing the work even if a stylesheet later tried to
    expect(pending.textContent).toMatch(/waiting for a seat/i)
    expect(started.textContent).not.toMatch(/waiting/i)
    expect(paints(started)).toEqual([])
    expect(paints(pending)).toEqual([])

    // a pending role has no lane, so it is not a control — there is nothing to select
    expect(pending.tagName).not.toBe('BUTTON')
    expect(pending.hasAttribute('data-lane')).toBe(false)
    expect(started.tagName).toBe('BUTTON')
  })

  it('a_pending_role_is_not_an_alarm', async () => {
    // A ticket whose ONLY unusual fact is a role queued for a seat. Every lane the server reports
    // is quiet, and it says so itself: `needs_you:false` on both.
    await mountApp('#/tickets/TKT-142',
      ticketRoutes(ticketDoc({ roles: [PENDING] }), TWO_LANES))
    await settle()
    await settle()
    screen.getByTestId('roles-row')

    // ZERO. Not a chip reading `1`, not a chip reading `0` — §4.1's rule is that it is absent.
    expect(screen.queryByTestId('needs-you-chip')).toBeNull()
    expect(screen.getByTestId('top-bar').textContent).not.toMatch(/need you/)

    // and no border: the row takes neither the alarm tone nor its tint, and nothing in the header
    // claims to be an alarm
    const row = screen.getByTestId('roles-row')
    expect(paints(row)).toEqual([])
    expect(paints(roleEls()[0])).toEqual([])
    expect(roleEls()[0].getAttribute('data-alarm')).toBe('false')
    expect(screen.getByTestId('ticket-header').querySelectorAll('[data-alarm="true"]'))
      .toHaveLength(0)

    // the three axis chips are the three axis chips: a role did not become a fourth
    expect(screen.getAllByTestId('state-chip')).toHaveLength(3)
  })

  it('the_count_stays_the_servers_number_when_a_role_is_queued', async () => {
    // The other half of the clause: one lane that really does need a person, plus a pending role.
    // The counter says 1, because the server said 1.
    const rows = [
      lane({ lane: 'lane/01', ticket_id: 'TKT-142', needs_you: true }),
      lane({ lane: 'lane/02', ticket_id: 'TKT-142', role: 'qa', needs_you: false }),
    ]
    await mountApp('#/tickets/TKT-142',
      ticketRoutes(ticketDoc({ roles: [PENDING, STARTED] }), rows))
    await settle()
    await settle()

    expect(screen.getAllByTestId('role')).toHaveLength(2)
    expect(screen.getByTestId('needs-you-chip')).toHaveTextContent('1 lane needs you')
  })

  /**
   * MANDATORY NEGATIVE — the shape this repository's own manifest produces every day.
   *
   * `.factory/factory.json` declares no `roles`, deliberately (SRV-FEAT-022's report, note 4), so
   * every ticket in this repo comes back from E5 with no `roles` key at all. An always-present
   * empty container is how a header fills with things that never say anything.
   */
  it('a_ticket_with_no_roles_shows_no_row', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), TWO_LANES))
    await settle()
    await settle()

    const header = screen.getByTestId('ticket-header')
    expect(header.querySelector('[data-testid="roles-row"]')).toBeNull()
    expect(screen.queryByTestId('roles-row')).toBeNull()
    expect(screen.queryAllByTestId('role')).toHaveLength(0)

    // no heading, no `0 roles`, no empty container under any other name
    expect(header.textContent).not.toMatch(/role/i)
    expect(header.textContent).not.toMatch(/\b0 roles\b/)

    // the header is STRUCTURALLY what it was: identity row, chips row, strip row, and nothing
    // between them. A row rendered unconditionally fails here even while it renders empty.
    //
    // `cmd-row` is WEB-FEAT-046's, and it is here because these fixtures hold a lane: E19's two
    // controls, and — on the default fixture manifest, which declares neither `build` nor `gate`
    // for `core` — the note naming the two keys that would have to exist. It is listed rather than
    // filtered out so that this assertion keeps costing something: a row that renders EMPTY still
    // fails it, which is the rule this case exists for.
    const kids = Array.from(header.children)
    expect(kids.map((k) => k.className))
      .toEqual(['tkt-identity', 'chips-row', 'strip-row', 'cmd-row'])
    expect(header.querySelector('.cmd-row')?.textContent)
      .toBe('no build or gate declared · workspaces.core.build · workspaces.core.gate')
  })
})
