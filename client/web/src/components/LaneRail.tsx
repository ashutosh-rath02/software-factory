import { useCallback, useState } from 'react'
import { CaretLeft, CaretRight } from '@phosphor-icons/react'

import { ClaimControl } from './ClaimControl'
import { EmptyBlock } from './Blocks'
import { Clock } from './Clock'
import { LaneBranchRow, LaneCard, laneStageLabel, laneSub, laneTimeLeft, nestByRole } from './LaneCard'
import { href } from '../routes'
import { useLanes } from '../services/lanes'
import { formatCost, formatElapsed, laneStateLine, laneTone, nothingRanYet } from '../vocabulary'
import type { Lane } from '../types/api'

const RAIL_KEY = 'factory.rail'

/**
 * SPEC.md §4.4 — the lane rail. 236 px, left, collapsible and remembered.
 *
 * Every card in it is `LaneCard`, the component the vocabulary sheet documents. The rail's own
 * job is the frame, the count, the order, and turning E9's numbers into the words and clocks the
 * card draws — and the six states the sheet draws are lane cards here too, because they are the
 * same six objects rendered by the same function.
 *
 * **WEB-FEAT-050 — it shows the lanes of the ticket this screen is about, and no others.** Until
 * then it listed every lane in the repository, which was §4.4's own deliberate reading; the
 * argument for changing it is in the specification and not repeated here. What matters at this
 * line is the thing that pays for it: §4.1's `N lanes need you` chip counts EVERY lane and links
 * to what it counted, so a lane needing a person on a ticket nobody has open is still one click
 * away — from anywhere, not only from here. Delete that chip and this filter becomes a way to
 * lose work, which is what `rail.test.tsx::a_lane_that_needs_you_on_another_ticket_is_still_
 * reachable` exists to say out loud.
 *
 * The filter is HERE, over the one shared lane set, and not an endpoint that answers "this
 * ticket's lanes": E9 is also the chip's data and §4.14's, and a second answer is a second number
 * that can disagree with the count this whole product exists to put in front of somebody.
 *
 * Collapsed or expanded is the ONE piece of UI state this ticket persists, and it never goes to
 * the server: which pane a person likes is not a fact about the repository.
 */
