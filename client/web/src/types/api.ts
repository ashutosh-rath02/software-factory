// GENERATED from factory/docs/technical/contracts/http.schema.json — do not edit by hand.
// Regenerate with `npm run gen:api-types`; tests/api-types.test.ts fails if this drifts,
// and gate/browser-gate.mjs validates the real server against the same schema.
// contract: factory-v0.1.0

/**
 * E18's optional start fields. Agent and model name manifest-declared choices; executable
 * paths, credentials and provider transport stay server-owned. Omitting agent selects
 * default_agent; omitting model selects that profile's default; omitting effort resolves role,
 * then run_effort, then the profile default. Legacy singular-agent manifests retain their
 * established defaults.
 */
export interface StartRunRequest {
  prompt?: string
  resume?: boolean
  agent?: string
  model?: string
  tree?: string
  effort?: string
}

/**
 * The three axes, never flattened (SPEC.md §3.2). `motion` is null on a document with no lane
 * behind it, which is every document until SRV-FEAT-011.
 */
export interface State {
  process: string
  motion: string | null
  evidence: string
}

/**
 * What git and the lane set the tool owns say became of this ticket's work (SPEC.md §3.5).
 * Derived, never read from the document: `state` is the answer, `claimed` is what the document
 * said, and `disagrees` is true when the two cannot both be right — which is the most valuable
 * thing on the row. `reason` is set only when `state` is `unknown`.
 */
export interface Delivery {
  state: 'not_started' | 'running' | 'stalled' | 'unshipped' | 'waiting' | 'done' | 'unknown'
  detail: string
  claimed: string
  disagrees: boolean
  needs_you: boolean
  reason?: string
}

/**
 * The pill's own count and its text, computed server-side so the list and the counter cannot
 * drift apart (SPEC.md E3).
 */
export interface NeedsYou {
  count: number
  total?: number
  label: string
}

/**
 * Where a ticket is in its own sequence. `total` is that sequence's length and nothing else —
 * progress is only comparable inside a group. `name` is the stage the `index` points at, `""`
 * when nothing has been reported yet: the socket's own stage object has carried it since the
 * contract was written, and E29 filters and facets by that word, so both halves of the wire
 * now say it rather than leaving a surface to recompute it from an index and a sequence it
 * fetched separately (SRV-FEAT-311). Additive: the member is optional and no existing consumer
 * reads it.
 */
export interface Stage {
  index: number
  name?: string
  total: number
  round: number
  of: number
  source: string
}

/**
 * A fact about the repository, not an error state: a missing directory is a manifest mistake
 * and the screen says which path. `code` is what a surface reads to decide whether this
 * EXPLAINS an empty list (`invalid_manifest`) or annotates a populated one (`path_missing`).
 * `command_unrunnable` (SRV-FIX-202) annotates a workspace whose declared `build`, `gate` or
 * `review` (SRV-FEAT-500) names a program nothing a lane claimed for it is given provides:
 * `pointer` is `/workspaces/<name>/<key>`, `field` is the manifest key a person edits and
 * `program` is the word that was not found. The manifest loads and the lane is claimed — this
 * is the only notice before that lane reaches GATE. `unaccountable_worktree` (SRV-FIX-432)
 * annotates E9: one entry directly under the lane root that a restart could not adopt as a
 * lane — not a registered worktree of this repository, or one whose branch is outside
 * `ticket/*`. `path` is that entry, absolute. It is a fact about the lane root and never an
 * error: nothing was created, moved, pruned or removed to produce it, and `E13` remains the
 * only path that removes anything. Every entry under the lane root is either a row in `lanes`
 * or one of these, so the deletion rule `S5` states can be answered from E9 alone.
 * `unknown_parent` (SRV-FEAT-547) annotates E3 and E4: a requirement's `parent:` names no
 * requirement in any declared repository — `id` is that requirement and `value` is the word it
 * wrote. `parent_cycle` (SRV-FEAT-547) is one entry per cycle, `ids` naming its members in the
 * order the walk found them. Both leave every requirement involved at the top level and the
 * response at 200: an unresolvable link is a fact about the repository's documents, never an
 * error state, and the screen is never blank.
 */
export interface Warning {
  code: string
  path?: string
  pointer?: string
  message: string
  field?: string
  program?: string
  id?: string
  value?: string
  ids?: string[]
}

/**
 * One row of 4.2: id, state dot, title, the needs pill, one tag per workspace it touches,
 * status and last-updated. `parent` and `children` are the tree (SRV-FEAT-547): the array this
 * row lives in is FLAT and carries every requirement, and a surface resolves each `children`
 * id against that array. A TOP-LEVEL row is one whose `parent` is `""` — a requirement that
 * names a parent is drawn under it and never at the top, and a `children` id the body does not
 * carry is a row a surface draws at the top rather than a crash.
 */
export interface RequirementRow {
  id: string
  title: string
  status: string
  parent: string
  children: string[]
  updated_iso: string
  path: string
  workspaces: string[]
  state: State
  delivery: Delivery
  needs_you: NeedsYou
  ticket_count: number
  own_ticket_count: number
  parse_error?: string
}

/**
 * One ticket inside a workspace group: id, dot, title, state, stage N/M, lane and cost.
 */
export interface TicketRow {
  id: string
  title: string
  status: string
  state: State
  delivery: Delivery
  stage: Stage
  needs_you: boolean
  lane: string | null
  cost_usd: number
  parse_error?: string
}

/**
 * One workspace's tickets, carrying the stage sequence its own tickets declared. A ticket
 * naming a workspace the manifest does not declare lands in a final group called `unassigned`
 * rather than disappearing.
 */
export interface Group {
  workspace: string
  stage_sequence: string[]
  stage_sequence_source?: string
  count: number
  tickets: TicketRow[]
}

