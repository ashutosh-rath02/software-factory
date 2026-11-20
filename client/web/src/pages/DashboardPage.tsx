import { DashboardLanes } from '../components/DashboardLanes'
import { useCallback } from 'react'

import { EmptyBlock, Placeholder, ReconnectBlock } from '../components/Blocks'
import { tintFor, tone } from '../components/Chips'
import { RemedyControl } from '../components/Remedy'
import { getHome } from '../services/http'
import { useEndpoint, useFactory } from '../services/factory'
import { useSubscription } from '../services/subscribe'
import {
  clauseLabel, clauseValue, deliveryCellLabel, deliveryCellTone, deliveryValue,
  homeEmptyTitle, homeIsEmpty,
} from '../vocabulary'
import { href } from '../routes'
import { ago } from './RequirementsPage'
import type { DeliveryCount, NeedsYouRow, UnpushedRow } from '../types/api'

/**
 * SPEC.md §4.18 — the first screen, and the only one whose question is *what should I do next*.
 *
 * Three panels, ONE request, and no panel that spends money or moves a branch. `E30` answers all
 * three because three endpoints would be three answers about one repository at three instants:
 * the counts in the first panel and the rows in the second would be free to disagree while each
 * stayed correct.
 *
 * The row is `delivery`'s values and not §3.2b's nine cells, and §4.18 carries the reason in the
 * document rather than here: those nine predicates read `lane · process · role · verdict ·
 * delivery · ledger` per ticket, `E30` serves none of that per ticket, and no endpoint anywhere
 * serves a verdict or the escape ledger. `SRV-FEAT-363` puts the axes in this same one read, and
 * the strip becomes the nine cells then — computed in the one module §3.2b names, out of this one
 * answer, and never out of a second request.
 */
