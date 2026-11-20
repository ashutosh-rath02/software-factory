import { useSecond } from '../services/clock'

/**
 * A number that counts up from a timestamp the server sent once.
 *
 * The server sends `elapsed_s` when it changes and `motion.since` when a lane falls silent, and
 * never again — SPEC.md M4 is explicit that metrics are "never on a timer". Everything between
 * those frames is arithmetic, done here, which is what lets a quiet lane produce no traffic at all
 * and a dead socket leave the last number readable rather than blank.
 *
 * `data-clock` is on it so the mandatory negative can name the one thing on the screen that is
 * allowed to change without a frame arriving.
 */
export function Clock({ testId, baseS = 0, sinceMs, running, format, down = false }: {
  testId: string
  /** what the server last said this number was */
  baseS?: number
  /**
   * when it said it — `null` freezes the value at `baseS`.
   *
   * A LIST of moments is a total counting from several at once: §4.5's identity row sums every
   * run's duration, and a ticket can hold more than one live run (a `run` lane and a `qa` lane
   * are two). One `since` cannot express two clocks, and a header frozen under a ticking row is
   * two numbers about one run disagreeing on screen (`WEB-FIX-091`).
   */
  sinceMs?: number | null | number[]
  running: boolean
  format: (seconds: number) => string
  /**
   * Count DOWN from `baseS` instead of up — a lane's time left (WEB-FIX-428). The same ticker and
   * the same anchor rule: the server said `remaining_s` once, and the seconds since are arithmetic.
   */
  down?: boolean
}) {
  const now = useSecond(running)
  const sinces = (Array.isArray(sinceMs) ? sinceMs : [sinceMs])
    .filter((at): at is number => at !== null && at !== undefined && Number.isFinite(at))
  const passed = sinces.reduce((total, at) => total + Math.max(0, now - Math.floor(at / 1000)), 0)
  const seconds = down ? Math.max(0, baseS - passed) : baseS + passed
  return <span data-testid={testId} data-clock="true">{format(seconds)}</span>
}
