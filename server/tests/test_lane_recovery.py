"""SRV-FIX-416 — a restart must not forget a lane.

The registry was memory only. Restarting the server to load a rebuilt binary emptied it twice in
one session while sixteen checkouts sat on disk, and `GET /api/lanes` then answered `[]` about
every one of them.

That is worse than losing a list, because `CLAUDE.md` carries a hard rule pointing at that endpoint
as the authority:

    Never remove a lane checkout without asking the factory whether it owns a live lane there.
    `git status` answers does this hold uncommitted files; it cannot answer is an agent running in
    it. Only `GET :7777/api/lanes` can.

After a restart that answer is wrong for every lane at once. On 2026-08-06 the belief "verified
empty" deleted a worktree 410 seconds into a run; a restart manufactures the same belief wholesale.
And the seats lie the other way too — `used: 0` with sixteen checkouts hands out seats the tool
does not have.
"""

import json
import subprocess
import time

import pytest

REQ_DIR, TKT_DIR, RPT_DIR = "docs/epics", "docs/tickets", "docs/reports"


def manifest_for():
    return {
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}},
        "lanes": {"host": 3},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": "claude"},
    }


def doc(front):
    return "\n".join(["---"] + [f"{k}: {v}" for k, v in front.items()] + ["---", "", "Body.", ""])


def git(root, *args):
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"git {' '.join(args)} → {out.stderr}"
    return out.stdout.strip()


@pytest.fixture
def world(repo, serve):
    class World:
        def prepare(self):
            """The repository on disk, with no server pointed at it yet.

            Split out of `build` for SRV-FIX-432, whose subject is what a server finds when it
            BOOTS: the checkouts have to already be there, so the process that answers is the
            first one to see them.
            """
            repo.write(".factory/factory.json", json.dumps(manifest_for()))
            repo.write(f"{REQ_DIR}/REQ-001.md", doc({"id": "REQ-001", "title": "R"}))
            for n in (1, 2):
                repo.write(f"{TKT_DIR}/TKT-{n}.md", doc({
                    "id": f"TKT-{n}", "parent": "REQ-001", "workspace": "alpha",
                    "title": f"Ticket {n}", "status": "in-progress"}))
            git(repo.root, "init", "-q", "-b", "main")
            git(repo.root, "config", "user.email", "t@example.invalid")
            git(repo.root, "config", "user.name", "T")
            git(repo.root, "add", "-A")
            git(repo.root, "commit", "-q", "-m", "the repository")
            self.root = repo.root
            return self

        def build(self):
            self.prepare()
            self.srv = serve(repo.root)
            return self

        def claim(self, ticket):
            r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": "alpha"})
            assert r.status_code == 201, r.text
            return r.json()

        def lanes(self, srv=None):
            r = (srv or self.srv).get("/api/lanes")
            assert r.status_code == 200, r.text
            return r.json()

    return World()


def test_lanes_survive_a_restart(world, serve):
    """The claim is written down, so a second server reads the same set."""
    w = world.build()
    first = {w.claim("TKT-1")["lane"], w.claim("TKT-2")["lane"]}

    before = w.lanes()
    assert {l["lane"] for l in before["lanes"]} == first
    assert before["seats"]["host"]["used"] == 2

    # a SECOND server over the same repository — the restart, without killing the first's fixture
    again = serve(w.root)
    after = again.get("/api/lanes")
    assert after.status_code == 200, after.text
    body = after.json()

    assert {l["lane"] for l in body["lanes"]} == first, (
        f"a restart lost the lane set: {[l['lane'] for l in body['lanes']]} — the worktrees are "
        f"still on disk and `GET /api/lanes` is the authority CLAUDE.md's deletion rule consults")
    assert body["seats"]["host"]["used"] == 2, (
        f"seats disagree with the lanes actually held: {body['seats']}")
    for lane in body["lanes"]:
        assert lane["ticket_id"] in {"TKT-1", "TKT-2"}, lane
        assert lane["workspace"] == "alpha", lane
        assert lane["branch"], f"a recovered lane forgot its branch: {lane}"


