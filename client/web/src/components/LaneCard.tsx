import type { MouseEvent, ReactNode } from 'react'
import { X } from '@phosphor-icons/react'

import { tintFor, tone } from './Chips'
import { Clock } from './Clock'
import { formatLeft, formatSilence, laneBlockingLine, laneSubLine } from '../vocabulary'
import type { Lane, Resource } from '../types/api'

/**
 * A lane card (Dashboard.dc.html for the sheet's six, :114-134 for the rail's).
 *
 * The vocabulary sheet draws six of these, and they are lane cards rather than a separate
 * component on purpose — the sheet documents what the product renders, so it has to render the
 * product's own component or it will drift from it.
 *
 * `WEB-FEAT-013` gave it the four things the design's rail draws and the sheet had no use for:
 * the sub-line, the progress bar with its stage name, the selected border, and a link. It is the
 * same component with more props, deliberately: a `RailLaneCard` beside this one would make the
 * sheet start lying, which is the failure `SPEC.md` §4.11's own test exists to prevent.
 *
 * `elapsed` and `cost` are nodes rather than strings because the rail's elapsed is a CLOCK — it
 * counts up from a timestamp with no frames arriving — and the sheet's is a sample.
 *
 * `WEB-FEAT-050` gave it a THIRD caller — SPEC.md §4.14's surface, which draws every lane in the
 * repository — and moved the two functions that turn an E9 row into these props down here beside
 * it (`laneSub`, `laneStageLabel`). They were the rail's private helpers, and a second copy of
 * either in the new surface would be a second place the six states drift, which is the failure
 * §4.11's own test exists to prevent.
 */
export function LaneCard({
  name, ticket, state, toneValue, elapsed, cost, alarm,
  sub, words, stage, pct, lane, role, selected = false, href, onRelease, remaining,
}: {
  name: string
  ticket: string
  state: string
  toneValue: string
  elapsed: ReactNode
  cost: ReactNode
  /**
   * E9's `remaining_s`, as a count-down clock — WEB-FIX-428, `factory.pen` `fTClA` / `z5wSV`. A node
   * for the reason `elapsed` is one: it moves with no frame arriving. Absent draws nothing, which
   * is what a lane with no measured estimate, and a done one, show.
   */
  remaining?: ReactNode
  alarm: boolean
  /**
   * E9's own `role` — WEB-FEAT-079. Drawn as a tag when it is not the tool's default, and the
   * ticket id stays beside it: an orphan role lane draws at top level with no parent above it to
   * read one off. The vocabulary sheet's six samples pass none, so the sheet is unchanged.
   */
  role?: string
  /** the optional second line: why it is queued, what vanished, what it is waiting to be asked */
  sub?: ReactNode
  /**
   * E9's `last_words.text` — what the agent last SAID or last CALLED (FAC-EPIC-373).
   *
   * Not folded into `sub`, and that is `WEB-FIX-277`'s rule: the sub-line answers *why is it not
   * working* and this answers *what is it doing*. Two facts, two readers, two elements. The
   * server's string, drawn verbatim — the design's `›` is a CSS `::before`, so what a screen
   * reader and a test read is what the server sent.
   */
  words?: string | null
  /** the stage name, or `4/8` when this surface has never read that ticket's sequence */
  stage?: string
  /** `stage.index / stage.total`, per E9 — the only progress number that is not a guess */
  pct?: number
  lane?: string
  selected?: boolean
  href?: string
  /**
   * E13 — WEB-FEAT-044. Absent on the vocabulary sheet's six, because those are samples and a
   * sample nobody can release is the point of a sample.
   */
  onRelease?(): void
}) {
  // A card is a LINK, so the one button on it has to stop the click reaching the anchor around it
  // — otherwise releasing a lane also navigates to the ticket whose lane just stopped existing.
  const release = (event: MouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    onRelease?.()
  }

  const card = (
    <div className="lane-card" data-testid="lane-card" data-lane={lane} data-ticket={ticket}
      data-role={role} data-alarm={String(alarm)} data-selected={String(selected)}
      style={tone(toneValue, tintFor(toneValue))}>
      <div className="lane-top">
        <span className="lane-dot" style={tone(toneValue)} />
        <span className="lane-name">{name}</span>
        <span style={{ flex: 1 }} />
        {role && role !== RUN && (
          <span className="lane-role" data-testid="lane-role">{role}</span>
        )}
        <span className="lane-ticket">{ticket}</span>
        {onRelease && (
          <button type="button" className="lane-release" data-testid="lane-release"
            title="Release lane — the branch is kept" aria-label={`release ${name}`}
            onClick={release}>
            <X size={10} />
          </button>
        )}
      </div>
      <div className="lane-state">{state}</div>
      {sub ? <div className="lane-sub" data-testid="lane-sub">{sub}</div> : null}
      {words ? <div className="lane-words" data-testid="lane-words">{words}</div> : null}
      {stage !== undefined && (
        <div className="lane-progress">
          <div className="lane-bar" data-testid="lane-bar" data-pct={String(pct ?? 0)}>
            <div className="lane-bar-fill" style={{ width: `${pct ?? 0}%` }} />
          </div>
          <span className="lane-stage" data-testid="lane-stage">{stage}</span>
        </div>
      )}
      <div className="lane-foot">
        <span data-testid="lane-elapsed">{elapsed}</span>
        {remaining ? <span data-testid="lane-remaining">{remaining}</span> : null}
        <span data-testid="lane-cost">{cost}</span>
      </div>
    </div>
  )

  // A lane is a ticket somebody is working on, so selecting one is going to that ticket. It is a
  // link and not a button: this ticket is reads-only, and the address bar is the state.
  return href ? <a className="lane-link" href={href}>{card}</a> : card
}

