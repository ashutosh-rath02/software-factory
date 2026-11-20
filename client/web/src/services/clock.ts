import { useEffect, useState } from 'react'

/**
 * The one ticker in the client, and the only thing that updates by itself.
 *
 * SPEC.md §4.4: "the elapsed and silence numbers tick client-side from `since` timestamps, so a
 * quiet socket does not freeze a clock." A lane that has been silent for four minutes is the
 * screen's most urgent number, and it is exactly the number a dead socket would stop.
 *
 * So it is a clock — and a clock is the disguise polling comes back in. Three properties keep it
 * one thing rather than the first of many:
 *
 *   ONE interval, however many clocks are on screen. Every subscriber shares it, so a second
 *     ticks once and React commits once, no matter how many cards are in the rail.
 *   NO interval when nothing is mounted. The last subscriber to leave clears it, which is why
 *     the requirements list, the requirement detail and the vocabulary sheet run with no timer at
 *     all — and why `no-poll.test.tsx` did not have to change.
 *   NO state. It publishes a whole-second number and nothing else. It cannot fetch, it cannot
 *     invalidate, and there is nothing in it for a later ticket to hang a refresh on.
 *
 * `lane-rail.test.tsx::nothing_is_rebuilt_on_a_timer` advances ten minutes and asserts that the
 * only text on the screen that moved belongs to a clock.
 */

const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined
let second = Math.floor(Date.now() / 1000)

/** The current whole second, as the last tick saw it. */
export function currentSecond(): number {
  return second
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (timer === undefined) {
    timer = setInterval(() => {
      second = Math.floor(Date.now() / 1000)
      for (const l of Array.from(listeners)) l()
    }, 1000)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && timer !== undefined) {
      clearInterval(timer)
      timer = undefined
    }
  }
}

/**
 * The current second while `running`, and the last one it saw while not.
 *
 * `running` is false when the socket is down, because §4.4 says the clocks stop: a card whose
 * elapsed time kept climbing after the connection died would be counting something nobody is
 * watching, and the number would be a guess dressed as a measurement.
 */
export function useSecond(running: boolean): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))
  useEffect(() => {
    if (!running) return undefined
    second = Math.floor(Date.now() / 1000)
    setNow(second)
    return subscribe(() => setNow(currentSecond()))
  }, [running])
  return now
}