def test_a_lane_whose_worktree_is_gone_is_not_recovered(world, serve):
    """MANDATORY NEGATIVE — recovery may not invent a lane.

    A record whose checkout has been removed is not a lane, and reporting one would put back
    exactly the false answer this ticket exists to remove, pointing the other way.
    """
    w = world.build()
    lane = w.claim("TKT-1")
    worktree = lane["worktree"]

    subprocess.run(["rm", "-rf", worktree], check=True, timeout=30)

    again = serve(w.root)
    body = again.get("/api/lanes").json()
    names = [l["lane"] for l in body["lanes"]]
    assert lane["lane"] not in names, (
        f"{lane['lane']} was recovered with no worktree on disk: {names}")
    assert body["seats"]["host"]["used"] == len(body["lanes"]), body["seats"]


def test_a_released_lane_stays_released_across_a_restart(world, serve):
    """Release removes the record, so the seat does not come back with the process."""
    w = world.build()
    lane = w.claim("TKT-1")["lane"]
    r = w.srv.delete(f"/api/lanes/{lane}?force=true")
    assert r.status_code in (200, 204), r.text

    again = serve(w.root)
    body = again.get("/api/lanes").json()
    assert lane not in [l["lane"] for l in body["lanes"]], body
    assert body["seats"]["host"]["used"] == 0, body["seats"]


# ── SRV-FIX-432 · a lane the tool did not write down is a lane it cannot see ───────────────────
#
# `SRV-FIX-416` taught the store to read `.factory/lanes/*.json` back. It never taught it to look
# at the lane root. So the set after a restart is the set of FILES the previous process happened
# to leave, and on 2026-09-06 that was `{"lanes":[]}` over four checkouts holding 36 commits.
# `S5` names `GET /api/lanes` the only authority on whether a lane is live, and silence from the
# sole authority is what deleted a worktree 410 s into a run on 2026-08-06.
#
# The three kinds below are NAMED because a sweep asserted over one kind proves nothing about a
# sweep: a clean run lane, a run lane holding commits no other ref carries, and a `-qa` role lane
# on a second ticket. Their lane names are deliberately out of order and out of step with their
# ticket ids — `lane/02` holds TKT-3 and `lane/09` holds TKT-1 — so a pairing invented by the
# reader (numbering them as it finds them, or sorting by ticket) cannot pass.

LANE_ROOT = ".factory/worktrees/lane"

# lane directory → (branch, ticket id, role, what kind of checkout it is)
ON_DISK = {
    "lane/02": ("ticket/TKT-3-qa", "TKT-3", "qa",  "a role lane on a second ticket"),
    "lane/05": ("ticket/TKT-2",    "TKT-2", "run", "a run lane holding commits no other ref has"),
    "lane/09": ("ticket/TKT-1",    "TKT-1", "run", "a clean run lane"),
}

# the two entries under the lane root that are NOT lanes, and must be reported and left alone
NOT_A_WORKTREE = "lane/04"          # a directory git has never heard of
NOT_A_TICKET_BRANCH = "lane/06"     # a registered worktree, on a branch outside `ticket/*`
LOOSE_BRANCH = "scratch/loose"


def real(path):
    import os
    return os.path.realpath(str(path))


def lane_root(w):
    from pathlib import Path
    return Path(real(Path(w.root) / LANE_ROOT))


def checkouts_on_disk(w):
    """The state a killed process leaves: five directories under the lane root, no records.

    Built with `git worktree add` and `mkdir` only — nothing here goes through the tool, because
    what is being asserted is what the tool makes of a lane root it did NOT write.
    """
    from pathlib import Path

    # the tool never edits a target repository's `.gitignore`; this fixture is the repository, and
    # a repository that does not ignore `.factory/` watches its own status change under it.
    (Path(w.root) / ".gitignore").write_text(".factory/\n")
    git(w.root, "add", "-A")
    git(w.root, "commit", "-q", "-m", "ignore the tool's own directory")

    for lane, (branch, _tid, _role, _kind) in ON_DISK.items():
        git(w.root, "worktree", "add", "-q", "-b", branch, f"{LANE_ROOT}/{lane.split('/')[1]}")

    # `lane/05` holds two commits no other ref carries — `git rev-list --count main..` is 2
    five = Path(w.root) / LANE_ROOT / "05"
    for n in (1, 2):
        (five / f"work-{n}.txt").write_text(f"work {n}\n")
        git(five, "add", "-A")
        git(five, "commit", "-q", "-m", f"TKT-2: work {n}")

    # …and the two that are not lanes
    plain = Path(w.root) / LANE_ROOT / NOT_A_WORKTREE.split("/")[1]
    plain.mkdir(parents=True)
    (plain / "notes.txt").write_text("somebody's scratch directory\n")
    git(w.root, "worktree", "add", "-q", "-b", LOOSE_BRANCH,
        f"{LANE_ROOT}/{NOT_A_TICKET_BRANCH.split('/')[1]}")

    records = Path(w.root) / ".factory" / "lanes"
    assert not records.exists() or not list(records.glob("*.json")), (
        f"this case is about a lane root with NO records beside it; {records} is not empty")
    return w


