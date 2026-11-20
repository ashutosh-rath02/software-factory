/**
 * The vocabulary — one table, read by the sheet that documents it and by every component that
 * renders a state.
 *
 * SPEC.md §4.11: "it is deliberately the *same* constant table the rest of the UI renders from.
 * A vocabulary sheet maintained separately from the components it documents is a vocabulary
 * sheet that will lie."
 *
 * So `axisValue()` is the only way a component learns a state's label, note or colour, and a
 * state that is not here has nowhere to come from — it renders nothing, visibly, rather than
 * something wrong. `tests/vocabulary.test.tsx` asserts object identity, not deep equality.
 *
 * Values are SPEC.md §3.2's, in full. Labels, notes and tones are the design's own for the five
 * process values, the two motion values and the three evidence values its sheet draws
 * (Dashboard.dc.html). The three §3.2 adds — `queued`, `blocked`, `killed` — take their
 * notes from §3.2's own words and their tones from the design's palette; the reasoning is in
 * the delivery report and neither is new decoration.
 */

import type { Delivery, Home, Lane, LaneState, Resource, State } from './types/api'

export type { Delivery, Lane, LaneState, Resource, State }

export interface AxisValue {
  /** the wire value, as the server reports it */
  value: string
  /** what a person reads */
  label: string
  /** the one-line note on the vocabulary sheet */
  note: string
  /** a token name from the design's state palette */
  tone: string
  /**
   * The word this value takes when it is JOINED to another axis's on a lane card.
   *
   * `running · producing` and `finished · claimed done` are the design's own card phrases
   * (Dashboard.dc.html), and they are shorter than the sheet's `producing output` and
   * `claimed · no proof` because a 236 px card has one line for them. It is the same value said
   * in a narrower place, so it lives on the same row — a second table of card phrases is the
   * drift `SPEC.md` §4.11 exists to prevent.
   */
  short?: string
}

export const TONE = {
  run: 'var(--st-run)',
  silent: 'var(--st-silent)',
  crash: 'var(--st-crash)',
  wait: 'var(--st-wait)',
  claim: 'var(--st-claim)',
  done: 'var(--st-done)',
  none: 'var(--st-none)',
} as const

export const TINT = {
  run: 'var(--tint-run)',
  silent: 'var(--tint-silent)',
  crash: 'var(--tint-crash)',
  wait: 'var(--tint-wait)',
  claim: 'var(--tint-claim)',
  done: 'var(--tint-done)',
  none: 'transparent',
} as const

export const AXES = {
  process: [
    { value: 'not_started', label: 'not started', note: 'no lane yet', tone: TONE.none },
    { value: 'queued', label: 'queued', note: 'waiting for an exclusive resource', tone: TONE.none },
    { value: 'running', label: 'running', note: 'a turn is in flight', tone: TONE.run },
    { value: 'blocked', label: 'blocked', note: 'waiting on your answer', tone: TONE.wait },
    { value: 'idle', label: 'idle · waiting for you', note: 'asked, then stopped', tone: TONE.wait },
    { value: 'finished', label: 'finished', note: 'ended cleanly', tone: TONE.done },
    { value: 'crashed', label: 'crashed mid-turn', note: 'session died', tone: TONE.crash },
    { value: 'killed', label: 'killed by you', note: 'you ended it', tone: TONE.crash },
  ],
  motion: [
    { value: 'producing', label: 'producing output', note: 'tokens arriving', tone: TONE.run,
      short: 'producing' },
    { value: 'silent', label: 'silent', note: 'alive, going nowhere', tone: TONE.silent,
      short: 'silent' },
  ],
  evidence: [
    { value: 'nothing_claimed', label: 'nothing claimed', note: 'still working', tone: TONE.done },
    { value: 'claimed_unproven', label: 'claimed · no proof', note: 'looks like success', tone: TONE.claim,
      short: 'claimed done' },
    { value: 'claimed_proven', label: 'claimed · proof', note: 'report cites a run', tone: TONE.run,
      short: 'proof attached' },
  ],
} as const satisfies Record<string, readonly AxisValue[]>

