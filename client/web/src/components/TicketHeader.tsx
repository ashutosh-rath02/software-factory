import { useState } from 'react'

import { Clock } from './Clock'
import { AgentSelector, useAgentSelection } from './AgentSelector'
import { CommandButtons } from './CommandButtons'
import { DeliveryBadge } from './DeliveryBadge'
import { StateChip, WorkspaceTag } from './Chips'
import { RolesRow } from './Roles'
import { StageStrip } from './StageStrip'
import { useLanes } from '../services/lanes'
import { runTotals, type RunsState } from '../services/runs'
import { formatCost, formatElapsed, formatSilence } from '../vocabulary'
import type { Lane, Ticket } from '../types/api'

/**
 * SPEC.md §4.5 — the identity row, the three state chips, and the stage strip.
 *
 * Two sources, and keeping them apart is the whole of this component's judgement:
 *
 *   the DOCUMENT (E5) gives the id, the title, the workspace, the repository's own extra fields,
 *     the runs, the declared sequence and where the ticket says it is in it. None of that moves
 *     when the socket does, so the strip is untouched by a drop.
 *   the LANE (E9, then M2/M3/M4) gives the three axes. A lost socket dims those chips and keeps
 *     their last value, because a stale state that admits it is stale beats a blank one.
 *
 * A ticket with no lane reads its axes off the document, which is how §4.5's empty state comes
 * out as `PROCESS · NOT STARTED` / `MOTION · —` / `EVIDENCE · NOTHING CLAIMED` without a special
 * case for it.
 */
export function TicketHeader({ ticket, lane, live, runs: history }: {
  ticket: Ticket
  /** the lane running this ticket, when one is */
  lane?: Lane
  live: boolean
  /** the screen's E7, patched by `M11` — the rows §4.10's table draws */
  runs?: RunsState
}) {
  const axes = lane?.state
  const process = axes?.process ?? ticket.state?.process ?? ''
  const motion = axes ? axes.motion?.value : (ticket.state?.motion ?? null)
  const evidence = axes?.evidence ?? ticket.state?.evidence ?? ''
  const silentSince = axes?.motion?.value === 'silent' && axes.motion.since
    ? Date.parse(axes.motion.since) : null
  // A ticket nobody has run has no runs object at all on some servers; `0 runs · 0m 00s · $0.00`
  // is the honest reading of that, and a header that vanished would move the layout.
  //
  // The numbers themselves come from the RUNS rows once E7 has answered (`runTotals`, F27): the
  // document's count is a reading of the run directory taken when the page loaded, and it does
  // not move when a run starts, because starting a run does not edit a document. `WEB-FIX-091`
  // is what that cost — `0 runs` over a lane that was producing output.
  const runs = runTotals(ticket, history) ?? { count: 0, duration_s: 0, cost_usd: 0 }

  return (
    <div className="tkt-head" data-testid="ticket-header">
      <div className="tkt-identity" data-testid="ticket-identity">
        <span className="tkt-head-id">{ticket.id}</span>
        <span className="tkt-head-title">{ticket.title}</span>
        <WorkspaceTag name={ticket.workspace} />
        {/* The repository's own vocabulary on the screen without the tool knowing what any of
            it means: exactly `display.ticket_fields`, in declaration order, values untouched. */}
        {ticket.extras?.map((extra) => (
          // The document's `owner` is folded INTO the owner chip rather than drawn beside it.
          // `E5` can carry two claims — what git says and what the repository declares — and they
          // can disagree: the gate fixture has git saying `Gate` and the document `core team`.
          // Two chips reading `owner X` and `owner Y` tell a reader nothing about which is which,
          // and suppressing one hides a claim the repository made (WEB-FIX-328).
          extra.label.toLowerCase() === 'owner' && ticket.owner ? null : (
            <span className="tag" data-testid="ticket-extra" key={extra.label}>
              {extra.label} {extra.value}
            </span>
          )
        ))}
        {/* WEB-FEAT-325 — who wrote it. `source` is shown because `commits` (what git says) and
            a future `frontmatter` (what a person wrote down) are different claims, and it is
            printed verbatim: this client knows none of the repository's vocabulary. Nothing is
            drawn when `owner` is null — a chip claiming an absent fact is worse than no chip. */}
        {ticket.owner && (
          <span className="tag" data-testid="ticket-owner">
            owner {ticket.owner.name} · {ticket.owner.source}
            {declaredOwner(ticket) && (
              <span className="tag-second"> · declared {declaredOwner(ticket)}</span>
            )}
          </span>
        )}
        {lane && <span className="tkt-head-lane">{lane.lane}</span>}
        <StartQa ticket={ticket} />
        <span style={{ flex: 1 }} />
        <span className="tkt-head-metrics" data-testid="ticket-metrics">
          {runs.count} runs{' · '}
          <Clock testId="ticket-runs-elapsed" baseS={runs.duration_s}
            sinceMs={runs.liveSince ?? null} running={live} format={formatElapsed} />
          {' · '}{formatCost(runs.cost_usd)}
        </span>
      </div>

      {/* F31 — who else is on this ticket. Renders NOTHING when E5 carried no `roles` key, which
          is every ticket in a repository that declares none. */}
      <RolesRow roles={ticket.roles} />

      {/* F17 — three chips, in this order, and never merged into one status. The delivery badge
          is NOT a fourth axis and does not join them: the three read the LANE, it reads the disk,
          and it sits at the far end of the row for that reason.

          It belongs here because this is the surface a person opens to look at ONE ticket, and it
          was the only surface without it — `RequirementsPage` and `RequirementPage` both render it.
          `FEAT-040` is what that costs: the document says `todo`, six commits and a passing QA
          verdict sit in the lane's worktree, E5 answers `unshipped · claimed todo · disagrees`,
          and the page showed `NOT STARTED / NOTHING CLAIMED` with an unlit strip. The tool knew;
          the screen did not say. The badge renders nothing when there is nothing to report, so an
          ordinary ticket is unchanged. */}
      <div className="chips-row" data-testid="state-chips">
        <StateChip axis="process" value={process} title dim={!live} />
        <StateChip axis="motion" value={motion} title dim={!live}
          suffix={silentSince === null ? undefined : (
            <>
              {' '}
              <Clock testId="motion-silence" sinceMs={silentSince} running={live}
                format={formatSilence} />
            </>
          )} />
        <StateChip axis="evidence" value={evidence} title dim={!live} />
        <span style={{ flex: 1 }} />
        <DeliveryBadge delivery={ticket.delivery} />
      </div>

      <div className="strip-row">
        {/* §8 D2 — the strip stays linear. `index` is 1-based on the wire and the strip's
            `active` is an offset, so a ticket at stage 0 has every chip a ghost. */}
        <StageStrip stages={ticket.stages} active={ticket.stage.index - 1} />
        <span style={{ flex: 1 }} />
        <span className="stage-caption" data-testid="stage-caption">{captionFor(ticket, process === 'running')}</span>
      </div>

      {/* E19 — the workspace's own build and gate, under the strip and against the LANE, because a
          command needs a worktree. Renders NOTHING on a ticket nobody has claimed a lane for, and
          nothing at all in a repository whose manifest declares neither command. */}
      <CommandButtons lane={lane} />
    </div>
  )
}

