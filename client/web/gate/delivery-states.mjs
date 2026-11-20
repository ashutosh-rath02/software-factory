/**
 * WHICH DELIVERY STATES THE BROWSER GATE ASSERTS, and it is not a list anybody typed
 * (WEB-FIX-373).
 *
 * `browser-gate.mjs` required every one of `['done', 'running', 'stalled', 'unshipped',
 * 'waiting']` to appear as a badge across the static sweep. `SRV-FIX-453` narrowed `running` to
 * *a lane the tool started whose agent is alive* — `app/delivery/probe.cpp`, deliberately, and it
 * is the right definition — and `gate/fixture-repo` has no such lane while the static sweep runs,
 * so the badge cannot be drawn there any more. The assertion's domain was five elements typed into
 * the gate, so a change to what the PRODUCT means by a state could not reach it: the list agreed
 * with the gate that held it and with nothing else, which is `_TEMPLATE.md` §2's first way a check
 * lies, in a gate rather than in a test.
 *
 * It lives in its own module for the reason `surfaces.mjs` and `sweep.mjs` do: a rule kept
 * privately inside a ten-minute browser run is a rule nothing can ask a question of, and
 * `tests/gate-delivery-states.test.ts` drives this function directly — including §1's mandatory
 * negative, which is a BREAK and not an input.
 *
 * The verdict is an equality in BOTH directions and both are load-bearing:
 *
 *   expected \ drawn   the fixture reached a state the screen did not badge — the half the gate
 *                      has always had, and the half that was red about `running`
 *   drawn \ expected   the screen badged a state the derivation did not produce — the half §1
 *                      criterion 3 exists for. A derived set that silently shrinks to whatever
 *                      happens to be on screen is the same defect from the other side, and only
 *                      this direction can see it.
 */

/**
 * @typedef {{ state: string, source: string, needsYou?: boolean }} Produced
 *   one `delivery.state` the sweep's own fixture answered with, and WHERE — `E4` for the
 *   requirement itself, or for one ticket under it. `source` is what makes the verdict line say
 *   which state came from where instead of asserting a set nobody can trace.
 *
 *   `needsYou` is the server's own answer for that row (`delivery.needs_you`), carried so the gate
 *   can ask WHICH states must paint an alarm border without a pair typed into it (`WEB-FIX-405`).
 *   `['stalled', 'unshipped']` was written into `browser-gate.mjs` beside the derived domain, and
 *   it is the same defect one line lower: `probe.cpp` decides that set and this file did not.
 */

/**
 * @param {{ produced?: Produced[], words?: string[], drawn?: string[], domain?: string[] }} given
 *   `produced` — every state `E4` answered with on the routes the static sweep opens.
 *   `words`    — the delivery values the product's own vocabulary sheet draws (§4.11). A state
 *                the sheet has no word for draws no badge at all: `DeliveryBadge` returns null
 *                for `not_started` and `unknown`, deliberately.
 *   `drawn`    — every `data-state` the browser painted on those routes.
 *   `domain`   — the CONTRACT's enumeration of `delivery.state`
 *                (`docs/technical/contracts/http.schema.json` `$defs/delivery`, which is `SPEC.md` §3.5's table).
 *                `WEB-FIX-405` — the fixture and the vocabulary sheet are two things this gate
 *                happens to be looking at; the enumeration is what the product says exists, and a
 *                state in either of the first two that is not in the third is a disagreement
 *                between the product and its own contract, not a badge that failed to draw.
 * @returns {{ ok: boolean, expected: string[], offBadge: string[], line: string }}
 */
