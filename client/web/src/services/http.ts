import type {
  Diff, ErrorBody, Health, Home, Lane, Lanes, Manifest, PermissionRules, Remedy, Report,
  Metrics, Requirement, Requirements, RunHistory, Ticket, Transcript, Tickets, RunIndex,
  Escapes, Wiki} from '../types/api'
import type { Decision, Policy } from './permissions'

/**
 * The only thing in the client that makes an HTTP request.
 *
 * `services/` is the only place that talks to the wire — a page that fetches is a page that will
 * drift. It knew only GET until this batch, where two tickets needed the same thing at once:
 * `WEB-FEAT-014` for E14 and E15 — the two things SPEC.md §4.7's single control does — and
 * `WEB-FEAT-018` for E20, E21 and E23, SPEC.md §4.12's. They arrived as two primitives, `post()`
 * and `send()`; integration kept `send()`, because a POST-only helper is the general one with three
 * verbs deleted, and two ways to write is how the next surface picks the wrong one. Five calls, one
 * primitive, and the rule both tickets quoted still holds: a surface that wants to write adds a call
 * here, in a review, on purpose. Every one of the five is an explicit click or an explicit `Enter` —
 * nothing is sent on mount, on focus, or on reconnect — and there is still no put() and no del()
 * beyond the two `send()` already carries.
 *
 * A 404 is DATA here, not an exception. E6's 404 is the normal case — it is what 4.9's empty
 * state renders — and a client that logged it as a failure would fill a console with the
 * ordinary state of a ticket nobody has finished.
 */

export type Fetched<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: ErrorBody['error'] | null }

export function detailPath(result: Fetched<unknown>): string | undefined {
  if (result.ok) return undefined
  const detail = result.error?.detail as Record<string, unknown> | undefined
  return typeof detail?.path === 'string' ? detail.path : undefined
}

async function get<T>(path: string, params?: Record<string, string>): Promise<Fetched<T>> {
  const query = params ? `?${new URLSearchParams(params).toString()}` : ''
  const response = await fetch(`${path}${query}`)
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (response.status >= 200 && response.status < 300) {
    return { ok: true, status: response.status, data: body as T }
  }
  const error = (body as ErrorBody | null)?.error ?? null
  return { ok: false, status: response.status, error }
}

/** E1 — version, repository, uptime. */
export const getHealth = () => get<Health>('/api/health')

/** E2 — the resolved manifest. */
export const getManifest = () => get<Manifest>('/api/manifest')

/** E3 — the requirement list. Filtering and search are the SERVER's, so the count line and the
 *  rows come from one answer and cannot disagree. */
export const getRequirements = (q: string, filter: string) =>
  get<Requirements>('/api/requirements', { q, filter })

/** E4 — one requirement, its tickets grouped by workspace. */
export const getRequirement = (id: string) =>
  get<Requirement>(`/api/requirements/${encodeURIComponent(id)}`)

/** E5 — one ticket. */
export const getTicket = (id: string) =>
  get<Ticket>(`/api/tickets/${encodeURIComponent(id)}`)

/** E6 — the delivery report, or the 404 that is 4.9's empty state. */
export const getReport = (id: string) =>
  get<Report>(`/api/tickets/${encodeURIComponent(id)}/report`)

/**
 * E30 — SPEC.md §4.18's three panels, at ONE instant.
 *
 * There is no second call for this surface and that is the whole design of it: three panels drawn
 * from three endpoints is three answers about one repository at three instants, and the counts in
 * the first panel and the rows in the second would be free to disagree while each stayed correct.
 */
export const getHome = () => get<Home>('/api/home')

/** E9 — the lane set, behind §4.4's rail and §4.1's counter alike. Read once, shared. */
/**
 * `E29` · `/api/tickets` — the index, grouped by the requirement each ticket names as parent.
 *
 * `provenance` and `stage` are the two filter rows the design draws above the table
 * (`factory.pen`, frame `DAiio`). They go to the server rather than being applied here: the
 * facet counts beside each control are the server's, and a client that filtered its own rows
 * would draw counts for one set and rows for another.
 */
export const getTickets = (provenance = 'all', stage = '', q = '', page = 1) =>
  get<Tickets>(`/api/tickets?provenance=${encodeURIComponent(provenance)}`
    + `&stage=${encodeURIComponent(stage)}&q=${encodeURIComponent(q)}&page=${page}`)

export const getLanes = () => get<Lanes>('/api/lanes')

/** E7 — a ticket's run history, newest first (§4.10). */
/** `E31` · `/api/runs` — every run this tool started, across every ticket. */
export const getAllRuns = () => get<RunIndex>('/api/runs')

/** `E28` · `/api/escapes` — the ledger of defects a gate passed. */
export const getEscapes = () => get<Escapes>('/api/escapes')

/** `E27` · `/api/wiki` — the repository's own pages, whole. */
export const getWiki = () => get<Wiki>('/api/wiki')

/**
 * `E32` · `/api/metrics` — the playbook's numbers over a window of N days (§4.21).
 *
 * The window is a parameter and not a filter this client applies: every number in the answer is
 * a reduction over that window, and a surface that asked for one window and reduced another
 * would draw a rate over one collection beside a count over a different one.
 */