def rows_by_lane(body):
    return {l["lane"]: l for l in body["lanes"]}


def test_a_restart_adopts_the_worktrees_no_record_describes(world, serve):
    """§1.1 — a restart lists the checkouts that are on disk, not the records the last process left.

    Three checkouts, three kinds, no `.factory/lanes/` at all. RED at the filing commit, where the
    same call answers `{"lanes":[]}` with all three sitting there.
    """
    from pathlib import Path

    w = checkouts_on_disk(world.prepare())

    assert git(w.root, "rev-list", "--count", "main..ticket/TKT-2") == "2", (
        "the fixture's second kind is supposed to hold commits no other ref carries")

    srv = serve(w.root)                                   # the restart
    body = srv.get("/api/lanes").json()
    rows = rows_by_lane(body)

    assert set(rows) == set(ON_DISK), (
        f"a restart answered about {sorted(rows)} while the lane root held {sorted(ON_DISK)} — "
        f"`S5` makes GET /api/lanes the only authority on what is in that directory, and a "
        f"checkout it does not list is one the sole authority is silent about\n"
        f"  lane root: {lane_root(w)}\n"
        f"  git worktree list: {git(w.root, 'worktree', 'list')}")

    for lane, (branch, ticket, role, kind) in ON_DISK.items():
        row = rows[lane]
        assert (row["branch"], row["ticket_id"], row["role"]) == (branch, ticket, role), (
            f"{lane} ({kind}) was adopted with {row['branch']!r}/{row['ticket_id']!r}/"
            f"{row['role']!r}, and disk says {branch!r}/{ticket!r}/{role!r}")
        assert real(row["worktree"]) == str(lane_root(w) / lane.split("/")[1]), row
        assert row["state"]["process"] == "not_started", (
            f"{lane} was adopted as {row['state']['process']!r} — adoption is a READ and starts "
            f"nothing, so every adopted lane is stopped: {row['state']}")

    assert body["seats"]["host"]["used"] == 3, (
        f"three checkouts are held and the seats say {body['seats']} — seats that under-count are "
        f"seats the tool hands out into directories another agent may still be writing")


def test_an_adopted_lane_is_written_down_and_survives_a_second_restart(world, serve):
    """§1.2 — what was derived once is written down; the derivation is a repair, not a per-boot guess."""
    from pathlib import Path

    w = checkouts_on_disk(world.prepare())
    first = serve(w.root).get("/api/lanes").json()
    assert set(rows_by_lane(first)) == set(ON_DISK), first

    records = Path(w.root) / ".factory" / "lanes"
    written = sorted(records.glob("*.json"))
    assert len(written) == len(ON_DISK), (
        f"{len(written)} record(s) under {records} for {len(ON_DISK)} adopted lane(s): "
        f"{[p.name for p in written]}")

    on_file = {}
    for p in written:
        r = json.loads(p.read_text())
        on_file[r["lane"]] = (r["branch"], r["ticket_id"], r["role"])
    assert on_file == {k: v[:3] for k, v in ON_DISK.items()}, (
        f"the records do not describe what was adopted: {on_file}")

    second = serve(w.root).get("/api/lanes").json()
    assert {l["lane"]: (l["branch"], l["ticket_id"], l["role"]) for l in second["lanes"]} \
        == {k: v[:3] for k, v in ON_DISK.items()}, second
    assert second["seats"]["host"]["used"] == first["seats"]["host"]["used"] == 3, (
        f"seats moved between two boots over an unchanged lane root: "
        f"{first['seats']} then {second['seats']}")