export function judgeDeliveryBadges({ produced = [], words = [], drawn = [], domain = [] } = {}) {
  const sources = new Map()
  for (const { state, source } of produced) {
    if (!state) continue
    if (!sources.has(state)) sources.set(state, [])
    if (source && !sources.get(state).includes(source)) sources.get(state).push(source)
  }

  const reached = [...sources.keys()].sort()
  const expected = reached.filter((s) => words.includes(s))
  const offBadge = reached.filter((s) => !words.includes(s))
  const painted = [...new Set(drawn.filter(Boolean))].sort()

  const missing = expected.filter((s) => !painted.includes(s))
  const unexpected = painted.filter((s) => !expected.includes(s))

  // Both inputs are sweeps, and `_TEMPLATE.md` §2's third way a check lies is a sweep that
  // examined nothing: a vocabulary sheet that drew no delivery row, or a fixture that answered
  // with no state, would make every set operation below vacuously true.
  const refusals = []
  if (domain.length === 0) refusals.push('the contract enumerated no delivery state')
  if (words.length === 0) refusals.push('the vocabulary sheet named no delivery word')
  if (reached.length === 0) refusals.push('the sweep’s fixture produced no delivery state')
  const outsideTheContract = reached.filter((s) => !domain.includes(s))
  if (domain.length > 0 && outsideTheContract.length > 0) {
    refusals.push(`the fixture produced ${outsideTheContract.join(' · ')}, which the contract does `
      + 'not enumerate')
  }
  const wordsOutside = words.filter((w) => !domain.includes(w))
  if (domain.length > 0 && wordsOutside.length > 0) {
    refusals.push(`the vocabulary sheet draws ${wordsOutside.join(' · ')}, which the contract does `
      + 'not enumerate')
  }
  // …and one state is not a population. On a singleton every set comparison is the identity, so a
  // derivation that collapsed to one row would agree with any screen that drew that one row.
  if (reached.length > 0 && expected.length < 2) {
    refusals.push(`the derivation reached fewer than two badge states (${
      expected.join(' · ') || 'none'}) — one state is not a population`)
  }
  if (missing.length > 0) {
    refusals.push(`the fixture reached ${missing.join(' · ')} and the screen drew no badge for it`)
  }
  if (unexpected.length > 0) {
    refusals.push(`the screen drew ${unexpected.join(' · ')}, which the derivation did not produce`)
  }

  const provenance = [
    ...expected.map((s) => `${s} ← ${sources.get(s).join(', ')}`),
    ...offBadge.map((s) => `${s} (no badge word) ← ${sources.get(s).join(', ')}`),
  ].join(' | ')

  return {
    ok: refusals.length === 0,
    expected,
    offBadge,
    line: refusals.length === 0
      ? `${provenance} · drawn ${painted.join(' · ') || 'none'}`
      : `${refusals.join('; ')} · derived ${provenance || 'nothing'} · drawn ${
        painted.join(' · ') || 'none'}`,
  }
}

/**
 * Every `delivery.state` one `E4` body carries, with its source named.
 *
 * The requirement's OWN badge is on that screen beside its tickets' (`RequirementPage.tsx` draws
 * both), so a derivation that read only the tickets would leave the roll-up drawing a state
 * nothing expected — which is exactly the `drawn \ expected` direction, firing on the gate's own
 * blind spot rather than on a defect.
 *
 * @param {string} id
 * @param {any} body
 * @returns {Produced[]}
 */
export function statesIn(id, body) {
  const rows = [{ state: body?.delivery?.state, source: `${id} · the requirement`,
                  needsYou: body?.delivery?.needs_you === true }]
  for (const group of body?.groups ?? []) {
    for (const ticket of group?.tickets ?? []) {
      rows.push({ state: ticket?.delivery?.state, source: `${id} · ${ticket?.id ?? '?'}`,
                  needsYou: ticket?.delivery?.needs_you === true })
    }
  }
  return rows.filter((r) => r.state)
}

/**
 * Which states the SERVER says need a person, and which it says do not — from `E4`'s own
 * `needs_you`, never from a pair written into the gate.
 *
 * Both halves are returned because a set of alarms with no calm states beside it makes the border
 * check the identity: every badge painting a border passes a rule that only ever looks at badges
 * that should.
 *
 * @param {Produced[]} produced
 * @returns {{ alarm: string[], calm: string[], disagree: string[] }}
 *   `disagree` names any state the fixture answered BOTH ways, which is the server contradicting
 *   itself and not something this gate may average over.
 */
export function alarmStates(produced) {
  const seen = new Map()
  for (const row of produced) {
    if (!seen.has(row.state)) seen.set(row.state, new Set())
    seen.get(row.state).add(row.needsYou === true)
  }
  const of = (want) => [...seen.entries()]
    .filter(([, vals]) => vals.size === 1 && vals.has(want)).map(([s]) => s).sort()
  return {
    alarm: of(true),
    calm: of(false),
    disagree: [...seen.entries()].filter(([, vals]) => vals.size > 1).map(([s]) => s).sort(),
  }
}
