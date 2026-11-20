import { describe, expect, it } from 'vitest'

import { LIVE_SURFACES, resolveScope } from '../gate/surfaces.mjs'
import { schemaCoverage } from '../gate/sweep.mjs'


const responses = { E8: {}, E10: {}, E18: {}, E99: {} }
const deferred = new Map([
  ['E8', 'section 8d'], ['E10', 'section 8d'], ['E18', 'section 10'],
  ['E99', 'a section that does not exist'],
])

describe('the scoped browser gate schema ledger', () => {
  it('records E18 as a scope skip only when every live surface is excluded', () => {
    const judged = new Set(['E8', 'E10', 'E99'])
    const staticOnly = resolveScope({ argv: [
      '--surfaces', 'shell-and-requirements,requirement-detail', '--themes', 'dark,light',
    ] })
    const result = schemaCoverage(responses, judged, deferred, staticOnly, LIVE_SURFACES)
    expect([...result.scopedOut]).toEqual(['E18'])
    expect(result.unjudged).toEqual([])

    for (const scope of [
      resolveScope({}),
      resolveScope({ argv: ['--surfaces', 'requirements-live'] }),
      resolveScope({ argv: ['--themes', 'dark'] }),
    ]) {
      const required = schemaCoverage(responses, judged, deferred, scope, LIVE_SURFACES)
      expect([...required.scopedOut]).toEqual([])
      expect(required.unjudged).toEqual(['E18'])
    }
  })

  it('still fails for every other unjudged node on a static-only scope', () => {
    const staticOnly = resolveScope({ argv: ['--surfaces', 'shell-and-requirements'] })
    const result = schemaCoverage(responses, new Set(['E8']), deferred, staticOnly, LIVE_SURFACES)
    expect([...result.scopedOut]).toEqual(['E18'])
    expect(result.unjudged).toEqual(['E10', 'E99'])
  })
})
