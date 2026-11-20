import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { deferred, sweepRequest, FIXTURE_BODIES } from '../gate/sweep.mjs'

/**
 * WEB-FIX-213 — a response node says which verb reaches it, and the sweep asks by that verb.
 *
 * `http.schema.json` recorded a `path` and no method, and `gate/browser-gate.mjs`'s section 6b
 * GETs every node it holds. `E26` is `POST /api/tickets/ids`, so the GET matched
 * `GET /api/tickets/{id}` with `id` bound to the literal string `ids` and the sweep validated
 * ANOTHER endpoint's 404 against `E26`'s schema — measured with the real server and curl in
 * `docs/reports/WEB-FIX-213/01-repro-get-vs-post.log`:
 *
 *   GET  /api/tickets/ids  → HTTP 404 {"error":{"code":"not_found","message":"no ticket with id ids"…
 *   POST /api/tickets/ids  → HTTP 201 {"id":"TKT-171","prefix":"TKT","series":"","number":171}
 *
 * The route table below is PARSED from `app/api/manager.h`, never transcribed. A test that agrees
 * with a copy of the C++ is a second source of truth waiting to disagree, which is the same defect
 * one file to the left.
 */

const HERE = dirname(fileURLToPath(import.meta.url))
const read = (rel: string) => readFileSync(join(HERE, rel), 'utf8')

const SCHEMA = JSON.parse(read('../../../docs/technical/contracts/http.schema.json'))
const NODES: [string, any][] = Object.entries(SCHEMA.responses)

