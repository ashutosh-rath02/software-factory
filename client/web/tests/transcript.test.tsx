import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent, within } from '@testing-library/react'

import { calls, callsTo, settle } from './helpers'
import {
  deliver, kindsInSpec, live, LANE, measure, mountSession, snapshot, type Row,
} from './session'

/**
 * SPEC.md §4.6 — the transcript.
 *
 * The window onto a run, and the only one. Four cases, and the first is the one that matters most:
 * a kind §4.6's table names and the client silently drops is a hole in the only thing a person has
 * to look at, and it is invisible precisely because nothing renders.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** One entry of every kind, with the design's own sample values where it drew one. */
const PAYLOADS: Record<string, Record<string, unknown>> = {
  human: { text: 'still nothing after five minutes — what are you waiting on?', mark: 'delivered' },
  prose: { text: 'Both acceptance criteria for the flash side now hold.' },
  tool: { tool: 'Bash', summary: 'cd core && pytest tests/ -v', ok: true, duration_s: 4.81,
          input: 'cd core && pytest tests/ -v', result: '32 passed, 1 failed in 4.81s' },
  file_edit: { path: 'core/tasks/build.py', added: 18, removed: 6,
               diff: '@@ -40,6 +40,18 @@\n-    return True\n+    return is_fresh(artifact, sources)' },
  tool_result: { text: '32 passed, 1 failed in 4.81s' },
  error: { text: 'AssertionError: expected rebuild, got cache hit' },
  turn: { n: 4, duration_s: 192, cost_usd: 0.41 },
  silence: { silent_for_s: 252 },
  held: { held_count: 1 },
  stopped: { text: 'STOPPED BY YOU · turn 4 · 41s · $0.86',
             sub: 'What already ran stays on disk. The session stays alive.' },
  ended: { text: 'SESSION ENDED · 5 turns · $1.86', sub: 'The session id is kept.' },
  killed: { text: 'KILLED BY YOU · mid-turn', sub: 'The process group is gone.' },
  permission_request: { request_id: 'req-1', tool: 'Bash',
                        input_summary: 'npm run gate',
                        rule_that_would_be_written: 'Bash(npm run gate)' },
  permission_decision: { decision: 'always', rule: 'Bash(npm run *)' },
  stage: { stage: 'GATE', round: 2 },
  command: { which: 'gate', tool: 'gate', summary: 'npm run gate', ok: false, duration_s: 61,
             output: 'FAIL tests/transcript.test.tsx' },
  isolation_refusal: { path: '/home/you/other-repo/src/main.c', operation: 'write' },
}

function everyKind(): Row[] {
  return kindsInSpec().map((kind, i): Row => [i + 1, kind, PAYLOADS[kind] ?? {}])
}

const nodes = () => screen.getAllByTestId('entry')

/** The one entry of a kind, found the way a reviewer would: by the attribute that names it. */
function ofKind(kind: string): HTMLElement {
  const found = nodes().filter((n) => n.getAttribute('data-kind') === kind)
  if (found.length !== 1) throw new Error(`${found.length} entries of kind ${kind}, expected 1`)
  return found[0]
}