def test_an_adopted_lane_is_released_through_e13_and_stays_released(world, serve):
    """§1.3 — a person can end an adopted lane through the tool, and it stays ended."""
    w = checkouts_on_disk(world.prepare())
    srv = serve(w.root)
    assert set(rows_by_lane(srv.get("/api/lanes").json())) == set(ON_DISK)

    r = srv.delete("/api/lanes/lane/09")                  # the clean run lane, on ticket/TKT-1
    assert r.status_code == 200, r.text
    end = r.json()
    assert end["released"] is True, (
        f"E13 answered about lane/09 without releasing it: {end} — `detail.reason:\"leftover\"` is "
        f"the answer E13 gives about something it cannot describe, and an adopted lane is a lane")
    assert end["detail"]["reason"] != "leftover", end
    assert end["branch_kept"] is True, end

    assert git(w.root, "rev-parse", "--verify", "--quiet", "ticket/TKT-1"), (
        "E13 kept `branch_kept: true` and the branch is gone")

    again = serve(w.root).get("/api/lanes").json()
    rows = rows_by_lane(again)
    assert "lane/09" not in rows, (
        f"a released lane came back on the next boot: {sorted(rows)} — adoption would then be a "
        f"per-boot guess that undoes what a person did through the tool")
    assert set(rows) == {"lane/02", "lane/05"}, rows
    assert again["seats"]["host"]["used"] == 2, again["seats"]


def test_a_directory_that_is_not_a_ticket_worktree_is_reported_and_never_adopted(world, serve):
    """§1.4 — MANDATORY NEGATIVE. What is not a lane is not adopted, and nothing is removed to tidy up.

    Two kinds of refusal, because one kind is not a refusal rule: a directory git has never heard
    of, and a registered worktree on a branch outside `ticket/*`.
    """
    from pathlib import Path

    w = checkouts_on_disk(world.prepare())
    srv = serve(w.root)
    body = srv.get("/api/lanes").json()

    names = set(rows_by_lane(body))
    assert NOT_A_WORKTREE not in names and NOT_A_TICKET_BRANCH not in names, (
        f"something that is not a ticket worktree was adopted as a lane: {sorted(names)}")

    warnings = body.get("warnings", [])
    refused = [w_ for w_ in warnings if w_.get("code") == "unaccountable_worktree"]
    paths = [real(w_["path"]) for w_ in refused if "path" in w_]
    for entry in (NOT_A_WORKTREE, NOT_A_TICKET_BRANCH):
        want = str(lane_root(w) / entry.split("/")[1])
        assert paths.count(want) == 1, (
            f"{entry} appears {paths.count(want)} time(s) in warnings and must appear exactly "
            f"once — silence about it is the answer this ticket exists to remove, and saying it "
            f"twice is a rail that grows a row per boot\n  warnings: {warnings}")
    assert len(refused) == 2, f"warnings carry entries nothing under the lane root explains: {refused}"

    # …and nothing was tidied away.
    plain = lane_root(w) / NOT_A_WORKTREE.split("/")[1]
    assert plain.is_dir() and (plain / "notes.txt").read_text() == \
        "somebody's scratch directory\n", f"{plain} was touched"
    loose = lane_root(w) / NOT_A_TICKET_BRANCH.split("/")[1]
    assert loose.is_dir() and (loose / ".git").exists(), f"{loose} was touched"
    assert git(w.root, "rev-parse", "--verify", "--quiet", LOOSE_BRANCH), (
        f"{LOOSE_BRANCH} was removed; adoption is a READ")


def test_every_directory_under_the_lane_root_is_a_lane_or_a_reported_one(world, serve):
    """§1.5 — the lane root is accounted for entirely, so the deletion rule can be answered from E9 alone.

    `SRV-FIX-416` §1.3 stated this and no case in this repository implements it. The sweep is the
    FILESYSTEM's list, not a list this case types: a set you typed is bounded by what you already
    thought of.
    """
    w = checkouts_on_disk(world.prepare())
    srv = serve(w.root)
    body = srv.get("/api/lanes").json()

    entries = sorted(p for p in lane_root(w).iterdir())
    assert entries, (
        f"nothing under {lane_root(w)} — this case swept an empty directory and asserted nothing")

    accounted = {real(l["worktree"]) for l in body["lanes"]} \
              | {real(w_["path"]) for w_ in body.get("warnings", []) if "path" in w_}
    missing = [str(p) for p in entries if real(p) not in accounted]
    assert not missing, (
        f"{len(missing)} of {len(entries)} entries under the lane root are in neither `lanes` nor "
        f"`warnings`: {missing}\n  E9 is the one authority CLAUDE.md's deletion rule consults, and "
        f"an entry absent from both is one it says nothing about\n  lanes: "
        f"{[l['lane'] for l in body['lanes']]}\n  warnings: {body.get('warnings', [])}")


