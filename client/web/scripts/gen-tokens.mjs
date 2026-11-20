#!/usr/bin/env node
/**
 * The client's design tokens, generated from the delivered design.
 *
 * design/README.md: "the token values and component names here are what the implementation
 * is gated against, so a colour drifting is a red build rather than a discussion." Sixty hex
 * values transcribed by hand is precisely how a colour drifts, so they are not transcribed —
 * this reads them, and `tests/theme.test.tsx` asserts the checked-in file is byte-identical to
 * what this produces AND that every value still equals the design's, in both themes.
 *
 * Two blocks, each layered from the same two sources in the same order — the design system
 * underneath, the product's own variables on top:
 *   :root                 nocturne.css :root                 + factory.pen's dark values
 *   [data-theme="light"]  nocturne.css [data-theme="light"]  + factory.pen's light values
 *
 * Both halves of the light block matter. `factory.pen`'s variables are themed and nocturne's are
 * not, so a nocturne token the client paints reached the light theme by inheriting `:root` and
 * kept a value chosen for a dark ground — the accent at 2.62:1 on a light panel (WEB-FIX-326).
 *
 *   npm run gen:tokens
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const NOCTURNE = new URL('../design/nocturne.css', import.meta.url)
const PEN = new URL('../design/tokens.json', import.meta.url)
const OUT = new URL('../src/styles/tokens.css', import.meta.url)

/**
 * The custom properties of the first block matching `selector`, in declaration order.
 *
 * Comments are stripped BEFORE splitting on `;` — nocturne's accent carries a review note
 * containing a semicolon, and splitting first turns one declaration into three.
 */
export function tokensIn(css, selector) {
  const start = css.indexOf(selector)
  if (start < 0) return []
  const open = css.indexOf('{', start)
  let depth = 0
  let end = open
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') { depth -= 1; if (depth === 0) { end = i; break } }
  }
  const body = css.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, '')
  const out = []
  for (const decl of body.split(';')) {
    const at = decl.indexOf(':')
    if (at < 0) continue
    const name = decl.slice(0, at).trim()
    if (!name.startsWith('--')) continue
    out.push([name, decl.slice(at + 1).trim()])
  }
  return out
}

function block(selector, pairs) {
  const seen = new Map()
  for (const [name, value] of pairs) seen.set(name, value)
  const lines = [...seen].map(([name, value]) => `  ${name}: ${value};`)
  return `${selector} {\n${lines.join('\n')}\n}\n`
}

/** The design's own variables, one theme at a time. `factory.pen` is the source; `tokens.json`
 *  is its export, because a .pen is encrypted and only the Pencil MCP tools can open one — which
 *  a shell script cannot call. Change the design, re-export, and the diff shows what moved. */
export function penTokens(pen, theme) {
  return Object.entries(pen.variables).map(([name, byTheme]) => [`--${name}`, byTheme[theme]])
}

export function generate(nocturne, pen) {
  const dark = [...tokensIn(nocturne, ':root'), ...penTokens(pen, 'dark')]
  const light = [...tokensIn(nocturne, '[data-theme="light"]'), ...penTokens(pen, 'light')]

  return [
    '/* GENERATED from factory/client/web/design/nocturne.css and factory.pen — do not edit by hand.',
    '   Regenerate with `npm run gen:tokens`; tests/theme.test.tsx fails if this drifts.',
    '',
    '   The design is accepted as delivered and is the source of truth for anything visual.',
    '   No colour, radius, space or font may be written anywhere else in this client. */',
    '',
    block(':root', dark),
    block('[data-theme="light"]', light),
  ].join('\n')
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const css = generate(readFileSync(NOCTURNE, 'utf8'), JSON.parse(readFileSync(PEN, 'utf8')))
  writeFileSync(OUT, css)
  console.log(`[+] ${fileURLToPath(OUT)}`)
}