/**
 * One of the manifest's `display.ticket_fields`, label and value, passed through untouched.
 * The tool does not know what any of them mean.
 */
export interface Extra {
  label: string
  value: string
}

/**
 * Derived, never reported: a report exists (claimed), and it contains the string of a run id
 * the tool assigned (proven).
 */
export interface Evidence {
  verdict: string
  run_id?: string
}

/**
 * What a ticket's runs add up to (E5). SPEC.md §4.5's identity row reads all three — `4 runs ·
 * 18m 40s · $1.86` — so they are typed rather than an untyped object the screen has to guess
 * at.
 */
export interface Runs {
  count: number
  duration_s: number
  cost_usd: number
}

/**
 * A LANE's motion axis (E9, M2). Deliberately not the string `$defs/state` carries: a row on a
 * list says whether anything is arriving, and a lane card has to say how long nothing has
 * been. `since` is what the client's clock ticks from, so a quiet socket does not freeze it
 * (SPEC.md §4.4).
 */
export interface Motion {
  value: string | null
  silent_for_s: number
  since: string | null
}

/**
 * The three axes of a lane, never flattened (SPEC.md §3.2). `alarm` is not a summary of them —
 * it names which two of the nine combinations carry a border, which is a fact about the pair
 * and not a rank. `blocked` is set only while a person is being asked.
 */
export interface LaneState {
  process: string
  motion: Motion
  evidence: string
  stopped: {
    reason: 'work_landed' | 'holds_work' | 'crashed' | 'never_started' | 'ended'
    seconds: number
    since: string | null
  } | null
  blocked?: Record<string, unknown> | null
  alarm?: boolean
}

/**
 * Where a LANE is in its ticket's sequence. Three fields, not `$defs/stage`'s five: `of` and
 * `source` are the document's answer and reach the screen through E5, while the rail draws a
 * bar and a name. `index` and `total` are both positions in the TICKET's own sequence — the
 * manifest's only when the ticket declares none — so a ticket of eight stages is out of eight,
 * never out of the manifest's ten.
 */
export interface LaneStage {
  index: number
  total: number
  round: number
}

/**
 * Why a lane is `queued`: the exclusive resource it needs, who holds it, and its place in the
 * queue. SPEC.md §4.4 draws a queued card with its position, so the position is on the wire
 * rather than derived in the browser.
 */
export interface WaitingFor {
  resource: string
  held_by: string | null
  holder_alive: boolean | null
  position: number
}

/**
 * FAC-EPIC-373 — what the agent in a lane last SAID or last CALLED, as one line. The three
 * kinds are the agent's own utterances and nothing else: `tool_result` is what the tool said
 * back, and `turn` and `error` are the tool's bookkeeping, so none of them moves it. `text` is
 * the first non-empty line of a `prose` block clipped to 160 code points with a trailing `…`,
 * the tool's name for a `tool`, and the tool's name, one space and the path the transcript
 * carries for a `file_edit`. It is never rewritten by the client. Derived in
 * `app/agent/transcript`, which is the only module that may read a CLI frame (MODEL.md S6).
 */
export interface LastWords {
  kind: 'prose' | 'tool' | 'file_edit'
  text: string
  at: string
}

/**
 * One lane: a worktree, a branch, a seat, and possibly one exclusive resource held (SPEC.md
 * §3.1). `progress_pct` is the share of the ticket's own stage sequence that is FINISHED —
 * `(stage.index - 1) * 100 / stage.total`, integer, floored at 0, because the stage last
 * reported is the one in progress — and `100` once the ticket's `status:` is one the manifest
 * declares under `status.done` (SRV-FIX-599). `remaining_s` is the seconds the stages still
 * ahead are expected to take, measured: the median duration of each one in this workspace's
 * finished stages, minus the time already spent in the current one. A stage nobody in the
 * workspace ever measured adds nothing; the answer is `null` when a stage ahead has only one
 * or two measurements or when none ahead has any, so it is never a guess, and `0` when the
 * ticket is done.
 */
export interface Lane {
  lane: string
  ticket_id: string
  workspace: string
  role: string
  branch: string
  worktree: string
  state: LaneState
  needs_you: boolean
  detail?: Record<string, unknown> | null
  stage: LaneStage
  progress_pct: number
  remaining_s: number | null
  elapsed_s: number
  cost_usd: number
  last_words: LastWords | null
  run_id?: string | null
  session_id?: string | null
  resources_held: string[]
  waiting_for?: Record<string, unknown> | null
}

/**
 * An exclusive resource and who is waiting for it. The board is one of these: two lanes cannot
 * flash it at once, so the second is `queued` and the queue is on the wire rather than
 * inferred.
 */
export interface Resource {
  name: string
  exclusive: boolean
  description: string | null
  held_by: string | null
  holder_alive: boolean | null
  queue: string[]
}

/**
 * How many agent seats this host declared and how many are taken. A lane needs one, so this is
 * the other thing that can make a claim wait.
 */
export interface Seats {
  host: {
    total: number
    used: number
  }
}

/**
 * F31 — a role the tool started for this ticket, or one queued for a seat. `lane` and `run_id`
 * exist only once it has been given one, and `pending` means queued rather than dropped: it is
 * a fact drawn quietly, and none of SPEC.md §3.3's clauses, so it never enters the `needs you`
 * count.
 */
export interface Role {
  role: string
  stage: string
  state: 'pending' | 'started'
  lane?: string
  run_id?: string
}

