import { useCallback, useEffect, useRef, useState } from 'react'

import { useFactory } from './factory'
import type { LaneCommand } from './socket'
import { isRunChange } from '../vocabulary'
import type { ServerFrame, SnapshotFrame, TranscriptFrame } from '../types/socket'

/**
 * One lane's transcript — SPEC.md §4.6's data half.
 *
 * There is no HTTP read here on purpose. `E8` exists for a historical run in the RUNS tab, but the
 * LIVE column already has a cheaper answer that `SRV-FEAT-005` built and `services/subscribe.ts`
 * reserved for this ticket by name: the `lane` scope's `M1 snapshot` carries `E8`'s body, and the
 * `M5 transcript` frames that follow it carry everything after. So the surface subscribes and then
 * listens, and there is not one fetch, not one interval and not one refetch in this file.
 *
 * Everything below is one rule from §4.6 made mechanical:
 *
 *   "Entries carry a monotonic `seq` per run; the client applies them IN ORDER and requests a
 *    resync on a gap."
 *
 * A frame ahead of the sequence is BUFFERED rather than rendered, because an entry drawn next to
 * the wrong neighbour is a lie no reader can detect, while a short pause is one they never notice.
 */

export interface Entry {
  seq: number
  ts?: string
  kind: string
  [key: string]: unknown
}

export interface Transcript {
  entries: Entry[]
  /** the last CONTIGUOUS sequence held — what a `resume` would ask from */
  lastSeq: number
  runId: string | null
}

const EMPTY: Transcript = { entries: [], lastSeq: 0, runId: null }

/** A `M5` frame flattened into the shape a snapshot entry already has. One seam, one conversion. */
function entryOf(frame: TranscriptFrame): Entry {
  return { ...frame.entry, seq: frame.seq, ts: frame.ts }
}

/** The lane scope's snapshot, and nothing else's — a `{ticket}` scope's carries no entries. */
function isLaneSnapshot(frame: ServerFrame, lane: string): frame is SnapshotFrame {
  return frame.m === 'snapshot' && 'lane' in frame.scope && frame.scope.lane === lane
}

/**
 * WEB-FEAT-046 · M10 — one command the operator started (E19), as its output arrives.
 *
 * A command is not part of the agent's run: it is a separate process a person asked for, so it
 * carries no `seq` (`seq: null` on every `command` frame) and it is not in the run's sequence log.
 * That leaves it outside every rule above — nothing to order it against, nothing to resync — so it
 * is accumulated here, keyed by `command_id`, and appended after the run's own entries. Two
 * consequences, both deliberate:
 *
 *   the entry GROWS. `chunk` is raw output and a build that prints a progress bar sends dozens of
 *     frames for one line, so appending is the only reading of §4.6 that does not draw a hundred
 *     entries for one gate. The exit code lands on the same entry when the process exits.
 *   the SEQ IS NEGATIVE, counted down from -1. It is a React key and a `data-seq`, never compared
 *     with the run's, and a negative number cannot collide with a log the server numbers from 1.
 */
/** The shape `Transcript.tsx`'s `command` case already renders — nothing here is new drawing. */
function commandEntries(held: Map<string, LaneCommand>): Entry[] {
  const out: Entry[] = []
  for (const [id, c] of held) {
    out.push({
      seq: c.seq, ts: c.ts, kind: 'command', command_id: id, which: c.which, output: c.output,
      // Collapsed, a command reads `gate  running` and then `gate  exit 1`. The tool reports the
      // number and forms no opinion about it; `ok` is the drawing of that number, not a verdict
      // on the repository's gate.
      summary: c.exitCode === undefined ? 'running' : `exit ${c.exitCode}`,
      ...(c.exitCode === undefined ? {} : { exit_code: c.exitCode, ok: c.exitCode === 0 }),
    })
  }
  return out
}

