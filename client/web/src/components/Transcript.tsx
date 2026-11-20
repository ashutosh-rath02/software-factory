import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  CaretDown, CaretRight, Check, Circle, Prohibit, StopCircle, WarningCircle, X,
} from '@phosphor-icons/react'

import { tone } from './Chips'
import { TONE, formatCost, formatElapsed, formatSilence } from '../vocabulary'
import type { Entry } from '../services/transcript'

/**
 * SPEC.md §4.6 — the transcript: a scrolling column, max 900 px, of typed entries.
 *
 * Every kind in §4.6's table renders, and the shapes are the design's own vocabulary sheet rather
 * than anything invented here (`design/Ticket.dc.html` for the column and `:494-522`
 * for the sheet's line samples). Five kinds — `ended`, `killed`, `permission_decision`,
 * `permission_request` and `isolation_refusal` — have no sample line in the design; §4.6's table
 * says which drawn shape each of them takes, so each takes it and nothing is invented. The
 * delivery report says so out loud rather than leaving it to be discovered.
 *
 * Two rules run through the whole file:
 *
 *   APPEND-ONLY. An entry is never re-rendered after it arrives, except a `tool` gaining its
 *     result — which is its own frame with the same `seq` and `patch:true`, applied in
 *     `services/transcript.ts`. Expansion is a `useState` inside the entry, so it belongs to the
 *     entry and cannot be reset by anything that happens above it (F12).
 *   NOTHING ON A TIMER. There is no interval in this file and no fetch. The column moves because
 *     a frame arrived, and for no other reason.
 */

/** `4.81` → `4.8s`, and a minute-long command in the shape the rest of the product uses. */
function duration(seconds: unknown): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return ''
  return seconds < 60 ? `${seconds.toFixed(1)}s` : formatElapsed(seconds)
}

const text = (entry: Entry, key = 'text'): string =>
  typeof entry[key] === 'string' ? (entry[key] as string) : ''

const number = (entry: Entry, key: string): number =>
  typeof entry[key] === 'number' ? (entry[key] as number) : 0

/** `10:00:00` off the entry's own ISO stamp — the design puts a time on `human` and on `error`. */
function clockOf(entry: Entry): string {
  const at = Date.parse(String(entry.ts ?? ''))
  if (Number.isNaN(at)) return ''
  const d = new Date(at)
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((v) => String(v).padStart(2, '0')).join(':')
}

/** What answering a permission request would do — the slot `WEB-FEAT-018` fills (F22, §8 D4). */
export type Decide = (requestId: string, decision: 'once' | 'always' | 'deny') => void