/**
 * SPEC.md §3.6 — the action that ends the state this error describes. It travels INSIDE the
 * failure that needs it, never in a second call: a remedy that arrives separately from its
 * failure is a remedy that can be missing at the moment the failure is shown. `action` is a
 * verb from §9.2's closed set and nothing else, so a server that invents one is a schema
 * failure rather than a blank button; `target` is what the verb acts on, so the client builds
 * the call from the remedy instead of re-deriving it from the code; `destroys` names what is
 * lost from §9.3's nouns and is `[]` when nothing is, which is what makes *no failure's only
 * remedy destroys work* checkable; `confirm` is present when the action needs a human sentence
 * before it fires, and whether to confirm is the server's statement about consequence, not a
 * client's taste. §11's check 12 holds all of it.
 */
export interface Remedy {
  action: 'retry' | 'reload_manifest' | 'claim_lane' | 'open_lane' | 'start_run' | 'end_session' | 'kill_run' | 'recover_lane' | 'release_lane' | 'discard_ignored' | 'release_resource'
  target?: Record<string, unknown>
  label: string
  destroys: ('turn' | 'session' | 'worktree' | 'ignored_files')[]
  confirm?: string
}

/**
 * SPEC.md §5's one error shape. A thing that does not exist is 404 with `detail.path` naming
 * where the tool looked — nine of ten of those are a manifest pointing at the wrong directory.
 * `remedy` is §3.6's: the action that ends this state, offered by the failure that needs it.
 * `null` is legal only for a code named in §11 check 12's exemption table, with its reason — a
 * silent null is a red gate.
 */
export interface ErrorBody {
  error: {
    code: string
    message?: string
    detail?: Record<string, unknown>
    remedy?: Remedy | null
  }
}

/**
 * Provider cumulative token totals saved in run meta. Effective tokens are uncached input plus
 * one tenth of cached input plus output. Older runs may have no usage.
 */
export interface RunUsage {
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  reasoning_output_tokens: number
  effective_tokens: number
}

/**
 * One row of E7 — a run as its own `meta.json` recorded it, which is why it outlives the
 * process that made it (F6) and survives its transcript being cleaned. `outcome` is the
 * record's six words, the same six E31's `runIndexRow` carries — one record, one vocabulary.
 * The sixth is `orphaned`, which F6's settle writes over `running` when nothing is behind the
 * record any more, and it is a word only a RECORD can hold: `M11 run` is published from a live
 * run and names five, because a settled run has no process left to publish from (SRV-FIX-344).
 * §4.10's six are a different six — the client's reading of the terminal words, because
 * `completed` splits on §3.4's evidence.
 */
export interface RunRow {
  run_id: string
  n: number
  started_iso: string
  ended_iso: string | null
  outcome: 'running' | 'ended cleanly' | 'killed by you' | 'completed' | 'crashed' | 'orphaned'
  stage: number
  duration_s: number
  cost_usd: number
  usage?: RunUsage
  lane: string
  session_id: string
  agent?: string
  driver?: string
  model?: string
  cost_known?: boolean
  tree: RunTree
  effort?: string | null
}

/**
 * One row of E31 — E7's row plus the three a repository-wide list needs and a per-ticket one
 * does not: `ticket_id`, `ticket_title` and `kind`. It is read out of the run's own
 * `meta.json`, so it outlives the process that made it (F6) and survives its transcript being
 * cleaned — the row is the record and the log is the evidence, and E8 answering 404 for it is
 * §4.10's sentence rather than this list's problem. `kind` is the role the run was started
 * for, from the manifest's `roles` (F31), and `run` for one nobody declared a role for: the
 * manifest's word, never the tool's, which is why it is a bare string here and not an enum.
 * `ended_iso` is ABSENT while a run is live — not null, not the start time — and `duration_s`
 * is then measured to now. `outcome` is `runRow`'s six words exactly — this list and the
 * ticket's own tab describe one record with one vocabulary, and `orphaned` is the one F6's
 * settle writes over `running` when nothing is behind the record any more.
 */
export interface RunIndexRow {
  run_id: string
  n: number
  kind: string
  ticket_id: string
  ticket_title: string
  lane: string
  stage: number
  outcome: 'running' | 'ended cleanly' | 'killed by you' | 'completed' | 'crashed' | 'orphaned'
  started_iso: string
  ended_iso?: string
  duration_s: number
  cost_usd: number
  session_id: string
  agent?: string
  driver?: string
  model?: string
  cost_known?: boolean
  tree: RunTree
  effort?: string | null
}

/**
 * One replayed transcript entry. Its kinds and payloads are SPEC.md §4.6's table and belong to
 * the module that parses the agent's output; this types only what every kind carries, exactly
 * as socket.schema.json's `entry` does.
 */
export interface TranscriptEntry {
  seq: number
  ts?: string
  kind: string
}

/**
 * One changed file. `committed` is false for work that is only in the worktree — a person
 * deciding whether to keep a lane has to see that too, so a diff of commits alone would answer
 * the wrong question.
 */
export interface DiffFile {
  path: string
  status: string
  added: number
  removed: number
  committed: boolean
}

/**
 * One commit the lane made on its own branch, with the subject a person wrote and the sha they
 * would check out.
 */
export interface DiffCommit {
  sha: string
  subject: string
  iso: string
}

/**
 * What the lane adds up to, summed by the server so the DIFF tab's count and its rows cannot
 * disagree.
 */
export interface DiffTotals {
  files: number
  added: number
  removed: number
  commits: number
}

/**
 * One standing rule — E11 lists them and M12 broadcasts the same six fields, so the client
 * holds one type for both. `source` is `manifest` or `granted`, and it is the whole of the
 * difference between the two kinds: a rule declared in the manifest is a committed file's,
 * cannot be revoked from a UI, and has a null `granted_iso` because it was declared and never
 * granted. `match_count` lives for the life of the tool process and is deliberately not
 * persisted — a policy file rewritten on every match is a file with a permanent diff.
 */