export function useTranscript(lane: string | null): Transcript {
  const { onFrame, send, held: heldByTheSocket } = useFactory()
  const [view, setView] = useState<Transcript>(EMPTY)

  // The authority, so two frames in one tick both see what the first did. `view` is the copy
  // React renders; nothing reads `held` during a render.
  const held = useRef<Transcript>(EMPTY)
  const pending = useRef(new Map<number, Entry>())
  /**
   * The patches whose entry is not on screen yet — WEB-FIX-278.
   *
   * `pending` holds an entry that arrived before its turn; this holds the completion of an entry
   * that has not had its turn either. Both are keyed by the same `seq`, and that keying is the
   * whole of the rule: a patch belongs to ONE entry and to no other, so an unplaceable one waits
   * for its own `seq` and never settles the next entry to turn up.
   */
  const patches = useRef(new Map<number, Entry>())
  const asked = useRef(-1)

  // M10's entries, RENDERED from the socket module's per-lane store rather than from one of this
  // component's own (`WEB-FIX-072`). The module is what a mount is seeded from and what outlives
  // it; a second copy here is a copy that empties every time a person leaves the tab, and nothing
  // on the wire can replay a command — `seq: null`, outside the run's log, no snapshot, no resume.
  const [commands, setCommands] = useState<Entry[]>([])

  const commit = useCallback((next: Transcript) => { held.current = next; setView(next) }, [])

  /** An entry as it is PLACED, carrying whatever patch arrived for it while it was not here. */
  const placed = useCallback((entry: Entry): Entry => {
    const patch = patches.current.get(entry.seq)
    if (!patch) return entry
    patches.current.delete(entry.seq)
    return { ...entry, ...patch }
  }, [])

  /**
   * A NEW SURFACE, seeded from what this client already knows — WEB-FIX-051.
   *
   * This used to reset to `EMPTY` unconditionally, which is correct about the component and wrong
   * about the client: the socket module's lane map outlives every mount and still holds every
   * entry. Resetting to empty and then asking a live connection what was missed got the honest
   * answer *nothing was missed*, and the column stayed blank until the next frame's seq gap
   * triggered a resync — the user's "send a message and the errors come back".
   *
   * `lastSeq` and `runId` come from the same place as the entries. Taking the entries and leaving
   * `lastSeq` at 0 would make the very next frame look like a gap and fire a resync per remount.
   */
  useEffect(() => {
    const known = lane ? heldByTheSocket(lane) : null
    const seeded: Transcript = known && known.entries.length
      ? {
          entries: known.entries.slice().sort((a, b) => a.seq - b.seq) as Entry[],
          lastSeq: known.lastSeq,
          // From the socket module's own record, not from an entry. An ENTRY has no `run_id` —
          // only the `M5` frame around it does — so reading one here returned `undefined` every
          // time and a new run in this lane was dropped as a replay (QA, F2).
          runId: known.runId,
        }
      : EMPTY
    held.current = seeded
    pending.current = new Map()
    patches.current = new Map()
    asked.current = -1
    setView(seeded)
    // …and the commands this client has already seen in this lane, which is the whole ticket.
    setCommands(known ? commandEntries(known.commands) : [])
  }, [lane, heldByTheSocket])

  useEffect(() => {
    if (!lane) return undefined
    return onFrame((frame: ServerFrame) => {
      // The only frame that REPLACES state. `replaces_tail` is the server saying its log cannot
      // cover the gap, and obeying it is the whole point of the decision being made there.
      if (isLaneSnapshot(frame, lane)) {
        const data = frame.data as { entries?: Entry[]; next_after_seq?: number; run_id?: string }
        const entries = (data.entries ?? []).slice().sort((a, b) => a.seq - b.seq)
        pending.current = new Map()
        // …and the patches held for entries this snapshot has just replaced. The log a snapshot is
        // built from keeps the patch line beside the call line at the same `seq`
        // (`SequenceLog::appendAt`), so a snapshot re-supplies whatever a held patch carried; and a
        // patch kept across a state the server says REPLACES the tail is a patch aimed at a `seq`
        // that may now be a different entry.
        patches.current = new Map()
        asked.current = -1
        commit({
          entries,
          lastSeq: data.next_after_seq ?? (entries.length ? entries[entries.length - 1].seq : 0),
          runId: data.run_id ?? null,
        })
        return
      }

      // M10 — E19's output, outside the run's sequence entirely. See `commandEntries` above.
      if (frame.m === 'command') {
        // Another lane's command is another lane's business. The module has already filed it
        // under that lane; this surface simply does not redraw for it.
        if (frame.lane !== lane) return
        // `apply()` in the socket module ran before this callback — same frame, one line up in
        // `onmessage` — so the store already holds this chunk. Nothing is accumulated twice.
        const known = heldByTheSocket(lane)
        if (known) setCommands(commandEntries(known.commands))
        return
      }

      if (frame.m !== 'transcript' || frame.lane !== lane) return
      const arrived = entryOf(frame)
      let current = held.current

      /**
       * A NEW RUN IN THE SAME LANE, and the reason every rule below has to be told about it.
       *
       * `seq` is the RUN's, not the lane's: every run numbers its entries from 1. So the second
       * run in a lane arrives at seq 1 while this column still holds the first run's tail, and
       * the rule two lines down — `arrived.seq <= lastSeq`, a replay of what we already hold —
       * is then true of every entry the new run will ever send. The column freezes on a finished
       * run's last line while the lane goes on reporting `producing`, which is the worst screen
       * this surface can draw: a person cannot tell it from an agent that has stopped.
       *
       * The server hands the tail over with a `replaces_tail` snapshot, and that is the fix. This
       * is the second lock on the same door, and it is not redundant: it costs one comparison,
       * and the cost of the two halves disagreeing is a transcript nobody can read with nothing
       * on screen to say so.
       */
      if (isRunChange(frame.run_id, current.runId)) {
        pending.current = new Map()
        // The ONE case where losing a patch is correct, and the reason it has to be said out loud:
        // `seq` is the RUN's, so a patch held for seq 3 of the run that just ended names seq 3 of
        // the run that just started exactly as well, and would settle a brand new call with a
        // finished run's outcome.
        patches.current = new Map()
        asked.current = -1
        current = { entries: [], lastSeq: 0, runId: frame.run_id }
        held.current = current
      }

      // A `tool` gaining its result: its own frame, the same seq, `patch:true`. §1's only
      // permitted re-render of an entry that already arrived.
      //
      // MERGED over the entry, never substituted for it. `socket.schema.json` on `transcript.patch`
      // — "a tool entry gaining its result, and a §4.6 `silence` rule gaining the length the gap
      // really was … **Both are one entry becoming complete**". A patch frame carries the field it
      // adds and nothing else: `{seq, kind, ok}` for a finished tool call. Assigning it over the
      // entry threw away `tool` and `summary`, so every completed tool call rendered as a tick and
      // a duration with no name — eleven of them in a row on the first live lane anybody watched
      // (`WEB-FIX-275`).
      //
      // HELD when it cannot be placed, never dropped (`WEB-FIX-278`). An entry buffered in
      // `pending` behind a gap is not in `entries`, so `findIndex` fails for it exactly as it does
      // for one that has not arrived at all — and the `return` that used to stand here threw the
      // completion away with no record, so the gap filled and the call was appended for ever
      // unsettled. A patch the protocol does not make optional is not a frame this client may lose.
      if (frame.patch) {
        const at = current.entries.findIndex((e) => e.seq === arrived.seq)
        if (at < 0) {
          const before = patches.current.get(arrived.seq)
          patches.current.set(arrived.seq, before ? { ...before, ...arrived } : arrived)
          return
        }
        const entries = current.entries.slice()
        entries[at] = { ...entries[at], ...arrived }
        commit({ ...current, entries, runId: frame.run_id ?? current.runId })
        return
      }

      if (arrived.seq <= current.lastSeq) return          // a replay of what we already hold

      if (arrived.seq > current.lastSeq + 1) {
        pending.current.set(arrived.seq, arrived)
        // Asked ONCE per gap, not once per frame: a burst arriving out of order must not become
        // a burst of resumes.
        if (asked.current !== current.lastSeq) {
          asked.current = current.lastSeq
          send({ m: 'resume', lane, after_seq: current.lastSeq })
        }
        return
      }

      const entries = current.entries.concat(placed(arrived))
      let last = arrived.seq
      for (;;) {
        const next = pending.current.get(last + 1)
        if (!next) break
        pending.current.delete(last + 1)
        entries.push(placed(next))
        last += 1
      }
      asked.current = -1
      commit({ entries, lastSeq: last, runId: frame.run_id ?? current.runId })
    })
  }, [onFrame, send, commit, placed, lane])

  // The run's entries, then the commands a person started, and no allocation at all on the
  // overwhelmingly common path where nobody has run one. `lastSeq` stays the RUN's — a `resume`
  // must never ask a server to replay something that was never in its log.
  if (commands.length === 0) return view
  return { ...view, entries: view.entries.concat(commands) }
}
