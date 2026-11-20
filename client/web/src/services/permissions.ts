import type { PermissionRule } from '../types/api'
import type { PermissionRequestFrame, TranscriptEntry } from '../types/socket'

/**
 * SPEC.md §4.12's vocabulary and its one shape the socket generator cannot reach.
 *
 * Two things live here and nothing else does: the three policy values with the sentence each one
 * owes a reader, and the reader that turns a `permission_request` — an M6 frame, or the transcript
 * entry the same payload becomes — into the props the block renders.
 */

export type Policy = 'ask_all' | 'edit_free' | 'never_ask'

/**
 * The three values, in SPEC.md §4.12's own order, each with ONE line of what it means.
 *
 * The line is not decoration. `edit_free` does not say which tools are free, `never_ask` does not
 * say what it stops asking about, and a policy control whose options a person cannot read is how
 * `never ask` gets chosen by somebody who meant *"stop interrupting me about files"*. The wording
 * of `edit_free` is the server's own list (`SRV-FEAT-017` §1.6): the file tools are free and
 * everything else asks.
 */
export const POLICIES: ReadonlyArray<{ value: Policy; label: string; consequence: string }> = [
  {
    value: 'ask_all',
    label: 'ask about everything',
    consequence: 'Every tool call a rule does not already cover stops and asks you.',
  },
  {
    value: 'edit_free',
    label: 'edit files freely, ask before commands',
    consequence: 'Reading and writing files runs without asking. Every command still stops and asks.',
  },
  {
    value: 'never_ask',
    label: 'never ask',
    consequence: 'Nothing stops. Every tool call runs, including commands you have never seen.',
  },
]

export function policyLabel(value: string): string {
  return POLICIES.find((p) => p.value === value)?.label ?? value
}

/**
 * E21 says `applies_to: "new_runs"` and means it — a run captures the policy in force when it
 * started, while rules are evaluated live. §4.12 requires the control to say so out loud, and this
 * is the sentence it says.
 */
export const POLICY_APPLIES = 'A change here applies to lanes started afterwards. '
  + 'A run already in flight keeps the policy it started with.'

/**
 * The standing rule that came closest, and why it did not match (`socket.schema.json`,
 * `permission.request.nearest_miss`).
 *
 * A rule that nearly matched and a rule that is missing are different problems with different
 * fixes. This has already cost a run: an agent waited on an approval for a command a rule was
 * supposed to cover and did not, and nothing on the screen could say which rule or where it
 * diverged.
 */
export interface NearestMiss {
  rule_id: string
  pattern: string
  source: string
  differs_on: string
  matched_prefix: string
  why_it_did_not_match: string
}

/**
 * What the block renders — M6's payload, narrowed.
 *
 * `src/types/socket.ts` is GENERATED and types `nearest_miss` as `Record<string, unknown>` because
 * the socket generator does not descend into a nested object. Tightening that means changing a
 * shared generator and regenerating the whole of `socket.ts`, which is the file the transcript
 * ticket is also carrying — so the shape is narrowed here, at this client's own boundary, and
 * nothing is hand-edited in a generated file.
 */
export interface PermissionAsk {
  request_id: string
  lane: string
  tool: string
  input_summary: string
  /** exactly what `always` writes — literal, escaped, never widened (SRV-FEAT-017 §1.4) */
  rule_that_would_be_written: string
  nearest_miss?: NearestMiss
}

export type Decision = 'once' | 'always' | 'deny'

const str = (value: unknown): string => (typeof value === 'string' ? value : '')

function missFrom(value: unknown): NearestMiss | undefined {
  if (!value || typeof value !== 'object') return undefined
  const miss = value as Record<string, unknown>
  if (!str(miss.pattern) || !str(miss.why_it_did_not_match)) return undefined
  return {
    rule_id: str(miss.rule_id), pattern: str(miss.pattern), source: str(miss.source),
    differs_on: str(miss.differs_on), matched_prefix: str(miss.matched_prefix),
    why_it_did_not_match: str(miss.why_it_did_not_match),
  }
}

/**
 * A transcript entry, or nothing — the seam the SESSION tab meets this component at.
 *
 * The transcript is `WEB-FEAT-014`'s and it renders entries by kind; this is the one function it
 * needs to render this kind, so the entry is read HERE rather than in the transcript's own switch.
 * An entry of another kind, or one missing the two fields the block exists to show, returns null
 * and renders nothing — a request that cannot say what `always` would write is not one a person
 * should be answering.
 */
export function askFromEntry(entry: TranscriptEntry): PermissionAsk | null {
  if (entry.kind !== 'permission_request') return null
  const request_id = str(entry.request_id)
  const rule = str(entry.rule_that_would_be_written)
  if (!request_id || !rule) return null
  return {
    request_id,
    lane: str(entry.lane),
    tool: str(entry.tool),
    input_summary: str(entry.input_summary),
    rule_that_would_be_written: rule,
    nearest_miss: missFrom(entry.nearest_miss),
  }
}

/** The same payload as it arrives live (M6), so the two paths cannot render differently. */
export function askFromFrame(frame: PermissionRequestFrame): PermissionAsk {
  return {
    request_id: frame.request_id,
    lane: frame.lane,
    tool: frame.tool,
    input_summary: frame.input_summary,
    rule_that_would_be_written: frame.rule_that_would_be_written,
    nearest_miss: missFrom(frame.nearest_miss),
  }
}

/** M12 broadcasts the six fields `PermissionRule` declares; this is that cast, in one place. */
export function rulesFrom(value: unknown[]): PermissionRule[] {
  return value as PermissionRule[]
}

/** `3 matches`, `1 match`, and the honest answer for a rule that has never answered anything. */
export function matchCount(n: number): string {
  if (n <= 0) return 'no matches yet'
  return `${n} ${n === 1 ? 'match' : 'matches'}`
}

/** `2026-07-27 09:41`, or the dash a manifest rule earns by never having been granted. */
export function grantedAt(iso: string | null): string {
  if (!iso) return '—'
  const [date, time] = iso.split('T')
  return time ? `${date} ${time.slice(0, 5)}` : date
}