export function LaneRail({ lanes, anchors, loaded, ticketId, workspace, stages, manifestStages,
                           live, runCount, showClaim = true, selectedLane, tab }: {
  lanes: Lane[]
  anchors: Record<string, number>
  loaded: boolean
  /** the ticket this screen is about — its lane is the selected one */
  ticketId: string
  /**
   * The open ticket's own `workspace:`, which is what a claim from this rail is claimed IN
   * (WEB-FEAT-044 §1). The rail has never needed it before: reading lanes says which workspace
   * each one is in, and making one is the first thing that has to decide.
   */
  workspace: string
  /**
   * How many runs this ticket has, from the screen's one reading of E7 (`WEB-FIX-091`). The empty
   * block's sentence is a claim about runs and the rail has no other way to know (`WEB-FIX-088`).
   */
  runCount?: number
  /** SESSION owns this same no-lane action while its composer is visible. */
  showClaim?: boolean
  /** the open ticket's own declared sequence, which names the stage on ITS lane's card */
  stages: string[]
  /** the manifest's, which names it on any lane running the same-length sequence */
  manifestStages: string[]
  live: boolean
  /** the lane the screen is showing — the one card drawn selected */
  selectedLane?: string
  /** the open tab, kept when a card switches lanes */
  tab?: string
}) {
  const { resources, release, leftAnchors } = useLanes()

  // §4.4, and the whole of the filter.
  //
  // `filter` and not `find`: a ticket can hold more than one lane, because a role starts one of
  // its own (F31), and a rail reduced to "the one lane" would hide the second.
  //
  // NOT memoised, deliberately. A `useMemo` here would have to be keyed on the IDENTITY of an
  // array this component did not create, and identity is not something a list handed down from a
  // provider promises: `claim.test.tsx` mutates its E9 body in place, which left a memoised
  // `mine` empty while `lanes` held a row. Filtering a handful of rows on every render is cheaper
  // than the bug, and `LaneCard` is not memoised either, so the memo bought nothing.
  const mine = lanes.filter((row) => row.ticket_id === ticketId)

  // One lane's card, wherever it sits — a branch's parent and the lanes under it are the same
  // object drawn the same way, so the props are written once (WEB-FEAT-079).
  const card = (lane: Lane) => (
    <LaneCard key={lane.lane}
      lane={lane.lane}
      name={lane.lane}
      ticket={lane.ticket_id}
      role={lane.role}
      state={laneStateLine(lane.state)}
      toneValue={laneTone(lane.state)}
      sub={laneSub(lane, live, resources)}
      words={lane.last_words?.text ?? null}
      onRelease={() => { void confirmRelease(lane.lane, release) }}
      stage={laneStageLabel(lane, stages, manifestStages)}
      pct={lane.progress_pct}
      elapsed={<Clock testId="lane-clock" baseS={lane.elapsed_s}
        sinceMs={anchors[lane.lane] ?? null} running={live} format={formatElapsed} />}
      remaining={laneTimeLeft(lane, leftAnchors[lane.lane] ?? null, live)}
      cost={formatCost(lane.cost_usd)}
      alarm={lane.needs_you || lane.ticket_id === ticketId}
      selected={lane.lane === selectedLane}
      href={href({ name: 'ticket', id: lane.ticket_id, tab, lane: lane.lane })} />
  )

  const [open, setOpen] = useState(() => localStorage.getItem(RAIL_KEY) !== 'collapsed')
  const toggle = useCallback(() => {
    setOpen((was) => {
      localStorage.setItem(RAIL_KEY, was ? 'collapsed' : 'open')
      return !was
    })
  }, [])

  return (
    <div className="rail" data-testid="lane-rail" data-open={String(open)}>
      <div className="rail-head">
        {open && <span className="rail-title">LANES</span>}
        {open && <span className="rail-count" data-testid="lane-count">{loaded ? mine.length : ''}</span>}
        <button type="button" className="rail-toggle" data-testid="rail-toggle" onClick={toggle}
          aria-label={open ? 'collapse the lane rail' : 'expand the lane rail'}
          aria-expanded={open}>
          {open ? <CaretLeft size={11} /> : <CaretRight size={11} />}
        </button>
      </div>

      {open && (
        <div className="rail-cards">
          {/* WEB-FEAT-050 — `mine.length` and NOT `lanes.length`, which is the one thing a naive
              filter gets wrong. The block was chosen by the size of the GLOBAL set, so a ticket
              with no lane of its own would have shown somebody else's card and no way to start
              one, on exactly the screen where starting one is the next thing to do. The emptiness
              is a fact about this TICKET (§4.4), not about the repository. */}
          {loaded && mine.length === 0 && (
            <EmptyBlock title="NO LANE ASSIGNED" testId="rail-empty">
              {nothingRanYet(runCount)} Start a lane to begin.
              {/* WEB-FEAT-045 — one click: E12, then E18 in what it claimed. The chain is the
                  CONTROL's, not the rail's: the composer's seat has to do exactly the same thing,
                  and a rail that owned the sequence would be a second implementation of it. */}
              {showClaim && <ClaimControl ticketId={ticketId} workspace={workspace} />}
            </EmptyBlock>
          )}
          {/* WEB-FEAT-079 — the role lanes of this ticket are drawn UNDER its `run` lane. The
              rule is `nestByRole`, shared with §4.14, and the card is unchanged: what a lane is
              has one drawing, and where it sits is a container around it. */}
          {nestByRole(mine).map((branch) => (
            <LaneBranchRow key={branch.lane.lane} lane={branch.lane.lane} card={card(branch.lane)}>
              {branch.children.length === 0 ? null : branch.children.map(card)}
            </LaneBranchRow>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * E13, asked first — SPEC.md §4.4, WEB-FEAT-044.
 *
 * A `confirm()` and not a modal, which is §1's own pre-decision. Two questions, and the second one
 * only when the server has said the first answer is not enough: `force=true` kills a live process,
 * and a release that killed an agent because one dialog was clicked through would be the failure
 * this product is a reaction to. Both sentences say what SURVIVES, because that is the thing a
 * person is actually deciding about — E13 keeps the branch, always.
 */
async function confirmRelease(
  lane: string, release: (lane: string, force?: boolean) => Promise<{ ok: boolean; status: number }>,
) {
  if (!window.confirm('Release lane? The branch is kept.')) return
  const answer = await release(lane)
  if (answer.ok || answer.status !== 409) return
  if (!window.confirm('A process is still running. Force?')) return
  await release(lane, true)
}
