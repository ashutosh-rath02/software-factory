import { act } from '@testing-library/react'

/**
 * One keystroke, dispatched where a person's fingers put it.
 *
 * The listener under test is on `window`, so the event is dispatched on whatever HAS FOCUS and
 * allowed to bubble — which is what makes `e.target` a real answer rather than one a case
 * asserted into existence. The event is returned so a case can ask whether the product consumed
 * it: `preventDefault` is the whole difference between a key that navigates and a key that types.
 */
export async function press(target: Element, init: KeyboardEventInit): Promise<KeyboardEvent> {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  await act(async () => { target.dispatchEvent(event) })
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
  return event
}

/**
 * The keystroke a written hint SPELLS — `alt N` becomes `Alt` held and the `N` key down.
 *
 * Read off the screen, or out of the design, and turned into an event, so a case never types the
 * binding it is checking. The hint a person is shown and the key the product answers to are one
 * fact, and a test that typed both halves could only ever agree with itself.
 *
 * `code`, not `key`: `Alt` rewrites `key` on macOS, so `code` is the only thing a letter can be
 * identified by while a modifier is down.
 */
export function keyFromHint(hint: string): KeyboardEventInit {
  const parts = hint.trim().split(/[\s+]+/).filter(Boolean)
  const letter = parts[parts.length - 1] ?? ''
  const held = parts.slice(0, -1).map((m) => m.toLowerCase())
  return {
    code: `Key${letter.toUpperCase()}`,
    key: letter.toLowerCase(),
    altKey: held.includes('alt'),
    ctrlKey: held.includes('ctrl'),
    metaKey: held.includes('meta'),
    shiftKey: held.includes('shift'),
  }
}
