#!/usr/bin/env node
/**
 * The client's socket types, generated from docs/technical/contracts/socket.schema.json.
 *
 * ARCHITECTURE.md: "Types come from the schema by generation, so a field added on the server
 * cannot silently disagree with the field the UI reads." Hand-written types on the far side of
 * a wire are exactly how that happens, so this file exists and `src/types/socket.ts` is never
 * edited. `tests/socket.test.ts` asserts the checked-in output is byte-identical to what this
 * produces — drift is a red build, not a discussion.
 *
 *   npm run gen:types
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const SCHEMA = new URL('../../../docs/technical/contracts/socket.schema.json', import.meta.url)
const OUT = new URL('../src/types/socket.ts', import.meta.url)

const REFS = {
  '#/$defs/iso': 'string',
  '#/$defs/scope': 'Scope',
  '#/$defs/entry': 'TranscriptEntry',
}

/** One JSON-Schema property, as a TypeScript type. */
function tsType(node) {
  if (node.$ref) return REFS[node.$ref] ?? 'unknown'
  if ('const' in node) return typeof node.const === 'string' ? `'${node.const}'` : String(node.const)
  if (node.enum) return node.enum.map((v) => `'${v}'`).join(' | ')
  const types = Array.isArray(node.type) ? node.type : [node.type]
  const one = (t) => {
    if (t === 'string') return 'string'
    if (t === 'integer' || t === 'number') return 'number'
    if (t === 'boolean') return 'boolean'
    if (t === 'null') return 'null'
    if (t === 'object') return 'Record<string, unknown>'
    if (t === 'array') return 'unknown[]'
    return 'unknown'
  }
  return types.map(one).join(' | ')
}

/** `catalog.changed` → `CatalogChangedFrame` */
function interfaceName(message) {
  const camel = message
    .split(/[.\-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')
  return `${camel}Frame`
}

function wrap(text, indent = ' * ') {
  const words = text.split(/\s+/)
  const lines = []
  let line = ''
  for (const word of words) {
    if (line && (line + ' ' + word).length > 92) { lines.push(line); line = word }
    else line = line ? `${line} ${word}` : word
  }
  if (line) lines.push(line)
  return lines.map((l) => `${indent}${l}`.trimEnd()).join('\n')
}

export function generate(schema) {
  const envelope = schema.properties
  const messages = schema.$defs.message
  const names = Object.keys(messages)

  const out = []
  out.push('// GENERATED from factory/docs/technical/contracts/socket.schema.json — do not edit by hand.')
  out.push('// Regenerate with `npm run gen:types`; tests/socket.test.ts fails if this drifts.')
  out.push(`// contract: ${schema.contract}`)
  out.push('')

  const scope = schema.$defs.scope
  out.push('/**')
  out.push(wrap(scope.description))
  out.push(' */')
  out.push('export type Scope =')
  for (const [key, value] of Object.entries(scope.properties)) {
    out.push(`  | { ${key}: ${tsType(value)} }`)
  }
  out.push('')

  const entry = schema.$defs.entry
  out.push('/**')
  out.push(wrap(entry.description))
  out.push(' */')
  out.push('export interface TranscriptEntry {')
  for (const [key, value] of Object.entries(entry.properties)) {
    const optional = (entry.required ?? []).includes(key) ? '' : '?'
    out.push(`  ${key}${optional}: ${tsType(value)}`)
  }
  out.push('  [k: string]: unknown')
  out.push('}')
  out.push('')

  for (const name of names) {
    const message = messages[name]
    const props = { ...envelope, ...message.properties }
    const required = new Set([...(schema.required ?? []), ...(message.required ?? [])])
    const other = message.direction === 'client' ? 'server' : 'client'
    out.push('/**')
    out.push(` * \`${name}\` · ${message.direction} → ${other}`)
    out.push(' *')
    out.push(wrap(message.description))
    out.push(' */')
    out.push(`export interface ${interfaceName(name)} {`)
    for (const [key, value] of Object.entries(props)) {
      out.push(`  ${key}${required.has(key) ? '' : '?'}: ${tsType(value)}`)
    }
    out.push('}')
    out.push('')
  }

  const byDirection = (want) => names.filter((n) => messages[n].direction === want)
  const union = (list) => list.map((n) => interfaceName(n)).join('\n  | ')

  out.push('/** Everything the client may send. */')
  out.push(`export type ClientFrame =\n  | ${union(byDirection('client'))}`)
  out.push('')
  out.push('/** Everything the server may send. */')
  out.push(`export type ServerFrame =\n  | ${union(byDirection('server'))}`)
  out.push('')
  out.push('export type Frame = ClientFrame | ServerFrame')
  out.push('')
  out.push('/** Every message name in the contract, in schema order. */')
  out.push(`export const MESSAGES = [\n${names.map((n) => `  '${n}',`).join('\n')}\n] as const`)
  out.push('')

  return out.join('\n')
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const schema = JSON.parse(readFileSync(SCHEMA, 'utf8'))
  writeFileSync(OUT, generate(schema))
  console.log(`[+] ${fileURLToPath(OUT)}`)
}
