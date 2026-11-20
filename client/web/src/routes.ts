/**
 * Eight addressable surfaces, in the hash. No router library: the whole of what a router would
 * do here is a switch over four shapes, and a dependency that owns the address bar is a
 * dependency every later ticket has to reason about.
 *
 * The hash matters — the built client is a static bundle served by whatever is in front of it,
 * and a path-based route would need that server to know about the client's routes.
 *
 * The count is ADDRESSES and not route names: `#/tickets/TKT-142` and `#/tickets/TKT-142/session`
 * are two addresses of one surface. `routes.test.ts` reads this sentence, so a route added without
 * it turns red rather than leaving a comment that quietly stopped being true.
 */

export type Route =
  /**
   * SPEC.md §4.18 — the first screen, and what an unknown hash resolves to (WEB-FEAT-001). It
   * takes no id and it is the bare `#/`: the whole of it is the answer to one question, and the
   * question is *what should I do next*.
   */
  | { name: 'home' }
  | { name: 'requirements' }
  | { name: 'tickets' }
  | { name: 'runs' }
  | { name: 'escapes' }
  /** SPEC.md §4.21 — how the process itself is doing, over a window a person chooses. */
  | { name: 'metrics' }
  | { name: 'wiki' }
  | { name: 'seams' }
  | { name: 'orchard' }
  | { name: 'delivery' }
  | { name: 'requirement'; id: string }
  /**
   * `tab` is an OPAQUE string this module neither validates nor defaults. The tab registry
   * (`components/tabs.tsx`) is the only place a tab id exists, and a router that knew one would be
   * a second file every new tab has to edit — which is exactly what four parallel tickets cannot
   * afford. An absent or unknown segment resolves to the registry's first row, there.
   */
  | { name: 'ticket'; id: string; tab?: string; lane?: string }
  | { name: 'vocabulary' }
  | { name: 'permissions' }
  /**
   * SPEC.md §4.14 — every lane in the repository (WEB-FEAT-050). It takes no id: the whole of it
   * is the answer to one question, and there is nothing to address inside it.
   */
  | { name: 'lanes' }

/** Where an address nobody recognises lands, and where the client opens. */
export const HOME: Route = { name: 'home' }

/**
 * §4.2's list, which used to be `HOME` and is not any more.
 *
 * The breadcrumb's first crumb is still `requirements` and still goes to the requirements list —
 * a crumb reading one word and navigating somewhere else is worse than no crumb.
 */
export const REQUIREMENTS: Route = { name: 'requirements' }

export function parse(hash: string): Route {
  // `?lane=` picks which of a ticket's lanes the screen shows; every other route ignores it.
  const [path, query = ''] = hash.split('?', 2)
  const lane = new URLSearchParams(query).get('lane') ?? undefined
  const parts = path.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
  if (parts[0] === 'vocabulary') return { name: 'vocabulary' }
  if (parts[0] === 'permissions') return { name: 'permissions' }
  if (parts[0] === 'lanes') return { name: 'lanes' }
  if (parts[0] === 'runs') return { name: 'runs' }
  if (parts[0] === 'escapes') return { name: 'escapes' }
  if (parts[0] === 'metrics') return { name: 'metrics' }
  if (parts[0] === 'wiki') return { name: 'wiki' }
  if (parts[0] === 'seams') return { name: 'seams' }
  if (parts[0] === 'orchard') return { name: 'orchard' }
  if (parts[0] === 'delivery') return { name: 'delivery' }
  if (parts[0] === 'requirements') {
    return parts[1] ? { name: 'requirement', id: parts[1] } : REQUIREMENTS
  }
  if (parts[0] === 'tickets') {
    // `#/tickets` is the index; `#/tickets/<id>` is one ticket. Two addresses, two surfaces.
    return parts[1] ? { name: 'ticket', id: parts[1], tab: parts[2], lane } : { name: 'tickets' }
  }
  return HOME
}

export function href(route: Route): string {
  switch (route.name) {
    case 'home': return '#/'
    case 'requirements': return '#/requirements'
    case 'tickets': return '#/tickets'
    case 'runs': return '#/runs'
    case 'escapes': return '#/escapes'
    case 'metrics': return '#/metrics'
    case 'wiki': return '#/wiki'
    case 'seams': return '#/seams'
    case 'orchard': return '#/orchard'
    case 'delivery': return '#/delivery'
    case 'requirement': return `#/requirements/${encodeURIComponent(route.id)}`
    case 'ticket': return `#/tickets/${encodeURIComponent(route.id)}`
      + (route.tab ? `/${encodeURIComponent(route.tab)}` : '')
      + (route.lane ? `?lane=${encodeURIComponent(route.lane)}` : '')
    case 'vocabulary': return '#/vocabulary'
    case 'permissions': return '#/permissions'
    case 'lanes': return '#/lanes'
  }
}
