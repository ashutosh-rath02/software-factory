import { TicketsByEpic } from '../components/TicketsByEpic'

/**
 * `#/tickets` — the design's `Tickets` destination (`factory.pen` rail `yKLSZ`, table `DAiio`).
 *
 * The table is a surface of its own rather than a block on the dashboard. The design draws it
 * inside `SCREEN · Dashboard`, but §1 criterion 6 of §4.18 forbids that screen fetching `E29`,
 * and it is right to: the dashboard answers *what should I do next* from ONE read, and a second
 * connection makes two counts on one screen true at two different moments. The rail already
 * names `Tickets` as its own destination, so the design's own navigation is where it goes.
 */
export function TicketsPage() {
  return <div className="page" data-testid="tickets-page"><TicketsByEpic /></div>
}
