import { axisValue } from '../vocabulary'
import type { ServerFrame } from '../types/socket'

/**
 * SPEC.md §4.13 — what the product does when the person is not looking.
 *
 * The decisions live here, as functions of a frame and nothing else, so that "exactly three" is a
 * table somebody can read rather than a shape spread through a component's effects. The component
 * (`components/BackgroundTab.tsx`) owns the count, the title and the browser's permission; this
 * file owns WHICH frames are worth interrupting a person for.
 *
 * Nothing here is sent to the server, and nothing here is on a timer.
 */

export interface Reach {
  title: string
  body: string
}

/**
 * The three events, and exactly three.
 *
 *   M6 permission.request                     the run is stopped dead until it is answered
 *   M2 lane.state → idle | blocked            the agent is waiting on a person, burning nothing
 *   M11 run outcome crashed                   it is over and nobody knows
 *
 * A stage completing is the FREQUENT event, and a channel that fires on frequent events is a
 * channel that gets muted — once muted, the three above are lost with it. A run finishing normally
 * is not an interruption either, nor is one a person killed themselves, nor is silence: silence is
 * already visible on the rail and resolves itself often enough that a notification for it would
 * train the person to ignore all of them.
 *
 * `seen` is the last process this notifier saw for each lane, and it is what makes the second row
 * a TRANSITION rather than a state. M2 fires on any axis moving, `delivery` included, so a lane
 * sitting `idle` while its delivery state changes produces several frames that all read `idle`.
 * Firing on each of them turns one stopped lane into a stream.
 */
export function reachOut(frame: ServerFrame, seen: Map<string, string>): Reach | null {
  if (frame.m === 'permission.request') {
    return {
      title: `${frame.lane} is waiting for permission`,
      body: `${frame.tool} · ${frame.input_summary}`,
    }
  }

  if (frame.m === 'run' && frame.outcome === 'crashed') {
    return { title: `${frame.lane} crashed`, body: `run ${frame.run_id} died mid-turn` }
  }

  if (frame.m === 'lane.state') {
    const lane = frame.lane
    if (!lane) return null
    const process = processOf(frame)
    if (!process) return null
    const was = seen.get(lane)
    seen.set(lane, process)
    if (process !== 'idle' && process !== 'blocked') return null
    if (was === process) return null
    const entry = axisValue('process', process)
    return { title: `${lane} · ${entry?.label ?? process}`, body: entry?.note ?? '' }
  }

  return null
}

/**
 * A lane started — the moment the browser is asked for notification permission, and never on page
 * load. A page that asks before it has done anything is a page people refuse, and a refusal is
 * not askable again.
 */
export function startsALane(frame: ServerFrame): boolean {
  if (frame.m === 'run') return frame.outcome === 'running'
  if (frame.m === 'lane.state') return processOf(frame) === 'running'
  return false
}

function processOf(frame: ServerFrame): string | null {
  if (frame.m !== 'lane.state') return null
  const state = frame.state as { process?: unknown } | undefined
  return typeof state?.process === 'string' ? state.process : null
}

const PREFIX = /^(\(\d+\)|\(offline\))\s+/

/** So a remount, or a second render, cannot stack one prefix on top of another. */
export function stripPrefix(title: string): string {
  let out = title
  while (PREFIX.test(out)) out = out.replace(PREFIX, '')
  return out
}

/**
 * `(2) (offline) Factory`.
 *
 * The count and the socket are two different facts and neither replaces the other: events are
 * pending AND nothing new can arrive to join them.
 */
export function titleFor(base: string, count: number, offline: boolean): string {
  const parts: string[] = []
  if (count > 0) parts.push(`(${count})`)
  if (offline) parts.push('(offline)')
  return [...parts, base].join(' ')
}

const ICON = 'link[rel="icon"]'

/**
 * The favicon's dot, which happens whether or not the browser granted anything.
 *
 * The dot is drawn in the accent ALREADY inside the document's icon — read off the href rather
 * than written here — so this file names no colour and `tokens.css` stays the only place one
 * lives. A document with no inline SVG icon to read falls back to `currentColor`, which is a
 * keyword rather than a colour.
 */
export function setFaviconDot(on: boolean): void {
  let link = document.querySelector<HTMLLinkElement>(ICON)
  if (!link) {
    link = document.createElement('link')
    link.setAttribute('rel', 'icon')
    document.head.appendChild(link)
  }
  if (link.dataset.plain === undefined) link.dataset.plain = link.getAttribute('href') ?? ''
  const plain = link.dataset.plain
  link.setAttribute('href', on ? withDot(plain) : plain)
}

const CLOSE = '%3C/svg%3E'
const BARE = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'"
  + CLOSE

export function withDot(plain: string): string {
  const source = plain.endsWith(CLOSE) ? plain : BARE
  const accent = /stroke='(%23[0-9a-fA-F]{3,8})'/.exec(source)?.[1] ?? 'currentColor'
  const dot = `%3Ccircle cx='12' cy='4' r='3.6' fill='${accent}'/%3E`
  return source.slice(0, -CLOSE.length) + dot + CLOSE
}
