import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { screen, cleanup, act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { FakeSocket, HEALTH, MANIFEST, lane, laneSet, resource, settle, stubFetch } from './helpers'
import { FactoryProvider } from '../src/services/factory'
import { LanesProvider, useLanes } from '../src/services/lanes'
import { laneSub } from '../src/components/LaneCard'

/**
 * WEB-FIX-200 — `M8 resource` patches a row; it does not construct one.
 *
 * `Resource` is E9's shape and E9 is a READ: `holder_alive` is derived by the server at read time
 * (`app/lane/render.cpp`), because nothing delivers an event when a process somewhere else dies.
 * `M8` is an EDGE — claimed, released, the queue moved — and `socket.schema.json` closes every
 * frame with `unevaluatedProperties: false`, so the frame does not carry liveness and cannot be
 * made to without a server contract change. `SRV-FEAT-180` decided that in as many words.
 *
 * The client built a WHOLE `Resource` out of that partial frame (`services/lanes.tsx:149`), so the
 * day E9's shape gained a member M8 does not carry, `tsc -b` refused and `main` stopped building.
 * The type error is the symptom; the defect is a patch written as a constructor.
 *
 * What the frame does carry about liveness, and what these cases assert:
 *
 *   `held_by: null`            nobody holds it, so `holder_alive` is `null` — the frame says so,
 *                              and it is exactly what E9 renders for the same state
 *   `held_by: <the same one>`  the frame names the holder E9 already judged, so E9's answer STANDS
 *   `held_by: <someone else>`  a holder nothing has judged, so `null` — unknown, never `true`
 *
 * The last two are the point. `holder_alive: false` is the only thing that tells a queue on a
 * corpse from an ordinary queue, and a queue move must not erase it — which is precisely what the
 * cheap fix for the build break, a hardcoded `holder_alive: null` at line 149, would do.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  vi.setSystemTime(new Date('2026-07-28T10:00:00Z'))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

const HERE = dirname(fileURLToPath(import.meta.url))

/** The lane the rail draws as queued — the card whose sub-line reads the resource row. */
const QUEUED = lane({
  lane: 'lane/02', ticket_id: 'TKT-142', workspace: 'core',
  state: {
    process: 'not_started', motion: { value: null, silent_for_s: 0, since: null },
    evidence: 'nothing_claimed', blocked: null, alarm: false,
  },
  run_id: null, session_id: null, resources_held: [],
  waiting_for: { resource: 'climate-chamber', held_by: 'lane/01', holder_alive: false,
                 position: 1 },
})

/** The M8 frame, in `socket.schema.json`'s own shape — every member it has and no others. */
const m8 = (over: Record<string, unknown> = {}) => ({
  m: 'resource', ts: '2026-07-28T10:00:01Z', seq: null,
  name: 'climate-chamber', exclusive: true,
  description: 'one chamber, one run at a time', held_by: 'lane/01', queue: ['lane/02'],
  ...over,
})

/**
 * The rows the queued card reads, and the sub-line it draws out of them — one array, both
 * readings, so an assertion about the row is an assertion about what the card was given.
 */
function Probe() {
  const { lanes, resources } = useLanes()
  return (
    <div>
      <div data-testid="rows">{JSON.stringify(resources)}</div>
      {lanes.map((row) => (
        <div key={row.lane} data-testid="sub">{laneSub(row, true, resources)}</div>
      ))}
    </div>
  )
}

async function mount(rows: Array<Record<string, unknown>>) {
  FakeSocket.all = []
  stubFetch({
    '/api/health': { body: HEALTH },
    '/api/manifest': { body: MANIFEST },
    '/api/lanes': { body: laneSet([QUEUED], rows) },
  })
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  await act(async () => {
    render(<FactoryProvider><LanesProvider><Probe /></LanesProvider></FactoryProvider>)
  })
  await act(async () => { FakeSocket.last?.onopen?.() })
  await settle()
  return FakeSocket.last
}

const rows = () => JSON.parse(screen.getByTestId('rows').textContent!)
const chamber = () => rows().find((row: { name: string }) => row.name === 'climate-chamber')

describe('6.3 M8 resource · the row a queued card reads', () => {
  it('test_an_m8_frame_carries_whether_the_holder_is_alive', async () => {
    // E9 has already said this chamber is held by a lane whose process is gone.
    const socket = await mount([
      resource({ held_by: 'lane/01', holder_alive: false, queue: ['lane/02'] }),
    ])
    expect(chamber().holder_alive).toBe(false)
    expect(screen.getByTestId('sub')).toHaveTextContent('one chamber, one run at a time')

    // The queue moves. The frame names the SAME holder, so E9's answer about it still stands —
    // and the card still knows it is queued behind a corpse.
    await act(async () => { socket.deliver(m8({ queue: ['lane/02', 'lane/03'] })) })
    await settle()
    expect(chamber().holder_alive).toBe(false)
    expect(chamber().queue).toEqual(['lane/02', 'lane/03'])
    // the row really was patched by the frame, not left alone
    expect(chamber().description).toBe('one chamber, one run at a time')

    // Released. The frame says nobody holds it, which IS the liveness answer — and it is the same
    // one E9 renders for the same state (`render.cpp`: `heldBy.empty() ? null`).
    await act(async () => { socket.deliver(m8({ held_by: null, queue: [] })) })
    await settle()
    expect(chamber().held_by).toBeNull()
    expect(chamber().holder_alive).toBeNull()

    // Claimed by somebody else. Nothing has judged that holder, so the row says `unknown` —
    // never `true`, which is the invention this criterion exists to forbid.
    await act(async () => { socket.deliver(m8({ held_by: 'lane/09', queue: [] })) })
    await settle()
    expect(chamber().held_by).toBe('lane/09')
    expect(chamber().holder_alive).toBeNull()

    // And a resource this client has never read is `null` too, for the same reason.
    await act(async () => { socket.deliver(m8({ name: 'board-master', held_by: 'lane/04' })) })
    await settle()
    const board = rows().find((row: { name: string }) => row.name === 'board-master')
    expect(board.holder_alive).toBeNull()
  })

  it('test_m8_does_not_carry_holder_alive_and_the_client_may_not_invent_it', async () => {
    // MANDATORY NEGATIVE for the case above, and the reason it is written the way it is.
    //
    // `holder_alive` is not a member of M8 and a frame carrying one is refused by the contract,
    // so a client that read it off the frame would be reading a member no server may send. This
    // is the assertion that fails the day somebody "fixes" the case above by widening the socket
    // contract in a ticket that declared no seam.
    const schema = JSON.parse(readFileSync(join(HERE, '../../../docs/technical/contracts/socket.schema.json'), 'utf8'))
    const m8def = schema.$defs.message.resource
    expect(Object.keys(m8def.properties)).not.toContain('holder_alive')
    expect(schema.unevaluatedProperties).toBe(false)

    // ...and the client does not read one. `frame.holder_alive` anywhere in the M8 handler is the
    // shape this forbids; the value comes from E9's row or it is `null`.
    const source = readFileSync(join(HERE, '../src/services/lanes.tsx'), 'utf8')
    expect(source).not.toMatch(/frame\.holder_alive/)

    // The live consequence, run rather than described: a corpse-held row that receives a frame
    // whose holder is unchanged keeps `false`, and one whose holder changed does not keep it.
    const socket = await mount([
      resource({ held_by: 'lane/01', holder_alive: false, queue: ['lane/02'] }),
    ])
    await act(async () => { socket.deliver(m8({ holder_alive: true })) })
    await settle()
    expect(chamber().holder_alive).toBe(false)
  })
})
