import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup, fireEvent, act } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { mountApp } from './helpers'
import { generate, penTokens } from '../scripts/gen-tokens.mjs'

afterEach(cleanup)

// Resolved on node:path, not on `new URL(…, import.meta.url)`: inside a test body jsdom's own
// URL is the global one, and it resolves a relative path against the document, not the module.
const HERE = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(HERE, rel), 'utf8')

const NOCTURNE = read('../design/nocturne.css')
const PEN = JSON.parse(read('../design/tokens.json'))
const TOKENS = read('../src/styles/tokens.css')

/**
 * The custom properties declared in the FIRST block matching `selector`.
 *
 * Written here rather than imported from the generator on purpose: a test that parses with the
 * generator's own parser can only prove the generator agrees with itself.
 */
function tokensIn(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(selector)
  if (start < 0) return new Map()
  const open = css.indexOf('{', start)
  let depth = 0
  let end = open
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') { depth -= 1; if (depth === 0) { end = i; break } }
  }
  const body = css.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, '')
  const out = new Map<string, string>()
  for (const decl of body.split(';')) {
    const at = decl.indexOf(':')
    if (at < 0) continue
    const name = decl.slice(0, at).trim()
    if (!name.startsWith('--')) continue
    out.set(name, decl.slice(at + 1).trim())
  }
  return out
}

/** The design's dark set is nocturne's :root plus `factory.pen`'s own variables.
 *  `tokens.json` is the .pen's export — a .pen is encrypted and only the Pencil MCP tools open
 *  one, which neither this test nor `npm run gen:tokens` can call. Change the design, re-export,
 *  and this test is what says whether the client followed. */
const designDark = new Map([
  ...tokensIn(NOCTURNE, ':root'),
  ...penTokens(PEN, 'dark'),
] as [string, string][])
/** And the light set is the same two sources, read symmetrically — nocturne's own
 *  `[data-theme="light"]` under the .pen's light values. WEB-FIX-326: while this side read the
 *  .pen alone, a nocturne token had a dark value and no light one, so the light theme inherited
 *  `:root` and every check here still passed. */
const designLight = new Map([
  ...tokensIn(NOCTURNE, '[data-theme="light"]'),
  ...penTokens(PEN, 'light'),
] as [string, string][])

const clientDark = tokensIn(TOKENS, ':root')
const clientLight = tokensIn(TOKENS, '[data-theme="light"]')

describe('F26 · theme', () => {
  it('tokens_match_design', () => {
    expect(designDark.size).toBeGreaterThan(40)
    expect(designLight.size).toBeGreaterThan(10)

    // nobody hand-edited the generated file
    expect(generate(NOCTURNE, PEN)).toBe(TOKENS)

    // every token the client defines is byte-equal to the design's value — in BOTH themes
    for (const [name, value] of clientDark) {
      expect(designDark.has(name), `:root --${name} is not a design token`).toBe(true)
      expect(clientDark.get(name), `dark ${name}`).toBe(designDark.get(name))
      expect(value).toBe(designDark.get(name))
    }
    for (const [name, value] of clientLight) {
      expect(designLight.has(name), `light ${name} is not a design token`).toBe(true)
      expect(value).toBe(designLight.get(name))
    }

    // and none of the design's tokens went missing on the way in
    for (const name of designDark.keys()) expect(clientDark.has(name), `missing ${name}`).toBe(true)
    for (const name of designLight.keys()) expect(clientLight.has(name), `missing light ${name}`).toBe(true)
  })

  it('no_colour_is_written_anywhere_but_the_token_file', () => {
    // A hard-coded #232532 in a component passes a token comparison and is still drift.
    const dir = join(HERE, '../src/styles')
    for (const file of readdirSync(dir)) {
      if (file === 'tokens.css') continue
      // comments are prose, and this file's own comments talk about the colours it may not use
      const css = readFileSync(join(dir, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
      expect(css.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [], `${file} hex literal`).toEqual([])
      expect(css.match(/\b(oklch|rgba?|hsla?)\(/g) ?? [], `${file} colour function`).toEqual([])
    }
  })

  it('dark_by_default_light_available_remembered', async () => {
    localStorage.clear()
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: [], total: 0, in_flight: 0, warnings: [] } },
    })
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    await act(async () => { fireEvent.click(screen.getByTestId('theme-toggle')) })
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    cleanup()
    await mountApp('#/requirements', {
      '/api/requirements': { body: { requirements: [], total: 0, in_flight: 0, warnings: [] } },
    })
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    localStorage.clear()
  })
})
