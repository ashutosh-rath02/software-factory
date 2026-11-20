import { useCallback, useState } from 'react'

import { tone } from './Chips'
import { AgentSelector, useAgentSelection } from './AgentSelector'
import { GitRefusal } from './GitRefusal'
import { useFactory } from '../services/factory'
import { useLanes, type StartOutcome } from '../services/lanes'
import { href } from '../routes'
import { DISCARD_IGNORED_DESTROYS, TONE, destroysLine, isQueued } from '../vocabulary'
import type { Fetched } from '../services/http'
import type { ErrorBody, Lane } from '../types/api'

/**
 * WEB-FEAT-044 — `Start a lane`. ONE control, two seats.
 *
 * §4.4's `NO LANE ASSIGNED` block and §4.7's `Start a lane to send a message.` are the same dead
 * end said twice, and this is the same way out of it said once: a second component beside this one
 * would be two dialogs, two error vocabularies and two chances for one of them to stop matching
 * what E12 actually answers.
 *
 * The words are the SERVER's wherever the server has any. `409 no_seat` already says "release a
 * lane or raise lanes.host" — a manifest key a person can go and change — and repeating that here
 * in this client's own phrasing would be a second copy of a sentence that is allowed to move.
 * What is added is the pair of numbers behind it, because "every seat is taken" is a claim a
 * reader will want to check.
 *
 * There is no `409 resource_busy` case. SPEC.md E12 is explicit: a held exclusive resource creates
 * the lane `queued` with `waiting_for` set, and it comes back `201` like any other. Drawing an
 * error for it would be drawing a refusal the server never sends — the queued CARD is the answer,
 * and `LaneRail` renders it.
 */

/** What to tell a person when E12 said no. Empty when it said yes. */
export function claimRefusal(answer: Fetched<Lane>): string {
  if (answer.ok) return ''
  const detail = (answer.error?.detail ?? {}) as Record<string, unknown>
  const said = answer.error?.message
    ?? `the lane could not be claimed (HTTP ${answer.status || 'no answer'})`
  if (answer.error?.code === 'no_seat'
      && typeof detail.used === 'number' && typeof detail.total === 'number') {
    return `${said} — ${detail.used} of ${detail.total} seats used`
  }
  return said
}

/**
 * SRV-FIX-059 QA, finding 2 — the leftover this claim tripped over, when THIS screen can clear it.
 *
 * The server's answer already carried the whole next step and no screen read it: `claimRefusal`
 * renders `error.message` alone, so a person met "something is already at the path this lane would
 * use" and nothing else, while `detail.clear_with` sat in the same body naming the call that fixes
 * it. The way out existed only for somebody holding `curl` — which is not the sentence the ticket
 * set out to make true ("a person with a browser and no terminal must be able to get out").
 *
 * `ours` is the gate and not a decoration. A directory this tool did not create is refused by E13
 * with `not_ours`, so offering the button for it would be offering an action that cannot work.
 * Returns null whenever the refusal is anything else — this adds a control to ONE case.
 */
export function leftoverToClear(answer: Fetched<Lane>):
    { lane: string, path: string, ignored: string[] } | null {
  if (answer.ok || answer.error?.code !== 'worktree_in_the_way') return null
  const detail = (answer.error?.detail ?? {}) as Record<string, unknown>
  if (detail.ours !== true) return null
  if (typeof detail.lane !== 'string' || !detail.lane) return null
  // SRV-FIX-059 round 5 — what will ACTUALLY block the clear, by name. A lane that ran its build
  // once holds paths git was told to ignore; round 4 made the clear refuse on them rather than
  // delete them silently, and a screen that did not carry the names offered a button that then
  // said no. `ignored_files: 3` is the answer that sent a person here in the first place.
  const ignored = Array.isArray(detail.ignored)
    ? detail.ignored.filter((p): p is string => typeof p === 'string')
    : []
  return { lane: detail.lane, path: typeof detail.path === 'string' ? detail.path : '', ignored }
}