export interface PermissionRule {
  rule_id: string
  pattern: string
  source: string
  granted_iso: string | null
  match_count: number
  revocable: boolean
}

/**
 * One cell of §4.18's `Where the work is` row: a `delivery` value (SPEC.md §3.5) and how many
 * of this repository's tickets are in it. `E30` serves one entry per value IN §3.5's TABLE
 * ORDER, including the values whose count is zero — a row of counts that omits its empty
 * members is a row whose shape changes under the reader. It is one axis and not a pipeline:
 * the design's nine steps mix a lane's `process`, a role's state and delivery into one line,
 * which is §3.2's rule broken in the one place a person most wants it broken.
 */
export interface DeliveryCount {
  state: string
  count: number
}

/**
 * One ticket that needs a person, and WHY: `clauses` are §3.3's seven conditions by the names
 * `E9` already uses for the same facts — a second vocabulary for them is two places the states
 * drift. `detail` is §3.3's own sentence for the clauses that fired, so the row says why
 * rather than drawing an alarm. `remedy` is §3.6's object or `null`, and a `null` here is
 * ordinary: most of §3.3's clauses end when a person reads something, not when the tool acts.
 * A ticket satisfying three clauses is ONE row and one unit of somebody's attention.
 */
export interface NeedsYouRow {
  ticket_id: string
  title: string
  clauses: ('idle' | 'blocked' | 'crashed' | 'claimed_unproven' | 'silent' | 'stalled' | 'unshipped')[]
  detail: string
  remedy: Remedy | null
  lane?: string | null
}

/**
 * A ticket whose work exists and has never left this machine: `delivery` is `unshipped` with
 * commits no remote has (SPEC.md §3.5 rule 3). `commits` is how many are ahead of the base
 * branch, and `age_s` is how long the OLDEST of them has been sitting here — the panel's job
 * is that nobody discovers a week of work living on one laptop, and the newest commit's age
 * cannot answer that. Seconds, formatted by the client. IT NAMES THEM AND DOES NOT PUSH THEM:
 * the row carries no remedy, because §3.5's read-only property is six git subcommands served
 * at `E1` so it can be asserted, and one write verb here would end that guarantee for the
 * whole product in exchange for one button (§4.18).
 */
export interface UnpushedRow {
  ticket_id: string
  title: string
  branch: string
  commits: number
  age_s: number
}

/**
 * One ticket with the axes SPEC.md §3.2b's nine predicates read, carried in E30's same one
 * read. THE AXES AND NEVER THE CELL: no member here holds §3.2b's ten values, because the
 * flattening is the client's one derived presentation axis and a server that served it would
 * be the second definition §3.2b constraint 3 forbids — a surface that recomputes it is a
 * second answer to one question, and so is a server that answers it first. `status` is the
 * document's own claim and `delivery` is what git says, both carried, exactly as §4.17's row
 * carries both. `verdict` and `escaped` are the two facts git does not carry and §3.5b is
 * where each is read: `verdict` is `pass` or `fail` only when a verdict document exists beside
 * the ticket's report AND the document claims one of those two words, and `null` otherwise — a
 * claim nobody else wrote down is a claim, not a verdict. `escaped` is a row of the ledger
 * `docs.escapes` names carrying this ticket's id; a repository that declares no ledger answers
 * `false` everywhere, because no ledger and no row are the same answer to *does a row name
 * this*.
 */
export interface HomeTicketRow {
  ticket_id: string
  title: string
  lane: string | null
  role: string | null
  state: State
  delivery: Delivery
  status: string
  verdict: string | null
  escaped: boolean
}

/**
 * How many lane seats this host declared and how many are taken, at the instant the rest of
 * this answer was read. The same two numbers `E9` serves as `seats.host`, carried here so
 * §4.18's three panels and the count beside them come out of one read rather than out of a
 * second request that can disagree.
 */
export interface LaneOccupancy {
  occupied: number
  total: number
}

/**
 * Who has touched this ticket (SPEC.md §4.17). `source` states where the answer came from and
 * is the whole point of the object: `commits` means the author of the commits the ticket's
 * branch holds that the base branch does not — read with `rev-list`, one of §3.5's six
 * read-only subcommands, and the most frequent author of them — and `document` means there are
 * no such commits and the value is the `owner` field `display.ticket_fields` named, passed
 * through untouched. A branch already merged into the base holds no commits the base does not,
 * so a merged ticket falls back to the document exactly as an unstarted one does; the tool
 * does not reconstruct which side of a merge a commit came from. A ticket with neither has NO
 * owner and the member is absent rather than an empty string: nobody has touched this and
 * somebody wrote a blank are different facts.
 */
export interface Owner {
  name: string
  source: 'commits' | 'document'
  value?: string
}

/**
 * One value a filter can take and how many tickets in the WHOLE repository carry it — never
 * how many are on the page. A facet computed over the window reports one set of stages on page
 * one and a different set on page two for the same repository, while every assertion about the
 * rows passes (WEB-FIX-091).
 */
export interface Facet {
  value: string
  count: number
}

/**
 * The two axes §3.2 forbids flattening, counted separately. `provenance` takes
 * `delivery.state`'s own values (§3.5) and `stage` the stage names tickets have actually
 * reached, with `not_started` for the ones that have reached none. Both are counted over every
 * ticket matching `q`, BEFORE the `provenance` and `stage` filters and before the page window
 * — so a facet says what clicking it would give rather than what is already selected, while
 * `total` stays the filtered total.
 */
export interface Facets {
  provenance: Facet[]
  stage: Facet[]
}

