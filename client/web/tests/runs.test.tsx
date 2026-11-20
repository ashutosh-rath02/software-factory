import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, within } from '@testing-library/react'
import { useEffect, useState } from 'react'

import { mountApp, settle, ticketDoc, ticketRoutes, lane as laneRow, callsTo, calls } from './helpers'
import { TABS, labelOf, type TabProps } from '../src/components/tabs'
import { ReplayedRun } from '../src/components/ReplayedRun'
import { useReplayedRun, clearReplay } from '../src/services/runs'
import { useFactory } from '../src/services/factory'
import { deliver as deliverFrames } from './session'
import { TONE } from '../src/vocabulary'

/**
 * 4.10 RUNS — reading a finished run back.
 *
 * Every case mounts the whole app at `#/tickets/TKT-142/runs`, because a tab rendered outside the
 * shell is a tab whose loading, empty and socket-down rules were never run.
 */

/**
 * The registry's OWN SESSION row, taken before any case replaces it.
 *
 * `WEB-FIX-354` — every case in this file used to swap `TABS.session` for the seam below and put
 * it back with `delete`, so the real `SessionTab` was never mounted here and the banner criterion
 * was asserted against a stand-in that draws it unconditionally. The product's early return on a
 * ticket with no lane was therefore unreachable from this suite while it was on screen in the
 * browser gate. Restoring the row rather than deleting it also keeps SESSION first in
 * `tabIds()`, which is where the registry declares it.
 */
const REAL_SESSION = TABS.session

/** One E7 row, in the shape `app/agent/history.cpp::renderRow` actually serves. */
function run(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run-1', n: 1, started_iso: '2026-07-28T11:20:44Z', ended_iso: '2026-07-28T11:22:00Z',
    outcome: 'completed', stage: 0, duration_s: 76, cost_usd: 0.62,
    lane: 'lane/01', session_id: 'sess-1',
    ...over,
  }
}

/**
 * The six outcomes §4.10 names, newest first — and they are six from FIVE wire words, because
 * `completed` is the one that splits on §3.4's evidence: the report cites `run-2` and nothing else.
 */
const SIX = [
  run({ run_id: 'run-6', n: 6, outcome: 'running', ended_iso: null, stage: 4,
        started_iso: '2026-07-28T14:02:11Z', duration_s: 724, cost_usd: 0.74 }),
  run({ run_id: 'run-5', n: 5, outcome: 'ended cleanly', stage: 2,
        started_iso: '2026-07-28T13:31:50Z', duration_s: 258, cost_usd: 0.39 }),
  run({ run_id: 'run-4', n: 4, outcome: 'crashed', stage: 1,
        started_iso: '2026-07-28T11:58:02Z', duration_s: 62, cost_usd: 0.11 }),
  run({ run_id: 'run-3', n: 3, outcome: 'killed by you', stage: 3,
        started_iso: '2026-07-28T11:40:00Z', duration_s: 41, cost_usd: 0.86 }),
  run({ run_id: 'run-2', n: 2, outcome: 'completed', stage: 7,
        started_iso: '2026-07-28T11:30:00Z', duration_s: 300, cost_usd: 1.10 }),
  run({ run_id: 'run-1', n: 1, outcome: 'completed', stage: 0,
        started_iso: '2026-07-28T11:20:44Z', duration_s: 76, cost_usd: 0.62 }),
]

const REPORT = {
  ticket_id: 'TKT-142', path: '/tmp/fixture/docs/reports/TKT-142-report.md',
  body_markdown: '# it is done\n', evidence: { verdict: 'claimed_proven', run_id: 'run-2' },
}

function entry(over: Record<string, unknown> = {}) {
  return { seq: 1, ts: '2026-07-28T11:40:01Z', kind: 'prose', text: 'first', ...over }
}

/**
 * What `WEB-FEAT-014`'s SESSION tab does, in the two lines the seam asks of it.
 *
 * This is not a mock of the tab: it is the integration, written here because `014` is being built
 * in another worktree at the same time. The live column stands in for its transcript — it collects
 * `M5` off the same `onFrame` the real one will — so the negative case below is asking the real
 * question: does a replay leak into the live stream, or the live stream into a replay?
 */
function SessionSeam(_props: TabProps) {
  const replay = useReplayedRun()
  const live = useLiveEntries()

  if (replay) {
    return (
      <ReplayedRun replay={replay}
        renderEntry={(e) => (
          <div data-testid="replay-entry">{String((e as { text?: string }).text ?? e.kind)}</div>
        )} />
    )
  }
  return (
    <div data-testid="live-column">
      {live.map((e, i) => <div key={i} data-testid="live-entry">{e}</div>)}
    </div>
  )
}

