import { afterEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cleanup, screen, within } from '@testing-library/react'

import { lane, laneSet, mountApp, settle } from './helpers'

/**
 * WEB-FEAT-366 — the design leads, and it is on disk.
 *
 * `WEB-FIX-334`: `WEB-FEAT-332` said the design led and it had — on the pen.dev CANVAS, which is
 * not the file. So this reads the FILE, off disk, as JSON, exactly as `design-search-row.test.ts`
 * does, and its negative runs the SAME predicate over the blob at this ticket's filing commit.
 *
 * `WEB-FIX-336` is the other half and it is why the third case exists: a check that asserts the
 * layer a ticket ADDED passes on a frame that contradicts itself one line above. The criterion is
 * about the FRAME — so the third case drives the shipped component with the frame's own copy and
 * requires the screen to be the frame.
 */
const DESIGN = join(__dirname, '..', 'design', 'factory.pen')
const ROOT = join(__dirname, '..', '..', '..')

/** `WEB-FEAT-366: file the ticket` — the design as it stood when the round was handed this work. */
const BEFORE = '39d94ca9'

const LANES_FRAME = 'mTLsb'          // `Dashboard · Lanes`
const TABLE_FRAME = 'WkeQN'          // `Lanes · Table`

type Node = {
  id?: string; name?: string; type?: string; content?: string; fill?: string
  children?: Node[]
}

const parse = (source: string) => JSON.parse(source) as { children: Node[] }
const frame = (doc: { children: Node[] }, id: string) => doc.children.find((c) => c.id === id)
const nodes = (n: Node | undefined): Node[] =>
  n === undefined ? [] : [n, ...(n.children ?? []).flatMap(nodes)]

function copy(n: Node | undefined): string[] {
  if (!n) return []
  const here = n.type === 'text' && n.content ? [n.content] : []
  return here.concat(...(n.children ?? []).map(copy))
}

/** The smallest descendant frame NAMED for a lane — the card in `mTLsb`, the row in `WkeQN`. */
const drawingOf = (root: Node | undefined, name: string): Node | undefined =>
  nodes(root).filter((n) => n.type === 'frame' && n.name === name)
    .sort((a, b) => nodes(a).length - nodes(b).length)[0]

/**
 * The predicate, written once so the positive, the negative and the component case run the SAME
 * code. A layer is the words line when it is a TEXT layer named `Words`; the design's `›` glyph is
 * part of the copy a person reads and is drawn by the client as a CSS `::before`, so `text` is the
 * copy with that glyph and `said` is the string the server would have had to send for it.
 */
function wordsLine(source: string, frameId: string, laneName: string) {
  const drawing = drawingOf(frame(parse(source), frameId), laneName)
  const layer = nodes(drawing).find((n) => n.type === 'text' && n.name === 'Words')
  const text = layer?.content ?? ''
  return {
    drawn: drawing !== undefined,
    present: layer !== undefined,
    text,
    said: text.replace(/^›\s*/, ''),
    fill: layer?.fill,
    /** every line of copy in that card or row, so a case can say what IS there when it fails */
    copy: copy(drawing),
  }
}

const git = (...args: string[]) =>
  execFileSync('git', args, { cwd: ROOT, maxBuffer: 32 << 20 }).toString('utf8')

afterEach(cleanup)