# ── SRV-FIX-532 cluster C3 — a lane's state is its own ────────────────────────────────────────
#
# Two tickets, one cause, and `docs/operations/ESCAPES.md` already carries it twice in three days:
# `SRV-FIX-247` — `sessionByLane_` keyed by a lane NAME, and lane names are REUSED, so a new
# occupant resumed a dead session; `SRV-FIX-256` — `roles_` holding the name of a released lane.
# **A lane is the one thing in this tool designed to be destroyed and its name reused**, and every
# fixture is claim → act → assert → tear down, so a record that outlives the lane is never asked.


def a_process_living_in(path):
    """A real process whose CWD is `path`, and the pid the operating system gave it.

    Not a pid written into a record: the question `SRV-FIX-433` is about is *what is running in
    this checkout*, and the only thing that answers it is the process table.
    """
    import subprocess

    p = subprocess.Popen(["/bin/sh", "-c", "sleep 120"], cwd=str(path),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def test_an_adopted_lane_with_a_live_process_is_not_reported_as_stopped(world, serve):
    """§1 C3 — a restart that adopts a checkout somebody is still working in says so.

    `SRV-FIX-433` criterion 1. A process is running with its cwd inside one of the adopted
    checkouts and NO `.factory/lanes/` record exists for any of them, so a row that knows about
    that process can only have got it from the process table.

    **The pid is asserted through the thing it governs, not as a field**, because §1 widens no
    surface and `E9` carries none: a lane the tool believes is idle is a lane `E13` will release
    without `?force=true` and reap out from under somebody. So the case asks `E13` — and the
    quiet lane beside it releases cleanly in the same world, because a fixture where every lane
    is live cannot tell *adopted with a process* from *adopted*.
    """
    w = checkouts_on_disk(world.prepare())
    busy = lane_root(w) / "05"
    quiet = lane_root(w) / "09"
    assert busy.is_dir() and quiet.is_dir(), (busy, quiet)

    living = a_process_living_in(busy)
    try:
        srv = serve(w.root)                                   # the restart
        rows = rows_by_lane(srv.get("/api/lanes").json())

        held = rows["lane/05"]
        assert held["state"]["process"] != "not_started", (
            f"lane/05 holds a running process and reads {held['state']['process']!r}: "
            f"{held['state']}")
        assert not (held["state"].get("stopped") or {}).get("reason") == "never_started", (
            f"lane/05 holds a running process and is reported as never started: {held['state']}")

        idle = rows["lane/09"]
        assert idle["state"]["process"] == "not_started", (
            f"lane/09 has nothing running in it and reads {idle['state']['process']!r} — if every "
            f"adopted lane reports the same thing, this case cannot tell the two apart: "
            f"{idle['state']}")

        # THE PID, asked of the boundary it exists for.
        refused = srv.delete("/api/lanes/lane/05")
        assert refused.status_code == 409, (
            f"an adopted lane with a live process was released without `?force=true`: "
            f"{refused.status_code} {refused.text[:400]}\n"
            f"That release reaps a checkout somebody's agent is still writing in, which is the "
            f"2026-08-06 incident `CLAUDE.md` opens with.")
        assert str(living.pid) in refused.text, (
            f"the refusal does not name the process it found: {refused.text[:400]}")

        released = srv.delete("/api/lanes/lane/09")
        assert released.status_code in (200, 204), (
            f"the lane with nothing running in it could not be released either, so the rule above "
            f"is *refuse everything*: {released.status_code} {released.text[:400]}")
    finally:
        living.kill()
        living.wait()


def test_a_pid_that_is_not_running_in_this_checkout_is_not_adopted(world, serve):
    """§1 C-NEG — a pid that is not ours is never adopted as ours.

    `SRV-FIX-433` criterion 3, mandatory negative, two members driven in one world:

      1. a process that is alive and running somewhere ELSE — a pid from a previous boot may
         belong to anybody now, and claiming one is how `E13 ?force=true` kills a stranger's
         process group;
      2. checkouts with nothing running in them at all.

    Both are reported stopped and release cleanly, while the one checkout that DOES hold a process
    is refused — so *adopt nothing* fails here.
    """
    from pathlib import Path

    w = checkouts_on_disk(world.prepare())
    stranger = a_process_living_in(Path(w.root))
    living = a_process_living_in(lane_root(w) / "05")
    try:
        srv = serve(w.root)
        rows = rows_by_lane(srv.get("/api/lanes").json())

        for lane in ("lane/02", "lane/09"):
            assert rows[lane]["state"]["process"] == "not_started", (
                f"{lane} has nothing running in it and reads "
                f"{rows[lane]['state']['process']!r}: {rows[lane]['state']}")
            gone = srv.delete(f"/api/lanes/{lane}")
            assert gone.status_code in (200, 204), (
                f"{lane} holds no process and could not be released, so a pid was borrowed from "
                f"somewhere else — a stranger's process group: {gone.status_code} "
                f"{gone.text[:400]}")

        assert rows["lane/05"]["state"]["process"] != "not_started", (
            f"the one checkout that DOES hold a process was adopted as idle, so the rule above "
            f"is *adopt nothing*: {rows['lane/05']['state']}")
        held = srv.delete("/api/lanes/lane/05")
        assert held.status_code == 409, held.text[:400]
        assert str(stranger.pid) not in held.text, (
            f"a process running outside every lane was adopted as one: pid {stranger.pid} named "
            f"in {held.text[:400]}")
    finally:
        for p in (stranger, living):
            p.kill()
            p.wait()


# ── SRV-FIX-532 cluster C3 — a lane's conversation is its own ─────────────────────────────────
#
# `SRV-FIX-431`, and `docs/operations/ESCAPES.md`'s `SRV-FIX-247` a second time: `sessionByLane_` is keyed by
# a lane NAME and lane names are REUSED, so the next occupant of `lane/03` resumed a conversation
# that belonged to whoever had it before — a session id the agent then cannot find, a run that
# dies, and a lane reporting `crashed` with no reason a person can act on.
#
# Every fixture is claim → act → assert → tear down, so a record that outlives the lane is never
# interrogated. **Any record storing a lane name owes a case that releases the lane and then asks
# the record**, and these are those cases.

from test_agent import AGENT_SH, agentenv                           # noqa: E402,F401


def test_a_reclaimed_lane_does_not_resume_the_old_session(agentenv):
    """§1 C3 — a lane released and claimed again starts a fresh conversation.

    `SRV-FIX-431` criterion 1. The lane NAME is asserted to be the same before anything is
    concluded — a re-claim that happened to land on a different name would pass whatever the tool
    remembered, and the whole defect is that the name is what it remembered by.
    """
    env = agentenv.build()
    first = env.claim("TCK-001")
    was = env.start(first, resume=True)
    assert was["session_id"], was
    env.wait_log(first, "ready")
    env.srv.post(f"/api/lanes/{first['lane']}/kill", json={"grace_s": 1})

    gone = env.srv.delete(f"/api/lanes/{first['lane']}?force=true")
    assert gone.status_code in (200, 204), gone.text

    # The seat is free when `E9` says it is, and by nothing else — S5. Claiming before the release
    # has landed takes the NEXT name, and this case is about the name being reused.
    import time as _time
    deadline = _time.time() + 20
    while _time.time() < deadline:
        listed = env.srv.get("/api/lanes").json()
        if first["lane"] not in {row["lane"] for row in listed["lanes"]}:
            break
        _time.sleep(0.2)
    else:
        raise AssertionError(f"{first['lane']} never left GET /api/lanes after a forced release")

    # The SAME ticket, which is `SRV-FIX-431` criterion 1's own word: `E13` keeps the checkout,
    # so a second claim for that ticket adopts it and takes the lane name back. That is where a
    # name is reused inside one process's lifetime, and `sessionByLane_` is keyed by the name.
    again = env.claim("TCK-001")
    assert again["lane"] == first["lane"], (
        f"the re-claim landed on {again['lane']} and the released lane was {first['lane']} — this "
        f"case is about a name being REUSED, and these are two names")

    now = env.start(again, resume=True)
    assert now["session_id"] != was["session_id"], (
        f"a re-claimed lane resumed the released lane's conversation: {now['session_id']} is the "
        f"session {first['lane']} held before it was released. The agent "
        f"cannot find it, the run dies, and the lane reports `crashed` with no reason.")
    env.srv.post(f"/api/lanes/{again['lane']}/kill", json={"grace_s": 1})


def test_a_live_session_is_still_resumed(agentenv):
    """§1 C-NEG — the fix may not become *always start fresh*.

    `SRV-FIX-431` criterion 3, mandatory negative. Resuming a live session is what keeps a round's
    context across an interruption, and losing it silently would be this defect inverted. Same
    lane, never released: the second run carries the first run's session id and a different run id.
    """
    env = agentenv.build()
    lane = env.claim("TCK-001")
    first = env.start(lane, resume=True)
    env.wait_log(lane, "ready")
    env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})

    second = env.start(lane, resume=True)
    assert second["session_id"] == first["session_id"], (
        f"a lane that was never released started a new conversation: {second['session_id']} != "
        f"{first['session_id']} — an interruption now costs the round its context")
    assert second["run_id"] != first["run_id"], "a second run reused the first run's id"
    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})