describe('4.6 the transcript', () => {
  it('every_kind_renders', async () => {
    // The list comes off SPEC.md's own table, not off the component — otherwise this case can
    // only prove the component agrees with itself.
    const kinds = kindsInSpec()
    expect(kinds).toContain('isolation_refusal')
    expect(kinds.length).toBeGreaterThanOrEqual(17)
    for (const kind of kinds) expect(PAYLOADS[kind], `no fixture for ${kind}`).toBeTruthy()

    const app = await mountSession()
    await deliver(app, snapshot(everyKind()))

    // ── 1 · not one kind is dropped, and they are in seq order
    expect(nodes().map((n) => n.getAttribute('data-kind'))).toEqual(kinds)
    for (const kind of kinds) {
      expect(ofKind(kind).textContent, `${kind} rendered nothing`).not.toBe('')
    }

    // ── 2 · each one has the shape the vocabulary sheet draws (Ticket.dc.html)
    const human = ofKind('human')
    expect(human).toHaveTextContent('YOU')
    expect(human).toHaveTextContent('delivered')
    expect(human).toHaveTextContent('still nothing after five minutes')

    expect(ofKind('prose')).toHaveTextContent('Both acceptance criteria for the flash side now hold.')

    // the collapsed tool line: tool, argument, duration — and no INPUT/RESULT until it is opened
    const tool = ofKind('tool')
    expect(tool).toHaveTextContent('Bash')
    expect(tool).toHaveTextContent('cd core && pytest tests/ -v')
    expect(tool).toHaveTextContent('4.8s')
    expect(within(tool).queryByText('INPUT')).toBeNull()

    expect(ofKind('tool_result')).toHaveTextContent('32 passed, 1 failed in 4.81s')

    const error = ofKind('error')
    expect(error).toHaveTextContent('ERROR ·')
    expect(error).toHaveTextContent('AssertionError: expected rebuild, got cache hit')

    // the design's own turn rule, composed from n / duration_s / cost_usd
    expect(ofKind('turn')).toHaveTextContent('turn 4 · 3m 12s · $0.41')

    // §8 D5 — the counter always. `silent` without a number is not actionable.
    const silence = ofKind('silence')
    expect(silence).toHaveTextContent('NO OUTPUT · 4m 12s')
    expect(silence).toHaveTextContent('last tool call has not returned · turn cost still accruing')

    const held = ofKind('held')
    expect(held).toHaveTextContent('NOT PROCESSED · AGENT IS MID-TURN')
    expect(held).toHaveTextContent('send again to interrupt')

    expect(ofKind('stopped')).toHaveTextContent('STOPPED BY YOU · turn 4 · 41s · $0.86')
    expect(ofKind('stopped')).toHaveTextContent('What already ran stays on disk')
    expect(ofKind('ended')).toHaveTextContent('SESSION ENDED · 5 turns · $1.86')
    expect(ofKind('killed')).toHaveTextContent('KILLED BY YOU · mid-turn')

    // the three that carry a state colour carry the design's token and not a hard-coded one
    expect(ofKind('killed').getAttribute('data-tone')).toBe('crash')
    expect(ofKind('held').getAttribute('data-tone')).toBe('silent')
    expect(ofKind('isolation_refusal').getAttribute('data-tone')).toBe('crash')

    // F9 — a refusal names the path, because a refusal that is only a log line is one nobody sees
    const refusal = ofKind('isolation_refusal')
    expect(refusal).toHaveTextContent('REFUSED · outside the lane')
    expect(refusal).toHaveTextContent('/home/you/other-repo/src/main.c')

    // §8 D4 — no drawing exists, so the accent `held` rectangle with the three buttons it is
    const request = ofKind('permission_request')
    expect(request).toHaveTextContent('Bash')
    expect(request).toHaveTextContent('Bash(npm run gate)')
    const buttons = within(request).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['Allow once', 'Always allow', 'Deny'])

    expect(ofKind('permission_decision')).toHaveTextContent('rule added · Bash(npm run *)')

    // the `turn` rule shape, per §4.6
    expect(ofKind('stage')).toHaveTextContent('GATE · round 2')

    // a `tool` line whose tool name is the command, expanding to the output
    const command = ofKind('command')
    expect(command).toHaveTextContent('gate')
    expect(command).toHaveTextContent('npm run gate')

    // ── 3 · the column is the design's: one scrolling region, max 900 px
    const column = screen.getByTestId('transcript')
    expect(column.querySelector('.transcript-column')).not.toBeNull()
  })

  it('tool_expands', async () => {
    // F12. One line; clicking reveals INPUT and RESULT; and the expansion SURVIVES what arrives
    // after it — an entry that folds itself shut because something else happened is an entry
    // nobody can read.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'tool', PAYLOADS.tool]]))

    const tool = ofKind('tool')
    expect(tool.getAttribute('data-expanded')).toBe('false')
    expect(within(tool).queryByText('INPUT')).toBeNull()

    await act(async () => { fireEvent.click(within(tool).getByTestId('entry-line')) })
    await settle()

    expect(ofKind('tool').getAttribute('data-expanded')).toBe('true')
    expect(within(ofKind('tool')).getByText('INPUT')).toBeTruthy()
    expect(within(ofKind('tool')).getByText('RESULT')).toBeTruthy()
    expect(ofKind('tool')).toHaveTextContent('32 passed, 1 failed in 4.81s')

    // twenty more entries arrive
    const more = []
    for (let i = 0; i < 20; i += 1) more.push(live(2 + i, 'prose', { text: `line ${i}` }))
    await deliver(app, ...more)

    expect(nodes()).toHaveLength(21)
    expect(ofKind('tool').getAttribute('data-expanded')).toBe('true')
    expect(within(ofKind('tool')).getByText('RESULT')).toBeTruthy()
  })

  it('file_edit_shows_a_diff', async () => {
    // F13. `Edit  <path> · +18 −6` collapsed, and the unified diff on expansion — NOT INPUT and
    // RESULT. Editing files is the commonest thing an agent does and it must be the easiest thing
    // in the transcript to open.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'file_edit', PAYLOADS.file_edit]]))

    const edit = ofKind('file_edit')
    expect(within(edit).getByTestId('entry-line')).toHaveTextContent('Edit')
    expect(within(edit).getByTestId('entry-line')).toHaveTextContent('core/tasks/build.py')
    expect(within(edit).getByTestId('entry-line')).toHaveTextContent('+18 −6')

    await act(async () => { fireEvent.click(within(edit).getByTestId('entry-line')) })
    await settle()

    const opened = ofKind('file_edit')
    expect(opened.getAttribute('data-expanded')).toBe('true')
    expect(within(opened).getByTestId('entry-diff'))
      .toHaveTextContent('return is_fresh(artifact, sources)')
    expect(within(opened).queryByText('INPUT')).toBeNull()
    expect(within(opened).queryByText('RESULT')).toBeNull()
  })

  it('entries_apply_in_seq_order_and_a_gap_resyncs', async () => {
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'one' }]]))
    expect(nodes()).toHaveLength(1)

    // ── out of order · 3 before 2. The column shows one, two, three — never one, three, two.
    await deliver(app, live(3, 'prose', { text: 'three' }))
    await deliver(app, live(2, 'prose', { text: 'two' }))
    expect(nodes().map((n) => n.textContent)).toEqual(['one', 'two', 'three'])

    // ── a replay of what we already hold changes nothing
    await deliver(app, live(2, 'prose', { text: 'two' }))
    expect(nodes()).toHaveLength(3)

    // ── a `tool` gaining its result: the SAME seq with patch:true, MERGED over what is held.
    //    The patch carries the field it adds and nothing else, which is what the wire sends and
    //    what `live()` now refuses to let a fixture fake (WEB-FIX-275).
    await deliver(app, live(4, 'tool', { tool: 'Bash', summary: 'pytest', ok: null }))
    expect(ofKind('tool').getAttribute('data-ok')).toBe('null')
    await deliver(app, live(4, 'tool', { ok: true, duration_s: 4.81, result: 'ok' }, true))
    expect(nodes()).toHaveLength(4)
    expect(ofKind('tool').getAttribute('data-ok')).toBe('true')

    // ── the gap. Seq 7 with 5 and 6 missing: it is HELD BACK, and one `resume` goes out naming
    //    the last contiguous seq. A client that rendered 7 next to 4 would be showing a hole it
    //    could not see.
    const before = app.socket().frames().filter((f) => f.m === 'resume').length
    await deliver(app, live(7, 'prose', { text: 'seven' }))
    expect(nodes()).toHaveLength(4)

    const resumes = app.socket().frames().filter((f) => f.m === 'resume')
    expect(resumes.length).toBe(before + 1)
    expect(resumes[resumes.length - 1]).toMatchObject({ lane: LANE, after_seq: 4 })

    // the server replays the two it owes; the buffered 7 then drains behind them, in order
    await deliver(app, live(5, 'prose', { text: 'five' }), live(6, 'prose', { text: 'six' }))
    expect(nodes().map((n) => n.textContent))
      .toEqual(['one', 'two', 'three', expect.stringContaining('Bash'), 'five', 'six', 'seven'])
  })

  it('a_second_run_starts_the_column_over', async () => {
    // A LANE OUTLIVES ITS RUNS AND THE SEQUENCE DOES NOT. Every run numbers its own entries from
    // 1 (`agent/session.cpp`), so the second run in a lane arrives at seq 1 while the column
    // still holds the first run's tail — and §4.6's ordering rule, applied to a `lastSeq` that
    // belongs to a finished run, reads every one of them as a replay and drops it.
    //
    // The screen that produced this case: `Agent is producing`, the Stop button, and not one
    // line for four minutes, while the run had already written its answer to disk. The composer
    // was right and the column was frozen, which is the worst of the two — a person watching it
    // has no way to tell a stuck agent from a stuck screen.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'one' }],
                                 [2, 'prose', { text: 'two' }]]))
    expect(nodes()).toHaveLength(2)

    await deliver(app, live(1, 'prose', { text: 'a new run speaks' }, undefined, 'run-2'))
    expect(nodes().map((n) => n.textContent)).toEqual(['a new run speaks'])

    // ...and the new run's own sequence applies from there, in its own numbering
    await deliver(app, live(2, 'prose', { text: 'and again' }, undefined, 'run-2'))
    expect(nodes().map((n) => n.textContent)).toEqual(['a new run speaks', 'and again'])

    // ── NEGATIVE. The run already on screen does not restart the column on every frame: a reset
    //    per entry would throw away the transcript on every line the agent writes.
    await deliver(app, live(3, 'prose', { text: 'third' }, undefined, 'run-2'))
    expect(nodes().map((n) => n.textContent))
      .toEqual(['a new run speaks', 'and again', 'third'])

    // ── and no `resume` went out. A new run is not a gap in an old one, and asking a server to
    //    replay a sequence that belongs to a finished run is how a client asks for a hole.
    expect(app.socket().frames().filter((f) => f.m === 'resume')).toHaveLength(0)
  })

  /**
   * WEB-FIX-051 — a surface that comes back is not a connection that was away.
   *
   * The user's report: "khi chuyển tab qua requirement quay lại thì mất lỗi. send tin nhắn thì
   * hiện lại lỗi." Leave the SESSION tab, come back, and the whole transcript is gone; send a
   * message and it reappears one frame later.
   *
   * `socket.ts` decided between `resume` and `subscribe` from the module-level lane map alone, and
   * that map outlives the component. A remount on a live connection therefore asked the question a
   * CAUGHT-UP connection asks — "what did I miss?" — and got the honest answer: nothing. The
   * surface, which had just reset itself to EMPTY on mount, drew the nothing.
   */
  it('a_remounting_surface_asks_for_a_snapshot', async () => {
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first' }]]),
                  live(2, 'prose', { text: 'second' }),
                  live(3, 'prose', { text: 'third' }))
    expect(nodes()).toHaveLength(3)

    const before = app.socket().frames().length

    // away — the SESSION surface unmounts, the socket does not
    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()
    // ...and back
    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    const asked = app.socket().frames().slice(before)
      .filter((f) => f.m === 'subscribe' || f.m === 'resume')
    expect(asked.length).toBeGreaterThan(0)
    const lanes = asked.filter((f) => f.m === 'resume'
      || (f.scope as Record<string, unknown> | undefined)?.lane === LANE)
    expect(lanes.map((f) => f.m)).not.toContain('resume')
    expect(lanes.map((f) => f.m)).toContain('subscribe')

    // and the entries are on the screen with no further frame arriving — the module still holds
    // them, and a surface that has to wait for a round trip is a surface that blanks in between
    expect(nodes()).toHaveLength(3)
  })

  it('a_resume_that_is_answered_with_nothing_still_leaves_a_full_surface', async () => {
    // Whatever shape the fix takes, a surface must never END a mount holding fewer entries than
    // the socket module does. This is the invariant, stated without naming the mechanism, so a
    // future rewrite of `subscribe`/`resume` cannot satisfy the case above and break this.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first' }]]),
                  live(2, 'prose', { text: 'second' }))
    expect(nodes()).toHaveLength(2)

    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    // the server answers the remount's question with an EMPTY replay — the exact answer that
    // blanked the screen, delivered on purpose
    await deliver(app)
    expect(nodes()).toHaveLength(2)
    expect(screen.getByText('first')).toBeTruthy()
    expect(screen.getByText('second')).toBeTruthy()
  })

  it('a_remount_then_a_new_run_does_not_drop_the_new_runs_entries', async () => {
    // WEB-FIX-051 QA, F2 — a regression the first fix introduced.
    //
    // Seeding a remounting surface needs three things from the socket module: the entries, the
    // `lastSeq` they end at, and the RUN they belong to. The first fix took the run id off the
    // last ENTRY — and an entry has no `run_id`; only the `M5` frame around it does. So `runId`
    // seeded as `null` every time, and `a_second_run_starts_the_column_over`'s rule ("a new run
    // renumbers from 1") could not fire: every entry of the new run is `seq <= lastSeq`, so all of
    // them were dropped as replays until a snapshot happened to arrive.
    //
    // The comment beside that line explained why `lastSeq` has to travel with the entries, and the
    // next line took the wrong field.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first run, one' }]]),
                  live(2, 'prose', { text: 'first run, two' }))
    expect(nodes()).toHaveLength(2)

    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()
    expect(nodes()).toHaveLength(2)

    // a SECOND run in the same lane, numbering from 1 again, with no snapshot in between
    await deliver(app, live(1, 'prose', { text: 'second run, one' }, undefined, 'run-2'))
    await settle()

    expect(nodes().map((n) => n.textContent), 'the new run was dropped as a replay')
      .toEqual(['second run, one'])
  })

  it('a_run_that_started_while_away_is_the_one_on_screen_when_we_come_back', async () => {
    // WEB-FIX-073. The case above delivers the second run's entries while the surface is MOUNTED,
    // where the component's own run-change rule catches them. This one delivers them while it is
    // away — which is the ordinary shape of the journey, because leaving the tab is what a person
    // does while a run is starting — and then only the MODULE has seen them.
    //
    // `apply()` compared `seq` against the lane's `lastSeq` with no look at `run_id`, so run B's
    // entries 1 and 2 were behind run A's 2 and were discarded as replays. A mount seeds from that
    // record, so the surface came back showing a FINISHED run while a live one was producing.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'run one, first' }]]),
                  live(2, 'prose', { text: 'run one, second' }))
    expect(nodes().map((n) => n.textContent)).toEqual(['run one, first', 'run one, second'])

    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()

    // a second run starts in the same lane, numbering from 1, with NOTHING mounted to see it
    await deliver(app, live(1, 'prose', { text: 'run two, first' }, undefined, 'run-2'),
                  live(2, 'prose', { text: 'run two, second' }, undefined, 'run-2'))
    await settle()

    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    // no snapshot is delivered after the remount: this asserts what the MODULE held, which is
    // what a surface is seeded from
    expect(nodes().map((n) => n.textContent), 'the run that started while away was dropped')
      .toEqual(['run two, first', 'run two, second'])
  })

  it('a_permission_entry_while_away_does_not_erase_the_run', async () => {
    // Transcribed from QA round 1, PROBE_B. The frame the permission path publishes carries
    // `run_id: ""`, and round 1's fix treated it as a new run — so an entry that arrives while the
    // tab is away took the whole transcript with it, which is a worse version of the defect this
    // ticket was written to remove.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'run one, first' }]]),
                  live(2, 'prose', { text: 'run one, second' }))
    expect(nodes()).toHaveLength(2)

    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()

    await deliver(app, live(3, 'prose', { text: 'run one, third' }, undefined, ''))
    await settle()

    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    expect(nodes().map((n) => n.textContent), 'a frame naming no run erased the run')
      .toEqual(['run one, first', 'run one, second', 'run one, third'])
  })

  it('a_transcript_survives_a_snapshot_that_names_no_run', async () => {
    // Transcribed from QA round 2, PROBE_B — the same journey as the module case above, on screen.
    const app = await mountSession()
    // a snapshot whose lane has no run — what the server emits when `l.runId` is empty
    const runless = snapshot([[1, 'prose', { text: 'one' }],
                              [2, 'prose', { text: 'two' }]])
    ;(runless.data as { run_id: string | null }).run_id = null
    await deliver(app, runless)
    expect(nodes()).toHaveLength(2)

    await deliver(app, live(3, 'prose', { text: 'three' }))
    expect(nodes().map((n) => n.textContent)).toEqual(['one', 'two', 'three'])
  })

  it('command_output_survives_a_remount', async () => {
    // `WEB-FIX-072`. `M10` carries `seq: null` by contract and is outside the run's sequence log,
    // so no `snapshot` and no `resume` replays it: unlike the run's entries, which `WEB-FIX-051`
    // made survive, a command's output could not come back on the next frame, on a reload, on a
    // reconnect, or ever. Leave the tab after running Gate and the whole panel is gone.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first' }]]))
    await deliver(app,
      { m: 'command', ts: '2026-07-28T10:00:05Z', seq: null, lane: LANE,
        command_id: 'cmd/07', which: 'gate', chunk: '170/170 checks passed\n' },
      { m: 'command', ts: '2026-07-28T10:00:09Z', seq: null, lane: LANE,
        command_id: 'cmd/07', which: 'gate', exit_code: 0 })
    const expand = async () => {
      // The output panel is collapsed until the line is clicked — `commands.test.tsx`'s idiom,
      // and the only way a command's output is ON the screen at all.
      const line = screen.getAllByTestId('entry')
        .find((e) => e.getAttribute('data-kind') === 'command')
      await act(async () => {
        fireEvent.click(line!.querySelector('[data-testid="entry-line"]') as HTMLElement)
      })
      return line!
    }
    expect((await expand()).textContent).toContain('170/170 checks passed')

    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    // with NO further frame arriving — there is nothing on the wire that could bring it back
    const back = screen.getAllByTestId('entry')
      .find((e) => e.getAttribute('data-kind') === 'command')
    expect(back, 'the command entry is gone after the remount').toBeDefined()
    expect(back!.textContent).toContain('exit 0')
    await act(async () => {
      fireEvent.click(back!.querySelector('[data-testid="entry-line"]') as HTMLElement)
    })
    expect(back!.textContent, 'the command OUTPUT is gone after the remount')
      .toContain('170/170 checks passed')
  })

  it('a_command_still_streaming_keeps_streaming_into_the_same_entry', async () => {
    // The case §1 says decides the design: a command still RUNNING when the surface remounts must
    // keep appending to the entry it already had, not start a second one and not restart it.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first' }]]))
    await deliver(app,
      { m: 'command', ts: '2026-07-28T10:00:05Z', seq: null, lane: LANE,
        command_id: 'cmd/08', which: 'build', chunk: 'one\n' })

    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    await deliver(app,
      { m: 'command', ts: '2026-07-28T10:00:06Z', seq: null, lane: LANE,
        command_id: 'cmd/08', which: 'build', chunk: 'two\n' },
      { m: 'command', ts: '2026-07-28T10:00:07Z', seq: null, lane: LANE,
        command_id: 'cmd/08', which: 'build', chunk: 'three\n' })

    const drawn = screen.getAllByTestId('entry').filter(
      (e) => e.getAttribute('data-kind') === 'command')
    expect(drawn, 'one command, one entry').toHaveLength(1)
    await act(async () => {
      fireEvent.click(drawn[0].querySelector('[data-testid="entry-line"]') as HTMLElement)
    })
    expect(drawn[0].textContent).toContain('one')
    expect(drawn[0].textContent).toContain('two')
    expect(drawn[0].textContent).toContain('three')
    expect((drawn[0].textContent ?? '').indexOf('one'))
      .toBeLessThan((drawn[0].textContent ?? '').indexOf('three'))
  })

  it('MANDATORY_NEGATIVE_a_command_from_another_lane_is_still_ignored', async () => {
    // The cheap fix is a module-level store keyed by `command_id` alone, which puts one lane's
    // gate output into another lane's transcript. The store is keyed by LANE first.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first' }]]))
    await deliver(app,
      { m: 'command', ts: '2026-07-28T10:00:05Z', seq: null, lane: 'lane/09',
        command_id: 'cmd/99', which: 'gate', chunk: 'SOMEBODY ELSES OUTPUT\n' },
      { m: 'command', ts: '2026-07-28T10:00:06Z', seq: null, lane: 'lane/09',
        command_id: 'cmd/99', which: 'gate', exit_code: 1 })

    const commandEntriesDrawn = () => screen.queryAllByTestId('entry').filter(
      (e) => e.getAttribute('data-kind') === 'command')
    expect(commandEntriesDrawn()).toHaveLength(0)

    // …and it is still not there after a remount, which is the ONLY place a lane-blind store
    // leaks: before the remount `transcript.ts` refuses to redraw for another lane's frame, so an
    // assertion made here passes for a reason that has nothing to do with the store's keying.
    //
    // Round 1 asserted on the surface's TEXT here, and a command's output is behind the collapse —
    // so the leaked entry was on the screen and the assertion could not see it, and the case
    // stayed green against the very patch §1 named it must fail against (QA, F1). It counts the
    // entries now, and opens the one it must not find.
    await act(async () => {
      window.location.hash = '#/requirements'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await act(async () => {
      window.location.hash = '#/tickets/TKT-142/session'
      window.dispatchEvent(new Event('hashchange'))
    })
    await settle()
    await settle()

    const leaked = commandEntriesDrawn()
    if (leaked.length > 0) {
      await act(async () => {
        fireEvent.click(leaked[0].querySelector('[data-testid="entry-line"]') as HTMLElement)
      })
    }
    expect(leaked, "another lane's command was drawn in this lane's transcript").toHaveLength(0)
    expect(screen.getByTestId('surface').textContent).not.toContain('SOMEBODY ELSES OUTPUT')
  })

  it('a_reconnect_still_resumes', async () => {
    // NEGATIVE (mandatory). The cheap fix for the two cases above is to make every subscribe path
    // send `subscribe`, which turns every reconnect into a full snapshot and throws away the
    // replay path `SRV-FEAT-005` exists for. `restore()` keeps its own choice, and this is where
    // that is held: a CONNECTION that was away asks what it missed, naming the seq it holds.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'first' }]]),
                  live(2, 'prose', { text: 'second' }),
                  live(3, 'prose', { text: 'third' }))
    expect(nodes()).toHaveLength(3)

    await act(async () => { app.socket().onclose?.() })
    await act(async () => { vi.advanceTimersByTime(2000) })
    await act(async () => { app.socket().onopen?.() })
    await settle()

    const asked = app.socket().frames()
    expect(asked.some((f) => f.m === 'resume' && f.lane === LANE && f.after_seq === 3)).toBe(true)
    expect(asked.some((f) => f.m === 'subscribe'
      && (f.scope as Record<string, unknown> | undefined)?.lane === LANE)).toBe(false)
  })

  it('does_not_poll', async () => {
    // F11's client half, on this surface. Ten minutes with the transcript on screen: no fetch, no
    // frame, and nothing re-rendered from a timer. The prototype polled every eight seconds.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'one' }]]))

    const fetchesBefore = calls.length
    const before = nodes()
    const box = screen.getByTestId('composer-draft') as HTMLTextAreaElement
    await act(async () => { fireEvent.change(box, { target: { value: 'half-typed' } }) })

    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    await settle()

    // the clock really moved: the socket's 10 s liveness ping is the only timer in the system
    expect(app.socket().frames().filter((f) => f.m === 'ping').length).toBeGreaterThanOrEqual(59)

    // ── zero fetches. The prototype polled every eight seconds: seventy-five in this window.
    expect(calls.length).toBe(fetchesBefore)
    expect(callsTo('/api/lanes')).toHaveLength(1)

    // ── and nothing in the transcript was rebuilt: the SAME DOM nodes, and the draft with them
    expect(nodes()).toEqual(before)
    expect(screen.getByTestId('composer-draft')).toBe(box)
    expect(box.value).toBe('half-typed')
  })

  it('the_column_follows_the_bottom_only_when_the_reader_was_there', async () => {
    // §4.6's scrolling column. Following the tail is right when you are reading the tail and
    // wrong when you are reading something above it, and the difference is what makes a
    // transcript readable while it is still moving.
    const app = await mountSession()
    await deliver(app, snapshot([[1, 'prose', { text: 'one' }]]))
    const column = screen.getByTestId('transcript')

    measure(column, { scrollHeight: 1000, clientHeight: 200, scrollTop: 800 })
    await act(async () => { fireEvent.scroll(column) })
    await deliver(app, live(2, 'prose', { text: 'two' }))
    expect(column.scrollTop).toBe(1000)

    measure(column, { scrollHeight: 1000, clientHeight: 200, scrollTop: 0 })
    await act(async () => { fireEvent.scroll(column) })
    await deliver(app, live(3, 'prose', { text: 'three' }))
    expect(column.scrollTop).toBe(0)
  })

  it('an_empty_lane_is_the_designs_own_block', async () => {
    // §4.6's empty state, and §4.7's: a ticket nobody has opened a lane for offers no box to type
    // into, because there is nothing on the other end of it.
    const app = await mountSession([])
    expect(screen.getByTestId('session-empty')).toHaveTextContent('NO LANE ASSIGNED')
    expect(screen.getByTestId('surface').querySelectorAll('textarea')).toHaveLength(0)
    expect(screen.getByTestId('composer')).toHaveTextContent('Start a lane to send a message.')
    expect(app.socket().frames().some((f) => f.m === 'resume')).toBe(false)
  })
})

