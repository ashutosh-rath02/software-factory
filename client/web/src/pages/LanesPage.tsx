import { EmptyBlock, Placeholder, ReconnectBlock } from '../components/Blocks'
import { Clock } from '../components/Clock'
import { LaneBranchRow, LaneCard, laneStageLabel, laneSub, laneTimeLeft, nestByRole } from '../components/LaneCard'
import { useFactory } from '../services/factory'
import { useLanes } from '../services/lanes'
import { useSubscription } from '../services/subscribe'
import { formatCost, formatElapsed, laneStateLine, laneTone } from '../vocabulary'
import { href } from '../routes'
import type { Lane } from '../types/api'

/**
 * SPEC.md §4.14 — every lane in the repository (WEB-FEAT-050).
 *
 * This is what §4.4's rail stopped being. The rail used to list every lane, which put four other
 * tickets' work in a permanent column belonging to the one document a person is reading; the rail
 * is now about its own ticket and the whole answer lives here, where the whole answer is the point.
 *
 * **It is not a new data source.** No endpoint, no socket message, no second definition of any
 * state: §3.3's seven clauses are the server's, `needs_you` is E9's word, and the card is §4.4's
 * own `LaneCard` — the component the vocabulary sheet documents. Two drawings of a lane would be
 * two places the six states drift.
 *
 * **It never polls.** E9 once, then `M2`/`M3`/`M4`, which reach the `requirements` scope this
 * surface subscribes to (`laneStateScopes`, §6.2 M14) — which is exactly why a lane on a ticket
 * nobody has open is live here. Re-reading E9 on a timer is the cheap way to build a screen like
 * this one and it is the thing this architecture exists not to have.
 */
export function LanesPage() {
  const { socketState, manifest } = useFactory()
  const { lanes, anchors, leftAnchors, resources, loaded } = useLanes()
  useSubscription({ requirements: true })

  const live = socketState === 'open'
  const manifestStages = manifest?.stages ?? []
  // Not memoised, for the reason `LaneRail` is not: a memo here would be keyed on the identity of
  // a list this component did not create, and a provider hands down a value, not a promise about
  // its reference. Grouping a handful of rows costs nothing next to being quietly stale.
  const groups = groupByWorkspace(lanes, Object.keys(manifest?.workspaces ?? {}))

  // One lane's card, wherever it sits in its group's branches (WEB-FEAT-079).
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
      /* The manifest's sequence is the ONLY candidate: this surface has read no
         ticket document, so anything else would be a guessed stage name. §4.3 —
         4/8 is never compared against 2/4. */
      stage={laneStageLabel(lane, manifestStages)}
      pct={lane.progress_pct}
      elapsed={<Clock testId="lane-clock" baseS={lane.elapsed_s}
        sinceMs={anchors[lane.lane] ?? null} running={live} format={formatElapsed} />}
      remaining={laneTimeLeft(lane, leftAnchors[lane.lane] ?? null, live)}
      cost={formatCost(lane.cost_usd)}
      /* `needs_you` alone. The rail's second reason for the same treatment — this
         lane is the open ticket's — has no meaning here: nothing is open. */
      alarm={lane.needs_you}
      href={href({ name: 'ticket', id: lane.ticket_id })} />
  )

  return (
    <div className="lanes" data-testid="surface">
      {socketState === 'lost' && <ReconnectBlock />}

      <div className="lanes-inner">
        {!loaded ? <Placeholder what="reading the lanes…" />
          : groups.length === 0 ? (
            /* No `Start a lane` here, and that is the specification's: the action needs a TICKET
               to take it against, and this surface is the one screen in the product that is about
               no ticket in particular. §4.4's block and §4.7's composer are where it lives. */
            <EmptyBlock title="NO LANES" testId="lanes-empty">
              No lane has been claimed in this repository. Open a ticket and start one.
            </EmptyBlock>
          ) : groups.map((group) => (
            <div key={group.workspace} data-testid="lane-group">
              <div className="group-head">
                <span className="group-name" data-testid="group-name">{group.workspace}</span>
                <span className="group-rule" />
                <span className="group-count" data-testid="group-count">
                  {group.lanes.length} {group.lanes.length === 1 ? 'lane' : 'lanes'}
                </span>
              </div>
              <div className="lanes-grid">
                {/* WEB-FEAT-079 — §4.4's nesting, INSIDE this group and never across one. The
                    rule is keyed on the workspace as well as the ticket, so a role lane in
                    another group cannot be pulled out of the count that claims it. */}
                {nestByRole(group.lanes).map((branch) => (
                  <LaneBranchRow key={branch.lane.lane} lane={branch.lane.lane}
                    card={card(branch.lane)}>
                    {branch.children.length === 0 ? null : branch.children.map(card)}
                  </LaneBranchRow>
                ))}
              </div>
            </div>
          ))}
      </div>
    </div>
  )
}

export interface LaneGroup { workspace: string; lanes: Lane[] }

/**
 * SPEC.md §4.14's grouping, which is §4.3's grouping for §4.3's reason.
 *
 * *"Each workspace runs its own stage sequence, so progress is only comparable within a group."* A
 * lane card carries a progress bar and a stage, and a flat list of every lane in a repository is a
 * column in which `4/8` sits directly above `2/4` to be compared.
 *
 * Declared order first, because the manifest is where a repository states what it is made of and
 * that order is a human's. Anything the manifest does not declare comes after, in E9's own order —
 * the same shape §4.3 gives its final `unassigned` group. Lanes inside a group keep E9's order,
 * because the server's order is the only one that is not this client's opinion.
 */
export function groupByWorkspace(lanes: Lane[], declared: string[]): LaneGroup[] {
  const groups = new Map<string, Lane[]>()
  for (const lane of lanes) {
    const key = lane.workspace || 'unassigned'
    const held = groups.get(key)
    if (held) held.push(lane)
    else groups.set(key, [lane])
  }
  const rank = (workspace: string) => {
    const at = declared.indexOf(workspace)
    return at < 0 ? declared.length : at
  }
  return [...groups.entries()]
    .map(([workspace, rows]) => ({ workspace, lanes: rows }))
    .sort((a, b) => rank(a.workspace) - rank(b.workspace))
}