export function Transcript({ entries, onDecide }: { entries: Entry[]; onDecide?: Decide }) {
  const column = useRef<HTMLDivElement>(null)
  // Following the tail is right when you are reading the tail and wrong when you are reading
  // anything above it. The reader's position is a fact about the reader, so it lives in a ref and
  // never causes a render of its own.
  const atBottom = useRef(true)

  const onScroll = useCallback(() => {
    const el = column.current
    if (el) atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }, [])

  const count = entries.length
  useEffect(() => {
    const el = column.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [count])

  return (
    <div className="transcript" data-testid="transcript" ref={column} onScroll={onScroll}>
      <div className="transcript-column">
        {entries.map((entry) => (
          <TranscriptEntry key={entry.seq} entry={entry} onDecide={onDecide} />
        ))}
      </div>
    </div>
  )
}

/**
 * One entry, and the only place a kind becomes a shape.
 *
 * `data-kind` is on every one of them because the alternative — asserting on a class name that
 * happens to be there — is an assertion that passes when the wrong branch renders.
 */
export function TranscriptEntry({ entry, onDecide }: { entry: Entry; onDecide?: Decide }) {
  const [expanded, setExpanded] = useState(false)
  const kind = entry.kind
  const toggle = useCallback(() => { setExpanded((open) => !open) }, [])

  const wrap = (children: ReactNode, extra: Record<string, string> = {}) => (
    <div className="entry" data-testid="entry" data-kind={kind} data-seq={entry.seq} {...extra}>
      {children}
    </div>
  )

  switch (kind) {
    // ── the design's accent-edged panel: YOU, the time, and a delivery mark (:207-217)
    case 'human': {
      const mark = text(entry, 'mark')
      return wrap(
        <div className="entry-human">
          <div className="entry-human-head">
            <span className="entry-you">YOU</span>
            <span className="entry-time">{clockOf(entry)}</span>
            <span style={{ flex: 1 }} />
            <span className="entry-mark" data-mark={mark}
              style={tone(mark === 'held' ? TONE.silent : 'var(--t3)')}>{mark}</span>
          </div>
          <div className="entry-human-body">{text(entry)}</div>
        </div>,
      )
    }

    // ── the only thing in the transcript that reads like prose (:240)
    case 'prose':
      return wrap(<div className="entry-prose">{text(entry)}</div>)

    // ── one collapsed mono line, expanding to INPUT and RESULT (:244-266)
    case 'tool':
      return wrap(
        <ToolLine entry={entry} expanded={expanded} onToggle={toggle}
          tool={text(entry, 'tool')} argument={text(entry, 'summary')}>
          <Panel label="INPUT" body={text(entry, 'input')} />
          <div className="entry-rule" />
          <Panel label="RESULT" body={text(entry, 'result')} />
        </ToolLine>,
        { 'data-expanded': String(expanded), 'data-ok': String(entry.ok ?? null) },
      )

    // ── the same line, and the diff IN PLACE OF input/result (F13). Editing files is the
    //    commonest thing an agent does; it should be the easiest thing here to open.
    case 'file_edit':
      return wrap(
        <ToolLine entry={entry} expanded={expanded} onToggle={toggle} tool="Edit"
          argument={`${text(entry, 'path')} · +${number(entry, 'added')} −${number(entry, 'removed')}`}>
          <pre className="entry-diff" data-testid="entry-diff">{text(entry, 'diff')}</pre>
        </ToolLine>,
        { 'data-expanded': String(expanded) },
      )

    // ── a `tool` line whose tool name is the command, expanded to its output (E19/M10)
    case 'command':
      return wrap(
        <ToolLine entry={entry} expanded={expanded} onToggle={toggle}
          tool={text(entry, 'which') || text(entry, 'tool')} argument={text(entry, 'summary')}>
          <Panel label="OUTPUT" body={text(entry, 'output')} />
        </ToolLine>,
        { 'data-expanded': String(expanded), 'data-ok': String(entry.ok ?? null) },
      )

    // ── indented mono block with a left rule (:269)
    case 'tool_result':
      return wrap(<pre className="entry-result">{text(entry)}</pre>)

    // ── crash-coloured block, `ERROR · <time>` (:273-278)
    case 'error':
      return wrap(
        <Crash title={`ERROR · ${clockOf(entry)}`} body={text(entry)} />,
        { 'data-tone': 'crash' },
      )

    // ── F9. A refusal that is only a log line is a refusal nobody sees, so it is the error block
    //    and it names the path (§4.6, M9).
    case 'isolation_refusal':
      return wrap(
        <Crash title="REFUSED · outside the lane"
          body={`${text(entry, 'operation')} ${text(entry, 'path')}`.trim()} />,
        { 'data-tone': 'crash' },
      )

    // ── the centred rule (:281-287)
    case 'turn':
      return wrap(<Rule label={`turn ${number(entry, 'n')} · ${formatElapsed(number(entry, 'duration_s'))} · ${formatCost(number(entry, 'cost_usd'))}`} />)

    // ── §8 D2's only visual consequence outside the strip: the sequence of transitions lives here
    case 'stage':
      return wrap(
        <Rule label={`${text(entry, 'stage')}${number(entry, 'round') > 1 ? ` · round ${number(entry, 'round')}` : ''}`} />,
      )

    // ── §8 D5 — the counter always. "Silent 4m 12s" is actionable; "silent" is not. (:290-293)
    case 'silence':
      return wrap(
        <>
          <Rule label={`NO OUTPUT · ${formatSilence(number(entry, 'silent_for_s'))}`} toneValue={TONE.silent} />
          <div className="entry-rule-note">
            last tool call has not returned · turn cost still accruing
          </div>
        </>,
        { 'data-tone': 'silent' },
      )

    // ── the silent rectangle UNDER the message it belongs to (:219-227)
    case 'held':
      return wrap(
        <Rect toneValue={TONE.silent} icon={<WarningCircle size={15} />}
          title="NOT PROCESSED · AGENT IS MID-TURN" aside="send again to interrupt">
          Your message is held here, not lost.
        </Rect>,
        { 'data-tone': 'silent' },
      )

    // ── the stopped shape, and the two §4.6 gives the same shape to (:230-238)
    case 'stopped':
    case 'ended':
      return wrap(
        <Rect icon={<StopCircle size={15} />} title={text(entry)}
          aside={kind === 'stopped' ? 'revise and send to continue' : ''}>
          {text(entry, 'sub')}
        </Rect>,
      )

    case 'killed':
      return wrap(
        <Rect toneValue={TONE.crash} icon={<Prohibit size={15} />} title={text(entry)} aside="">
          {text(entry, 'sub')}
        </Rect>,
        { 'data-tone': 'crash' },
      )

    // ── §8 D4: the design has no drawing at all, so this is its `held` rectangle in the accent
    //    colour with the three buttons §4.6's table names. Answering is E23 and belongs to
    //    WEB-FEAT-018 — `onDecide` is the slot, and the buttons say so rather than lying.
    case 'permission_request': {
      const id = text(entry, 'request_id')
      const rule = text(entry, 'rule_that_would_be_written')
      return wrap(
        <Rect toneValue="var(--color-accent)" icon={<WarningCircle size={15} />}
          title={`PERMISSION · ${text(entry, 'tool')}`} aside="">
          <div className="entry-permission-summary">{text(entry, 'input_summary')}</div>
          <code className="entry-permission-rule">{rule}</code>
          <div className="entry-permission-buttons" data-testid="permission-buttons">
            {(['once', 'always', 'deny'] as const).map((decision) => (
              <button key={decision} type="button" className="entry-permission-button"
                data-decision={decision} disabled={!onDecide}
                title={onDecide ? undefined : 'answering a request is E23 — WEB-FEAT-018'}
                onClick={onDecide ? () => onDecide(id, decision) : undefined}>
                {DECISION_LABELS[decision]}
              </button>
            ))}
          </div>
        </Rect>,
        { 'data-tone': 'accent' },
      )
    }

    // ── one mono line: `allowed once · Bash` / `rule added · Bash(npm run *)` / `denied`
    case 'permission_decision': {
      const decision = text(entry, 'decision')
      const rule = text(entry, 'rule')
      const phrase = decision === 'always' ? 'rule added'
        : decision === 'deny' ? 'denied' : 'allowed once'
      return wrap(
        <div className="entry-decision">{rule ? `${phrase} · ${rule}` : phrase}</div>,
      )
    }

    // A kind the server invented and this table never declared renders NOTHING, visibly — the
    // same bargain `Chips.tsx` makes with a state nobody put on the vocabulary sheet.
    default:
      return null
  }
}

const DECISION_LABELS = { once: 'Allow once', always: 'Always allow', deny: 'Deny' } as const

/** The collapsed mono line every tool-shaped kind shares (:245-254). */
function ToolLine({ entry, tool, argument, expanded, onToggle, children }: {
  entry: Entry
  tool: string
  argument: string
  expanded: boolean
  onToggle(): void
  children: ReactNode
}) {
  const ok = entry.ok
  const outcome = ok === true ? TONE.run : ok === false ? TONE.crash : TONE.silent
  return (
    <div className="entry-tool">
      <div className="entry-line" data-testid="entry-line" role="button" tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle() } }}>
        {expanded ? <CaretDown size={11} /> : <CaretRight size={11} />}
        <span className="entry-outcome" style={tone(outcome)}>
          {ok === true ? <Check size={11} /> : ok === false ? <X size={11} /> : <Circle size={11} />}
        </span>
        <span className="entry-tool-name">{tool}</span>
        <span className="entry-tool-arg">{argument}</span>
        <span style={{ flex: 1 }} />
        <span className="entry-tool-dur">{duration(entry.duration_s)}</span>
      </div>
      {expanded && <div className="entry-panel">{children}</div>}
    </div>
  )
}

