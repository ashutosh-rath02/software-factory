import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
// The gate's OWN judge, imported — `gate-scope.test.ts` is the pattern and its reason holds here:
// a test that re-types the rule it is checking agrees with itself and with nothing else.
import { alarmStates, judgeDeliveryBadges } from '../gate/delivery-states.mjs'

/**
 * WEB-FIX-373 — the browser gate asserted five delivery states and its fixture can reach four.
 *
 * `SRV-FIX-453` narrowed `running` to a lane the tool started whose agent is alive
 * (`app/delivery/probe.cpp`), which is the right definition and which no static frame of
 * `gate/fixture-repo` can reach. The gate's assertion was a five-element array typed into
 * `browser-gate.mjs`, so a change to what the PRODUCT means by a state could not reach it: the
 * list agreed with the gate that held it and with nothing else.
 *
 * So the domain is derived — from what the sweep's own fixture produces, read off `E4` at the
 * moment the sweep opens each route — and the judgement is an equality in BOTH directions. The
 * second direction is what §1's mandatory negative is about: a derivation that silently shrinks
 * to whatever happens to be on screen is the same defect from the other side, and only
 * `drawn \ expected` can see it.
 */

const GATE = join(__dirname, '..', 'gate')

/**
 * `gate/fixture-repo` as the two routes the static sweep opens actually answer it, with the
 * source of every state named — which is what §1 means by *the gate names which state came from
 * where*.
 *
 * FIVE states over TWO requirements, and they do not agree with each other: four badge words under
 * `REQ-014` and one off-badge state under `REQ-009`. A one-requirement, one-state fixture would
 * make every set operation below the identity, which is `_TEMPLATE.md` §2's rule about a value
 * reduced from a collection.
 */
const PRODUCED = [
  { state: 'unshipped', source: 'REQ-014 · the requirement' },
  { state: 'done', source: 'REQ-014 · TKT-139' },
  { state: 'unshipped', source: 'REQ-014 · TKT-142' },
  { state: 'stalled', source: 'REQ-014 · TKT-147' },
  { state: 'waiting', source: 'REQ-014 · TKT-151' },
  { state: 'not_started', source: 'REQ-009 · the requirement' },
  { state: 'not_started', source: 'REQ-009 · TKT-155' },
]

/** The five words the product's own vocabulary sheet draws (§4.11, `src/vocabulary.ts` DELIVERY). */
const WORDS = ['running', 'stalled', 'unshipped', 'waiting', 'done']

/**
 * The CONTRACT's enumeration of `delivery.state`, read off `http.schema.json` (`WEB-FIX-405`).
 *
 * Not a list here: the schema is `SPEC.md` §3.5's table, and the point of the fourth input is that
 * the gate's domain stops being anything a session wrote down. A copy in this file would be the
 * defect it was added to close.
 */
const DOMAIN: string[] = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', 'docs', 'technical/contracts/http.schema.json'), 'utf8'),
).$defs.delivery.properties.state.enum

/** What the browser paints on those two routes when nothing is wrong. */
const DRAWN = ['unshipped', 'done', 'stalled', 'waiting']