/**
 * SPEC.md §3.5 — what git and the process table say became of the work.
 *
 * A fourth thing a row can say, and deliberately NOT a fourth axis: the three axes describe a
 * lane's process, and this describes a ticket's history, which exists whether a lane ever did.
 * It lives in this table for the same reason the axes do — the vocabulary sheet and every
 * component that renders a badge read the same constants, so the sheet cannot come to lie.
 *
 * `alarm` is the two that need a person. `done` does not get one: a border on the ordinary
 * outcome is a border nobody reads.
 */
export const DELIVERY = [
  { value: 'running', label: 'running', note: 'a live session is working on it', tone: TONE.run,
    alarm: false },
  { value: 'stalled', label: 'stalled', note: 'launched, gone, and left nothing',
    tone: TONE.crash, alarm: true },
  { value: 'unshipped', label: 'unshipped', note: 'nobody outside this machine can see it',
    tone: TONE.claim, alarm: true },
  { value: 'waiting', label: 'waiting', note: 'a pull request is open; a human merges',
    tone: TONE.wait, alarm: false },
  { value: 'done', label: 'done', note: 'it is on the base branch', tone: TONE.done,
    alarm: false },
] as const satisfies readonly (AxisValue & { alarm: boolean })[]

/**
 * The badge's own vocabulary, or nothing.
 *
 * `not_started` and `unknown` render no badge: the first has nothing to say, and the second is
 * said once in the top bar rather than repeated on every row. A word on thirty rows that all
 * read "unknown" is noise a person learns to skip, and the one place it matters — "this is not
 * a git repository, so I cannot tell you" — is a property of the whole screen.
 */
export function deliveryValue(state: string | undefined) {
  return DELIVERY.find((d) => d.value === state)
}

/**
 * SPEC.md §4.18's strip — one cell per §3.5 value, INCLUDING the two the badge has no word for.
 *
 * `DELIVERY` above is the badge's vocabulary and it is deliberately five: `not_started` has
 * nothing to report on a row and `unknown` is said once in the top bar rather than repeated
 * thirty times. A strip is the other case — it is a row of counts over the whole axis, and a cell
 * that vanished when it was empty would be a row whose shape changes under the reader (`E30`
 * serves all seven, zeros included, for exactly that reason). So the two are given their labels
 * here and nowhere else, and every other word still comes off the table above.
 */
const OFF_BADGE: Record<string, string> = { not_started: 'not started', unknown: 'unknown' }

export function deliveryCellLabel(state: string): string {
  return deliveryValue(state)?.label ?? OFF_BADGE[state] ?? state
}

/** The tone a strip cell takes, and `none` for a state the badge does not colour. */
export function deliveryCellTone(state: string): string {
  return deliveryValue(state)?.tone ?? TONE.none
}

/**
 * SPEC.md §3.3's seven clauses, each resolved to the axis value that already says it.
 *
 * `E30` sends the clause BY NAME — `needsYouRow`'s own reason is that a second vocabulary for
 * these facts is two places the states drift — and every one of the seven is a value one of the
 * tables above already carries: five are axis values, two are `delivery`'s. So this maps rather
 * than restates, and a clause the client does not know renders as itself rather than as nothing.
 */
const CLAUSE_AXIS: Record<string, AxisName | 'delivery'> = {
  idle: 'process', blocked: 'process', crashed: 'process',
  claimed_unproven: 'evidence', silent: 'motion',
  stalled: 'delivery', unshipped: 'delivery',
}

export function clauseValue(clause: string): AxisValue | undefined {
  const axis = CLAUSE_AXIS[clause]
  if (!axis) return undefined
  return axis === 'delivery' ? deliveryValue(clause) : axisValue(axis, clause)
}

/** The word a `needs you` row wears, and the clause name itself when the table does not know it. */
export function clauseLabel(clause: string): string {
  return clauseValue(clause)?.label ?? clause
}

export type AxisName = keyof typeof AXES

export function axisValue(axis: AxisName, value: string): AxisValue | undefined {
  return (AXES[axis] as readonly AxisValue[]).find((v) => v.value === value)
}

