// GENERATED from factory/docs/technical/contracts/socket.schema.json — do not edit by hand.
// Regenerate with `npm run gen:types`; tests/socket.test.ts fails if this drifts.
// contract: factory-v0.1.0

/**
 * A named region of state a surface reads. Exactly one key: the scope is the key, and the key
 * is what a delta is routed by.
 */
export type Scope =
  | { requirements: true }
  | { ticket: string }
  | { lane: string }

/**
 * One transcript entry, in E8's shape: `{seq,ts,kind,…payload}`. Its kinds belong to the
 * module that parses the agent's output; the socket carries it and forms no opinion about it.
 * What the socket DOES have an opinion about is that an entry is not a frame — `m` and a
 * nested `entry` are refused by name, because until SRV-FIX-049 a lane snapshot's `entries`
 * were whole transcript frames and every one of them validated: `data` said only `object`, so
 * the one place the two shapes could have been compared said nothing about either.
 */
export interface TranscriptEntry {
  seq: number
  ts: string
  kind: string
  [k: string]: unknown
}

/**
 * `subscribe` · client → server
 *
 * A surface mounts. The server replies with exactly one snapshot for that scope and then sends
 * that scope's deltas, and no other scope's.
 */
export interface SubscribeFrame {
  m: 'subscribe'
  ts?: string
  seq?: number | null
  scope: Scope
}

/**
 * `resume` · client → server
 *
 * The socket reconnected and the client already holds part of a lane's transcript. The server
 * replays from after_seq; when the log cannot cover the gap it sends a snapshot with
 * replaces_tail instead, and the client discards its tail rather than merging into a hole.
 */
export interface ResumeFrame {
  m: 'resume'
  ts?: string
  seq?: number | null
  lane: string
  after_seq: number
}

/**
 * `ping` · client → server
 *
 * Every 10 s of client idleness, so a dead connection is noticed in about 15 s rather than at
 * the next user action. The only periodic thing in the system.
 */
export interface PingFrame {
  m: 'ping'
  ts?: string
  seq?: number | null
}

/**
 * `hello` · server → client
 *
 * Sent once per connection the moment it opens — per connection, not once at boot, because a
 * server that greets only its first client looks identical to a working one until the second
 * tab opens.
 */
export interface HelloFrame {
  m: 'hello'
  ts: string
  seq: null
  factory: string
}

/**
 * `snapshot` · server → client
 *
 * The full state for one scope, and the only frame that replaces state; every other frame
 * patches. Sent after subscribe, and after a resume the log cannot cover.
 */
export interface SnapshotFrame {
  m: 'snapshot'
  ts: string
  seq: number | null
  scope: Scope
  data: Record<string, unknown>
  replaces_tail?: boolean
}

/**
 * `catalog.changed` · server → client
 *
 * A watched document changed on disk. Routed to the scopes it affects and to no others; the
 * client refetches exactly that document.
 */
export interface CatalogChangedFrame {
  m: 'catalog.changed'
  ts: string
  seq: null
  kind: 'epic' | 'ticket' | 'report'
  id: string
  path: string
  change: 'created' | 'modified' | 'deleted'
}

/**
 * `resource` · server → client
 *
 * M8 — an exclusive resource was claimed, released, or its queue changed. This is how a
 * `queued` lane learns it is next. `held_by` is null when nobody holds it, and `queue` is the
 * FIFO order, visible on purpose: a queue nobody can see is a queue nobody trusts.
 */
export interface ResourceFrame {
  m: 'resource'
  ts: string
  seq: null
  name: string
  exclusive: boolean
  description?: string | null
  held_by: string | null
  queue: unknown[]
}

/**
 * `stage` · server → client
 *
 * M3 — a run reported the stage it reached (E22), or a lane's ticket document changed on disk
 * — its declared sequence or its `status:` (SRV-FIX-599), so a ticket set done fills its bars
 * with no reload. Sent once per lane the ticket holds. STAGE NAMES ARE STRINGS: there is no
 * built-in sequence, no meaning attached to `GATE` or `QA`, and no cap on a round — a retry
 * limit belongs to the repository's process and not to this tool (SPEC.md §10). The tool
 * stores what was reported and never infers a stage from what the agent did. This frame is
 * also why no `progress.sh` has to be copied into a target repository: the URL and a token
 * arrive in the run's environment, so reporting a stage is one HTTP request and the tool works
 * out which run made it.
 */
export interface StageFrame {
  m: 'stage'
  ts: string
  seq: null
  lane: string | null
  ticket_id: string
  stage: Record<string, unknown>
  progress_pct: number
  remaining_s: number | null
  regressed_from?: string
}

/**
 * `lane.state` · server → client
 *
 * M2 — a state a surface renders has changed. One frame per change, never a periodic tick: the
 * server compares what it last sent for this row and sends nothing when nothing moved, which
 * is why a lane falling silent is announced exactly once and a quiet lane produces no traffic
 * at all. Two shapes, one message, because there are two different nouns. A LANE frame carries
 * the three axes of SPEC.md §3.2. A TICKET frame carries §3.5's `delivery` with `lane: null`,
 * because a ticket nobody has opened a lane for still has a delivery state and `ticket_id`
 * names the row the client patches. At least one of the two is always present. This frame is
 * what makes derived state obey the client's no-polling rule: the tool looks at the world on a
 * worker and tells the browser when the answer moved.
 */