describe('WEB-FIX-373 — the delivery states the gate asserts are the ones its sweep reaches', () => {
  /**
   * §1 criterion 1. The set is derived and it is the fixture's, so `running` — which
   * `SRV-FIX-453` put out of the static sweep's reach — is not in it, and no state the fixture
   * does reach is left out.
   */
  it('the_domain_is_the_fixtures_own_states_and_the_verdict_names_where_each_came_from', () => {
    const verdict = judgeDeliveryBadges({ produced: PRODUCED, words: WORDS, drawn: DRAWN, domain: DOMAIN })

    expect(verdict.ok, verdict.line).toBe(true)
    expect(verdict.expected).toEqual(['done', 'stalled', 'unshipped', 'waiting'])
    // The state the narrowing put out of reach is NOT asserted over the static sweep…
    expect(verdict.expected).not.toContain('running')
    // …and it is not silently dropped either: it is a word the badge has, which the fixture did
    // not produce here, and section 10 is where it is asked for.
    expect(verdict.offBadge).toEqual(['not_started'])

    // "the gate names which state came from where" — every state in the line, with its source.
    for (const { state, source } of PRODUCED) {
      expect(verdict.line).toContain(state)
      expect(verdict.line).toContain(source)
    }
  })

  /**
   * §1 criterion 3 — THE MANDATORY NEGATIVE, unit half. Remove one reachable state from what the
   * derivation produces and the verdict refuses, NAMING it.
   *
   * `waiting` is the removed one: it is produced by exactly one ticket (`TKT-151`), so dropping it
   * from `produced` is the whole of the break and nothing else moves.
   */
  it('MANDATORY_NEGATIVE_a_reachable_state_dropped_from_the_derivation_is_refused_by_name', () => {
    const shrunk = PRODUCED.filter((p) => p.state !== 'waiting')
    const verdict = judgeDeliveryBadges({ produced: shrunk, words: WORDS, drawn: DRAWN, domain: DOMAIN })

    expect(verdict.ok).toBe(false)
    expect(verdict.line).toContain('waiting')
    expect(verdict.line).toMatch(/drew .*the derivation did not produce|not produced/)
  })

  /**
   * The other direction, which is the half the gate has always had: a state the fixture reaches
   * and the screen stops drawing is red, by name.
   */
  it('a_reachable_state_the_screen_stops_drawing_is_refused_by_name', () => {
    const verdict = judgeDeliveryBadges({
      produced: PRODUCED, words: WORDS, drawn: DRAWN.filter((s) => s !== 'stalled'),
      domain: DOMAIN,
    })

    expect(verdict.ok).toBe(false)
    expect(verdict.line).toContain('stalled')
  })

  /**
   * A state the badge has no word for draws nothing — `DeliveryBadge` returns null for
   * `not_started` and `unknown` — so a screen that started drawing one is red rather than being
   * quietly tolerated as "not in the expected set".
   */
  it('a_state_the_badge_has_no_word_for_must_draw_no_badge', () => {
    const verdict = judgeDeliveryBadges({
      produced: PRODUCED, words: WORDS, drawn: [...DRAWN, 'not_started'], domain: DOMAIN,
    })

    expect(verdict.ok).toBe(false)
    expect(verdict.line).toContain('not_started')
    expect(verdict.line).toMatch(/no badge word/)
  })

  /**
   * `_TEMPLATE.md` §2, the third way a check lies: a check that sweeps nothing has not run. Both
   * inputs to the derivation are sweeps — the vocabulary sheet and the routes — and each of them
   * coming back empty must be a refusal rather than a vacuous pass.
   */
  it('a_derivation_that_swept_nothing_is_refused_rather_than_passing_vacuously', () => {
    expect(judgeDeliveryBadges({ produced: PRODUCED, words: [], drawn: DRAWN, domain: DOMAIN }).ok)
      .toBe(false)
    expect(judgeDeliveryBadges({ produced: [], words: WORDS, drawn: [], domain: DOMAIN }).ok)
      .toBe(false)
    // WEB-FIX-405 — and the fourth sweep, the contract's own enumeration, the same way.
    expect(judgeDeliveryBadges({ produced: PRODUCED, words: WORDS, drawn: DRAWN, domain: [] }).ok)
      .toBe(false)
    // …and one state is not a population: every set operation on a singleton is the identity.
    const one = judgeDeliveryBadges({
      produced: [{ state: 'done', source: 'REQ-014 · TKT-139' }], words: WORDS, drawn: ['done'],
      domain: DOMAIN,
    })
    expect(one.ok).toBe(false)
    expect(one.line).toMatch(/one state|fewer than two/)
  })

  /**
   * WEB-FIX-405 — and WHICH states must paint an alarm border is the server's answer too.
   *
   * `browser-gate.mjs` held `['stalled', 'unshipped']` one line below the derived domain, which is
   * the same defect at a smaller scale: `probe.cpp` decides `needs_you` and the gate had written
   * the answer down. The set now comes from `E4`'s own field, and BOTH halves are returned,
   * because a rule checked only on badges that should draw a border is satisfied by a browser that
   * draws one on everything.
   */
  it('which_states_paint_an_alarm_border_is_the_servers_answer', () => {
    const alarms = alarmStates(PRODUCED.map((r) => ({
      ...r, needsYou: r.state === 'stalled' || r.state === 'unshipped',
    })))
    expect(alarms.alarm).toEqual(['stalled', 'unshipped'])
    expect(alarms.calm, 'nothing to contrast the alarms against — the rule is the identity')
      .toEqual(['done', 'not_started', 'waiting'])
    expect(alarms.disagree).toEqual([])

    // A state the fixture answered BOTH ways is the server contradicting itself, and it belongs in
    // neither half: averaged into one, the gate would assert a border it cannot know is owed.
    const both = alarmStates([
      { state: 'unshipped', source: 'a', needsYou: true },
      { state: 'unshipped', source: 'b', needsYou: false },
      { state: 'done', source: 'c', needsYou: false },
    ])
    expect(both.disagree).toEqual(['unshipped'])
    expect(both.alarm).toEqual([])
    expect(both.calm).toEqual(['done'])
  })

  /**
   * The defect itself, as the gate would have met it: the typed five-element array is gone from
   * `browser-gate.mjs`. §3's own query, run as an assertion — `_TEMPLATE.md` §2's first way a
   * check lies is a set somebody typed, and the only way this file can say it is fixed is to look.
   */
  it('no_delivery_state_list_is_typed_into_the_browser_gate', () => {
    const source = readFileSync(join(GATE, 'browser-gate.mjs'), 'utf8')
    const typed = source.match(/\[\s*'done'\s*,|\[\s*'running'\s*,|\[\s*'not_started'\s*,/g) ?? []
    expect(typed, `typed delivery-state lists still in browser-gate.mjs: ${typed.join(' · ')}`)
      .toEqual([])
  })
})
