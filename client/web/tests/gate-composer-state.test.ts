import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { producingControlCheck } from '../gate/fixture-checks.mjs'

describe('WEB-FIX-404 — the composer gate proves the state it names', () => {
  const controls = [{ label: 'Stop', mode: 'stop' }]
  const producing = { lane: 'lane/06', state: { process: 'running', motion: { value: 'producing' } } }
  const silent = { ...producing, state: { process: 'running', motion: { value: 'silent' } } }

  it('test_producing_control_requires_observed_running_producing_state', () => {
    const ready = producingControlCheck(producing, controls)
    expect(ready.passed, ready.name).toBe(true)
    expect(ready.detail).toContain('lane/06 is running/producing')
    const stale = producingControlCheck(silent, controls)
    expect(stale.passed, 'Stop alone cannot prove the lane is producing').toBe(false)
    expect(stale.detail).toContain('lane/06 is running/silent')
  })

  it('test_stale_premise_names_the_motion_and_does_not_weaken_the_control', () => {
    const stale = producingControlCheck(silent, [{ label: 'Send', mode: 'send' }])
    expect(stale.passed, stale.name).toBe(false)
    expect(stale.detail).toContain('Send (send) — lane/06 is running/silent')
    expect(producingControlCheck(undefined, controls).passed, 'no lane is no evidence').toBe(false)
    expect(producingControlCheck(producing, []).passed).toBe(false)
    expect(producingControlCheck(producing, [...controls, ...controls]).passed).toBe(false)
    expect(producingControlCheck(producing, [{ label: 'Send', mode: 'send' }]).passed).toBe(false)
    const stopped = { ...producing, state: { process: 'stopped', motion: { value: 'producing' } } }
    expect(producingControlCheck(stopped, controls).passed).toBe(false)
  })

  it('test_live_gate_uses_the_state_checked_verdict', () => {
    const source = readFileSync(join(__dirname, '../gate/browser-gate.mjs'), 'utf8')
    expect(source).toContain('producingControlCheck(')
    expect(source).toContain('ok(producingControl.name, producingControl.passed, producingControl.detail)')
    expect(source).toContain('the composer carries exactly ONE control, and it reads Send on a quiet lane')
    expect(source).toContain('mid-turn the SAME one control reads Stop, in the crash colour, in the same seat')
    expect(source).toContain('stop[0].colour !== quiet[0]?.colour')
    expect(source).toContain('stop[0].x === quiet[0]?.x && stop[0].y === quiet[0]?.y')
  })

  it('test_the_producing_fixture_has_a_finite_output_bound', () => {
    const source = readFileSync(join(__dirname, '../gate/browser-gate.mjs'), 'utf8')
    const command = /lane: producing, command: "([^"]+)"/.exec(source)?.[1] ?? ''
    const loop = /^for gate_tick in \$\(seq 1 (\d+)\); do printf '[^']+'; sleep 1; done$/.exec(command)
    expect(loop, 'the fixture must emit actual output in a bounded loop').not.toBeNull()
    expect(Number(loop?.[1])).toBeGreaterThan(0)
    expect(Number(loop?.[1])).toBeLessThanOrEqual(600)
  })
})