export const getMetrics = (days: number) =>
  get<Metrics>('/api/metrics', { days: String(days) })

export const getRuns = (id: string) =>
  get<RunHistory>(`/api/tickets/${encodeURIComponent(id)}/runs`)

/**
 * E8 — one run's transcript, replayed.
 *
 * `limit` is the endpoint's own maximum. A finished run read back off the RUNS tab is read to
 * decide something about it, and 200 entries of a 900-entry run is a decision made on a quarter
 * of the evidence; `complete` says plainly when even 1000 was not all of it.
 */
export const getTranscript = (runId: string, afterSeq = 0, limit = 1000) =>
  get<Transcript>(`/api/runs/${encodeURIComponent(runId)}/transcript`,
                  { after_seq: String(afterSeq), limit: String(limit) })

/** E10 — what a lane changed, and with `path` that one file's patch. */
export const getDiff = (lane: string, path?: string) =>
  get<Diff>(`/api/lanes/${encodeURIComponent(lane)}/diff`, path ? { path } : undefined)
/** E11 — the standing policy and every rule in force. Read once; M12 keeps it true after that. */
export const getPermissionRules = () => get<PermissionRules>('/api/permissions/rules')

/**
 * The three writes, and the one thing their answers are read for.
 *
 * Every one of them also broadcasts `M12 rules` or `M7 permission.resolved` to every client, and
 * THAT is what changes the screen — so this returns `Fetched` for its error alone. `409
 * not_revocable`, `410 gone` and the `404` with `detail.state` of the ordinary two-tab race are
 * facts a person has to be told; the success body is a thing the socket already said better.
 */