/**
 * What the server actually applied, echoed so the footer states it rather than re-deriving it
 * from the URL the client thinks it sent. `provenance` is `all` when nothing narrowed it; `q`
 * and `stage` are empty strings when absent.
 */
export interface AppliedFilters {
  q: string
  provenance: string
  stage: string
}

/**
 * One row of §4.17's table. `provenance` is `delivery.state` and NEVER the document's
 * `status:` field — the document is a claim written by a session that may not have survived to
 * write it truthfully. Both are carried: `status` is what the document said, `provenance` is
 * what git says, and `delivery.disagrees` marks the pair when they cannot both be right, which
 * is the most valuable thing on the row (§3.5). `provenance` and `stage` are two axes and are
 * never merged into one status chip (§3.2).
 */
export interface TicketIndexRow {
  id: string
  title: string
  workspace: string
  status: string
  provenance: string
  delivery: Delivery
  stage: Stage
  lane: string | null
  evidence: Evidence
  roles: Role[]
  owner?: Owner
  extras: Extra[]
  cost_usd: number
  needs_you: boolean
  parse_error?: string
}

/**
 * The tickets one requirement claims as its children, and the group header's own numbers.
 * `count`, `merged` and `pushed` are over the WHOLE group after filtering — not over the page
 * — and they are members of this object so the design's two-segment bar cannot describe a set
 * the rows below it are not (WEB-FIX-091). `tickets` is this group's slice of the page window
 * and is empty on a page that lands entirely in other groups. A ticket whose `parent` names no
 * requirement on disk lands in the synthetic `standalone` group, last, which is the same one
 * name E3 and E4 already use.
 */
export interface TicketGroup {
  parent: string
  title: string
  count: number
  merged: number
  pushed: number
  tickets: TicketIndexRow[]
}

/**
 * One cell of the ledger's table. `column` is the file's own header text, verbatim.
 * `ticket_id` is set when the cell's text contains an id the catalog resolves, so the client
 * links it and parses nothing; a cell naming an id nobody wrote carries text and no
 * `ticket_id`.
 */
export interface EscapeCell {
  column: string
  text: string
  ticket_id?: string
}

/**
 * One row of the ledger, one `cells` entry per column, in the file's order.
 */
export interface EscapeRow {
  cells: EscapeCell[]
}

/**
 * One frontmatter key the tool does not understand, in the order the document wrote it. Values
 * pass through untouched — the tool renders them as labels and knows none of them, exactly as
 * `display.ticket_fields` reaches E5's `extras`.
 */
export interface WikiFront {
  label: string
  value: string
}

/**
 * One page. `id` is the filename without its extension — the repository's own word and the
 * only name this surface has for a page. `title` is the frontmatter's, falling back to the
 * filename. A document whose frontmatter will not parse stays in the list with `parse_error`
 * set and `body_markdown` intact: one unreadable file may not blank a screen.
 */
export interface WikiSection {
  id: string
  title: string
  path: string
  bytes: number
  front: WikiFront[]
  body_markdown: string
  parse_error?: string
}

/**
 * One repository in the monorepo this manifest declares. A monorepo is a SET of repositories,
 * each carrying its own documents; the tool serves them so a surface can show what it is
 * pointed at without reading the manifest file.
 */
export interface ManifestRepo {
  path: string
  docs: {
    epics: string
    tickets: string
    reports: string
    wiki?: string
    escapes?: string
  }
}

/**
 * One reduction over a collection of runs, in minutes. `median` and `mean` are `null` when `n`
 * is 0 — nothing was measured, which is not the same fact as a measured zero, and a screen
 * drawing `0.0m` over an empty window says something false about a repository that has simply
 * not run anything.
 */
export interface Series {
  median: number | null
  mean: number | null
  n: number
}

/**
 * One stage of the repository's own sequences, with the minutes its runs spent in it. `name`
 * is a stage the repository declared — on a ticket's `stages:` line or in the manifest — never
 * a name the tool knows (S1). A stage no run reported has no row: a row with `n: 0` would be a
 * claim about a collection with no members.
 */
export interface StageMinutes {
  name: string
  median: number
  mean: number
  n: number
}

/**
 * One DIRECT child requirement on E4, with that child's own tickets grouped exactly as its own
 * page groups them — `tickets` is E4's `group` shape reused, not a second one. One level deep:
 * a grandchild appears on ITS parent's page, and E3 is where the whole tree is. `ticket_count`
 * is the same roll-up E3's row for this id carries, so one word has one meaning on both
 * endpoints; `own_ticket_count` is beside it because the roll-up sits next to a collection
 * holding the own tickets only, and a number beside a collection it is not the count of is a
 * false sentence (WEB-FIX-091).
 */
export interface RequirementChild {
  id: string
  title: string
  repo: string
  status: string
  own_ticket_count: number
  ticket_count: number
  delivery: Delivery
  tickets: Group[]
}

/**
 * A finite explicit command step. Reuse is opt-in because ignored outputs and external
 * dependencies are outside the source identity.
 */
export interface RoutingStep {
  which: string
  accepted_exit_codes: number[]
  reuse?: boolean
  require_decision?: boolean
}

/**
 * E33 strict submission. Duplicate JSON members and unknown fields are refused before effects.
 */
export interface RoutingRequest {
  request_id: string
  steps: RoutingStep[]
}

/**
 * Terminal captured command evidence bound to inputs. Complete captures are bytes, exposed as
 * base64 only when explicitly requested. An empty source_after records unknown inputs only on
 * the final nonreused receipt of needs_main/main/source_unresolved; it cannot certify
 * completion or reuse.
 */
export interface RoutingReceipt {
  index: number
  which: string
  command: string
  command_id: string
  source: string
  source_after: string
  exit_code: number
  capture_path: string
  capture_sha256: string
  bytes: number
  accepted: boolean
  reused: boolean
  complete: boolean
  output_base64?: string
}

