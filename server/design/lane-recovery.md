# The lane set outlives the process that owns it

`SRV-FIX-416`. The registry was memory only. Restarting the server to load a rebuilt binary emptied
it **twice in one session** while sixteen checkouts sat on disk, and `GET /api/lanes` then answered
`[]` about every one of them:

```
before   GET /api/lanes → 10 lanes, seats {"total":10,"used":10}
restart  ./build.sh run "$PWD"
after    GET /api/lanes → 0 lanes,  seats {"total":10,"used":0}
$ ls .factory/worktrees/lane | wc -l   →  16
```

That is worse than losing a list, because `CLAUDE.md` points at that endpoint as the authority:

> Only `GET :7777/api/lanes` can answer *is an agent running in it*.

After a restart the answer is wrong for every lane at once — the same belief that on 2026-08-06
deleted a worktree 410 s into a run, manufactured wholesale. And the seats lie the other way too:
`used: 0` with sixteen checkouts hands out seats that are held.

## Where a claim is written down

`.factory/lanes/<lane>.json`, one flat object per lane, written at claim and removed at release.

**`app/lane` does not know that sentence.** It is `manifest::Store::Data::lanesDir`, resolved in
`load_()` from `FACTORY_DIR` and `LANES_DIR` beside `runsDir`, `worktreesDir`, `permissionsPath`
and `idsPath`, and `lane::Store::init()` copies it into `lanesDir_` the same way it copies
`worktreesDir` into `worktreeRoot_`. `SRV-FIX-416` shipped with the name joined at three sites
inside `app/lane/store.cpp`, which made `S1` — *the manifest is the only door* — false, and
`test_manifest.py::test_no_module_but_manifest_knows_a_path` red on `main` for four days
(`SRV-FIX-447`).

It is resolved **unconditionally**, before the manifest file is read, exactly as its four siblings
are. That is not tidiness: `lane::Store::init()` reads `manifest::Store::instance().data()`
whatever state the manifest is in, and a lane set addressable only when the manifest parses would
lose recovery on the one boot where `GET /api/lanes` matters most. It is not published in `E2`'s
`resolved.paths` either — none of the five is.

**Outside the worktree, deliberately.** A file inside would dirty the branch the lane is working on,
and dirt is one of the signals a person reads before releasing. `.factory/lanes/` joins
`worktrees/`, `runs/` and `ids.json` in the target repository's ignore list — the tool never edits a
`.gitignore`, so a repository that does not ignore it sees its own status change. Without that line
every `confine` and `isolation` case went red on *"the main checkout changed"*, which is the fixture
correctly refusing a tool that dirties the repository it is watching.

## What recovery does, and what it refuses to do

**It never starts anything.** It reports what is there; whether a recovered round continues is a
separate act a person or a role takes.

- **A pid from a previous boot may belong to somebody else now**, so only a process still alive is
  carried forward. A dead one becomes a lane that has stopped, which is what it is.
- **A record whose worktree is gone is dropped**, along with its file. A lane with no checkout is
  not a lane, and reporting one would put back the same false answer pointing the other way —
  `test_a_lane_whose_worktree_is_gone_is_not_recovered` is that assertion.

The reader is deliberately not a JSON parser: it reads back only the keys written here, and anything
it cannot read leaves the lane unrecovered rather than half-recovered.

## Why the tests are `test_lane_recovery.py`

Watched red against the old binary, rebuilt without the change:

```
FAILED test_lane_recovery.py::test_lanes_survive_a_restart
Extra items in the right set: 'lane/01', 'lane/02'
```

## What the API layer does with a lane that has stopped

**`_lanes.cpp`** fetches the delivery probe's snapshot before rendering, because the lanes screen is
drawn from that answer and a lane can then say whether its work has **landed** or whether it still
holds commits nobody else can see (`SRV-FEAT-415`). A caller that cannot reach the probe passes
nothing, and the lane says *less* rather than something false.

**`lane-view.cpp`** injects `laneDelivery` the way `laneEvidence` was already injected: the lane
module never learns what a ticket is, and delivery arrives as a plain string.

**`_delivery.cpp`** releases a lane whose work has landed, hanging off the probe's existing
`onChange` — event-driven, no new timer. It fires **only** on §3.5's `done`.