function Panel({ label, body }: { label: string; body: string }) {
  return (
    <>
      <div className="entry-panel-label">{label}</div>
      <pre className="entry-panel-body">{body}</pre>
    </>
  )
}

/** The design's centred rule with a caption (:281-287). */
function Rule({ label, toneValue }: { label: string; toneValue?: string }) {
  return (
    <div className="entry-rule-row" style={toneValue ? tone(toneValue) : undefined}>
      <span className="entry-rule-line" />
      <span className="entry-rule-label">{label}</span>
      <span className="entry-rule-line" />
    </div>
  )
}

/** The rectangle the design uses for held, stopped and (§8 D4) a permission request. */
function Rect({ toneValue, icon, title, aside, children }: {
  toneValue?: string
  icon: ReactNode
  title: string
  aside: string
  children: ReactNode
}) {
  return (
    <div className="entry-rect" data-toned={String(Boolean(toneValue))}
      style={toneValue ? tone(toneValue) : undefined}>
      <span className="entry-rect-icon">{icon}</span>
      <div className="entry-rect-main">
        <div className="entry-rect-title">{title}</div>
        <div className="entry-rect-body">{children}</div>
      </div>
      {aside && <div className="entry-rect-aside">{aside}</div>}
    </div>
  )
}

/** The crash-coloured block `error` and `isolation_refusal` share (:273-278). */
function Crash({ title, body }: { title: string; body: string }) {
  return (
    <div className="entry-crash">
      <div className="entry-crash-title">{title}</div>
      <pre className="entry-crash-body">{body}</pre>
    </div>
  )
}