// ── WEB-FIX-275 · a patch COMPLETES an entry; it does not replace it ──────────────────────────
//
// `socket.schema.json` on `transcript.patch`: it exists for "a tool entry gaining its result, and
// a §4.6 `silence` rule gaining the length the gap really was … **Both are one entry becoming
// complete**". Becoming complete is a merge. `transcript.ts:191` does `entries[at] = arrived`,
// which is a replacement, and a real patch frame carries only the field it adds:
//
//     {"seq":2,"kind":"tool","tool":"Bash","summary":"cat …","ok":null,"input":{…}}
//     {"seq":2,"kind":"tool","ok":true}                       ← everything else is gone
//
// Seen on a live lane: eleven consecutive tool rows with a tick, a duration, and no tool name and
// no command — the one surface whose whole job is to say what the agent is doing.
//
// The case above this block delivers a patch that RE-SENDS `tool` and `summary`, which is why it
// passes: the fixture constructs the finished entry instead of replaying the two frames the wire
// actually sends. That is the escape, and these cases are the wire's version.

describe('WEB-FIX-275 · a patch completes the entry it names', () => {
  it('test_a_patch_completes_the_tool_call_it_names', async () => {
    const app = await mountSession()
    await deliver(app, live(1, 'tool', { tool: 'Bash', summary: 'cd core && pytest tests/ -v' }))
    expect(ofKind('tool')).toHaveTextContent('Bash')

    // the wire's patch: the outcome, and nothing else
    await deliver(app, live(1, 'tool', { ok: true, duration_s: 4.81 }, true))

    expect(nodes()).toHaveLength(1)
    expect(ofKind('tool')).toHaveTextContent('Bash')
    expect(ofKind('tool')).toHaveTextContent('cd core && pytest tests/ -v')
  })

  it('test_a_patch_still_applies_the_field_it_carries', async () => {
    // MANDATORY NEGATIVE. The cheap fix for the case above is to ignore patch frames, which keeps
    // every name and loses every result — `WEB-FIX-145`'s defect arrived at from the other side.
    const app = await mountSession()
    await deliver(app, live(1, 'tool', { tool: 'Bash', summary: 'pytest', ok: null }))
    expect(ofKind('tool').getAttribute('data-ok')).toBe('null')

    await deliver(app, live(1, 'tool', { ok: true, duration_s: 4.81 }, true))
    expect(ofKind('tool').getAttribute('data-ok')).toBe('true')
  })

  it('test_the_fixture_cannot_express_a_wire_that_does_not_exist', async () => {
    // §3's structural change, and the case that proves it is a door rather than a comment.
    //
    // The class WEB-FIX-275 escaped from is *a fixture that constructs the end state instead of
    // replaying the sequence that produces it*. Round 1 added the first guard and shipped it with
    // NO CASE, which is the same shape one level up: a guard nobody exercises is a guard that can
    // be deleted without a gate noticing. Both halves are asserted here.
    //
    // The third half is structural and needs no assertion because it is a compile error: `entry()`
    // is no longer exported from session.tsx, so a test cannot build an `Entry` at all. `snapshot()`
    // takes `[seq, kind, payload]` rows and builds them itself.

    // a CALL frame may not carry a settled outcome — the server always emits "ok":null there
    expect(() => live(1, 'tool', { tool: 'Bash', summary: 'pytest', ok: true }))
      .toThrow(/a wire that does not exist/)
    expect(() => live(1, 'file_edit', { path: 'a.py', ok: false }))
      .toThrow(/a wire that does not exist/)

    // a PATCH frame may not re-send identity — that is what makes a replacing client look correct
    expect(() => live(1, 'tool', { tool: 'Bash', ok: true }, true))
      .toThrow(/re-sends tool/)

    // and the shapes the wire DOES send are still expressible
    expect(() => live(1, 'tool', { tool: 'Bash', summary: 'pytest', ok: null })).not.toThrow()
    expect(() => live(1, 'tool', { ok: true, duration_s: 4.81 }, true)).not.toThrow()
  })

  it('test_no_entry_loses_a_field_a_patch_does_not_carry', async () => {
    // ROUND 2. WEB-FIX-275's QA failed this criterion: the `silence` arm asserted nothing about
    // silence, and its fixture sent `seconds`/`since` — fields no silence ENTRY carries. Those two
    // belong to `lane.state.motion` (socket.schema.json:503-517). A silence entry carries
    // `silent_for_s`, and nothing else.
    //
    // THE DOMAIN IS ENUMERATED FROM THE SERVER'S OWN EMITTERS, not from the schema's prose. The
    // schema's `transcript.patch` sentence names two kinds; `app/agent/transcript.cpp:241` sets
    // `kind = edit ? "file_edit" : "tool"` and `:284` patches whichever it was, so the server
    // patches THREE. All three are covered here. The schema sentence is filed as SRV-FIX-289.
    //
    // WHAT EACH ARM CAN AND CANNOT FAIL ON — written down because two of the three cannot fail on
    // the defect this ticket is about, and a criterion that hides that is the reason round 1 passed
    // its own gate (`_TEMPLATE.md` §1: if the fact is not observable, the ABSENCE IS THE FINDING).
    //
    //   tool       DISCRIMINATES. The call carries {tool, summary, input}; the patch carries
    //              {ok, duration_s, result} and none of the three. `tool` and `summary` are
    //              rendered, so replacing instead of merging is visible on the screen. Red under
    //              the pre-fix `entries[at] = arrived`.
    //   file_edit  CANNOT. The patch re-carries every field this kind RENDERS — path, added,
    //              removed, diff (`transcript.cpp:286-289`) — and the fields it drops (`tool`,
    //              `summary`, `input`) are not drawn: `Transcript.tsx` hardcodes tool="Edit".
    //              So this arm holds under replace as well. It is kept because the day file_edit
    //              renders one of those fields it becomes discriminating for free.
    //   silence    CANNOT, and this is the honest answer to what QA asked for. `silenceMembers`
    //              (`app/agent/session.cpp:72-76`) emits exactly `kind`, `patch` and
    //              `silent_for_s`. The patch carries the entry's only payload field, so replace
    //              and merge produce the identical entry. What it CAN fail on is a client that
    //              drops patch frames — WEB-FIX-145's defect — which would leave the row reading
    //              the first count for ever. That regression is real and this arm names it.
    const app = await mountSession()

    // ── tool: the call, then the outcome, exactly as `transcript.cpp` emits them ──────────────
    await deliver(app, live(1, 'tool', { tool: 'Bash', summary: 'cd core && pytest tests/ -v',
                                         ok: null, duration_s: 0,
                                         input: 'cd core && pytest tests/ -v' }))
    await deliver(app, live(1, 'tool', { ok: true, duration_s: 4.81,
                                         result: '32 passed, 1 failed in 4.81s' }, true))

    const tool = ofKind('tool')
    expect(tool).toHaveTextContent('Bash')
    expect(tool).toHaveTextContent('cd core && pytest tests/ -v')
    expect(tool.getAttribute('data-ok')).toBe('true')

    // ── file_edit: the same two frames for the other tool kind ───────────────────────────────
    await deliver(app, live(2, 'file_edit', { tool: 'Edit', summary: 'core/tasks/build.py',
                                              ok: null, duration_s: 0, path: 'core/tasks/build.py',
                                              added: 0, removed: 0, diff: '' }))
    await deliver(app, live(2, 'file_edit', { ok: true, duration_s: 1.2, result: 'ok',
                                              path: 'core/tasks/build.py', added: 18, removed: 6,
                                              diff: '@@ -40,6 +40,18 @@' }, true))

    const edit = ofKind('file_edit')
    expect(edit).toHaveTextContent('core/tasks/build.py')
    expect(edit).toHaveTextContent('+18')
    expect(edit).toHaveTextContent('6')

    // ── silence: `silent_for_s`, the one field the entry has ─────────────────────────────────
    await deliver(app, live(3, 'silence', { silent_for_s: 0 }))
    expect(ofKind('silence')).toHaveTextContent('0m 00s')

    await deliver(app, live(3, 'silence', { silent_for_s: 240 }, true))
    expect(ofKind('silence')).toHaveTextContent('4m 00s')

    // three entries, not six: a patch completes an entry, it does not append one.
    expect(nodes()).toHaveLength(3)
  })
})

