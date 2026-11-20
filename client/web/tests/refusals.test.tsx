import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent, within } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  MANIFEST, calls, callsTo, lane, mountApp, requirement, settle, ticketDoc, ticketRoutes,
  type Reply, type Routes,
} from './helpers'

/**
 * WEB-FEAT-179 — a manifest or git refusal is an instruction to go elsewhere, and should be an
 * action.
 *
 * Two families of refusal are precise, correct, and end with the person leaving the tool.
 *
 * The MANIFEST family names the exact JSON pointer that failed, and `services/factory.tsx` threw
 * the whole body away — `getManifest()` was read for its success alone — so the most actionable
 * sentence the server can produce reached no surface at all.
 *
 * The GIT family carries the facts that make it actionable: a count of uncommitted changes, every
 * path git was told to ignore, a branch name, a path. `ClaimControl` rendered `error.message` and
 * dropped the rest, and the only control it ever offered for them was `discard_ignored` — the
 * DESTRUCTIVE one. The tool offered the way to lose the files and not the way to keep them.
 *
 * Every word rendered here comes off the wire. SPEC.md §3.6's `remedy` is the action; §9.2's verbs
 * and §9.3's nouns are closed sets frozen by WEB-FEAT-173 and reaching this client through
 * `src/types/api.ts`, which is GENERATED. The client never infers a remedy from a code string, so
 * a body with no remedy renders no control — not a greyed one, which is a state that cannot be
 * ended wearing the costume of one that can.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

// ── the manifest family ────────────────────────────────────────────────────────────────────────

/** E2's refusal, in the C++'s own shape (`manifest::Store::errorJson`), with §11 check 12's remedy. */
const INVALID: Reply = {
  status: 422,
  body: {
    error: {
      code: 'invalid_manifest',
      message: 'this resource declares a device that is not on this machine; plug it in or remove it',
      detail: {
        path: '/repo/.factory/factory.json',
        reason: 'no such device',
        pointer: '/resources/board/devices/0',
        field: 'resources.board.devices',
      },
      remedy: {
        action: 'reload_manifest',
        target: {},
        label: 'Re-read the manifest',
        destroys: [],
      },
    },
  },
}

const RELOAD = '/api/manifest/reload'

const catalogue = (warnings: unknown[] = []): Routes => ({
  '/api/requirements': {
    status: 200,
    body: { requirements: [requirement()], total: 1, in_flight: 0, warnings },
  },
})

const writes = () => calls.filter((c) => c.method !== 'GET')