/**
 * What to tell a person when E18 said no — WEB-FEAT-045. Empty when it said yes.
 *
 * It lives beside `claimRefusal` because the two are the same job: E12 and E13 and E18 are the three
 * writes that change what a lane IS, and one place for their words is one place to keep true to what
 * the server actually answers.
 *
 * Both `409`s are named rather than passed through, and for opposite reasons. `wrong_state` needs the
 * RUN, which the refusal itself does not carry — see `LaneSet.start`. `resource_busy` is not a
 * mistake at all: the lane is queued and behaving correctly, so the sentence says what it is waiting
 * for and does not read like a failure.
 */
export function startRefusal(answer: StartOutcome): string {
  if (answer.ok) return ''
  const detail = (answer.error?.detail ?? {}) as Record<string, unknown>
  const said = answer.error?.message
    ?? `the run could not be started (HTTP ${answer.status || 'no answer'})`
  if (answer.error?.code === 'wrong_state' && answer.heldByRun) {
    return `${answer.heldByRun} is already running in this lane — nothing was started, and your `
         + 'message is still here.'
  }
  if (answer.error?.code === 'resource_busy' && typeof detail.resource === 'string') {
    const held = typeof detail.held_by === 'string' ? `, held by ${detail.held_by}` : ''
    const place = typeof detail.position === 'number' ? ` at #${detail.position}` : ''
    return `this lane is waiting for ${detail.resource}${held}${place} — nothing was started, and `
         + 'your message is still here.'
  }
  // WEB-FEAT-179 — WHICH branch and WHICH path. E18's sentence ends "commit it and start again",
  // a shell instruction printed by a tool, and the two facts that would make it followable sit in
  // the same body: the branch the lane is on, and where the document would have to be. Both are
  // `detail`'s own strings; nothing here is composed from the ticket id or the worktree root, which
  // is how SRV-FIX-055 put a path in front of an agent that resolved to nothing.
  if (answer.error?.code === 'ticket_not_on_branch') {
    const branch = typeof detail.branch === 'string' ? detail.branch : ''
    const path = typeof detail.path === 'string' ? detail.path : ''
    if (branch || path) {
      return `${said}${branch ? ` — the lane is on ${branch}` : ''}`
           + `${path ? `, and the agent looked for ${path}` : ''}`
    }
  }
  return said
}

/**
 * The one destructive control in this family, and the confirmation in front of it — WEB-FEAT-179.
 *
 * §1: "the tool offers the way to lose the files and not the way to keep them". Before this ticket
 * `discard_ignored` was the ONLY control any git refusal ever had, and one click fired it. It stays
 * — a person who has read the list is allowed to decide — behind a step that names what is lost in
 * SPEC.md §9.3's own nouns, so the click that reveals the cost is never the click that pays it.
 *
 * It is not a remedy off the wire: E13 names the call in `detail.clear_with` and `SRV-FIX-059`
 * built this button for it. It says what it costs in the same words a remedy would, out of the same
 * table, because a person cannot be expected to know which of two controls came from where.
 */
function DiscardIgnored({ paths, disabled, busy, onFire }: {
  paths: string[]
  disabled: boolean
  busy: boolean
  onFire: () => void
}) {
  const [asked, setAsked] = useState(false)
  const destroys = DISCARD_IGNORED_DESTROYS.join(' ')

  if (!asked) {
    return (
      <button type="button" className="empty-action" data-testid="claim-discard-ignored"
        data-destroys={destroys} disabled={disabled} style={tone(TONE.crash)}
        onClick={() => setAsked(true)}>
        {`Discard ${paths.length} ignored path${paths.length === 1 ? '' : 's'} and start`}
      </button>
    )
  }

  return (
    <div className="claim-discard" data-testid="claim-discard-confirm" style={tone(TONE.crash)}>
      <div className="claim-refused" style={tone(TONE.crash)}>
        {destroysLine(DISCARD_IGNORED_DESTROYS)}
        <ul>{paths.map((p) => <li key={p}><code>{p}</code></li>)}</ul>
      </div>
      <button type="button" className="empty-action" data-testid="claim-discard-fire"
        data-destroys={destroys} disabled={disabled} style={tone(TONE.crash)}
        onClick={onFire}>
        {busy ? 'Discarding…' : `Discard ${paths.length === 1 ? 'it' : 'them'} and start`}
      </button>
    </div>
  )
}

/** Every path `detail.ignored` names, and nothing the client invented. */
function ignoredIn(error: ErrorBody['error'] | null): string[] {
  const detail = (error?.detail ?? {}) as Record<string, unknown>
  return Array.isArray(detail.ignored)
    ? detail.ignored.filter((p): p is string => typeof p === 'string')
    : []
}