/**
 * `stage 4/8 · core sequence`, and `stage 4/8 · round 2 of 4 · ticket sequence` when the active
 * stage has been entered more than once.
 *
 * This caption is the ONLY visual consequence of a round anywhere in the product (§8 D2). The
 * strip does not gain a chip, no branch is drawn, and the full sequence of transitions lives in
 * the transcript where the `stage` entries already are.
 */
function captionFor(ticket: Ticket, running: boolean): string {
  const { index, total, round, of, source } = ticket.stage
  // A live run that has reported NOTHING is not the same picture as a ticket nobody ever ran, and
  // both drew ten ghosts under the identical sentence — so a person watching a lane produce output
  // could not tell "it has not told me yet" from "nothing has ever happened here".
  //
  // The strip itself does not move: tinting a chip nobody reported would be claiming work, which
  // is the one thing E22's whole design refuses (`lane/progress.h`). The difference is said in
  // words instead, and the words name the reason rather than the symptom.
  if (index === 0 && running) return `running · no stage reported · ${total} in the ${source} sequence`
  const rounds = round > 1 ? ` · round ${round} of ${Math.max(of, round)}` : ''
  return `stage ${index}/${total}${rounds} · ${source} sequence`
}

/** The repository's own `owner` field, when its manifest declares one and it says something the
 *  derived owner does not. Equal readings are one fact and are printed once. */
function declaredOwner(ticket: { owner?: { name: string } | null;
                                 extras?: Array<{ label: string; value: string }> }) {
  const declared = ticket.extras?.find((e) => e.label.toLowerCase() === 'owner')?.value
  return declared && declared !== ticket.owner?.name ? declared : undefined
}

/**
 * WEB-FEAT-350 — start QA from the ticket, where its status already is.
 *
 * The server claims a QA lane by itself when a round REPORTS the QA stage, and that role fires
 * once per ticket. So a QA lane released by hand — or a round that reached `awaiting-qa` some
 * other way — left a person with no way back except a terminal, while the screen went on offering
 * `Release lane` as though it were reversible.
 *
 * Drawn only when the server would accept it: the ticket is at `awaiting-qa` and no `role: qa`
 * lane exists. An action whose answer is an error a person cannot act on is worse than no action.
 */
function StartQa({ ticket }: { ticket: Ticket }) {
  const { selection, valid } = useAgentSelection(ticket.id)
  const [starting, setStarting] = useState(false)
  const { lanes, claim, claiming, start } = useLanes()
  const already = lanes.some((row) => row.ticket_id === ticket.id && row.role === 'qa')
  if (ticket.status !== 'awaiting-qa' || already) return null

  const go = async () => {
    if (starting || !valid) return
    setStarting(true)
    try {
      const claimed = await claim(ticket.id, ticket.workspace, 'qa')
      // A claim that failed has already said why on the row; starting a run into nothing would
      // replace that reason with a second, less specific one.
      if (claimed?.ok && claimed.data) await start(claimed.data.lane, undefined, selection)
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="qa-start">
      <AgentSelector ticketId={ticket.id} disabled={claiming || starting} />
      <button type="button" className="tag tag-action" data-testid="start-qa"
              disabled={claiming || starting || !valid} onClick={go}
              title="Claim a QA lane for this ticket and start the verdict session in it">
        {claiming || starting ? 'Starting…' : 'Start QA'}
      </button>
    </div>
  )
}