describe('WEB-FEAT-179 · a manifest refusal is an action', () => {
  it('test_a_manifest_refusal_shows_the_pointer_and_offers_a_reload', async () => {
    await mountApp('#/requirements', { '/api/manifest': INVALID, ...catalogue() })
    await settle()
    await settle()

    // The banner is in the SHELL and not on one surface: a manifest that did not load is the one
    // failure that makes the whole tool unusable rather than one lane.
    const banner = screen.getByTestId('manifest-banner')
    expect(banner).toHaveTextContent(
      'this resource declares a device that is not on this machine')

    // The pointer, VERBATIM. It is the whole of what makes this actionable — a person has to know
    // which key to open — and a client that reworded or truncated it would send them to the wrong
    // line of a file this tool may never write.
    expect(screen.getByTestId('manifest-banner-pointer').textContent)
      .toBe('/resources/board/devices/0')
    // ...and the file it points into, so there is no guessing WHICH document
    expect(banner).toHaveTextContent('/repo/.factory/factory.json')

    // The control is the server's remedy, with the server's own label. §3.6: the client owns tone
    // and placement, not text.
    const control = within(banner).getByTestId('remedy') as HTMLButtonElement
    expect(control).toHaveTextContent('Re-read the manifest')
    // it costs nothing, so it fires without a question
    expect(control.getAttribute('data-destroys')).toBe('')

    const before = writes().length
    await act(async () => { fireEvent.click(control) })
    await settle()

    // It RE-READS. One call, and it is the reload — nothing restarts, nothing is written, and the
    // client did not reach for the manifest file itself.
    const made = writes().slice(before)
    expect(made.map((c) => `${c.method} ${c.url}`)).toEqual([`POST ${RELOAD}`])
  })

  it('test_a_valid_manifest_after_a_reload_clears_the_banner_without_a_page_load', async () => {
    let valid = false
    const app = await mountApp('#/requirements', {
      '/api/manifest': () => (valid ? { status: 200, body: MANIFEST } : INVALID),
      [RELOAD]: () => (valid ? { status: 200, body: MANIFEST } : INVALID),
      ...catalogue(),
    })
    await settle()
    await settle()
    expect(screen.getByTestId('manifest-banner')).toBeTruthy()

    // Half-typed text in the filter box is the evidence for "without a page load": a reload of the
    // document would take it with it, and so would a remount of the tree. It is the same probe
    // `no-poll.test.tsx` uses, for the same reason.
    const draft = screen.getByPlaceholderText('filter requirements…') as HTMLInputElement
    await act(async () => { fireEvent.change(draft, { target: { value: 'half-typed' } }) })

    // the person fixed the file in their editor
    valid = true
    await act(async () => { fireEvent.click(screen.getByTestId('remedy')) })
    await settle()
    await settle()

    expect(screen.queryByTestId('manifest-banner')).toBeNull()
    expect((screen.getByPlaceholderText('filter requirements…') as HTMLInputElement).value)
      .toBe('half-typed')

    // ...and nothing was put on a clock to find out whether it worked. Ten minutes, no fetches.
    const settled = calls.length
    const commits = app.commits()
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()
    const pings = app.socket().frames().filter((f) => f.m === 'ping')
    expect(pings.length).toBeGreaterThanOrEqual(59)
    expect(calls.length).toBe(settled)
    expect(app.commits()).toBe(commits)
  })
})

// ── the git family ─────────────────────────────────────────────────────────────────────────────

const CLAIMED = 'lane/07'

/** E12's refusal when a leftover checkout is at the path — the door into E13's three. */
const IN_THE_WAY: Reply = {
  status: 409,
  body: {
    error: {
      code: 'worktree_in_the_way',
      message: 'something is already at the path this lane would use',
      detail: {
        path: `/repo/.factory/worktrees/${CLAIMED}`,
        git: "fatal: '...' already exists",
        ours: true, lane: CLAIMED, uncommitted_changes: 0, unmerged_commits: 0, ignored: [],
        clear_with: `DELETE /api/lanes/${CLAIMED}?force=true`,
      },
      remedy: {
        action: 'open_lane', target: { lane: CLAIMED }, label: 'Open the lane', destroys: [],
      },
    },
  },
}

interface Seen { claims: number; releases: Array<{ force: boolean; discardIgnored: boolean }> }

/**
 * The lane endpoints, as one fake server: E12 refuses the first claim with a leftover, E13 then
 * answers whatever the case is about.
 */
function laneRoutes(seen: Seen, release: Reply): Routes {
  return {
    '/api/lanes': (_url, init) => {
      if ((init?.method ?? 'GET') !== 'POST') return { body: { lanes: [], resources: [],
        seats: { host: { total: 3, used: 0 } } } }
      seen.claims += 1
      return IN_THE_WAY
    },
    [`/api/lanes/${CLAIMED}`]: (url, init) => {
      if ((init?.method ?? 'GET') !== 'DELETE') return { status: 405, body: {} }
      seen.releases.push({
        force: url.searchParams.get('force') === 'true',
        discardIgnored: url.searchParams.get('discard_ignored') === 'true',
      })
      return release
    },
    [`/api/lanes/${CLAIMED}/runs`]: { status: 201,
      body: { run_id: 'run-1', session_id: 'sess-1', pid: 4242 } },
  }
}

