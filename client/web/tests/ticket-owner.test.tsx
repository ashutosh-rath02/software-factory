import { describe, it, expect, afterEach } from 'vitest'
import { screen, cleanup } from '@testing-library/react'

import { mountApp, settle, ticketDoc, ticketRoutes } from './helpers'

/**
 * WEB-FEAT-325 · §1 — the tool knows who wrote every ticket and no screen has ever shown it.
 *
 * `owner` is `{name, source}` on `E5` and `E29`, derived by the server from the branch's commit
 * authors. The design puts it in the identity row as `owner <name> · <source>` — `factory.pen`
 * frame `maEaE`, beside the workspace and parent chips.
 */
const OWNED = ticketDoc({ owner: { name: 'Member Dev', source: 'commits' } })
const NOBODY = ticketDoc({ owner: null })
const STRANGE = ticketDoc({ owner: { name: 'Member Dev', source: 'frontmatter-v2' } })

afterEach(cleanup)

describe('WEB-FEAT-325 · the screen says who wrote it', () => {
  it('test_the_header_names_the_owner', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(OWNED, []))
    await settle()
    const chip = screen.getByTestId('ticket-owner')
    expect(chip).toHaveTextContent('Member Dev')
    // `source` is SHOWN, not hidden: `commits` is what git says and a future `frontmatter` is what
    // a person wrote down. A surface that flattens them asserts they are the same claim.
    expect(chip).toHaveTextContent('commits')
    // it belongs to the identity row, with the workspace and the parent — not a footnote
    expect(screen.getByTestId('ticket-identity').contains(chip)).toBe(true)
  })

  it('test_no_owner_no_chip', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(NOBODY, []))
    await settle()
    // not `owner —`, not `unknown`. `owner` is null until a commit exists, and a chip claiming an
    // absent fact is worse than no chip.
    expect(screen.queryByTestId('ticket-owner')).toBeNull()
  })

  it('test_an_unknown_source_is_printed_not_translated', async () => {
    await mountApp('#/tickets/TKT-142', ticketRoutes(STRANGE, []))
    await settle()
    // MANDATORY NEGATIVE. A client that mapped sources onto friendly words would silently print
    // the wrong one for a source it has never seen — the tool knows none of the repository's
    // vocabulary, exactly as `display.ticket_fields` reaches E5's `extras` untouched.
    expect(screen.getByTestId('ticket-owner')).toHaveTextContent('frontmatter-v2')
  })
})
