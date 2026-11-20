import type { CSSProperties, ReactNode } from 'react'

import { AXES, TINT, TONE, axisValue, type AxisName } from '../vocabulary'

/** A token reference in a CSS custom property. Colours live in tokens.css and nowhere else. */
export const tone = (value: string, tint?: string): CSSProperties =>
  ({ '--tone': value, ...(tint ? { '--tint': tint } : {}) }) as CSSProperties

/** The tint that belongs to a tone, so an alarm's border and its fill cannot disagree. */
export function tintFor(toneValue: string): string {
  const key = (Object.keys(TONE) as Array<keyof typeof TONE>)
    .find((k) => TONE[k] === toneValue)
  return key ? TINT[key] : 'transparent'
}

/**
 * A state's coloured dot — the mark the vocabulary sheet documents (Dashboard.dc.html).
 *
 * It resolves through `axisValue`, so a state nobody put on the sheet has no colour to take and
 * renders nothing. That is the point: a component cannot invent a state quietly.
 */
export function StateDot({ axis, value, className = 'dot' }:
{ axis: AxisName; value: string; className?: string }) {
  const entry = axisValue(axis, value)
  if (!entry) return null
  return <span data-testid="state-dot" className={className} style={tone(entry.tone)} />
}

/**
 * One axis chip — `EVIDENCE · CLAIMED · NO PROOF` (Ticket.dc.html).
 *
 * Three of these and never one merged status (`SPEC.md` F17). The header passes `title`, which is
 * what puts the axis's own name on the front of it; the sheet does not, because a sheet listing
 * an axis has already said which axis it is.
 *
 * Two states that look alike and are not:
 *
 *   an INVENTED value — `hibernating` — renders NOTHING. A component cannot bring a state into
 *     existence that the shared table never declared, and rendering nothing is visible where
 *     rendering something wrong is not (`vocabulary.test.tsx`).
 *   an EMPTY value — a lane that has never run has no motion at all — renders `—` when it has a
 *     title to hang it on. §4.5's empty state is explicit: `MOTION · —`, not a missing chip and
 *     not an invented word.
 */
export function StateChip({ axis, value, title = false, suffix, dim = false }: {
  axis: AxisName
  value: string | null | undefined
  /** put `PROCESS · ` in front of it — the header's form, not the sheet's */
  title?: boolean
  /** the silence counter, which is a clock and so belongs to the caller */
  suffix?: ReactNode
  /** the socket is down: keep the value, and say it is not being kept up to date */
  dim?: boolean
}) {
  const entry = value ? axisValue(axis, value) : undefined
  if (!entry && (value || !title)) return null

  const toneValue = entry?.tone ?? TONE.none
  return (
    <span data-testid="state-chip" className="evidence-banner" data-axis={axis}
      data-alarm="false" data-dim={String(dim)}
      style={tone(toneValue, tintFor(toneValue))}>
      <span className="chip-dot" style={tone(toneValue)} />
      {title ? `${AXIS_TITLES[axis]} · ${(entry?.label ?? '—').toUpperCase()}` : entry?.label}
      {suffix}
    </span>
  )
}

/** The pill a row takes when something in it needs a person (Dashboard.dc.html). */
export function NeedsPill({ label, toneValue, testId = 'needs-pill' }:
{ label: string; toneValue: string; testId?: string }) {
  if (!label) return null
  return (
    <span data-testid={testId} className="needs-pill" style={tone(toneValue)}>{label}</span>
  )
}

/** One tag per workspace a requirement touches (Dashboard.dc.html). */
export function WorkspaceTag({ name }: { name: string }) {
  return <span className="tag" data-testid="workspace-tag">{name}</span>
}

export const AXIS_TITLES: Record<AxisName, string> = {
  process: 'PROCESS', motion: 'MOTION', evidence: 'EVIDENCE',
}

export const AXIS_ORDER = Object.keys(AXES) as AxisName[]

/** The row's left edge (Dashboard.dc.html) — the state's colour when it needs a person,
 *  and none when it does not, so the column of marks is scannable without reading a word. */
export const mark = (value: string): CSSProperties => ({ '--mark': value }) as CSSProperties