/** Every route the server really registers, as `{method, path, handler}`. */
function routeTable() {
  const source = read('../../../server/app/api/manager.h')
  const rows = [...source.matchAll(
    /HTTP_(?:ASYNC_)?SERVICE\(\s*(GET|POST|PUT|DELETE|PATCH)\s*,\s*"([^"]+)"\s*,\s*(\w+)\s*\)/g)]
  return rows.map((m) => ({ method: m[1], path: m[2], handler: m[3] }))
}

/** Which route a request reaches: an exact path wins, otherwise one `{placeholder}` per segment. */
function routeFor(method: string, path: string) {
  const want = path.split('/')
  const candidates = routeTable().filter((r) => {
    if (r.method !== method) return false
    const have = r.path.split('/')
    if (have.length !== want.length) return false
    return have.every((seg, i) => seg === want[i] || /^\{.+\}$/.test(seg))
  })
  const exact = candidates.find((r) => r.path === path)
  return exact ?? candidates[0] ?? null
}

const ROUTES = routeTable()

describe('WEB-FIX-213 · the verb is part of the node, and the sweep asks by it', () => {
  it('test_every_response_node_declares_the_verb_that_reaches_it', () => {
    // The route table is real, or nothing below means anything.
    expect(ROUTES.length).toBeGreaterThan(20)
    expect(ROUTES).toContainEqual({ method: 'POST', path: '/api/tickets/ids', handler: 'AllocateTicketId' })

    const missing = NODES.filter(([, node]) => typeof node.method !== 'string' || !node.method)
    expect(missing.map(([id]) => id)).toEqual([])

    // and it is one of the verbs manager.h declares FOR THAT PATH — not merely a verb
    const wrong = NODES.filter(([, node]) =>
      !ROUTES.some((r) => r.method === node.method && r.path === node.path))
    expect(wrong.map(([id, n]) => `${id} ${n.method} ${n.path}`)).toEqual([])

    // the status the described body arrives with is declared too: E26 answers 201, not 200, so a
    // sweep taught only the verb would fail it for a new wrong reason (SPEC.md §5 · E26)
    const statusless = NODES.filter(([, node]) => !Number.isInteger(node.status))
    expect(statusless.map(([id]) => id)).toEqual([])
    expect(SCHEMA.responses.E26.status).toBe(201)
  })

  it('test_the_sweep_uses_the_declared_verb', () => {
    // THE REQUEST THE SWEEP BUILDS, not the gate's summary line: a sweep that skipped E26 would
    // turn the summary green, and that is the cheap fix this refuses.
    const e26 = sweepRequest('E26', SCHEMA.responses.E26)
    expect(e26.method).toBe('POST')
    expect(e26.path).toBe('/api/tickets/ids')
    expect(e26.status).toBe(201)
    expect(e26.body).toEqual(FIXTURE_BODIES.E26)

    // every node the sweep reaches is asked by the verb its node declares, and none is dropped
    for (const [id, node] of NODES) {
      if (deferred(node)) continue
      const built = sweepRequest(id, node)
      expect(`${id} ${built.method}`).toBe(`${id} ${node.method}`)
      expect(built.path).not.toMatch(/\{/)
    }

    // a node that declares no verb is REFUSED, never swept with a guess — the guess is the defect
    expect(() => sweepRequest('EX', { path: '/api/health', status: 200 })).toThrow(/method/)
  })

  it('test_a_node_whose_path_collides_with_another_route_is_not_read_as_that_route', () => {
    // MANDATORY NEGATIVE, transcribed from the reproduction rather than from the criterion's name.
    //
    // With the verb declared, a request for E26 may not be answered by E5. This is asserted against
    // the server's REAL route table: the request the sweep builds must reach `AllocateTicketId`,
    // and the GET of the same path — what the sweep built before this ticket — reaches `Ticket`,
    // a different endpoint that answers `404 not_found` for a ticket named `ids`.
    const built = sweepRequest('E26', SCHEMA.responses.E26)
    expect(routeFor(built.method, built.path)?.handler).toBe('AllocateTicketId')

    // the collision itself, kept executable: the path alone does NOT identify the endpoint
    expect(routeFor('GET', '/api/tickets/ids')?.handler).toBe('Ticket')
    expect(routeFor('GET', '/api/tickets/ids')?.path).toBe('/api/tickets/{id}')

    // and E26's node describes a body E5's 404 cannot satisfy — a check that only asserted
    // `status === 200` would pass on any 200 from any route and is not this
    expect(SCHEMA.responses.E26.required).toEqual(['id', 'prefix', 'series', 'number'])
  })

  it('test_a_second_non_get_endpoint_is_swept_by_its_own_verb', () => {
    // GENERALITY, and it is the point: the next non-GET endpoint must not need a line here.
    // A SECOND real non-GET route out of manager.h, driven through the same builder, with nothing
    // added anywhere for it.
    const second = ROUTES.find((r) => r.method === 'DELETE' && r.path === '/api/permissions/rules/{rule_id}')
    expect(second).toBeTruthy()

    const built = sweepRequest('E20', { path: second!.path, method: second!.method, status: 200 })
    expect(built.method).toBe('DELETE')
    expect(routeFor(built.method, second!.path)?.handler).toBe('RevokeRule')

    // and a third, with a body-carrying verb — the dispatch is on `method`, not on a list of ids
    const third = ROUTES.find((r) => r.method === 'PUT' && r.path === '/api/permissions/policy')
    expect(sweepRequest('E21', { path: third!.path, method: third!.method, status: 200 }).method)
      .toBe('PUT')
  })

  it('test_no_response_node_goes_unswept', () => {
    // A sweep that stops asking is the defect one step further along, so the set of nodes section
    // 6b does not reach STATES WHAT CHECKS IT instead of living in a `const LATER` nobody can test.
    const exempt = NODES.filter(([, node]) => deferred(node))
    for (const [id, node] of exempt) {
      expect(`${id}: ${typeof node.checked_by}`).toBe(`${id}: string`)
      expect(node.checked_by.length).toBeGreaterThan(0)
    }

    // These paths need a recorded run or claimed lane, so later sections judge them with real ids.
    expect(exempt.map(([id]) => id).sort()).toEqual(['E10', 'E18', 'E33', 'E34', 'E8'])
    for (const [, node] of exempt) expect(node.path).toMatch(/\{/)

    // every node is one or the other, and the union is the whole file
    const swept = NODES.filter(([, node]) => !deferred(node)).map(([id]) => id)
    expect([...swept, ...exempt.map(([id]) => id)].sort())
      .toEqual(NODES.map(([id]) => id).sort())
  })
})