export function DashboardPage() {
  const { socketState } = useFactory()

  useSubscription({ requirements: true })

  const load = useCallback(() => getHome(), [])
  const { result, loading } = useEndpoint(
    'home',
    load,
    // every document kind moves a count here: an epic is a ticket's parent, a ticket is a row,
    // and a report changes the evidence a §3.3 clause is derived from
    () => true,
    // §4.18's other live source. A lane going idle is not a document changing, and recomputing
    // §3.3 in the browser instead of asking again would be a second implementation of the
    // definition this whole surface is about.
    (frame) => frame.m === 'lane.state',
  )

  const dim = socketState === 'lost'
  const home = result?.ok ? result.data : undefined
  const nothing = home !== undefined && homeIsEmpty(home)

  return (
    <div className="surface surface-scroll" data-testid="surface">
      {dim && <ReconnectBlock />}

      {loading && <Placeholder what="reading the repository…" />}

      {result?.ok && result.data.warnings?.map((warning) => (
        <div className="perms-warning" data-testid="catalogue-warning" key={warning.message}>
          {warning.message}
          {warning.pointer ? ` — ${warning.pointer}` : (warning.path ? ` — ${warning.path}` : '')}
        </div>
      ))}

      {result && !result.ok && (
        <EmptyBlock title="THE SCREEN COULD NOT BE READ">
          {result.error?.message ?? `The server answered ${result.status}.`}
        </EmptyBlock>
      )}

      {/* The design's `Dashboard · Empty` frame, and its copy verbatim. It is one statement and
          not two: a row of seven zeros beside a block saying there is nothing would be the screen
          answering the same question twice.

          It says TICKETS, and that is the whole of the frame's 2026-09-02 edit: `renderHome`
          builds every member of this answer from `Store::instance().tickets()` and never reads a
          requirement, so a block claiming there are no requirements would be a claim about a
          collection this answer does not contain — true of a repository with twelve epics and no
          tickets, which is the state of any repository between `/split-requirement` and the first
          `/new-ticket`. An empty state is a claim about a collection being empty; it is made about
          the collection that was counted.

          The sentence and the condition both come from `vocabulary.ts` now (WEB-FIX-002), and
          neither is written here. They were written here, agreeing with each other and about
          different collections, and no case could see the difference.

          It carries the outbound edge too, because it REPLACES the panel that had it: `all
          requirements →` lives in the strip's header and the strip is exactly what an empty
          repository does not draw. §4.18's landing route is where every unknown hash resolves,
          and on a fresh repository it had nothing on it that went anywhere. */}
      {nothing && (
        <>
          <EmptyBlock title={homeEmptyTitle()} testId="home-empty">
            <code>docs/tickets</code> is declared and readable, and holds no document the tool
            {' '}counts. _TEMPLATE.md and README.md are forms, not work — they are never counted.
          </EmptyBlock>
          {/* Beside the block and not inside it. The block is a CLAIM about a collection and the
              rule `emptyBlockFaults` enforces is about the nouns a claim may use; a destination is
              not a claim, and a link labelled `all requirements →` inside the sentence would make
              the one thing that says what was counted read as though it were about requirements
              again. */}
          <div className="home-empty-more" data-testid="home-empty-more">
            <a href={href({ name: 'requirements' })}>all requirements →</a>
          </div>
        </>
      )}

      {home && !nothing && (
        <div className="home" data-testid="home-panels" data-dim={String(dim)}>
          <Strip cells={home.delivery} />

          <section className="home-panel" data-testid="needs-you-panel">
            <div className="home-head">
              <h4 className="home-title">Needs you</h4>
              <span className="home-count" data-testid="needs-you-count">
                {home.needs_you.total}
              </span>
              <span className="home-note">nothing here spends money on its own</span>
            </div>
            {home.needs_you.rows.length === 0
              ? (
                <div className="home-quiet" data-testid="needs-you-empty">Nothing needs you.</div>
              )
              : (
                <div className="home-rows">
                  {home.needs_you.rows.map((row) => (
                    <NeedsRow key={row.ticket_id} row={row} disabled={dim} />
                  ))}
                </div>
              )}
          </section>

          <section className="home-panel" data-testid="unpushed-panel">
            <div className="home-head">
              <h4 className="home-title">Unpushed work</h4>
              <span className="home-count" data-testid="unpushed-count">
                {home.unpushed.total}
              </span>
              {/* It NAMES them and does not push them: §3.5's read-only property is six git
                  subcommands, served at E1 so it can be asserted, and one write verb here would
                  end that guarantee for the whole product in exchange for one button. */}
              <span className="home-note">named, never pushed — nothing here writes</span>
            </div>
            {home.unpushed.rows.length === 0
              ? (
                <div className="home-quiet" data-testid="unpushed-empty">
                  Everything committed has left this machine.
                </div>
              )
              : (
                <div className="home-rows">
                  {home.unpushed.rows.map((row) => <AheadRow key={row.ticket_id} row={row} />)}
                </div>
              )}
          </section>
        </div>
      )}

      {/* `Dashboard · Lanes` (`factory.pen`, frame `mTLsb`) — it fetches nothing: `LanesProvider`
          already reads E9 shell-wide. `Dashboard · Tickets by epic` is NOT here: it needs E29, and
          §1 criterion 6 says a dashboard that fetches E29 or E9 for itself fails. It has its own
          surface at `#/tickets`, which is the destination the design's rail already names. */}
      <DashboardLanes />
    </div>
  )
}

/**
 * §4.18 — one cell per `delivery` value, in the order `E30` sent them, which is §3.5's table
 * order. Every entry, including the empty ones: a row of counts that omits its empty members is a
 * row whose shape changes under the reader.
 *
 * The states §3.3 counts are tinted — the design's own sentence, *"tinted stages are waiting on a
 * person, not on an agent"* — and `DELIVERY` already marks exactly those two as an alarm, so the
 * tint reads that rather than a second list of which states matter.
 */
