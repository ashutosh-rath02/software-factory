# Delivery — what actually happened, and who is allowed to say so

## Role receipts after a turn (SRV-FIX-586)

After API persists a completed role turn's branch tip, it requests a delivery scan. A request
arriving during a scan schedules one follow-up pass, so the receipt cannot be missed by a scan
that began before it was written. The snapshot callback still supplies role-session receipts to
the lander, including when Git's fingerprint has not changed since the earlier scan.

`docs/product/SPEC.md` §3.5. The module answers one question per ticket: **did the work leave this machine.** A
document's `status:` field is a claim written by a session that may not have survived to write it
truthfully, so the answer is derived — from git, and from the lane set the tool owns.

## The rule that changed, and why it had to

`SRV-FIX-453`. Rule 1 used to read:

> `running` — a live process whose program is the manifest's `agent.command` carries the ticket id
> on its command line.

and `scan_()` implemented it by walking `/proc` and keeping every process whose program basename
matched `agent.command`, then settling `running` on the first whose command line named the ticket
as a whole word.

**That set is not the set of lanes.** It contains an operator's own interactive session when its
prompt names the ticket, a QA session started outside any lane, and anything else on the machine
whose argv happens to carry the id — and it does **not** contain a lane whose agent runs under a
different program name inside its confinement, which every lane in this repository does: the
manifest's command is a script, the kernel rewrites a script's argv to its interpreter, and the
process reads as `sh`. Both errors were on screen at once:

```
gate/shots/empty-send-starts-a-run-dark.png, TKT-155
  delivery badge   ● running            process chip   CRASHED MID-TURN
gate/shots/requirement-detail-live-dark.png, TKT-142
  lane             running · producing  delivery badge unshipped · document says todo
```

`docs/technical/MODEL.md` §2 `S5` had already settled the question this was answering for itself:

> Whether a lane is live is answered by `GET /api/lanes` and by nothing else.

A second authority on one fact is not a redundancy, it is a disagreement waiting for a screen to
show both halves of. Rule 1 is now:

> `running` — `E9` lists a lane holding this ticket whose `state.process` is `running`, `blocked`
> or `idle`.

`detail` names it: `lane <name> holds it and its agent is alive`.

## The direction the dependency points

`api ──▶ delivery · lane`, and the arrow is not bent to fix this. `delivery` does not include
`app/lane`; it is **handed** the answer:

```cpp
using LiveLanes = std::map<std::string, std::string>;   // ticket id → the lane holding it
static State decide(const Snapshot&, const Subject&, const LiveLanes& live);
```

The probe keeps owning the ORDER of rules 1–6 — that ordering is the whole of §3.5 and it belongs
in one place. What it no longer owns is where rule 1's evidence comes from.

`api::liveLanes()` builds the map in `api/lane-view.cpp`, beside `api::laneLookup`, which already
reads the lane snapshot the same way. A record is alive when
`lane::axesFor(record, nullptr, nullptr).process` is `running`, `blocked` or `idle` — the field
`E9` itself publishes, taken from `lane::state::axesOf`, so the badge and the rail cannot disagree
by construction. `not_started`, `queued`, `finished`, `crashed` and `killed` are not alive. Where
more than one lane holds a ticket the map keeps the lowest-ranked, `running < blocked < idle`.

`api::withDelivery` is the join: it fetches the lane snapshot beside the probe's snapshot and the
lander's refusals, so every surface behind `E3`, `E4`, `E5`, `E30` and the socket's own snapshot
render gets the same three inputs from one place.

## What stops arriving, and what replaced it

The agent list used to be part of the snapshot's fingerprint, so a `claude` appearing anywhere on
the machine moved the fingerprint and the probe published a delivery frame. That was an accident
that happened to keep `M2` alive; with the process table gone a probe pass is silent about lanes,
and a badge would have sat still until the next git change.

`api::publishDeliveryStates()` re-decides every ticket and republishes what moved. It is called
from the two transitions that change liveness and from nowhere else:

- `agent::Store::onRun` — a run reports `running` at `E18` and a terminal outcome at `E17`;
- `lane::Store::onMembership` — a lane is claimed at `E12` or released at `E13`.

`lastSent` still de-duplicates, so a transition that does not move a ticket's `delivery` sends
nothing and a quiet board produces no traffic. Both that path and the probe's own pass finish
inside `land::Lander::refusalsThen`, which posts to the lander's queue — one strand over
`lastSent`, and no mutex.

**Only the probe's pass lands anything.** `releaseLandedLanes`, `askTheLanes` and
`land::Lander::consider` stay on the git-driven path; a lane event publishes state and decides
nothing about merging.

## What the module may not do again

Two sites in `server/app/` may open `/proc`, and each answers a different question:
`app/agent/proc.cpp`, the owner of every pid the tool spawns, and `app/lane/store.cpp::holderOf`,
which asks which pid holds a device fd — `S5`'s resource half. `delivery` is not one of them, and
`test_component_scheme.py::test_only_the_process_owner_and_the_device_holder_read_proc` sweeps the
tree rather than a list, so a fourth module deciding liveness from a proxy goes red before it
ships.

That is the class, and this is its third instance: `SRV-FIX-128` read a failed `git status` as a
clean worktree, `SRV-FIX-125` read a remote-tracking cache as the remote, and this read the process
table as the lane set. Each time `scan_()` decided a fact from a proxy of it.

## An adopted lane

An adopted lane carries `pid: 0` and `process: not_started` today, so a live agent in an adopted
checkout reads not-`running` here too. That is `SRV-FIX-433`'s defect and it is corrected there.
This module does not re-derive liveness from `/proc` to paper over it — papering over it is the
thing that was wrong.


## Completed snapshot subscriptions (SRV-FIX-571)

`onChange` fires only when the Git snapshot fingerprint changes. `onSnapshot` observes each
completed scan on the same component queue. API uses the latter to compose Git state with current
independent session receipts, which can arrive after the last commit. Delivery does not read
agent ledgers or learn about landing eligibility. This reuses the existing bounded server scan;
it adds neither a browser poll nor a second timer. Immutable snapshots and API's identical-body
suppression keep unchanged delivery state silent on the socket.
