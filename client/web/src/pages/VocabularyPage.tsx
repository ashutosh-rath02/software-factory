import { CaretRight, Check, StopCircle, WarningCircle } from '@phosphor-icons/react'

import { EmptyBlock, ReconnectBlock } from '../components/Blocks'
import { AXIS_ORDER, AXIS_TITLES, tone } from '../components/Chips'
import { LaneCard } from '../components/LaneCard'
import { StageStrip } from '../components/StageStrip'
import { useFactory } from '../services/factory'
import {
  AXES, DELIVERY, SAMPLE_LANES, SAMPLE_SILENCE_S, TONE, formatSilence, laneStateLine, laneTone,
} from '../vocabulary'

/**
 * 4.11 — the design's own reference surface, shipped as part of the product.
 *
 * "It is the thing an implementation is gated against, so it ships as part of the product rather
 * than as a document." Every value on it comes from `src/vocabulary.ts` — the same table the
 * chips, the dots, the row marks and the lane card read — because a vocabulary sheet maintained
 * separately from the components it documents is a vocabulary sheet that will lie.
 *
 * E2 is the only request it makes, and only for the stage names in the sequence sample. It
 * renders offline, and says so by simply working.
 */
export function VocabularyPage() {
  const { manifest } = useFactory()
  const stages = manifest?.stages ?? []
  const silence = formatSilence(SAMPLE_SILENCE_S)

  return (
    <div className="vocab" data-testid="surface">
      <div className="vocab-inner">

        <div>
          <div className="vocab-label">STATE · THREE AXES, NEVER FLATTENED</div>
          <div className="axes">
            {AXIS_ORDER.map((axis) => (
              <div className="axis" key={axis} data-testid={`axis-${axis}`}>
                <div className="axis-name">{AXIS_TITLES[axis]}</div>
                <div className="axis-values">
                  {AXES[axis].map((value) => (
                    <div className="axis-value" key={value.value} data-testid="axis-value">
                      <span className="axis-dot" style={tone(value.tone)} />
                      <span className="axis-label" style={tone(value.tone)}>
                        {value.value === 'silent' ? `${value.label} ${silence}` : value.label}
                      </span>
                      <span className="axis-note">{value.note}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="vocab-note">
            Two combinations are alarms, not statuses:{' '}
            <span className="vocab-mono" style={tone(TONE.silent)}>running + silent</span> and{' '}
            <span className="vocab-mono" style={tone(TONE.claim)}>finished + claimed, no proof</span>.
            Both carry a border; nothing else does.
          </div>
        </div>

        <div>
          <div className="vocab-label">DELIVERY · WHAT ACTUALLY HAPPENED</div>
          <div className="vocab-delivery">
            {DELIVERY.map((value) => (
              <div className="axis-value" key={value.value} data-testid="delivery-value"
                data-state={value.value}>
                <span className="axis-dot" style={tone(value.tone)} />
                <span className="axis-label" style={tone(value.tone)}>{value.label}</span>
                <span className="axis-note">{value.note}</span>
              </div>
            ))}
          </div>
          <div className="vocab-note">
            Derived from git and the process table, never from a document&rsquo;s{' '}
            <span className="vocab-mono">status</span> field — which is a claim written by a
            session that may not have survived to write it truthfully. Where the two disagree the
            row shows this one and says the document disagrees.{' '}
            <span className="vocab-mono" style={tone(TONE.crash)}>stalled</span> and{' '}
            <span className="vocab-mono" style={tone(TONE.claim)}>unshipped</span> need a person
            and carry a border; the rest do not.
          </div>
        </div>

        <div>
          <div className="vocab-label">LANE CARD · SIX STATES</div>
          <div className="lane-cards">
            {/* The state LINE and the colour come from `laneStateLine` and `laneTone` — the two
                functions the rail calls — so the sheet cannot come to disagree with the screen it
                documents. Only the numbers are samples. */}
            {SAMPLE_LANES.map((lane) => (
              <LaneCard key={lane.name} name={lane.name} ticket={lane.ticket}
                state={laneStateLine(lane.state)} toneValue={laneTone(lane.state)}
                sub={lane.sub || undefined} stage={lane.stage} pct={lane.pct}
                elapsed={lane.elapsed} cost={lane.cost} alarm={lane.alarm} />
            ))}
          </div>
        </div>

        <div>
          <div className="vocab-label">TRANSCRIPT LINES</div>
          <div className="samples">
            <div className="sample sample-top">
              <span className="sample-key">AGENT PROSE</span>
              <span className="sample-prose">
                Full contrast, sans, wide leading. The only thing in the transcript that reads
                like prose.
              </span>
            </div>
            <div className="sample">
              <span className="sample-key">TOOL CALL</span>
              <span className="sample-tool">
                <CaretRight size={11} />
                <Check size={11} color="var(--st-run)" />
                <span className="sample-tool-name">Bash</span>
                cd core &amp;&amp; pytest tests/ -v
              </span>
            </div>
            <div className="sample">
              <span className="sample-key">TOOL RESULT</span>
              <span className="sample-result">32 passed, 1 failed in 4.81s</span>
            </div>
            <div className="sample">
              <span className="sample-key">ERROR</span>
              <span className="sample-error">AssertionError: expected rebuild, got cache hit</span>
            </div>
            <div className="sample">
              <span className="sample-key">HUMAN</span>
              <span className="sample-human">Distinct panel, accent edge, and a delivery mark.</span>
            </div>
            <div className="sample">
              <span className="sample-key">TURN</span>
              <span className="sample-turn-rule" />
              <span className="sample-turn">turn 4 · 3m 12s · $0.41</span>
              <span className="sample-turn-rule-grow" />
            </div>
          </div>
        </div>

        <div>
          <div className="vocab-label">COMPOSER · SEND, HELD, STOP, CONTINUE</div>
          <div className="composer-samples">
            <div className="held-block">
              <WarningCircle size={15} color="var(--st-silent)" />
              <div style={{ flex: 1 }}>
                <div className="held-title">NOT PROCESSED · AGENT IS MID-TURN</div>
                <div className="held-body">
                  Held, not lost. The rectangle sits under the message it belongs to.
                </div>
              </div>
              <div className="held-aside">send again to interrupt</div>
            </div>
            <div className="stopped-block">
              <StopCircle size={15} color="var(--t2)" />
              <div style={{ flex: 1 }}>
                <div className="stopped-title">STOPPED BY YOU · turn 4 · 41s · $0.86</div>
                <div className="held-body">
                  What already ran stays on disk. The session stays alive.
                </div>
              </div>
              <div className="stopped-aside">revise and send to continue</div>
            </div>
            <div className="composer-note">
              One control, three jobs. Idle it says Send. Mid-turn it becomes Stop, in the crash
              colour, exactly where your hand already is. A message sent while the agent is silent
              posts as held with the rectangle above; sending again interrupts and delivers
              everything held, in order. Nothing is silently dropped and nothing is rolled back
              behind your back.
            </div>
          </div>
        </div>

        <div className="vocab-pair">
          <div>
            <div className="vocab-label">STAGE SEQUENCE</div>
            <div className="stage-box">
              <StageStrip stages={stages} active={stages.length > 2 ? 2 : -1} />
              <div className="stage-note">
                Length and names come from the repository. Done stages are outlined, the active
                one is tinted, the rest are ghosts.
              </div>
            </div>
          </div>
          <div>
            <div className="vocab-label">EMPTY · ERROR</div>
            <div className="composer-samples">
              <EmptyBlock title="NO LANE ASSIGNED">
                Nothing has run on this ticket yet. Start a lane to begin.
              </EmptyBlock>
              <ReconnectBlock />
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