/**
 * A stable request for advice after repeated identical actual failures; it does not claim an
 * advisor session ran.
 */
export interface RoutingCheckpoint {
  id: string
  kind: 'repeated_error'
  cause: string
  receipt_count: number
}

/**
 * RunTree
 */
export interface RunTree {
  name: string
  decider: string
  agents: string[]
  continuation?: 'goal'
  compact_on_stage?: boolean
  token_budget?: number
}

/** `E1` · `/api/health` */
/**
 * E1 — who this is, what it is pointed at, and how long it has been up. Always 200: a process
 * that is up with a broken manifest must be able to say so.
 */
export interface Health {
  factory: string
  manifest: string
  repo: {
    name: string
    root: string
    git_root: string
  }
  uptime_s: number
  delivery?: {
    available: boolean
    reason?: string
    base?: string
    forge?: string
    git: string[]
  }
  detail?: Record<string, unknown>
}

/** `E2` · `/api/manifest` */
/**
 * E2 — the manifest as loaded, normalised. Everything the tool knows about the repository it
 * is pointed at is in here, and the tool attaches meaning to none of the names. A manifest
 * declaring `repos` carries that block INSTEAD of a top-level `docs` — the documents belong to
 * a repository, and two answers to where they are is a manifest that means nothing, so the
 * loader refuses both together and this body never carries both (SRV-FIX-181).
 */
export interface Manifest {
  factory: string
  repo: {
    name: string
    root: string
  }
  path: string
  workspaces: Record<string, unknown>
  resources?: Record<string, unknown>
  lanes: {
    host: number
  }
  docs?: {
    epics: string
    tickets: string
    reports: string
  }
  repos?: Record<string, unknown>
  stages?: string[]
  status?: Record<string, unknown>
  display?: Record<string, unknown>
  default_agent?: string
  default_tree?: string
  trees?: Record<string, unknown>
  agents?: Record<string, unknown>
  agent: {
    command: string
  }
  resolved: {
    paths: Record<string, unknown>
  }
  warnings: Warning[]
  run_effort?: string
}

/** `E3` · `/api/requirements` */
/**
 * E3 — the requirement list, filtered and searched server-side so the count line and the rows
 * cannot disagree. `total` is the count line; it is never computed in the browser.
 */
export interface Requirements {
  requirements: RequirementRow[]
  total: number
  in_flight: number
  warnings?: Warning[]
}

/** `E4` · `/api/requirements/{id}` */
/**
 * E4 — one requirement and its tickets, grouped by workspace, each group carrying its own
 * stage sequence, preceded by its direct child requirements (SRV-FEAT-547). `state` and
 * `delivery` here are rolled up over this requirement and every descendant, so §4.2's row and
 * §4.3's header cannot say two different words about the same document; `needs_you` is not —
 * it is `{count,total}` over the `groups` this page draws.
 */
export interface Requirement {
  id: string
  title: string
  status?: string
  path?: string
  updated_iso?: string
  summary: string
  body_markdown: string
  needs_you: NeedsYou
  state?: State
  delivery?: Delivery
  children: RequirementChild[]
  groups: Group[]
  warnings?: Warning[]
}

/** `E5` · `/api/tickets/{id}` */
/**
 * E5 — one ticket: its frontmatter, the repository's own extra fields in declaration order,
 * its body, and where the file is.
 */
export interface Ticket {
  id: string
  title: string
  workspace: string
  owner?: Owner
  status: string
  parent?: string
  path: string
  updated_iso?: string
  stages: string[]
  stage: Stage
  extras: Extra[]
  body_markdown: string
  lane?: string | null
  runs?: Runs
  report: {
    exists: boolean
    path: string
    evidence: string
  }
  needs_you?: boolean
  state: State
  delivery: Delivery
  roles?: Role[]
  parse_error?: string
}

/** `E6` · `/api/tickets/{id}/report` */
/**
 * E6 — the delivery report and its evidence verdict. Its 404 is the normal case, not an error:
 * it is what 4.9's empty state renders, and the client must not log it as a failure.
 */
export interface Report {
  ticket_id: string
  path: string
  updated_iso?: string
  body_markdown: string
  evidence: Evidence
}

/** `E7` · `/api/tickets/{id}/runs` */
/**
 * E7 — a ticket's run history, newest first. An unknown ticket is a 404 and a known ticket
 * with no runs is a 200 with an empty list: two different questions, and they must not share
 * an answer.
 */
export interface RunHistory {
  runs: RunRow[]
}

/** `E8` · `/api/runs/{run_id}/transcript` */
/**
 * E8 — one run's transcript, replayed: backfill for the live SESSION tab, and the whole of a
 * finished run read back from the RUNS tab. A run whose log has been cleaned answers 404
 * naming the path it looked for, while the run's row survives in E7 — history is expendable
 * and correctness is not.
 */
export interface Transcript {
  run_id: string
  entries: TranscriptEntry[]
  next_after_seq: number
  complete: boolean
}

/** `E9` · `/api/lanes` */
/**
 * E9 — the lane set: every lane, the exclusive resources and who holds them, and the host's
 * seats. It is the data behind SPEC.md §4.4's rail AND §4.1's `N lanes need you` chip, read
 * once and shared, because two readers of one endpoint are two answers that can disagree.
 */
export interface Lanes {
  lanes: Lane[]
  resources: Resource[]
  seats: Seats
  warnings?: Warning[]
}

/** `E10` · `/api/lanes/{lane}/diff` */
/**
 * E10 — what this lane changed and what it committed, and with `?path=` one file's patch. A
 * lane that changed nothing answers 200 with empty arrays, which the DIFF tab renders as
 * "Nothing changed in this lane yet."; an unknown lane is a 404 naming it. Deciding whether to
 * keep the work is the point of the exercise, and it cannot be done from a transcript (F23).
 */
