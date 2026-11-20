import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, fireEvent } from '@testing-library/react'

import { MANIFEST, callsTo, mountApp, settle, ticketDoc, ticketRoutes, type Routes }
  from './helpers'
import { LANE, deliver, docLane, running } from './session'

/**
 * WEB-FEAT-046 · SPEC.md E19 + M10 — the workspace's OWN build and gate command, run from the
 * ticket header.
 *
 * The tool has no opinion about what either command does. Every string here comes out of the
 * manifest and goes onto the wire untouched, which is why the first case asserts the tooltip and
 * the request body against the same declared value: a client that shipped its own default build
 * command would run the wrong one and look right doing it.
 *
 * Two facts drive the control, and keeping them apart is the whole of it:
 *
 *   the MANIFEST (E2) says which of `build` and `gate` this workspace declares. An undeclared
 *     command is ABSENT, never disabled — a greyed `Gate` says "not now" when the truth is "not
 *     ever, until somebody writes it into `.factory/factory.json`", so the note names the key.
 *   the LANE says whether a command is in flight. `M10` carries a `chunk` per burst and one more
 *     frame with `exit_code`, and only that last frame gives the buttons back.
 *
 * The workspace read is the LANE's, not the document's — E19 resolves the command from
 * `record->workspace` (`factory/server/app/lane/command.cpp`), so a button drawn from the ticket
 * file could offer a command the server would refuse.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/**
 * Four workspaces, one per shape E19 can be in. The gate's own fixture repository declares the
 * first two exactly like this (`gate/fixture-repo/.factory/factory.json`), so the browser gate and
 * this file are asserting the same manifest.
 */
const WORKSPACES = {
  core: { path: '.', build: 'make -C core', gate: 'cd core && pytest tests/ -v' },
  edge: { path: 'edge', gate: 'npm --prefix edge test' },
  web: { path: 'web', build: 'npm --prefix web run build' },
  ledger: { path: 'ledger' },
}

const GATE = WORKSPACES.core.gate
const COMMANDS = `/api/lanes/${LANE}/commands`

/** E19's `202`: the id the M10 frames will carry, and the string the manifest declared. */
const accepted = { body: { command_id: 'cmd/07', command: GATE } }

/** M10 — output as it arrives, and once more when it exits. Never both in one frame. */
const command = (over: Record<string, unknown> = {}) => ({
  m: 'command', ts: '2026-07-28T10:00:05Z', seq: null, lane: LANE,
  command_id: 'cmd/07', which: 'gate', ...over,
})

/**
 * The SESSION tab of a ticket whose lane is in `workspace`.
 *
 * The SESSION tab rather than the bare ticket route because §1's claim is that output reaches the
 * TRANSCRIPT: a control asserted on a surface with no transcript on it proves the POST went out
 * and nothing about what a person then sees.
 */
async function mount(workspace: string, over: Routes = {}) {
  const rows = [running({ workspace })]
  const app = await mountApp('#/tickets/TKT-142/session', {
    ...ticketRoutes(ticketDoc({ lane: docLane(rows) }), rows),
    '/api/manifest': { body: { ...MANIFEST, workspaces: WORKSPACES } },
    [COMMANDS]: accepted,
    ...over,
  })
  await settle()
  await settle()
  return app
}

/**
 * The bodies sent to `path`, parsed.
 *
 * Read off the fetch spy rather than through `calls`, which records the url and the verb only. A
 * case that asserted the path alone would pass on `{"which":"build"}` sent from the Gate button.
 */
function sentTo(path: string): unknown[] {
  const spy = globalThis.fetch as unknown as
    { mock: { calls: Array<[RequestInfo | URL, RequestInit | undefined]> } }
  return spy.mock.calls
    .filter(([url]) => new URL(String(url), 'http://factory.test').pathname === path)
    .map(([, init]) => (init?.body === undefined ? null : JSON.parse(String(init.body))))
}

const entry = () => screen.getByTestId('transcript').querySelector('[data-kind="command"]')

