import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup, render, within } from '@testing-library/react'

import { mountApp, MANIFEST } from './helpers'
import { AXES, axisValue, type AxisName } from '../src/vocabulary'
import { StateChip, StateDot } from '../src/components/Chips'

afterEach(cleanup)

const AXIS_NAMES: AxisName[] = ['process', 'motion', 'evidence']

describe('4.11 vocabulary sheet', () => {
  it('renders_from_the_shared_constants', async () => {
    await mountApp('#/vocabulary', {})
    await screen.findByTestId('axis-process')

    for (const axis of AXIS_NAMES) {
      const panel = screen.getByTestId(`axis-${axis}`)
      const rows = within(panel).getAllByTestId('axis-value')

      // the sheet lists this axis and nothing else: no extra row, no missing one
      expect(rows).toHaveLength(AXES[axis].length)

      AXES[axis].forEach((entry, i) => {
        expect(rows[i]).toHaveTextContent(entry.label)
        expect(rows[i]).toHaveTextContent(entry.note)

        // the SAME object, not a parallel list that happens to agree today
        expect(axisValue(axis, entry.value)).toBe(entry)
      })
    }

    // the stage-sequence sample comes from E2, which is the only request this surface makes
    const strip = screen.getByTestId('stage-strip')
    for (const stage of MANIFEST.stages) expect(strip).toHaveTextContent(stage)

    // the six lane-card states are lane cards, not a separate component
    expect(screen.getAllByTestId('lane-card')).toHaveLength(6)
  })

  it('a_state_no_one_put_on_the_sheet_cannot_be_rendered', () => {
    // The teeth of the rule above. A component that invents a state has nowhere to read its
    // label, its note or its colour from, so it renders nothing rather than something wrong —
    // and this case fails the day someone adds a state to one and not the other.
    expect(axisValue('process', 'hibernating')).toBeUndefined()

    render(<div>
      <StateChip axis="process" value="hibernating" />
      <StateDot axis="evidence" value="invented" />
    </div>)
    expect(screen.queryByTestId('state-chip')).toBeNull()
    expect(screen.queryByTestId('state-dot')).toBeNull()

    cleanup()
    render(<StateChip axis="evidence" value="claimed_unproven" />)
    expect(screen.getByTestId('state-chip'))
      .toHaveTextContent(axisValue('evidence', 'claimed_unproven')!.label)
  })

  it('every_value_SPEC_3_2_names_is_on_the_sheet', () => {
    // SPEC.md §3.2's three axes, in full. The design drew five process values; the
    // specification has eight, and the sheet is what an implementation is gated against.
    expect(AXES.process.map((v) => v.value)).toEqual([
      'not_started', 'queued', 'running', 'blocked', 'idle', 'finished', 'crashed', 'killed'])
    expect(AXES.motion.map((v) => v.value)).toEqual(['producing', 'silent'])
    expect(AXES.evidence.map((v) => v.value)).toEqual([
      'nothing_claimed', 'claimed_unproven', 'claimed_proven'])
  })
})