export interface Diff {
  lane: string
  branch: string
  base?: string | null
  base_branch?: string | null
  files: DiffFile[]
  commits: DiffCommit[]
  totals: DiffTotals
  patch?: string
}

/** `E11` · `/api/permissions/rules` */
/**
 * E11 — the standing policy and every rule in force, for SPEC.md §4.12. Read once when the
 * surface mounts and never again on a clock: E20, E21 and E23 all broadcast M12 to every
 * client, so the screen changes because the tool said it changed and a second tab cannot show
 * a policy that is no longer in force. `policy_source` is `session` once E21 has been used in
 * this tool process and `manifest` otherwise. A corrupt rules file is a 200 with `warnings`
 * and an EMPTY rule set — a policy fails closed, to asking, never open.
 */
export interface PermissionRules {
  policy: string
  policy_source: string
  rules: PermissionRule[]
  warnings?: Warning[]
}

/** `E18` · `/api/lanes/{lane}/runs` */
/**
 * E18 — a run started in the lane. The resolved agent profile, driver, model and reasoning
 * effort are returned with its process and session identity, including when defaults were
 * selected. `effort` is what the launch sent, and `null` when it sent none. An invalid
 * selection is refused before spawning; no fallback driver is substituted.
 */
export interface StartedRun {
  run_id: string
  session_id: string
  pid: number
  agent: string
  driver: string
  model: string
  tree: RunTree
  effort?: string | null
}

/** `E26` · `/api/tickets/ids` */
/**
 * E26 — one ticket id, allocated by the tool, handed to exactly one caller. SPEC.md §4.4's
 * shared-value rule on the wire: a lane is a checkout cut at claim time and nothing merges
 * into a live one, so an id each lane computes from the files it can see is an id two lanes
 * can compute the same value for and both be right. `number` is the number alone and `id` is
 * what the document will be called; `series` is the sequence it came from — `FAC` for
 * `FAC-FIX`, `""` for a prefix that is only a type — because the counter is the series' and
 * never the type's. The high-water mark spans every declared repository, and an id handed out
 * is spent whether or not a document is ever written with it.
 */
export interface TicketId {
  id: string
  prefix: string
  series: string
  number: number
}

/** `E30` · `/api/home` */
/**
 * E30 — where the work is, what needs you, and what has never left this machine, in ONE read.
 * §4.18 draws three panels and they are three views of one question: three endpoints would be
 * three answers about one repository at three instants, which is FAC-EPIC-004's defect class
 * generalised, and the counts in the first panel and the rows in the second would be free to
 * disagree while each stayed correct. NOTHING HERE WRITES. `needs_you.total` counts tickets
 * and not clauses. Reads product repositories only, as E3 and E29 do. `tickets` carries the
 * same read one row per ticket, with the axes §3.2b's predicates need and never the cell they
 * flatten to: the strip and the counts beside it are then two views of one array read at one
 * instant, which is what this endpoint exists to guarantee. A repository with nothing in it
 * answers 200 with every count zero and both row lists empty, and the `docs` directories
 * absent answers the same plus `warnings:[{code:"path_missing",path}]` — a 404 for either
 * would say *this repository does not exist* about a repository that does.
 */
export interface Home {
  delivery: DeliveryCount[]
  needs_you: {
    total: number
    rows: NeedsYouRow[]
  }
  unpushed: {
    total: number
    rows: UnpushedRow[]
  }
  lanes: LaneOccupancy
  tickets?: HomeTicketRow[]
  warnings?: Warning[]
}

/** `E29` · `/api/tickets` */
/**
 * E29 — every ticket in the repository, grouped by the requirement it names as `parent`, with
 * the filter facets and the page window. Filtering, search, faceting and paging are all the
 * server's: `total`, `from` and `to` are members of this answer and are what the count line
 * renders, so a screen cannot say `showing 1–12 of 12` about a repository with three hundred
 * tickets while every assertion about its rows passes (WEB-FIX-091). `?q=` matches id, title,
 * owner and lane case-insensitively; `?provenance=` takes `delivery.state`'s values and
 * `?stage=` a stage name the repository declares — two axes, never collapsed into one status
 * parameter (§3.2) — and a value neither declares is `400 bad_filter` naming the parameter and
 * listing what is accepted, exactly as E3 refuses one. A `page` beyond the last is an empty
 * window and not a 404: a stale page number in a link somebody sent is not a missing thing,
 * and `page_size` above the maximum is clamped with
 * `warnings:[{code:"clamped",field:"page_size"}]`.
 */
export interface Tickets {
  groups: TicketGroup[]
  total: number
  from: number
  to: number
  page: number
  pages: number
  page_size: number
  facets: Facets
  applied: AppliedFilters
  warnings?: Warning[]
}

/** `E31` · `/api/runs` */
/**
 * E31 — every run in the repository, newest first; the index E7 is the per-ticket slice of.
 * `?limit=<int, default 100, max 500>&before=<run_id>` pages it, and `total` counts every run
 * recorded rather than the page. `committed` is `false` and is SERVED rather than described,
 * because `.factory/runs/` is not committed: §4.19's line about this being one machine's
 * history is a fact the client reads instead of a sentence somebody typed into a component. No
 * runs at all — `.factory/runs/` absent, or present and empty — is a 200 with an empty list
 * and `total: 0`: history is expendable (§2) and its absence is a state, not an error.
 */
export interface RunIndex {
  runs: RunIndexRow[]
  total: number
  committed: boolean
}