export interface LaneStateFrame {
  m: 'lane.state'
  ts: string
  seq: null
  lane: string | null
  ticket_id: string
  state?: Record<string, unknown>
  waiting_for?: Record<string, unknown> | null
  delivery?: Record<string, unknown>
  needs_you: boolean
}

/**
 * `lane.metrics` · server → client
 *
 * M4 — a turn ended, or the agent reported what it has spent mid-turn. NEVER on a timer:
 * `elapsed_s` is sent so a card can start its clock and the client ticks it from there, which
 * is what lets a quiet lane produce no traffic at all (SPEC.md §4.4). `turn.cost_usd` is the
 * turn's OWN cost, the delta — the CLI reports a session total, and summing totals overstates
 * a run by more with every turn.
 */
export interface LaneMetricsFrame {
  m: 'lane.metrics'
  ts: string
  seq: null
  lane: string
  elapsed_s: number
  cost_usd: number
  remaining_s: number | null
  turn: Record<string, unknown>
}

/**
 * `lane.membership` · server → client
 *
 * M17 — the lane SET changed: E12 claimed one, E13 released one, or the tool claimed one for
 * itself (F31). Every other lane frame patches a row that already exists; this is the only one
 * that says a row appeared or went away, which is why it is a message of its own and not a
 * flag on `lane.state` — a released lane has no state to render. Routed to the lane, its
 * ticket and the requirements surface, so a lane the tool starts by itself reaches a screen
 * that has never heard of it. The client re-reads E9 on `claimed` and drops the row on
 * `released`.
 */
export interface LaneMembershipFrame {
  m: 'lane.membership'
  ts: string
  seq: null
  lane: string
  ticket_id: string
  workspace: string
  change: 'claimed' | 'released'
}

/**
 * `lane.words` · server → client
 *
 * M18 — the agent in a lane said something or called something, and this is that one line. Not
 * a flag on `lane.state`: `lane.state` fires when an AXIS of a lane's state changes, and last
 * words change on every entry, so a frame carrying `state`, `delivery` and `waiting_for` on
 * every sentence the agent writes is the wrong shape for the rate. It NEVER carries `null` — a
 * frame is sent only when there are words, and the `null` a new run starts with reaches the
 * client through the E9 re-read `lane.membership` already triggers and through `lane.state`'s
 * process transition to `running`. Coalesced per lane to at most one frame per 2 s with the
 * trailing value winning: an agent emits several entries a second at peak and a person reads
 * the line once every few seconds. That is a debounce armed by an entry, never a periodic tick
 * — nothing fires because time passed with nothing new. Routed to the same scopes `lane.state`
 * reaches, because the lanes surface subscribes to `requirements` (SPEC.md §4.14) and must
 * hear it.
 */
export interface LaneWordsFrame {
  m: 'lane.words'
  ts: string
  seq: null
  lane: string
  ticket_id: string
  last_words: Record<string, unknown>
}

/**
 * `isolation` · server → client
 *
 * M9 — the lane's confinement refused an access, naming the path it tried. Enforcement is a
 * mount namespace the tool builds for every process a lane starts, so the refusal happens in
 * the kernel and inside the child; this frame is how it stops being silent, because a boundary
 * that refuses without reporting is half of what went wrong the first time. `refused` is
 * always true: the boundary reports what it stopped, never what it allowed. Best-effort — a
 * program that swallows its own error message produces a refusal nobody sees, and the access
 * still fails.
 */
export interface IsolationFrame {
  m: 'isolation'
  ts: string
  seq: null
  lane: string
  operation: 'write' | 'read' | 'access'
  path: string
  refused: true
}

/**
 * `command` · server → client
 *
 * M10 — output from a command the workspace itself declared (E19), as it arrives, and once
 * more when the process exits. `chunk` is raw output, not lines: a build that prints a
 * progress bar with no newline for two minutes would otherwise look dead. Exactly one of
 * `chunk` and `exit_code` is present — a reader that has seen `exit_code` is done. The tool
 * has no opinion about what the output means or about the exit code: the number is a fact
 * reported, and whether a gate passing matters is the repository's business.
 */
export interface CommandFrame {
  m: 'command'
  ts: string
  seq: null
  lane: string
  command_id: string
  which: 'build' | 'gate' | 'review'
  chunk?: string
  exit_code?: number
}

/**
 * `run` · server → client
 *
 * M11 — a run started, and again when it reached a terminal outcome. E16 answers 202, so the
 * outcome of an `end` has to arrive somewhere and this is where. The terminal words are the
 * tool's own record of which verb it was ASKED for, not an inference from an exit status:
 * `crashed` (it died on its own) and `killed` (a person killed it) are not the same fact and
 * the RUNS table shows them differently.
 */