async function claimThenClear(seen: Seen, release: Reply) {
  await mountApp('#/tickets/TKT-142',
    { ...ticketRoutes(ticketDoc({ workspace: 'edge', lane: null }), []),
      ...laneRoutes(seen, release) })
  await settle()
  await settle()
  await act(async () => { fireEvent.click(screen.getAllByTestId('claim-lane')[0]) })
  await settle()
  await act(async () => { fireEvent.click(screen.getAllByTestId('claim-clear-leftover')[0]) })
  await settle()
  await settle()
}

describe('WEB-FEAT-179 · a git refusal offers the way that keeps the work', () => {
  it('test_uncommitted_changes_names_the_count_and_offers_the_non_destructive_way_first', async () => {
    // E13's first refusal. `uncommitted_changes: 3` is the fact that decides what a person does
    // next, and it reached the screen as the message alone — the same sentence for three changes
    // and for three hundred.
    const seen: Seen = { claims: 0, releases: [] }
    await claimThenClear(seen, {
      status: 409,
      body: {
        error: {
          code: 'uncommitted_changes',
          message: "this lane's leftover checkout holds work that is not committed; "
                 + 'it will not be removed',
          detail: { lane: CLAIMED, path: `/repo/.factory/worktrees/${CLAIMED}`, ours: true,
                    uncommitted_changes: 3 },
          // §11 check 12: `open_lane` → [] is the one the server sends. `release_lane` costs
          // `["session","worktree"]` and is not what a refusal about KEEPING work offers.
          remedy: { action: 'open_lane', target: { lane: CLAIMED },
                    label: 'Open the lane and look at the work', destroys: [] },
        },
      },
    })

    const block = screen.getByTestId('git-refusal')
    expect(block).toHaveTextContent(
      "this lane's leftover checkout holds work that is not committed")
    // the count, by name — not a count the client derived from a list it does not have
    expect(screen.getByTestId('git-refusal-count')).toHaveTextContent('3')

    // The control that KEEPS the work is the one offered, and it destroys nothing.
    const control = within(block).getByTestId('remedy') as HTMLButtonElement
    expect(control).toHaveTextContent('Open the lane and look at the work')
    expect(control.getAttribute('data-destroys')).toBe('')

    // ...and no destructive control is on this screen at all. There is nothing ignored to discard,
    // and `release_lane` is not offered by a refusal whose whole subject is work that survives.
    expect(screen.queryAllByTestId('claim-discard-ignored')).toHaveLength(0)
    expect(seen.releases).toEqual([{ force: true, discardIgnored: false }])
  })

  it('test_ignored_work_lists_the_paths_it_would_lose', async () => {
    // E13's second refusal names every path git was told to ignore, and NO surface showed them:
    // `ClaimControl` listed `detail.ignored` off E12's `worktree_in_the_way` and rendered E13's
    // own `ignored_work` as its message alone. A person met "it will not be removed" and no list.
    const seen: Seen = { claims: 0, releases: [] }
    const IGNORED = ['evidence/agent_app_1.log', 'evidence/build/', '.env.local']
    await claimThenClear(seen, {
      status: 409,
      body: {
        error: {
          code: 'ignored_work',
          message: "this lane's leftover checkout holds files git was told to ignore, which is "
                 + 'where work that cannot be got back lives; it will not be removed',
          detail: { lane: CLAIMED, path: `/repo/.factory/worktrees/${CLAIMED}`, ours: true,
                    ignored: IGNORED },
          remedy: { action: 'open_lane', target: { lane: CLAIMED },
                    label: 'Open the lane and look at the files', destroys: [] },
        },
      },
    })

    // every path, on the screen
    const named = screen.getByTestId('git-refusal-ignored')
    for (const path of IGNORED) expect(named).toHaveTextContent(path)

    // the way that keeps them is offered, and it is first
    const controls = screen.getAllByTestId('remedy')
    expect(controls[0]).toHaveTextContent('Open the lane and look at the files')
    expect(controls[0].getAttribute('data-destroys')).toBe('')

    // The destructive way exists and CANNOT FIRE from one click: the confirmation names what is
    // lost, in §9.3's nouns, and the release only happens after it.
    const destroy = screen.getByTestId('claim-discard-ignored') as HTMLButtonElement
    expect(destroy.getAttribute('data-destroys')).toBe('session worktree ignored_files')
    const before = seen.releases.length
    await act(async () => { fireEvent.click(destroy) })
    await settle()
    expect(seen.releases).toHaveLength(before)

    const confirm = screen.getByTestId('claim-discard-confirm')
    for (const path of IGNORED) expect(confirm).toHaveTextContent(path)
    expect(confirm).toHaveTextContent('files git was told to ignore')

    await act(async () => { fireEvent.click(within(confirm).getByTestId('claim-discard-fire')) })
    await settle()
    await settle()
    expect(seen.releases.slice(before)).toEqual([{ force: false, discardIgnored: true }])
  })

  it('test_ticket_not_on_branch_says_which_branch_and_which_path', async () => {
    // E18's refusal ends "commit it and start again" — a shell instruction printed by a tool. It
    // carries the branch and the path that would make it followable, and the composer rendered
    // the sentence alone.
    const LANE = 'lane/03'
    const rows = [lane({
      lane: LANE, ticket_id: 'TKT-142', workspace: 'core', branch: 'ticket/TKT-142',
      state: {
        process: 'not_started', motion: { value: null, silent_for_s: 0, since: null },
        evidence: 'nothing_claimed', blocked: null, alarm: false,
      },
      run_id: null, session_id: null, waiting_for: null,
    })]
    await mountApp('#/tickets/TKT-142/session', {
      ...ticketRoutes(ticketDoc({ workspace: 'core', lane: LANE }), rows),
      '/api/tickets/TKT-142/runs': { body: { runs: [], total: 0 } },
      [`/api/lanes/${LANE}/runs`]: {
        status: 409,
        body: {
          error: {
            code: 'ticket_not_on_branch',
            message: "this ticket is not on the lane's branch, so the agent could not open it; "
                   + 'commit it and start again',
            detail: {
              ticket_id: 'TKT-142',
              path: `/repo/.factory/worktrees/${LANE}/docs/tickets/TKT-142.md`,
              branch: 'ticket/TKT-142',
            },
            remedy: { action: 'retry', target: {}, label: 'Try again', destroys: [] },
          },
        },
      },
    })
    await settle()
    await settle()

    const box = screen.getByTestId('composer-draft') as HTMLTextAreaElement
    await act(async () => { fireEvent.change(box, { target: { value: 'go' } }) })
    await act(async () => { fireEvent.keyDown(box, { key: 'Enter' }) })
    await settle()
    await settle()

    const said = screen.getByTestId('composer-refused')
    // WHICH branch, and WHICH path — both from `detail`, neither composed by the client
    expect(said).toHaveTextContent('ticket/TKT-142')
    expect(said).toHaveTextContent(
      `/repo/.factory/worktrees/${LANE}/docs/tickets/TKT-142.md`)
  })
})