/**
 * One colour for a row's dot and its left edge.
 *
 * The axes are never flattened into a status — the vocabulary sheet says so and this is not that.
 * It is one mark on a scannable list, and it takes the most urgent axis that has something to
 * say: an unproven claim is an alarm (SPEC.md §3.2), a live process outranks a dormant one, and
 * a document with nothing behind it is the ghost colour.
 */
export function markTone(state: State | undefined, delivery?: Delivery): string {
  // The derived state outranks the document's, and an alarm outranks everything: a row whose
  // work is stalled must not take its colour from a `status:` field that was written before the
  // session died. Same precedence the badge shows, so the edge and the word cannot disagree.
  const derived = deliveryValue(delivery?.state)
  if (derived?.alarm) return derived.tone
  if (!state) return derived?.tone ?? TONE.none
  if (state.evidence === 'claimed_unproven') return TONE.claim
  const process = axisValue('process', state.process)
  if (process && state.process !== 'not_started') return process.tone
  if (state.motion === 'silent') return TONE.silent
  const evidence = axisValue('evidence', state.evidence)
  if (evidence && state.evidence !== 'nothing_claimed') return evidence.tone
  return derived?.tone ?? TONE.none
}

/**
 * The phrase a ticket row shows in the state column (Dashboard.dc.html).
 *
 * Same precedence as `markTone`, so the word and the colour can never disagree, and every word
 * comes off the table above rather than out of a document's frontmatter: `todo` is a repository's
 * own vocabulary and the tool has no opinion about it, while `claimed · no proof` is the tool's
 * and means exactly one thing.
 */
export function stateLabel(state: State | undefined): string {
  if (!state) return ''
  if (state.evidence !== 'nothing_claimed') {
    return axisValue('evidence', state.evidence)?.label ?? ''
  }
  if (state.motion === 'silent') return axisValue('motion', 'silent')?.label ?? ''
  return axisValue('process', state.process)?.label ?? ''
}

/**
 * The filter chips of 4.2, in the design's order (Dashboard.dc.html).
 *
 * `done` is the fifth and is not the design's — it is E3's `filter=done`, which keeps the rows
 * whose §3.5 delivery state says the work is on the base branch. It goes last because the four
 * before it are about work in flight and this one is about work that is over, and because the
 * order of this table is the order on screen.
 */
export const FILTERS = [
  { key: 'all', label: 'all' },
  { key: 'needs', label: 'needs you' },
  { key: 'running', label: 'running' },
  { key: 'blocked', label: 'blocked' },
  { key: 'done', label: 'done' },
] as const

export type FilterKey = (typeof FILTERS)[number]['key']

/**
 * The id E3 gives the one row no document backs: the tickets whose `parent` names no requirement
 * in the repository (WEB-FEAT-041). The server decides whether it exists and puts it last; this
 * is the client's single copy of the name, so no component grows a second one.
 */
export const STANDALONE = 'standalone'

/** `252` is the design's own sample (Dashboard.dc.html) — 4m 12s, as its sheet reads. */
export const SAMPLE_SILENCE_S = 252

