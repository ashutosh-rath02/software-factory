import { useLanes } from '../services/lanes'
import type { Route } from '../routes'

/**
 * `Shell · Nav` — the design's 300px left rail (`factory.pen`, frame `yKLSZ`).
 *
 * TWO DELIBERATE DEPARTURES FROM THE DESIGN, both stated rather than quietly taken.
 *
 * 1. ONE COUNT, AND ONLY THE ONE THAT IS FREE. The design shows `Epics 7`, `Tickets 135`,
 *    `Lanes 2/3`, `Escapes 174`. Every one costs a request, and this client's rule is that a
 *    surface makes EXACTLY ONE — `dashboard.test.tsx::test_the_screen_makes_exactly_one_request`
 *    and `no-poll.test.tsx` enforce it, and a rail on every surface fetching for itself adds a
 *    second request to every surface.
 *
 *    `Lanes` is the exception because `LanesProvider` ALREADY reads `E9` shell-wide for §4.1's
 *    counter, so the number is on hand and reading it costs nothing. The other three would each
 *    open a connection; they wait for `WEB-FEAT-314`'s shared load. Improvising underneath a rule
 *    three cases defend is how a gate gets deleted for being noisy.
 * 2. EVERY DESTINATION THE DESIGN NAMES IS NOW REACHABLE. They are drawn as declared and unreachable.
 *    A rail that silently omits half the design is one nobody can tell is incomplete; one that
 *    says `soon` states what it owes.
 */
type Item = { label: string; route?: Route['name']; hash?: string; soon?: boolean;
              count?: string }

const ITEMS: Item[] = [
  { label: 'Dashboard', route: 'home', hash: '#/' },
  { label: 'Delivery', route: 'delivery', hash: '#/delivery' },
  { label: 'Epics', route: 'requirements', hash: '#/requirements' },
  { label: 'Tickets', route: 'tickets', hash: '#/tickets' },
  { label: 'Lanes', route: 'lanes', hash: '#/lanes' },
  { label: 'Runs', route: 'runs', hash: '#/runs' },
  { label: 'Wiki', route: 'wiki', hash: '#/wiki' },
  { label: 'Escapes', route: 'escapes', hash: '#/escapes' },
  { label: 'Metrics', route: 'metrics', hash: '#/metrics' },
  { label: 'Seams', route: 'seams', hash: '#/seams' },
  { label: 'Orchard', route: 'orchard', hash: '#/orchard' },
  { label: 'Vocabulary', route: 'vocabulary', hash: '#/vocabulary' },
  { label: 'Permissions', route: 'permissions', hash: '#/permissions' },
]

export function SideNav({ current }: { current: Route['name'] }) {
  const { lanes, loaded, seats } = useLanes()
  // The seat total is the MANIFEST's, carried through E9. It used to be `Math.max(lanes.length, 3)`
  // — a number this component invented, which read `0/3` on a host declaring ten seats and would
  // have read `7/7` on the same host with seven lanes. A rail that computes a total can disagree
  // with the tool about how much room there is.
  const laneCount = loaded && seats !== null ? `${lanes.length}/${seats}` : undefined

  return (
    <nav className="sidenav" data-testid="sidenav" aria-label="surfaces">
      {ITEMS.map(raw => {
        const item = raw.label === 'Lanes' ? { ...raw, count: laneCount } : raw
        const active = item.route !== undefined && item.route === current
        const cls = ['sidenav-row', active ? 'is-active' : '', item.soon ? 'is-soon' : '']
          .filter(Boolean).join(' ')
        const body = (
          <>
            <span className="sidenav-label">{item.label}</span>
            {item.count !== undefined && <span className="sidenav-count">{item.count}</span>}
            {item.soon && <span className="sidenav-soon">soon</span>}
          </>
        )
        return item.soon || !item.hash
          ? <span key={item.label} className={cls} aria-disabled="true">{body}</span>
          : <a key={item.label} className={cls} href={item.hash}
               aria-current={active ? 'page' : undefined}>{body}</a>
      })}
    </nav>
  )
}
