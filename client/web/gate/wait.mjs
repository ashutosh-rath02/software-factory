/**
 * Waiting, for a gate that drives a real client.
 *
 * `SRV-FEAT-165` round 1 shipped this wait into `browser-gate.mjs`:
 *
 *   waitForFunction((before) => document.querySelectorAll(ROW).length !== before, allIds.length)
 *
 * and its QA ran the gate twice and got `183/186 · GATE FAILED` both times, on a build where the
 * feature was correct and the same gate had been green for the session that wrote it.
 *
 * `useEndpoint` (`src/services/factory.tsx:221`) returns `result: undefined` the moment its key
 * changes, and the key carries the filter — so pressing a filter chip empties the list to ZERO
 * rows and refills it when the response lands. QA sampled the row count every 25 ms across the
 * click and measured the trajectory this module is built around:
 *
 *   0,2,2,2,2,2,2,2,2,2, …
 *
 * `length !== before` is satisfied by that leading `0`. Whether a run is green is then a race
 * between the poll and the network, which is why one machine saw 186/186 and another saw 183/186
 * on the same commit.
 *
 * **A wait waits for the surface to have SETTLED, never for it to have merely CHANGED.** The
 * settled signal is the client's own loading state, not a row count, so nothing here encodes the
 * answer the assertion is about to check — a wait that waits for the expected number would be the
 * assertion, and would pass by construction.
 */

/**
 * Resolve on the first sample that is not the loading state.
 *
 * `sample()` returns `{ loading, rows }`. Returns the settled sample, or the last one seen if the
 * surface never settles inside `timeout` — a timeout is reported to the caller as data rather than
 * thrown, so the assertion downstream fails with the real state instead of an exception.
 */
export async function settleOnLoaded(sample, { timeout = 10000, interval = 25, now = Date.now,
                                               sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  const deadline = now() + timeout
  let seen = await sample()
  while (seen.loading) {
    if (now() >= deadline) return { ...seen, timedOut: true }
    await sleep(interval)
    seen = await sample()
  }
  return { ...seen, timedOut: false }
}

/** The banned shape, named once so the test that forbids it and this comment cannot drift apart. */
export const CHANGE_WAIT = /\.length\s*!==\s*before/
