import { useCallback, useRef, useState } from 'react'

import { ClaimControl, startRefusal } from './ClaimControl'
import { Composer } from './Composer'
import { AgentSelector, useAgentSelection } from './AgentSelector'
import { EmptyBlock } from './Blocks'
import { ReplayedRun } from './ReplayedRun'
import { Transcript, TranscriptEntry as EntryLine } from './Transcript'
import { decidePermission, interruptRun, sendMessage } from '../services/http'
import { useFactory } from '../services/factory'
import { useLanes } from '../services/lanes'
import { hasLiveSession, isQueued, nothingRanYet } from '../vocabulary'
import { runTotals, useReplayedRun, type Replay } from '../services/runs'
import { useSubscription } from '../services/subscribe'
import { useTranscript, type Entry } from '../services/transcript'
import type { TabProps } from './tabs'
import '../styles/session.css'

/**
 * SPEC.md §4.6 and §4.7 — the SESSION tab: the transcript, and the composer under it.
 *
 * The half of the ticket screen a person actually works in, and the first client surface that
 * SENDS anything. Every send is an explicit click or an explicit `Enter`: nothing goes out on
 * mount, on focus, or on reconnect.
 *
 * `LiveSession` and `IdleSession` are separate components rather than branches inside this one so
 * that the hooks each state needs — the subscription, the transcript, the lane set on one side,
 * the replayed run on the other — are not conditional. `LiveSession` is also the seam that keeps
 * the composer's draft alive: both are declared at module level, so an arriving entry re-renders
 * them and never remounts them.
 */
export function SessionTab({ ticketId, ticket, lane, runs }: TabProps) {
  if (!lane) return <IdleSession ticketId={ticketId} ticket={ticket} runs={runs} />
  return <LiveSession ticketId={ticketId} lane={lane} />
}

/**
 * The run being read back — §4.10's second paragraph, drawn wherever this tab is asked for it.
 *
 * `WEB-FEAT-016` put this branch inside `LiveSession` only, so reading a finished run back
 * needed a lane to be assigned RIGHT NOW. A finished run is history and the lane set is a fact
 * about the present: a ticket the tool has already released the seat of — which is every ticket
 * whose work is on the base branch — answered a click on its own RUNS table with `NO LANE
 * ASSIGNED` (`WEB-FIX-354`). Both branches call it, and neither owns it.
 */
function Replayed({ replay }: { replay: Replay }) {
  return <ReplayedRun replay={replay}
    renderEntry={(entry) => <EntryLine entry={entry as unknown as Entry} />} />
}

/**
 * The tab with no lane of its own: a replayed run, or §4.7's way of starting one.
 *
 * A component rather than a branch, for the reason `LiveSession` is one — `useReplayedRun` is a
 * hook, and the tab cannot ask for the replay before it knows whether there is a lane without
 * asking for it conditionally.
 */
function IdleSession({ ticketId, ticket, runs }: Omit<TabProps, 'lane'>) {
  const replay = useReplayedRun(ticketId)
  if (replay) return <Replayed replay={replay} />

  // The ticket's own `workspace:` is what a claim from here is claimed in, and this tab already
  // holds the whole E5 answer — so §4.7's way out of its empty state costs no extra request.
  const doc = ticket?.result?.ok ? ticket.result.data : undefined
  const workspace = doc?.workspace
  // …and the screen's ONE reading of the run history, which `WEB-FIX-091` put in `TabProps`.
  // The block's sentence is a claim about runs and this is where the runs are known.
  const ran = runTotals(doc, runs)?.count
  return (
    <div className="session" data-testid="session">
      <div className="transcript" data-testid="transcript">
        <div className="transcript-column">
          <EmptyBlock title="NO LANE ASSIGNED" testId="session-empty">
            {nothingRanYet(ran)}
          </EmptyBlock>
        </div>
      </div>
      <Composer lane={null} producing={false} heldCount={0} sessionCost={0} live={false}
        onSend={() => {}} onInterrupt={() => {}}
        claim={<ClaimControl ticketId={ticketId} workspace={workspace ?? ''} />} />
    </div>
  )
}

/**
 * The last entry that says anything about the held queue.
 *
 * `held_count` is the server's number and the client never counts messages itself — but between
 * the `E14` that returned `held` and the frame that reports it, only the response knows. So the
 * wire wins whenever it has spoken since the last interrupt, and the response fills the gap.
 */
function marker(entries: Entry[]): Entry | undefined {
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const kind = entries[i].kind
    if (kind === 'held' || kind === 'turn' || kind === 'stopped'
        || kind === 'ended' || kind === 'killed') return entries[i]
  }
  return undefined
}

