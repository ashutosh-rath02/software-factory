import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { CHANGE_WAIT, settleOnLoaded } from '../gate/wait.mjs'

/**
 * SRV-FEAT-165 round 2 — the assertion QA's FAIL says was missing.
 *
 * Transcribed from the verdict's own reproduction (`factory/docs/reports/SRV-FEAT-165-qa.md`,
 * finding 1), not from the criterion's name. QA sampled the row count every 25 ms across the click
 * on the real client and published the trajectory:
 *
 *   row-count trajectory after the click: 0,2,2,2,2,2,2,2,2,2, …
 *   first value a "length !== before" wait would see: 2
 *
 * The leading `0` is the loading state, and it is not a race: `useEndpoint` clears its result
 * whenever its key changes, so it happens on EVERY filter press. What raced was only whether the
 * gate's poll got there first — which is why the same commit measured 186/186 on one machine and
 * 183/186 on another, twice.
 */
const HERE = dirname(fileURLToPath(import.meta.url))

/** QA's trajectory, as samples. The client is loading exactly while it has no result. */
const QA_TRAJECTORY = [
  { loading: true, rows: 0 },
  { loading: false, rows: 2 },
  { loading: false, rows: 2 },
  { loading: false, rows: 2 },
]

function sampler(trajectory: { loading: boolean; rows: number }[]) {
  let i = 0
  return async () => trajectory[Math.min(i++, trajectory.length - 1)]
}

describe('the gate waits for a settled surface, never for a changed one', () => {
  it('test_the_wait_never_returns_the_loading_state', async () => {
    const got = await settleOnLoaded(sampler(QA_TRAJECTORY), { interval: 0 })
    expect(got.rows, 'the wait returned the loading state QA measured').toBe(2)
    expect(got.loading).toBe(false)
    expect(got.timedOut).toBe(false)
  })

  /**
   * The regression this exists to catch, run rather than described: the shipped predicate was
   * "different from what it was", and against the same trajectory it stops at the first sample.
   */
  it('test_the_predicate_that_failed_qa_stops_on_the_loading_state', async () => {
    const before = 5                                  // five rows before the click, as in the gate
    const trajectory = QA_TRAJECTORY
    const firstChanged = trajectory.find((s) => s.rows !== before)
    expect(firstChanged, 'the trajectory must contain a changed sample at all').toBeDefined()
    expect(firstChanged!.rows, 'a change-wait lands on 0 rows — this is the defect').toBe(0)
    expect(firstChanged!.loading).toBe(true)

    // and the settle-wait, given the identical trajectory, does not
    const settled = await settleOnLoaded(sampler(trajectory), { interval: 0 })
    expect(settled.rows).not.toBe(firstChanged!.rows)
  })

  it('test_a_surface_that_never_settles_is_reported_not_hidden', async () => {
    const stuck = await settleOnLoaded(sampler([{ loading: true, rows: 0 }]),
      { timeout: 0, interval: 0 })
    expect(stuck.timedOut, 'a wait that gives up must say so, not return a settled-looking sample')
      .toBe(true)
  })

  /**
   * Negative (mandatory), and the one that binds this module to the gate that has to use it.
   * The unit cases above stay green if `browser-gate.mjs` keeps its old wait and simply never
   * imports this file — which is precisely the state round 1 shipped. This reads the gate.
   */
  it('negative_the_browser_gate_contains_no_change_wait', () => {
    const gate = readFileSync(join(HERE, '../gate/browser-gate.mjs'), 'utf8')
    const offending = gate.split('\n')
      .map((line, n) => [n + 1, line] as const)
      .filter(([, line]) => CHANGE_WAIT.test(line))
      .map(([n, line]) => `browser-gate.mjs:${n}: ${line.trim()}`)
    expect(offending, 'a wait satisfied by the loading state is back in the gate').toEqual([])
    expect(gate, 'the gate must actually use the settle-wait').toContain('settleOnLoaded')
  })
})
