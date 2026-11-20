import { afterEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cleanup, screen } from '@testing-library/react'

import { lane, mountApp, settle, ticketDoc, ticketRoutes } from './helpers'
import { keyFromHint, press } from './keys'
import { NEEDS_YOU_KEY_HINT } from '../src/components/TopBar'

/**
 * WEB-FEAT-365 — the design leads, and it is on disk.
 *
 * The pattern is `design-search-row.test.ts`'s: parse `factory.pen` as JSON off the FILE, because
 * the pen.dev canvas is a buffer and a screenshot of a design is not the design (`WEB-FIX-334`).
 *
 * And the criterion is about the FRAME rather than the layer this ticket added (`WEB-FIX-336`).
 * The chip is located by what it SAYS, not by the id typed here, and the frame is then asserted
 * as a state the shipped component can be in: the real `TopBar`, driven with the frame's own
 * count, must draw the frame's own hint copy — and the key that copy spells must be the key that
 * navigates. A check that only asked whether a `KeyHint` layer exists would pass on a design
 * whose hint says something the product does not answer to.
 */

const ROOT = join(__dirname, '..', '..', '..')
const DESIGN = join(__dirname, '..', 'design', 'factory.pen')
const TOP_BAR = 'QFMQY'                 // `Shell · Top bar`
/** `WEB-FEAT-365: file the ticket — Alt+N goes where the needs-you chip goes`. */
const FILED = '5f47c811'

type Node = { id?: string; name?: string; type?: string; content?: string; children?: Node[] }

const parse = (source: string) => JSON.parse(source) as { children: Node[] }
const frame = (doc: { children: Node[] }, id: string) => doc.children.find((c) => c.id === id)
const nodes = (n: Node | undefined): Node[] =>
  n === undefined ? [] : [n, ...(n.children ?? []).flatMap(nodes)]

function copy(n: Node | undefined): string[] {
  if (!n) return []
  const here = n.type === 'text' && n.content ? [n.content] : []
  return here.concat(...(n.children ?? []).map(copy))
}

const git = (...args: string[]) =>
  execFileSync('git', args, { cwd: ROOT, maxBuffer: 32 << 20 }).toString('utf8')

/** The `N need you` label §4.1's chip is drawn around, whatever number it happens to carry. */
const LABEL = /^(\d+)\s+need\s+you$/

/**
 * The top bar's needs-you chip, as a STATE: what it counts, and what it says the key is.
 *
 * Located by the copy it draws — the SMALLEST frame inside `Shell · Top bar` holding a
 * `N need you` label — so a renamed layer or a moved id is still found, and a chip that stopped
 * being drawn fails by name instead of quietly emptying the sweep.
 */
function chipState(source: string) {
  const bar = frame(parse(source), TOP_BAR)
  const holders = nodes(bar).filter(
    (n) => n.type === 'frame' && copy(n).some((t) => LABEL.test(t.trim())))
  const chip = holders.sort((a, b) => copy(a).length - copy(b).length)[0]
  const texts = copy(chip).map((t) => t.trim())
  const label = texts.find((t) => LABEL.test(t)) ?? ''
  return {
    found: chip !== undefined,
    count: Number(LABEL.exec(label)?.[1] ?? NaN),
    /** everything the chip says that is not its count — the hint, or nothing at all */
    hint: texts.filter((t) => t !== label && t !== '').join(' '),
  }
}

afterEach(cleanup)

describe('WEB-FEAT-365 — the design carries the key the shell binds', () => {
  it('the_top_bar_frame_carries_the_key_beside_the_chip', async () => {
    const state = chipState(readFileSync(DESIGN, 'utf8'))

    // the sweep found something: a locator that matches nothing is a check that has not run
    expect(state.found, `frame ${TOP_BAR} draws no \`N need you\` chip at all`).toBe(true)
    expect(state.count, `frame ${TOP_BAR}'s chip draws no count`).toBeGreaterThan(0)

    // §1's DESIGN line — the chip carries the key beside its text
    expect(state.hint, `frame ${TOP_BAR}'s needs-you chip carries no key hint`).not.toBe('')

    // the code reads its hint out of ONE constant, and that constant is the design's own copy
    expect(NEEDS_YOU_KEY_HINT).toBe(state.hint)

    // ── and the frame is a state the shipped component can be in. Driven with the FRAME's own
    //    count — more than one lane needing a person, which is the state a `7` describes — the
    //    real chip must draw the frame's own hint, and the key that hint spells must arrive.
    const rows = [
      ...Array.from({ length: state.count }, (_, i) => lane({
        lane: `lane/${String(i + 1).padStart(2, '0')}`,
        ticket_id: `TKT-1${String(40 + i).padStart(2, '0')}`,
        needs_you: true,
      })),
      lane({ lane: 'lane/90', ticket_id: 'TKT-199', needs_you: false }),
    ]
    await mountApp('#/tickets/TKT-142', ticketRoutes(ticketDoc(), rows))
    await settle()
    await settle()

    const chip = screen.getByTestId('needs-you-chip')
    expect(chip).toHaveTextContent(`${state.count} lanes need you`)
    expect((screen.getByTestId('needs-you-key').textContent ?? '').trim()).toBe(state.hint)

    const where = chip.getAttribute('href')
    expect(where).toBe('#/lanes')
    const arrived = await press(document.body, keyFromHint(state.hint))
    expect(location.hash).toBe(where)
    expect(arrived.defaultPrevented).toBe(true)
  })

  /**
   * MANDATORY NEGATIVE — the same predicate, on the design as this ticket was filed against.
   *
   * `5f47c811` is the filing commit. The chip has to be FOUND there, and to carry no hint: a
   * negative that went green because the locator stopped matching would be evidence about the
   * locator, not about the design.
   */
  it('the_check_fails_on_the_design_that_had_no_key_hint', () => {
    const before = chipState(git('show', `${FILED}:client/web/design/factory.pen`))

    expect(before.found, `frame ${TOP_BAR} held no needs-you chip at ${FILED} — the locator `
                         + 'changed, not the hint').toBe(true)
    expect(before.count, `the chip at ${FILED} drew no count`).toBeGreaterThan(0)
    expect(before.hint, `the design at ${FILED} must carry NO key hint, and carries `
                        + `"${before.hint}"`).toBe('')
  })
})