function LiveSession({ ticketId, lane }: { ticketId: string; lane: string }) {
  const { selection, valid } = useAgentSelection(ticketId)
  const { socketState } = useFactory()
  const { lanes, start } = useLanes()
  useSubscription({ lane })

  // ── the second seam this batch left unwired, the same shape as the permission one. WEB-FEAT-016
  //    built the RUNS tab, the replay store and the whole read-only pane, and left two lines for
  //    the SESSION tab it could not see. Unwired, selecting a run on RUNS does nothing at all.
  //    It returns EARLY and deliberately: the replay is the branch not taken, so there is no live
  //    stream to merge into it and no second composer to keep in sync.
  const replay = useReplayedRun(ticketId)

  const { entries, lastSeq } = useTranscript(lane)
  const [posted, setPosted] = useState(0)
  const [starting, setStarting] = useState(false)
  // The sequence an interrupt emptied the queue at. A `held` entry older than that has been
  // answered — it stays in the transcript, because the transcript is a record, and it stops
  // counting, because the queue it described is gone.
  const cleared = useRef(0)

  const row = lanes.find((held) => held.lane === lane)
  const live = socketState === 'open'

  // ── the seam WEB-FEAT-014 and WEB-FEAT-018 each left for the other. 014 drew the request and
  //    left `onDecide` a slot with its buttons disabled and saying so; 018 built E23 and left one
  //    line for integration. Neither branch could wire it, and unwired it ships three dead buttons
  //    in the one place a person is asked a question. It is passed only while the socket is open:
  //    the answer's effect arrives as M12, so a decision whose outcome cannot be observed is worse
  //    than one that was never offered.
  const decide = useCallback((requestId: string, decision: 'once' | 'always' | 'deny') => {
    void decidePermission(requestId, decision)
  }, [])

  // The last sequence held, read at the moment an interrupt lands rather than at the moment its
  // handler was built: `Send again` awaits a message before interrupting, so entries can arrive
  // in between and a closure would clear the wrong point in the run.
  const seq = useRef(lastSeq)
  seq.current = lastSeq

  /**
   * The first Send into a lane with no live session STARTS ONE — E18, WEB-FEAT-045.
   *
   * Which verb this is is decided from §3.2's process axis (`hasLiveSession`) and from nothing else.
   * Before this ticket every Send was E14, and E14 into a lane WEB-FEAT-044 had just claimed is a
   * `409 wrong_state`: the one action that turns a claimed lane into a working one had no control
   * anywhere in the client. The typed text becomes the run's first prompt, and an empty box starts
   * with no prompt at all, so the agent begins on the ticket's own content (§1's pre-decision).
   */
  const onSend = useCallback(async (text: string) => {
    if (!hasLiveSession(row)) {
      if (!valid) return 'Select an available agent and model before starting.'
      setStarting(true)
      try {
        const answer = await start(lane, text || undefined, selection)
        return answer.ok ? true : startRefusal(answer)
      } finally {
        setStarting(false)
      }
    }
    const answer = await sendMessage(lane, text)
    if (!answer.ok) return false
    setPosted(answer.data.status === 'held' ? answer.data.held_count : 0)
    return true
  }, [lane, row, start, selection, valid])

  const onInterrupt = useCallback(() => {
    void interruptRun(lane).then((answer) => {
      if (!answer.ok || !answer.data.interrupted) return
      cleared.current = seq.current
      setPosted(0)
    })
  }, [lane])

  // The two halves of this batch typed one concept twice: WEB-FEAT-016's replay entries come from
  // E8 over HTTP and are `TranscriptEntry`; WEB-FEAT-014's live ones come from M5 over the socket
  // and are `Entry`. Both are SPEC.md §4.6's table and the server builds both from the same parser,
  // so widening here is safe — `Entry` carries an index signature and the E8 shape is a subset. One
  // type for one concept is the right answer and it is a change across both, not an integration
  // edit made past two green gates.
  //
  // It stands BELOW every hook this component takes, and that is not tidiness. It used to stand
  // above three of them, so the render that entered a replay took ten hooks and the one that left
  // it took thirteen — `Rendered more hooks than during the previous render`, which unmounts the
  // tab. Nothing had ever crossed it: the suite mounted a stand-in for this component and the
  // browser gate could not reach a replay at all (`WEB-FIX-354`).
  if (replay) return <Replayed replay={replay} />

  const boundary = marker(entries)
  const fromWire = boundary && boundary.kind === 'held' && boundary.seq > cleared.current
    ? Number(boundary.held_count ?? 0) : 0
  const heldCount = Math.max(fromWire, posted)

  return (
    <div className="session" data-testid="session">
      {entries.length === 0
        ? (
          <div className="transcript" data-testid="transcript">
            <div className="transcript-column">
              <EmptyBlock title="NO OUTPUT YET" testId="session-empty">
                Nothing has run in <code>{lane}</code> yet.
              </EmptyBlock>
            </div>
          </div>
        )
        : <Transcript entries={entries} onDecide={live ? decide : undefined} />}

      {/* WEB-FIX-061 QA — a QUEUED lane has no live session and Send does NOT start a run in it:
          E18 answers `409 resource_busy` while the lane waits its turn for an exclusive resource.
          Reading `hasLiveSession` alone told the person the opposite, which is `isQueued`'s own
          doc comment being disregarded one file away. A lane doing exactly what it should must
          not be made to look broken. */}
      {!hasLiveSession(row) && <AgentSelector ticketId={ticketId} disabled={!live || starting} />}
      <Composer lane={lane} heldCount={heldCount}
        startsARun={!hasLiveSession(row) && !isQueued(row)}
        producing={row?.state?.process === 'running' && row.state.motion?.value === 'producing'}
        sessionCost={row?.cost_usd ?? 0} live={live} onSend={onSend} onInterrupt={onInterrupt} />
    </div>
  )
}