function useLiveEntries(): string[] {
  const { onFrame } = useFactory()
  const [held, setHeld] = useState<string[]>([])
  useEffect(() => onFrame((frame) => {
    if (frame.m === 'transcript') {
      const text = (frame.entry as { text?: string }).text ?? ''
      setHeld((current) => [...current, text])
    }
  }), [onFrame])
  return held
}

/** jsdom queues `hashchange` as a task; the app's router listens for it, so let it land. */
async function arrive() {
  await act(async () => { vi.advanceTimersByTime(1) })
  await settle()
  await settle()
}

function transcriptFrame(seq: number, text: string) {
  return {
    m: 'transcript', ts: '2026-07-28T14:03:00Z', seq, lane: 'lane/01', run_id: 'run-6',
    entry: { seq, ts: '2026-07-28T14:03:00Z', kind: 'prose', text },
  }
}

/**
 * `rows` is E9's lane set, and the ticket document's own `lane:` follows it — the two are one
 * fact and a fixture in which they disagree proves only which of them the screen happened to
 * read (`TicketPage.tsx`: `row?.lane ?? doc?.lane ?? null`). An empty set is a ticket nothing is
 * running in, which is where `WEB-FIX-354` lives.
 */
function routesFor(runs: unknown[], over: Record<string, unknown> = {},
                   rows: Array<Record<string, unknown>> =
                     [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142' })]) {
  const doc = ticketDoc({ lane: rows.length > 0 ? String(rows[0].lane) : null,
                          runs: { count: runs.length, duration_s: 1461, cost_usd: 3.82 } })
  return {
    ...ticketRoutes(doc, rows),
    '/api/tickets/TKT-142/report': { body: REPORT },
    '/api/tickets/TKT-142/runs': { body: { runs } },
    ...over,
  }
}

beforeEach(() => {
  clearReplay()
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T14:10:00Z'))
  TABS.session = { label: 'SESSION', component: SessionSeam }
})
afterEach(() => { cleanup(); clearReplay(); TABS.session = REAL_SESSION; vi.useRealTimers() })

describe('4.10 · the RUNS tab', () => {
  it('rows_newest_first_with_their_outcomes', async () => {
    await mountApp('#/tickets/TKT-142/runs', routesFor(SIX))
    await settle()
    await settle()

    const rows = screen.getAllByTestId('run-row')
    expect(rows).toHaveLength(6)
    expect(rows.map((r) => within(r).getByTestId('run-n').textContent)).toEqual(
      ['#6', '#5', '#4', '#3', '#2', '#1'])

    // The six words of §4.10, and NOT the five the wire carries: `completed` is two answers.
    expect(rows.map((r) => within(r).getByTestId('run-outcome').textContent)).toEqual([
      'running', 'ended cleanly', 'crashed mid-turn', 'killed by you',
      'finished · proof', 'finished · no proof',
    ])

    // each in its state colour, off the SAME constant table the vocabulary sheet reads
    expect(rows.map((r) => within(r).getByTestId('run-outcome').style.getPropertyValue('--tone')))
      .toEqual([TONE.run, TONE.done, TONE.crash, TONE.crash, TONE.run, TONE.claim])

    // STARTED · STAGE · DUR · COST — the design's own columns (Ticket.dc.html)
    const second = rows[1]
    expect(within(second).getByTestId('run-stage')).toHaveTextContent('IMPL')
    expect(within(second).getByTestId('run-dur')).toHaveTextContent('4m 18s')
    expect(within(second).getByTestId('run-cost')).toHaveTextContent('$0.39')

    // and the tab says how many without the page computing it (WEB-FEAT-013's label function)
    expect(screen.getByRole('tab', { name: /RUNS/ })).toHaveTextContent('RUNS · 6')
  })

  it('selecting_a_row_replays_that_run', async () => {
    await mountApp('#/tickets/TKT-142/runs', routesFor(SIX, {
      '/api/runs/run-3/transcript': { body: {
        run_id: 'run-3', complete: true, next_after_seq: 2,
        entries: [entry({ seq: 1, text: 'it began' }), entry({ seq: 2, text: 'it was killed' })],
      } },
    }))
    await settle()

    await act(async () => { screen.getAllByTestId('run-row')[3].click() })
    await arrive()

    // the SESSION tab, not a modal and not another screen
    expect(location.hash).toBe('#/tickets/TKT-142/session')
    expect(screen.getByTestId('surface')).toBeTruthy()

    // that run's entries, from E8
    expect(callsTo('/api/runs/run-3/transcript')).toHaveLength(1)
    expect(screen.getAllByTestId('replay-entry').map((e) => e.textContent))
      .toEqual(['it began', 'it was killed'])

    // a banner naming the run, and the pane saying out loud that it is read only
    const banner = screen.getByTestId('replay-banner')
    expect(banner).toHaveTextContent('run #3')
    expect(banner).toHaveTextContent('read only')
    expect(screen.getByTestId('replayed-run').getAttribute('data-readonly')).toBe('true')

    // the composer cannot send into a finished run, so it does not offer to
    const composer = screen.getByTestId('replay-composer')
    expect(within(composer).getByRole('textbox')).toBeDisabled()
    expect(within(composer).getByRole('button')).toBeDisabled()
  })

  it('a_running_row_updates_from_M11', async () => {
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor(SIX))
    await settle()
    await settle()

    const dur = () => within(screen.getAllByTestId('run-row')[0]).getByTestId('run-dur').textContent

    // a live run's clock counts from what the server last said, client-side
    expect(dur()).toBe('12m 04s')
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(dur()).toBe('12m 09s')

    // M11 patches the row IN PLACE — same six rows, same position, a terminal outcome
    await act(async () => {
      app.socket().deliver({
        m: 'run', ts: '2026-07-28T14:15:00Z', seq: null, lane: 'lane/01', run_id: 'run-6', n: 6,
        outcome: 'crashed', started_iso: '2026-07-28T14:02:11Z',
        ended_iso: '2026-07-28T14:15:00Z', stage: 4, duration_s: 780, cost_usd: 0.91,
      })
    })
    await settle()

    const rows = screen.getAllByTestId('run-row')
    expect(rows).toHaveLength(6)
    expect(within(rows[0]).getByTestId('run-n')).toHaveTextContent('#6')
    expect(within(rows[0]).getByTestId('run-outcome')).toHaveTextContent('crashed mid-turn')
    expect(within(rows[0]).getByTestId('run-cost')).toHaveTextContent('$0.91')
    // ended: the clock is frozen at what the server said, not still counting
    expect(dur()).toBe('13m 00s')
    expect(callsTo('/api/tickets/TKT-142/runs')).toHaveLength(1)

    // and a run that starts while the tab is open arrives as a row rather than as a refetch
    await act(async () => {
      app.socket().deliver({
        m: 'run', ts: '2026-07-28T14:16:00Z', seq: null, lane: 'lane/01', run_id: 'run-7', n: 7,
        outcome: 'running', started_iso: '2026-07-28T14:16:00Z', ended_iso: null,
        stage: 0, duration_s: 0, cost_usd: 0,
      })
    })
    await settle()
    expect(screen.getAllByTestId('run-row')).toHaveLength(7)
    expect(within(screen.getAllByTestId('run-row')[0]).getByTestId('run-n')).toHaveTextContent('#7')
    expect(callsTo('/api/tickets/TKT-142/runs')).toHaveLength(1)
  })

  it('a_running_rows_clock_stops_when_the_socket_drops', async () => {
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor(SIX))
    await settle()
    await settle()
    const dur = () => within(screen.getAllByTestId('run-row')[0]).getByTestId('run-dur').textContent
    expect(dur()).toBe('12m 04s')

    await act(async () => { app.socket().onclose?.() })
    await settle()
    screen.getByTestId('reconnect')

    // §4.10's socket-down rule: rows stay, the running row keeps its label, its clock stops
    await act(async () => { vi.advanceTimersByTime(60_000) })
    expect(screen.getAllByTestId('run-row')).toHaveLength(6)
    expect(within(screen.getAllByTestId('run-row')[0]).getByTestId('run-outcome'))
      .toHaveTextContent('running')
    expect(dur()).toBe('12m 04s')
  })

  it('no_runs_is_not_an_error', async () => {
    await mountApp('#/tickets/TKT-142/runs', routesFor([]))
    await settle()
    await settle()

    expect(screen.getByTestId('runs-empty')).toHaveTextContent('No runs recorded for this ticket.')
    expect(screen.queryAllByTestId('run-row')).toHaveLength(0)
    expect(labelOf('runs', { ticketId: 'TKT-142', lane: null,
                            ticket: { loading: true } } as TabProps)).toBe('RUNS')
  })

  it('MANDATORY_NEGATIVE_a_replayed_run_cannot_be_mistaken_for_the_live_one', async () => {
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor(SIX, {
      '/api/runs/run-3/transcript': { body: {
        run_id: 'run-3', complete: true, next_after_seq: 2,
        entries: [entry({ seq: 1, text: 'it began' }), entry({ seq: 2, text: 'it was killed' })],
      } },
    }))
    await settle()

    await act(async () => { screen.getAllByTestId('run-row')[3].click() })
    await arrive()
    expect(screen.getAllByTestId('replay-entry')).toHaveLength(2)

    // the LIVE run keeps talking while a finished one is being read
    await act(async () => {
      app.socket().deliver(transcriptFrame(11, 'live one'))
      app.socket().deliver(transcriptFrame(12, 'live two'))
      app.socket().deliver(transcriptFrame(13, 'live three'))
    })
    await settle()

    // not one of them appended to the run being replayed …
    const replayed = screen.getAllByTestId('replay-entry').map((e) => e.textContent)
    expect(replayed).toEqual(['it began', 'it was killed'])
    expect(screen.getByTestId('replayed-run').textContent).not.toContain('live one')
    expect(screen.getByTestId('replayed-run').textContent).not.toContain('live three')
    // … and the banner has not quietly become the live run's
    expect(screen.getByTestId('replay-banner')).toHaveTextContent('run #3')
    expect(screen.getByTestId('replayed-run').getAttribute('data-readonly')).toBe('true')

    // going back to the live run shows EVERY frame that arrived while you were reading
    const before = calls.length
    await act(async () => { screen.getByTestId('replay-exit').click() })
    await settle()
    expect(screen.queryByTestId('replayed-run')).toBeNull()
    expect(screen.getAllByTestId('live-entry').map((e) => e.textContent))
      .toEqual(['live one', 'live two', 'live three'])
    // and leaving a replay refetches nothing: the live column never stopped holding its own
    expect(calls.length).toBe(before)
  })
})