export interface RunFrame {
  m: 'run'
  ts: string
  seq: null
  lane: string
  run_id: string
  n: number
  outcome: 'running' | 'ended cleanly' | 'killed by you' | 'completed' | 'crashed'
  started_iso: string
  ended_iso: string | null
  stage: number
  duration_s: number
  cost_usd: number
  exit_code?: number | null
  signal?: number | null
  agent?: string
  driver?: string
  model?: string
  cost_known?: boolean
}

/**
 * `transcript` · server → client
 *
 * One entry as it is parsed out of a run's output. The only sequenced frame, append-only, and
 * the only one resume replays.
 */
export interface TranscriptFrame {
  m: 'transcript'
  ts: string
  seq: number
  lane: string
  run_id: string
  entry: TranscriptEntry
  patch?: true
}

/**
 * `permission.request` · server → client
 *
 * M6 — the agent asked for permission, and the run is stopped dead until somebody answers. Two
 * fields carry the whole reason this message exists rather than a yes/no prompt.
 * `rule_that_would_be_written` is the exact rule `always` will write — literal, escaped, never
 * widened — so agreeing to it is agreeing to something readable rather than to a blank cheque.
 * `nearest_miss` is present when an existing rule for this same tool ALMOST matched, and it
 * names which one and where it diverged: a rule that nearly matched and a rule that is missing
 * are different problems with different fixes, and a request that cannot tell them apart
 * leaves the person clicking `always` again. This has already cost a run. `expires_at` is null
 * on purpose and stays null: a request that times out into a denial produces a run that failed
 * for a reason nobody saw.
 */
export interface PermissionRequestFrame {
  m: 'permission.request'
  ts: string
  seq: null
  lane: string
  request_id: string
  tool: string
  input_summary: string
  rule_that_would_be_written: string
  nearest_miss?: Record<string, unknown>
  frozen_by?: Record<string, unknown>
  expires_at: null
}

/**
 * `permission.resolved` · server → client
 *
 * M7 — a person answered at E23, or the request was voided because the run it belonged to is
 * gone. Sent to EVERY client rather than routed to a scope, so a request answered in one tab
 * stops waiting in the other. `voided` is not a fourth thing a person can click: it is what
 * the tool does when the run dies, and it exists so that a request is never left waiting for
 * ever — and so that an answer arriving afterwards is refused instead of granting a standing
 * permission nobody meant to grant.
 */
export interface PermissionResolvedFrame {
  m: 'permission.resolved'
  ts: string
  seq: null
  lane: string
  request_id: string
  decision: 'once' | 'always' | 'deny' | 'voided'
  rule_id?: string
}

/**
 * `rules` · server → client
 *
 * M12 — a rule was granted or revoked, or the policy changed. Sent to every client, so a
 * second tab cannot show a policy that is no longer in force. `source` is the whole of the
 * difference between the two kinds of rule: a rule declared in the manifest is a committed
 * file's, is listed as such, and cannot be revoked from a UI — a UI that silently overrides a
 * committed file is how a repository stops meaning what it says.
 */
export interface RulesFrame {
  m: 'rules'
  ts: string
  seq: null
  policy: 'ask_all' | 'edit_free' | 'never_ask'
  rules: unknown[]
}

/**
 * `pong` · server → client
 *
 * The answer to a ping. Bare: it carries no state, so a liveness check can never be mistaken
 * for data arriving.
 */
export interface PongFrame {
  m: 'pong'
  ts: string
  seq: null
}

/**
 * `error` · server → client
 *
 * A frame the server refused. The connection stays open and keeps delivering: the tool serves
 * a browser left open for days, and a socket that drops on the first surprise turns every
 * later ticket into a debugging session. Same shape as the HTTP error convention.
 */
export interface ErrorFrame {
  m: 'error'
  ts: string
  seq: null
  code: 'bad_frame' | 'unknown_message' | 'unknown_scope'
  message: string
  detail?: Record<string, unknown>
}

/** Everything the client may send. */
export type ClientFrame =
  | SubscribeFrame
  | ResumeFrame
  | PingFrame

/** Everything the server may send. */
export type ServerFrame =
  | HelloFrame
  | SnapshotFrame
  | CatalogChangedFrame
  | ResourceFrame
  | StageFrame
  | LaneStateFrame
  | LaneMetricsFrame
  | LaneMembershipFrame
  | LaneWordsFrame
  | IsolationFrame
  | CommandFrame
  | RunFrame
  | TranscriptFrame
  | PermissionRequestFrame
  | PermissionResolvedFrame
  | RulesFrame
  | PongFrame
  | ErrorFrame

export type Frame = ClientFrame | ServerFrame

/** Every message name in the contract, in schema order. */
export const MESSAGES = [
  'subscribe',
  'resume',
  'ping',
  'hello',
  'snapshot',
  'catalog.changed',
  'resource',
  'stage',
  'lane.state',
  'lane.metrics',
  'lane.membership',
  'lane.words',
  'isolation',
  'command',
  'run',
  'transcript',
  'permission.request',
  'permission.resolved',
  'rules',
  'pong',
  'error',
] as const
