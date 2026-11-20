#!/usr/bin/env node
/**
 * The client's HTTP response types, generated from docs/technical/contracts/http.schema.json.
 *
 * The same bargain `gen-socket-types.mjs` makes with the socket schema, for the half of the wire
 * that is HTTP. `SRV-FEAT-005`'s report handed these types here by name: "the bodies are the HTTP
 * endpoints' and those types are WEB-FEAT-006's to generate."
 *
 * A schema nothing checks is a second source of truth waiting to disagree, so this one is
 * checked twice: `tests/api-types.test.ts` asserts the checked-in file is byte-identical to what
 * this produces, and `gate/browser-gate.mjs` validates the REAL server's live responses against
 * the same file. A drift between this and the C++ turns a gate red rather than blanking a screen.
 *
 *   npm run gen:api-types
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const SCHEMA = new URL('../../../docs/technical/contracts/http.schema.json', import.meta.url)
const OUT = new URL('../src/types/api.ts', import.meta.url)

function refName(ref) {
  const leaf = ref.split('/').pop()
  return leaf.charAt(0).toUpperCase() + leaf.slice(1)
}

function tsType(node) {
  if (node.$ref) return refName(node.$ref)
  // A closed set on the wire is a closed set in the types — `gen-socket-types.mjs` has rendered
  // `enum` this way since the socket contract was written, and this half of the wire was simply
  // lagging. Without it a verb the client must not invent renders as `string`, which is a verb
  // the compiler cannot catch (WEB-FEAT-173, SPEC.md §9.2).
  if (node.enum) return node.enum.map((v) => `'${v}'`).join(' | ')
  if (node.anyOf) return node.anyOf.map(tsType).join(' | ')
  if (node.type === 'array') {
    const item = tsType(node.items ?? {})
    return item.includes(' | ') ? `(${item})[]` : `${item}[]`
  }
  const types = Array.isArray(node.type) ? node.type : [node.type]
  const one = (t) => {
    if (t === 'string') return 'string'
    if (t === 'integer' || t === 'number') return 'number'
    if (t === 'boolean') return 'boolean'
    if (t === 'null') return 'null'
    if (t === 'object') return node.properties ? 'INLINE' : 'Record<string, unknown>'
    return 'unknown'
  }
  return types.map(one).join(' | ')
}

function wrap(text, indent = ' * ') {
  const words = String(text).split(/\s+/)
  const lines = []
  let line = ''
  for (const word of words) {
    if (line && (line + ' ' + word).length > 92) { lines.push(line); line = word }
    else line = line ? `${line} ${word}` : word
  }
  if (line) lines.push(line)
  return lines.map((l) => `${indent}${l}`.trimEnd()).join('\n')
}

/** An inline object literal, so a nested shape stays where it is described. */
function inlineObject(node, indent) {
  const required = new Set(node.required ?? [])
  const pad = ' '.repeat(indent)
  const lines = Object.entries(node.properties ?? {}).map(([key, value]) => {
    const type = tsType(value)
    // A NULLABLE inline object is still an inline object. `tsType` returns `INLINE | null` for
    // `type: ["object", "null"]` with properties, and an equality check against `'INLINE'` missed
    // it — emitting the literal word INLINE into the generated file, where `tsc` then failed with
    // `Cannot find name 'INLINE'` (WEB-FIX-349). The shape is rendered and the null re-attached.
    const rendered = type.startsWith('INLINE')
      ? inlineObject(value, indent + 2) + (type.includes('null') ? ' | null' : '')
      : type
    return `${pad}  ${key}${required.has(key) ? '' : '?'}: ${rendered}`
  })
  return `{\n${lines.join('\n')}\n${pad}}`
}

function iface(name, node) {
  const out = []
  out.push('/**')
  out.push(wrap(node.description ?? name))
  out.push(' */')
  out.push(`export interface ${name} ${inlineObject(node, 0)}`)
  out.push('')
  return out.join('\n')
}

export function generate(schema) {
  const out = []
  out.push('// GENERATED from factory/docs/technical/contracts/http.schema.json — do not edit by hand.')
  out.push('// Regenerate with `npm run gen:api-types`; tests/api-types.test.ts fails if this drifts,')
  out.push('// and gate/browser-gate.mjs validates the real server against the same schema.')
  out.push(`// contract: ${schema.contract}`)
  out.push('')

  for (const [key, node] of Object.entries(schema.$defs)) {
    out.push(iface(node.title ?? refName(key), node))
  }
  for (const [id, node] of Object.entries(schema.responses)) {
    out.push(`/** \`${id}\` · \`${node.path}\` */`)
    out.push(iface(node.title, node).replace(/^\/\*\*\n/, '/**\n'))
  }

  out.push('/** Every response body this client reads, by the endpoint number SPEC.md §5 gives it. */')
  out.push('export interface Responses {')
  for (const [id, node] of Object.entries(schema.responses)) {
    out.push(`  ${id}: ${node.title}`)
  }
  out.push('}')
  out.push('')
  out.push('/** The paths, so a caller cannot invent one. */')
  out.push('export const PATHS = {')
  for (const [id, node] of Object.entries(schema.responses)) {
    out.push(`  ${id}: '${node.path}',`)
  }
  out.push('} as const')
  out.push('')
  // And the verbs, for the same reason and out of the same node (WEB-FIX-213). A path on its own
  // is not an address: `E26` is `POST /api/tickets/ids`, the browser gate's schema sweep had only
  // the path, GET it, reached `GET /api/tickets/{id}` with `id` bound to the literal string `ids`,
  // and judged E5's 404 against E26's shape. A caller that cannot invent a path could still invent
  // the verb, so the two travel together.
  out.push('/** The verbs that reach them, so a caller cannot invent one of those either. */')
  out.push('export const METHODS = {')
  for (const [id, node] of Object.entries(schema.responses)) {
    out.push(`  ${id}: '${node.method}',`)
  }
  out.push('} as const')
  out.push('')

  return out.join('\n')
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const schema = JSON.parse(readFileSync(SCHEMA, 'utf8'))
  writeFileSync(OUT, generate(schema))
  console.log(`[+] ${fileURLToPath(OUT)}`)
}
