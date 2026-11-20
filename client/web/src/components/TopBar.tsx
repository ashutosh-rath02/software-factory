import { Fragment, useEffect, useRef } from 'react'
import { Moon, Sun, Warning } from '@phosphor-icons/react'

import { href, type Route } from '../routes'
import { useFactory } from '../services/factory'
import { useLanes } from '../services/lanes'
import { TONE } from '../vocabulary'
import { tone } from './Chips'

export interface Crumb { label: string; route?: Route }

/**
 * The key that goes where the chip goes, and the text the chip draws to say so — WEB-FEAT-365.
 *
 * Two exports out of one module, because the hint a person reads and the key the shell answers to
 * are the same fact: a hint that drifted from its binding is a control lying about itself.
 *
 * `code` and not `key`: `Alt` rewrites `key` on macOS, so `n` is not what arrives.
 */
export const NEEDS_YOU_KEY_HINT = 'alt N'
export const NEEDS_YOU_KEY_CODE = 'KeyN'

/** Where a key is a keystroke and not a command — a field, a draft, or prose being edited. */
function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable
    || ['input', 'textarea', 'select'].includes(target.tagName.toLowerCase())
}

/**
 * The app shell, always present, 38 px (Dashboard.dc.html).
 *
 * Brand · breadcrumb · the repository · VOCABULARY · the socket chip · the theme toggle.
 *
 * The `needs you` chip arrived with WEB-FEAT-013, when lanes became something that could need
 * you. It follows §4.1 in both directions: the count is E9's own — the SERVER decides which of
 * §3.3's seven clauses a lane satisfies, and a browser that recomputed that would be a second
 * implementation of the definition that matters most — and at zero the chip is ABSENT rather than
 * a `0`, because a chip reading `0 lanes need you` teaches a person to stop looking at that
 * corner of the screen. On a lost socket it dims and keeps its last value: a stale count that
 * admits it is stale is more useful than a blank.
 *
 * `PERMISSIONS` arrived with WEB-FEAT-018, and only then: a control that does nothing teaches the
 * user it is broken, so it waited for its screen. It is §8 D4's ONLY addition to the design's
 * chrome, and it is here because a revocable list nobody can reach is not revocable — it borrows
 * `VOCABULARY`'s own class rather than taking a style of its own, which is what "styled exactly as
 * VOCABULARY" has to mean if it is to stay true.
 *
 * `LANES` arrived with WEB-FEAT-050 and for the same reason `PERMISSIONS` did — it waited for its
 * screen (§4.14) — and it borrows the same `chrome-link`, because *"styled exactly as VOCABULARY"*
 * has to mean the same class or it stops being true. It is the third and, on the evidence of the
 * two before it, the last thing this bar can hold without becoming a menu.
 *
 * One thing the design does NOT draw is here: the repository name and the contract version.
 * SPEC.md §4.1's Data line makes E1 the source of both, and nothing else on any of the seven
 * surfaces shows which repository this window is pointed at. It borrows `VOCABULARY`'s own type
 * and adds no component and no token.
 *
 * `alt N` arrived with WEB-FEAT-365, and it is a second TRIGGER of §4.1's one derivation rather
 * than a second derivation: the key writes the same `destination` string the anchor carries as
 * its `href`. The hint is drawn on the chip because a binding nobody is told about is a binding
 * nobody uses, and it is the same constant the handler binds — one module, so the two cannot
 * drift.
 */
