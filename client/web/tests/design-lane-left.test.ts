import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { formatElapsed, formatLeft } from '../src/vocabulary'

/**
 * WEB-FIX-428 · criterion 1 — the time left is in the DESIGN, read off disk.
 *
 * The canvas is not the file (`WEB-FIX-334`): this parses `factory.pen` as JSON. And the criterion
 * is about the FRAME, not the layer the ticket added (`WEB-FIX-336`): each card's foot is read as a
 * whole — what sits before the new text, what sits after it — and the product's own formatter,
 * driven with the frame's own numbers, must produce the frame's own copy.
 */
type Node = { id?: string; name?: string; type?: string; content?: string; children?: Node[] }

const doc = JSON.parse(readFileSync(join(__dirname, '..', 'design', 'factory.pen'), 'utf8')) as Node

function find(id: string, n: Node = doc): Node | undefined {
  if (n.id === id) return n
  for (const c of n.children ?? []) {
    const hit = find(id, c)
    if (hit) return hit
  }
  return undefined
}

const foot = (id: string) => (find(id)?.children ?? []).map((c) => [c.name, c.content])

describe('WEB-FIX-428 the lane card draws the time left', () => {
  it('both_cards_draw_the_time_left_after_the_elapsed_time_and_a_queued_lane_draws_none', () => {
    // The rail's main lane (`fTClA`), the dashboard's running lane (`z5wSV`) and its queued lane
    // (`p9ofZ`, `—` for elapsed), in one assertion — the frame as a state.
    expect([foot('fTClA'), foot('z5wSV'), foot('p9ofZ')]).toEqual([
      [['1h 42m', '1h 42m'], ['Time left', '~12m left'], ['BUILD', 'BUILD'], ['$22.90', '$22.90']],
      [['Stage', 'IMPL 4/10'], ['Elapsed', '3m 12s'], ['Time left', '~12m left'], ['Cost', '$1.84']],
      [['Stage', expect.any(String)], ['Elapsed', '—'], ['Cost', '—']],
    ])
  })

  it('the_products_formatters_draw_the_frames_own_copy', () => {
    // 750 s is the frame's estimate; 192 s its elapsed. Driven through the SHIPPED formatters, so a
    // frame that drew a shape the product cannot produce is red here.
    expect([formatLeft(750), formatElapsed(192)]).toEqual(['~12m left', '3m 12s'])
  })
})
