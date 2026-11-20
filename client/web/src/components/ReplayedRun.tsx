import type { ReactNode } from 'react'

import { EmptyBlock, Placeholder } from './Blocks'
import { tintFor, tone } from './Chips'
import { outcomeOf } from './RunsTab'
import { clearReplay, type Replay } from '../services/runs'
import type { RunRow, TranscriptEntry } from '../types/api'

/**
 * A finished run, read back — SPEC.md §4.10, second paragraph.
 *
 * This is the whole of what the SESSION tab does differently when a historical run is selected,
 * kept in one component so that `WEB-FEAT-014`'s tab is two lines:
 *
 *     const replay = useReplayedRun()
 *     if (replay) return <ReplayedRun replay={replay} renderEntry={Entry} />
 *
 * The live column and the composer are in the branch not taken, which is why there is no second
 * composer to disable and no live stream to accidentally merge into. `renderEntry` is that
 * ticket's own entry renderer — §4.6's table is its contract, not this ticket's, and a replay that
 * drew entries its own way would be a second vocabulary for the same lines.
 *
 * **Nothing here sends anything.** §1: "A replayed run is read-only, and the screen says so." A
 * control that cannot do what its label promises is worse than no control, so the composer is
 * present, disabled, and says why — a person who does not know they are reading history is a
 * person about to type into it.
 */
export function ReplayedRun({ replay, renderEntry }: {
  replay: Replay
  renderEntry?: (entry: TranscriptEntry) => ReactNode
}) {
  const outcome = outcomeOf({ outcome: replay.outcome, run_id: replay.runId } as RunRow,
                            replay.proven ? replay.runId : undefined)

  return (
    <div className="replay" data-testid="replayed-run" data-readonly="true">
      <div className="replay-banner" data-testid="replay-banner"
        style={tone(outcome.tone, tintFor(outcome.tone))}>
        <span className="chip-dot" style={tone(outcome.tone)} />
        <span className="replay-what">
          reading run #{replay.n} · {outcome.label} · read only
        </span>
        <button type="button" className="replay-exit" data-testid="replay-exit"
          onClick={() => clearReplay()}>back to the live run</button>
      </div>

      <div className="replay-entries" data-testid="replay-entries">
        <ReplayBody replay={replay} renderEntry={renderEntry} />
      </div>

      <div className="replay-composer" data-testid="replay-composer">
        <textarea className="composer-box" rows={2} disabled readOnly value=""
          placeholder="This run has finished." />
        <div className="replay-hint">
          <span>reading run #{replay.n} · nothing can be sent into a finished run</span>
          <button type="button" className="composer-send" disabled>Send</button>
        </div>
      </div>
    </div>
  )
}

function ReplayBody({ replay, renderEntry }: {
  replay: Replay
  renderEntry?: (entry: TranscriptEntry) => ReactNode
}) {
  if (replay.loading) return <Placeholder what="reading the run…" />

  // E8's 404 — the run happened, its row is still in E7, and its log has been cleaned. §2 is
  // explicit that this costs history and never correctness, so it says so instead of pretending
  // the run never ran.
  if (replay.missing !== undefined) {
    return (
      <EmptyBlock title="THIS RUN'S TRANSCRIPT IS GONE" testId="replay-missing">
        Run #{replay.n} happened, and its log is no longer at <code>{replay.missing}</code>.
      </EmptyBlock>
    )
  }

  if (replay.entries.length === 0) {
    return (
      <EmptyBlock title="NOTHING WAS RECORDED" testId="replay-empty">
        Run #{replay.n} produced no transcript entries.
      </EmptyBlock>
    )
  }

  return (
    <>
      {replay.entries.map((entry) => (
        <div key={entry.seq}>
          {renderEntry ? renderEntry(entry) : <PlainEntry entry={entry} />}
        </div>
      ))}
      {!replay.complete && (
        <div className="replay-more" data-testid="replay-truncated">
          This run is longer than one page; these are its first {replay.entries.length} entries.
        </div>
      )}
    </>
  )
}

/**
 * What an entry looks like when nobody passed a renderer.
 *
 * Deliberately the plainest thing that is still readable, and deliberately NOT a second
 * implementation of §4.6's table: that table is `WEB-FEAT-014`'s, and two renderers for one
 * vocabulary is exactly the drift §4.11 exists to prevent. This is what the pane shows before
 * that ticket lands, so a replay is never a blank rectangle.
 */
function PlainEntry({ entry }: { entry: TranscriptEntry }) {
  const text = (entry as { text?: string }).text
  return (
    <div className="replay-line" data-testid="replay-line">
      <span className="replay-kind">{entry.kind}</span>
      {text ? <span className="replay-text">{text}</span> : null}
    </div>
  )
}