export function ClaimControl({ ticketId, workspace }: {
  ticketId: string
  /** the ticket's OWN `workspace:` — §1's pre-decision, and there is no chooser in v1 */
  workspace: string
}) {
  const { selection, valid } = useAgentSelection(ticketId)
  const [starting, setStarting] = useState(false)
  const { claim, claiming, start, release } = useLanes()
  const { socketState } = useFactory()
  const [refused, setRefused] = useState('')
  /**
   * E13's own refusal, as a BODY — WEB-FEAT-179.
   *
   * `uncommitted_changes`, `ignored_work` and `unmerged_commits` each carry the fact that decides
   * what a person does next, and this path kept `error.message` and dropped the rest. A count, a
   * list of paths and a branch name are not decoration on those three sentences; they are the
   * sentences' subject.
   */
  const [refusalBody, setRefusalBody] = useState<ErrorBody['error'] | null>(null)
  const [leftover, setLeftover] =
    useState<{ lane: string, path: string, ignored: string[] } | null>(null)
  const [clearing, setClearing] = useState(false)
  // SPEC.md §4.4's own socket-down rule, in its own words: "cards keep their last state, clocks
  // stop, `Start a lane` is disabled". A claim whose outcome cannot be observed leaves a worktree
  // and a seat allocated to a screen that will not be told about either.
  const live = socketState === 'open'

  /**
   * ONE CLICK: E12, then E18 — §1's pre-decision, so nobody meets a claimed-but-empty lane.
   *
   * Unless the lane came back QUEUED, which is the exception §1 names: E18 would answer
   * `409 resource_busy`, and putting that in front of a person for a lane that is doing exactly
   * what it should — waiting its turn for the board — would make the correct case look broken.
   */
  const startLane = useCallback(async () => {
    if (!valid || starting) return
    setStarting(true)
    try {
    setRefused('')
    setRefusalBody(null)
    setLeftover(null)
    const answer = await claim(ticketId, workspace)
    // `null` is the guard: a claim was already in flight, so this click asked for nothing and has
    // nothing to report. Saying so would be reporting a failure that did not happen.
    if (!answer) return
    if (!answer.ok) {
      setRefused(claimRefusal(answer))
      setLeftover(leftoverToClear(answer))
      return
    }
    if (isQueued(answer.data)) return
    const run = await start(answer.data.lane, undefined, selection)
    if (!run.ok) setRefused(startRefusal(run))
    } finally { setStarting(false) }
  }, [claim, start, ticketId, workspace, selection, valid, starting])

  /**
   * E13, then E12 again — the way out of `worktree_in_the_way`, on the screen that hit it.
   *
   * It does NOT retry the claim when E13 refused. E13's whole job here is to refuse a leftover
   * holding work — an uncommitted change, or a commit no other branch carries — and a retry after
   * that refusal would claim nothing and replace the sentence explaining why with a second copy of
   * the sentence that sent the person here. The refusal is the answer, so it is what is shown.
   */
  const clearLeftover = useCallback(async (discardIgnored = false) => {
    if (!leftover) return
    setClearing(true)
    try {
      // `discardIgnored` arrives only from the control that listed the paths. The ordinary way out
      // never sends it — a click on a leftover with nothing to list would otherwise be answering a
      // question nobody was asked (§1 round 5, "consent is the only door").
      const gone = await release(leftover.lane, !discardIgnored, discardIgnored)
      if (!gone.ok) {
        // The BODY, not a sentence taken out of it. E13's three git refusals are the whole reason
        // `GitRefusal` exists, and a `message` extracted here would drop the count, the paths and
        // the branch on the way to the screen — which is exactly what this ticket is about.
        if (gone.error) setRefusalBody(gone.error)
        else {
          setRefused(`the leftover could not be cleared (HTTP ${gone.status || 'no answer'})`)
        }
        setLeftover(null)
        return
      }
      setRefusalBody(null)
      setLeftover(null)
      await startLane()
    } finally {
      setClearing(false)
    }
  }, [leftover, release, startLane])

  /**
   * The same discard, from E13's OWN refusal rather than from E12's.
   *
   * `clearLeftover` needs `leftover`, which is `worktree_in_the_way`'s detail; once E13 has
   * refused, that state is gone and the lane's name is in the refusal instead. One call either
   * way, so the person who reads the list gets the same button wherever the list came from.
   */
  const discardFromRefusal = useCallback(async () => {
    const detail = (refusalBody?.detail ?? {}) as Record<string, unknown>
    const lane = typeof detail.lane === 'string' ? detail.lane : ''
    if (!lane) return
    setClearing(true)
    try {
      const gone = await release(lane, false, true)
      if (!gone.ok) {
        if (gone.error) setRefusalBody(gone.error)
        return
      }
      setRefusalBody(null)
      await startLane()
    } finally {
      setClearing(false)
    }
  }, [refusalBody, release, startLane])

  // A ticket with no `workspace:` names no worktree to open, which is exactly the shape this
  // repository shipped until SRV-FIX-021. The control says why rather than 400ing on a click.
  if (!workspace) {
    return (
      <div className="claim" data-testid="claim">
        <div className="claim-refused" data-testid="claim-error" style={tone(TONE.silent)}>
          this ticket declares no <code>workspace:</code>, so there is no worktree to open a lane in
        </div>
      </div>
    )
  }

  return (
    <div className="claim" data-testid="claim">
      <AgentSelector ticketId={ticketId} disabled={claiming || starting || !live} />
      <button type="button" className="empty-action" data-testid="claim-lane"
        disabled={claiming || starting || !live || !valid} onClick={() => { void startLane() }}>
        {claiming || starting ? 'Starting…' : 'Start a lane'}
      </button>
      {!live && (
        <div className="claim-refused" data-testid="claim-offline" style={tone(TONE.silent)}>
          the socket is down — a claim you could not watch is worse than none
        </div>
      )}
      {refused && (
        <div className="claim-refused" data-testid="claim-error" style={tone(TONE.crash)}>
          {refused}
        </div>
      )}

      {/* E13's git refusals, with their facts and the server's own way out. The remedy §11 check
          12 gives all three is `open_lane → []` — the way that KEEPS the work — and `open_lane` is
          an address, resolved here because this is the component that knows which ticket the lane
          belongs to. The destructive way sits under it, behind its confirmation. */}
      {refusalBody && (
        <GitRefusal error={refusalBody} onRetry={() => { void startLane() }}
          addressFor={() => href({ name: 'ticket', id: ticketId, tab: 'diff' })}>
          {ignoredIn(refusalBody).length > 0 && (
            <DiscardIgnored paths={ignoredIn(refusalBody)} disabled={clearing || !live}
              busy={clearing} onFire={() => { void discardFromRefusal() }} />
          )}
        </GitRefusal>
      )}
      {leftover && (
        <div className="claim-leftover" data-testid="claim-leftover">
          {/* Two doors, and never one. A leftover with nothing ignored in it clears with the
              ordinary call; one that holds paths git was told to ignore is a decision, and the
              decision is made from the NAMES rather than from a count. Round 4 made this refusal
              honest; round 5 makes it actionable without making it automatic. */}
          {leftover.ignored.length === 0 ? (
            <button type="button" className="empty-action" data-testid="claim-clear-leftover"
              disabled={clearing || !live} onClick={() => { void clearLeftover() }}>
              {clearing ? 'Clearing…' : 'Clear the leftover checkout and start'}
            </button>
          ) : (
            <>
              <div className="claim-refused" data-testid="claim-leftover-ignored"
                style={tone(TONE.crash)}>
                this checkout holds files git was told to ignore — clearing it loses them:
                <ul>
                  {leftover.ignored.map((p) => <li key={p}><code>{p}</code></li>)}
                </ul>
              </div>
              {/* WEB-FEAT-179 — the same control, and it no longer fires on the click that
                  reveals what it costs. */}
              <DiscardIgnored paths={leftover.ignored} disabled={clearing || !live}
                busy={clearing} onFire={() => { void clearLeftover(true) }} />
            </>
          )}
          {leftover.path && (
            <div className="claim-refused" data-testid="claim-leftover-path"
              style={tone(TONE.silent)}>
              <code>{leftover.path}</code> — kept if it holds uncommitted work, or commits no other
              branch has
            </div>
          )}
        </div>
      )}
    </div>
  )
}