describe('WEB-FEAT-366 — the design holds the line the product ships', () => {
  it('the_lane_frames_carry_the_words_line_on_the_running_lane_only', () => {
    const source = readFileSync(DESIGN, 'utf8')

    // BOTH frames, and in each of them the RUNNING lane has the line and the QUEUED one does not.
    // A case asserting only the running lane passes on a design that draws the line on every card,
    // which is the state §1's negative exists to refuse.
    const sites = [
      { frame: LANES_FRAME, running: 'lane/01', queued: 'lane/02' },
      { frame: TABLE_FRAME, running: 'lane/01', queued: 'lane/02' },
    ]
    expect(sites.length, 'the sweep is empty').toBeGreaterThan(0)

    const texts = new Set<string>()
    for (const site of sites) {
      const spoke = wordsLine(source, site.frame, site.running)
      const silent = wordsLine(source, site.frame, site.queued)

      expect(spoke.drawn, `frame ${site.frame} draws no ${site.running}`).toBe(true)
      expect(silent.drawn, `frame ${site.frame} draws no ${site.queued}`).toBe(true)

      expect(spoke.present,
        `frame ${site.frame} · ${site.running} carries no Words line: ${spoke.copy.join(' | ')}`)
        .toBe(true)
      expect(silent.present,
        `frame ${site.frame} · ${site.queued} is queued and must carry no Words line, and it `
        + `carries "${silent.text}"`).toBe(false)

      // the design's own glyph, and NO NEW COLOUR — `$t3` is the token `.lane-sub` already uses
      expect(spoke.text.startsWith('›'), `the line reads "${spoke.text}"`).toBe(true)
      expect(spoke.fill).toBe('$t3')
      texts.add(spoke.said)
    }

    // the two frames are one surface drawn twice, so they say the SAME thing. §4.20's own
    // sentence: the line on `#/lanes`, the rail's card and the STATE cell all read one text.
    expect([...texts], 'the two frames draw different words').toHaveLength(1)

    // and the queued card still says why it is queued — the words line was ADDED beside the
    // sub-line, never folded into it (WEB-FIX-277)
    const queuedCopy = wordsLine(source, LANES_FRAME, 'lane/02').copy.join(' ')
    expect(queuedCopy).toContain('board-master')
  })

  /**
   * MANDATORY NEGATIVE — executable rather than remembered.
   *
   * `39d94ca9` is `WEB-FEAT-366: file the ticket`, the commit this round started from. The same
   * predicate over that blob must find the line ABSENT in both frames; the frames themselves were
   * there all along, so it is the line that was missing and not the surface.
   */
  it('the_check_fails_on_the_design_that_had_no_words_line', () => {
    const before = git('show', `${BEFORE}:client/web/design/factory.pen`)
    for (const id of [LANES_FRAME, TABLE_FRAME]) {
      const spoke = wordsLine(before, id, 'lane/01')
      expect(spoke.drawn, `frame ${id} did not draw lane/01 at ${BEFORE} either`).toBe(true)
      expect(spoke.present, `frame ${id} already carried a Words line at ${BEFORE}`).toBe(false)
      expect(spoke.copy.length, `frame ${id} · lane/01 was empty at ${BEFORE}`)
        .toBeGreaterThan(0)
    }
  })

  /**
   * The frame is a STATE THE SHIPPED COMPONENT CAN BE IN — `WEB-FIX-336`'s rule.
   *
   * `Dashboard · Lanes` is `DashboardLanes.tsx`'s frame, so the component is driven with the
   * frame's own lane names, its own state line and its own words, and what it renders has to be
   * the frame's own copy. Restating the component's rule in an assertion would only agree with
   * itself; this can only agree with the design.
   */
  it('the_frames_copy_is_what_the_shipped_lane_block_renders', async () => {
    const source = readFileSync(DESIGN, 'utf8')
    const spoke = wordsLine(source, LANES_FRAME, 'lane/01')
    const silent = wordsLine(source, LANES_FRAME, 'lane/02')
    expect(spoke.said, 'the frame draws no words for lane/01').not.toBe('')

    await mountApp('#/', {
      '/api/lanes': {
        body: laneSet([
          lane({
            lane: 'lane/01', ticket_id: 'WEB-FEAT-004', workspace: 'core',
            state: { process: 'running',
                     motion: { value: 'producing', silent_for_s: 0, since: null },
                     evidence: 'nothing_claimed', blocked: null, alarm: false },
            last_words: { kind: 'file_edit', text: spoke.said, at: '2026-09-02T09:59:59Z' },
          }),
          lane({
            lane: 'lane/02', ticket_id: 'SRV-FEAT-021', workspace: 'core', role: 'qa',
            state: { process: 'queued', motion: { value: null, silent_for_s: 0, since: null },
                     evidence: 'nothing_claimed', blocked: null, alarm: false },
            detail: { waiting_for: 'board-master' }, last_words: null,
          }),
        ]),
      },
    })
    await settle()
    await settle()

    const cardOf = (name: string) =>
      Array.from(screen.getByTestId('dashboard-lanes')
        .querySelectorAll<HTMLElement>('.lane-card'))
        .find((c) => c.textContent?.includes(name))!

    // the frame's own copy, on the screen, from the field — not the glyph, which is CSS
    expect(within(cardOf('lane/01')).getByTestId('lane-words').textContent).toBe(spoke.said)
    expect(spoke.text).toBe(`› ${spoke.said}`)

    // and the frame's queued card is a state the same component draws with no line at all
    expect(within(cardOf('lane/02')).queryByTestId('lane-words')).toBeNull()
    expect(silent.present).toBe(false)
  })
})