A live process is never *interrupted*, and a **finished** one is ended (`SRV-FIX-600`). Every real
round ends with its session alive and its turn over — `idle` — so the first version, which skipped
any live pid, released nothing the product ever produced: `TL-FEAT-041` passed, landed, and held
two seats until a person released them. Now a pass on `done` ends each `idle` session through
`agent::Store::end` (which closes stdin; the captured CLI exits 1.1 s later,
`tests/fixtures/cli-eof-2026-10-20-…json`), and the probe's next pass finds no process and releases
the lane through the original path. `running` and `blocked` are skipped; `blocked` is read with the
permissions snapshot, which is fetched before `refusalsThen` so the whole decision runs on the
Lander's queue beside `lastSent`. `endedForLanding` is keyed by **run id**, not lane name, because
names are reused, and it is pruned to the runs `E9` still lists. A session that does not exit
after `E16` keeps its lane: this module does not kill, and that case is filed on its own.

The probe needs one fact to say `done` while such a session is up: `LiveLanes` carries, per
ticket, whether its best-ranked live lane is `idle` (`Holder::finished`), and `decide` asks the
base branch first for a finished holder on a `pass`. Three of seven real lanes measured on 2026-09-05 were `unshipped` — work nobody
else can see — and releasing one strands commits no other ref points at.
`test_a_lane_holding_unshipped_work_is_kept` is the assertion that keeps the two apart.

**`_start-run.cpp`** picks the prompt from the lane's **role**. A run started in a `role: qa` lane
used to be handed `agent.start_prompt_file` — the run-ticket prompt — so it re-ran the round it was
supposed to be verifying. The role already names its own prompt in the manifest; the auto-start path
read it and this one did not, so the same lane behaved differently depending on who started it
(`WEB-FEAT-350`).

## The `stopped` axis (`SRV-FEAT-415`)

`process == idle` is *"the agent's process is alive and its turn is over"*, so an agent that
**finished** and one that stopped **to ask a question** are one value. Four situations shared the
words `idle · waiting for you`, and §3.3's silence alarm cannot separate them either — silence is
measured only against a **live** process, so the one state meaning *this has stopped and needs
somebody* is the state no alarm covers.

`stopped` carries `{reason, seconds, since}` — `work_landed · holds_work · crashed · never_started ·
ended` — and `needs_you` drops a lane whose work is already on the base branch, because a counter
that is always on is a channel a person learns to ignore.

**It is a member of `state`, not a sibling of it.** As a sibling the client could not see it: the
screen reads the state object, so the card went on saying `waiting for you` while the server knew
better (`WEB-FIX-349`). For the same reason it belongs to `$defs/laneState` alone and not to
`$defs/state`, which `requirementRow`, `ticketRow`, `homeTicketRow`, `E4` and `E5` all reference —
a ticket row would otherwise be required to carry a member only a lane has.

`DeliveryFn` is optional everywhere: a caller that cannot reach the probe passes nothing and the
lane says less rather than something false.

## Reading the lane root itself (`SRV-FIX-432`)

`SRV-FIX-416`, above, taught the store to read `.factory/lanes/*.json` back. It never taught it to
look at the directory those files describe. So the set after a restart was *the files the previous
process happened to leave*, not *the checkouts on disk*, and a record whose worktree is gone was
dropped while **a worktree whose record is gone was never looked at**. Measured on this repository
on 2026-09-06:

```
$ ls .factory/lanes/         → empty
$ curl -s :7777/api/lanes    → {"lanes":[],"resources":[],"seats":{"host":{"total":10,"used":0}}}
$ git worktree list          → lane/01 ticket/WEB-FIX-329     lane/07 ticket/SRV-FIX-379
                               lane/14 ticket/WEB-FIX-329-qa  lane/17 ticket/SRV-FIX-379-qa
```

Those four branches carried 36 commits nothing else had. `E9` answered `[]` about all of them, and
`E13` cannot release what `E9` does not list.

`recover_()` therefore has two halves, in this order: **read back**, then **adopt**. Both run inside
`init()`, which `core::Engine::start()` calls before it starts any io thread, so the one
`git worktree list --porcelain` this adds blocks nothing — there is no handler thread yet to block.

### Adopted iff three things, and reported otherwise

A registered worktree of this repository, directly under `<worktrees>/lane/`, on a `ticket/*`
branch. `reserve_()` has adopted an existing worktree at CLAIM time since `SRV-FIX-189`; this is the
same three questions asked at BOOT, over the whole directory instead of one path.

Everything else is a `warnings[{code:"unaccountable_worktree", path}]` row. **Both halves matter and
neither is the interesting one on its own**: a sweep that only adopts leaves the tidy-looking
silence this ticket is about, one directory further down, and a sweep that only warns still cannot
answer the deletion rule. The invariant is that the lane root is accounted for **entirely** —
`test_every_directory_under_the_lane_root_is_a_lane_or_a_reported_one` reads the directory off the
filesystem and requires every entry to be in one list or the other.