/**
 * `WEB-FIX-354` — the SESSION tab a person actually lands on, not a seam standing in for it.
 *
 * Every case above replaces `TABS.session`, so the component the RUNS tab navigates TO was never
 * the product's. These mount the real one. The browser gate had been red about this for two
 * tickets: selecting a finished run landed on SESSION with `NO LANE ASSIGNED` and no banner,
 * while the row underneath — the one that guards the read-only promise — counted the enabled
 * controls inside an absent panel, found none, and reported the missing panel as safe.
 *
 * §4.10: *"Selecting a row loads that run's transcript into the SESSION tab, read-only, with a
 * banner naming the run."* A finished run is history; whether a lane is assigned right now is a
 * fact about the present and has nothing to say about it.
 */
describe('4.10 · reading a finished run back on the REAL SESSION tab', () => {
  /**
   * The gate's own fixture, in the shape it serves it: TKT-139 has two runs of DIFFERENT
   * outcomes and no lane. Two, because with one row "the run that was selected" and "the only
   * run there is" are the same answer and no assertion can tell them apart.
   */
  const FINISHED_AND_ORPHANED = [
    run({ run_id: 'run-0003', n: 2, outcome: 'completed', stage: 5,
          started_iso: '2026-07-27T09:14:02Z', ended_iso: '2026-07-27T09:26:38Z',
          duration_s: 756, cost_usd: 1.91 }),
    run({ run_id: 'run-0002', n: 1, outcome: 'orphaned', stage: 2,
          started_iso: '2026-07-26T16:41:11Z', ended_iso: '2026-07-26T18:02:54Z',
          duration_s: 4903, cost_usd: 3.42 }),
  ]

  const TRANSCRIPTS = {
    '/api/runs/run-0003/transcript': { body: {
      run_id: 'run-0003', complete: true, next_after_seq: 1,
      entries: [entry({ seq: 1, text: 'the finished one' })],
    } },
    '/api/runs/run-0002/transcript': { body: {
      run_id: 'run-0002', complete: true, next_after_seq: 1,
      entries: [entry({ seq: 1, text: 'the orphaned one' })],
    } },
  }

  /** Every control the read-only composer offers, and whether any of them can still act. */
  function composerControls() {
    const composer = screen.getByTestId('replay-composer')
    return Array.from(composer.querySelectorAll('textarea, button')) as Array<
      HTMLTextAreaElement | HTMLButtonElement>
  }

  beforeEach(() => { TABS.session = REAL_SESSION })

  it('the_read_only_promise_is_asserted_on_a_panel_that_exists', async () => {
    // No lane, which is the state the browser gate photographs and the ordinary state of a
    // ticket somebody opens to read its runs back: nothing is running in it any more.
    await mountApp('#/tickets/TKT-142/runs',
                   routesFor(FINISHED_AND_ORPHANED, TRANSCRIPTS, []))
    await settle()
    await settle()

    // the ORPHANED one — the second row, so the banner cannot be right by naming the only run
    await act(async () => { screen.getAllByTestId('run-row')[1].click() })
    await arrive()

    expect(location.hash).toBe('#/tickets/TKT-142/session')
    // NOT the empty state: `NO LANE ASSIGNED` is what a person saw here instead of their run
    expect(screen.queryByTestId('session-empty')).toBeNull()

    const banner = screen.getByTestId('replay-banner')
    expect(banner).toHaveTextContent('run #1')
    expect(banner).toHaveTextContent('orphaned')
    expect(banner).toHaveTextContent('read only')
    // the run's own entries, drawn by §4.6's table rather than by a second one
    expect(within(screen.getByTestId('replay-entries')).getAllByTestId('entry')
      .map((e) => e.textContent)).toEqual(['the orphaned one'])

    // The read-only promise, asserted on the panel and its controls TOGETHER: the count is part
    // of the sentence, so an absent panel throws on the query and a present-but-empty one fails
    // the length. Counting `0 enabled` inside nothing is what reported this defect as safe.
    const controls = composerControls()
    expect(controls.length).toBeGreaterThan(0)
    expect(controls.filter((c) => !c.disabled).map((c) => c.tagName)).toEqual([])

    // and `back to the live run` leaves the replay, on a ticket that has no live run to go to
    await act(async () => { screen.getByTestId('replay-exit').click() })
    await settle()
    expect(screen.queryByTestId('replay-banner')).toBeNull()
    expect(screen.getByTestId('session-empty')).toBeTruthy()
  })

  it('reading_a_run_back_on_a_live_lane_and_leaving_it_again', async () => {
    // The same journey on a ticket a lane IS assigned to, which is where a person reads a run
    // back while the next one is already going. The replay branch lives inside the live session's
    // component, so both crossings — into the replay and out of it — go through it.
    await mountApp('#/tickets/TKT-142/runs',
                   routesFor(FINISHED_AND_ORPHANED, TRANSCRIPTS,
                             [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142' })]))
    await settle()
    await settle()

    await act(async () => { screen.getAllByTestId('run-row')[0].click() })
    await arrive()
    expect(screen.getByTestId('replay-banner')).toHaveTextContent('run #2')
    expect(screen.getByTestId('replay-banner')).toHaveTextContent('read only')

    // back to the live run: the live column returns, and the tab is still on the screen
    await act(async () => { screen.getByTestId('replay-exit').click() })
    await settle()
    expect(screen.queryByTestId('replayed-run')).toBeNull()
    expect(screen.getByTestId('session')).toBeTruthy()
    expect(screen.getByTestId('composer')).toBeTruthy()
  })

  it('a_run_is_read_back_on_its_OWN_ticket_and_on_no_other', async () => {
    // The selection is module state, held for the whole screen — so the one thing that says
    // WHICH ticket it belongs to is the `ticketId` the RUNS tab put in it. Before this ticket
    // nothing read it, and the pane was reachable only from a ticket holding a lane; giving it
    // to the idle branch as well is what makes every other ticket a place it could be drawn.
    const doc = ticketDoc({ lane: null, runs: { count: 2, duration_s: 1461, cost_usd: 3.82 } })
    const other = ticketDoc({ id: 'TKT-151', title: 'Provenance in the ledger', lane: null,
                              runs: { count: 0, duration_s: 0, cost_usd: 0 } })
    await mountApp('#/tickets/TKT-142/runs', {
      ...routesFor(FINISHED_AND_ORPHANED, TRANSCRIPTS, []),
      '/api/tickets/TKT-142': { body: doc },
      '/api/tickets/TKT-151': { body: other },
      '/api/tickets/TKT-151/report': { status: 404, body: { error: { code: 'not_found' } } },
      '/api/tickets/TKT-151/runs': { body: { runs: [] } },
    })
    await settle()
    await settle()

    await act(async () => { screen.getAllByTestId('run-row')[0].click() })
    await arrive()
    expect(screen.getByTestId('replay-banner')).toHaveTextContent('run #2')

    // the OTHER ticket's SESSION tab: its own empty state, not this run
    await act(async () => { location.hash = '#/tickets/TKT-151/session' })
    await arrive()
    expect(screen.getByTestId('ticket-identity')).toHaveTextContent('TKT-151')
    expect(screen.queryByTestId('replay-banner')).toBeNull()
    expect(screen.queryByTestId('replayed-run')).toBeNull()
    expect(screen.getByTestId('session-empty')).toBeTruthy()
    // and it did not go and read the other ticket's run either
    expect(callsTo('/api/runs/run-0003/transcript')).toHaveLength(1)

    // …and coming back, the selection is still there: it was scoped, not cleared
    await act(async () => { location.hash = '#/tickets/TKT-142/session' })
    await arrive()
    expect(screen.getByTestId('replay-banner')).toHaveTextContent('run #2')
  })

  it('MANDATORY_NEGATIVE_a_run_that_is_still_running_is_not_replayed', async () => {
    // A lane IS assigned here, because "still running" is what a live lane means: the live
    // session is already on this tab and a replay of it would be a second copy that stops
    // updating (`RunsTab.tsx`, the `running` branch this case exists to keep).
    const live = run({ run_id: 'run-0004', n: 3, outcome: 'running', ended_iso: null, stage: 1,
                       started_iso: '2026-07-28T14:02:11Z', duration_s: 724, cost_usd: 0.74 })
    await mountApp('#/tickets/TKT-142/runs',
                   routesFor([live, ...FINISHED_AND_ORPHANED], TRANSCRIPTS,
                             [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142' })]))
    await settle()
    await settle()

    // the running row — the SESSION tab is the live one, and there is no replay of it
    await act(async () => { screen.getAllByTestId('run-row')[0].click() })
    await arrive()
    expect(location.hash).toBe('#/tickets/TKT-142/session')
    expect(screen.queryByTestId('replayed-run')).toBeNull()
    expect(screen.queryByTestId('replay-banner')).toBeNull()
    expect(screen.getByTestId('session')).toBeTruthy()
    expect(callsTo('/api/runs/run-0004/transcript')).toHaveLength(0)

    // …and the case is not passing because clicking a row does nothing: a FINISHED row, on the
    // same screen, does open its replay.
    await act(async () => { location.hash = '#/tickets/TKT-142/runs' })
    await arrive()
    await act(async () => { screen.getAllByTestId('run-row')[1].click() })
    await arrive()
    expect(screen.getByTestId('replay-banner')).toHaveTextContent('run #2')

    // going back to the running row leaves the replay behind rather than keeping it on screen
    await act(async () => { location.hash = '#/tickets/TKT-142/runs' })
    await arrive()
    await act(async () => { screen.getAllByTestId('run-row')[0].click() })
    await arrive()
    expect(screen.queryByTestId('replayed-run')).toBeNull()
    expect(screen.getByTestId('session')).toBeTruthy()
  })
})

/**
 * `WEB-FIX-091` — the header above the table, and the tab above the header.
 *
 * §4.5's identity row and §4.10's tab label both state a COUNT of runs, and the table under them
 * states the runs. `WEB-FEAT-016` gave the table E7 patched live by `M11` and left the two numbers
 * reading E5, which is a snapshot of the same files taken when the page loaded. Nothing refetches
 * it, because a run starting does not change the ticket DOCUMENT — so the moment a run begins, the
 * screen says `0 runs · RUNS · 0` over a row that is producing output.
 *
 * F27 is the rule these three have to keep: the header reports the same number E7's table does,
 * from the same files. One reading, not two that agree at load and part company afterwards.
 */
describe('4.5 · the count over the table is the table', () => {
  const tabLabel = () =>
    document.querySelector('[data-tab="runs"]')?.textContent ?? ''
  const metrics = () => screen.getByTestId('ticket-metrics').textContent ?? ''

  /** M11, the frame a run's first breath arrives on. */
  function runFrame(over: Record<string, unknown> = {}) {
    return {
      m: 'run', ts: '2026-07-28T14:03:00Z', seq: null, lane: 'lane/01',
      run_id: 'run-7', n: 1, outcome: 'running', started_iso: '2026-07-28T14:02:11Z',
      ended_iso: null, stage: 0, duration_s: 0, cost_usd: 0,
      ...over,
    }
  }

  it('test_a_run_in_flight_is_counted_and_listed', async () => {
    // The page opened before the run did, which is the ordinary order: a person opens a ticket
    // and then starts it. E5 answered `0 runs` and is right about the moment it was asked.
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor([], {
      '/api/tickets/TKT-142': { body: ticketDoc({ lane: 'lane/01',
        runs: { count: 0, duration_s: 0, cost_usd: 0 } }) },
    }))
    await settle()
    await settle()
    expect(tabLabel()).toBe('RUNS · 0')

    await deliverFrames(app, runFrame())

    const rows = screen.getAllByTestId('run-row')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByTestId('run-outcome')).toHaveTextContent('running')
    expect(tabLabel()).toBe('RUNS · 1')
    expect(metrics()).toMatch(/^1 runs? · /)
  })

  it('test_a_run_row_carries_the_time_it_started', async () => {
    // Both ways a row reaches the table: read off E7 at mount, and appended by M11 while it is
    // open. A start time the wire carried may not come out as `—` on either path.
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor([
      run({ run_id: 'run-1', n: 1, started_iso: '2026-07-28T11:20:44Z' }),
    ]))
    await settle()
    await settle()

    const fetched = within(screen.getAllByTestId('run-row')[0]).getByTestId('run-started')
    expect(fetched.textContent).not.toBe('—')
    expect(fetched.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/)

    await deliverFrames(app, runFrame())
    const live = within(screen.getAllByTestId('run-row')[0]).getByTestId('run-started')
    expect(live.textContent).not.toBe('—')
    expect(live.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('MANDATORY_NEGATIVE_test_a_run_in_another_tickets_lane_is_not_counted', async () => {
    // Deriving the count from the rows may not invent one. The empty case keeps §4.10's copy, and
    // the count over it stays 0 when a frame arrives for a lane that is running a DIFFERENT
    // ticket.
    //
    // Round 2 wrote this case with `lane/02` naming no ticket at all, and QA was right that it
    // then asserted the wrong rule: `M11` carries no `ticket_id`, so a lane name alone says
    // nothing, and a frame from a lane that IS running this ticket must be counted rather than
    // ignored (the case above). What makes a lane this ticket's is E9, which the page already
    // holds — so the fixture gives `lane/02` an owner, and that owner is somebody else.
    const app = await mountApp('#/tickets/TKT-142/runs', {
      ...ticketRoutes(ticketDoc({ lane: 'lane/01', runs: { count: 0, duration_s: 0, cost_usd: 0 } }),
        [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142' }),
         laneRow({ lane: 'lane/02', ticket_id: 'TKT-999' })]),
      '/api/tickets/TKT-142/report': { body: REPORT },
      '/api/tickets/TKT-142/runs': { body: { runs: [] } },
    })
    await settle()
    await settle()

    expect(screen.getByTestId('runs-empty')).toHaveTextContent('No runs recorded for this ticket.')
    expect(tabLabel()).toBe('RUNS · 0')
    expect(metrics()).toMatch(/^0 runs · /)

    await deliverFrames(app, runFrame({ lane: 'lane/02', run_id: 'run-elsewhere' }))
    expect(screen.getByTestId('runs-empty')).toHaveTextContent('No runs recorded for this ticket.')
    expect(tabLabel()).toBe('RUNS · 0')
    expect(metrics()).toMatch(/^0 runs · /)
  })

  it('test_a_run_in_a_second_lane_on_this_ticket_is_counted_and_listed', async () => {
    // QA's round-2 F1. A ticket holds more than one lane — `run` and `qa` are two roles and the
    // gate exercises both — and the page resolves ONE of them for its own header. A run started
    // in the other is still a run on this ticket: the frame reaches this client because the
    // client asked for this TICKET's scope, and it carries no ticket id of its own to check.
    const app = await mountApp('#/tickets/TKT-142/runs', {
      ...ticketRoutes(ticketDoc({ lane: 'lane/01', runs: { count: 1, duration_s: 76, cost_usd: 0.62 } }),
        [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142', role: 'run' }),
         laneRow({ lane: 'lane/10', ticket_id: 'TKT-142', role: 'qa' })]),
      '/api/tickets/TKT-142/report': { body: REPORT },
      '/api/tickets/TKT-142/runs': { body: { runs: [
        run({ run_id: 'run-1', n: 1, outcome: 'completed', duration_s: 76, cost_usd: 0.62 }),
      ] } },
    })
    await settle()
    await settle()
    expect(tabLabel()).toBe('RUNS · 1')

    await deliverFrames(app, runFrame({ lane: 'lane/10', run_id: 'run-2', n: 2 }))

    const rows = screen.getAllByTestId('run-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByTestId('run-outcome')).toHaveTextContent('running')
    expect(tabLabel()).toBe('RUNS · 2')
    expect(metrics()).toMatch(/^2 runs · /)
  })
})

/**
 * `WEB-FIX-091` round 2 — transcribed from QA's own reproductions, RED before anything is fixed.
 *
 * Three of QA's findings are regressions this ticket introduced, and all three are the same
 * mistake in different clothes: **the rows became the source of truth before they were a complete
 * reading of it.** A count derived from an incomplete list is worse than the stale count it
 * replaced, because it looks live.
 */
describe('4.5 · what the derived count owes the rows it is derived from', () => {
  const tabLabel = () => document.querySelector('[data-tab="runs"]')?.textContent ?? ''
  const metrics = () => screen.getByTestId('ticket-metrics').textContent ?? ''

  function runFrame(over: Record<string, unknown> = {}) {
    return {
      m: 'run', ts: '2026-07-28T14:03:00Z', seq: null, lane: 'lane/01',
      run_id: 'run-7', n: 1, outcome: 'running', started_iso: '2026-07-28T14:02:11Z',
      ended_iso: null, stage: 0, duration_s: 0, cost_usd: 0,
      ...over,
    }
  }

  it('test_a_frame_after_a_failed_read_does_not_become_the_whole_history', async () => {
    // QA's F2. E7 failed, so the document's 12 stands. One `M11` frame then arrives — it is one
    // run's news, not an answer about the ticket — and the old code promoted the single row it
    // built from that frame into the authoritative reading: `RUNS · 12` became `RUNS · 1`.
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor([], {
      '/api/tickets/TKT-142': { body: ticketDoc({ lane: 'lane/01',
        runs: { count: 12, duration_s: 9999, cost_usd: 7.5 } }) },
      '/api/tickets/TKT-142/runs': { status: 500,
        body: { error: { code: 'boom', message: 'no', detail: {} } } },
    }))
    await settle()
    await settle()
    expect(tabLabel()).toBe('RUNS · 12')

    await deliverFrames(app, runFrame({ run_id: 'run-13', n: 13 }))
    expect(tabLabel()).toBe('RUNS · 12')
    expect(metrics()).toMatch(/^12 runs · /)
  })

  it('test_a_run_that_started_while_the_read_was_in_flight_survives_the_answer', async () => {
    // QA's F3. E7's answer is a photograph of the moment it was taken, and a run that began after
    // that moment is not in it. Replacing the rows wholesale dropped the live row — and `M11`
    // fires on start and end, never on a timer, so it stayed dropped for the life of the run.
    let release = () => {}
    const held = new Promise<void>((resolve) => { release = resolve })
    const app = await mountApp('#/tickets/TKT-142/runs', routesFor([], {
      '/api/tickets/TKT-142': { body: ticketDoc({ lane: 'lane/01',
        runs: { count: 0, duration_s: 0, cost_usd: 0 } }) },
      '/api/tickets/TKT-142/runs': async () => { await held; return { body: { runs: [] } } },
    }))
    await settle()

    await deliverFrames(app, runFrame())
    // While the read is in flight the tab still reads the DOCUMENT's count, and the pane says it
    // is reading rather than drawing a table — the two do not contradict each other, and a frame
    // may not promote itself into an answer about the whole ticket (QA's F2, one case up). What
    // the frame HAS earned is survival: the answer that lands next was taken before it existed.
    expect(screen.queryByTestId('runs-empty')).toBeNull()

    await act(async () => { release(); await Promise.resolve() })
    await settle()
    await settle()

    expect(screen.getAllByTestId('run-row')).toHaveLength(1)
    expect(tabLabel()).toBe('RUNS · 1')
    expect(metrics()).toMatch(/^1 runs? · /)
    expect(screen.queryByTestId('runs-empty')).toBeNull()
  })

  it('test_the_header_total_counts_with_the_row_it_totals', async () => {
    // QA's F4. `duration_s` is what the server last said; the row's cell counts on from it and the
    // header's did not, so the two numbers §4.5 and §4.10 draw about ONE run drifted apart on
    // screen — the very class this ticket exists to remove.
    await mountApp('#/tickets/TKT-142/runs', routesFor([
      run({ run_id: 'run-6', n: 6, outcome: 'running', ended_iso: null,
            started_iso: '2026-07-28T14:02:11Z', duration_s: 724, cost_usd: 0.74 }),
    ]))
    await settle()
    await settle()

    expect(metrics()).toContain('12m 04s')
    expect(screen.getByTestId('run-dur')).toHaveTextContent('12m 04s')

    await act(async () => { vi.advanceTimersByTime(120_000) })
    expect(screen.getByTestId('run-dur')).toHaveTextContent('14m 04s')
    expect(metrics()).toContain('14m 04s')
  })
})

/**
 * `WEB-FIX-091` round 3 — the completeness of the row set is ONE fact, and every surface owes it.
 *
 * `known` was added in round 1 and consumed by the header alone. QA found the pane still telling a
 * ticket with twelve runs that nothing had ever run on it, and then drawing one row under a header
 * saying twelve. The fact existed; it had one reader.
 */
describe('4.10 · a read that failed is not a ticket with no runs', () => {
  const tabLabel = () => document.querySelector('[data-tab="runs"]')?.textContent ?? ''

  function unread() {
    return mountApp('#/tickets/TKT-142/runs', {
      ...ticketRoutes(ticketDoc({ lane: 'lane/01',
        runs: { count: 12, duration_s: 9999, cost_usd: 7.5 } }),
        [laneRow({ lane: 'lane/01', ticket_id: 'TKT-142' })]),
      '/api/tickets/TKT-142/report': { body: REPORT },
      '/api/tickets/TKT-142/runs': { status: 500,
        body: { error: { code: 'boom', message: 'no', detail: {} } } },
    })
  }

  it('test_an_unreadable_history_says_so_instead_of_saying_nothing_ran', async () => {
    await unread()
    await settle()
    await settle()

    expect(screen.queryByTestId('runs-empty')).toBeNull()
    expect(screen.getByTestId('runs-unread')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('No runs recorded for this ticket.')
    expect(tabLabel()).toBe('RUNS · 12')
  })

  it('test_a_live_row_under_an_unread_history_says_it_is_only_what_is_live', async () => {
    const app = await unread()
    await settle()
    await settle()

    await deliverFrames(app, {
      m: 'run', ts: '2026-07-28T14:03:00Z', seq: null, lane: 'lane/01',
      run_id: 'run-13', n: 13, outcome: 'running', started_iso: '2026-07-28T14:02:11Z',
      ended_iso: null, stage: 0, duration_s: 0, cost_usd: 0,
    })

    // The row is drawn — it is the live run and hiding it would be worse — and the pane says, in
    // the same glance, that it is not the whole history. `RUNS · 12` over one row is only a
    // contradiction while nothing on screen accounts for the difference.
    expect(screen.getAllByTestId('run-row')).toHaveLength(1)
    expect(screen.getByTestId('runs-unread')).toBeInTheDocument()
    expect(tabLabel()).toBe('RUNS · 12')
  })
})
