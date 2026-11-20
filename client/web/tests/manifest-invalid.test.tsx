import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { mountApp, settle, requirement } from './helpers'

/**
 * SRV-FIX-071 — a failed load and an empty repository are not the same screen.
 *
 * `GET /api/manifest` answers E2's refusal; every other endpoint answers `200` with empty data.
 * Before this, that meant the first surface a person opens said `NO REQUIREMENTS ... a directory
 * that is not there is the usual reason` — a confident, wrong explanation — while the real reason
 * sat in an endpoint no screen reads.
 *
 * In this repository the trigger is ordinary: `.factory/factory.json` declares a device, so an
 * unplugged ESP32 or a shifted enumeration order empties the catalogue.
 */

afterEach(cleanup)

const INVALID = {
  code: 'invalid_manifest',
  message: 'this resource declares a device that is not on this machine; plug it in or remove it',
  pointer: '/resources/board/devices/0',
  path: '/dev/ttyUSB-unplugged',
}

const routes = (warnings: unknown[]) => ({
  '/api/requirements': { status: 200, body: { requirements: [], total: 0, in_flight: 0, warnings } },
})

describe('4.2 · a manifest that did not load', () => {
  it('a_surface_whose_manifest_failed_to_load_says_so_instead_of_showing_nothing', async () => {
    await mountApp('#/requirements', routes([INVALID]))
    await settle()
    await settle()

    const said = screen.getByTestId('catalogue-warning')
    expect(said).toHaveTextContent(INVALID.message)
    // the pointer is what makes it actionable — a person has to know which key to open
    expect(said).toHaveTextContent(INVALID.pointer)

    // ...and the confident wrong explanation is not offered beside it
    const empty = screen.queryByTestId("empty")
    expect(empty?.textContent ?? '').not.toContain('a directory that is not there')
  })

  it('an_ordinary_empty_catalogue_keeps_its_ordinary_empty_state', async () => {
    // NEGATIVE (mandatory). The cheap fix is error chrome on every empty list, which would teach
    // a reader to ignore the one that matters.
    await mountApp('#/requirements', routes([]))
    await settle()
    await settle()

    expect(screen.queryByTestId('catalogue-warning')).toBeNull()
    expect(screen.getByTestId("empty")).toHaveTextContent('NO REQUIREMENTS')
  })

  it('a_populated_catalogue_with_a_warning_still_lists_its_rows', async () => {
    // The warning explains an EMPTY list; it must not replace a populated one. `path_missing` on
    // one docs directory while the others load is exactly that case.
    await mountApp('#/requirements', {
      '/api/requirements': {
        status: 200,
        body: {
          requirements: [requirement()], total: 1, in_flight: 0,
          warnings: [{ code: 'path_missing', path: '/repo/docs/reports',
                       message: '"/repo/docs/reports" is not a directory' }],
        },
      },
    })
    await settle()
    await settle()

    expect(screen.getAllByTestId('requirement-row')).toHaveLength(1)
    expect(screen.getByTestId('catalogue-warning')).toHaveTextContent('is not a directory')
  })
})
