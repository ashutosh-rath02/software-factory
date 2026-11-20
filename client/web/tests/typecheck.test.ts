import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * WEB-FIX-200 — the suite typechecks, so a green suite and a building tree are one claim.
 *
 * `main` was pushed not building. `npm run test` was green — `218 passed (218)` — because vitest
 * transpiles and never typechecks, and `tsc` ran only inside `npm run build`, which the ticket
 * that broke it (`SRV-FEAT-180`, a factory-server ticket) had no reason to run. A generated type
 * gained a required member, its one consumer did not, and every gate in the sequence said yes.
 *
 * So the typechecker runs here, over the same project `npm run build` compiles. It is the same
 * compiler reading the same `tsconfig.json`: there is no second definition of what "typechecks"
 * means, and no way to be green here and red there.
 */

const HERE = dirname(fileURLToPath(import.meta.url))
const CLIENT = join(HERE, '..')
const TSC = join(CLIENT, 'node_modules', 'typescript', 'bin', 'tsc')

function typecheck(project: string) {
  const run = spawnSync(process.execPath, [TSC, '--noEmit', '-p', project], {
    cwd: CLIENT, encoding: 'utf8',
  })
  return { code: run.status, out: `${run.stdout ?? ''}${run.stderr ?? ''}` }
}

describe('the client typechecks', () => {
  it('test_the_client_build_typechecks_every_generated_member', () => {
    const { code, out } = typecheck('tsconfig.json')
    expect(out.trim()).toBe('')
    expect(code).toBe(0)
  }, 180_000)

  it('test_the_typecheck_case_fails_on_a_consumer_that_drops_a_generated_member', () => {
    // MANDATORY NEGATIVE — and it is this ticket's own defect, kept executable.
    //
    // The same function the live case runs, given a consumer written the way `lanes.tsx:149` was
    // written on `7ef95ead`: a `Resource` literal missing `holder_alive`. It has to come back
    // TS2741, or the case above is a check that cannot fail and this file is decoration.
    //
    // Under `node_modules/` deliberately: `tsconfig.json` includes `src`, `tests` and `scripts`,
    // and a file that does not compile dropped into any of them would break the live case above
    // instead of this one — a negative that breaks its own control proves nothing.
    const work = mkdtempSync(join(CLIENT, 'node_modules', '.typecheck-negative-'))
    try {
      writeFileSync(join(work, 'tsconfig.json'), JSON.stringify({
        extends: '../../tsconfig.json', include: ['consumer.ts'],
      }))
      writeFileSync(join(work, 'consumer.ts'), [
        "import type { Resource } from '../../src/types/api'",
        '',
        'export const row: Resource = {',
        "  name: 'climate-chamber', exclusive: true, description: null,",
        "  held_by: 'lane/01', queue: [],",
        '}',
        '',
      ].join('\n'))

      const { code, out } = typecheck(join(work, 'tsconfig.json'))
      expect(code).not.toBe(0)
      expect(out).toContain('TS2741')
      expect(out).toContain("Property 'holder_alive' is missing")
    } finally {
      rmSync(work, { recursive: true, force: true })
    }
  }, 180_000)
})
