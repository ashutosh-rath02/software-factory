# E26 — the tool allocates a ticket id, once

The decisions behind `app/catalog/ids.{h,cpp}`, moved out of the code by `SRV-FEAT-229` so the
source carries no comments and the reasoning survives anyway.

## What it replaces

`NNN = highest taken + 1`, computed by each caller from the files it could see. Every caller is in
a lane; a lane is a checkout cut at claim time, and nothing merges into a live one. Two lanes
filing within one window counted two different sets, answered the same number, and **were both
right**. `SRV-FIX-188` is the instance: two lanes filed `SRV-FIX-185` an hour apart, the merge had
no conflict, and both got `409 ambiguous_ticket_id`.

The fix is not a lock file and not a wider glob — those are the same read-then-write with a wider
read. The question moves to the one process that can see every declared repository at once.

## Why a component and not a function

An id handed out is **spent** whether or not a document is ever written with it. A tool that
reissues what a crashed agent never wrote reintroduces the collision on the recovery path, so the
answer has to outlive the request.

That is also why the ledger is written *before* the caller is answered: an id is on disk before
anybody learns it. The cost is a gap in the sequence when an agent dies; the alternative is a
duplicate, and a gap is cheap.

## The counter belongs to the SERIES, not the type

A ticket id is `[<series>-]<type>-<number>`. `FAC-FEAT`, `FAC-FIX` and `FAC-REF` draw from one
sequence; `FEAT`, `FIX` and `REF` from another. A counter per *type* hands out a number another
type has already spent — the same collision one column to the left.

## A prefix nothing uses gets no counter

Starting a series on demand is the defect wearing a different hat: the id looks allocated,
collides with nothing, and means nothing. An unknown prefix is refused with the known set, which
is why `FAC-REF` is rejected today — no document uses it yet.

## Three digits

Because that is how every id in every repository this has been pointed at is written. An allocator
whose output does not look like what is already on disk is one a person edits by hand.

## Threading

`spent_` and the write queue live on ONE `core::Queue` and are reached from nowhere else. No mutex:
a mutex here would block an io thread in order to serialise an increment, and there is already an
executor for that.

The disk is touched from a `core::Worker` at both ends — the survey of every declared repository's
ids on the way in, the ledger on the way out. **The allocation itself is the only step that must be
serialised, and it is the one step that touches no disk.**

**One ledger write in flight, never two**, with everything that arrived meanwhile riding the next
one. A per-allocation write posted to a worker *pool* can complete out of order, and the last to
land would then be the older snapshot — a ledger that goes backwards is a ledger that reissues.
Each write carries the whole ledger for the same reason.

## Shutdown leaves nobody waiting

A caller blocked on a ledger write while the process is going away gets the failure rather than
silence. Its id was allocated in memory and never recorded, and saying so is the only answer that
does not risk it being handed out twice.

## The ledger is not the repository's record

`.factory/ids.json` is the tool's own memory of what it reserved. The high-water mark still comes
from the documents on disk; the ledger only remembers ids reserved and never written. A ledger
that is absent or unreadable is therefore **not an error** — the sequence restarts from the
highest id on disk, and all that is lost is the memory of the gaps.

## The ledger is the counter (`SRV-FIX-207`, and round 1's QA FAIL)

`init` read `.factory/ids.json` once into `spent_` and every allocation after that answered
`max(highest id on disk, spent_[series]) + 1` from that in-memory map. One server serialises its
own callers on its own queue, so eight threads at one instance were always right — and one process
out there was no counter at all. QA measured it on the product: two servers, one repository, six
`POST /api/tickets/ids` each from a barrier, **twelve allocations and six ids, every one handed out
twice**. The ledger afterwards held only the high-water mark, which is what made the duplicates
invisible to anything reading it later.

So the ledger IS the counter, and it is read, advanced and written inside one exclusion the
FILESYSTEM arbitrates: `O_CREAT|O_EXCL` on `<ids.json>.lock`, taken before the survey is consulted
and released after the write lands. Two servers are two processes and no in-process primitive
reaches them; `CLAUDE.md` forbids a mutex in event-driven code besides, and this is not one — it is
a lock between processes, held on a worker, never on a handler thread.

`spent_` survives as a cache of the last answer THIS process gave, and is never consulted for the
next one. When the lock cannot be taken within `LOCK_TRIES × LOCK_WAIT_US` (2 s), `E26` answers
`write_failed` and allocates nothing: an id the tool cannot record is an id it will hand out again,
so refusing is the only safe answer and guessing is the shape the fix must not take.