def the_real_cli_on_a_missing_conversation():
    """The recording of what the REAL agent does when the conversation is gone.

    `_TEMPLATE.md` §2: *a stand-in is judged against the real thing.* The capture is committed at
    `tests/fixtures/cli-resume-2026-09-11-a-conversation-that-is-gone.json` and it is the arbiter —
    the stand-in below has to give the verdict the recording shows, and where the real thing exits
    non-zero, so must it. Re-capture with the `argv` the file carries.
    """
    import json
    from pathlib import Path

    path = (Path(__file__).resolve().parent / "fixtures"
            / "cli-resume-2026-09-11-a-conversation-that-is-gone.json")
    assert path.is_file(), f"{path} is gone; re-capture it before trusting the stand-in"
    cap = json.loads(path.read_text())
    assert cap["returncode"] != 0, cap
    assert cap["stderr"].strip(), cap
    return cap


GONE_CONVERSATION_AGENT = """#!/bin/sh
printf '%s\\n' "{said}" >&2
exit {code}
"""


def test_a_missing_conversation_is_named_on_the_lane(agentenv):
    """§1 C3 — a run that dies because its conversation is gone says so, not a bare `crashed`.

    `SRV-FIX-431` criterion 4. The stand-in prints what the REAL CLI printed and exits with the
    code the real CLI exited with — both read out of the committed capture, neither typed here —
    so a case that passes against the stand-in is a case that would pass against the program.

    The assertion is the outcome a person reads on the lane: it names the session id the agent
    could not find. `crashed` is true and useless; a session id is something to act on.
    """
    cap = the_real_cli_on_a_missing_conversation()
    said = cap["stderr"].strip().splitlines()[0]
    assert "No conversation found with session ID" in said, (
        f"the capture no longer carries the CLI's own diagnostic, so this case is asserting a "
        f"sentence nobody produces: {said!r}")

    env = agentenv.build(script=GONE_CONVERSATION_AGENT.format(said=said,
                                                               code=cap["returncode"]))
    lane = env.claim("TCK-001")
    run = env.start(lane, resume=True)

    import json as _json
    import time as _time
    deadline = _time.time() + 25
    row = None
    while _time.time() < deadline:
        rows = {r["lane"]: r for r in env.srv.get("/api/lanes").json()["lanes"]}
        row = rows[lane["lane"]]
        if row["state"]["process"] not in ("running", "starting", "idle"):
            if row.get("last_words"):
                break
        _time.sleep(0.2)

    assert row is not None, "the lane never appeared"
    assert row["state"]["process"] == "crashed", (
        f"the state vocabulary is closed and this run did crash; it reads "
        f"{row['state']['process']!r}: {row['state']}")
    said = _json.dumps(row.get("last_words"))
    assert run["session_id"] in said, (
        f"the lane says `crashed` and nothing else. `crashed` is true and useless — the reason "
        f"is the one thing a person can act on, and the conversation the agent could not find is "
        f"{run['session_id']}. The lane last said: {said}")
    assert "conversation" in said, (
        f"the lane names an id without saying what about it: {said}")