export function formatSilence(seconds: number): string {
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`
}

/** `12m 04s`, and `1h 02m 03s` once a lane has been open long enough to need the hour. */
export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const rest = String(whole % 60).padStart(2, '0')
  return hours > 0
    ? `${hours}h ${String(minutes).padStart(2, '0')}m ${rest}s`
    : `${minutes}m ${rest}s`
}

/**
 * `~12m left`, `<1m left`, `~1h 05m left` — E9's `remaining_s` (SRV-FIX-599), counted down.
 *
 * Minutes rounded DOWN and marked `~`: the number is a median of measured stages, so a second hand
 * would claim a precision it does not have. Under a minute it says so rather than `~0m`.
 */
export function formatLeft(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  if (whole < 60) return '<1m left'
  const minutes = Math.floor(whole / 60)
  return minutes >= 60
    ? `~${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m left`
    : `~${minutes}m left`
}

/** `$1.86` — two places, always, because a column of costs is read by its shape. */
export function formatCost(usd: number): string {
  return `$${(Number.isFinite(usd) ? usd : 0).toFixed(2)}`
}

/**
 * The state LINE on a lane card — the design's own six phrases (Dashboard.dc.html).
 *
 * Composed from the table above and from nothing else, so the six the sheet draws and the six the
 * rail renders cannot come apart. It is not a flattening of the axes: the three chips in §4.5 are
 * where the axes are read independently, and this is one line on a 236 px card that has room for
 * the two facts that matter about the lane it is on.
 */
/**
 * WEB-FIX-349 — what a lane that has STOPPED is actually waiting for.
 *
 * `process: idle` is "the agent's process is alive and its turn is over", so four situations share
 * it and the axis label could only ever name one of them. `SRV-FEAT-415` made the server say which,
 * and `holds_work` is the reason the old sentence got most wrong: the lane is not waiting on an
 * answer, it is holding commits nobody else can see, and pushing them is a different act.
 *
 * Reasons the server does not send a sentence for fall through to the process label, which is
 * already right for them — `crashed` and `never_started` say themselves.
 */
const STOPPED_LINE: Record<string, string> = {
  holds_work: 'idle · holds work not pushed',
  work_landed: 'idle · work landed',
  ended: 'idle · round ended',
}

export function laneStateLine(state: LaneState | undefined): string {
  const process = state && axisValue('process', state.process)
  if (!process) return ''
  const stopped = (state as { stopped?: { reason?: string } | null }).stopped
  const said = stopped?.reason ? STOPPED_LINE[stopped.reason] : undefined
  if (said) return said
  if (state.process === 'running') {
    const motion = axisValue('motion', state.motion?.value ?? '')
    if (motion) return `${process.label} · ${motion.short ?? motion.label}`
  }
  if (state.process === 'finished') {
    const evidence = axisValue('evidence', state.evidence)
    if (evidence?.short) return `${process.label} · ${evidence.short}`
  }
  return process.label
}

/**
 * The one colour a lane card takes — its dot, its state line, its bar and, when it is an alarm,
 * its border and fill.
 *
 * Same two alarms §3.2 names and in the same order: `running + silent` and
 * `finished + claimed_unproven`. Everything else is the process's own colour, so the card and the
 * `PROCESS` chip beside it cannot disagree about what is happening.
 */
export function laneTone(state: LaneState | undefined): string {
  if (!state) return TONE.none
  if (state.process === 'running' && state.motion?.value === 'silent') return TONE.silent
  if (state.process === 'finished' && state.evidence === 'claimed_unproven') return TONE.claim
  return axisValue('process', state.process)?.tone ?? TONE.none
}

/**
 * The card's optional sub-line, when the tool knows something a person would want next.
 *
 * Every branch is a fact off E9 rather than a phrase invented for the screen: why it is queued,
 * what it is waiting to be asked, what vanished from disk. A lane with nothing to add gets no
 * line, and the card is shorter — an empty sub-line on every card is a row of nothing to read.
 * The silence counter is NOT here: it is a clock, so the rail renders it as one.
 *
 * `describe` is WEB-FEAT-044's: `waiting_for` carries the resource's NAME and E9 carries its
 * `description` in a list beside the lanes, so the one sentence that says what a person is waiting
 * for has to be assembled from both. It is a lookup passed in rather than a second table imported
 * here, because this module is the vocabulary and a resource is a fact about a repository.
 */
export function laneSubLine(lane: Lane,
                            describe?: (resource: string) => string | null): string {
  const detail = lane.detail as { reason?: string } | null | undefined
  if (detail?.reason) return detail.reason.replace(/_/g, ' ')

  const blocked = lane.state?.blocked as { reason?: string } | null | undefined
  if (blocked?.reason) return `waiting on your answer · ${blocked.reason}`

  const waiting = lane.waiting_for as
    { resource?: string; held_by?: string | null; position?: number } | null | undefined
  if (waiting?.resource) {
    const what = describe?.(waiting.resource)
    const says = what ? ` · ${what}` : ''
    const held = waiting.held_by ? ` · held by ${waiting.held_by}` : ''
    const place = waiting.position ? ` · #${waiting.position}` : ''
    return `queued for ${waiting.resource}${says}${held}${place}`
  }

  if (lane.state?.process === 'finished' && lane.state.evidence === 'claimed_unproven') {
    return 'no proof attached'
  }
  return ''
}