/** The tool's own default role (E12), and the only name this module compares against. */
const RUN = 'run'

/** One top-level lane and the role lanes drawn under it — WEB-FEAT-079, SPEC.md §4.4. */
export interface LaneBranch { lane: Lane; children: Lane[] }

/**
 * E9's flat list, in the shape §4.4 and §4.14 draw it: a `run` lane, and the roles under it.
 *
 * **The parent is not a guess.** The server refuses a second lane on the same `(ticket, role)` with
 * `409 lane_exists` and derives the branch name from that pair, so a ticket has at most one `run`
 * lane and the relation is already inside the `role` every row carries. That is why this ticket
 * added no field to E9 — a browser inferring a parent it was not told is exactly the second answer
 * §4.4's own note about per-ticket queries refuses to have.
 *
 * **`workspace` is part of the key, and §4.14 is why.** Progress is only comparable inside a
 * group (§4.3), so a role lane whose workspace differs from its ticket's `run` lane is nobody's
 * child: it draws at top level in the group it belongs to. One rule, both surfaces — the rail
 * draws no groups but names its stages off one sequence, and the comparison is just as wrong there.
 *
 * **An orphan draws.** A role lane whose `run` lane has been released (E13 takes one at a time)
 * keeps E9's own position at top level. Dropping it would hide a running check at the one moment
 * it is the only thing left to look at.
 */
export function nestByRole(lanes: Lane[]): LaneBranch[] {
  const isRun = (row: Lane) => !row.role || row.role === RUN
  // The join is a NUL, written as an ESCAPE and never as the byte itself: no id or workspace
  // name can contain one, so two different pairs cannot collide into one key — and a literal
  // NUL in a source file makes git call it binary and stop showing anybody the diff.
  const keyOf = (row: Lane) => `${row.ticket_id}\u0000${row.workspace}`

  const under = new Map<string, Lane[]>()
  for (const row of lanes) if (isRun(row) && !under.has(keyOf(row))) under.set(keyOf(row), [])

  // A second `run` lane on one key cannot exist while the server refuses one, and if it ever did
  // it would take an empty list rather than a second rendering of the first one's children.
  const taken = new Set<string>()
  const branches: LaneBranch[] = []
  for (const row of lanes) {
    const key = keyOf(row)
    if (isRun(row)) {
      const first = !taken.has(key)
      taken.add(key)
      branches.push({ lane: row, children: first ? under.get(key)! : [] })
      continue
    }
    const children = under.get(key)
    if (children) children.push(row)
    else branches.push({ lane: row, children: [] })
  }
  return branches
}

