import { useEffect } from 'react'

import { useFactory } from './factory'
import type { Scope } from '../types/socket'

/**
 * Subscribe this surface's scope for as long as it is mounted (M14).
 *
 * The subscription is why `M13 catalog.changed` arrives at all — it is the routing key, and
 * without it a document written by an agent would sit on disk with nothing on screen noticing.
 * Subscribing twice to one scope replaces the first (SRV-FEAT-005), so a re-mounting surface
 * cannot double-deliver.
 *
 * The `snapshot` the server answers with is deliberately not this client's data source. E3 takes
 * a query and a filter and a scope object has no room for either, so a list reading rows from a
 * snapshot and filtered rows from HTTP would have two code paths for one list and a count line
 * that came from whichever answered last. WEB-FEAT-013/014 will use the `lane` scope's snapshot,
 * which carries entries no endpoint here serves.
 */
export function useSubscription(scope: Scope) {
  const { subscribe } = useFactory()
  const key = 'requirements' in scope ? 'requirements'
    : 'ticket' in scope ? `ticket:${scope.ticket}` : `lane:${scope.lane}`

  useEffect(() => {
    subscribe(scope)
    // WEB-FIX-051 — this fires on a MOUNT and on nothing else.
    //
    // `socketState` used to be a dependency, so a reconnect re-ran it, and the reason given was
    // that a surface which mounted while the socket was down never got its subscribe out. That
    // reason does not hold: `subscribe()` registers the scope whether or not the frame can be
    // sent, and `socket.ts`'s `restore()` walks every registered scope on open. The reconnect was
    // covered twice.
    //
    // It became harmful the moment a surface's subscribe stopped deciding between `subscribe` and
    // `resume`. A reconnect now sends BOTH — `restore()`'s `resume`, asking for the tail, and this
    // hook's `subscribe`, asking for a full snapshot that replaces it — so the replay path
    // `SRV-FEAT-005` exists for is paid for and then thrown away.
    // `transcript.test.tsx::a_reconnect_still_resumes` fails if the dependency comes back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, subscribe])
}