/** `E28` · `/api/escapes` */
/**
 * E28 — the ledger of defects a gate passed. `columns` is the header row of the FIRST markdown
 * table in the declared file, verbatim; `rows` are its rows in the file's order. Nothing is
 * sorted, ranked, dated or scored: the file's order is the repository's statement about its
 * own history. `docs.escapes` not declared is a 404 `not_found` with `detail.key`; declared
 * and the file absent is a 200 with empty `columns` and `rows` and a `path_missing` warning,
 * because a missing file is a manifest mistake and not an error state. Present with no
 * markdown table is a 200 and no warning — a ledger with no rows yet is a repository that has
 * not been caught out.
 */
export interface Escapes {
  columns: string[]
  rows: EscapeRow[]
  total: number
  path: string
  warnings?: Warning[]
}

/** `E27` · `/api/wiki` */
/**
 * E27 — the repository's own pages, whole. No request parameters: filtering a handful of
 * hand-written pages is the client's, and the whole set is one answer. `docs.wiki` not
 * declared is a 404 `not_found` with `detail.key` and no `detail.path`, because there is no
 * path to name — the answer is a line to add to the manifest. Declared and the directory
 * absent is a 200 with an empty list and a `path_missing` warning.
 */
export interface Wiki {
  sections: WikiSection[]
  total: number
  warnings?: Warning[]
}

/** `E32` · `/api/metrics` */
/**
 * E32 — the playbook's stage metrics for a window of N days, computed when asked from the run
 * records and documents the tool already holds. `?days=<int 1..3650, default 30>`; nothing is
 * indexed, nothing is cached and nothing is written (S2). Every series is bounded by its own
 * clock: the run series by `started_iso`, `escapes_in_window` by the date cell of the ledger
 * row, `spec_to_land_hours` by the landing commit's date. EVERY RATE IS `null` WHEN ITS
 * DENOMINATOR IS 0 and a number only when something was measured — `0` means measured and none
 * qualified, which is a different fact from nothing having run, and a screen that cannot tell
 * them apart draws a failing process over an empty repository. Counts stay `0`, because a
 * count of nothing is nothing. `first_pass_qa_rate` and `tickets_with_verdict` are the same
 * collection: the rate's denominator is served beside it so a surface cannot draw one over the
 * other's population (WEB-FIX-091). A window with no runs at all is a 200, not a 404: history
 * is expendable (§2) and its absence is a state. `?days=` that is not an integer in 1..3650 is
 * a 400 `bad_window`.
 */
export interface Metrics {
  window_days: number
  runs: number
  tickets_with_verdict: number
  first_pass_qa_rate: number | null
  rounds_per_ticket: {
    median: number | null
    mean: number | null
  }
  stage_minutes: StageMinutes[]
  active_minutes: Series
  spec_to_land_hours: {
    median: number | null
    n: number
  }
  escapes_in_window: number
  rework_after_build: {
    tickets: number
    share: number | null
  }
}

/** `E33` · `/api/lanes/{lane}/routing` */
/**
 * Run an explicit finite plan of manifest-declared commands through E19 confinement. Identical
 * retained delivery returns 200. Accepted completion returns control to main and never grades
 * or lands a ticket.
 */
export interface RoutingSubmission {
  request_id: string
  lane: string
  ticket_id: string
  source: string
  state: 'running' | 'completed' | 'needs_main' | 'interrupted'
  action: 'wait' | 'main' | 'advisor'
  reason: string
  next_index: number
  receipts: RoutingReceipt[]
  checkpoint: RoutingCheckpoint | null
}

/** `E34` · `/api/lanes/{lane}/routing/{request_id}` */
/**
 * Read a retained routing job without dispatch. include_output=true includes the complete
 * base64 capture. Damaged evidence or changed inputs cannot be presented as reusable
 * completion.
 */
export interface RoutingState {
  request_id: string
  lane: string
  ticket_id: string
  source: string
  state: 'running' | 'completed' | 'needs_main' | 'interrupted'
  action: 'wait' | 'main' | 'advisor'
  reason: string
  next_index: number
  receipts: RoutingReceipt[]
  checkpoint: RoutingCheckpoint | null
}

/** Every response body this client reads, by the endpoint number SPEC.md §5 gives it. */
export interface Responses {
  E1: Health
  E2: Manifest
  E3: Requirements
  E4: Requirement
  E5: Ticket
  E6: Report
  E7: RunHistory
  E8: Transcript
  E9: Lanes
  E10: Diff
  E11: PermissionRules
  E18: StartedRun
  E26: TicketId
  E30: Home
  E29: Tickets
  E31: RunIndex
  E28: Escapes
  E27: Wiki
  E32: Metrics
  E33: RoutingSubmission
  E34: RoutingState
}

/** The paths, so a caller cannot invent one. */
export const PATHS = {
  E1: '/api/health',
  E2: '/api/manifest',
  E3: '/api/requirements',
  E4: '/api/requirements/{id}',
  E5: '/api/tickets/{id}',
  E6: '/api/tickets/{id}/report',
  E7: '/api/tickets/{id}/runs',
  E8: '/api/runs/{run_id}/transcript',
  E9: '/api/lanes',
  E10: '/api/lanes/{lane}/diff',
  E11: '/api/permissions/rules',
  E18: '/api/lanes/{lane}/runs',
  E26: '/api/tickets/ids',
  E30: '/api/home',
  E29: '/api/tickets',
  E31: '/api/runs',
  E28: '/api/escapes',
  E27: '/api/wiki',
  E32: '/api/metrics',
  E33: '/api/lanes/{lane}/routing',
  E34: '/api/lanes/{lane}/routing/{request_id}',
} as const

/** The verbs that reach them, so a caller cannot invent one of those either. */
export const METHODS = {
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
} as const
