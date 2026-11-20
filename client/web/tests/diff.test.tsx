import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, within } from '@testing-library/react'

import { mountApp, settle, ticketDoc, ticketRoutes, lane as laneRow, calls } from './helpers'

/**
 * §8 D3 · F23 — the fifth tab.
 *
 * "Deciding whether to keep the work is the point of the exercise, and it cannot be done from a
 * transcript." So the assertion is that the files, the counts, the commits and one file's patch
 * are all reachable WITHOUT LEAVING the ticket screen.
 */

/** E10, in the shape `app/lane/diff.cpp` actually serves. */
const DIFF = {
  lane: 'lane/01', branch: 'ticket/TKT-142', base: 'a1b2c3d', base_branch: 'main',
  files: [
    { path: 'core/tasks/build.py', status: 'modified', added: 18, removed: 6, committed: true },
    { path: 'tests/test_build.py', status: 'added', added: 3, removed: 0, committed: false },
  ],
  commits: [
    { sha: '9f2c1ab4d5e6f708192a3b4c5d6e7f8091a2b3c4',
      subject: 'TKT-142: refuse a stale artifact', iso: '2026-07-28T13:02:00Z' },
  ],
  totals: { files: 2, added: 21, removed: 6, commits: 1 },
}

const PATCH = '--- a/core/tasks/build.py\n+++ b/core/tasks/build.py\n@@ -1,3 +1,4 @@\n+import hashlib\n'

/** `lane/01` percent-encoded — one path segment, which is what `api::laneNameOf` decodes. */
const DIFF_PATH = '/api/lanes/lane%2F01/diff'

function routes(body: unknown = DIFF) {
  const doc = ticketDoc({ lane: 'lane/01' })
  return {
    ...ticketRoutes(doc, [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142' })]),
    [DIFF_PATH]: (url: URL) => (url.searchParams.get('path')
      ? { body: { ...(body as object), patch: PATCH } }
      : { body }),
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T14:10:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('the DIFF tab', () => {
  it('files_commits_and_a_patch', async () => {
    const app = await mountApp('#/tickets/TKT-142/diff', routes())
    await settle()
    await settle()

    // the two files, each with its own +n/−n
    const files = screen.getAllByTestId('diff-file')
    expect(files).toHaveLength(2)
    expect(within(files[0]).getByTestId('diff-path')).toHaveTextContent('core/tasks/build.py')
    expect(within(files[0]).getByTestId('diff-added')).toHaveTextContent('+18')
    expect(within(files[0]).getByTestId('diff-removed')).toHaveTextContent('−6')
    expect(within(files[1]).getByTestId('diff-path')).toHaveTextContent('tests/test_build.py')
    expect(within(files[1]).getByTestId('diff-added')).toHaveTextContent('+3')
    expect(within(files[1]).getByTestId('diff-removed')).toHaveTextContent('−0')

    // the commit, with its subject and its sha
    const commit = screen.getByTestId('diff-commit')
    expect(commit).toHaveTextContent('TKT-142: refuse a stale artifact')
    expect(commit).toHaveTextContent('9f2c1ab')

    // the totals, and the tab that counts them (§8 D3's `DIFF · N files`)
    expect(screen.getByTestId('diff-totals')).toHaveTextContent('2 files · +21 −6 · 1 commit')

    // The tab ROW is the page's render and a tab cannot re-render its page, so the count the tab
    // fetched reaches the label on the page's next render — here the metrics frame a live lane
    // sends anyway. The label never claims a count it has not read. WEB-FIX-036 is the seam fix.
    expect(screen.getByRole('tab', { name: /DIFF/ })).toHaveTextContent('DIFF')
    await act(async () => {
      app.socket().deliver({ m: 'lane.metrics', ts: '2026-07-28T14:10:05Z', seq: null,
                             lane: 'lane/01', elapsed_s: 800, cost_usd: 0.81,
                             turn: { n: 2, cost_usd: 0.07 } })
    })
    await settle()
    expect(screen.getByRole('tab', { name: /DIFF/ })).toHaveTextContent('DIFF · 2 files')

    // one file's patch, on demand, with `?path=` — and still on the ticket screen
    await act(async () => { within(files[0]).getByTestId('diff-open').click() })
    await settle()
    await settle()

    const asked = calls.map((c) => c.url).filter((u) => u.includes('/diff?'))
    expect(asked).toHaveLength(1)
    expect(new URL(asked[0], 'http://factory.test').searchParams.get('path'))
      .toBe('core/tasks/build.py')
    expect(screen.getByTestId('diff-patch')).toHaveTextContent('import hashlib')

    // F23: without leaving the ticket screen
    expect(location.hash).toBe('#/tickets/TKT-142/diff')
    expect(screen.getByTestId('ticket-header')).toBeTruthy()
    expect(screen.getByTestId('surface')).toBeTruthy()
  })

  it('a_lane_that_changed_nothing', async () => {
    await mountApp('#/tickets/TKT-142/diff', routes({
      lane: 'lane/01', branch: 'ticket/TKT-142', base: 'a1b2c3d', base_branch: 'main',
      files: [], commits: [], totals: { files: 0, added: 0, removed: 0, commits: 0 },
    }))
    await settle()
    await settle()

    expect(screen.getByTestId('diff-empty')).toHaveTextContent('Nothing changed in this lane yet.')
    expect(screen.queryAllByTestId('diff-file')).toHaveLength(0)
    expect(screen.queryAllByTestId('diff-commit')).toHaveLength(0)
  })

  it('no_lane_is_not_a_failed_request', async () => {
    // A ticket nobody has claimed a lane for: there is no branch to compare, and E10 is never
    // asked. Rendering an error here would teach a person that an unstarted ticket is broken.
    await mountApp('#/tickets/TKT-142/diff', {
      ...ticketRoutes(ticketDoc({ lane: null }), []),
    })
    await settle()
    await settle()

    expect(screen.getByTestId('diff-no-lane')).toHaveTextContent('No lane is claimed')
    expect(calls.filter((c) => c.url.includes('/diff'))).toHaveLength(0)
  })
})
