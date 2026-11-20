import { useEffect, useRef, useState } from 'react'

import { useFactory } from '../services/factory'
import { reachOut, setFaviconDot, startsALane, stripPrefix, titleFor } from '../services/notify'
import type { ServerFrame } from '../types/socket'

/**
 * SPEC.md §4.13 · F24 — the background tab. Renders nothing; mounted once, in the shell.
 *
 * It is not a surface and it takes no row in `components/tabs.tsx`: a tab that draws nothing is a
 * tab nobody can open. What it owns is the count of events raised since the person last looked,
 * and the two channels that carry it.
 *
 * The title prefix is PRIMARY — it happens whether or not the browser granted notification
 * permission, and whether or not the browser has a `Notification` at all — and the favicon dot
 * with it. A system notification is additional and best-effort. No sound, no repeat, no
 * escalation, and nothing is sent to the server.
 *
 * Two rules follow from the word `background`:
 *
 *   an event that arrives while the tab is VISIBLE raises nothing. The person is already looking.
 *     Without this, a session that never leaves the tab accumulates a prefix with no way to clear
 *     it, because the `focus` that clears it never fires.
 *   the count clears the moment the tab is focused, and on nothing else. There is no timer in
 *     this file, which is what keeps `no-poll.test.tsx` true.
 *
 * `N` is NOT §3.3's `needs you`. That counter is the server's, it means *lanes that need a
 * person*, and it does not drop because a tab was focused; this one is *events you have not seen
 * yet*. Two numbers, two questions — and §3.3 gains no clause here.
 */
export function BackgroundTab() {
  const { onFrame, socketState } = useFactory()
  const [count, setCount] = useState(0)

  const base = useRef<string | null>(null)
  if (base.current === null) base.current = stripPrefix(document.title)

  /** the last process seen per lane, so `idle` is a transition and not a repeated state */
  const seen = useRef(new Map<string, string>())
  const asked = useRef(false)

  useEffect(() => onFrame((frame: ServerFrame) => {
    if (startsALane(frame)) ask(asked)

    const reach = reachOut(frame, seen.current)
    if (!reach) return
    if (document.visibilityState !== 'hidden') return

    setCount((n) => n + 1)
    raise(reach.title, reach.body)
  }), [onFrame])

  useEffect(() => {
    const clear = () => { if (document.visibilityState !== 'hidden') setCount(0) }
    window.addEventListener('focus', clear)
    document.addEventListener('visibilitychange', clear)
    return () => {
      window.removeEventListener('focus', clear)
      document.removeEventListener('visibilitychange', clear)
    }
  }, [])

  useEffect(() => {
    document.title = titleFor(base.current ?? '', count, socketState !== 'open')
    setFaviconDot(count > 0)
  }, [count, socketState])

  return null
}

/**
 * Asked at the moment a lane starts, once, and only while the browser has not already decided.
 * `requestPermission` is not awaited: whatever it answers, the title has already worked.
 */
function ask(asked: { current: boolean }): void {
  if (asked.current) return
  asked.current = true
  if (typeof Notification === 'undefined') return
  if (Notification.permission !== 'default') return
  try { void Notification.requestPermission() } catch { /* best-effort, always */ }
}

function raise(title: string, body: string): void {
  if (typeof Notification === 'undefined') return
  if (Notification.permission !== 'granted') return
  try { void new Notification(title, { body }) } catch { /* best-effort, always */ }
}
