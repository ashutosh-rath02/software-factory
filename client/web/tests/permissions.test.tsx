import { describe, it, expect, afterEach, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { PermissionRequestBlock } from '../src/components/PermissionRequest'
import { askFromEntry } from '../src/services/permissions'
import { FactoryProvider } from '../src/services/factory'
import type { TranscriptEntry } from '../src/types/socket'
import {
  FakeSocket, HEALTH, MANIFEST, calls, callsTo, lane, mountApp, settle, stubFetch, ticketDoc,
  ticketRoutes,
} from './helpers'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

const HERE = dirname(fileURLToPath(import.meta.url))
/**
 * A source file with its comments stripped — `theme.test.tsx`'s own technique, for the same
 * reason: the component's doc comment NAMES the four mechanisms it may not use, and a grep that
 * counted prose would fail on the sentence promising the thing it is checking for.
 */
const source = (rel: string) =>
  readFileSync(join(HERE, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')

/**
 * SPEC.md §4.12 — the permissions surface.
 *
 * Three parts and one word in the top bar, and the ticket is right that they only work together:
 * `allow always` is one click, ten clicks make a policy nobody can state, and a policy nobody can
 * read is indistinguishable from `never ask`.
 *
 * Everything here is driven against FIXTURES answering the frozen contract — E11's body, M6's
 * frame and M12's broadcast — because the server half (SRV-FEAT-017) is merged and its shapes are
 * the ones `docs/technical/contracts/http.schema.json` and `docs/technical/contracts/socket.schema.json` already declare.
 */

/** E11's rule, in the six fields socket.schema.json:418-433 and the HTTP schema both require. */
function rule(over: Record<string, unknown> = {}) {
  return {
    rule_id: 'rule-6b1f0a3c', pattern: 'Bash(npm run build)', source: 'granted',
    granted_iso: '2026-07-27T09:41:12Z', match_count: 3, revocable: true,
    ...over,
  }
}

const MANIFEST_RULE = rule({
  rule_id: 'rule-manifest-1', pattern: 'Read(src/*)', source: 'manifest',
  granted_iso: null, match_count: 11, revocable: false,
})

/** E11's body. `policy_source` is `manifest` until E21 has been used in this tool process. */
function rulesBody(over: Record<string, unknown> = {}) {
  return {
    policy: 'ask_all', policy_source: 'manifest', rules: [rule(), MANIFEST_RULE], warnings: [],
    ...over,
  }
}

const RULES_PATH = '/api/permissions/rules'

/** M6's payload, as a transcript entry — the shape WEB-FEAT-014's transcript will hand the block.
 *
 * `seq` and `ts` are here from SRV-FIX-049 on, because the schema now requires them of an entry:
 * an entry is E8's `{seq,ts,kind,…payload}`, and the socket refuses to call anything else one.
 * A fixture that could not have come off the wire is a fixture that proves less than it claims.
 */
function askEntry(over: Record<string, unknown> = {}): TranscriptEntry {
  return {
    seq: 7, ts: '2026-07-30T00:00:00Z',
    kind: 'permission_request',
    request_id: 'req-74a9d0a7', lane: 'lane/01', tool: 'Bash',
    input_summary: 'npm run build',
    rule_that_would_be_written: 'Bash(npm run build)',
    expires_at: null,
    ...over,
  }
}

const NEAREST_MISS = {
  rule_id: 'rule-manifest-1', pattern: 'Bash(npm run test:*)', source: 'manifest',
  differs_on: 'argument', matched_prefix: 'npm run ',
  why_it_did_not_match:
    'the rule matches this argument up to `npm run `, then requires `test:` where the argument '
    + 'has `build`',
}

/**
 * The block on its own, inside the socket provider — which is what makes M7 reach it.
 *
 * A component test rather than a page one, because the request is an entry the TRANSCRIPT renders
 * (WEB-FEAT-014, in a worktree beside this one). This lane owns the component and the reader that
 * turns an entry into its props; the wiring is one line at integration.
 */
async function mountRequest(entry: TranscriptEntry = askEntry(),
                            routes: Record<string, { status?: number; body: unknown }> = {}) {
  FakeSocket.all = []
  stubFetch({ '/api/health': { body: HEALTH }, '/api/manifest': { body: MANIFEST }, ...routes })
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)

  const ask = askFromEntry(entry)
  expect(ask, 'the fixture entry is a permission_request the reader accepts').not.toBeNull()
  await act(async () => {
    render(<FactoryProvider><PermissionRequestBlock ask={ask!} /></FactoryProvider>)
  })
  await act(async () => { FakeSocket.last?.onopen?.() })
  await settle()
  return { socket: () => FakeSocket.last }
}

const decisions = () => calls.filter((c) => c.url.includes('/api/permissions/decisions'))
const policyWrites = () => calls.filter((c) => c.url.includes('/api/permissions/policy'))

describe('SPEC.md §4.12 · the permissions surface', () => {
  it('policy_names_its_consequence', async () => {
    // F22's client half. Three options, one line of what each MEANS — the value alone is
    // unreadable, and `edit_free` does not say which tools are free.
    await mountApp('#/permissions', {
      [RULES_PATH]: { body: rulesBody() },
      '/api/permissions/policy': { body: { policy: 'never_ask', applies_to: 'new_runs' } },
    })
    await screen.findByTestId('policy-control')

    const options = screen.getAllByTestId('policy-option')
    expect(options.map((o) => o.getAttribute('data-policy')))
      .toEqual(['ask_all', 'edit_free', 'never_ask'])

    const lines = screen.getAllByTestId('policy-consequence').map((n) => n.textContent?.trim() ?? '')
    expect(lines).toHaveLength(3)
    for (const line of lines) expect(line.length).toBeGreaterThan(20)
    expect(new Set(lines).size, 'three options, three different consequences').toBe(3)
    expect(lines[1]).toMatch(/command/i)

    // E11 said the manifest's value, and that is what is selected before anybody touches it
    const chosen = screen.getByTestId('policy-input-ask_all') as HTMLInputElement
    expect(chosen.checked).toBe(true)

    // the control says out loud that a change applies to lanes started AFTERWARDS — standing,
    // not only after a change, because it is the thing a person needs before they click
    expect(screen.getByTestId('policy-note').textContent)
      .toMatch(/applies to lanes started afterwards/i)

    await act(async () => {
      fireEvent.click(screen.getByTestId('policy-input-never_ask'))
    })
    await settle()

    expect(policyWrites()).toHaveLength(1)
    expect(policyWrites()[0].method).toBe('PUT')
    // and the change says it again, attached to the value that just changed
    const applied = await screen.findByTestId('policy-applied')
    expect(applied.textContent).toMatch(/never ask/i)
    expect(applied.textContent).toMatch(/applies to lanes started afterwards/i)
  })

  it('the_request_shows_the_rule_before_you_grant_it', async () => {
    await mountRequest(askEntry({ nearest_miss: NEAREST_MISS }))

    const block = screen.getByTestId('permission-request')
    expect(block.textContent).toContain('Bash')
    expect(block.textContent).toContain('npm run build')

    const written = screen.getByTestId('request-rule')
    expect(written.textContent).toContain('Bash(npm run build)')

    const miss = screen.getByTestId('request-miss')
    expect(miss.textContent).toContain('Bash(npm run test:*)')
    expect(miss.textContent).toContain('then requires `test:` where the argument has `build`')

    // BEFORE the buttons, not merely present: a rule shown underneath the button that writes it
    // is a rule shown after the decision.
    const always = screen.getByTestId('allow-always')
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING
    expect(written.compareDocumentPosition(always) & FOLLOWING).toBeTruthy()
    expect(miss.compareDocumentPosition(always) & FOLLOWING).toBeTruthy()

    // three buttons, and each says what it does
    expect(screen.getByTestId('allow-once').textContent).toMatch(/allow once/i)
    expect(always.textContent).toMatch(/allow always/i)
    expect(screen.getByTestId('deny').textContent).toMatch(/deny/i)

    // and a request nothing came close to says nothing about a near miss at all
    cleanup()
    await mountRequest(askEntry())
    expect(screen.queryByTestId('request-miss')).toBeNull()
    expect(screen.getByTestId('request-rule').textContent).toContain('Bash(npm run build)')
  })

  it('granted_rules_are_listed_and_revocable', async () => {
    await mountApp('#/permissions', {
      [RULES_PATH]: { body: rulesBody() },
      '/api/permissions/rules/rule-6b1f0a3c': { body: { revoked: true, remaining: 1 } },
    })
    await screen.findByTestId('rules-table')

    const rows = screen.getAllByTestId('rule-row')
    expect(rows).toHaveLength(2)

    const granted = rows.find((r) => r.getAttribute('data-rule') === 'rule-6b1f0a3c')!
    expect(granted.textContent).toContain('Bash(npm run build)')
    expect(granted.textContent).toContain('granted')
    expect(granted.textContent).toContain('2026-07-27')
    expect(granted.textContent, 'the match count, so a rule nobody uses can be found')
      .toMatch(/3 matches/)

    // a manifest rule says WHY it cannot be revoked, and links to the file that declares it
    const declared = rows.find((r) => r.getAttribute('data-rule') === 'rule-manifest-1')!
    expect(declared.textContent).toContain('declared in the manifest')
    expect(declared.querySelector('[data-testid="revoke"]'),
      'a manifest rule offers no revoke that would lie').toBeNull()
    const link = declared.querySelector('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toContain('.factory/factory.json')

    // revoke: one DELETE, and the list changes because M12 said it did — not a refetch
    const before = callsTo(RULES_PATH).length
    await act(async () => { fireEvent.click(granted.querySelector('[data-testid="revoke"]')!) })
    await settle()

    const del = calls.filter((c) => c.url.includes('/api/permissions/rules/rule-6b1f0a3c'))
    expect(del).toHaveLength(1)
    expect(del[0].method).toBe('DELETE')

    await act(async () => {
      FakeSocket.last.deliver({
        m: 'rules', ts: '2026-07-27T10:00:00Z', seq: null,
        policy: 'ask_all', rules: [MANIFEST_RULE],
      })
    })
    await settle()

    expect(screen.getAllByTestId('rule-row')).toHaveLength(1)
    expect(screen.queryByText('Bash(npm run build)')).toBeNull()
    expect(callsTo(RULES_PATH).length, 'the broadcast is the update; nothing refetched')
      .toBe(before)
  })

  it('answering_in_one_tab_resolves_the_other', async () => {
    // M7 is a BROADCAST, not a routed frame, exactly so that a request answered in one tab stops
    // waiting in the other. A correct agent waiting on a person who already answered is a lane
    // burning nothing for no reason.
    const mounted = await mountRequest(askEntry({ nearest_miss: NEAREST_MISS }))
    expect(screen.getByTestId('permission-request').getAttribute('data-pending')).toBe('true')

    const before = calls.length
    await act(async () => {
      mounted.socket().deliver({
        m: 'permission.resolved', ts: '2026-07-27T10:00:00Z', seq: null,
        lane: 'lane/01', request_id: 'req-74a9d0a7', decision: 'always',
        rule_id: 'rule-6b1f0a3c',
      })
    })
    await settle()

    const block = screen.getByTestId('permission-request')
    expect(block.getAttribute('data-pending')).toBe('false')
    expect(screen.queryByTestId('allow-always'), 'no button left to press twice').toBeNull()
    expect(screen.queryByTestId('allow-once')).toBeNull()
    expect(screen.queryByTestId('deny')).toBeNull()
    expect(screen.getByTestId('request-resolved').textContent).toMatch(/answered elsewhere/i)
    expect(calls.length, 'without a refetch').toBe(before)

    // and a frame about a DIFFERENT request leaves this one waiting
    cleanup()
    const other = await mountRequest(askEntry())
    await act(async () => {
      other.socket().deliver({
        m: 'permission.resolved', ts: '2026-07-27T10:00:00Z', seq: null,
        lane: 'lane/01', request_id: 'req-somebody-else', decision: 'deny',
      })
    })
    await settle()
    expect(screen.getByTestId('permission-request').getAttribute('data-pending')).toBe('true')
  })

  it('a_blocked_lane_reads_as_blocked', async () => {
    // SPEC.md §3.2's `blocked`: alive, mid-turn, and cannot continue until a person answers. A
    // correct agent waiting on a person is not a stalled one, and it is not a silent one either.
    const blocked = lane({
      state: {
        process: 'blocked',
        motion: { value: null, silent_for_s: 0, since: null },
        evidence: 'nothing_claimed',
        blocked: { reason: 'permission', request_id: 'req-74a9d0a7' },
        alarm: false,
      },
      needs_you: true,
    })
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), [blocked]))
    await screen.findByTestId('lane-card')

    const card = screen.getByTestId('lane-card')
    const state = card.querySelector('.lane-state')!.textContent!.trim()
    expect(state).toBe('blocked')
    expect(state).not.toContain('silent')
    expect(screen.getByTestId('lane-sub').textContent)
      .toMatch(/waiting on your answer · permission/)

    const chips = screen.getAllByTestId('state-chip')
    const process = chips.find((c) => c.getAttribute('data-axis') === 'process')!
    expect(process.textContent).toBe('PROCESS · BLOCKED')
    const motion = chips.find((c) => c.getAttribute('data-axis') === 'motion')!
    expect(motion.textContent, 'the axes are never flattened').toBe('MOTION · —')

    // and the person is told, in the one place that is on every screen
    expect(screen.getByTestId('needs-you-chip').textContent).toMatch(/1 lane needs you/)
  })

  it('nothing_grants_itself', async () => {
    // MANDATORY NEGATIVE. `Allow always` writes a permanent rule into a file a human commits, and
    // a dialog that grants one because a key was already held is how a policy nobody stated gets
    // written. Only a deliberate click on the control whose label says so may reach it.
    vi.useFakeTimers({ shouldAdvanceTime: false })
    await mountRequest(askEntry({ nearest_miss: NEAREST_MISS }), {
      '/api/permissions/decisions': { body: { decision: 'always', unblocked: true } },
    })

    const block = screen.getByTestId('permission-request')
    const always = screen.getByTestId('allow-always')

    // 1 · nothing is focused, so a key already held lands nowhere
    expect(document.activeElement).toBe(document.body)
    expect(always).not.toBe(document.activeElement)
    for (const id of ['allow-once', 'allow-always', 'deny']) {
      expect(screen.getByTestId(id).hasAttribute('autofocus'), `${id} autoFocus`).toBe(false)
      expect(screen.getByTestId(id).getAttribute('accesskey'), `${id} accessKey`).toBeNull()
    }

    // 2 · no key reaches it — not on the window, not on the block, not on the button itself
    for (const target of [window, document, block, always]) {
      for (const key of ['Enter', ' ', 'y', 'a', 'Escape']) {
        await act(async () => { fireEvent.keyDown(target as Element, { key }) })
        await act(async () => { fireEvent.keyUp(target as Element, { key }) })
      }
    }
    await settle()
    expect(decisions(), 'a key press decided something').toHaveLength(0)
    expect(policyWrites()).toHaveLength(0)

    // 3 · nothing auto-dismisses. M6's `expires_at` is null on purpose: a request that times out
    // into a denial produces a run that failed for a reason nobody saw.
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()
    expect(decisions()).toHaveLength(0)
    expect(screen.getByTestId('permission-request').getAttribute('data-pending')).toBe('true')

    // 4 · and the component contains none of the four mechanisms that would break the three above
    const component = source('../src/components/PermissionRequest.tsx')
    expect(component).not.toMatch(/onKeyDown|onKeyUp|onKeyPress/)
    expect(component).not.toMatch(/autoFocus/)
    expect(component).not.toMatch(/accessKey/)
    expect(component).not.toMatch(/setTimeout|setInterval/)
    expect(component).not.toMatch(/\.focus\(\)/)

    // 5 · the control: a deliberate click DOES reach it, or the four above prove nothing
    await act(async () => { fireEvent.click(always) })
    await settle()
    expect(decisions()).toHaveLength(1)
    expect(decisions()[0].method).toBe('POST')
  })

  it('the_top_bar_reaches_the_screen', async () => {
    // §8 D4's one addition to the design's chrome: a revocable list nobody can reach is not
    // revocable. Styled exactly as VOCABULARY — asserted as the same class, not as a similar look.
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: [], total: 0, in_flight: 0, warnings: [] } },
    })
    const link = screen.getByText('PERMISSIONS') as HTMLAnchorElement
    const vocabulary = screen.getByText('VOCABULARY') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#/permissions')
    expect(link.className).toBe(vocabulary.className)

    // and the word goes somewhere: the route renders the surface, with its own breadcrumb
    cleanup()
    await mountApp('#/permissions', { [RULES_PATH]: { body: rulesBody({ rules: [] }) } })
    await screen.findByTestId('policy-control')
    expect(screen.getByTestId('breadcrumb').textContent).toContain('permissions')

    // §4.12's empty state, which is not a blank table
    expect(screen.getByTestId('rules-empty').textContent)
      .toMatch(/Nothing has been granted\. Every request will be asked\./)
  })

  it('a_lost_socket_disables_every_write', async () => {
    // §4.12's own socket-down rule. The table is last-known and dimmed; revoke and the policy
    // change are disabled — a write whose outcome cannot be observed is worse than one that did
    // not happen, and this surface's outcome arrives on the socket.
    const app = await mountApp('#/permissions', { [RULES_PATH]: { body: rulesBody() } })
    await screen.findByTestId('rules-table')

    await act(async () => { app.socket().onclose?.() })
    await settle()

    expect(screen.getByTestId('rules-table').getAttribute('data-dim')).toBe('true')
    expect((screen.getAllByTestId('revoke')[0] as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId('policy-input-never_ask') as HTMLInputElement).disabled).toBe(true)

    // the rows are still there — nothing is cleared and nothing is refetched behind the back
    expect(screen.getAllByTestId('rule-row')).toHaveLength(2)
    expect(callsTo(RULES_PATH)).toHaveLength(1)
  })

  it('the_surface_never_polls', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const app = await mountApp('#/permissions', { [RULES_PATH]: { body: rulesBody() } })
    await settle()
    // getBy, not findBy: findBy waits on a timer, and the timers here are frozen on purpose
    screen.getByTestId('rules-table')

    const mountCommits = app.commits()
    await act(async () => { vi.advanceTimersByTime(1000) })
    await settle()
    const commitsBefore = app.commits()
    expect(commitsBefore).toBeLessThanOrEqual(mountCommits + 1)

    const fetches = calls.length
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved — the socket's 10 s ping is the only timer in the system
    expect(app.socket().frames().filter((f) => f.m === 'ping').length).toBeGreaterThanOrEqual(59)
    expect(calls.length).toBe(fetches)
    expect(callsTo(RULES_PATH)).toHaveLength(1)
    expect(app.commits()).toBe(commitsBefore)
  })
})