/**
 * The other half of `laneSubLine`'s `waiting_for` clause — WEB-FIX-277.
 *
 * `waiting_for` tells the WAITER everything: the resource, what it is, who holds it, the place in
 * the queue. The holder was told nothing, and the holder is the only lane whose release ends the
 * wait — the release button is on its card and on no other. A state whose action lives on somebody
 * else's card is WEB-FEAT-173's rule broken wearing a politer face.
 *
 * It is a fact off E9 and not a second inference: `resources[].queue` is the server's own list of
 * lanes waiting on each resource, and `held_by` is the server's own answer about who holds it. The
 * waiters are LISTED rather than counted, because the name is what a person goes and looks at and
 * a queue is bounded by the seats a host declared.
 *
 * A resource nobody is waiting for produces nothing. A lane holds the board for its whole run,
 * alone, on the ordinary run, and a permanent badge for that is noise that trains the reader to
 * ignore the one time it means something.
 */
export function laneBlockingLine(lane: Lane, resources: Resource[]): string {
  return resources
    .filter((row) => row.held_by === lane.lane && row.queue.length > 0)
    .map((row) => `${row.queue.join(', ')} waiting on ${row.name}`)
    .join(' · ')
}

/**
 * §3.2's three process values that mean A PROCESS IS ALIVE — WEB-FEAT-045.
 *
 * `app/lane/state.cpp:50-62` reports exactly these three when the lane holds a running pid
 * (`blocked` when it is waiting on a permission answer, `running` mid-turn, `idle` between turns).
 * Every other value — `not_started`, `queued`, `finished`, `crashed`, `killed` — means there is
 * nothing on the other end of a message, and the composer's first Send has to START one (E18)
 * instead of posting into a session that is not there (E14, `409 wrong_state`).
 *
 * It is the PROCESS axis and deliberately not `run_id`: a lane whose run crashed an hour ago still
 * carries the run's id, and a client that read that would send E14 and call the refusal a bug.
 */
const ALIVE = ['running', 'idle', 'blocked']

export function hasLiveSession(lane: Lane | undefined): boolean {
  return ALIVE.includes(lane?.state?.process ?? '')
}

/**
 * A lane waiting for an exclusive resource. `queued` is the axis and `waiting_for` is the reason,
 * and either one being present is enough: a lane that says one without the other is a lane this
 * client must still not try to start a run in (E18 answers `409 resource_busy`).
 */
export function isQueued(lane: Lane | undefined): boolean {
  return lane?.state?.process === 'queued' || Boolean(lane?.waiting_for)
}

/**
 * The six lane-card states the design's sheet draws (Dashboard.dc.html), as sample data
 * for 4.11.
 *
 * They carry a lane's STATE rather than a rendered phrase, and the sheet reads it through
 * `laneStateLine` and `laneTone` — the same two functions the rail calls. That is §4.11's rule
 * with teeth: change a word here and it changes in both places, or in neither.
 */