// ── WEB-FIX-278 · a patch whose entry is not drawn yet is HELD, not dropped ────────────────────
//
// `WEB-FIX-275` fixed what the merge does; this is about when the merge is reached. The branch's
// own guard — `if (at < 0) return` — throws the frame away whenever the entry it names is sitting
// in `pending`, buffered behind a gap, rather than in `entries`. The gap then fills, the entry is
// appended exactly as it arrived, and the tool call keeps `ok: null` for ever.
//
// The class is *an early `return` that discards a frame the protocol says is not optional*, and the
// structural answer is that the map already holding out-of-order ENTRIES gains a patch side: the
// only way a patch is lost is now a run change, which is the one case where losing it is correct.

describe('WEB-FIX-278 · a patch is held until the entry it names is placed', () => {
  /** The one row at a `seq`, found by the attribute the component writes for it. */
  function atSeq(seq: number): HTMLElement {
    const found = nodes().filter((n) => n.getAttribute('data-seq') === String(seq))
    if (found.length !== 1) throw new Error(`${found.length} entries at seq ${seq}, expected 1`)
    return found[0]
  }

  it('test_a_patch_for_a_call_still_behind_a_gap_is_applied_when_the_gap_fills', async () => {
    // The QA probe of WEB-FIX-275, verbatim (`reports/WEB-FIX-275/WEB-FIX-275-qa-probe.test.tsx`).
    // Red before the fix: `expected 'null' to be 'true'`.
    const app = await mountSession()
    await deliver(app, live(1, 'prose', { text: 'starting' }))
    // seq 3 arrives before seq 2 — buffered in `pending`
    await deliver(app, live(3, 'tool', { tool: 'Bash', summary: 'cd core && pytest tests/ -v' }))
    // its outcome arrives while it is still buffered
    await deliver(app, live(3, 'tool', { ok: true, duration_s: 4.81 }, true))
    // the gap fills
    await deliver(app, live(2, 'prose', { text: 'filling the gap' }))

    const tool = ofKind('tool')
    expect(tool.getAttribute('data-ok')).toBe('true')
    // and the call's own halves are still there — a held patch completes the entry it names, it
    // does not stand in for it
    expect(tool).toHaveTextContent('Bash')
    expect(tool).toHaveTextContent('cd core && pytest tests/ -v')
    expect(nodes().map((n) => n.getAttribute('data-seq'))).toEqual(['1', '2', '3'])
  })

  it('test_a_patch_for_a_seq_that_never_arrives_is_not_applied_to_a_later_entry', async () => {
    // MANDATORY NEGATIVE. The cheap fix for the case above is to stash every unplaceable patch and
    // apply it to whatever entry turns up next — which settles one tool call with another one's
    // outcome, and that is a worse screen than the blank tick, because it reads as a fact.
    //
    // WHAT THIS ARM CAN AND CANNOT FAIL ON. It cannot fail on the defect the first case names: a
    // dropped patch is applied to nothing, so today it is green. It fails on a fix that holds
    // patches WITHOUT keying them to their entry — proven against that mutant, not asserted from
    // its name.
    const app = await mountSession()
    await deliver(app, live(1, 'prose', { text: 'starting' }))

    // an ORPHAN: seq 9 is past the gap and no frame ever fills it
    await deliver(app, live(9, 'tool', { ok: true, duration_s: 9.99 }, true))

    // the run then goes on, in order, with two calls of its own that are still unsettled
    await deliver(app, live(2, 'tool', { tool: 'Bash', summary: 'npm run build', ok: null }))
    await deliver(app, live(3, 'tool', { tool: 'Read', summary: 'src/main.ts', ok: null }))

    expect(nodes(), 'the orphan patch was drawn as a row of its own').toHaveLength(3)
    expect(atSeq(2).getAttribute('data-ok'), 'seq 2 took the orphan patch').toBe('null')
    expect(atSeq(3).getAttribute('data-ok'), 'seq 3 took the orphan patch').toBe('null')
    expect(atSeq(2)).toHaveTextContent('npm run build')
    expect(atSeq(3)).toHaveTextContent('src/main.ts')
  })

  it('test_a_run_change_discards_the_patches_held_for_the_previous_run', async () => {
    // `seq` is the RUN's — every run numbers its entries from 1 — so a patch held for seq 3 of run
    // A names seq 3 of run B just as well, and a run change that clears `pending` and leaves the
    // patches behind settles a brand new call with a finished run's outcome.
    //
    // Enumerated from `transcript.ts`'s own `isRunChange` reset, which already clears `pending`.
    const app = await mountSession()
    await deliver(app, live(1, 'prose', { text: 'run one' }))
    // run A's seq 3 is patched before its call ever arrives — held
    await deliver(app, live(3, 'tool', { ok: true, duration_s: 4.81 }, true))

    // a SECOND run in the same lane, numbering from 1 again
    await deliver(app, live(1, 'prose', { text: 'run two, one' }, undefined, 'run-2'))
    await deliver(app, live(2, 'prose', { text: 'run two, two' }, undefined, 'run-2'))
    await deliver(app, live(3, 'tool', { tool: 'Bash', summary: 'pytest', ok: null },
                            undefined, 'run-2'))

    expect(nodes()).toHaveLength(3)
    expect(ofKind('tool').getAttribute('data-ok'),
           "run A's held patch settled run B's call").toBe('null')
    expect(ofKind('tool')).toHaveTextContent('pytest')
  })
})