function Strip({ cells }: { cells: DeliveryCount[] }) {
  return (
    <section className="home-panel" data-testid="delivery-strip">
      <div className="home-head">
        <h4 className="home-title">Where the work is</h4>
        <span className="home-note">
          what git and the process table say became of each ticket · one axis, not a pipeline
        </span>
        {/* §4.18's cells are specified as links into §4.17, which has no surface in this client
            yet. Until it does, the panel carries the one destination that exists — §4.2's list —
            rather than seven controls that would go nowhere. */}
        <a className="home-more" href={href({ name: 'requirements' })}>all requirements →</a>
      </div>
      <div className="strip">
        {cells.map((cell) => {
          const colour = deliveryCellTone(cell.state)
          // The two §3.3 counts, and `DELIVERY` already knows which they are: `alarm` is
          // `stalled` and `unshipped` and nothing else. A second list here would be a second
          // answer to "which states are waiting on a person".
          const tinted = deliveryValue(cell.state)?.alarm === true
          return (
            <div key={cell.state} className="strip-cell" data-testid="strip-cell"
              data-state={cell.state} data-tinted={String(tinted)}
              style={tinted ? tone(colour, tintFor(colour)) : tone(colour)}>
              <span className="strip-top">
                <span className="chip-dot" style={tone(colour)} />
                <span className="strip-count" data-testid="strip-count">{cell.count}</span>
              </span>
              <span className="strip-label">{deliveryCellLabel(cell.state)}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/**
 * One ticket that needs a person, and WHY — §3.3's clauses by name, and the server's own sentence.
 *
 * The sentence is `E30`'s `detail` rendered as given. A client that composed one out of the clause
 * names would be a second implementation of §3.3's words, and the two would drift on the day a
 * clause is added.
 */
function NeedsRow({ row, disabled }: { row: NeedsYouRow; disabled: boolean }) {
  const first = row.clauses.map(clauseValue).find(Boolean)
  const colour = first?.tone ?? 'var(--st-none)'
  return (
    <div className="home-row" data-testid="needs-you-row" data-clauses={row.clauses.join(' ')}>
      <span className="chip-dot" style={tone(colour)} />
      <div className="home-row-text">
        <div className="home-row-line">
          <span className="home-row-label" style={tone(colour)}>
            {row.clauses.map(clauseLabel).join(' · ')}
          </span>
          <a className="home-row-ref" href={href({ name: 'ticket', id: row.ticket_id })}>
            {row.ticket_id}
          </a>
          <span className="home-row-title">{row.title}</span>
        </div>
        <div className="home-row-note" data-testid="needs-you-detail">{row.detail}</div>
      </div>
      <RemedyControl remedy={row.remedy} testId="needs-you-remedy" announceCost
        disabled={disabled}
        addressFor={() => href({ name: 'ticket', id: row.ticket_id })} />
    </div>
  )
}

/**
 * §4.18's third panel. `age_s` is how long the OLDEST of these commits has been sitting here —
 * `unpushedRow`'s own reason is that the panel's job is that nobody discovers a week of work
 * living on one laptop, and the newest commit's age cannot answer that.
 */
function AheadRow({ row }: { row: UnpushedRow }) {
  return (
    <div className="home-row" data-testid="unpushed-row">
      <span className="chip-dot" style={tone('var(--st-claim)')} />
      <div className="home-row-text">
        <div className="home-row-line">
          <a className="home-row-ref" href={href({ name: 'ticket', id: row.ticket_id })}>
            {row.ticket_id}
          </a>
          <span className="home-row-title">{row.title}</span>
        </div>
        <div className="home-row-note">
          <code>{row.branch}</code> · {row.commits} {row.commits === 1 ? 'commit' : 'commits'}
          {' '}· oldest {agoFrom(row.age_s)}
        </div>
      </div>
    </div>
  )
}

/** `ago()` takes an instant and this row carries a duration; one formatter, two doors. */
function agoFrom(seconds: number): string {
  return ago(new Date(Date.now() - Math.max(0, seconds) * 1000).toISOString())
}