export const SAMPLE_LANES = [
  { name: 'lane/01', ticket: 'TKT-142', elapsed: '12m 04s', cost: '$0.74', alarm: true,
    sub: 'no output 4m 12s', stage: 'BUILD', pct: 50,
    state: { process: 'running', motion: { value: 'silent', silent_for_s: SAMPLE_SILENCE_S, since: null },
             evidence: 'nothing_claimed', stopped: null } },
  { name: 'lane/02', ticket: 'TKT-147', elapsed: '4m 12s', cost: '$0.22', alarm: false,
    sub: '', stage: 'GATE', pct: 75,
    state: { process: 'running', motion: { value: 'producing', silent_for_s: 0, since: null },
             evidence: 'nothing_claimed', stopped: null } },
  { name: 'lane/03', ticket: 'TKT-151', elapsed: '31m 08s', cost: '$1.04', alarm: true,
    sub: 'asked a question 2m ago', stage: 'TEST', pct: 25,
    state: { process: 'idle', motion: { value: null, silent_for_s: 0, since: null },
             evidence: 'nothing_claimed', stopped: null } },
  { name: 'lane/04', ticket: 'TKT-139', elapsed: '22m 47s', cost: '$1.91', alarm: true,
    sub: 'no proof attached', stage: 'DELIVER', pct: 100,
    state: { process: 'finished', motion: { value: null, silent_for_s: 0, since: null },
             evidence: 'claimed_unproven', stopped: null } },
  { name: 'lane/05', ticket: 'TKT-133', elapsed: '8m 55s', cost: '$0.48', alarm: true,
    sub: 'socket closed at 13:41', stage: 'IMPL', pct: 38,
    state: { process: 'crashed', motion: { value: null, silent_for_s: 0, since: null },
             evidence: 'nothing_claimed', stopped: null } },
  { name: 'lane/06', ticket: 'TKT-128', elapsed: '17m 22s', cost: '$0.96', alarm: false,
    sub: '', stage: 'DELIVER', pct: 100,
    state: { process: 'finished', motion: { value: null, silent_for_s: 0, since: null },
             evidence: 'claimed_proven', stopped: null } },
] as const satisfies readonly {
  name: string; ticket: string; elapsed: string; cost: string; alarm: boolean
  sub: string; stage: string; pct: number; state: LaneState
}[]

/**
 * §4.6's Empty is a fact about RUNS. `SPEC.md`: *"a lane with no **run** yet"*.
 *
 * Drawn on the absence of a LANE, it says something false about the ordinary end of every finished
 * piece of work — a released lane leaves the runs on disk and no lane at all, and the screen then
 * reads `2 runs · RUNS · 2 · CRASHED MID-TURN` over *"Nothing has run on this ticket yet."*
 * (`WEB-FIX-088`, from `WEB-FEAT-085`'s audit).
 *
 * The heading stays: no lane IS assigned, and that is what the block is for. Only the sentence
 * under it changes, and only when there is something to say instead.
 */
export function nothingRanYet(count: number | undefined): string {
  if (!count) return 'Nothing has run on this ticket yet.'
  return `${count} run${count === 1 ? '' : 's'} recorded — read ${count === 1 ? 'it' : 'them'} `
       + 'on the RUNS tab.'
}

/**
 * §4.18's Empty is a fact about TICKETS, and the noun is the whole of `WEB-FIX-002`.
 *
 * `E30` builds every member of its answer — `delivery`, `needs_you`, `unpushed` — from the ticket
 * set, and reads no requirement anywhere. `WEB-FEAT-001` drew *"No requirements in this
 * repository."* out of it, so every repository between `/split-requirement` and the first
 * `/new-ticket` was told something `GET /api/requirements` contradicted in the same breath.
 *
 * The noun is here, alone, because the two halves of that defect live in two languages: the answer
 * is decided in `render.cpp` and the sentence is drawn in a browser.
 * `server/tests/test_home.py::test_a_repository_with_epics_and_no_tickets_is_not_told_it_has_no_requirements`
 * reads this constant and checks it against the collection that actually moves `E30`'s counts.
 */
export const HOME_COLLECTION = 'tickets'

/** The collection nouns a sentence on this surface could be read as being about. */
const COLLECTIONS = ['tickets', 'requirements', 'epics', 'reports', 'runs', 'lanes'] as const

/** Is this answer empty? — §4.18's condition, in one place, so the copy cannot outlive it. */
export function homeIsEmpty(home: Home): boolean {
  return home.delivery.every((cell) => cell.count === 0)
    && home.needs_you.rows.length === 0
    && home.unpushed.rows.length === 0
}

/** The empty block's sentence — `No tickets in this repository.` */
export function homeEmptyTitle(): string {
  return `No ${HOME_COLLECTION} in this repository.`
}

