/**
 * The request `browser-gate.mjs`'s section 6b issues for one node of `http.schema.json`.
 *
 * WEB-FIX-213. It used to build a GET for every node, because the schema recorded a `path` and no
 * verb. `E26` is `POST /api/tickets/ids`, so the GET reached `GET /api/tickets/{id}` — a DIFFERENT
 * endpoint — with `id` bound to the literal string `ids`, and the sweep validated that endpoint's
 * 404 against `E26`'s schema. Measured with the real server and curl, no browser involved
 * (`docs/reports/WEB-FIX-213/01-repro-get-vs-post.log`):
 *
 *   GET  /api/tickets/ids  → 404 {"error":{"code":"not_found","message":"no ticket with id ids"…}}
 *   POST /api/tickets/ids  → 201 {"id":"TKT-171","prefix":"TKT","series":"","number":171}
 *
 * It lives in its own module so `tests/api-methods.test.ts` can assert on the request this BUILDS
 * rather than on the gate's summary line — a sweep that skipped `E26` would turn that line green,
 * and it is the cheap fix the ticket refuses.
 *
 * THE SCHEMA CARRIES THE CONTRACT; THIS FILE CARRIES THE FIXTURE. `method` and `status` are facts
 * about the wire and live in `http.schema.json`, where every consumer generates from them.
 * `TKT-139` and `{"prefix":"TKT"}` are documents in `gate/fixture-repo` — the same kind of value
 * the `{id}` substitution has always been, and the same place it has always lived.
 */

/** What `{id}` is bound to, per node. */
export const FIXTURE_IDS = { E4: 'REQ-014', default: 'TKT-139' }

/**
 * What a body-carrying verb sends, per node — the fixture repository's own values.
 *
 * `TKT` is the prefix `gate/fixture-repo`'s tickets use, so `E26` answers `201` with an id from
 * that series. A node whose verb needs a body and has no entry here sends none and the server
 * refuses it loudly, which is the failure that gets fixed rather than the one that gets missed.
 */
export const FIXTURE_BODIES = { E26: { prefix: 'TKT' } }

/**
 * Why section 6b cannot reach this node — or `null`, meaning it can.
 *
 * This is `const LATER = new Set(['E8', 'E10'])` turned inside out. A set the gate kept privately
 * was a set the schema could not be tested against and one nobody could see growing; the node now
 * states what checks it instead, and `tests/api-methods.test.ts` plus the gate's own end-of-run
 * ledger make that statement an assertion. The set of nodes nobody sweeps cannot grow by one line.
 */
export function deferred(node) {
  return node.checked_by ?? null
}

/** E18 belongs to section 10, which a static-only scope deliberately excludes. */
export function schemaCoverage(responses, judged, deferredNodes, scope, liveSurfaces) {
  const scopedOut = new Set()
  if (responses.E18 && deferredNodes.has('E18') && !judged.has('E18')
      && !liveSurfaces.some(([name]) => scope.inScope(name))) {
    scopedOut.add('E18')
  }
  const unjudged = Object.keys(responses).filter((id) => !judged.has(id) && !scopedOut.has(id))
  return { scopedOut, unjudged }
}

/**
 * The request for one node: the verb it declares, its path with the fixture's ids bound in, the
 * status its body arrives with, and a body where the verb carries one.
 *
 * A node that declares no verb is REFUSED here rather than swept with a guess. The guess is the
 * defect — it is what turned `E26` into a call on `E5`.
 */
export function sweepRequest(id, node) {
  if (typeof node.method !== 'string' || !node.method) {
    throw new Error(`${id}: no method in http.schema.json — a node that does not say which verb `
      + 'reaches it cannot be swept, and guessing GET is WEB-FIX-213')
  }
  const path = node.path.replace('{id}', FIXTURE_IDS[id] ?? FIXTURE_IDS.default)
  return { method: node.method, path, status: node.status ?? 200, body: FIXTURE_BODIES[id] }
}