describe('E19 · run the workspace\'s declared build or gate command', () => {
  it('test_gate_runs_the_string_the_manifest_declares', async () => {
    const app = await mount('core')

    // ── the label is the word, the tooltip is the command. §1's pre-decision, and the reason a
    //    reader can tell WHICH gate is about to run without opening the manifest.
    const gate = screen.getByTestId('command-gate')
    expect(gate).toHaveTextContent('Gate')
    expect(gate).toHaveAttribute('title', GATE)
    expect(screen.getByTestId('command-build')).toHaveAttribute('title', WORKSPACES.core.build)

    await act(async () => { fireEvent.click(gate) })
    await settle()

    // one request, to E19, naming the command by its manifest key and not by its string
    const posts = callsTo(COMMANDS)
    expect(posts).toHaveLength(1)
    expect(posts[0].method).toBe('POST')
    expect(sentTo(COMMANDS)).toEqual([{ which: 'gate' }])

    // ── and the output arrives on M10, into the transcript, as a `command` entry
    await deliver(app, command({ chunk: '32 passed, 1 failed' }))
    const line = entry()
    expect(line).not.toBeNull()
    expect(line).toHaveTextContent('gate')

    await act(async () => {
      fireEvent.click(line!.querySelector('[data-testid="entry-line"]') as HTMLElement)
    })
    expect(entry()).toHaveTextContent('32 passed, 1 failed')

    // a second burst appends to the SAME entry — one command is one line, however it arrives
    await deliver(app, command({ chunk: ' in 4.81s' }))
    expect(screen.getByTestId('transcript')
      .querySelectorAll('[data-kind="command"]')).toHaveLength(1)
    expect(entry()).toHaveTextContent('32 passed, 1 failed in 4.81s')
  })

  it('test_a_workspace_with_no_gate_says_so_and_does_not_offer_the_button', async () => {
    // `web` declares a build and no gate
    await mount('web')

    expect(screen.getByTestId('command-build')).toHaveAttribute('title', WORKSPACES.web.build)
    // ABSENT, not disabled — and not anywhere else on the surface either
    expect(screen.queryByTestId('command-gate')).toBeNull()
    expect(screen.queryByText('Gate')).toBeNull()
    // a note names the manifest key that would have to exist
    expect(screen.getByTestId('commands-note')).toHaveTextContent('workspaces.web.gate')
    // nothing was sent, and nothing could be
    expect(callsTo(COMMANDS)).toHaveLength(0)

    // ── and a workspace declaring NEITHER offers no button at all, naming both keys
    cleanup()
    await mount('ledger')
    expect(screen.queryByTestId('command-build')).toBeNull()
    expect(screen.queryByTestId('command-gate')).toBeNull()
    const both = screen.getByTestId('commands-note')
    expect(both).toHaveTextContent('workspaces.ledger.build')
    expect(both).toHaveTextContent('workspaces.ledger.gate')
    expect(callsTo(COMMANDS)).toHaveLength(0)
  })

  it('test_a_failing_gate_shows_its_exit_code', async () => {
    const app = await mount('core')
    await act(async () => { fireEvent.click(screen.getByTestId('command-gate')) })
    await settle()

    await deliver(app, command({ chunk: 'E   assert stale is False\n' }))
    // while it runs there is no exit code to show, and the entry does not claim one
    expect(entry()).not.toHaveTextContent('exit')

    await deliver(app, command({ exit_code: 1 }))
    const line = entry()
    expect(line).toHaveTextContent('exit 1')
    // and it is drawn as the failure it is, not as an outcome nobody knows
    expect(line?.getAttribute('data-ok')).toBe('false')

    // the tool reports the number and forms no opinion about it: a 0 is the same entry
    await deliver(app, command({ command_id: 'cmd/08', which: 'build', exit_code: 0 }))
    const lines = Array.from(
      screen.getByTestId('transcript').querySelectorAll('[data-kind="command"]'))
    expect(lines).toHaveLength(2)
    expect(lines[1]).toHaveTextContent('exit 0')
    expect(lines[1].getAttribute('data-ok')).toBe('true')
  })

  it('test_the_button_is_disabled_while_a_command_is_already_running', async () => {
    const app = await mount('core')
    await act(async () => { fireEvent.click(screen.getByTestId('command-gate')) })
    await settle()
    expect(callsTo(COMMANDS)).toHaveLength(1)

    // BOTH of them: one lane runs one command at a time, so a build offered mid-gate is an offer
    // the server would refuse
    expect(screen.getByTestId('command-gate')).toBeDisabled()
    expect(screen.getByTestId('command-build')).toBeDisabled()

    // clicking anyway spawns nothing
    await act(async () => { fireEvent.click(screen.getByTestId('command-gate')) })
    await act(async () => { fireEvent.click(screen.getByTestId('command-build')) })
    await settle()
    expect(callsTo(COMMANDS)).toHaveLength(1)
    expect(sentTo(COMMANDS)).toEqual([{ which: 'gate' }])

    // output is not the end of a command, so it does not give the buttons back
    await deliver(app, command({ chunk: 'collecting … ' }))
    expect(screen.getByTestId('command-gate')).toBeDisabled()
    expect(screen.getByTestId('command-build')).toBeDisabled()

    // the exit is. One frame, no refetch, no timer.
    await deliver(app, command({ exit_code: 0 }))
    expect(screen.getByTestId('command-gate')).not.toBeDisabled()
    expect(screen.getByTestId('command-build')).not.toBeDisabled()

    // and now a second command CAN be started
    await act(async () => { fireEvent.click(screen.getByTestId('command-build')) })
    await settle()
    expect(sentTo(COMMANDS)).toEqual([{ which: 'gate' }, { which: 'build' }])
  })

  /** §1's two remaining rules. Neither is one of the four names, and both can regress. */
  it('with_no_lane_there_is_nothing_to_run_and_no_control_at_all', async () => {
    // A command runs in a worktree, and a ticket nobody has claimed a lane for has none — so the
    // control is HIDDEN rather than disabled. There is nothing a person could do about a disabled
    // Gate here except claim a lane, which is a different control on a different surface.
    const app = await mountApp('#/tickets/TKT-142/session',
      { ...ticketRoutes(ticketDoc({ lane: null }), []),
        '/api/manifest': { body: { ...MANIFEST, workspaces: WORKSPACES } } })
    await settle()
    await settle()

    screen.getByTestId('ticket-header')
    expect(screen.queryByTestId('commands')).toBeNull()
    expect(screen.queryByTestId('command-gate')).toBeNull()
    expect(screen.queryByTestId('command-build')).toBeNull()
    // not even the note: a missing manifest key is not what is stopping anybody here
    expect(screen.queryByTestId('commands-note')).toBeNull()
    expect(app.socket().frames().some((f) => f.m === 'command')).toBe(false)
  })

  it('a_404_not_declared_names_the_manifest_key_that_would_have_to_exist', async () => {
    // The manifest this client read and the one the server resolved can disagree — a hand-edited
    // `.factory/factory.json` is a file a person changes while a lane is open. E19's refusal
    // carries `detail.field`, and repeating it is the difference between "it did not work" and
    // "write this key".
    await mount('core', {
      [COMMANDS]: {
        status: 404,
        body: { error: { code: 'not_declared',
                         message: 'this workspace declares no gate command',
                         detail: { field: 'workspaces.core.gate', workspace: 'core' } } },
      },
    })

    await act(async () => { fireEvent.click(screen.getByTestId('command-gate')) })
    await settle()

    expect(screen.getByTestId('command-error')).toHaveTextContent('workspaces.core.gate')
    // and the refusal is not a running command: the buttons come back
    expect(screen.getByTestId('command-gate')).not.toBeDisabled()
    expect(screen.getByTestId('transcript')
      .querySelectorAll('[data-kind="command"]')).toHaveLength(0)
  })
})
