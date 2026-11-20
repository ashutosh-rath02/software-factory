import { useCallback, useEffect, useRef, useState } from 'react'

import { detailField, runCommand } from '../services/http'
import { useFactory } from '../services/factory'
import type { Lane, Manifest } from '../types/api'
import '../styles/commands.css'

/**
 * SPEC.md E19 + M10 — `Build` and `Gate`, in the ticket header beside the stage strip.
 *
 * The two commands a repository declares about itself, and the only place in this client that asks
 * the tool to run something that is not an agent. Everything about them comes from
 * `.factory/factory.json`:
 *
 *   WHETHER a button exists is `workspaces.<name>.build` / `.gate` being declared. Undeclared is
 *     ABSENT, never disabled — greying a `Gate` out says "not now" when the truth is "not until
 *     somebody writes that key", so the note says which key, and a person can go and write it.
 *   WHAT it runs is the manifest's string, and this component never sends it. E19 takes
 *     `{"which":"gate"}` and resolves the string server-side from the LANE's workspace, which is
 *     why the tooltip is the only place the string appears at all: it is there so a reader knows
 *     what is about to happen, not so a client can choose.
 *   WHOSE workspace is the LANE's (`app/lane/command.cpp` reads `record->workspace`), not the
 *     ticket document's. A button drawn from the document would offer, on a lane claimed for a
 *     different workspace, a command the server would refuse.
 *
 * And with no lane there is no control at all. A command runs in a worktree; a disabled `Gate` on
 * an unclaimed ticket is an offer whose only remedy is a different control on a different surface.
 */

type Which = 'build' | 'gate'

/** Both, in this order, everywhere — the order they are declared in and drawn in. */
const WHICH: readonly Which[] = ['build', 'gate']
const LABEL: Record<Which, string> = { build: 'Build', gate: 'Gate' }

/**
 * The string this workspace declares for `which`, or nothing.
 *
 * `undefined` means the key is ABSENT; an empty string means it is declared empty, and the two are
 * different manifests to the server (`manifest::Workspace::hasGate`). So the test is on the type
 * and never on truthiness: a workspace declaring `"gate": ""` gets a button that runs it and finds
 * out, which is the answer E19 would give.
 */
function declaredCommand(manifest: Manifest | undefined, workspace: string,
                         which: Which): string | undefined {
  const entry = manifest?.workspaces?.[workspace]
  if (typeof entry !== 'object' || entry === null) return undefined
  const value = (entry as Record<string, unknown>)[which]
  return typeof value === 'string' ? value : undefined
}

export function CommandButtons({ lane }: { lane?: Lane }) {
  const { manifest, onFrame } = useFactory()
  const name = lane?.lane
  const workspace = lane?.workspace ?? ''

  // `command_id` → has it exited. One lane runs one command at a time, so this is at most a couple
  // of rows — and it is a MAP rather than a boolean because the exit can arrive before this
  // client's own `202` does: one worker hop for a command that prints nothing and returns. A
  // command already marked exited is never un-exited by the response that started it.
  const seen = useRef(new Map<string, boolean>())
  const [running, setRunning] = useState(false)
  const [starting, setStarting] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const mark = useCallback((id: string, exited: boolean) => {
    seen.current.set(id, exited || seen.current.get(id) === true)
    setRunning(Array.from(seen.current.values()).some((done) => !done))
  }, [])

  useEffect(() => {
    seen.current = new Map()
    setRunning(false)
    setStarting(false)
    setProblem(null)
  }, [name])

  // M10 is the only thing that ends a command. There is no poll for an exit code and no timeout: a
  // gate that takes eleven minutes is a gate that takes eleven minutes.
  useEffect(() => {
    if (!name) return undefined
    return onFrame((frame) => {
      if (frame.m !== 'command' || frame.lane !== name) return
      mark(frame.command_id, frame.exit_code !== undefined)
    })
  }, [name, onFrame, mark])

  const run = useCallback(async (which: Which) => {
    if (!name) return
    setStarting(true)
    setProblem(null)
    const answer = await runCommand(name, which)
    setStarting(false)
    if (!answer.ok) {
      // The manifest this client read and the one the server resolved can disagree — the file is
      // hand-written and a person may edit it with a lane open. `detail.field` is the key that
      // would have to exist, and repeating it is the difference between "that failed" and
      // "write this line".
      const field = detailField(answer)
      setProblem(field
        ? `nothing ran · the manifest would have to declare ${field}`
        : answer.error?.message ?? `the ${which} command could not be started`)
      return
    }
    mark(answer.data.command_id, false)
  }, [name, mark])

  if (!name || !manifest) return null

  const offered = WHICH.filter((w) => declaredCommand(manifest, workspace, w) !== undefined)
  const missing = WHICH.filter((w) => declaredCommand(manifest, workspace, w) === undefined)
  const busy = starting || running

  return (
    <div className="cmd-row" data-testid="commands" data-busy={String(busy)}>
      {offered.map((which) => (
        <button key={which} type="button" className="cmd-button" data-which={which}
          data-testid={`command-${which}`} disabled={busy}
          title={declaredCommand(manifest, workspace, which)}
          onClick={() => { void run(which) }}>
          {LABEL[which]}
        </button>
      ))}
      {missing.length > 0 && (
        <span className="cmd-note" data-testid="commands-note">
          {`no ${missing.join(' or ')} declared · `}
          {missing.map((w) => `workspaces.${workspace}.${w}`).join(' · ')}
        </span>
      )}
      {problem !== null && (
        <span className="cmd-error" data-testid="command-error">{problem}</span>
      )}
    </div>
  )
}
