import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { screen } from '@testing-library/react'

import { laneStateLine } from '../src/vocabulary'
import { mountApp } from './helpers'
import { refuseOverBudget, suiteTimeoutMs } from './suite-budget'

/**
 * WEB-FIX-349 — the card said `waiting for you` after the server had said what it was waiting for.
 *
 * `SRV-FEAT-415` gave the server a `stopped` axis and nothing in the client read it, so five lanes
 * reading `stopped: {reason: "holds_work"}` all drew the one fixed string from `vocabulary.ts:71`.
 *
 * `holds_work` is the reason that matters most, and it is NOT the operator's turn in the sense the
 * old words imply: the lane holds commits nobody else can see, and what it needs is for that work
 * to be pushed — a different act from answering a question, and not one that screen offers.
 */
const lane = (over: Record<string, unknown> = {}) => ({
  process: 'idle', motion: { value: null, silent_for_s: 0, since: null },
  evidence: 'nothing_claimed', ...over,
}) as never

const SCHEMA = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', 'docs', 'technical/contracts/http.schema.json'), 'utf8'))

/** The ceiling this file's one timing-coupled case runs under — the suite's, never its own. */
const BUDGET_MS = suiteTimeoutMs()

describe('WEB-FIX-349 · a stopped lane says what stopped it', () => {
  /**
   * The FIRST version of this file put `stopped` beside `process` in its own fixture and passed
   * while the real screen still said `waiting for you` — because the server served it as a SIBLING
   * of `state`, not a member. A fixture is a guess about a payload until something ties it to the
   * contract, so this case is that tie: `stopped` is a member of the state shape the client reads.
   */
  it('test_the_contract_puts_stopped_inside_the_state_the_client_reads', () => {
    const state = SCHEMA.$defs?.laneState ?? SCHEMA.$defs?.state
    expect(state, 'no lane state definition in the contract').toBeTruthy()
    expect(Object.keys(state.properties), 'the contract does not put `stopped` inside the state')
      .toContain('stopped')
  })

  it('test_a_lane_holding_work_says_so', () => {
    const line = laneStateLine(lane({ stopped: { reason: 'holds_work', seconds: 1687 } }))
    expect(line).toBe('idle · holds work not pushed')
    expect(line).not.toContain('waiting for you')
  })

  /**
   * The set is DERIVED from the contract, not typed here: a reason the server learns to send and
   * this file has never heard of would otherwise fall through to the old sentence silently, which
   * is the defect one size smaller.
   */
  it('test_every_declared_reason_has_a_line', () => {
    const schema = SCHEMA
    const reasons: string[] = []
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) { node.forEach(walk); return }
      if (!node || typeof node !== 'object') return
      const o = node as Record<string, unknown>
      const stopped = (o.properties as Record<string, unknown> | undefined)?.stopped as
        Record<string, unknown> | undefined
      const enumed = ((stopped?.properties as Record<string, unknown> | undefined)?.reason as
        Record<string, unknown> | undefined)?.enum
      if (Array.isArray(enumed)) reasons.push(...(enumed as string[]))
      Object.values(o).forEach(walk)
    }
    walk(schema)

    expect(reasons.length, 'the sweep found no declared reasons, so it asserts nothing')
      .toBeGreaterThan(0)

    for (const reason of [...new Set(reasons)]) {
      const line = laneStateLine(lane({
        stopped: { reason, seconds: 12 },
        process: reason === 'never_started' ? 'not_started'
               : reason === 'crashed' ? 'crashed' : 'idle',
      }))
      expect(line, `\`${reason}\` produced no line`).toBeTruthy()
      expect(line, `\`${reason}\` still reads as the old catch-all`)
        .not.toBe('idle · waiting for you')
    }
  })

  it('test_a_running_lane_reads_as_before', () => {
    // this ticket adds a sentence for stopped lanes; it does not rewrite the ones that worked
    const running = laneStateLine(lane({
      process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
      stopped: null,
    }))
    expect(running).toBe('running · producing')
  })
})

/* ------------------------------------------------------------------------------------------- *
 * The seat total the rail draws.
 *
 * `SideNav` computed `Math.max(lanes.length, 3)` — a number the CLIENT invented. On a host
 * declaring ten seats with no lanes claimed it read `0/3`, and with seven lanes it would have read
 * `7/7`. The operator saw `0/3`, asked, and the API said `{"total": 10, "used": 0}`: the screen and
 * the tool disagreeing about how much room there is.
 * ------------------------------------------------------------------------------------------- */

describe('the rail draws the seat total the SERVER declared', () => {
  it('test_the_seat_total_comes_from_the_answer_not_from_the_lane_count', async () => {
    const started = performance.now()
    await mountApp('#/lanes', {
      '/api/lanes': { body: { lanes: [], resources: [],
                              seats: { host: { total: 10, used: 0 } } } },
    })
    // WAIT for the count rather than settling a fixed number of ticks: this case mounts the whole
    // app, and a fixed settle is a race that passes alone and fails under full-suite load — which
    // is `WEB-FIX-327`'s shape, and not one worth reproducing in a new file.
    await screen.findByText('0/10', {}, { timeout: BUDGET_MS })
    const rail = screen.getByTestId('sidenav').textContent ?? ''
    expect(rail, 'the rail invented a seat total instead of reading the one it was given')
      .toContain('0/10')
    expect(rail).not.toContain('0/3')

    // WEB-FIX-405 §1 B2 — and it takes a FRACTION of its ceiling. `WEB-FIX-372` gave this case a
    // private 20 s because the mount alone cost 4.6 s against vitest's 5 s default, which is a case
    // that reports host load as a defect. A private ceiling is not the answer either: it makes this
    // one case unfalsifiable about the thing every other case in the suite is exposed to. So the
    // ceiling is the SUITE's, stated in `vite.config.ts`, and a run that eats half of it is refused
    // HERE, with the number, rather than passing at 96% and failing on the next machine.
    //
    // Half, which is `refuseOverBudget`'s default and the share `notify.test.tsx` has used since
    // `WEB-FIX-396` — ONE convention rather than a second number this case chose for itself.
    // Measured over five consecutive whole-suite runs on 2026-09-12: 5741, 5371, 5354, 5373,
    // 5365 ms against a 10 000 ms refusal.
    refuseOverBudget(performance.now() - started, BUDGET_MS)
  }, BUDGET_MS)
})
