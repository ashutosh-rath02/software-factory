import { describe, it, expect, afterEach, vi } from 'vitest'
import { act, renderHook, cleanup } from '@testing-library/react'

import { SETTLE_MS, useSettled } from '../src/services/settle'

/**
 * WEB-FIX-333 · the module, with no surface attached.
 *
 * `TicketsByEpic` is the only consumer today, and a rule enforced by the consumer is a rule that
 * consumer has — the next one will not. `WEB-FIX-051` made the socket module the seed for every
 * mount and asserted it through four cases, every one of which mounted a component; its own
 * regression passed through the COMPONENT's rule and never reached the module's, which did not
 * have one, and the module dropped early entries for two months (`WEB-FIX-073`).
 *
 * So the four clauses of `useSettled`'s contract are driven here, through its own entry point,
 * reading its own return value, with nothing rendered around it.
 */
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('WEB-FIX-333 · useSettled — the module on its own', () => {
  it('test_the_first_value_is_returned_at_once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const { result } = renderHook(() => useSettled('WEB-FIX-329'))

    // clause 1 — a first render asks with what it was given; a hook starting empty would make
    // every surface fetch twice on mount
    expect(result.current).toBe('WEB-FIX-329')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('test_only_the_value_that_stood_still_is_returned', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const { result, rerender } = renderHook(({ v }: { v: string }) => useSettled(v),
                                            { initialProps: { v: '' } })

    const ID = 'WEB-FIX-329'
    const seen: string[] = [result.current]
    for (let i = 1; i <= ID.length; i += 1) {
      await act(async () => { rerender({ v: ID.slice(0, i) }); vi.advanceTimersByTime(120) })
      seen.push(result.current)
    }

    // clause 2 — one timer at a time, and each new value disarms the last: nothing on the way
    // to the settled value was ever returned
    expect(new Set(seen)).toEqual(new Set(['']))
    expect(vi.getTimerCount()).toBe(1)

    await act(async () => { vi.advanceTimersByTime(SETTLE_MS) })
    expect(result.current).toBe(ID)

    // clause 3 — at rest the module owns no clock at all. A surface left mounted for ten minutes
    // has nothing to fire, which is what makes this a debounce and not an interval.
    expect(vi.getTimerCount()).toBe(0)
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })
    expect(result.current).toBe(ID)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('test_a_value_that_comes_back_inside_the_window_is_never_returned', async () => {
    // MANDATORY NEGATIVE, at the module — clause 4, which criterion 3 rests on. A hook that
    // re-set the settled value to itself would move the consumer's key and ask for a query the
    // person deleted; a hook that fired the intermediate value late would ask for two.
    vi.useFakeTimers({ shouldAdvanceTime: false })
    let renders = 0
    const { result, rerender } = renderHook(({ v }: { v: string }) => { renders += 1; return useSettled(v) },
                                            { initialProps: { v: '' } })
    const rendersAtRest = renders

    for (const v of ['3', '32', '3', '']) {
      await act(async () => { rerender({ v }); vi.advanceTimersByTime(60) })
    }

    // THE line. The value has come home inside the window, so there is nothing left to fire —
    // asserted HERE, while a timer would still be pending, and not after ten minutes have burned
    // it off. Deleting clause 4 leaves a timer armed on a value equal to the settled one; it
    // fires `setSettled('')`, React bails out on the identical state, and every assertion taken
    // after the fact is green over a module that armed a clock it had no reason to arm.
    expect(vi.getTimerCount()).toBe(0)

    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000) })

    expect(result.current).toBe('')
    expect(vi.getTimerCount()).toBe(0)
    // the four rerenders are the caller's own — not one extra commit came from the hook
    expect(renders).toBe(rendersAtRest + 4)
  })
})
