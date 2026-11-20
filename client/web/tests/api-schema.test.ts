import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { generate } from '../scripts/gen-api-types.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(HERE, rel), 'utf8')

/**
 * `docs/technical/contracts/http.schema.json` has one job: describe what the server really sends, so a drift turns a
 * gate red instead of blanking a screen. `SRV-FEAT-022` shipped `roles` on E5 and could not add it
 * here — that file is validated by the browser gate, which lives in this project, and `022` was
 * scoped out of it. A field the schema does not mention is the schema quietly stopping doing the
 * one thing it is for.
 */
describe('the HTTP contract · E5 describes what F31 put on it', () => {
  it('e5_roles_is_described', () => {
    const schema = JSON.parse(read('../../../docs/technical/contracts/http.schema.json'))
    const e5 = schema.responses.E5

    // the field itself, as an array of the shape SPEC.md E5 gives
    expect(e5.properties.roles).toEqual({
      type: 'array', items: { $ref: '#/$defs/role' },
    })

    // ABSENT, not empty: E5 answers byte for byte what it did before F31 when no role was reached
    expect(e5.required).not.toContain('roles')

    const role = schema.$defs.role
    expect(role.title).toBe('Role')
    expect(role.required).toEqual(['role', 'stage', 'state'])
    expect(role.properties.state.enum).toEqual(['pending', 'started'])
    // `lane` and `run_id` exist only once a role has one, so neither is required
    expect(Object.keys(role.properties)).toEqual(['role', 'stage', 'state', 'lane', 'run_id'])

    // and the generated file is the schema's own output, byte for byte. A generated file that has
    // drifted from its schema is the failure this test exists for.
    const checkedIn = read('../src/types/api.ts')
    expect(generate(schema)).toBe(checkedIn)
    expect(checkedIn).toContain('export interface Role ')
    expect(checkedIn).toContain('roles?: Role[]')
  })
})
