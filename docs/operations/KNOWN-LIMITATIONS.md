# Known limitations — what to avoid while using this factory

The factory is good enough to run Itor3 through it. It is not finished, and some of what is left is
cheaper to steer around than to fix. This file is the steering.

**`superseded` is not `fixed`.** On 2026-09-01, 177 tickets were closed by decision
(`BACKLOG-CLOSE-2026-09-01.md`) — the queue was retired, not the defects. So an entry below whose
ticket reads `superseded` is still a live limitation; only an entry whose ticket reached `pass` was
actually repaired, and those have been removed from this file.

Removed on 2026-09-02, each because its ticket passed: *do not run a gate while a QA session is
running* (`SRV-FIX-078`, `SRV-FIX-109`), *command output does not survive a remount*
(`WEB-FIX-072`), *frames sent while no surface is mounted are dropped* (`WEB-FIX-073`), and *the
template and the README appear as a requirement and a ticket* — that last one repaired the same day
in `catalog::Store::isDocument`, which now skips `_*.md` and `README.md`.

**How to read it.** Every entry says what the tool does wrong, what it costs you, and what to do
instead. Entries are here because *you can see them and avoid them*. Defects where the tool tells
you something false and you have no way to know are **not** on this list — those are being fixed,
because there is nothing a user can do about them.

Each entry names its ticket. When a ticket lands, delete its entry.

---

## Delivery — what the board says shipped

### Keep one remote, and call it `origin` — or check the delivery column twice
`SRV-FIX-125` · partially fixed

The delivery column asks the remote what it holds. It asks **every configured remote** it can find
a name for, but it can only find names for remotes git already has refs under — a remote that is
configured and has never been pushed to or fetched from is invisible to it. In a repository with
one remote named `origin`, everything here is correct.

**Avoid:** several remotes with the work spread across them; a remote whose name appears nowhere in
`refs/remotes` yet.

### Do not let two clones push the same branch
`SRV-FIX-125` · open

If another clone pushes to a branch while your checkout has not fetched, the tool can read the
divergence as "commits nobody has seen" and raise an alarm that is not real, or the reverse. It is
correct when your checkout is up to date with the branch it is reporting on.

**Avoid:** two machines committing on one ticket branch. **Do instead:** `git fetch` before trusting
a `needs_you` on a branch somebody else also works on.

### Pull requests from forks are attributed by branch name alone
`SRV-FIX-127` · open

A pull request opened from a fork whose branch has the same name as one of yours is attributed to
yours, and vice versa. On a repository that takes no outside contributions this never fires.

**Avoid:** relying on the delivery column for fork pull requests.

### A network blip silences the delivery column until restart
`SRV-FIX-126` · open

The remote and the forge are each asked once; after one failure the tool stops asking for the life
of the server process. Every branch then reads `waiting`, which is **silent** — `needs_you` is only
raised by `unshipped` and `stalled`.

**Avoid:** trusting a quiet board after a network interruption. **Do instead:** restart the server
(`cd factory/server && ./build.sh run <repo-root>`) if the delivery column goes quiet all at once.

---

## Gates and the test suite

### Commit or move work-in-progress ticket files before running a gate
`SRV-FIX-130` · open

Three document gates enumerate tickets by globbing the **working tree**, not by asking git. A ticket
file you are still writing — or one another session left on disk — turns those gates red for
everybody in that checkout, on work that has nothing to do with it. Proven: the same commit is red
in the main checkout and green in a lane's worktree.

**Avoid:** leaving half-written ticket documents in `factory/docs/tickets/` while a gate runs.
**Do instead:** commit them, or move them aside until they are complete.

## Lanes and runs — what the board says is happening

### Other known display defects
Visible, wrong, harmless — the number or the label is off, the underlying state is fine.

| | |
|---|---|
| `WEB-FIX-088` | a ticket with two runs is told nothing has run on it |
| `WEB-FIX-091` | a live run is reported as zero runs by the header above it |
| `SRV-FIX-092` | the requirements count line reports a constant zero in flight |
| `WEB-FIX-094` | the needs-you chip cannot count the two states the spec calls most urgent |
| `WEB-FIX-095` | one column carries two different axes on neighbouring rows |
| `WEB-FIX-098` | a lane card picks a stage name by guessing the sequence length |
| `WEB-FIX-102` | the RUNS table names a stage no run ever reported |
| `WEB-FIX-103` | the status column carries a ticket count on the standalone row |
| `SRV-FIX-106` | the wire cannot say that no stage was reported |
| `WEB-FIX-067` | a patched entry is drawn twice from any snapshot |
| `WEB-FIX-090` | the composer offers Send mid-turn without saying the message will be held |

**Do instead:** when a count looks wrong, the endpoint underneath it is right — `GET /api/lanes`,
`GET /api/tickets/{id}/runs`.

---

## Things you will lose if you do them

## The factory managing itself

### The tool cannot see its own backlog
`SRV-FEAT-115` · open

This repository has two documentation roots — Itor3's `docs/` and the factory's own
`factory/docs/` — and a manifest declares one. Pointed at Itor3 the tool is correct; it simply
cannot show you the factory's own tickets.

**Avoid:** expecting `FAC-*` tickets to appear on the board. **Do instead:** read
`factory/docs/tickets/` directly.

### The QA handover can fail silently
`SRV-FIX-124` · open

A QA session launched from the session that wrote the handover can fail without saying so.

**Avoid:** assuming QA ran because it was launched. **Do instead:** check that a verdict file exists
and that `git log` shows it committed — a `status:` in a file is not evidence.

---

## Not limitations — deliberate

These come up as surprises and are working as designed.

- **A lane cannot push or open a pull request.** Lanes have no credentials by design: the real home
  is never mounted, so there is no ssh key, no credential helper and no `~/.git-credentials` inside
  one. Push and open the PR from outside the lane.
- **A lane's home is empty.** `/home/lane` is a fresh tmpfs holding only what the manifest declares
  as `toolchain`. If a build needs something from your home, declare it — narrowly.
- **`/`, your home, and any directory containing it are refused as a toolchain.** One manifest line
  naming `/home` would read-bind every home on the machine into every lane (`SRV-FIX-078`). Name the
  toolchain, not the directory it sits in.