async function send<T>(method: string, path: string, body?: unknown): Promise<Fetched<T>> {
  const response = await fetch(path, {
    method,
    ...(body === undefined ? {} : {
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),
  })
  let parsed: unknown = null
  try {
    parsed = await response.json()
  } catch {
    parsed = null
  }
  if (response.status >= 200 && response.status < 300) {
    return { ok: true, status: response.status, data: parsed as T }
  }
  return { ok: false, status: response.status, error: (parsed as ErrorBody | null)?.error ?? null }
}

/** E21 — set the standing policy. The manifest stays the default and is never rewritten. */
export const setPolicy = (policy: Policy) =>
  send<{ policy: string; applies_to: string }>('PUT', '/api/permissions/policy', { policy })

/** E20 — revoke one granted rule. A manifest rule answers `409 not_revocable`, and has no button. */
export const revokeRule = (ruleId: string) =>
  send<{ revoked: boolean; remaining: number }>(
    'DELETE', `/api/permissions/rules/${encodeURIComponent(ruleId)}`)

/** E23 — answer a pending request. `always` is the one that writes a rule a human then commits. */
export const decidePermission = (requestId: string, decision: Decision) =>
  send<{ decision: string; rule_id?: string; unblocked: boolean }>(
    'POST', '/api/permissions/decisions', { request_id: requestId, decision })

/**
 * E14 and E15 — SPEC.md §4.7's one control. `lane` is a path segment the server round-trips
 * verbatim (`WEB-FEAT-014` found encoding it 404s against the server this client ships with).
 */
const lanePath = (lane: string, tail: string) => `/api/lanes/${lane}/${tail}`

/** E14 — send one message. `held` when the process is mid-turn; the text is queued, never dropped. */
export interface Sent { status: 'delivered' | 'held'; held_count: number; entry_seq: number }
export const sendMessage = (lane: string, text: string) =>
  send<Sent>('POST', lanePath(lane, 'messages'), { text })

/** E15 — interrupt the turn, then deliver every held message in order. Nothing is reverted. */
export interface Interrupted { interrupted: boolean; delivered: number }
export const interruptRun = (lane: string) =>
  send<Interrupted>('POST', lanePath(lane, 'interrupt'), {})

/**
 * E12 and E13 — WEB-FEAT-044. The two writes that make a lane exist and stop existing.
 *
 * `claimLane` is the only one of the writes here whose SUCCESS body is read: E12's `201` is E9's
 * own lane object, so §4.4's rail draws the new lane off the response with no refetch and no frame
 * to wait for. Nothing else in the contract hands the client a whole row like that, and a claim
 * that needed a second request to become visible would look broken on exactly the slow link that
 * made it slow.
 *
 * `role` is a free string in E12 and defaults to `"run"` there too. It is passed explicitly rather
 * than left out: the roles a repository declares (F31) are the other thing that claims lanes, and
 * a body that says which one it meant is a body a log can be read against.
 */
export const claimLane = (ticketId: string, workspace: string, role = 'run') =>
  send<Lane>('POST', '/api/lanes', { ticket_id: ticketId, workspace, role })

/** E13's answer. `branch_kept` is always true — the tool never destroys a lane's commits. */
export interface Released {
  lane: string
  released: boolean
  worktree_removed: boolean
  branch_kept: boolean
}

/**
 * E13 — release a lane. `force` kills a live process first (E17) and the response says so.
 *
 * The path is built here rather than through `lanePath` because there is no tail: the lane name is
 * the last segment, and it is STILL not encoded, for the same reason `lanePath` does not encode it.
 */
export const releaseLane = (lane: string, force = false, discardIgnored = false) => {
  // Two questions, two parameters. `force` kills a live process; `discard_ignored` accepts losing
  // the paths git was told to ignore. SRV-FIX-059 round 5 keeps them apart on purpose — one flag
  // that meant both would let a single click answer a question a person was asked separately.
  const q = [force ? 'force=true' : '', discardIgnored ? 'discard_ignored=true' : '']
    .filter(Boolean).join('&')
  return send<Released>('DELETE', `/api/lanes/${lane}${q ? `?${q}` : ''}`)
}

/** E18's answer — WEB-FEAT-045. A pid, so the process exists by the time this is read. */
export interface Started { run_id: string; session_id: string; pid: number }

/**
 * E18 — start a run in a lane that has none.
 *
 * `resume: true` is the SPEC default and it is sent explicitly: continuing the lane's session is
 * the behaviour a person expects from a second run in the same lane, and a default that is not on
 * the wire is a default two implementations can disagree about. The prompt is OPTIONAL — with an
 * empty box the field is absent, and the agent starts on the ticket's own content instead.
 */
export interface AgentSelection { agent: string; model: string }

export const startRun = (lane: string, prompt?: string, selection?: AgentSelection) =>
  send<Started>('POST', lanePath(lane, 'runs'),
                { resume: true, ...(prompt ? { prompt } : {}), ...selection })

/**
 * E19 — run the workspace's declared `build` or `gate` command in the lane's worktree.
 *
 * `which` is a KEY, not a command: the string lives in `.factory/factory.json` and the server
 * resolves it from the lane's workspace. Sending the string would let a client run something the
 * repository never declared, which is the one thing E19 exists to prevent.
 *
 * `202` and then silence — the answer arrives as soon as the process exists and says nothing about
 * how it went. Output and the exit code come back on `M10`, so `command_id` is the only part of
 * this body a caller needs: it is what ties a click to the entry that grows in the transcript.
 */
export interface CommandStarted { command_id: string; command: string }
export const runCommand = (lane: string, which: 'build' | 'gate') =>
  send<CommandStarted>('POST', lanePath(lane, 'commands'), { which })

/**
 * The manifest key a refusal says would have to exist — E19's `404 not_declared` `detail.field`.
 *
 * `detailPath` above reads the same envelope for a filesystem path. Two readers rather than one
 * generic accessor because the two are read for different sentences, and a caller that reached
 * into `error.detail` itself is a caller that will do it again with a typo.
 */
export function detailField(result: Fetched<unknown>): string | undefined {
  if (result.ok) return undefined
  const detail = result.error?.detail as Record<string, unknown> | undefined
  return typeof detail?.field === 'string' ? detail.field : undefined
}

/**
 * WEB-FEAT-179 — the ONE place the manifest re-read's method and path exist.
 *
 * SPEC.md §9.2 freezes the verb (`reload_manifest`, `target {}`) and leaves the call it becomes to
 * the ticket that builds the endpoint. There is no `POST /api/manifest/reload` on the server yet —
 * `manifest::Store` loads once in `init()` — and `SRV-FEAT-190` is the child raised for it. Nothing
 * offers a person this button until the server sends the remedy that names it, so nobody meets a
 * control that 404s; what this constant does is stop the client from carrying two different
 * spellings of the call by the time it exists.
 *
 * The verb is `reload_manifest` and never `fix_manifest`, and that is the whole of the manifest
 * family's design: `.factory/factory.json` is the repository's document, committed and hand-written,
 * and this tool re-READS it. `tests/refusals.test.tsx` holds that to a source scan.
 */
export const MANIFEST_RELOAD_PATH = '/api/manifest/reload'

/**
 * SPEC.md §3.6 — the request a remedy becomes, or `null` when this client cannot make one.
 *
 * `null` is the whole version-skew rule in one value: a verb the client does not know is a verb it
 * renders no control for, so a server ahead of this bundle degrades to today's behaviour rather
 * than to a dead button. It is never inferred from the error's `code` — the body carries the
 * remedy or it does not.
 *
 * `open_lane` is not here: it is an ADDRESS, not a request, and `components/Remedy.tsx` resolves it
 * from the surface that knows which ticket the lane belongs to. `retry` is not here either — it is
 * "re-issue the request that just failed, unchanged", which only the caller holds.
 */
export function remedyRequest(remedy: Remedy): { method: string; path: string } | null {
  switch (remedy.action) {
    case 'reload_manifest':
      return { method: 'POST', path: MANIFEST_RELOAD_PATH }
    default:
      return null
  }
}

/** Make a remedy's call. Only for the verbs `remedyRequest` knows; `null` otherwise. */
export function performRemedy(remedy: Remedy): Promise<Fetched<unknown>> | null {
  const request = remedyRequest(remedy)
  if (!request) return null
  return send<unknown>(request.method, request.path)
}
