import { useEffect, useState } from 'react'

/**
 * The window `design/BRIEF.md` gives the `SEARCH` row: the field asks once the value has stood
 * still for this long, and never otherwise.
 *
 * 300 ms is above a brisk typist's inter-key interval (≈150 ms at 80 WPM) and below what a person
 * reads as lag. It is measured in PAUSES rather than in words, so somebody hunting for a character
 * gets more than one request and fewer than one per keystroke.
 */
export const SETTLE_MS = 300

/**
 * The last value that stood still for `ms` — the trailing half of a debounce, and nothing else.
 *
 * This is the client's second timer, and the first that can reach the network, so what separates
 * it from a poll is written into `no-poll.test.tsx` rather than into an allow-list: a poll fires
 * because time passed, a debounce because a person typed and then stopped. Four clauses:
 *
 *   1. It starts equal to `value`, so a first render asks with what it was given rather than
 *      fetching twice on every mount.
 *   2. While `value` differs from the settled one, exactly ONE timer exists. A new value disarms
 *      the previous timer before arming its own, so no prefix on the way to a settled query is
 *      ever returned.
 *   3. When the timer fires the effect re-runs, finds the two equal and arms nothing. A surface
 *      at rest owns no clock at all, which is why ten minutes of it produce no fetch.
 *   4. A value that comes back to the settled one inside the window leaves it UNTOUCHED — not
 *      re-set to itself. Typing `32` and deleting it moves no key, re-runs no effect and asks
 *      nothing; a debounce that fired the abandoned query late would land a stale answer on a
 *      screen that has moved on, which is worse than no debounce at all.
 *
 * `tests/settle.test.tsx` drives all four with no surface attached.
 */
export function useSettled<T>(value: T, ms: number = SETTLE_MS): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    if (Object.is(value, settled)) return undefined
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, settled, ms])

  return settled
}