export function TopBar({ crumbs, theme, onTheme }: {
  crumbs: Crumb[]
  theme: 'dark' | 'light'
  onTheme: () => void
}) {
  const { socketState, health } = useFactory()
  const { needsYou, lanes } = useLanes()
  const live = socketState === 'open'

  // WEB-FEAT-044 — where the chip GOES. The first lane E9 reported as needing a person, in E9's
  // own order, because the number and the destination have to come out of one answer: a chip that
  // counted from the lane set and navigated from somewhere else could point at a lane that is not
  // one of the ones it counted.
  const first = lanes.find((row) => row.needs_you)

  /**
   * WEB-FEAT-050 — and this chip is now load-bearing rather than convenient.
   *
   * §4.4's rail shows only the open ticket's lanes, so this is the one thing on any screen that can
   * still say a lane needs a person SOMEWHERE ELSE. Its destination is the answer to the question
   * it just asked: one lane, and it goes to that lane's ticket, because a list of one is a screen
   * nobody wanted; more than one, and it cannot name them, so it goes to the surface that can
   * (§4.14). Both are `E9`'s own answer, so the count and the destination cannot disagree.
   */
  const destination = needsYou > 1
    ? href({ name: 'lanes' })
    : first ? href({ name: 'ticket', id: first.ticket_id }) : ''

  /**
   * The one keystroke, on the window, bound once.
   *
   * `destination` is read through a ref rather than through the effect's dependencies: the
   * listener is added at mount and removed at unmount, and what it reads is the value the anchor
   * above was last drawn with. A binding that closed over one reading would keep sending a person
   * to a lane the chip stopped counting ten minutes ago.
   *
   * The ref is written AFTER the commit and never during the render. A render can be thrown away
   * — React runs one twice in development and may abandon one at any time — and a ref written in
   * the body would then hold a destination no anchor was ever drawn with, which is the one thing
   * this whole arrangement exists to prevent.
   */
  const goes = useRef(destination)
  useEffect(() => { goes.current = destination }, [destination])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.code !== NEEDS_YOU_KEY_CODE) return
      // in a draft the key TYPES — it never navigates out from under half a message
      if (isEditable(e.target)) return
      // nothing needs anyone, the chip is absent, and there is nothing to consume the key for
      if (!goes.current) return
      e.preventDefault()
      location.hash = goes.current
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="bar" data-testid="top-bar">
      <a className="bar-brand" href={href({ name: 'home' })}>FACTORY</a>
      <div className="bar-rule" />
      <nav className="crumbs" data-testid="breadcrumb" aria-label="breadcrumb">
        {crumbs.map((crumb, i) => (
          <Fragment key={`${crumb.label}-${i}`}>
            {i > 0 && <span className="crumb-sep">/</span>}
            {crumb.route
              ? <a className="crumb" href={href(crumb.route)}>{crumb.label}</a>
              : <span className="crumb" aria-current="page">{crumb.label}</span>}
          </Fragment>
        ))}
      </nav>

      <span className="bar-spacer" />

      {health && (
        <span className="chrome-text" data-testid="repo-name">
          {health.repo.name} · {health.factory}
        </span>
      )}
      {health?.delivery && !health.delivery.available && (
        // SPEC.md §3.5's negative case, stated once for the whole screen rather than repeated
        // as the word `unknown` on every row. The rows keep everything else they had — the
        // screen is what the person came for, and losing it is worse than not knowing this.
        <span className="chip" data-testid="delivery-chip" data-live="false"
          title={health.delivery.reason}
          style={tone(TONE.silent)}>
          <span className="chip-dot" style={tone(TONE.silent)} />
          delivery unknown
        </span>
      )}
      <a className="chrome-link" href={href({ name: 'vocabulary' })}>VOCABULARY</a>
      <a className="chrome-link" href={href({ name: 'permissions' })}>PERMISSIONS</a>
      <a className="chrome-link" href={href({ name: 'lanes' })}>LANES</a>

      <span className="chip" data-testid="socket-chip" data-live={String(live)}>
        <span className="chip-dot" style={tone(live ? TONE.run : TONE.crash)} />
        {live ? 'socket live' : 'reconnecting'}
      </span>

      {needsYou > 0 && first && (
        <a className="chip needs-chip" data-testid="needs-you-chip" data-dim={String(!live)}
          href={destination}
          title={needsYou > 1
            ? `${needsYou} lanes need you — see all of them (alt+N)`
            : `go to ${first.ticket_id} — ${first.lane} (alt+N)`}
          style={tone(TONE.silent)}>
          <Warning size={12} />
          {needsYou} {needsYou === 1 ? 'lane needs' : 'lanes need'} you
          <kbd className="chip-key" data-testid="needs-you-key">{NEEDS_YOU_KEY_HINT}</kbd>
        </a>
      )}

      <button type="button" className="toggle" data-testid="theme-toggle" onClick={onTheme}
        aria-label={`switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
        {theme === 'dark' ? <Moon size={12} /> : <Sun size={12} />}
        {theme}
      </button>
    </div>
  )
}