/**
 * The container the nesting is made of — WEB-FEAT-079.
 *
 * Both surfaces draw their own card props, so what is shared is the SHAPE and not the card: a
 * branch, and the lanes under it. A second copy of this markup in `LanesPage` would be a second
 * place the indentation drifts, which is the failure §4.11's own test exists to prevent.
 */
export function LaneBranchRow({ lane, card, children }: {
  lane: string
  card: ReactNode
  children: ReactNode
}) {
  return (
    <div className="lane-branch" data-testid="lane-branch" data-lane={lane}>
      {card}
      {children !== null && (
        <div className="lane-children" data-testid="lane-children" data-parent={lane}>
          {children}
        </div>
      )}
    </div>
  )
}

/**
 * The card's sub-line — SPEC.md §4.4 and §4.14, one function for both.
 *
 * Everything it can say is a fact off E9 (`laneSubLine`) except the silence counter, which is a
 * CLOCK: it counts up from `motion.since` with nothing arriving, so it is a component rather than
 * a string, and the design's own words go in front of it.
 */
export function laneSub(lane: Lane, live: boolean, resources: Resource[]): ReactNode {
  const own: ReactNode = lane.state?.motion?.value === 'silent' && lane.state.motion.since
    ? (
      <>
        no output{' '}
        <Clock testId="lane-silence" sinceMs={Date.parse(lane.state.motion.since)}
          running={live} format={formatSilence} />
      </>
    )
    // `waiting_for` names the resource and never says what it IS — that is E9's `resources` list,
    // and a queued card without it names a manifest key at somebody who wants to know what they
    // are waiting for.
    : laneSubLine(lane, (name) =>
      resources.find((row) => row.name === name)?.description ?? null) || null

  // WEB-FIX-277 — what is waiting on THIS lane, ADDED to whatever the lane says about itself and
  // never replacing it. A holder can be silent, or queued for a second resource, at the same time
  // as it is the reason somebody else is stuck, and the two facts have different readers.
  const blocking = laneBlockingLine(lane, resources)
  if (!blocking) return own
  return own ? <>{own} · {blocking}</> : blocking
}

/**
 * The stage's NAME when the caller holds a sequence it can belong to, and `4/8` when it does not.
 *
 * E9 carries `index` and `total` and no names, so a name can only come from a sequence this
 * surface has actually read: the rail passes the open ticket's own for the lanes running it and
 * the manifest's after that; §4.14's surface has read no ticket document at all and passes the
 * manifest's alone. Candidates are matched BY LENGTH and the first match wins.
 *
 * SPEC.md §4.3's rule is why it refuses to guess: progress is only comparable inside a sequence,
 * and `4/8` is never `2/4`. Indexing the manifest's sequence regardless would put a confident
 * wrong stage name on every card whose ticket declared its own.
 */
export function laneStageLabel(lane: Lane, ...candidates: (string[] | undefined)[]): string {
  const { index, total } = lane.stage
  const sequence = candidates.find((seq) => seq && seq.length === total)
  if (sequence && index >= 1 && index <= sequence.length) return sequence[index - 1]
  return `${index}/${total}`
}

/**
 * A lane's time left, or nothing — WEB-FIX-428. `null` is "not measured" and `0` is "done", and
 * neither is drawn: a dash or a `0m` would be a number the server did not send.
 */
export function laneTimeLeft(lane: Lane, sinceMs: number | null, running: boolean): ReactNode {
  if (!lane.remaining_s) return null
  return <Clock testId="lane-left-clock" baseS={lane.remaining_s} sinceMs={sinceMs}
    running={running} format={formatLeft} down />
}
