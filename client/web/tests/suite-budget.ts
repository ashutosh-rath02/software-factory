import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * The budget EVERY case in this suite runs under, read off `vite.config.ts` rather than typed.
 *
 * `WEB-FIX-396` pinned `notify.test.tsx`'s budget to 5000 — vitest's default — and asserted that
 * `vite.config.ts` declared no `testTimeout` at all, to keep one property: *this case may not buy
 * itself room the rest of the suite does not have.* The number was right and the mechanism was a
 * literal, so the property could only be held by the suite having no declared budget.
 *
 * `WEB-FIX-405` gives the suite one, because 5000 ms is not a budget the timing-coupled cases fit
 * inside on a machine running four lanes: `WEB-FIX-327`, `WEB-FIX-372` and `WEB-FIX-389` are three
 * tickets about the same five seconds. The property survives by being derived instead of typed —
 * a case reading this function takes exactly the suite's room, whatever the suite's room is, and a
 * per-case override for one case alone is still visible as a different number.
 */
export function suiteTimeoutMs(): number {
  const config = readFileSync(join(__dirname, '..', 'vite.config.ts'), 'utf8')
  const declared = /\btestTimeout:\s*(\d+)/.exec(config)
  if (!declared) {
    throw new Error(
      'vite.config.ts declares no testTimeout, so every case in this suite runs under vitest\'s '
      + '5 s default — which is the budget WEB-FIX-327, WEB-FIX-372 and WEB-FIX-389 were each '
      + 'filed about. A case cannot be about a budget the suite does not state.')
  }
  return Number(declared[1])
}

/**
 * Red when a case spent more than `share` of the budget it runs under.
 *
 * The number a timing-coupled case needs is not *did it finish* — vitest already answers that, by
 * failing — but *how much room was left*. A case that passes at 96% of its ceiling passes on this
 * machine, at this moment, and is a coin flip everywhere else.
 */
export function refuseOverBudget(elapsed_ms: number, budget_ms: number, share = 0.5): void {
  if (elapsed_ms >= budget_ms * share) {
    throw new Error(
      `the case cost ${Math.round(elapsed_ms)} ms of a ${budget_ms} ms budget — more than `
      + `${Math.round(share * 100)}% of it, and a verdict that depends on how busy the machine was `
      + 'trains a reader to re-run rather than to look')
  }
}