// ── the mandatory negative ─────────────────────────────────────────────────────────────────────

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..', 'src')

function sources(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...sources(full))
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full)
  }
  return out
}

/** `send('POST', '/api/…')` and `fetch(…, {method: …})` — every mutating call this client can make. */
const MUTATING = /\b(?:send|sendRaw)\s*<[^>]*>\s*\(\s*'(PUT|POST|PATCH|DELETE)'\s*,\s*([^)]*)/g

/**
 * The file with its comments removed, which is what a rule about what the client CAN DO has to
 * read.
 *
 * The first version of this case matched raw source and went red on `services/http.ts` — because
 * the doc comment there says "the verb is `reload_manifest` and never `fix_manifest`", which is the
 * sentence explaining the rule this check enforces. A guard that a file cannot state its own reason
 * without failing is a guard that teaches people to delete the reason. `SRV-FIX-068` is the same
 * shape pointing the other way: a source check defeated BY a comment. Both are the same fix — the
 * check reads code.
 *
 * `theme.test.tsx` already strips block comments for exactly this; line comments are stripped too,
 * because this repository writes both.
 */
const code = (text: string) => text
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split('\n')
  .filter((line) => !line.trimStart().startsWith('//'))
  .join('\n')

describe('MANDATORY NEGATIVE · the tool never writes the manifest', () => {
  it('test_the_tool_never_writes_the_manifest', () => {
    // Root CLAUDE.md: `.factory/factory.json` is "committed, hand-written, and never written by the
    // tool". The cheap fix for a manifest error is an in-app editor — a textarea and a PUT — and it
    // is cheap precisely because it looks like helping. It would make this client the author of a
    // document that is the repository's, and the reason the remedy is `reload_manifest` and never
    // `fix_manifest` (SPEC.md §9.2) is that one word.
    //
    // A source scan, because the rule is about what the client CAN do and not about which screen
    // was mounted in a test.
    const files = sources(SRC)
    expect(files.length).toBeGreaterThan(20)

    const offenders: string[] = []
    for (const file of files) {
      const text = code(readFileSync(file, 'utf8'))

      // 1. No code path names the manifest FILE. The client knows `/api/manifest`, an endpoint;
      //    a client that knows the path on disk is one edit from opening it.
      for (const [n, line] of text.split('\n').entries()) {
        if (line.includes('factory.json')) {
          offenders.push(`${file}:${n + 1} names factory.json in code`)
        }
      }

      // 2. No mutating request targets the manifest, except the ONE that re-reads it.
      for (const m of text.matchAll(MUTATING)) {
        const [, method, target] = m
        if (!/manifest/i.test(target)) continue
        const reload = /MANIFEST_RELOAD|['"`]\/api\/manifest\/reload['"`]/.test(target)
        if (!(method === 'POST' && reload)) {
          offenders.push(`${file}: ${method} at a manifest target — ${target.trim().slice(0, 60)}`)
        }
      }
    }
    expect(offenders, offenders.join('\n')).toEqual([])

    // 3. And no control offers it. The verbs this client can turn into a call are a closed set of
    //    SPEC.md §9.2's, and none of them writes anything: `fix_manifest` is not a verb, and a
    //    client that grew one would be adding a row here first.
    const http = code(readFileSync(join(SRC, 'services', 'http.ts'), 'utf8'))
    expect(http).not.toMatch(/fix_manifest|edit_manifest|write_manifest/)
    const remedy = code(readFileSync(join(SRC, 'components', 'Remedy.tsx'), 'utf8'))
    expect(remedy).not.toMatch(/fix_manifest|edit_manifest|write_manifest/)
  })

  it('and_a_manifest_refusal_on_the_screen_issues_no_write_but_the_reload', async () => {
    // The runtime half of the same rule: with the banner up, every control it offers is pressed,
    // and the only thing that leaves this client is the re-read.
    await mountApp('#/requirements', {
      '/api/manifest': INVALID,
      [RELOAD]: INVALID,
      ...catalogue(),
    })
    await settle()
    await settle()

    for (const control of screen.getAllByTestId('remedy')) {
      await act(async () => { fireEvent.click(control) })
      await settle()
    }
    // the banner is still up — the file on disk is still wrong, and the reload said so
    expect(screen.getByTestId('manifest-banner')).toBeTruthy()
    expect(writes().every((c) => c.method === 'POST' && c.url === RELOAD)).toBe(true)
    expect(callsTo(RELOAD).length).toBeGreaterThan(0)
  })
})