/**
 * Everything wrong with drawing `text` as §4.18's empty block for `home` — `[]` when nothing is.
 *
 * This is the RULE, and it is one function because the live case and the mandatory negative both
 * call it: a negative that cannot call the shipped rule stays green when the rule is deleted.
 *
 * It answers the question `_TEMPLATE.md` §2 was missing. *Assert the copy against the collection*
 * was already the rule and `WEB-FEAT-001` obeyed it — its condition and its copy sat in one
 * expression, and the condition was over a collection the copy did not name. So both halves agreed
 * with each other, neither was the collection the sentence was about, and the case shared the
 * mistake with the code.
 */
export function emptyBlockFaults(text: string, home: Home): string[] {
  const named = COLLECTIONS.filter((c) => new RegExp(`\\b${c}\\b`, 'i').test(text))
  const faults: string[] = []
  if (!homeIsEmpty(home)) {
    faults.push('drawn on an answer that is not empty')
  }
  for (const c of named) {
    if (c !== HOME_COLLECTION) faults.push(`names ${c}, which E30 does not count`)
  }
  if (!named.includes(HOME_COLLECTION)) {
    faults.push(`names no collection E30 counts — it counts ${HOME_COLLECTION}`)
  }
  return faults
}

/**
 * Has the run changed? — SPEC.md §4.6, and the only place this question is answered.
 *
 * It is here, in one function, because it was answered in two and they disagreed about what "no
 * run" is. `services/socket.ts` held one copy and `services/transcript.ts` another; three QA
 * rounds of `WEB-FIX-073` found three different values of *nothing* that one of them got wrong —
 * `undefined` on a transcript frame, `""` on the permission path's frame, and `null` held by the
 * module after a snapshot whose lane has no run. Each round fixed the value in front of it, in one
 * of the two copies.
 *
 * **A run id that is absent, null or empty names NO run, and no run is not a change.** A change is
 * two runs that are both named and different. `SequenceLog::beginRun` says the same thing on the
 * server, which is where an empty run id gets its meaning.
 */
export function isRunChange(incoming: string | null | undefined,
                            held: string | null | undefined): boolean {
  return Boolean(incoming) && Boolean(held) && incoming !== held
}

/**
 * SPEC.md §9.3 — what a remedy's `destroys` may name, and what each noun costs a person.
 *
 * WEB-FEAT-179. `destroys` is rendered on a control and compared in a test, so it is a closed
 * vocabulary and not free prose — and it is here, with the axes, because a remedy IS vocabulary
 * (WEB-FEAT-176 §1). The client owns these sentences and never the remedy's `label`, which is the
 * server's words rendered verbatim.
 *
 * A noun this table does not know renders as itself rather than as nothing: a control that says
 * `this loses telemetry` is worse English than the table would have written and it is still a
 * warning, where a silent drop is a control that destroys something nobody was told about.
 */
export const DESTROYS: Record<string, string> = {
  turn: 'the turn in flight, and whatever it produced that was never written down',
  session: 'the agent’s resumable conversation, and the memory of it on disk',
  worktree: 'the lane’s checkout, and anything committed only on its branch',
  ignored_files: 'files git was told to ignore inside the checkout — a gate capture, a database, keys',
}

/** The sentence a control wears BEFORE it fires. Empty when it costs nothing, and then nothing is said. */
export function destroysLine(destroys: readonly string[] | undefined): string {
  const said = (destroys ?? []).map((noun) => DESTROYS[noun] ?? noun)
  if (said.length === 0) return ''
  return `This cannot be got back: ${said.join('; ')}.`
}

/**
 * The tone a remedy takes from what it costs — no new colour, and no third state.
 *
 * `crash` is the palette's own word for something ending badly and it is what a destructive
 * control must wear; `claim` is the tone of a control that takes work on rather than away.
 */
export function remedyTone(destroys: readonly string[] | undefined): string {
  return (destroys ?? []).length > 0 ? TONE.crash : TONE.claim
}

/**
 * SPEC.md §9.2 — what `discard_ignored` costs, in the worst case.
 *
 * The client's own discard control (SRV-FIX-059) is not a remedy off the wire: E13's refusal names
 * the call in `detail.clear_with` and the client renders the button for it. It is still destructive,
 * so it says what it costs in the same words a remedy would, out of the same table.
 */
export const DISCARD_IGNORED_DESTROYS: readonly string[] = ['session', 'worktree', 'ignored_files']
