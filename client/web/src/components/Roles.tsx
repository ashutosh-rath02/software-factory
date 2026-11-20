import { useState } from 'react'

import type { Role } from '../types/api'

/**
 * SPEC.md §4.5 — the roles this ticket has started or queued, after the identity row.
 *
 * `SRV-FEAT-022` made the tool start a session by itself, and put a role waiting for a seat in
 * E5's `roles:[{…,"state":"pending"}]`. That is a queue nobody looks at, which is the failure
 * `022` exists to end arriving one layer up. This row is the half a person reads.
 *
 * **A pending role is a fact, not an alarm.** §3.3 is five lane clauses plus §3.5's two and this is
 * none of them, so nothing here writes `--tone` or `--tint` — the two custom properties every
 * alarm border and fill in this client is drawn from — and nothing here reads or writes the lane
 * set the `needs you` counter comes off. A counter that grows a clause per feature is one people
 * learn to ignore, and the queue this row makes visible is worth less than that.
 *
 * The design was drawn before F31 and has no sample for either kind of role, which §1 of the
 * ticket anticipates: the nearest existing vocabulary is `.tag` (`Dashboard.dc.html`) — the mono
 * hairline pill the header already uses for the workspace and for `display.ticket_fields` — and
 * that is what this reuses. No new colour, no new token.
 */
export function RolesRow({ roles }: { roles?: Role[] }) {
  const [picked, setPicked] = useState<string | null>(null)

  // Absent, not empty. `roles` is missing entirely from E5 whenever no declared role has been
  // reached — which is every ticket in this repository, whose own manifest declares none — and an
  // always-present empty container is how a header fills with things that never say anything.
  if (!roles || roles.length === 0) return null

  return (
    <div className="roles-row" data-testid="roles-row">
      <span className="roles-label">ROLES</span>
      {roles.map((role) => {
        const lane = role.state === 'started' ? role.lane : undefined
        const what = role.state === 'pending' ? 'waiting for a seat' : lane ?? role.state
        const text = `${role.role} · ${role.stage} · ${what}`

        // No lane, nothing to select — and a control that did nothing would teach the reader that
        // this row is broken. A queued role is a sentence, not a button.
        if (!lane) {
          return (
            <span key={`${role.role}·${role.stage}`} className="tag role" data-testid="role"
              data-role={role.role} data-state={role.state} data-alarm="false">{text}</span>
          )
        }

        return (
          <button key={`${role.role}·${role.stage}`} type="button" className="tag role"
            data-testid="role" data-role={role.role} data-state={role.state}
            data-lane={lane} data-alarm="false" aria-pressed={lane === picked}
            onClick={() => select(lane, setPicked)}>{text}</button>
        )
      })}
    </div>
  )
}

/**
 * Selecting a role's lane, from inside this row and nowhere else.
 *
 * A started role's lane runs the SAME ticket — that is what F31 does — so the rail's own meaning
 * of selection ("a lane is a ticket somebody is working on, so selecting one is going to that
 * ticket", `LaneCard`) has nowhere to navigate: the card is already on this screen and already
 * `data-selected`. So the click says which lane was picked and brings that card over. The rail is
 * read by selector rather than by prop because this ticket may not change it.
 */
function select(lane: string, setPicked: (lane: string) => void) {
  setPicked(lane)
  const card = document.querySelector(`[data-testid="lane-card"][data-lane="${lane}"]`)
  card?.scrollIntoView?.({ block: 'nearest' })
}
