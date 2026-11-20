import { describe, it, expect } from 'vitest'

import { HOME, href, parse, type Route } from '../src/routes'

/**
 * `routes.ts` opens by declaring how many addressable surfaces there are, in the hash.
 * WEB-FEAT-050 made it seven and WEB-FEAT-001 made it eight, and that sentence is part of the
 * contract the file keeps with its reader — so there is a test that fails when a route is added
 * and the sentence is not.
 */

/**
 * Every addressable shape, and it is SHAPES the file's opening sentence counts rather than route
 * names: `#/tickets/TKT-142` and `#/tickets/TKT-142/session` are two addresses of one surface and
 * the sentence has said `six` since there were five names.
 */
const ALL: Route[] = [
  { name: 'home' },
  { name: 'requirements' },
  { name: 'requirement', id: 'REQ-014' },
  { name: 'ticket', id: 'TKT-142' },
  { name: 'ticket', id: 'TKT-142', tab: 'session' },
  { name: 'vocabulary' },
  { name: 'permissions' },
  { name: 'lanes' },
]

describe('the hash is the address', () => {
  it('the_seventh_surface_round_trips', async () => {
    expect(href({ name: 'lanes' })).toBe('#/lanes')
    expect(parse(href({ name: 'lanes' }))).toEqual({ name: 'lanes' })
    expect(parse('#/lanes')).toEqual({ name: 'lanes' })

    // every surface, so the seventh is not the only one anybody checked
    for (const route of ALL) expect(parse(href(route))).toEqual(route)

    // WEB-FEAT-001 — the eighth is the bare hash, and it is what the client opens on
    expect(href({ name: 'home' })).toBe('#/')
    expect(parse('#/')).toEqual({ name: 'home' })
    // §4.2's list keeps its own address, and the crumb that says `requirements` still goes there
    expect(parse('#/requirements')).toEqual({ name: 'requirements' })

    // and an unknown hash still resolves HOME rather than to a blank screen
    expect(parse('#/nowhere')).toEqual(HOME)
    expect(parse('#/lane')).toEqual(HOME)
    expect(parse('')).toEqual(HOME)

    // The file says how many there are, and a route added without the sentence is a lie left in a
    // comment. Read off disk relative to the project root — `import.meta.url` under vitest is the
    // dev server's http URL, not this file's path.
    const fs = await import('node:fs/promises')
    const source = await fs.readFile('src/routes.ts', 'utf8')
    expect(source).toMatch(/Eight addressable surfaces/)
    expect(new Set(ALL.map(href)).size).toBe(8)
  })
})