### The branch is the record

`branchFor_` is the only producer of a lane branch, so `ticketFor_` is written as its inverse: take
the tail after `ticket/`, and if the last `-`-separated segment is not all digits, that segment is
the role and the rest is the id. Every id the tool allocates ends in `-<number>`, so
`ticket/SRV-FIX-432` is `SRV-FIX-432`/`run` and `ticket/SRV-FIX-432-qa` is `SRV-FIX-432`/`qa`.

It is deliberately **not** matched against the manifest's declared `roles`. Adoption has to answer
the same way in a repository that declares none, and a lane whose role the tool cannot name is
exactly the lane this ticket exists to stop hiding.

### What an adopted lane does NOT carry

`workspace` is `""` and `resources_held` is empty. `app/lane` may not reach sideways into `catalog`
to read a ticket's frontmatter (`docs/technical/ARCHITECTURE.md`), and an invented workspace would take an
exclusive resource for a lane in which nothing is running — the queue behind it would then wait on a
checkout with no process, which is `SRV-FIX-105` pointing the other way. `pid` is `0`: adoption
never inherits a process, so `state.process` is `not_started` and `stopped.reason` `never_started`
with no change to `lane::state::processOf`.

`claimed_at_ms` is the entry's own mtime, not `nowMs()` — the restart is not the claim, and
`elapsed_s` on the rail should say how long the checkout has been there.

### It is a survey, taken once

`warnings_` records what the boot sweep saw and `E9` reports it for the life of the process. It is
not recomputed per request on purpose: `list()` runs on the io_context and `git worktree list` is a
subprocess, so a per-request survey would block a handler thread. The cost is that an unaccountable
directory removed BY HAND after boot stays in `warnings` until the next restart — a lane root that
only ever grows through the tool cannot reach that state, and the alternative breaks a hard rule.

### The seat count is a report, not a limit, on this path

Adoption does not check `lanes.host`, exactly as the read-back does not. Refusing to LIST the fourth
checkout because three seats exist would be the same silence in a new place; `seats.host.used` says
what is held and `E12` is where the limit is enforced.

## An adopted lane is adopted with whatever is running in it (`SRV-FIX-433`)

`adopt_` built a `Record` with no pid, so a restart that found a checkout nobody had a record for
reported it `not_started` — while an agent was still working in it. That lane is then one `E13`
releases without `?force=true`, and the release reaps a checkout somebody is writing in: the
2026-08-06 incident `CLAUDE.md` opens with, arriving by a different door.

The question a restart has to answer about a checkout no record describes is *what is running in
there*, and the only thing that answers it is the process table — a pid in a record from a previous
boot may belong to anybody now. `git::pidRunningIn` walks `/proc` and takes the lowest-numbered
process whose working directory is inside that checkout; a process running anywhere else is never
adopted, and a checkout with nothing in it is still `not_started`.

`E9` gains no field for it: the pid is not a surface, it is what `E13` consults, and that is where
the case asserts it. A lane adopted with a live process is refused an unforced release and the
refusal names the pid; the quiet lane beside it releases cleanly, in the same world, because a
fixture where every lane is live cannot tell the two states apart.

## A lane's conversation does not outlive the lane (`SRV-FIX-431`)

`sessionByLane_` is keyed by a lane NAME, and a lane is the one thing in this tool designed to be
destroyed and its name reused. `E13` keeps the checkout, so a second claim for the same ticket
adopts it and takes the name back — and the next run resumed a conversation the agent could no
longer find, died, and reported `crashed`.

The record is dropped when the lane is released, which is the only moment that is certainly the end
of an occupancy: `api` hears `onMembership(record, claimed=false)` and tells `agent::Store` to
forget that lane's session and its run count. Nothing changes for a lane that was never released —
resuming a live session is what keeps a round's context across an interruption, and the mandatory
negative is the case that says so.

**The reason travels beside the outcome, never inside it.** `lane::state::processOf` is a closed
vocabulary and a free-text outcome falls through it to `not_started`, which is worse than
`crashed`. So the run still settles as `crashed` and the lane's LAST WORDS carry
*the conversation `<id>` is not there any more* — a session id is something a person can act on and
`crashed` is not. The marker the tool watches for is the CLI's own diagnostic, captured at
`tests/fixtures/cli-resume-2026-09-11-a-conversation-that-is-gone.json`; that recording is the
arbiter and the stand-in in the suite prints what it prints and exits as it exits.
