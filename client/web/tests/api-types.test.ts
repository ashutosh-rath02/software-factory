import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { generate } from '../scripts/gen-api-types.mjs'
import { METHODS, PATHS } from '../src/types/api'

const HERE = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(HERE, rel), 'utf8')

describe('the HTTP contract, client half', () => {
  it('generated_types_match_the_schema', () => {
    const schema = JSON.parse(read('../../../docs/technical/contracts/http.schema.json'))
    const checkedIn = read('../src/types/api.ts')

    // Byte-identical, not "equivalent". Drift is a red build, not a discussion — the same
    // bargain src/types/socket.ts already makes with docs/technical/contracts/socket.schema.json.
    expect(generate(schema)).toBe(checkedIn)

    // and it is really generated from THIS schema, not a stale copy that happens to compile
    expect(checkedIn).toContain('export interface Requirements')
    expect(checkedIn).toContain('groups: Group[]')
  })

  it('the_paths_are_the_ones_SPEC_5_names', () => {
    // A caller cannot invent a path: it comes off the schema with the endpoint number attached.
    expect(PATHS).toEqual({
      E1: '/api/health',
      E2: '/api/manifest',
      E3: '/api/requirements',
      E4: '/api/requirements/{id}',
      E5: '/api/tickets/{id}',
      E6: '/api/tickets/{id}/report',
      // E7, E8 and E10 arrived with WEB-FEAT-016 — the RUNS tab, the run it replays, and the
      // DIFF tab. Same story as E9: the server has served all three since SRV-FEAT-010 and 015.
      E7: '/api/tickets/{id}/runs',
      E8: '/api/runs/{run_id}/transcript',
      // E9 arrived with WEB-FEAT-013: the server has served it since SRV-FEAT-008 and the
      // client had no generated type for it, so SPEC.md §4.4's rail had nothing to read.
      E9: '/api/lanes',
      E10: '/api/lanes/{lane}/diff',
      // E18 returns the resolved agent, driver, and model for a newly started run.
      E18: '/api/lanes/{lane}/runs',
      // E11 arrived with WEB-FEAT-018 and is the ONLY permissions endpoint here: E20, E21 and
      // E23 are writes whose bodies the client never reads — every one of them broadcasts M12
      // or M7, and the socket is what changes the screen (SPEC.md §4.12).
      E11: '/api/permissions/rules',
      // E26 arrived with SRV-FIX-188 — the tool's ticket-id allocator. It is a POST, and it is
      // here for the reason E20, E21 and E23 are not: those broadcast a frame and the socket
      // changes the screen, while this one ANSWERS with the id it allocated and there is nowhere
      // else to read it. `PATHS` is generated from every response body the schema declares, not
      // from the set of calls the client happens to make today, so a caller that does not exist
      // yet does not make its path optional here (SPEC.md §5, E26).
      E26: '/api/tickets/ids',
      // E30 arrived with SRV-FEAT-313 — §4.18's home surface. It is ONE endpoint for three
      // panels by decision: `Where the work is`, `Needs you` and `Unpushed work` are three views
      // of one question, and three endpoints would be three answers about one repository at
      // three instants, with the counts in the first free to disagree with the rows in the
      // second while each stayed correct (SPEC.md §4.18, §5 E30). The path lands here before
      // WEB-FEAT-314's surface reads it, so the two build in parallel against one contract.
      E30: '/api/home',
      // E29 arrived with SRV-FEAT-311 — SPEC.md §4.17's ticket table. It is `GET /api/tickets`,
      // one segment shorter than `GET /api/tickets/{id}` and a different endpoint from
      // `POST /api/tickets/ids`; `tests/api-methods.test.ts` asserts against `manager.h`'s real
      // route table that the three do not answer for one another.
      E29: '/api/tickets',
      // E31 arrived with SRV-FEAT-315 — every run in the repository, newest first, which is what
      // §4.19's Runs surface draws. It is not E7 with a filter: E7 answers *what has run on this
      // ticket* and this answers *what has run*, and both stay because a list and a detail that
      // share one row cannot disagree about an outcome (SPEC.md §5, E31).
      E31: '/api/runs',
      // E28 arrived with the Escapes surface — SPEC.md §4.16's ledger. It is a GET over a file
      // the repository already keeps, and the tool understands none of its columns: the header
      // row is served verbatim and `ticket_id` is the only thing the server adds, so a cell can
      // be linked without the client parsing markdown (SPEC.md §5, E28).
      E28: '/api/escapes',
      // E27 arrived with the Wiki surface — SPEC.md §4.15's pages. It takes no parameters
      // because filtering a handful of hand-written pages is the client's and the whole set is
      // one answer; `front` carries every frontmatter key the tool does not understand, in the
      // order the document wrote them, exactly as `display.ticket_fields` reaches E5's `extras`.
      E27: '/api/wiki',
      // E32 arrived with SRV-FEAT-501 — SPEC.md §4.21's Metrics surface. It is the only endpoint
      // here that COMPUTES rather than reads: the playbook's stage numbers over a window of N
      // days, reduced on request from the run records and documents the tool already holds, with
      // nothing indexed and nothing written (SPEC.md §5, E32; MODEL.md `S2`).
      E32: '/api/metrics',
      E33: '/api/lanes/{lane}/routing',
      E34: '/api/lanes/{lane}/routing/{request_id}',
    })
  })

  it('the_methods_are_the_ones_SPEC_5_names', () => {
    // WEB-FIX-213. A path is not an address on its own, and this file is where that was learned:
    // `E26` is a POST, the comment above says so, and the one consumer that builds requests from
    // this schema — `gate/browser-gate.mjs`'s section 6b — GET it, matched `GET /api/tickets/{id}`
    // with `id` bound to the literal string `ids`, and validated E5's 404 against E26's schema.
    // So the verb travels wherever the path travels, generated from the same node.
    expect(METHODS).toEqual({
      E1: 'GET',
      E2: 'GET',
      E3: 'GET',
      E4: 'GET',
      E5: 'GET',
      E6: 'GET',
      E7: 'GET',
      E8: 'GET',
      E9: 'GET',
      E10: 'GET',
      E11: 'GET',
      E18: 'POST',
      E26: 'POST',
      E30: 'GET',
      E29: 'GET',
      E31: 'GET',
      E28: 'GET',
      E27: 'GET',
      E32: 'GET',
      E33: 'POST',
      E34: 'GET',
    })
    // every endpoint the client can name, it can also address
    expect(Object.keys(METHODS)).toEqual(Object.keys(PATHS))
  })
})
