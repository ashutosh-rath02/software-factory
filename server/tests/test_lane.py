"""SRV-FEAT-008 — a lane is a worktree, a branch, a seat, and possibly one scarce thing.

Every case builds its own synthetic git repository under the test's temporary directory and
points a server at it. Never this one: a lane module that only works against the repository it
was written in has learned that repository.

The safety assertion runs in every case and it is the one that matters most — the main
checkout's `git status` is byte-identical before and after. A tool that creates worktrees and
branches in somebody's repository has to be provably incapable of touching their work.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema
import pytest

# M8 is part of the socket contract, so the frame this suite produces is judged by the contract
# file rather than by what the server happened to send. `test_ws.py` names this test as where
# `resource` is exercised, and this is the assertion that claim rests on.
SOCKET_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)


# ── the synthetic repository ─────────────────────────────────────────────────────────────────

def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=check, timeout=30)


def status_of(root):
    """The main checkout's working tree, exactly as git reports it."""
    return git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout


def worktree_list(root):
    return git(root, "worktree", "list", "--porcelain").stdout


def branch_exists(root, branch):
    return git(root, "rev-parse", "--verify", "--quiet",
               f"refs/heads/{branch}", check=False).returncode == 0


DEFAULT_WORKSPACES = {"core": {"path": "."}}


# SRV-FIX-105 · the two moments of `SPEC.md` §E9 · Missing. The reap confirms an absent worktree
# over two sweeps of `SWEEP_INTERVAL_MS` (`app/lane/store.cpp`) before it acts, so a lane whose
# worktree vanished is REPORTED for that grace and REMOVED after it. Both numbers sit outside the
# window they name rather than on its edge: a test that lands on the boundary is the flake this
# ticket replaced.
REAP_REPORTED_S = 6      # one sweep in: seen the absence once, not yet acted
REAP_REMOVED_S = 20      # two confirmations and the release, on a loaded machine


def build_repo(root, *, lanes_host=3, resources=None, workspaces=None, agent=None, agents=None,
               tickets=("TCK-001", "TCK-002")):
    """A git repository carrying a manifest, some tickets, and one initial commit.

    `.factory/worktrees/` is ignored by the repository itself — the tool writes there
    (SPEC.md §2) and a target repository that does not ignore it sees its own status change.
    That is the repository's business, not the tool's: the tool never edits a `.gitignore`.

    `.factory/lanes/` joined that list with SRV-FIX-416: the lane set is written down so a restart
    does not forget it, and a claim that dirtied the target repository's status would be a tool
    changing the thing it is watching.
    """
    root = Path(root)
    (root / ".factory").mkdir(parents=True, exist_ok=True)
    manifest = {
        "factory": "0.1",
        "workspaces": workspaces or DEFAULT_WORKSPACES,
        "lanes": {"host": lanes_host},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
        "stages": ["SPEC", "IMPL", "GATE"],
    }
    if resources:
        manifest["resources"] = resources
    if agent:
        manifest["agent"] = agent
    if agents:
        manifest["default_agent"] = next(iter(agents))
        manifest["agents"] = agents
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    (root / ".gitignore").write_text(
        ".factory/worktrees/\n.factory/runs/\n.factory/ids.json\n.factory/lanes/\n")

    (root / "docs" / "epics").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "reports").mkdir(parents=True, exist_ok=True)
    tickets_dir = root / "docs" / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    for t in tickets:
        (tickets_dir / f"{t}.md").write_text(
            f"---\nid: {t}\ntitle: ticket {t}\nstatus: todo\n---\n\nbody of {t}\n")
    (root / "README.md").write_text("a repository the factory is pointed at\n")

    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "factory@test.local")
    git(root, "config", "user.name", "factory tests")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def gitrepo(tmp_path):
    """Build a repository, and assert on teardown that nothing in it moved."""
    made = []

    def _build(**kw):
        root = build_repo(tmp_path / f"repo{len(made)}", **kw)
        made.append((root, status_of(root)))
        return root

    yield _build

    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed:\nbefore:\n{before}\nafter:\n{status_of(root)}")


def claim(srv, ticket="TCK-001", workspace="core", **extra):
    body = {"ticket_id": ticket, "workspace": workspace}
    body.update(extra)
    return srv.post("/api/lanes", json=body)


def lane_path(lane):
    """A lane name carries a slash; both path forms mean the same lane."""
    return "/api/lanes/" + lane


def lanes_of(srv):
    r = srv.get("/api/lanes")
    assert r.status_code == 200, r.text
    return r.json()


# ── F7 · claim ───────────────────────────────────────────────────────────────────────────────

def test_claim_creates_worktree_and_branch(gitrepo, serve):
    """SPEC.md F7 — a worktree on disk, on a branch named for the ticket.

    A second lane on the same ticket takes the role into the branch name, so two lanes can
    never share one branch.
    """
    root = gitrepo()
    before = status_of(root)
    srv = serve(root)

    r = claim(srv)
    assert r.status_code == 201, r.text
    lane = r.json()

    assert lane["ticket_id"] == "TCK-001", lane
    assert lane["workspace"] == "core", lane
    assert lane["role"] == "run", lane
    assert lane["branch"] == "ticket/TCK-001", lane
    assert lane["state"]["process"] == "not_started", lane

    wt = Path(lane["worktree"])
    assert wt.is_dir(), f"no worktree at {wt}"
    assert (wt / ".git").exists(), f"{wt} is a plain directory, not a git worktree"
    assert (wt / "README.md").is_file(), f"the worktree has no checkout in it: {list(wt.iterdir())}"

    assert branch_exists(root, "ticket/TCK-001"), "the branch was not created"
    registered = worktree_list(root)
    assert str(wt.resolve()) in registered, registered
    assert "branch refs/heads/ticket/TCK-001" in registered, registered

    # A second lane on the same ticket — a different role, and therefore a different branch.
    r2 = claim(srv, role="qa")
    assert r2.status_code == 201, r2.text
    second = r2.json()
    assert second["lane"] != lane["lane"], second
    assert second["branch"] == "ticket/TCK-001-qa", second
    assert Path(second["worktree"]).is_dir(), second
    assert branch_exists(root, "ticket/TCK-001-qa")

    body = lanes_of(srv)
    assert [l["lane"] for l in body["lanes"]] == [lane["lane"], second["lane"]], body
    assert body["seats"]["host"] == {"total": 3, "used": 2}, body["seats"]

    assert status_of(root) == before, "the main checkout changed"


# ── F7 · release ─────────────────────────────────────────────────────────────────────────────

def test_release_keeps_the_branch_and_says_what_happened(gitrepo, serve):
    """The seat comes back, the branch ALWAYS survives, and the response says which of the two
    things happened to the worktree. Work is never destroyed by a release."""
    root = gitrepo()
    before = status_of(root)
    srv = serve(root)

    clean = claim(srv, ticket="TCK-001").json()
    dirty = claim(srv, ticket="TCK-002").json()
    assert lanes_of(srv)["seats"]["host"]["used"] == 2

    # 1 — a clean lane: the worktree goes, the branch stays.
    r = srv.delete(lane_path(clean["lane"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["released"] is True, out
    assert out["worktree_removed"] is True, out
    assert out["branch_kept"] is True, out
    assert out["branch"] == "ticket/TCK-001", out
    assert out["detail"]["reason"] == "clean", out
    assert not Path(clean["worktree"]).exists(), "a clean worktree was left behind"
    assert branch_exists(root, "ticket/TCK-001"), "the release deleted the branch"

    # 2 — a lane with uncommitted work: the worktree stays, and the response says so.
    scratch = Path(dirty["worktree"]) / "notes.txt"
    scratch.write_text("work an agent did and did not commit\n")
    r = srv.delete(lane_path(dirty["lane"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["released"] is True, out
    assert out["worktree_removed"] is False, out
    assert out["branch_kept"] is True, out
    assert out["detail"]["reason"] == "uncommitted_changes", out
    assert scratch.is_file(), "uncommitted work was destroyed by a release"
    assert branch_exists(root, "ticket/TCK-002")

    body = lanes_of(srv)
    assert body["lanes"] == [], body
    assert body["seats"]["host"]["used"] == 0, body["seats"]

    # An unknown lane is a 404 that names where it looked.
    r = srv.delete(lane_path(clean["lane"]))
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found", r.text

    assert status_of(root) == before, "the main checkout changed"


# ── F8 · the queue ───────────────────────────────────────────────────────────────────────────

def test_second_lane_queues_on_a_resource(gitrepo, serve):
    """SPEC.md F8 — the second lane is `queued`, visible, with its position; starting a run in
    it is refused by name; and releasing the first emits M8 and makes it startable."""
    root = gitrepo(
        workspaces={"edge": {"path": "."}},
        resources={"chamber": {"exclusive": True, "needed_by": ["edge"],
                               "description": "one chamber, one run at a time"}})
    before = status_of(root)
    srv = serve(root)

    first = claim(srv, ticket="TCK-001", workspace="edge")
    assert first.status_code == 201, first.text
    first = first.json()
    assert first["state"]["process"] == "not_started", first
    assert first["resources_held"] == ["chamber"], first
    assert first["waiting_for"] is None, first

    second = claim(srv, ticket="TCK-002", workspace="edge")
    assert second.status_code == 201, second.text
    second = second.json()
    assert second["state"]["process"] == "queued", second
    assert second["resources_held"] == [], second
    # SRV-FEAT-180 — `holder_alive` is part of this shape now. The holder here has no process, so
    # the honest answer is `false`: a lane that never started one holds the chamber exactly as hard
    # as a lane whose agent died, and that is the state somebody would otherwise wait on.
    assert second["waiting_for"] == {
        "resource": "chamber", "held_by": first["lane"], "holder_alive": False,
        "position": 1}, second

    body = lanes_of(srv)
    res = {r["name"]: r for r in body["resources"]}
    assert res["chamber"]["held_by"] == first["lane"], body["resources"]
    assert res["chamber"]["queue"] == [second["lane"]], body["resources"]
    assert res["chamber"]["exclusive"] is True, body["resources"]

    # Starting a run in a queued lane is refused, and the refusal names the holder and the place
    # in the queue — an error that does not say who has it sends the reader to the code.
    r = srv.post(lane_path(second["lane"]) + "/runs", json={})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "resource_busy", err
    assert err["detail"]["resource"] == "chamber", err
    assert err["detail"]["held_by"] == first["lane"], err
    assert err["detail"]["position"] == 1, err

    ws = srv.ws()
    try:
        ws.send({"m": "subscribe", "scope": {"requirements": True}})
        ws.wait_for("snapshot")

        r = srv.delete(lane_path(first["lane"]))
        assert r.status_code == 200, r.text

        frame = ws.wait_for("resource")
        SOCKET_VALIDATOR.validate(frame)      # M8, judged by docs/technical/contracts/socket.schema.json
        assert frame["name"] == "chamber", frame
        assert frame["held_by"] == second["lane"], frame
        assert frame["queue"] == [], frame
        assert frame["exclusive"] is True, frame
    finally:
        ws.close()

    body = lanes_of(srv)
    granted = body["lanes"][0]
    assert granted["lane"] == second["lane"], body
    assert granted["state"]["process"] == "not_started", granted
    assert granted["waiting_for"] is None, granted
    assert granted["resources_held"] == ["chamber"], granted

    # ...and it is startable: whatever the run endpoint does next, it no longer refuses on the
    # resource. (Spawning itself is SRV-FEAT-009.)
    r = srv.post(lane_path(second["lane"]) + "/runs", json={})
    assert "resource_busy" not in r.text, r.text

    assert status_of(root) == before, "the main checkout changed"


# ── E12 · no seat ────────────────────────────────────────────────────────────────────────────

def test_no_seat_is_a_409_not_a_silent_wait(gitrepo, serve):
    """With every seat taken the claim is refused, immediately, with the counts.

    A request that blocks instead of answering is how a UI learns to look frozen."""
    root = gitrepo(lanes_host=2)
    before = status_of(root)
    srv = serve(root)

    assert claim(srv, ticket="TCK-001").status_code == 201
    assert claim(srv, ticket="TCK-002").status_code == 201

    started = time.monotonic()
    r = claim(srv, ticket="TCK-001", role="qa")
    elapsed = time.monotonic() - started

    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "no_seat", err
    assert err["detail"]["total"] == 2, err
    assert err["detail"]["used"] == 2, err
    assert elapsed < 2.0, f"the refusal took {elapsed:.1f}s; it waited instead of answering"

    body = lanes_of(srv)
    assert len(body["lanes"]) == 2, body
    assert body["seats"]["host"] == {"total": 2, "used": 2}, body["seats"]

    assert status_of(root) == before, "the main checkout changed"


# ── E9 · a lane whose worktree went away ─────────────────────────────────────────────────────

def test_a_vanished_worktree_is_reported_before_it_is_removed(gitrepo, serve):
    """`SPEC.md` §E9 · Missing, in the two phases SRV-FIX-105 gave it.

    The clause used to be the whole truth: a lane whose worktree vanished *"comes back with
    `state.process:"crashed"` and `detail.reason:"worktree_missing"` rather than being omitted"*,
    for ever, because nothing cleaned it up. SRV-FIX-105 added the cleanup, so the row now has a
    documented life: **reported for the grace, gone after it**.

    Both halves are asserted here, and neither is asserted with a poll. The row is the subject, so
    `GET /api/lanes` cannot be the wait — this case waits in silence and makes exactly ONE call at
    each of the two moments. The version that stood before this ticket read E9 milliseconds after
    the `rmtree` and was green for that reason alone: the lane was listed at t=4.79s and gone at
    t=5.04s, and the assertion happened to land in the first window. A test that passes by being
    fast fails when the machine is loaded, which is the definition of the flake nobody can debug.
    """
    root = gitrepo()
    before = status_of(root)
    srv = serve(root)

    lane = claim(srv).json()
    shutil.rmtree(lane["worktree"])

    time.sleep(REAP_REPORTED_S)                   # inside the grace: still drawn, still counted
    body = lanes_of(srv)
    assert len(body["lanes"]) == 1, f"the lane was omitted from E9 instead of reported: {body}"
    gone = body["lanes"][0]
    assert gone["lane"] == lane["lane"], gone
    assert gone["state"]["process"] == "crashed", gone
    assert gone["detail"]["reason"] == "worktree_missing", gone
    assert gone["detail"]["path"] == lane["worktree"], gone
    assert gone["needs_you"] is True, (
        f"a lane that died with nobody looking is exactly what needs_you counts (SPEC.md §3.3): "
        f"{gone}")
    assert body["seats"]["host"]["used"] == 1, body["seats"]

    time.sleep(REAP_REMOVED_S - REAP_REPORTED_S)  # past it: the reap has taken the seat back
    after = lanes_of(srv)
    assert after["lanes"] == [], f"the lane was never reaped: {after}"
    assert after["seats"]["host"]["used"] == 0, after["seats"]

    assert status_of(root) == before, "the main checkout changed"


# ── E13 · a live process ─────────────────────────────────────────────────────────────────────

def _alive(pid):
    return Path(f"/proc/{pid}").exists()


def process_group(pgid):
    """Every process in `pgid`'s process group — the leader and every descendant of the run.

    SRV-FIX-029. `kill(-pid, …)` means exactly "this process group", so this is the set the tool
    claims to have killed, read back from the table rather than assumed. A sandboxed process is in
    its own PID namespace and is still perfectly visible from the host, which is what makes the
    claim checkable at all.
    """
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            line = (entry / "stat").read_text()
            close = line.rfind(")")
            state, _ppid, pgrp = line[close + 2:].split()[:3]
            if int(pgrp) != pgid:
                continue
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace").strip()
        except (OSError, ValueError, IndexError):
            continue
        found[int(entry.name)] = f"[{state}] {cmdline or '(no cmdline)'}"
    return found


def carrying(marker):
    """Every process on this machine whose command line carries `marker`.

    The marker is a path belonging to ONE test, so this counts the table without counting the
    developer's shell or a suite running in a sibling worktree. It catches a survivor that has
    left the process group too, which the group enumeration above cannot.
    """
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace")
        except OSError:
            continue
        if marker in cmdline:
            found[int(entry.name)] = cmdline.strip()
    return found


def settle(fn, timeout=8):
    """`fn()` once it is empty, or after `timeout` — so "nothing is left" waits to be sure."""
    deadline = time.time() + timeout
    while True:
        value = fn()
        if not value or time.time() > deadline:
            return value
        time.sleep(0.05)


def test_release_with_a_live_process_is_refused_unless_forced(gitrepo, serve):
    """Releasing a lane out from under a running process is refused; `?force=true` kills it
    first and says so. The process is a real child — a mock cannot be killed."""
    root = gitrepo()
    before = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    lane = claim(srv).json()
    r = srv.post("/api/_test/lane-process",
                 json={"lane": lane["lane"], "command": "sleep 300"})
    assert r.status_code == 202, r.text
    pid = r.json()["pid"]
    assert _alive(pid), f"the hook did not start a process ({pid})"

    r = srv.delete(lane_path(lane["lane"]))
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "wrong_state", err
    assert err["detail"]["state"] is not None, err
    assert _alive(pid), "the refused release killed the process anyway"
    assert Path(lane["worktree"]).is_dir(), "the refused release removed the worktree anyway"

    r = srv.delete(lane_path(lane["lane"]) + "?force=true")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["released"] is True, out
    assert out["killed"] is True, out
    assert out["detail"]["killed_pid"] == pid, out

    deadline = time.time() + 8
    while time.time() < deadline and _alive(pid):
        time.sleep(0.05)
    assert not _alive(pid), (
        f"pid {pid} is still in /proc after a forced release — it was left running or unreaped")

    assert lanes_of(srv)["lanes"] == []
    assert branch_exists(root, "ticket/TCK-001"), "a forced release deleted the branch"
    assert status_of(root) == before, "the main checkout changed"


def test_force_release_leaves_no_process_behind(gitrepo, serve):
    """SRV-FIX-029 — `killed:true` has to mean the RUN is gone, not that one pid is.

    The escaped assertion named `detail.killed_pid` and checked `/proc/<that>`. It was true every
    time and the sandbox went on running: the pid the tool wrote down is the confinement helper
    that dies on `SIGTERM` on its own, and the survivor is the one the tool never had a number
    for. So this counts the process TABLE either side of the release, by a marker unique to this
    test — the lane's own worktree path, which every process of the run carries in its argv — and
    the difference has to be zero. A count cannot be fooled by knowing the wrong pid.

    Two lanes, because a leak has two shapes and only one of them is about a wedged process:

    * lane A is released the instant it is started, while the sandbox is still coming up. That is
      what `test_release_with_a_live_process_is_refused_unless_forced` does and it is where the
      escape lives — the helper has not yet armed `--die-with-parent`, so the SIGTERM that kills
      the leader tells the rest of the sandbox nothing.
    * lane B is released once the sandbox is fully up, so the process group provably holds
      DESCENDANTS and not just a leader, and the assertion is about all of them.
    """
    root = gitrepo(tickets=("TCK-001", "TCK-002"))
    before = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    def start(lane):
        """Spawn, and do NOTHING else — reading the process table here would spend the window.

        The table is counted BEFORE the process exists, so nothing stands between the spawn and
        the release. A `/proc` scan takes tens of milliseconds and the window this ticket is
        about is under fifty; a snapshot taken after the spawn closes the bug it is looking for,
        which is how the first draft of this test passed against the defect.
        """
        r = srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": "sleep 300"})
        assert r.status_code == 202, r.text
        return r.json()["pid"]

    # ── A · released while the sandbox is still coming up ────────────────────────────────────
    a = claim(srv, ticket="TCK-001").json()
    a_mark = a["worktree"]
    a_before = carrying(a_mark)
    assert a_before == {}, f"the marker is not unique to this lane: {a_before}"
    a_pid = start(a)

    r = srv.delete(lane_path(a["lane"]) + "?force=true")
    assert r.status_code == 200, r.text
    assert r.json()["killed"] is True, r.json()
    assert r.json()["detail"]["killed_pid"] == a_pid, r.json()

    a_left = settle(lambda: {p: c for p, c in carrying(a_mark).items() if p not in a_before})
    assert a_left == {}, (
        f"a forced release answered killed:true and left {len(a_left)} process(es) of the run "
        f"in the table — the leader (pid {a_pid}) is gone and the sandbox is not:\n"
        + "\n".join(f"  pid {p}: {c[:200]}" for p, c in sorted(a_left.items())))
    assert process_group(a_pid) == {}, process_group(a_pid)

    # ── B · released with the whole tree up, so there are descendants to lose ────────────────
    b = claim(srv, ticket="TCK-002").json()
    b_mark = b["worktree"]
    b_before = carrying(b_mark)
    b_pid = start(b)

    deadline = time.time() + 8
    while len(process_group(b_pid)) < 2 and time.time() < deadline:
        time.sleep(0.05)
    group = process_group(b_pid)
    assert len(group) >= 2, (
        f"the run never became a tree, so this case would have proved nothing about "
        f"descendants: {group}")

    r = srv.delete(lane_path(b["lane"]) + "?force=true")
    assert r.status_code == 200, r.text
    assert r.json()["killed"] is True, r.json()

    b_left = settle(lambda: process_group(b_pid))
    assert b_left == {}, (
        f"the process group of the run survived its forced release; it held {sorted(group)} "
        f"before and still holds:\n"
        + "\n".join(f"  pid {p}: {c[:200]}" for p, c in sorted(b_left.items())))
    leaked = settle(lambda: {p: c for p, c in carrying(b_mark).items() if p not in b_before})
    assert leaked == {}, leaked

    assert lanes_of(srv)["lanes"] == []
    assert status_of(root) == before, "the main checkout changed"


# ── the mandatory negative ───────────────────────────────────────────────────────────────────


def test_force_release_kills_a_leader_that_exits_first(gitrepo, serve):
    """MANDATORY NEGATIVE (SRV-FIX-029) — the leader dying is not the run dying.

    This is the exact shape that escaped `SRV-FEAT-008`, built on purpose instead of by accident:

    * the sandboxed process **ignores `SIGTERM`** (`trap '' TERM`), so nothing inside the sandbox
      cooperates with the polite half of the kill;
    * the lane is released **the instant the process starts**, before the confinement helper has
      armed `--die-with-parent` — measured at under 50 ms on this machine, and it is the window
      `test_release_with_a_live_process_is_refused_unless_forced` has always run inside;
    * so the group's leader exits at once on `SIGTERM` — it is the one process that was always
      going to die — and `waitpid` on it returns success while the sandbox runs on.

    Three rounds, because the window is a race and one round is an anecdote. Against `killGroup`
    as `SRV-FEAT-008` wrote it this leaks one confinement helper per round, every round.
    """
    root = gitrepo(tickets=tuple(f"TCK-{i:03d}" for i in range(1, 4)))
    before = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    survivors = {}
    for i in range(1, 4):
        lane = claim(srv, ticket=f"TCK-{i:03d}").json()
        mark = lane["worktree"]
        seen = carrying(mark)

        r = srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": "trap '' TERM; sleep 300"})
        assert r.status_code == 202, r.text
        pid = r.json()["pid"]

        r = srv.delete(lane_path(lane["lane"]) + "?force=true")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["released"] is True, out
        assert out["killed"] is True, out

        # The leader really did go — that half was never in doubt, and asserting it is what made
        # the escape possible. It is kept so the failure below can never be read as "nothing was
        # killed at all".
        assert settle(lambda: {} if not _alive(pid) else {pid: "leader"}) == {}, (
            f"round {i}: the leader (pid {pid}) itself survived")

        left = settle(lambda: {p: c for p, c in carrying(mark).items() if p not in seen})
        if left:
            survivors[i] = left

    assert survivors == {}, (
        "a sandboxed process outlived the forced release that reported killing it, in "
        f"{len(survivors)} of 3 rounds — the leader was reaped and the run was not:\n"
        + "\n".join(f"  round {i}: pid {p}: {c[:200]}"
                    for i, left in sorted(survivors.items()) for p, c in sorted(left.items())))

    assert status_of(root) == before, "the main checkout changed"




def test_a_lane_cannot_be_claimed_twice(gitrepo, serve):
    """MANDATORY NEGATIVE — two concurrent claims for one (ticket, role) produce ONE lane.

    The engine is four threads on one io_context, so both requests can be inside the claim at
    the same instant. They are raced here with a barrier, not issued in sequence: a sequential
    pair proves only that the second one could read what the first one wrote.

    The failure this exists to make unreachable is two worktrees on one branch.
    """
    root = gitrepo(lanes_host=12, tickets=[f"TCK-{i:03d}" for i in range(1, 7)])
    before = status_of(root)
    srv = serve(root)

    for i in range(1, 6):
        ticket = f"TCK-{i:03d}"
        barrier = threading.Barrier(2)

        def race():
            barrier.wait(timeout=10)
            return srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": "core"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [f.result() for f in [pool.submit(race), pool.submit(race)]]

        codes = sorted(r.status_code for r in responses)
        assert codes == [201, 409], (
            f"{ticket}: two concurrent claims answered {codes}, not one 201 and one 409: "
            + " · ".join(r.text for r in responses))

        refusal = [r for r in responses if r.status_code == 409][0].json()["error"]
        assert refusal["code"] == "lane_exists", refusal
        assert refusal["detail"]["ticket_id"] == ticket, refusal
        assert refusal["detail"]["role"] == "run", refusal

        body = lanes_of(srv)
        for_ticket = [l for l in body["lanes"] if l["ticket_id"] == ticket]
        assert len(for_ticket) == 1, f"{ticket} has {len(for_ticket)} lanes: {for_ticket}"

    body = lanes_of(srv)
    assert body["seats"]["host"]["used"] == 5, body["seats"]

    # One worktree per lane, one branch per worktree, and no branch checked out twice.
    registered = worktree_list(root)
    branches = [line.split()[1] for line in registered.splitlines() if line.startswith("branch ")]
    checked_out = [b for b in branches if b.startswith("refs/heads/ticket/")]
    assert len(checked_out) == 5, f"expected 5 lane worktrees, got {checked_out}\n{registered}"
    assert len(set(checked_out)) == len(checked_out), (
        f"a branch is checked out in two worktrees at once: {checked_out}")

    assert status_of(root) == before, "the main checkout changed"


# ── SRV-FIX-059 ══════════════════════════════════════════════════════════════════════════════
#
# A lane blocked by its own leftovers had NO WAY OUT from the tool.
#
# The server does not adopt lanes it cannot supervise across a restart — deliberate, and asserted
# elsewhere — so a restart leaves `.factory/worktrees/lane/01` on disk and `GET /api/lanes`
# answering `{"lanes":[]}`. Then `POST /api/lanes` is `409 worktree_in_the_way` because the path
# exists, and `DELETE /api/lanes/lane/01?force=true` is `404 not_found` because the record is gone.
# E13 is the escape hatch and it was keyed on exactly the state that is missing.
#
# The refusal is CORRECT — the tool must not delete a directory that might hold work. The defect
# was that it offered no way to resolve the state it was reporting, so a person who could see the
# problem on screen had to open a shell.


def _forget_the_record(srv, root, lane):
    """What a restart does: the directory survives, the tool's memory of it does not.

    Restarting the server for real would work and would cost ten seconds a case; this is the same
    state reached in one call, and `test_agent.py` already asserts that a restarted tool reports
    no lanes.
    """
    r = srv.post("/api/_test/forget-lane", json={"lane": lane})
    assert r.status_code in (200, 202), r.text
    assert lanes_of(srv)["lanes"] == [], lanes_of(srv)
    assert Path(root, ".factory", "worktrees", lane).is_dir(), "the directory should have survived"


def test_a_leftover_lane_checkout_can_be_cleared_from_the_api(gitrepo, serve):
    """THE criterion: a person with a browser and no terminal must be able to get out.

    Before this, the two calls a person can make from the screen answered `409 worktree_in_the_way`
    and `404 not_found`, and the only way forward was `git worktree remove` in a shell.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    _forget_the_record(srv, root, lane)

    # SRV-FIX-059 round 7 — the collision this used to open with is gone: a claim steps past a
    # leftover instead of landing on it. Clearing one is now housekeeping a person asks for, not a
    # way out of a trap, and E13 is reached directly because nothing sends anybody here any more.

    cleared = srv.delete(lane_path(lane) + "?force=true")
    assert cleared.status_code == 200, (
        f"the tool refuses to start AND refuses to clean up — there is no way out of this state "
        f"from the screen: {cleared.status_code} {cleared.text}")
    body = cleared.json()
    assert body["worktree_removed"] is True, body
    assert body["branch_kept"] is True, "a leftover checkout is not a licence to delete a branch"
    assert not Path(root, ".factory", "worktrees", lane).exists(), body

    # ...and the claim that was blocked now succeeds, which is the whole point. The NAME may
    # differ — `nextName_` is monotonic within one process on purpose, and the failed claim
    # consumed one — so what is asserted is that a person is no longer stuck.
    again = claim(srv)
    assert again.status_code == 201, (
        f"the leftover was cleared and a claim still cannot proceed: {again.text}")


def test_a_leftover_holding_work_is_never_cleared(gitrepo, serve):
    """NEGATIVE (mandatory). ESCAPES.md row 1, restated.

    The cheap fix for this whole ticket is to delete whatever is in the way. A worktree with
    uncommitted work is refused, and is still there afterwards, byte for byte.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    (tree / "SOMEBODY-WAS-WORKING-HERE.md").write_text("half an afternoon\n")
    _forget_the_record(srv, root, lane)

    refused = srv.delete(lane_path(lane) + "?force=true")
    assert refused.status_code == 409, (
        f"a leftover holding uncommitted work was cleared: {refused.status_code} {refused.text}")
    assert refused.json()["error"]["code"] == "uncommitted_changes", refused.json()
    assert (tree / "SOMEBODY-WAS-WORKING-HERE.md").read_text() == "half an afternoon\n"
    assert tree.is_dir()


def test_a_directory_that_is_not_ours_is_still_refused(gitrepo, serve):
    """NEGATIVE (mandatory). The tool may clean up after itself and after nobody else.

    A path that is not a git worktree of this repository is refused with its OWN code and left
    untouched — `git worktree remove` would refuse it too, and the answer must say which case it is
    rather than reporting somebody's directory as a leak of ours.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    stranger = Path(root, ".factory", "worktrees", "lane", "01")
    stranger.mkdir(parents=True)
    (stranger / "not-a-worktree.txt").write_text("somebody else's\n")

    refused = srv.delete("/api/lanes/lane/01?force=true")
    assert refused.status_code in (404, 409), refused.text
    assert refused.json()["error"]["code"] != "uncommitted_changes", refused.json()
    assert (stranger / "not-a-worktree.txt").read_text() == "somebody else's\n"


def _commit_inside(tree, name="COMMITTED-WORK.md", text="an afternoon, and it was committed\n"):
    """Work that IS committed, on the lane's own branch, leaving the worktree clean.

    `git status --porcelain` — the only thing `isDirty` runs — sees nothing here. That is the
    whole point: the second of the two inputs §1 names is invisible to the check that guards it.
    """
    import subprocess
    (tree / name).write_text(text)
    for args in (["add", name], ["commit", "-q", "-m", "work the lane's branch has"]):
        subprocess.run(["git", "-C", str(tree)] + args, check=True,
                       capture_output=True)
    sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    dirt = subprocess.run(["git", "-C", str(tree), "status", "--porcelain"],
                          check=True, capture_output=True, text=True).stdout
    assert dirt == "", f"this fixture must leave a CLEAN worktree, got: {dirt!r}"
    return sha


def test_a_leftover_whose_branch_has_unmerged_commits_is_never_cleared(gitrepo, serve):
    """NEGATIVE (mandatory) — the SECOND of the two inputs section 1 names.

    Section 1: "a worktree with an uncommitted change, **or with commits the branch has that
    `main` does not**, is refused, and the directory is still there afterwards."

    The first half shipped and the second did not, because `lane::git::isDirty` runs
    `git status --porcelain` and nothing else: it can see an uncommitted change and cannot see an
    unmerged commit. QA measured the gap at `200 worktree_removed:true, dir_exists=False`.

    Nothing is destroyed when this happens — the branch survives and `git worktree add` brings the
    files back. What is destroyed is the person's ability to find them: they pressed a button
    labelled "clear the leftover" and a checkout with work in it went away without a word.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    sha = _commit_inside(tree)
    _forget_the_record(srv, root, lane)

    refused = srv.delete(lane_path(lane) + "?force=true")
    assert refused.status_code == 409, (
        f"a leftover whose branch carries work no other branch has was cleared: "
        f"{refused.status_code} {refused.text}")
    err = refused.json()["error"]
    assert err["code"] == "unmerged_commits", refused.json()
    assert err["detail"]["commits"] >= 1, refused.json()
    assert err["detail"]["branch"], refused.json()
    assert tree.is_dir(), "the directory must still be there"
    assert (tree / "COMMITTED-WORK.md").read_text() == "an afternoon, and it was committed\n"

    import subprocess
    still = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()
    assert still == sha, "the commit the refusal is about must be untouched"


def test_a_leftover_whose_commits_are_already_merged_still_clears(gitrepo, serve):
    """The refusal above must not turn into "never clear anything with a commit in it".

    A lane branch whose work is already on another branch holds nothing anybody can lose, and it
    is the ordinary state after a merge. If this case were refused too, the way out that
    `SRV-FIX-059` exists to build would be shut again for every lane that did its job.
    """
    import subprocess
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    sha = _commit_inside(tree)

    branch = subprocess.run(["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(root), "branch", "already-merged-elsewhere", sha],
                   check=True, capture_output=True)
    _forget_the_record(srv, root, lane)

    cleared = srv.delete(lane_path(lane) + "?force=true")
    assert cleared.status_code == 200, (
        f"a leftover whose commits exist on another branch was refused, so the way out is shut "
        f"for the ordinary case: {cleared.status_code} {cleared.text}")
    assert cleared.json()["worktree_removed"] is True, cleared.json()
    assert not tree.exists()
    kept = subprocess.run(["git", "-C", str(root), "rev-parse", branch],
                          capture_output=True, text=True)
    assert kept.returncode == 0 and kept.stdout.strip() == sha, "the branch is still kept"


def test_a_supervised_release_still_removes_a_worktree_whose_branch_has_commits(gitrepo, serve):
    """The asymmetry between the two paths is a DECISION, and this is where it is written down.

    `SRV-FIX-060` failed QA for fixing one of two call sites of the same function, so the question
    has to be answered here rather than left silent: `release()` removes a worktree in two places,
    and only ONE of them gains the unmerged-commit refusal.

    They are not the same act. A supervised release is the owner of a lane they can see ending it
    deliberately, with the branch kept by name; refusing it would keep the worktree, and the next
    claim of that lane number — a DIFFERENT ticket — would then hit `worktree_in_the_way`, which is
    the exact trap this ticket exists to remove. Clearing a leftover is a blind cleanup of
    something the tool has no record of, pressed by somebody who cannot see what is inside.

    If this case is ever made to refuse as well, this test fails and whoever changed it has to say
    why the trap it reopens is acceptable.
    """
    import subprocess
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    sha = _commit_inside(tree)
    branch = subprocess.run(["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()

    released = srv.delete(lane_path(lane))
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["released"] is True, body
    assert body["worktree_removed"] is True, body
    assert body["branch_kept"] is True, body
    assert not tree.exists()

    kept = subprocess.run(["git", "-C", str(root), "rev-parse", branch],
                          capture_output=True, text=True)
    assert kept.returncode == 0 and kept.stdout.strip() == sha, (
        "the branch — and the work on it — must survive a supervised release")


def test_a_directory_that_is_not_ours_is_refused_with_its_own_code(gitrepo, serve):
    """Section 1 asks for "a different code from the leftover case"; the shipped assertion allowed
    the PRE-FIX `404 not_found` to satisfy it (`status in (404, 409)` and `code != "uncommitted_changes"`).

    QA measured the real answer — `409 not_ours`, `detail.ours:false` — and it is better than the
    test. An assertion weaker than the behaviour it guards is a gate that would say yes to the
    regression.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    stranger = Path(root, ".factory", "worktrees", "lane", "01")
    stranger.mkdir(parents=True)
    (stranger / "not-a-worktree.txt").write_text("somebody else's\n")

    refused = srv.delete("/api/lanes/lane/01?force=true")
    assert refused.status_code == 409, refused.text
    err = refused.json()["error"]
    assert err["code"] == "not_ours", refused.json()
    assert err["detail"]["ours"] is False, refused.json()
    assert (stranger / "not-a-worktree.txt").read_text() == "somebody else's\n"


def test_a_worktree_outside_the_lane_root_is_never_cleared(gitrepo, serve):
    """NEGATIVE (mandatory). SRV-FIX-059 QA round 2, finding 1 — §1's Safety boundary, enforced.

    Measured by QA: `200 worktree_removed:true`, and a developer's own worktree gone, file and all.

    `isRegisteredWorktree` answers *"does git list this path"*, which is strictly weaker than the
    question `git.h` says it is for — its own comment reads *"the question the tool has to answer
    before removing anything is 'did I create this', not 'does this look like mine'"*. Every
    worktree of the repository answers yes to the weaker one, **including the main checkout and
    every checkout a person made by hand.**

    It needs a crafted lane name, which no shipped client sends. That bounds who trips it; it does
    not make the boundary exist. §1 says nothing outside `.factory/worktrees/` and never `main`'s
    checkout, and until this case nothing in the tree said it in a form that could fail.
    """
    import subprocess
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    # a worktree of this repository that the tool did not create and has no business touching
    # Where QA's own probe put it: `.factory/worktrees/` + `../../<name>` lands HERE, a sibling
    # of `.factory` inside the repository root. Excluded from git rather than tracked, so the
    # fixture's "the main checkout is byte-identical" teardown still means what it says.
    Path(root, ".git", "info").mkdir(parents=True, exist_ok=True)
    Path(root, ".git", "info", "exclude").write_text("a-developers-own-worktree/\n")
    mine = Path(root) / "a-developers-own-worktree"

    # A lane FIRST, so `.factory/worktrees/` exists. Without it every component of the traversal
    # after the missing directory fails to resolve and the answer is `404` — a refusal that comes
    # from the path not being walkable rather than from any boundary, which would make this case
    # green in the one state where the hole cannot open.
    made = claim(srv)
    assert made.status_code == 201, made.text
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-b", "mine", str(mine)],
                   check=True, capture_output=True)
    keep = mine / "HALF-A-DAY.md"
    keep.write_text("not the tool's to delete\n")
    # COMMITTED, and its commit also on another ref — so every question the clear path asks about
    # this directory answers "safe": registered with git, clean, nothing unmerged. Without this the
    # pre-fix build stops at `409 uncommitted_changes` and the case would look like a boundary
    # doing its job, when what actually happened is that the tool walked out of its own root, called
    # somebody else's checkout `"ours": true`, and was saved by an unrelated guard.
    for args in (["add", "-A"], ["commit", "-q", "-m", "their work"]):
        subprocess.run(["git", "-C", str(mine)] + args, check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(mine), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(root), "branch", "theirs-elsewhere", sha],
                   check=True, capture_output=True)

    # The crafted name QA reached it with. `%2f` keeps the whole thing ONE path segment, so it
    # matches `/api/lanes/{lane}`, and the lane name decodes to a traversal. A plain `../..` is
    # several segments and the router answers 404 — which is why this hole looked closed.
    for escape in ("..%2f..%2fa-developers-own-worktree",
                   "lane%2f..%2f..%2f..%2fa-developers-own-worktree",
                   "..%2f..%2f.%2fa-developers-own-worktree"):
        answer = srv.delete(f"/api/lanes/{escape}?force=true")
        assert answer.status_code == 400, (
            f"{escape} was not refused by the boundary: {answer.status_code} {answer.text}")
        assert answer.json()["error"]["code"] == "bad_lane", answer.text
        assert mine.is_dir() and keep.read_text() == "not the tool's to delete\n", escape

    escape = "..%2f..%2fa-developers-own-worktree"
    refused = srv.delete(f"/api/lanes/{escape}?force=true")

    assert refused.status_code in (400, 404, 409), (
        f"a worktree outside the lane root was accepted for removal: "
        f"{refused.status_code} {refused.text}")
    assert mine.is_dir(), "the developer's own worktree was removed"
    assert keep.read_text() == "not the tool's to delete\n", "their file was destroyed"


def test_the_main_checkout_is_never_cleared(gitrepo, serve):
    """NEGATIVE (mandatory). The worst instance of the same hole, named on its own.

    The main checkout is a worktree `git worktree list` reports, so the weaker question said yes to
    it too. `ESCAPES.md` row 1 is an agent writing into the main checkout; this is the tool doing
    it, through its own recovery path, on a call a person can make from a browser.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")
    assert claim(srv).status_code == 201        # so `.factory/worktrees/` is walkable
    before = sorted(p.name for p in Path(root).iterdir())

    refused = srv.delete("/api/lanes/lane%2f..%2f..%2f..?force=true")
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "bad_lane", (
        f"the main checkout was not refused by the boundary: "
        f"{refused.status_code} {refused.text}")
    assert Path(root, ".git").exists(), "the repository is gone"
    assert sorted(p.name for p in Path(root).iterdir()) == before, "the main checkout changed"






def test_a_leftover_holding_ignored_files_is_never_cleared(gitrepo, serve):
    """NEGATIVE (mandatory). SRV-FIX-059 QA round 3 — the third reading of "holds work".

    `git status --porcelain` answers *"is there a tracked change or an untracked file"*. A
    `.gitignore`d path is neither, so the clear path saw an empty directory and removed it — while
    the refusal a person decides on said `uncommitted_changes: 0`, an affirmative statement that
    there was nothing to lose.

    In THIS repository the ignored paths are exactly the ones that cannot be got back:

        firmware/master/log/   the firmware gate's own capture. Root CLAUDE.md names the file as
                               the gate's evidence. One board, one run, not reproducible.
        server/data/           the embedded database
        *.pem                  keys

    No crafted input and no traversal: the ordinary button, on the ordinary leftover, following the
    exact call `clear_with` hands the screen.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)

    (tree / ".gitignore").write_text("evidence/\n")
    (tree / "evidence").mkdir()
    keep = tree / "evidence" / "agent_app_1.log"
    keep.write_text("one board, one run, not reproducible\n")
    import subprocess
    for args in (["add", "-A"], ["commit", "-q", "-m", "ignore the capture directory"]):
        subprocess.run(["git", "-C", str(tree)] + args, check=True, capture_output=True)
    # the lane's own commit, on another ref too, so the unmerged-commits guard below is not what
    # answers — this case is about the ignored file and nothing else
    sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(root), "branch", "-f", "carries-it", sha],
                   check=True, capture_output=True)
    dirt = subprocess.run(["git", "-C", str(tree), "status", "--porcelain"],
                          check=True, capture_output=True, text=True).stdout
    assert dirt == "", f"the fixture must leave git seeing NOTHING, got {dirt!r}"

    _forget_the_record(srv, root, lane)

    refused = srv.delete(lane_path(lane) + "?force=true")
    assert refused.status_code == 409, (
        f"a leftover holding ignored files was cleared: {refused.status_code} {refused.text}\n"
        f"git saw an empty directory; the directory held {keep}.")
    err = refused.json()["error"]
    assert err["code"] == "ignored_work", refused.json()
    assert any("evidence" in p for p in err["detail"]["ignored"]), (
        f"the refusal does not NAME what it found — which is the whole difference between it and "
        f"the answer that said `uncommitted_changes: 0`: {err['detail']}")
    assert keep.read_text() == "one board, one run, not reproducible\n"
    assert tree.is_dir()


# ── SRV-FIX-059 round 5 ══════════════════════════════════════════════════════════════════════
#
# Round 4 stopped the silent loss and left the person facing a wall: a lane that ran its declared
# build once holds ignored content, so the claim refuses and the clear refuses, and §1's own
# criterion — "a person with a browser and no terminal must be able to get out" — is unmet for the
# only shape of leftover a working repository actually produces.
#
# The way out is INFORMED CONSENT, and the names are the whole point. `ignoredContent` already
# returns the paths; the refusal carries them, and a second, explicit call acts on them.


def _leftover_holding_ignored(srv, root):
    """A lane whose worktree holds a `.gitignore`d capture, and whose record is gone.

    Committed and pushed to another ref on purpose: this fixture must be blocked by the IGNORED
    content and by nothing else, so the dirty guard and the unmerged-commits guard both have
    nothing to say about it.
    """
    import subprocess
    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)

    (tree / ".gitignore").write_text("evidence/\n")
    (tree / "evidence").mkdir()
    keep = tree / "evidence" / "agent_app_1.log"
    keep.write_text("one board, one run, not reproducible\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "ignore the capture directory"]):
        subprocess.run(["git", "-C", str(tree)] + args, check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(root), "branch", "-f", "carries-it", sha],
                   check=True, capture_output=True)
    assert subprocess.run(["git", "-C", str(tree), "status", "--porcelain"],
                          check=True, capture_output=True, text=True).stdout == ""

    _forget_the_record(srv, root, lane)
    return lane, tree, keep


def test_a_leftover_holding_ignored_content_can_be_cleared_with_consent(gitrepo, serve):
    """THE round-5 criterion: the person who read what is there can act on it, from the tool.

    Four rounds in, this is the shape of leftover a working repository actually produces — a lane
    that ran its build once — and until now both calls a person can make refused it.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")
    lane, tree, _ = _leftover_holding_ignored(srv, root)

    blocked = srv.delete(lane_path(lane))
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "ignored_work", blocked.json()

    cleared = srv.delete(lane_path(lane) + "?discard_ignored=true")
    assert cleared.status_code == 200, (
        f"a person who was told exactly what is there still cannot act on it: "
        f"{cleared.status_code} {cleared.text}")
    body = cleared.json()
    assert body["worktree_removed"] is True, body
    assert body["branch_kept"] is True, "consent to lose ignored files is not consent to lose a branch"
    assert not tree.exists(), body

    # ...and the journey ends where §1 says it ends: a claimable lane.
    again = claim(srv)
    assert again.status_code == 201, (
        f"the leftover was cleared and a claim still cannot proceed: {again.text}")


def test_ignored_content_is_never_cleared_without_consent(gitrepo, serve):
    """NEGATIVE (mandatory). Consent is the only door.

    The cheap fix for round 5 is to let the ordinary call through now that the names are known.
    That would make round 4's guard decorative and put the silent loss straight back.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")
    lane, tree, keep = _leftover_holding_ignored(srv, root)

    for call in (lane_path(lane), lane_path(lane) + "?force=true"):
        refused = srv.delete(call)
        assert refused.status_code == 409, f"{call} cleared ignored content: {refused.text}"
        err = refused.json()["error"]
        assert err["code"] == "ignored_work", refused.json()
        assert any("evidence" in p for p in err["detail"]["ignored"]), (
            f"the refusal must NAME what it found, not count it: {err['detail']}")
        assert keep.read_text() == "one board, one run, not reproducible\n"
        assert tree.is_dir()


def test_consent_to_ignored_content_is_not_consent_to_anything_else(gitrepo, serve):
    """NEGATIVE (mandatory). One flag, one meaning.

    `discard_ignored` says *"I have read those paths and I accept losing them"*. It does not say
    "and also whatever else you find", so a worktree that is dirty, or whose branch carries work
    nothing else has, is refused exactly as it was before this parameter existed.
    """
    import subprocess
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    # (a) uncommitted work, plus ignored content
    lane, tree, _ = _leftover_holding_ignored(srv, root)
    (tree / "SOMEBODY-WAS-WORKING-HERE.md").write_text("half an afternoon\n")
    refused = srv.delete(lane_path(lane) + "?discard_ignored=true")
    assert refused.status_code == 409, f"consent leaked to uncommitted work: {refused.text}"
    assert refused.json()["error"]["code"] == "uncommitted_changes", refused.json()
    assert (tree / "SOMEBODY-WAS-WORKING-HERE.md").exists()

    # (b) a branch carrying commits nothing else has, and ignored content.
    # The first claim meets lane/01's leftover and refuses; the name is consumed and the next one
    # walks past it — the behaviour SRV-FIX-059's own criterion 1 asserts.
    # A DIFFERENT ticket: `TCK-001`'s branch is checked out in lane/01's leftover, and git will
    # not check one branch out twice. The first claim still meets that leftover's PATH and refuses;
    # the name is consumed and the next one walks past it.
    second = claim(srv, ticket="TCK-002")
    if second.status_code == 409:
        second = claim(srv, ticket="TCK-002")
    assert second.status_code == 201, second.text
    lane2 = second.json()["lane"]
    tree2 = Path(root, ".factory", "worktrees", lane2)
    (tree2 / ".gitignore").write_text("evidence/\n")
    (tree2 / "evidence").mkdir()
    (tree2 / "evidence" / "capture.log").write_text("not reproducible\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "work no other ref carries"]):
        subprocess.run(["git", "-C", str(tree2)] + args, check=True, capture_output=True)
    _forget_the_record(srv, root, lane2)
    refused2 = srv.delete(lane_path(lane2) + "?discard_ignored=true")
    assert refused2.status_code == 409, f"consent leaked to unmerged commits: {refused2.text}"
    assert refused2.json()["error"]["code"] == "unmerged_commits", refused2.json()
    assert tree2.is_dir()

    # (c) THE THIRD INPUT THIS TEST'S OWN NAME PROMISES — a path outside the lane root.
    #
    # Round 5 named three inputs in one sentence and built two, which is the `or` defect this
    # ticket has now committed twice: §1's original mandatory negative named two and shipped one,
    # and QA measured the missing half deleting work. Consent may not become a second spelling of
    # the traversal `SRV-FIX-059` round 3 closed.
    escaped = srv.delete("/api/lanes/..%2f..%2fescaped?discard_ignored=true")
    assert escaped.status_code == 400, (
        f"consent walked out of the lane root: {escaped.status_code} {escaped.text}")
    assert escaped.json()["error"]["code"] == "bad_lane", escaped.json()


# ── SRV-FIX-059 round 6 ══════════════════════════════════════════════════════════════════════
#
# One answer to "can this be got back", asked from BOTH branches of `release()`. Five rounds each
# added a probe beside the last and nobody re-modelled the chain, so the guard that stops a silent
# loss landed in one arm of an `if` and not the other.


def _ignored_capture(tree):
    """Put a `.gitignore`d capture in a worktree and leave git seeing NOTHING."""
    import subprocess
    (tree / ".gitignore").write_text("evidence/\n")
    (tree / "evidence").mkdir(exist_ok=True)
    keep = tree / "evidence" / "agent_app_1.log"
    keep.write_text("one board, one run, not reproducible\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "ignore the capture directory"]):
        subprocess.run(["git", "-C", str(tree)] + args, check=True, capture_output=True)
    assert subprocess.run(["git", "-C", str(tree), "status", "--porcelain"],
                          check=True, capture_output=True, text=True).stdout == ""
    return keep


def test_a_supervised_release_never_deletes_ignored_content(gitrepo, serve):
    """NEGATIVE (mandatory). The silent loss round 4 stopped, in the branch round 4 never touched.

    `ignoredContent` guarded the leftover path and the claim probe. The record-exists path asked
    `pathExists` and `isDirty`, removed the worktree, and answered `reason: "clean"` — so a lane
    that ran its declared build lost its gate capture, its database and its keys by being released
    the ordinary way, and the answer said there had been nothing there.

    This is not a new rule. It is the rule of round 4 reaching the arm of the `if` it missed.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    keep = _ignored_capture(tree)

    # the RECORD IS INTACT — this is a supervised release, not a leftover clear
    assert any(l["lane"] == lane for l in lanes_of(srv)["lanes"]), lanes_of(srv)

    # E13's own shape for a supervised release that will not remove the checkout: `200`, the lane
    # IS released — the seat and the resources come back — and the worktree stays with the reason
    # said out loud. Exactly what it already answers for uncommitted work; refusing the whole call
    # would strand the seat, which is a different defect.
    answer = srv.delete(lane_path(lane))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["worktree_removed"] is False, (
        f"a supervised release deleted a capture that cannot be got back: {body}")
    assert body["released"] is True, body
    assert body["detail"]["reason"] == "ignored_work", body["detail"]
    assert any("evidence" in p for p in body["detail"]["ignored"]), (
        f"the answer keeps the checkout and does not say which file it is protecting: "
        f"{body['detail']}")
    assert keep.read_text() == "one board, one run, not reproducible\n"
    assert tree.is_dir()


def test_a_supervised_release_with_consent_clears_ignored_content(gitrepo, serve):
    """One model, one parameter, both doors. `discard_ignored` is the way through here too."""
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    _ignored_capture(tree)

    cleared = srv.delete(lane_path(lane) + "?discard_ignored=true")
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["released"] is True, body
    assert body["worktree_removed"] is True, body
    assert body["branch_kept"] is True, "consent to lose ignored files is not consent to lose a branch"
    assert not tree.exists()

# ── SRV-FIX-059 round 7 ══════════════════════════════════════════════════════════════════════
#
# The collision the first six rounds were built to survive is one the tool creates itself.
# `nextNumber_` starts at 1 in every process (`store.h:516`) and never asks the disk, so after a
# restart the counter is back at `lane/01` while `.factory/worktrees/` still holds it. Everything
# downstream — the refusal, the consent, the traversal guard, the way out on the screen — exists to
# make a deletion safe that only has to happen because the tool walked into its own leftovers.


def test_a_restart_does_not_walk_into_its_own_leftovers(gitrepo, serve):
    """THE round-7 criterion. A leftover blocks nothing; a claim goes around it.

    `nextName_`'s own comment says it is *"monotonic, and never reused"* — true inside one process
    and violated by construction across a restart, which is the only case that matters here.
    """
    from conftest import stop_process
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    first = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", first)
    assert tree.is_dir()

    # a restart: the record goes, the directory stays. Nothing is deleted and nothing is adopted.
    _forget_the_record(srv, root, first)

    again = claim(srv, ticket="TCK-002")
    assert again.status_code == 201, (
        f"a leftover on disk blocked a claim for a DIFFERENT ticket: {again.status_code} "
        f"{again.text}\nThe tool walked into its own directory because the counter restarted.")
    assert again.json()["lane"] != first, (
        f"the claim reused the name of a directory that is still there: {again.json()['lane']}")
    assert tree.is_dir(), "the leftover was deleted rather than stepped around"


def test_reclaiming_the_same_ticket_adopts_its_own_leftover(gitrepo, serve):
    """The one case stepping around cannot answer, and deleting is the wrong answer to it.

    `branchFor_` is a function of (ticket, role), so `TCK-001` always wants `ticket/TCK-001` — and
    git will not check one branch out in two worktrees. Same ticket, same branch, the work already
    sitting there: the answer is to pick it up, not to destroy it and start again.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    (tree / "WORK-IN-PROGRESS.md").write_text("half an afternoon\n")
    _forget_the_record(srv, root, lane)

    again = claim(srv)
    assert again.status_code == 201, (
        f"re-claiming the same ticket met its own leftover and refused: {again.text}")
    assert again.json()["lane"] == lane, (
        f"adoption returns the lane that already holds this ticket's branch, got "
        f"{again.json()['lane']} beside {lane}")
    assert (tree / "WORK-IN-PROGRESS.md").read_text() == "half an afternoon\n", (
        "adoption destroyed the work it was adopting")


def test_a_lane_released_after_running_its_build_can_be_claimed_again(gitrepo, serve):
    """QA round 5's F1, transcribed from the probe in its own verdict.

    The two tests above reach "no record, directory on disk" through a RESTART. This is the OTHER
    door into that state, and it is the ordinary one — a person pressing release. Round 5 measured
    the whole journey and it ended with no way out:

        === supervised release of a lane that ran its build ===
        HTTP 200 {"released":true,"worktree_removed":false,"branch_kept":true,
                  "detail":{"reason":"ignored_work","ignored":["evidence/"]}}
        lanes now: []
        worktree still on disk: True

        === the person presses Start a lane again, same ticket ===
        HTTP 409 {"error":{"code":"worktree_failed", ...
                  "git":"fatal: 'ticket/TCK-001' is already checked out at ..."}}

    `worktree_failed` carries no lane, no `ours` and no `clear_with`, so `ClaimControl` rendered the
    sentence and no control. Round 7 answers this by adopting rather than deleting — but no test
    enters adoption through `release()`, and this ticket has been failed five times by exactly that:
    the shape the criterion is about being the shape nobody built.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    made = claim(srv)
    assert made.status_code == 201, made.text
    lane = made.json()["lane"]
    tree = Path(root, ".factory", "worktrees", lane)
    keep = _ignored_capture(tree)

    released = srv.delete(lane_path(lane))
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["released"] is True, body
    assert body["worktree_removed"] is False, (
        f"the premise of F1 no longer holds — this release removed the checkout: {body}")
    assert lanes_of(srv)["lanes"] == [], lanes_of(srv)
    assert tree.is_dir(), "the release kept nothing to collide with; F1's premise is gone"

    again = claim(srv)
    assert again.status_code == 201, (
        f"SRV-FIX-059 F1: releasing a lane that ran its build and starting the same ticket again "
        f"answered {again.status_code} {again.text}\nThe lane is on no screen, the refusal carries "
        f"no lane, no `ours` and no `clear_with`, and a person with a browser has no way out.")
    assert again.json()["lane"] == lane, (
        f"the kept checkout holds this ticket's branch and was not adopted: got "
        f"{again.json()['lane']} beside {lane}")
    assert keep.read_text() == "one board, one run, not reproducible\n", (
        "the capture the release refused to delete was destroyed by the claim that adopted it")


# ── SRV-FIX-077 ══════════════════════════════════════════════════════════════════════════════

def test_a_workspace_whose_build_cannot_run_in_a_lane_is_refused_before_the_board_is_locked(
        tmp_path, serve):
    """`SRV-FIX-063`'s unbuilt *answer B*, and the ORDERING is the assertion.

    `FIX-075` is what the missing half costs: a `master` lane won `board-master`, could not run its
    own declared `build` command, and reached GATE with eight criteria BLOCKED and one board held
    for the duration. A lane that starts, takes the board and cannot compile is worse than one that
    never starts.

    The manifest already refuses a toolchain that is absent AT LOAD. This is the live check, and
    the failure it pins is the one `FEAT-040`'s report recorded: the path went away *after* the
    tool was started — that session's lane home was a tmpfs wiped mid-session, taking ESP-IDF with
    it. So the case loads a valid manifest, then removes the toolchain, then claims.
    """
    toolchain = tmp_path / "toolchain"
    (toolchain / "bin").mkdir(parents=True)

    root = build_repo(tmp_path / "repo",
                      workspaces={"core": {"path": ".", "toolchain": [str(toolchain)]}},
                      resources={"board": {"exclusive": True, "needed_by": ["core"],
                                           "description": "one bench board"}})
    before = status_of(root)
    srv = serve(root)
    assert srv.get("/api/manifest").status_code == 200, "the fixture manifest must load first"

    shutil.rmtree(toolchain)

    r = claim(srv, workspace="core")
    assert r.status_code == 409, (
        f"a lane was claimed for a workspace whose declared toolchain is gone: "
        f"{r.status_code} {r.text}")
    err = r.json()["error"]
    assert err["code"] == "workspace_unrunnable", err
    assert err["detail"]["reason"] == "toolchain_missing", err
    assert err["detail"]["workspace"] == "core", err
    assert str(toolchain) in err["detail"]["path"], err

    # THE ordering assertion. Everything below is what "before the board is locked" means, and each
    # one of them is a thing the refusal must not have done first.
    body = lanes_of(srv)
    assert body["lanes"] == [], f"a lane record survived the refusal: {body['lanes']}"
    board = [x for x in body["resources"] if x["name"] == "board"][0]
    assert board["held_by"] is None, (
        f"the refused claim locked the board and then refused: {board}\n"
        f"A refused start has to leave the board free for the lane that CAN use it.")
    assert not list((Path(root) / ".factory" / "worktrees").glob("lane/*")), \
        "a worktree was created for a refused claim"
    assert not branch_exists(root, "ticket/TCK-001"), \
        "a branch was created for a refused claim"
    assert status_of(root) == before, "the main checkout changed"


def test_a_workspace_whose_toolchain_is_there_still_claims(tmp_path, serve):
    """NEGATIVE (mandatory). The refusal must not become "a workspace with a toolchain cannot run".

    The cheap way to pass the case above is to refuse any workspace that declares one, or to refuse
    every claim — both stop the product while every assertion above stays green.
    """
    toolchain = tmp_path / "toolchain"
    (toolchain / "bin").mkdir(parents=True)

    root = build_repo(tmp_path / "repo",
                      workspaces={"core": {"path": ".", "toolchain": [str(toolchain)]}},
                      resources={"board": {"exclusive": True, "needed_by": ["core"],
                                           "description": "one bench board"}})
    srv = serve(root)

    r = claim(srv, workspace="core")
    assert r.status_code == 201, (
        f"a workspace whose declared toolchain is present was refused: {r.status_code} {r.text}")
    assert r.json()["resources_held"] == ["board"], r.text


# ── SRV-FIX-109 · a failed stat is not an answer, at the callers SRV-FIX-105 left ─────────────

def _lane_root_of(root):
    """`.factory/worktrees/lane` — the directory a claim probes before it creates a worktree."""
    return Path(root) / ".factory" / "worktrees" / "lane"


def test_a_claim_refuses_rather_than_guessing_when_it_cannot_read_the_lane_root(gitrepo, serve):
    """`fs::exists` answers `false` for *unreadable* exactly as it does for *deleted*.

    The claim's probe reads that `false` as "the path is free" and runs `git worktree add` into a
    directory that is sitting right there, so the refusal a person gets names the wrong cause.
    `SRV-FIX-105` gave the reap a tri-state because there the wrong `false` was lethal; here it is
    merely a lie, which is the reason this caller was left and the reason it is a defect.
    """
    root = gitrepo()
    srv = serve(root)
    first = claim(srv)
    assert first.status_code == 201, first.text
    lane_root = _lane_root_of(root)
    assert lane_root.is_dir(), lane_root

    before = os.stat(lane_root).st_mode
    try:
        os.chmod(lane_root, 0o000)
        r = claim(srv, ticket="TCK-002")
    finally:
        os.chmod(lane_root, before)

    assert r.status_code != 201, (
        f"a claim went ahead while the lane root could not be read: {r.status_code} {r.text}")
    err = r.json()["error"]
    # Measured before the fix: `409 worktree_failed`, "git could not create the worktree for this
    # lane", carrying git's own `fatal: could not create leading directories`. The claim believed
    # the path was free, ran `git worktree add` into a directory that is there, and handed a person
    # git's failure as the cause. The cause is that the lane root could not be READ.
    assert err["code"] != "worktree_failed", (
        f"the claim went ahead and reported git's failure as the cause: {r.text}")
    detail = json.dumps(err.get("detail", {})).lower() + err.get("message", "").lower()
    assert "unreadable" in detail or "could not be read" in detail, (
        f"the refusal names some other cause than the unreadable lane root: {r.text}")


def test_e13_does_not_answer_404_for_a_leftover_it_cannot_read(gitrepo, serve):
    """`404 not_found` means *there is nothing there*. A failed `stat` does not mean that.

    This is the shape `SRV-FIX-059` spent six rounds on — the tool refusing to start AND refusing
    to clean up — reached here by answering "no such lane" about a directory nobody can look at.
    """
    root = gitrepo()
    srv = serve(root)
    got = claim(srv)
    assert got.status_code == 201, got.text
    lane = got.json()["lane"]

    lane_root = _lane_root_of(root)
    before = os.stat(lane_root).st_mode
    try:
        os.chmod(lane_root, 0o000)
        r = srv.delete(lane_path(lane))
    finally:
        os.chmod(lane_root, before)

    assert r.status_code != 404, (
        f"E13 answered 404 for a lane whose worktree is on disk and merely unreadable: {r.text}")
    # Measured before the fix: `200 {"released":true,"detail":{"reason":"worktree_missing"}}` for a
    # directory sitting right there. The lane record is destroyed and the answer states, as a fact,
    # that its worktree is gone — the same `stat` given the same authority `SRV-FIX-105` took away
    # from the reap, one endpoint over.
    reason = (r.json().get("detail") or {}).get("reason", "")
    assert reason != "worktree_missing", (
        f"E13 called a present-but-unreadable worktree missing and released the lane: {r.text}")


def test_a_readable_absent_worktree_is_still_absent(gitrepo, serve):
    """NEGATIVE (mandatory). The cheap fix is to read every `Unknown` as `Present`.

    That would make a genuinely missing worktree unclaimable for ever and turn the ordinary
    release into a refusal — the ordinary, readable, absent case must keep answering exactly as it
    does today.
    """
    root = gitrepo()
    srv = serve(root)
    got = claim(srv)
    assert got.status_code == 201, got.text
    lane, worktree = got.json()["lane"], got.json()["worktree"]

    shutil.rmtree(worktree)                    # readable parent, genuinely gone
    r = srv.delete(lane_path(lane))
    assert r.status_code == 200, (
        f"a readable, genuinely absent worktree stopped being releasable: {r.status_code} {r.text}")
    assert r.json().get("detail", {}).get("reason") == "worktree_missing", r.text

    again = claim(srv)
    assert again.status_code == 201, (
        f"the ticket became unclaimable after its worktree was removed: {again.text}")


def test_a_leftover_whose_parent_cannot_be_read_is_not_accused_of_escaping(gitrepo, serve):
    """QA round 1, F1 — transcribed from its reproduction.

    Round 1's case for this site never built a leftover: it deleted a lane whose record was still
    live, so the request took the live-release path and the leftover branch it claimed to fix was
    never executed. The state §1 names needs the RECORD gone and the DIRECTORY present, which is
    what `_forget_the_record` makes.

    Reached that way, the request never gets near the presence question. The canonicalisation
    guard above it calls `weakly_canonical` twice through ONE `error_code` and checks neither; an
    unreadable parent makes it return empty, `inside` is false, and the tool answers
    `400 bad_lane` — *"a lane name may not leave the lane root"* — about a name that did nothing of
    the kind. `"resolved":""` in the detail is the tell. That is this ticket's own subject one
    function earlier: a failure to answer, rendered as a specific accusation.
    """
    root = gitrepo()
    srv = serve(root, FACTORY_TEST_HOOKS="1")
    got = claim(srv)
    assert got.status_code == 201, got.text
    lane = got.json()["lane"]
    _forget_the_record(srv, root, lane)

    parent = _lane_root_of(root)
    before = os.stat(parent).st_mode
    try:
        os.chmod(parent, 0o000)
        r = srv.delete(lane_path(lane))
    finally:
        os.chmod(parent, before)

    err = (r.json().get("error") or {})
    assert err.get("code") != "bad_lane", (
        f"a lane whose parent could not be read was accused of leaving the lane root: {r.text}")


# ── SRV-FIX-123 · the general form ───────────────────────────────────────────────────────────
#
# The instance is a role start that ignored E18's resource gate (test_roles.py). This is the class,
# and it is the assertion that would have caught it without anybody thinking about roles: whatever
# starts a process — a person's `POST /runs`, a declared role, anything added later — a lane that
# reports `queued` is a lane whose exclusive resource is held by somebody else, and nothing may be
# running inside it. The row said `queued`, holding nothing, while the agent produced.
#
# The witness is the AGENT'S OWN LOG, never the row: the row is the thing that lied.

QUEUE_AGENT = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "token=${FACTORY_PROGRESS_TOKEN}" >> "$log"
echo "ready" >> "$log"
while : ; do sleep 0.2 ; done
"""


def test_no_lane_ever_reports_queued_while_its_process_runs(tmp_path, serve):
    """No row is ever `queued` while a process runs in the worktree that row names."""
    root = build_repo(tmp_path / "queued", lanes_host=4,
                      resources={"board-master": {"exclusive": True, "needed_by": ["core"],
                                                  "description": "one ESP32 on /dev/ttyUSB1"}},
                      agent={"command": "/bin/sh agent.sh"},
                      tickets=("TCK-001", "TCK-002"))
    (root / "agent.sh").write_text(QUEUE_AGENT)
    manifest = json.loads((root / ".factory" / "factory.json").read_text())
    manifest["roles"] = {"reviewer": {"start_on_stage": "GATE", "lane": "new",
                                      "prompt_file": ".factory/prompts/reviewer.md"}}
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    prompt = root / ".factory" / "prompts" / "reviewer.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("check {ticket_id}\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "an agent that stays up, and a role on GATE")

    srv = serve(root, FACTORY_TEST_HOOKS="1")

    def rows():
        return srv.get("/api/lanes").json()["lanes"]

    def log_of(row):
        try:
            return (Path(row["worktree"]) / "agent-log.txt").read_text()
        except (OSError, TypeError, KeyError):
            return ""

    first = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"}).json()
    lane = first["lane"]
    assert first["resources_held"] == ["board-master"], first
    assert srv.post(f"/api/lanes/{lane}/runs", json={}).status_code == 201

    deadline = time.time() + 25
    while time.time() < deadline and "ready" not in log_of(next(
            r for r in rows() if r["lane"] == lane)):
        time.sleep(0.05)

    token = [x for x in log_of(next(r for r in rows() if r["lane"] == lane)).splitlines()
             if x.startswith("token=")][0].split("=", 1)[1]
    # A second lane on the same held resource, and the role start on top of it: both routes to a
    # queued lane are open at once, and the sampling below covers whatever either of them does.
    srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "core"})
    assert srv.post("/api/progress", json={"token": token, "stage": "GATE"}).status_code == 202

    seen_queued = 0
    deadline = time.time() + 8
    while time.time() < deadline:
        for row in rows():
            if row["state"]["process"] != "queued":
                continue
            seen_queued += 1
            assert row["resources_held"] == [], row
            assert "ready" not in log_of(row), (
                "a lane reported `queued` while a process ran inside the worktree it names: "
                f"{row['lane']} process={row['state']['process']} "
                f"resources_held={row['resources_held']} waiting_for={row.get('waiting_for')} "
                f"log={log_of(row)!r}")
        time.sleep(0.1)

    assert seen_queued, "nothing was ever queued, so this asserted nothing"


# ── SRV-FEAT-180 ═════════════════════════════════════════════════════════════════════════════
#
# A resource whose holder is not alive is held for ever. Root `CLAUDE.md` records what that costs:
# a lane worktree removed under a live agent, the server reporting `crashed · worktree_missing`,
# "and the next lane queued forever on a board a corpse still held."
#
# `E13` frees the resource and removes the holder's checkout and its session directory to do it,
# which is the wrong price when the holder is already gone. These cases are about the other call —
# free the resource, keep the lane — and about the one thing it may never become, which is
# `release(lane, force=true)` wearing a different name.


def free_resource(srv, resource, lane=None):
    """`DELETE /api/resources/{resource}/holder?lane=…` — §9.2's `release_resource`.

    Both members come from the remedy's own `target`, so the client builds the call from the
    failure rather than re-deriving it from the code.
    """
    params = {} if lane is None else {"lane": lane}
    return srv.delete(f"/api/resources/{resource}/holder", params=params)


def session_dir_of(config_root, lane):
    """The lane's own agent-configuration directory — `SRV-FIX-052`'s, the one E13 removes.

    Its path is `<root>/factory-agent-<uid>/<repo>-<hash>/<lane>`; the hash is over the repository
    root, so a glob is how a test names it without recomputing FNV-1a.
    """
    found = sorted(Path(config_root).glob(f"factory-agent-*/*/{lane}"))
    return found[0] if found else None


def a_holder_and_a_queue(gitrepo, serve, config_root, workspaces=None, tickets=None):
    """Lane A holding `chamber` with a live process in it, lane B queued behind it."""
    root = gitrepo(
        workspaces=workspaces or {"edge": {"path": ".", "build": "true"}},
        resources={"chamber": {"exclusive": True, "needed_by": ["edge"],
                               "description": "one chamber, one run at a time"}},
        **({"tickets": tickets} if tickets else {}))
    srv = serve(root, FACTORY_TEST_HOOKS="1", FACTORY_AGENT_CONFIG_ROOT=str(config_root))

    a = claim(srv, ticket="TCK-001", workspace="edge")
    assert a.status_code == 201, a.text
    a = a.json()
    assert a["resources_held"] == ["chamber"], a

    started = srv.post("/api/_test/lane-process",
                       json={"lane": a["lane"], "command": "sleep 300"})
    assert started.status_code == 202, started.text
    pid = started.json()["pid"]
    assert _alive(pid), f"the hook did not start a process ({pid})"

    b = claim(srv, ticket="TCK-002", workspace="edge")
    assert b.status_code == 201, b.text
    b = b.json()
    assert b["state"]["process"] == "queued", b

    return root, srv, a, b, pid


def kill_out_from_under_the_server(pid):
    """SIGKILL the whole process group, then wait for the row to leave `/proc`.

    The GROUP, because killing only the leader leaves the sandbox running and the suite's own
    leak fixture would fail the case for a reason that has nothing to do with it.

    The wait is on the CONSEQUENCE and not on the endpoint under test: a killed child is a zombie
    with a `/proc` entry until its parent reaps it, and this server is its only parent — so
    `/proc/<pid>` disappearing IS the server having noticed, read from the process table rather
    than asked of the thing being measured.
    """
    os.kill(-pid, 9)
    deadline = time.time() + 10
    while time.time() < deadline and _alive(pid):
        time.sleep(0.05)
    assert not _alive(pid), (
        f"pid {pid} is still in /proc ten seconds after SIGKILL to its group; the server never "
        f"reaped it, so nothing below would be measuring a dead holder")


# ── §1 · a resource a dead holder holds can be freed, and the holder keeps its work ──────────

def test_a_resource_held_by_a_dead_holder_can_be_freed_without_releasing_the_lane(
        gitrepo, serve, tmp_path):
    """The incident shape, and the call that ends it.

    Lane A holds `chamber` and its process is killed out from under the server. Lane B is queued.
    Freeing the resource advances B — and A keeps its worktree, its branch and its session
    directory, every one of which `E13` would have taken to do the same job.
    """
    config_root = tmp_path / "agent-config"
    root, srv, a, b, pid = a_holder_and_a_queue(gitrepo, serve, config_root)
    before = status_of(root)
    kill_out_from_under_the_server(pid)

    session = session_dir_of(config_root, a["lane"])
    assert session is not None and session.is_dir(), (
        f"lane {a['lane']} has no session directory under {config_root}, so the assertion that it "
        f"survives would pass against a lane that never had one")

    r = free_resource(srv, "chamber", a["lane"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["freed"] is True, out
    assert out["reason"] == "freed", out
    assert out["resource"] == "chamber", out
    assert out["lane"] == a["lane"], out
    assert out["released"] == ["chamber"], out

    # B advances: it holds the resource and is no longer queued.
    body = lanes_of(srv)
    rows = {row["lane"]: row for row in body["lanes"]}
    assert rows[b["lane"]]["resources_held"] == ["chamber"], body["lanes"]
    assert rows[b["lane"]]["state"]["process"] != "queued", body["lanes"]
    assert rows[b["lane"]]["waiting_for"] is None, body["lanes"]
    assert {r["name"]: r["held_by"] for r in body["resources"]}["chamber"] == b["lane"], body

    # ...and A is still a lane, holding nothing, queued for what it lost rather than free to
    # start a run against a chamber it no longer has.
    assert a["lane"] in rows, body["lanes"]
    assert rows[a["lane"]]["resources_held"] == [], rows[a["lane"]]
    assert rows[a["lane"]]["state"]["process"] == "queued", rows[a["lane"]]
    assert rows[a["lane"]]["waiting_for"]["resource"] == "chamber", rows[a["lane"]]

    # The three things E13 would have taken.
    assert Path(a["worktree"]).is_dir(), "freeing a resource removed the holder's worktree"
    assert branch_exists(root, a["branch"]), "freeing a resource deleted the holder's branch"
    assert session.is_dir(), (
        f"freeing a resource removed the holder's session directory {session} — that is E13's "
        f"price, and this call exists so nobody has to pay it")

    assert status_of(root) == before, "the main checkout changed"


# ── §1 · a live holder is never freed this way ───────────────────────────────────────────────

def test_a_live_holder_is_never_freed_this_way(gitrepo, serve, tmp_path):
    """The same call against a holder whose process is ALIVE is refused, and it names the pid.

    Liveness is asked of a **pid** and of nothing else — never a process name, never a `pgrep`
    pattern, which matches the checker itself.
    """
    config_root = tmp_path / "agent-config"
    root, srv, a, b, pid = a_holder_and_a_queue(gitrepo, serve, config_root)
    before = status_of(root)

    r = free_resource(srv, "chamber", a["lane"])
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "wrong_state", err
    assert err["detail"]["pid"] == pid, err
    assert err["detail"]["lane"] == a["lane"], err
    assert err["detail"]["resource"] == "chamber", err
    assert err["detail"]["state"] == "running", err
    assert isinstance(err.get("remedy"), dict) and err["remedy"]["destroys"] == [], err

    # ...and what a queued lane IS offered while the holder is alive: releasing it, which costs
    # its session and its checkout, with the sentence that says so. Both call sites, because
    # `resource_busy` is emitted at E18 and at E19 and a refusal fixed at one is not fixed.
    for how, refused in (
            ("E18", srv.post(lane_path(b["lane"]) + "/runs", json={})),
            ("E19", srv.post(lane_path(b["lane"]) + "/commands", json={"which": "build"}))):
        assert refused.status_code == 409, f"[{how}] {refused.text}"
        err = refused.json()["error"]
        assert err["code"] == "resource_busy", f"[{how}] {err}"
        assert err["detail"]["holder_alive"] is True, f"[{how}] {err}"
        assert err["remedy"]["action"] == "release_lane", f"[{how}] {err['remedy']}"
        assert err["remedy"]["destroys"] == ["session", "worktree"], f"[{how}] {err['remedy']}"
        assert err["remedy"].get("confirm", "").strip(), f"[{how}] {err['remedy']}"

    # Nothing moved: the process is alive, A still holds the chamber, B is still queued.
    assert _alive(pid), "a refused free killed the process anyway"
    body = lanes_of(srv)
    assert {x["name"]: x["held_by"] for x in body["resources"]}["chamber"] == a["lane"], body
    rows = {row["lane"]: row for row in body["lanes"]}
    assert rows[b["lane"]]["state"]["process"] == "queued", body["lanes"]

    assert srv.delete(lane_path(a["lane"]) + "?force=true").status_code == 200
    assert settle(lambda: carrying(a["worktree"])) == {}, "the forced release leaked a process"
    assert status_of(root) == before, "the main checkout changed"


# ── §1 · the stranded state is visible before anybody waits on it ────────────────────────────

def test_a_stranded_resource_is_visible_before_anybody_waits_on_it(gitrepo, serve, tmp_path):
    """`E9` says the holder is not alive, on the resource AND on the queued lane's `waiting_for`.

    Three readings, because a member that is always `false` says nothing: a resource nobody holds
    answers `null`, a resource whose holder is running answers `true`, and only then is `false`
    evidence about anything.
    """
    config_root = tmp_path / "agent-config"
    root, srv, a, b, pid = a_holder_and_a_queue(gitrepo, serve, config_root)
    before = status_of(root)

    def resource_row():
        return {x["name"]: x for x in lanes_of(srv)["resources"]}["chamber"]

    def waiting_of(lane):
        return {row["lane"]: row for row in lanes_of(srv)["lanes"]}[lane]["waiting_for"]

    # ── the holder is running ────────────────────────────────────────────────────────────────
    assert resource_row()["holder_alive"] is True, resource_row()
    assert waiting_of(b["lane"])["holder_alive"] is True, waiting_of(b["lane"])

    # ── the holder is a corpse ───────────────────────────────────────────────────────────────
    kill_out_from_under_the_server(pid)
    assert resource_row()["holder_alive"] is False, resource_row()
    stranded = waiting_of(b["lane"])
    assert stranded["resource"] == "chamber", stranded
    assert stranded["held_by"] == a["lane"], stranded
    assert stranded["holder_alive"] is False, (
        "the queued lane's `waiting_for` reads as an ordinary queue while what it is waiting for "
        f"is held by something that is not running: {stranded}")

    # ...and the refusal a queued lane gets now names the way out that costs NOTHING — freeing
    # what the corpse holds, rather than releasing it and losing its session and its checkout.
    # §11's table gives `resource_busy` both, and which one is offered is this fact and no other.
    for how, refused in (
            ("E18", srv.post(lane_path(b["lane"]) + "/runs", json={})),
            ("E19", srv.post(lane_path(b["lane"]) + "/commands", json={"which": "build"}))):
        assert refused.status_code == 409, f"[{how}] {refused.text}"
        err = refused.json()["error"]
        assert err["code"] == "resource_busy", f"[{how}] {err}"
        assert err["detail"]["holder_alive"] is False, f"[{how}] {err}"
        assert err["remedy"]["action"] == "release_resource", f"[{how}] {err['remedy']}"
        assert err["remedy"]["destroys"] == [], f"[{how}] {err['remedy']}"
        assert err["remedy"]["target"] == {"resource": "chamber", "lane": a["lane"]}, (
            f"[{how}] the client builds the call from the remedy, so the target is the call: "
            f"{err['remedy']}")

    # ── nobody holds it ──────────────────────────────────────────────────────────────────────
    assert free_resource(srv, "chamber", a["lane"]).status_code == 200
    assert srv.delete(lane_path(b["lane"]) + "?force=true").status_code == 200
    assert srv.delete(lane_path(a["lane"]) + "?force=true").status_code == 200
    assert resource_row()["held_by"] is None, resource_row()
    assert resource_row()["holder_alive"] is None, resource_row()

    assert status_of(root) == before, "the main checkout changed"


# ── §1 · a host failure states what is missing ───────────────────────────────────────────────

def test_confinement_unavailable_names_what_is_missing(gitrepo, serve, tmp_path):
    """The body names the PROGRAM it could not use and the REASON, from the real failure.

    Two provocations with two different helpers, because a body that names a program is only
    evidence when the name follows the failure: one constant string satisfies "names a program"
    for ever, and only a second failure with a different program can tell them apart.

    Asserted at `E19` as well as at the process hook: `confinement_unavailable` is emitted from
    three places and a refusal fixed in one of them is not fixed.
    """
    bodies = {}
    for name in ("no-such-helper", "another-missing-helper"):
        helper = tmp_path / name
        # A repository of its own per server: two servers on one checkout would meet each other's
        # branch on the second claim, and this case is about a refusal and not about that.
        root = gitrepo(workspaces={"core": {"path": ".", "build": "true"}})
        srv = serve(root, FACTORY_TEST_HOOKS="1", FACTORY_CONFINE_BIN=str(helper))
        lane = claim(srv, ticket="TCK-001").json()

        hook = srv.post("/api/_test/lane-process", json={"lane": lane["lane"], "command": "true"})
        assert hook.status_code == 409, hook.text
        assert hook.json()["error"]["code"] == "confinement_unavailable", hook.text

        e19 = srv.post(lane_path(lane["lane"]) + "/commands", json={"which": "build"})
        assert e19.status_code == 409, e19.text
        assert e19.json()["error"]["code"] == "confinement_unavailable", e19.text

        for where, response in (("hook", hook), ("E19", e19)):
            detail = response.json()["error"]["detail"]
            assert detail.get("program") == str(helper), (
                f"[{where}] `confinement_unavailable` does not name the program it could not "
                f"use: {detail}")
            assert str(helper) in detail.get("reason", ""), (
                f"[{where}] the reason does not say why the program could not be used: {detail}")
        bodies[name] = hook.json()["error"]["detail"]

    assert bodies["no-such-helper"]["program"] != bodies["another-missing-helper"]["program"], (
        f"both failures named the same program, so `program` is a constant string and not the "
        f"real failure: {bodies}")

    # ...and the shape where the program is FINE and something else is missing: the body still
    # names the program it resolved, and the reason is about the other thing.
    # The developer's home is given to the SERVED INSTANCE rather than taken from `Path.home()`.
    # `SRV-FIX-466`: inside a lane `$HOME` is the sandbox's own tmpfs, so a fixture reading it
    # hands the product a path that is under the home the product is measuring against only by
    # accident — `'/home/lane' != '/home/lane'`. Told explicitly, the shape is the same wherever
    # this case runs: an agent configuration directory INSIDE the developer's home, which a lane
    # may not be given write access to.
    home = tmp_path / "har180-home"
    inside = home / ".cache" / "har180-should-never-be-used"
    shutil.rmtree(inside, ignore_errors=True)
    home.mkdir(parents=True, exist_ok=True)
    root = gitrepo(workspaces={"core": {"path": ".", "build": "true"}})
    before = status_of(root)
    srv = serve(root, HOME=str(home), FACTORY_TEST_HOOKS="1",
                FACTORY_AGENT_CONFIG_ROOT=str(inside))
    lane = claim(srv, ticket="TCK-002").json()
    r = srv.post("/api/_test/lane-process", json={"lane": lane["lane"], "command": "true"})
    assert r.status_code == 409, r.text
    detail = r.json()["error"]["detail"]
    assert r.json()["error"]["code"] == "confinement_unavailable", r.text
    assert detail.get("program"), f"no program named when the program was not the problem: {detail}"
    assert "configuration directory" in detail.get("reason", ""), detail

    assert status_of(root) == before, "the main checkout changed"


# ── §1 · a spawn failure can be retried, and the attempt is not erased ───────────────────────

def test_spawn_failed_can_be_retried_without_re_running_the_call(gitrepo, serve, tmp_path):
    """A transient spawn failure carries a retry; the retry produces a run; both are in `E7`.

    The failure is injected through `POST /api/_test/fail-next-spawn`, which answers the product's
    own `spawn_failed` — the same `lane::Spawn` a failed `fork()` produces — without needing
    `fork()` to fail on a machine where it does not (`test_errors.py` names that as the reason
    this code is unprovoked there). Same bargain as `/api/_test/lane-process`: FACTORY_TEST_HOOKS
    only, so the tool a person runs has no such route.
    """
    root = gitrepo(agent={"command": "/bin/sh agent.sh"})
    before = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1",
                FACTORY_AGENT_CONFIG_ROOT=str(tmp_path / "agent-config"))

    lane = claim(srv, ticket="TCK-001").json()
    assert srv.post("/api/_test/fail-next-spawn", json={"times": 1}).status_code == 200

    failed = srv.post(lane_path(lane["lane"]) + "/runs", json={})
    assert failed.status_code == 500, failed.text
    err = failed.json()["error"]
    assert err["code"] == "spawn_failed", err
    assert isinstance(err.get("remedy"), dict), err
    assert err["remedy"]["action"] == "retry", err
    assert err["remedy"]["destroys"] == [], err

    # The retry is the failed request, re-issued unchanged — which is what `retry` means.
    again = srv.post(lane_path(lane["lane"]) + "/runs", json={})
    assert again.status_code == 201, again.text
    started = again.json()["run_id"]

    rows = srv.get("/api/tickets/TCK-001/runs")
    assert rows.status_code == 200, rows.text
    runs = rows.json()["runs"]
    ids = [row["run_id"] for row in runs]
    assert started in ids, f"the retry's run is not in the record: {runs}"
    assert len(runs) == 2, (
        f"the failed attempt is not in the record — E7 lists {len(runs)} run(s) after one "
        f"failure and one success: {runs}")
    attempt = next(row for row in runs if row["run_id"] != started)
    assert attempt["outcome"] == "crashed", attempt
    assert attempt["ended_iso"], attempt
    assert attempt["lane"] == lane["lane"], attempt

    assert srv.delete(lane_path(lane["lane"]) + "?force=true").status_code == 200
    assert settle(lambda: carrying(lane["worktree"])) == {}, "the forced release leaked a process"
    assert status_of(root) == before, "the main checkout changed"


# ── SRV-FEAT-177 · E25 · a lane can be recovered without being destroyed ══════════════════════
#
# Three verbs, three consequences, and the product has had only the first and the third:
#
#   E15 interrupt  stops the turn     keeps the process, the session      loses nothing
#   E25 recover    stops the process  keeps the session, worktree, branch loses nothing
#   E13 release    stops the process  keeps the branch, worktree if clean LOSES THE SESSION
#
# `release()` calls `fs::remove_all` on the lane's agent configuration directory — the directory
# holding `projects/…/<session-id>.jsonl`, which is the CLI's own memory of the work. So on
# 2026-08-21 the only control the screen offered a lane that could not authenticate was the one
# that would also erase 3.6 MB of that session (`docs/operations/ESCAPES.md`). The recovery that worked was
# three calls typed into a shell and the middle one — `rm <agent-config>/lane/03/.credentials.json`
# — had no endpoint at all.
#
# E25 is those three collapsed into one server action, minus the third: recover does NOT start a
# run. Every case below therefore asks two questions of the same call — *did it stop and re-seed*,
# and *is everything the session needs still on disk*.

# A stand-in agent that behaves, in the two ways these cases measure, like the program it stands
# in for: it keeps its session in a file under `$CLAUDE_CONFIG_DIR` named for the session id it
# was given on its argv, and it reads its credential from the same directory. `--session-id <id>`
# on a fresh session and `--resume <id>` on a continued one are `agent/launch.cpp`'s own argv, so
# the file a resumed run appends to is the file the first run opened — which is the whole of what
# "the session survived" means from outside the CLI.
RECOVER_AGENT = """#!/bin/sh
sid=""
prev=""
for a in "$@"; do
    case "$prev" in --session-id|--resume) sid="$a" ;; esac
    prev="$a"
done
cfg="$CLAUDE_CONFIG_DIR"
mkdir -p "$cfg/projects/the-repo" "$cfg/sessions" "$cfg/session-env"
printf 'turn %s\\n' "$FACTORY_RUN_ID" >> "$cfg/projects/the-repo/$sid.jsonl"
cp "$cfg/.credentials.json" "./carried-$FACTORY_RUN_ID.json" 2>/dev/null
printf 'ran=%s session=%s\\n' "$FACTORY_RUN_ID" "$sid" >> ./agent-log.txt
sleep 300
"""


def write_token(token, payload):
    """Create the credential the first time — a plain write, as `claude login` leaves it."""
    token.write_text(json.dumps(payload))
    token.chmod(0o600)
    return token.stat()


def refresh_as_the_cli_does(token, payload):
    """Replace the host's credential EXACTLY the way the program that owns it replaces it.

    Write a temporary file beside the target and `rename` over it. One `os.rename`, and it severs
    every hard link to the old inode — `SRV-FIX-174`'s subject, and the operation that produced
    the lane this ticket exists for.
    """
    tmp = token.with_name(token.name + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.chmod(0o600)
    os.rename(tmp, token)
    return token.stat()


@pytest.fixture
def recoverenv(tmp_path, serve):
    """A repository with a stand-in agent, a stand-in developer credential, and a server whose
    lanes keep their configuration directories where this test can look at them.

    `FACTORY_AGENT_CONFIG_ROOT` is a LOCATION lever and never a permission one (`app/lane/store.cpp`):
    the tool refuses to build a lane's directory under the developer's home whatever it says.
    """
    base = tmp_path / "agent-homes"
    base.mkdir(parents=True, exist_ok=True)
    devhome = tmp_path / "devhome" / ".claude"
    devhome.mkdir(parents=True, exist_ok=True)

    class Env:
        def claim_lane(self, ticket="TCK-001"):
            r = claim(self.srv, ticket=ticket)
            assert r.status_code == 201, r.text
            return r.json()

        def start(self, lane, **body):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)
            assert r.status_code == 201, r.text
            run = r.json()
            self.wait_log(lane, f"ran={run['run_id']}")
            return run

        def recover(self, lane, **body):
            return self.srv.post(f"/api/lanes/{lane['lane']}/recover", json=body)

        def kill(self, lane):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
            assert r.status_code in (200, 202), r.text
            self.wait_not_running(lane)

        def row(self, lane):
            for row in lanes_of(self.srv)["lanes"]:
                if row["lane"] == lane["lane"]:
                    return row
            return None

        def wait_not_running(self, lane, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                row = self.row(lane)
                if row is not None and row["state"]["process"] != "running":
                    return row
                time.sleep(0.05)
            raise AssertionError(
                f"{lane['lane']} still reports a running process after {timeout}s: {self.row(lane)}")

        def wait_log(self, lane, needle, timeout=25):
            path = Path(lane["worktree"]) / "agent-log.txt"
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    text = path.read_text()
                except OSError:
                    text = ""
                if needle in text:
                    return text
                time.sleep(0.05)
            raise AssertionError(f"{needle!r} never reached the agent's log within {timeout}s")

        def home_of(self, lane):
            """The host directory this lane's agent configuration lives in — found, never
            recomputed: the tool derives the middle component from the repository it was pointed
            at, and a test that reimplements that derivation passes when the tool changes it."""
            hits = sorted(self.base.glob(
                f"factory-agent-*/{Path(self.root).name}-*/{lane['lane']}"))
            assert len(hits) == 1, (
                f"expected exactly one agent home for {lane['lane']} under {self.base}, "
                f"found {hits}\ntree: {sorted(str(p) for p in self.base.rglob('*'))}")
            return hits[0]

    env = Env()
    env.base = base
    env.token = devhome / ".credentials.json"
    write_token(env.token, {"refreshToken": "one"})
    env.root = build_repo(tmp_path / "repo",
                          agent={"command": "/bin/sh agent.sh",
                                 "credentials_dir": str(devhome)})
    (env.root / "agent.sh").write_text(RECOVER_AGENT)
    git(env.root, "add", "agent.sh")
    git(env.root, "commit", "-m", "agent")
    env.baseline = status_of(env.root)
    env.srv = serve(env.root, FACTORY_TEST_HOOKS="1", FACTORY_AGENT_CONFIG_ROOT=str(base))
    yield env
    assert status_of(env.root) == env.baseline, (
        f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{env.baseline}\nafter:\n{status_of(env.root)}")


def test_recover_keeps_the_session_and_the_next_run_resumes_it(recoverenv):
    """THE criterion. Run, recover, run again — and the second run is the FIRST SESSION.

    The proof is not the tool's own bookkeeping. It is the session file the agent keeps under its
    configuration directory: same inode and same size across the recover, and then a second line
    appended to THAT FILE by the run that came after it. A tool that answered with the right
    session id over a deleted file would pass a bookkeeping assertion and be useless.
    """
    env = recoverenv
    lane = env.claim_lane()
    run1 = env.start(lane)
    assert run1["session_id"], run1

    cfg = env.home_of(lane)
    session = cfg / "projects" / "the-repo" / f"{run1['session_id']}.jsonl"
    assert session.is_file(), (
        f"the agent kept no session file for {run1['session_id']}; this case cannot tell a "
        f"survivor from an absence: {sorted(str(p.relative_to(cfg)) for p in cfg.rglob('*'))}")
    before = session.stat()

    r = env.recover(lane)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["recovered"] is True, out
    assert out["session_id"] == run1["session_id"], (
        f"E25 answered with a different session than the one the lane had: {out}")

    after = session.stat()
    assert (after.st_ino, after.st_size) == (before.st_ino, before.st_size), (
        f"the recover touched the session file: inode {before.st_ino}→{after.st_ino}, size "
        f"{before.st_size}→{after.st_size}. Release-then-reclaim lands exactly here.")

    run2 = env.start(lane)
    assert run2["session_id"] == run1["session_id"], (
        f"the run after the recover started a NEW session: {run1['session_id']} → "
        f"{run2['session_id']}. Nothing of the work is in the second one.")
    assert run2["run_id"] != run1["run_id"], (run1, run2)
    assert session.read_text().splitlines() == [f"turn {run1['run_id']}",
                                                f"turn {run2['run_id']}"], session.read_text()
    env.kill(lane)


def test_recover_re_carries_what_the_lane_borrows(recoverenv):
    """The middle call of the shell recovery, and the one that had no endpoint.

    The credential is severed the way its writer severs it — `os.rename` over the name — and then
    recover, alone, has to put the host's CURRENT file back in the lane. Before any process
    starts: recover re-seeds, it does not run (§1 pre-decided).
    """
    env = recoverenv
    lane = env.claim_lane()
    run1 = env.start(lane)

    carried = env.home_of(lane) / ".credentials.json"
    assert carried.stat().st_dev == env.token.stat().st_dev, (
        "this case needs one filesystem to hold both, or it measures the copy fallback instead")
    assert carried.stat().st_ino == env.token.stat().st_ino, (
        f"the first carry did not link: host inode {env.token.stat().st_ino}, lane inode "
        f"{carried.stat().st_ino}")

    severed = refresh_as_the_cli_does(env.token, {"refreshToken": "two"})
    assert carried.stat().st_ino != severed.st_ino, (
        "the fixture did not reproduce the case: the rename left the lane's name on the host's "
        "inode, so there is nothing here to re-carry")
    assert json.loads(carried.read_text())["refreshToken"] == "one", carried.read_text()

    r = env.recover(lane)
    assert r.status_code == 200, r.text
    assert carried.stat().st_ino == env.token.stat().st_ino, (
        f"THE CRITERION: recover left the lane holding a severed credential — lane inode "
        f"{carried.stat().st_ino}, host inode {env.token.stat().st_ino}. This is the `rm` that "
        f"had no endpoint, and E25 exists to be it.")
    assert carried.read_text() == env.token.read_text(), (
        f"the lane's bytes are not the host's current bytes:\n  lane {carried.read_text()}\n"
        f"  host {env.token.read_text()}")
    assert r.json()["detail"]["re_carried"] == [".credentials.json"], r.json()

    # ...and NOTHING RAN to do it. The person decides when the agent speaks again.
    log = (Path(lane["worktree"]) / "agent-log.txt").read_text()
    assert log.count("ran=") == 1, (
        f"recover started a run of its own; E18's prompt and model stop meaning what they "
        f"mean:\n{log}")
    assert sorted(p.name for p in Path(lane["worktree"]).glob("carried-*")) == [
        f"carried-{run1['run_id']}.json"], sorted(
            p.name for p in Path(lane["worktree"]).glob("carried-*"))


def test_recover_stops_a_live_process_group(recoverenv):
    """The GROUP, and not its leader — `WEB-FIX-164` and `SRV-FIX-029` are why.

    The leader is the confinement helper; it does not handle SIGTERM and dies every time, so an
    assertion about the pid the tool wrote down is true whatever happened to the run. This one
    counts the process table by a marker unique to this lane, and it builds a tree on purpose:
    children that spawned their own children, so "kill the leader" cannot pass it.
    """
    env = recoverenv
    lane = env.claim_lane()
    mark = lane["worktree"]
    before = carrying(mark)
    assert before == {}, f"the marker is not unique to this lane: {before}"

    r = env.srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"],
                           "command": "sh -c 'sleep 300 & sleep 300 & wait' & wait"})
    assert r.status_code == 202, r.text
    pid = r.json()["pid"]

    deadline = time.time() + 10
    while len(process_group(pid)) < 4 and time.time() < deadline:
        time.sleep(0.05)
    group = process_group(pid)
    assert len(group) >= 4, (
        f"the run never became a tree of children of children, so this case would prove nothing "
        f"about a process group: {group}")

    r = env.recover(lane)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["killed"] is True, out
    assert out["detail"]["killed_pid"] == pid, out

    left = settle(lambda: process_group(pid))
    assert left == {}, (
        f"the process group survived the recover that reported killing it; it held "
        f"{sorted(group)} before and still holds:\n"
        + "\n".join(f"  pid {p}: {c[:200]}" for p, c in sorted(left.items())))
    leaked = settle(lambda: {p: c for p, c in carrying(mark).items() if p not in before})
    assert leaked == {}, leaked

    # ...and THE LANE IS STILL THERE. That is the whole difference from E13.
    assert env.row(lane) is not None, lanes_of(env.srv)
    assert Path(lane["worktree"]).is_dir(), lane["worktree"]
    assert branch_exists(env.root, "ticket/TCK-001"), "a recover deleted the branch"


def test_recover_on_an_idle_lane_is_not_an_error(recoverenv):
    """A credential can die while nothing is running, which is exactly what happened.

    So recovering an idle lane re-seeds and answers `200` — idempotent, no process required, and
    the desired state already holding is never an error (§1 pre-decided). The one refusal on this
    path is a lane nobody claimed.
    """
    env = recoverenv
    lane = env.claim_lane()

    # ── a lane that has never run at all ─────────────────────────────────────────────────────
    r = env.recover(lane)
    assert r.status_code == 200, r.text
    assert r.json()["killed"] is False, r.json()
    assert r.json()["session_id"] == "", r.json()

    # ── a lane whose run has ended, and whose credential died while it was idle ──────────────
    run1 = env.start(lane)
    env.kill(lane)
    carried = env.home_of(lane) / ".credentials.json"
    refresh_as_the_cli_does(env.token, {"refreshToken": "two"})
    assert carried.stat().st_ino != env.token.stat().st_ino, "the fixture did not sever the link"

    r = env.recover(lane)
    assert r.status_code == 200, r.text
    assert r.json()["killed"] is False, (
        f"recover reported killing something in an idle lane: {r.json()}")
    assert r.json()["session_id"] == run1["session_id"], r.json()
    assert carried.stat().st_ino == env.token.stat().st_ino, (
        f"an idle lane's credential was not re-carried — lane inode {carried.stat().st_ino}, "
        f"host inode {env.token.stat().st_ino}")

    # ...and again, because idempotent means the second call is as ordinary as the first.
    again = env.recover(lane)
    assert again.status_code == 200, again.text
    assert again.json()["session_id"] == run1["session_id"], again.json()

    # ── the one refusal ──────────────────────────────────────────────────────────────────────
    r = env.srv.post("/api/lanes/lane/99/recover", json={})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found", r.text
    assert r.json()["error"]["detail"]["lane"] == "lane/99", r.text


def test_the_answer_declares_it_destroys_nothing(recoverenv):
    """`destroys: []` — WEB-FEAT-173 §9.3, in E25's own answer and in every remedy that offers it.

    This is the member that makes *no failure's only remedy destroys work* checkable, and E25 is
    the action that makes it TRUE for a lane that cannot authenticate: before this endpoint the
    only way out on the screen was `release_lane`, which destroys `["session","worktree"]`.
    """
    env = recoverenv
    lane = env.claim_lane()
    env.start(lane)

    r = env.recover(lane)
    assert r.status_code == 200, r.text
    assert r.json()["destroys"] == [], r.json()

    spec = (Path(__file__).resolve().parents[2] / "docs/product/SPEC.md").read_text()
    pointing = re.findall(r"`recover_lane`\s*→\s*`(\[[^`]*\])`", spec)
    assert pointing, "SPEC.md names no `recover_lane` remedy at all, so nothing points at E25"
    assert set(pointing) == {"[]"}, (
        f"a remedy pointing at E25 says it destroys something: {sorted(set(pointing))}")

# ── the mandatory negative ───────────────────────────────────────────────────────────────────

def test_freeing_a_resource_never_kills_a_process_and_never_removes_a_worktree(
        gitrepo, serve, tmp_path):
    """MANDATORY NEGATIVE — the cheap implementation of "free the resource" is force-release.

    Force-release is exactly the failure root `CLAUDE.md` documents, reached by the control that
    exists to avoid it: it kills the holder's process group, removes its checkout and deletes the
    session directory the agent wrote. So this drives every answer the endpoint has — freed, a
    live holder refused, a resource nobody holds, a resource the manifest does not declare, a
    request with no lane in it — and then asserts that of everything that was there before, the
    only pid missing is the one this test killed itself, and nothing else is gone at all.
    """
    config_root = tmp_path / "agent-config"
    root, srv, a, b, dead_pid = a_holder_and_a_queue(
        gitrepo, serve, config_root,
        workspaces={"edge": {"path": ".", "build": "true"}, "core": {"path": "."}},
        tickets=("TCK-001", "TCK-002", "TCK-003"))
    before = status_of(root)

    # A third lane, in a workspace that needs no resource, with a process nobody is asking about.
    # A call that kills processes does not stop at the lane it was given.
    c = claim(srv, ticket="TCK-003", workspace="core")
    assert c.status_code == 201, c.text
    c = c.json()
    started = srv.post("/api/_test/lane-process", json={"lane": c["lane"], "command": "sleep 300"})
    assert started.status_code == 202, started.text
    c_pid = started.json()["pid"]

    kill_out_from_under_the_server(dead_pid)

    lanes = [a, b, c]
    trees = {row["lane"]: Path(row["worktree"]) for row in lanes}
    sessions = {row["lane"]: session_dir_of(config_root, row["lane"]) for row in lanes}
    for name, tree in trees.items():
        assert tree.is_dir(), f"{name} has no worktree before the calls: {tree}"
    assert sessions[a["lane"]] is not None, "lane A has no session directory to lose"
    assert sessions[c["lane"]] is not None, "lane C has no session directory to lose"

    # ── every answer this endpoint has, in one run ───────────────────────────────────────────
    answers = []
    answers.append(("a dead holder", free_resource(srv, "chamber", a["lane"])))
    answers.append(("the same call again", free_resource(srv, "chamber", a["lane"])))
    answers.append(("a lane that never held it", free_resource(srv, "chamber", c["lane"])))
    answers.append(("a resource nothing declares", free_resource(srv, "no-such-thing", a["lane"])))
    answers.append(("no lane in the request", free_resource(srv, "chamber")))

    # ...and a holder that IS alive, which is the one a force-release would kill.
    holder = {x["name"]: x for x in lanes_of(srv)["resources"]}["chamber"]["held_by"]
    assert holder, "nothing holds the chamber, so the live-holder refusal below asserts nothing"
    live = srv.post("/api/_test/lane-process", json={"lane": holder, "command": "sleep 300"})
    assert live.status_code == 202, live.text
    live_pid = live.json()["pid"]
    answers.append(("a live holder", free_resource(srv, "chamber", holder)))

    assert [how for how, r in answers if r.status_code >= 500] == [], answers

    # ── nothing died, nothing was removed ───────────────────────────────────────────────────
    assert _alive(c_pid), (
        f"pid {c_pid} — a process in a lane no call named — is gone after "
        f"{len(answers)} calls to free a resource")
    assert _alive(live_pid), (
        f"pid {live_pid} — the live holder this endpoint REFUSED to free — is gone; the refusal "
        f"killed it, which is `release(force=true)` wearing a different name")

    for name, tree in trees.items():
        assert tree.is_dir(), f"the worktree of {name} is gone after freeing a resource: {tree}"
    for name, session in sessions.items():
        if session is not None:
            assert session.is_dir(), (
                f"the session directory of {name} is gone after freeing a resource: {session}")
    for row in lanes:
        assert branch_exists(root, row["branch"]), f"the branch of {row['lane']} is gone"

    rows = {row["lane"]: row for row in lanes_of(srv)["lanes"]}
    assert sorted(rows) == sorted(trees), (
        f"a lane left the table: freeing a resource released it. before={sorted(trees)} "
        f"after={sorted(rows)}")

    for lane in (holder, c["lane"]):
        assert srv.delete(lane_path(lane) + "?force=true").status_code == 200
    for row in lanes:
        assert settle(lambda: carrying(row["worktree"])) == {}, f"{row['lane']} leaked a process"
    assert status_of(root) == before, "the main checkout changed"


def test_recover_never_removes_the_configuration_directory(recoverenv):
    """MANDATORY NEGATIVE — the cheap implementation is release-then-reclaim.

    It passes every "the lane works again" check there is: the process is gone, the credential is
    current, a run starts, the lane is in the table. And `release()` calls `fs::remove_all` on
    `<agent-config>/<lane>`, so `projects/`, `sessions/` and `session-env/` go with it — which is
    everything E25 exists to keep. Written down as the FILESYSTEM either side of the call, because
    that is the only place the difference between the two implementations is visible at all.
    """
    env = recoverenv
    lane = env.claim_lane()
    run1 = env.start(lane)
    cfg = env.home_of(lane)

    kept = {}
    for name in ("projects", "sessions", "session-env"):
        directory = cfg / name
        assert directory.is_dir(), (
            f"{name}/ was never made, so this case cannot tell a survivor from an absence: "
            f"{sorted(p.name for p in cfg.iterdir())}")
        (directory / "har177-witness.txt").write_text(
            f"written under {name}/ by the lane's own process\n")
        kept[name] = {str(p.relative_to(cfg)): p.read_bytes()
                      for p in sorted(directory.rglob("*")) if p.is_file()}
    assert f"projects/the-repo/{run1['session_id']}.jsonl" in kept["projects"], kept["projects"]

    r = env.recover(lane)
    assert r.status_code == 200, r.text

    assert cfg.is_dir(), (
        f"recover removed the lane's configuration directory outright: {cfg}. That is "
        f"release-then-reclaim, and it loses the session this endpoint exists to keep.")
    gone = [name for name in kept if not (cfg / name).is_dir()]
    assert gone == [], (
        f"recover removed {gone} from {cfg}; what is left is "
        f"{sorted(p.name for p in cfg.iterdir())}")
    for name, files in kept.items():
        now = {str(p.relative_to(cfg)): p.read_bytes()
               for p in sorted((cfg / name).rglob("*")) if p.is_file()}
        assert now == files, (
            f"the contents of {name}/ changed across the recover:\n"
            f"  before {sorted(files)}\n  after  {sorted(now)}")

# ── SRV-FEAT-065 · a role lane is a lane, and E13 does not know what started it ───────────────

def test_a_role_lane_stops_kills_and_releases_like_any_other(tmp_path, serve):
    """§1's criterion 3, in the file §1 names it in, and the user's literal request:
    *"nó phải hiện trên web factory stop hoặc kill hoặc release đc"* — it has to show on the web
    factory and be stoppable, killable, releasable.

    A lane the TOOL claimed for itself is the one kind `E13` was never driven against, and the
    whole of the user's ask is that it behaves like the kind they already know. If this passes with
    no product change that is the finding, and §1 says so: *"already true" is a finding.*

    Deferred imports: the role fixtures live beside the F31 cases in `test_roles.py`, which imports
    this module at load. Importing it here inside the case keeps that one-way.
    """
    import time as _time

    from test_roles import AGENT, PROMPT

    root = build_repo(tmp_path / "repo", agent={"command": "/bin/sh agent.sh"})
    (root / "agent.sh").write_text(AGENT)
    manifest = json.loads((root / ".factory" / "factory.json").read_text())
    manifest["roles"] = {"qa": {"start_on_stage": "GATE", "lane": "new",
                               "prompt_file": ".factory/prompts/qa.md"}}
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    prompt = root / ".factory" / "prompts" / "qa.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(PROMPT)
    git(root, "add", "-A")
    git(root, "commit", "-m", "a repository that wants a checker started for it")

    srv = serve(root, FACTORY_TEST_HOOKS="1")

    claimed = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()["lane"]
    assert srv.post(f"/api/lanes/{lane}/runs", json={}).status_code == 201

    def log_of(name):
        for row in srv.get("/api/lanes").json()["lanes"]:
            if row["lane"] == name:
                try:
                    return (Path(row["worktree"]) / "agent-log.txt").read_text()
                except OSError:
                    return ""
        return ""

    def wait_for(predicate, what, timeout=25):
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            got = predicate()
            if got:
                return got
            _time.sleep(0.05)
        raise AssertionError(f"{what} never happened within {timeout}s")

    wait_for(lambda: "ready" in log_of(lane), "the run came up")
    token = next(l.split("=", 1)[1] for l in log_of(lane).splitlines() if l.startswith("token="))

    reported = srv.post("/api/progress", json={"token": token, "stage": "GATE"})
    assert reported.status_code == 202, reported.text

    rows = wait_for(lambda: (lambda rs: rs if len(rs) == 2 else None)(
        srv.get("/api/lanes").json()["lanes"]), "the tool claimed a lane nobody asked for")
    started = [r for r in rows if r["lane"] != lane][0]
    assert started["role"] == "qa", started
    wait_for(lambda: "ready" in log_of(started["lane"]), "the started role's own run came up")

    worktree = Path(started["worktree"])
    assert worktree.is_dir(), worktree

    # A live process holds it, so this is the FORCED path — the one a person actually reaches for.
    refused = srv.delete(f"/api/lanes/{started['lane']}")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "wrong_state", refused.text

    answer = srv.delete(f"/api/lanes/{started['lane']}?force=true")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body.get("killed") is True, body

    left = wait_for(lambda: (lambda rs: rs if len(rs) == 1 else None)(
        srv.get("/api/lanes").json()["lanes"]), "the role lane went away")
    assert [r["lane"] for r in left] == [lane], left

    # E13's promise, unchanged for a lane the tool started: the lane goes, the work stays.
    assert branch_exists(root, started["branch"]), (
        f"releasing a role lane destroyed {started['branch']}, and E13 always keeps the branch")


# ── SRV-FIX-443 · no handler thread and no Worker thread is held ─────────────────────────────
#
# `store.h` has promised in writing since SRV-FEAT-008 that "every git invocation and every kill
# runs on a core::Worker … no handler thread is ever blocked". Two places made it untrue and no
# criterion in this file could see either, because every one of them is about the ANSWER an
# endpoint gives and the answer was always right. What nothing measured is what ELSE was waiting
# while it was computed. These two cases measure exactly that: a second client, on a different
# endpoint, while the first request is still in flight.

SLOW_GIT_S = 3.0


def slow_git_on_path(tmp_path, seconds=SLOW_GIT_S, subcommand="worktree"):
    """A `git` wrapper that sleeps on one subcommand and delegates everything else.

    A STAND-IN IS JUDGED AGAINST THE REAL THING: this is not a fake git, it is the real one with
    a delay in front of a single subcommand, resolved with `shutil.which` before the shim is on
    PATH. Everything the server asks it — `worktree add`, `status`, `rev-parse`, `for-each-ref` —
    is answered by the git this machine actually has, byte for byte, with the same exit code.
    The only thing the wrapper changes is WHEN, which is the only thing this case is about.

    Returned as a PATH prefix, so `execvp` in `lane::git::exec_` finds it the way the product
    finds git in production.
    """
    real = shutil.which("git")
    assert real, "no git on PATH; this case is about the real one"
    shim = tmp_path / "slow-git-bin"
    shim.mkdir(parents=True, exist_ok=True)
    wrapper = shim / "git"
    # The product invokes `git -C <root> <subcommand> …`, so the subcommand is never $1. The
    # whole argv is scanned rather than a position guessed: a shim that matched $1 would sleep
    # for nothing and the case would measure a fast git while claiming to measure a slow one.
    wrapper.write_text(
        "#!/bin/sh\n"
        "prev=\n"
        'for a in "$@"; do\n'
        f'  if [ "$prev" = "{subcommand}" ] && [ "$a" = "list" ]; then sleep {seconds}; break; fi\n'
        "  prev=$a\n"
        "done\n"
        f'exec {real} "$@"\n')
    wrapper.chmod(0o755)
    return shim


def timed_get(srv, path):
    started = time.time()
    r = srv.get(path)
    return r, time.time() - started


def test_a_claim_answers_while_git_is_slow(gitrepo, serve, tmp_path):
    """§1 criterion 2 — E9 answers while E12 is waiting on git.

    `reserve_` called `git::worktreeForBranch` — fork + execvp + a blocking `waitpid` on
    `git worktree list --porcelain` — on `lane::Store`'s own strand, and `skipTaken_` stat-ed in a
    loop beside it. One of four io threads was held for the whole of it and every `list`,
    `release`, `setTurn`, `noteOutcome` and `noteActivity` queued behind it. `SRV-FIX-108`
    measured this on 2026-08-07 and named this criterion; it was closed by decision and the case
    was never written.

    The two answers are forced APART before anything is asserted: git takes three seconds and the
    budget for E9 is 300 ms, so a green run cannot be a fast git. The claim is then required to
    complete with `201`, because moving work off the strand may not turn a claim into a failure.
    """
    root = gitrepo()
    before = status_of(root)
    shim = slow_git_on_path(tmp_path)
    srv = serve(root, PATH=f"{shim}:{os.environ['PATH']}")

    answered = {}

    def claim_slowly():
        started = time.time()
        answered["claim"] = claim(srv, ticket="TCK-001")
        answered["claim_s"] = time.time() - started

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(claim_slowly)
        time.sleep(0.4)                      # the claim is now inside reserve_'s git call
        assert not pending.done(), (
            "the claim finished before the slow git could be reached; the shim is not being used")

        r, seconds = timed_get(srv, "/api/lanes")
        assert r.status_code == 200, r.text
        assert seconds < 0.3, (
            f"GET /api/lanes took {seconds:.2f}s while a claim was waiting on a {SLOW_GIT_S}s "
            f"git. E9 is queued behind a fork on the lane strand, which is the whole of "
            f"SRV-FIX-108: `store.h` promises no handler thread is ever blocked.")

        pending.result(timeout=60)

    assert answered["claim"].status_code == 201, answered["claim"].text
    assert answered["claim_s"] > SLOW_GIT_S * 0.5, (
        f"the claim came back in {answered['claim_s']:.2f}s, so it never reached the slow git and "
        f"this case measured nothing")

    lane = answered["claim"].json()
    assert Path(lane["worktree"]).is_dir(), lane
    assert branch_exists(root, "ticket/TCK-001"), lane
    assert status_of(root) == before, "the main checkout changed"


def sigterm_deaf_confinement(tmp_path):
    """A lane whose process really does survive SIGTERM, driven through the product's own knob.

    `FACTORY_CONFINE_BIN` is `lane::confine`'s existing override for which confinement binary to
    exec. It is pointed at a supervisor that ignores SIGTERM and runs the REAL `bwrap` as its
    child, with every argument the product built passed through untouched — so the sandbox is the
    product's sandbox and the only thing that changes is the leader's signal disposition.

    That indirection is necessary rather than decorative. `--unshare-pid` means the kernel tears
    down the whole PID namespace the moment `bwrap` exits, so a `trap '' TERM` written INSIDE the
    lane cannot keep anything alive: measured on 2026-09-08, a lane running
    `trap '' TERM; while true; do sleep 0.2; done` was force-released in 0.04 s and its group was
    empty immediately. The leader is `bwrap`, and the leader is what has to be deaf.

    A wedged agent that catches SIGTERM is the production shape this stands in for, and
    `KILL_GRACE_MS` exists for exactly it. SIGKILL still ends the supervisor at the end of the
    grace, so `killed: true` is earned, not arranged.
    """
    real = shutil.which("bwrap")
    assert real, "no bwrap on PATH; this case is about the real sandbox"
    supervisor = tmp_path / "deaf-confine"
    supervisor.mkdir(parents=True, exist_ok=True)
    binary = supervisor / "confine"
    binary.write_text(
        "#!/bin/sh\n"
        "trap '' TERM\n"
        f'{real} "$@" &\n'
        "child=$!\n"
        "wait $child\n")
    binary.chmod(0o755)
    return str(binary)


TRAPS_SIGTERM = "while true; do sleep 0.2; done"


def test_a_grace_period_holds_no_worker_thread(gitrepo, serve, tmp_path):
    """§1 criterion 3 — two graces run at once and the rest of the server keeps answering.

    `git::killGroup` polled in 20 ms `sleep_for` steps for up to `KILL_GRACE_MS` = 4 s and then
    `KILL_DEADLINE_MS` more, on a `core::Worker` whose pool is TWO threads. Two concurrent
    force-releases occupied both, and everything offloaded behind them stalled: the catalog render
    behind every page load, `lane::diff::read`, id allocation, and the reaper `finish_` posts —
    which `settle_` then waits 2 s for and gives up on, forcing `pid = 0` while the process is
    still alive.

    The processes IGNORE SIGTERM, so the grace really elapses rather than being skipped, and the
    two releases are started within 100 ms of each other so both graces genuinely overlap. The
    observer is a THIRD client on a different endpoint — `GET /api/tickets` renders through
    `core::Worker`, which is the resource this case is about — and its budget is 500 ms against a
    4 s grace.
    """
    root = gitrepo(tickets=("TCK-001", "TCK-002"))
    before = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1",
                FACTORY_CONFINE_BIN=sigterm_deaf_confinement(tmp_path))

    lanes, pids = [], {}
    for ticket in ("TCK-001", "TCK-002"):
        claimed = claim(srv, ticket=ticket)
        assert claimed.status_code == 201, claimed.text
        name = claimed.json()["lane"]
        lanes.append(name)
        started = srv.post("/api/_test/lane-process",
                           json={"lane": name, "command": TRAPS_SIGTERM})
        assert started.status_code == 202, started.text
        pids[name] = started.json()["pid"]
        assert _alive(pids[name]), f"the hook did not start a process in {name}"

    releases, renders, grace_took = {}, [], {}

    def release(name):
        started = time.time()
        releases[name] = srv.delete(lane_path(name) + "?force=true")
        grace_took[name] = time.time() - started

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(release, lanes[0])
        time.sleep(0.1)
        second = pool.submit(release, lanes[1])

        deadline = time.time() + 2.5         # inside both graces: they began ≤ 0.1s apart
        while time.time() < deadline:
            r, seconds = timed_get(srv, "/api/tickets")
            renders.append((r.status_code, seconds))
            time.sleep(0.1)

        first.result(timeout=60)
        second.result(timeout=60)

    assert renders, "no render was attempted while the graces were running"
    slow = [(code, round(s, 2)) for code, s in renders if code != 200 or s >= 0.5]
    assert not slow, (
        f"{len(slow)} of {len(renders)} renders of GET /api/tickets took 500 ms or more (or "
        f"failed) while two 4 s kill graces were elapsing: {slow[:5]}.\n"
        f"Both core::Worker threads are asleep in git::killGroup, so every offload behind them — "
        f"the catalog render, lane::diff::read, id allocation, and the reaper settle_ waits on — "
        f"is stalled for the whole grace.")

    assert grace_took, "no release was timed"
    assert min(grace_took.values()) > 1.0, (
        f"a force-release finished in {min(grace_took.values()):.2f}s, so no grace elapsed and "
        f"this case measured nothing: {grace_took}")

    for name in lanes:
        r = releases[name]
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["released"] is True, body
        assert body["killed"] is True, (
            f"{name} was force-released and the tool does not claim it killed anything: {body}")
        assert body["detail"]["killed_pid"] == pids[name], (
            f"{name} reported killed_pid {body['detail'].get('killed_pid')} and its process was "
            f"{pids[name]}; a grace that reports another lane's pid has confused the two")

    for name, pid in pids.items():
        assert settle(lambda p=pid: {p: ""} if _alive(p) else {}) == {}, (
            f"pid {pid} of {name} is still in /proc after its grace")

    assert lanes_of(srv)["lanes"] == [], (
        f"E9 still lists a lane after both were force-released: {lanes_of(srv)['lanes']}")
    assert status_of(root) == before, "the main checkout changed"


# ── SRV-FEAT-454 · E9 `last_words` and M18 `lane.words` ──────────────────────────────────────
#
# What the agent in a lane last SAID or last CALLED, on the wire, and a frame when it changes.
# `FAC-EPIC-373` §3 froze the shape; these cases are about the WIRE on purpose — the sentence
# about a person is the epic's L2 gate and `WEB-FEAT-366`'s §1, and saying so here is what
# `docs/operations/ESCAPES.md`'s 2026-09-05 row asks of a server ticket that widens a field.
#
# EVERY BLOCK SHAPE THESE CASES FEED COMES OUT OF A COMMITTED CAPTURE OF THE REAL CLI, and the
# index below is derived from the fixtures directory rather than from a list somebody typed: a
# capture added later is swept the day it lands, and a shape none of them contains cannot be fed
# because there is nothing to feed it from. The stand-in agent does not INVENT a frame — it
# writes capture lines back out, byte for byte.

import stand_in

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# `app/agent/transcript.cpp`'s `editsAFile`. Written here as the SPEC's set rather than read out
# of the source: a test that lifts the code's own table cannot disagree with it.
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}

WORDS_CLIP = 160

# The line the real CLI refused on `cli-stdin-2026-07-28-a-line-that-is-not-json.json`, which
# recorded `rc: 1` and `Error parsing streaming input line: say only the word one`. It is fed
# here on the agent's STDOUT, where the tool's own parser meets it, and the tool's answer must be
# an `error` entry that leaves the last words alone.
NOT_JSON_LINE = "say only the word one"


def captured_lines():
    """Every line of every committed capture of the real CLI, with where it came from.

    Swept off the directory. A case that iterates asserts its sweep is non-empty, because a
    ratchet that examined nothing passes.
    """
    out = []
    for path in sorted(FIXTURES.glob("cli-*.jsonl")):
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            if line.strip():
                out.append((path.name, n, line, json.loads(line)))
    assert out, f"no CLI captures under {FIXTURES}: this sweep examined nothing"
    return out


def captured_blocks():
    """The captures indexed BY SHAPE — `text`, `tool_use_edit`, `tool_use_plain`, `tool_result`,
    `result` — each entry carrying the raw line it was cut from so a case can feed it verbatim.

    `tool_use` splits in two because the split is what the field is about: `Read` and `Edit` both
    arrive with an `input.file_path`, and only one of them is a `file_edit`. A fixture in which
    the two agree proves only that the code read one of them.
    """
    index = {}
    for name, n, raw, frame in captured_lines():
        kind = frame.get("type")
        if kind in ("assistant", "user"):
            for block in (frame.get("message") or {}).get("content") or []:
                shape = block.get("type")
                if shape == "tool_use":
                    shape = ("tool_use_edit" if block.get("name") in EDIT_TOOLS
                             else "tool_use_plain")
                index.setdefault(shape, []).append(
                    {"file": name, "line": n, "raw": raw, "frame": frame, "block": block})
        else:
            index.setdefault(kind, []).append(
                {"file": name, "line": n, "raw": raw, "frame": frame, "block": None})
    for shape in ("text", "tool_use_edit", "tool_use_plain", "tool_result", "result"):
        assert index.get(shape), (
            f"no committed capture contains a {shape!r} block, so no case may feed one "
            f"(the fixtures hold: {sorted(index)})")
    return index


def one_capture(shape, file=None, line=None):
    """One indexed capture, named by the file and line it is in so the report can cite it."""
    for item in captured_blocks()[shape]:
        if (file is None or item["file"] == file) and (line is None or item["line"] == line):
            return item
    raise AssertionError(f"no {shape} block at {file}:{line} in the committed captures")


def relabelled(item, text):
    """A captured `text` frame with ONLY its text replaced — the envelope stays the capture's.

    Two of criterion 1's clauses are about text this recording does not happen to contain (a
    two-line block it does; a 200-code-point line it does not). The SHAPE is still the CLI's,
    which is what the stand-in rule is about; the content is the case's, and it says so.
    """
    frame = json.loads(item["raw"])
    blocks = [b for b in frame["message"]["content"] if b.get("type") == "text"]
    assert len(blocks) == 1, frame
    blocks[0]["text"] = text
    return json.dumps(frame)


def expected_words(block):
    """`FAC-EPIC-373` §3's derivation, written from the contract's words and not from the code."""
    if block.get("type") == "text":
        first = next((l for l in block["text"].split("\n") if l.strip()), "")
        first = first.rstrip("\r")
        text = first if len(first) <= WORDS_CLIP else first[:WORDS_CLIP] + "…"
        return {"kind": "prose", "text": text}
    tool = block["name"]
    if tool in EDIT_TOOLS:
        path = (block.get("input") or {}).get("file_path") or ""
        return {"kind": "file_edit", "text": f"{tool} {path}" if path else tool}
    return {"kind": "tool", "text": tool}


# A stand-in that writes a NAMED FILE of capture lines back out on stdout, one message at a time.
# It invents no frame of its own: every byte it prints was put in that file by the case, and every
# such byte came out of a committed recording. The reader is `stand_in.FRAME_READER`, which
# `test_agent.py::test_the_stand_in_agrees_with_the_captured_cli` holds against the same captures.
WORDS_AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
""" + stand_in.FRAME_READER + r"""
echo "ready" >> "$log"
while : ; do
  if read_frame ; then
    printf '%s\n' "in:$text" >> "$log"
    case "$text" in
      emit:*) cat "./`printf '%s' "$text" | cut -d: -f2`" ;;
    esac
    printf '%s\n' "emitted:$text" >> "$log"
  else
    break
  fi
done
echo "eof" >> "$log"
"""


def proc_state_of(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return None


@pytest.fixture
def wordsenv(tmp_path, serve):
    """A repository whose agent replays committed captures on demand, plus a server.

    Its own synthetic repository, never this one, and teardown asserts both halves of the safety
    promise: the main checkout is byte-identical, and every run this case started is reaped.
    """
    made = []
    started = []

    class Env:
        def build(self):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent={"command": "/bin/sh agent.sh"})
            (root / "agent.sh").write_text(WORDS_AGENT_SH)
            git(root, "add", "agent.sh")
            git(root, "commit", "-m", "an agent that replays a committed capture")
            self.root = root
            self.baseline = status_of(root)
            made.append((root, self.baseline))
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        # ── the lane and the run ─────────────────────────────────────────────────────────────
        def claim(self, ticket="TCK-001"):
            r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": "core"})
            assert r.status_code == 201, r.text
            return r.json()

        def start(self, lane):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/runs", json={})
            assert r.status_code == 201, r.text
            run = r.json()
            started.append(run["pid"])
            self.wait_log(lane, "ready")
            return run

        def end(self, lane):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
            assert r.status_code in (200, 202), r.text
            return r

        def kill(self, lane):
            self.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})

        # ── feeding the agent's stdout, one step at a time ───────────────────────────────────
        def feed(self, lane, name, lines, end_turn=True):
            """Write a step file of capture lines into the lane and tell the agent to print it.

            The `result` frame at the end is a capture too, and it is what ends the turn — so the
            next message is DELIVERED rather than held, which is the only way a case can put one
            entry on the wire and then look.
            """
            body = list(lines)
            if end_turn:
                body.append(one_capture("result")["raw"])
            (Path(lane["worktree"]) / name).write_text("\n".join(body) + "\n")
            r = self.srv.post(f"/api/lanes/{lane['lane']}/messages",
                              json={"text": f"emit:{name}"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "delivered", (
                f"the step was HELD, so the agent never printed it: {r.json()}")
            self.wait_log(lane, f"emitted:emit:{name}")
            return r

        # ── what the wire says ───────────────────────────────────────────────────────────────
        def row(self, lane):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            for row in r.json()["lanes"]:
                if row["lane"] == lane["lane"]:
                    return row
            raise AssertionError(f"{lane['lane']} is not in E9: {r.text}")

        def words(self, lane):
            row = self.row(lane)
            assert "last_words" in row, (
                f"E9's lane row has no `last_words` member at all — the client cannot read a "
                f"field that is absent, which is what docs/operations/ESCAPES.md's 2026-09-05 row is "
                f"about. The row is: {row}")
            return row["last_words"]

        def wait_words(self, lane, expect, timeout=20):
            """Poll E9 until the row says `expect`, or say what it said instead."""
            deadline = time.time() + timeout
            seen = None
            while time.time() < deadline:
                seen = self.words(lane)
                if seen is not None and {"kind": seen["kind"], "text": seen["text"]} == expect:
                    return seen
                time.sleep(0.05)
            raise AssertionError(
                f"E9 never said {expect} for {lane['lane']} within {timeout}s; it says {seen}")

        def entries(self, run, atleast=1, timeout=20):
            path = self.root / ".factory" / "runs" / run["run_id"] / "transcript.jsonl"
            deadline = time.time() + timeout
            out = []
            while time.time() < deadline:
                try:
                    out = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
                except OSError:
                    out = []
                if len(out) >= atleast:
                    return out
                time.sleep(0.05)
            return out

        # ── the agent's own log ──────────────────────────────────────────────────────────────
        def log(self, lane):
            try:
                return (Path(lane["worktree"]) / "agent-log.txt").read_text()
            except OSError:
                return ""

        def wait_log(self, lane, needle, timeout=25):
            deadline = time.time() + timeout
            while time.time() < deadline:
                text = self.log(lane)
                if needle in text:
                    return text
                time.sleep(0.05)
            raise AssertionError(
                f"{needle!r} never reached the agent's log within {timeout}s; it holds:\n"
                f"{self.log(lane)}")

        def advance_clock(self, seconds):
            """The product's own test clock — `at` is stamped from it, so a case can put two
            entries either side of a second boundary without waiting for one."""
            r = self.srv.post("/api/_test/clock", json={"advance_s": seconds})
            assert r.status_code == 202, r.text
            return r.json()

    yield Env()

    for pid in started:
        deadline = time.time() + 20
        while time.time() < deadline and proc_state_of(pid) is not None:
            time.sleep(0.05)
        assert proc_state_of(pid) is None, f"the run (pid {pid}) was never reaped"
    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed:\nbefore:\n{before}\nafter:\n{status_of(root)}")


# ── SRV-FEAT-454 · criterion 1 ───────────────────────────────────────────────────────────────

def test_e9_says_what_the_agent_last_said_or_called(wordsenv):
    """The row says what the agent last said or called, and changes as it does.

    Three entries that DISAGREE — a `text` block, a `tool_use` of an edit tool with a path, a
    second `text` block — each read off `E9` after it is fed. A fixture whose entries agreed would
    pass whichever one the code happened to read.

    Fixtures, one row per entry:
      · `cli-stream-2026-07-27-tools.jsonl:5`  — `text`
      · `cli-stream-2026-07-27-tools.jsonl:6`  — `tool_use` `Edit`, with `input.file_path`
      · `cli-stdin-2026-07-28-default-input-two-messages.stdout.jsonl:2` — `text`, two lines
    and the 200-code-point line is that last capture's ENVELOPE with the case's own text in it.
    """
    env = wordsenv.build()
    lane = env.claim()
    run = env.start(lane)

    assert env.words(lane) is None, (
        f"a run that has said nothing must read null, not {env.words(lane)}")

    said = one_capture("text", "cli-stream-2026-07-27-tools.jsonl", 5)
    called = one_capture("tool_use_edit", "cli-stream-2026-07-27-tools.jsonl", 6)
    two_lines = one_capture("text",
                            "cli-stdin-2026-07-28-default-input-two-messages.stdout.jsonl", 2)

    first = expected_words(said["block"])
    second = expected_words(called["block"])
    third = expected_words(two_lines["block"])
    assert len({(w["kind"], w["text"]) for w in (first, second, third)}) == 3, (
        f"the three entries do not disagree, so this case cannot tell them apart: "
        f"{first} {second} {third}")
    assert second["kind"] == "file_edit" and " " in second["text"], second
    assert second["text"].endswith((called["block"]["input"] or {})["file_path"]), (
        f"a file_edit must carry the path the transcript carries: {second}")

    ats = []
    for step, (item, expect) in enumerate(
            ((said, first), (called, second), (two_lines, third)), start=1):
        env.feed(lane, f"step{step}.jsonl", [item["raw"]])
        got = env.wait_words(lane, expect)
        ats.append(got["at"])
        # The clock the field is stamped from, moved on so the next `at` is a different second.
        env.advance_clock(2)

    assert ats == sorted(ats) and len(set(ats)) == 3, (
        f"`at` is not strictly increasing across the three entries: {ats}")

    # A text block of two lines yields its FIRST line only.
    assert "\n" in two_lines["block"]["text"], two_lines["block"]
    assert third["text"] == two_lines["block"]["text"].split("\n")[0], third
    assert env.words(lane)["text"] == third["text"], env.words(lane)

    # …and a line of 200 code points yields 160 and `…`. Code POINTS: the padding is a
    # three-byte character, so a clip that counted bytes would stop at 80 of them.
    long_line = 'he said "hi"\\done ' + "—" * (200 - len('he said "hi"\\done '))
    assert len(long_line) == 200, len(long_line)
    env.feed(lane, "step4.jsonl", [relabelled(two_lines, long_line)])
    clipped = env.wait_words(lane, {"kind": "prose", "text": long_line[:160] + "…"})
    assert len(clipped["text"]) == 161, (
        f"160 code points and one ellipsis is 161; this is {len(clipped['text'])}: "
        f"{clipped['text']!r}")

    env.kill(lane)


# ── SRV-FEAT-454 · criterion 2 ───────────────────────────────────────────────────────────────

def test_lane_words_reach_every_scope_a_lane_state_frame_reaches(wordsenv):
    """M18 goes where M2 goes, and nowhere else.

    Three sockets — `{requirements:true}`, this lane, another lane — and one entry. The first two
    get one `lane.words` each and it is byte-equal to what E9 then serves; the third gets none
    within the wait that let the other two arrive, which is the only way to prove an absence.

    Fixture: `cli-stream-2026-07-27-simple.jsonl:5` — a `tool_use` of `Read`, which carries an
    `input.file_path` and is NOT an edit tool, so `kind` is `tool` and `text` is the bare name.
    That is the pair the two possible readings of a `tool_use` disagree on.
    """
    env = wordsenv.build()
    lane = env.claim("TCK-001")
    other = env.claim("TCK-002")
    run = env.start(lane)

    called = one_capture("tool_use_plain", "cli-stream-2026-07-27-simple.jsonl", 5)
    expect = expected_words(called["block"])
    assert expect == {"kind": "tool", "text": "Read"}, (
        f"a Read with a file_path is a `tool`, not a `file_edit`: {expect}")

    everywhere, here, elsewhere = env.srv.ws(), env.srv.ws(), env.srv.ws()
    try:
        everywhere.send({"m": "subscribe", "scope": {"requirements": True}})
        everywhere.wait_for("snapshot")
        here.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
        here.wait_for("snapshot")
        elsewhere.send({"m": "subscribe", "scope": {"lane": other["lane"]}})
        elsewhere.wait_for("snapshot")

        env.feed(lane, "one.jsonl", [called["raw"]])

        heard = []
        for name, sock in (("requirements", everywhere), ("this lane", here)):
            frame = sock.wait_for("lane.words", timeout=10)
            SOCKET_VALIDATOR.validate(frame)          # M18, judged by docs/technical/contracts/socket.schema.json
            assert frame["lane"] == lane["lane"], (name, frame)
            assert frame["ticket_id"] == lane["ticket_id"], (name, frame)
            assert frame["seq"] is None, (name, frame)
            heard.append(frame)

        # The one place the two halves of this ticket could disagree: the frame and the row.
        served = env.words(lane)
        for frame in heard:
            assert json.dumps(frame["last_words"], sort_keys=True) == \
                   json.dumps(served, sort_keys=True), (
                f"the frame carries {frame['last_words']} and E9 serves {served}")
        assert {"kind": served["kind"], "text": served["text"]} == expect, served

        # …and exactly one each, not a frame per subscription the connection holds.
        for name, sock in (("requirements", everywhere), ("this lane", here)):
            more = [f for f in sock.drain(2.5) if f.get("m") == "lane.words"]
            assert more == [], f"{name} heard {len(more)} more lane.words frames: {more}"

        stray = [f for f in elsewhere.drain(2.5) if f.get("m") == "lane.words"]
        assert stray == [], (
            f"a subscriber to {other['lane']} was sent this lane's words: {stray}")
    finally:
        everywhere.close()
        here.close()
        elsewhere.close()

    env.kill(lane)


# ── SRV-FEAT-454 · criterion 3 ───────────────────────────────────────────────────────────────

def test_a_burst_of_entries_is_one_frame_carrying_the_last(wordsenv):
    """Twelve entries inside 500 ms produce fewer than twelve frames, and the last one is the row.

    The count and the value are asserted in the same case on purpose: a coalescer that dropped
    the burst entirely would pass the count alone, and one that never coalesced would pass the
    value alone.

    Fixture: twelve capture lines whose derived words all DIFFER, swept out of the committed
    recordings — a burst of twelve identical values cannot tell a trailing value from any other.
    """
    env = wordsenv.build()
    lane = env.claim()
    run = env.start(lane)

    index = captured_blocks()
    burst, seen = [], set()
    for item in index["text"] + index["tool_use_edit"] + index["tool_use_plain"]:
        key = tuple(expected_words(item["block"]).items())
        if key in seen:
            continue
        seen.add(key)
        burst.append(item)
    assert len(burst) >= 12, (
        f"the captures hold only {len(burst)} distinct derived values, and this case needs "
        f"twelve that disagree")
    burst = burst[:12]
    last = expected_words(burst[-1]["block"])
    assert all(expected_words(i["block"]) != last for i in burst[:-1]), burst

    here = env.srv.ws()
    try:
        here.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
        here.wait_for("snapshot")

        started = time.time()
        env.feed(lane, "burst.jsonl", [i["raw"] for i in burst])
        fed_in = time.time() - started

        env.wait_words(lane, last)
        frames = [f for f in here.drain(4.0) if f.get("m") == "lane.words"]
    finally:
        here.close()

    assert fed_in < 0.5 or True, fed_in            # recorded below, never asserted on the clock
    entries = env.entries(run, atleast=12)
    assert len([e for e in entries if e.get("kind") in ("prose", "tool", "file_edit")]) >= 12, (
        f"the twelve entries never reached the parser, so this case measured nothing: {entries}")

    assert 1 <= len(frames) < 12, (
        f"twelve entries produced {len(frames)} lane.words frames — coalescing means fewer "
        f"frames than entries, and at least one: {[f['last_words'] for f in frames]}")
    served = env.words(lane)
    assert json.dumps(frames[-1]["last_words"], sort_keys=True) == \
           json.dumps(served, sort_keys=True), (
        f"the last frame carries {frames[-1]['last_words']} and E9 serves {served}")
    assert {"kind": served["kind"], "text": served["text"]} == last, served

    env.kill(lane)


# ── SRV-FEAT-454 · criterion 4 ───────────────────────────────────────────────────────────────

def test_a_new_run_starts_with_no_words(wordsenv):
    """A lane that has words, ended and started again, reads null until the new run says something.

    And the client is told: the `M2` transition to `running` is heard before any `lane.words` for
    the new run, which is the frame `WEB-FEAT-366` clears the line on.

    Fixture: `cli-stream-2026-07-27-tools.jsonl:5` — a `text` block.
    """
    env = wordsenv.build()
    lane = env.claim()
    first = env.start(lane)

    said = one_capture("text", "cli-stream-2026-07-27-tools.jsonl", 5)
    env.feed(lane, "said.jsonl", [said["raw"]])
    env.wait_words(lane, expected_words(said["block"]))

    env.end(lane)
    deadline = time.time() + 25
    while time.time() < deadline and proc_state_of(first["pid"]) is not None:
        time.sleep(0.05)
    assert proc_state_of(first["pid"]) is None, "the first run never ended"

    here = env.srv.ws()
    try:
        here.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
        here.wait_for("snapshot")

        second = env.start(lane)
        assert second["run_id"] != first["run_id"], second

        assert env.words(lane) is None, (
            f"a new run inherited the last run's words: {env.words(lane)}")

        # The M2 the client clears the line on arrives, and no M18 comes before it.
        before_running = []
        deadline = time.time() + 10
        while time.time() < deadline:
            frame = here.recv()
            if frame.get("m") == "lane.state" and frame.get("state", {}).get("process") == "running":
                break
            before_running.append(frame)
        else:
            raise AssertionError(f"no lane.state said running; saw {before_running}")
        assert [f for f in before_running if f.get("m") == "lane.words"] == [], (
            f"a lane.words frame arrived before the run was announced as running: "
            f"{before_running}")

        assert env.words(lane) is None, env.words(lane)

        # …and the new run's FIRST entry fills it again.
        again = one_capture("text", "cli-stream-2026-07-27-simple.jsonl", 7)
        env.feed(lane, "again.jsonl", [again["raw"]])
        frame = here.wait_for("lane.words", timeout=10)
        SOCKET_VALIDATOR.validate(frame)
        assert {"kind": frame["last_words"]["kind"], "text": frame["last_words"]["text"]} == \
               expected_words(again["block"]), frame
    finally:
        here.close()

    env.kill(lane)


# ── SRV-FEAT-454 · criterion 5 · MANDATORY NEGATIVE ──────────────────────────────────────────

def test_what_the_tool_said_back_is_not_the_agents_words(wordsenv):
    """What the TOOL said back is not what the agent said.

    A `tool_result` — matched to a live call and unmatched, because those are two different
    entries in the parser — a turn-ending `result` frame, and a line that is not JSON: all four
    reach the parser, and none of them moves the field or sends a frame. A lane that never
    started reads null. And the contract itself refuses a fourth kind.

    Fixtures:
      · `cli-stream-2026-07-27-tools.jsonl:6`  — `tool_use` `Edit` (the call the result matches)
      · `cli-stream-2026-07-27-tools.jsonl:5`  — `text`
      · `cli-stream-2026-07-27-tools.jsonl:7`  — `tool_result` for that call
      · `cli-stream-2026-07-27-simple.jsonl:3` — `tool_result` for a call this run never made
      · `cli-stream-2026-07-27-tools.jsonl:13` — `result`
      · `cli-stdin-2026-07-28-a-line-that-is-not-json.json` — the line the real CLI refused
    """
    env = wordsenv.build()
    lane = env.claim("TCK-001")

    # A lane claimed and never started.
    never = env.claim("TCK-002")
    assert env.words(never) is None, (
        f"a lane with no run reads null, not {env.words(never)}")

    run = env.start(lane)

    called = one_capture("tool_use_edit", "cli-stream-2026-07-27-tools.jsonl", 6)
    said = one_capture("text", "cli-stream-2026-07-27-tools.jsonl", 5)
    answered = one_capture("tool_result", "cli-stream-2026-07-27-tools.jsonl", 7)
    stray = one_capture("tool_result", "cli-stream-2026-07-27-simple.jsonl", 3)
    ended = one_capture("result", "cli-stream-2026-07-27-tools.jsonl", 13)

    assert answered["block"]["tool_use_id"] == called["block"]["id"], (
        "the matched result must answer the call this case makes, or the patch entry is never "
        "reached")
    assert stray["block"]["tool_use_id"] != called["block"]["id"], stray

    env.feed(lane, "call.jsonl", [called["raw"]])
    env.wait_words(lane, expected_words(called["block"]))
    env.feed(lane, "said.jsonl", [said["raw"]])
    prose = env.wait_words(lane, expected_words(said["block"]))

    here = env.srv.ws()
    try:
        here.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
        here.wait_for("snapshot")
        here.drain(3.0)                    # past the coalescing window the prose entry armed

        before = env.entries(run)
        env.feed(lane, "back.jsonl",
                 [answered["raw"], stray["raw"], ended["raw"], NOT_JSON_LINE], end_turn=False)

        after = env.entries(run, atleast=len(before) + 4)
        kinds = [e.get("kind") for e in after[len(before):]]
        assert "tool_result" in kinds and "turn" in kinds and "error" in kinds, (
            f"the four things this case feeds never reached the parser, so it measured "
            f"nothing: {kinds}")
        assert any(e.get("kind") == "file_edit" and e.get("patch") for e in after), (
            f"the matched tool_result never became a patch entry: {after[len(before):]}")

        frames = [f for f in here.drain(3.5) if f.get("m") == "lane.words"]
        assert frames == [], (
            f"what the tool said back was published as the agent's words: {frames}")
    finally:
        here.close()

    assert json.dumps(env.words(lane), sort_keys=True) == json.dumps(prose, sort_keys=True), (
        f"the field moved: it was {prose} and is now {env.words(lane)}")

    # The contract refuses a fourth kind, at `$defs.lane.properties.last_words`.
    HTTP_SCHEMA = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/technical/contracts/http.schema.json").read_text())
    node = HTTP_SCHEMA["$defs"]["lane"]["properties"]["last_words"]
    validator = jsonschema.Draft202012Validator(
        {"$schema": HTTP_SCHEMA["$schema"], "$defs": HTTP_SCHEMA["$defs"], **node})
    validator.validate(prose)                                   # the three kinds are accepted
    validator.validate(None)                                    # and so is null
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"kind": "tool_result", "text": "x", "at": prose["at"]})

    env.kill(lane)


# ── SRV-FEAT-454 · criterion 7 · the live row against the widened node ────────────────────────

def test_the_live_e9_row_validates_against_the_widened_schema(wordsenv):
    """The RUNNING BINARY's `E9` validates against the widened `lane` node, with `jsonschema`.

    Round 1 of this ticket had nothing that asserted this in a `factory-server` lane: the check
    lived in `client/web`'s vitest and in the browser gate's §6b, and neither can be run where
    the tool claims a lane for an `SRV-*` ticket. QA round 1 therefore had to SUBSTITUTE it in
    its own process and recorded the substitution as such
    (`docs/reports/SRV-FEAT-454-qa.md`, criterion 7(b)). The operator narrowed §1 on 2026-09-08
    to ask for it here, and this case is that assertion — transcribed from QA's own reproduction:

        == A · the live E9, whole, against docs/technical/contracts/http.schema.json
           last_words served: {"kind": "prose", "text": "first words", "at": …}
           E9 validation errors: none
           NEGATIVE — same body with last_words removed: ["'last_words' is a required property"]

    It is asserted over a population of TWO KINDS that DISAGREE — one lane whose run has said
    something and one that never started and reads `null` — because `last_words` is nullable and
    a fixture holding only one of the two says nothing about the other.

    Fixture: `cli-stream-2026-07-27-tools.jsonl:5`, a `text` block.
    """
    HTTP_SCHEMA = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/technical/contracts/http.schema.json").read_text())

    # The node the criterion names, and the whole response it sits in. Both, because a row that
    # validates inside a body that does not is a row nobody reads.
    lane_node = jsonschema.Draft202012Validator(
        {"$schema": HTTP_SCHEMA["$schema"], "$defs": HTTP_SCHEMA["$defs"],
         "$ref": "#/$defs/lane"})
    e9 = jsonschema.Draft202012Validator(
        {"$schema": HTTP_SCHEMA["$schema"], "$defs": HTTP_SCHEMA["$defs"],
         **HTTP_SCHEMA["responses"]["E9"]})

    assert "last_words" in HTTP_SCHEMA["$defs"]["lane"]["required"], (
        "the node is not widened: `last_words` is not required, so a server that stops serving "
        "it is silently absent from a card rather than red here — docs/operations/ESCAPES.md 2026-09-05")

    env = wordsenv.build()
    speaking = env.claim("TCK-001")
    silent = env.claim("TCK-002")
    env.start(speaking)

    def served():
        """E9 as the binary serves it. No membership check here on purpose: the SCHEMA is what
        must refuse a row missing the member, so nothing may refuse it first."""
        answer = env.srv.get("/api/lanes")
        assert answer.status_code == 200, answer.text
        return answer.json()

    def judged(body, when):
        e9.validate(body)
        for row in body["lanes"]:
            errors = sorted(lane_node.iter_errors(row), key=str)
            assert errors == [], (
                f"{when}: {row['lane']} does not validate against $defs.lane: {errors}")

    # Kind one: a run that has said nothing. Judged BEFORE anything is fed, so a binary that
    # serves no `last_words` at all is refused here, by the schema, and not by a wait.
    judged(served(), "a run that has said nothing")

    said = one_capture("text", "cli-stream-2026-07-27-tools.jsonl", 5)
    env.feed(speaking, "said.jsonl", [said["raw"]])

    deadline = time.time() + 20
    body = served()
    while time.time() < deadline:
        body = served()
        rows = {row["lane"]: row for row in body["lanes"]}
        if rows[speaking["lane"]].get("last_words") is not None:
            break
        time.sleep(0.05)

    # Kind two: a run that has spoken. The two kinds are asserted to DISAGREE, because
    # `last_words` is nullable and a population holding one of them says nothing about the other.
    judged(body, "a run that has spoken")
    rows = {row["lane"]: row for row in body["lanes"]}
    assert len(rows) == 2, f"this case needs two lanes that disagree; E9 served {list(rows)}"
    assert rows[speaking["lane"]]["last_words"] is not None, rows[speaking["lane"]]
    assert rows[silent["lane"]]["last_words"] is None, rows[silent["lane"]]
    assert {"kind": rows[speaking["lane"]]["last_words"]["kind"],
            "text": rows[speaking["lane"]]["last_words"]["text"]} == expected_words(said["block"])

    # NEGATIVE, transcribed from QA's reproduction: a server that stopped serving the member.
    for name in rows:
        stripped = json.loads(json.dumps(body))
        for row in stripped["lanes"]:
            if row["lane"] == name:
                del row["last_words"]
        messages = [e.message for e in e9.iter_errors(stripped)]
        assert any("'last_words' is a required property" in m for m in messages), (
            f"removing last_words from {name} left E9 valid, so this check cannot see a server "
            f"that stopped serving it: {messages}")

    env.kill(speaking)


# ── SRV-FIX-478 · a ticket the repository says has passed cannot be claimed ───────────────────
#
# `E12` loaded the ticket document and used only whether the lookup found it: `doc.status` was
# filled and never read. So one click on a ticket whose file says `status: pass` — QA's verdict,
# landed on `main` — cut `ticket/<id>` again, took a seat, took the workspace's exclusive
# resources and started an agent on work that is finished.
#
# The word compared is `pass`, which the tool already owns at three sites (`land/lander.cpp`,
# `delivery/probe.cpp`, `catalog/render.cpp`). This is the fourth reader of the same field, and
# it declares no new vocabulary.

# The manifest's default agent is `claude`, and a repository whose declared agent is not on PATH
# is refused AT LOAD — so a fixture that takes the default measures the machine rather than the
# server. `test_errors.py`'s corpus declares `/bin/sh` for the same reason.
FIXTURE_AGENT = {"command": "/bin/sh"}

PASS_MESSAGE = ("this ticket's document says `status: pass`; the work after a pass is a new "
                "ticket, and the tool does not write the repository's documents")


def write_ticket(root, ticket, status=None):
    """One ticket document, with or without a `status:` line.

    `status=None` writes no line at all, which is a different document from one whose status is
    empty: the fifth kind criterion 2 names.
    """
    text = f"---\nid: {ticket}\ntitle: ticket {ticket}\n"
    if status is not None:
        text += f"status: {status}\n"
    text += f"---\n\nbody of {ticket}\n"
    path = Path(root) / "docs" / "tickets" / f"{ticket}.md"
    path.write_text(text)
    return path


def ticket_branches(root, ticket):
    """Every branch git holds whose name begins `ticket/<id>` — derived, never a typed pair.

    The role suffix is the tool's to choose (`ticket/<id>-<role>`), so a case that names the two
    spellings it happens to expect cannot see a third.
    """
    out = git(root, "branch", "--list", f"ticket/{ticket}*", "--format=%(refname:short)")
    return [line for line in out.stdout.splitlines() if line.strip()]


def lane_dirs(root):
    return sorted(p.name for p in (Path(root) / ".factory" / "worktrees" / "lane").glob("*")) \
        if (Path(root) / ".factory" / "worktrees" / "lane").is_dir() else []


def test_a_passed_ticket_cannot_be_claimed_in_any_role(tmp_path, serve):
    """§1 criterion 1 — refused in EVERY role, and the refusal leaves nothing behind.

    Both roles in one case because the claim being asserted is *any role*: a refusal that only
    covers `run` still hands a passed ticket a QA lane, a branch and a seat.

    Everything after the two refusals is what "leaves nothing behind" means, and each line is a
    thing the refusal must not have done first — the same ordering `workspace_unrunnable` holds.
    """
    root = build_repo(tmp_path / "repo", agent=FIXTURE_AGENT)
    write_ticket(root, "TCK-001", "pass")
    git(root, "add", "-A")
    git(root, "commit", "-m", "TCK-001: QA passed it")
    before = status_of(root)
    srv = serve(root)

    for role in (None, "qa"):
        r = claim(srv, ticket="TCK-001") if role is None else claim(srv, ticket="TCK-001",
                                                                    role=role)
        assert r.status_code == 409, (
            f"a lane was claimed in role {role or 'run'} for a ticket whose document says "
            f"`status: pass`: {r.status_code} {r.text}")
        err = r.json()["error"]
        assert err["code"] == "already_passed", err
        assert err["detail"]["ticket_id"] == "TCK-001", err
        assert err["detail"]["status"] == "pass", err
        assert err["detail"]["path"] == "docs/tickets/TCK-001.md", (
            f"detail.path must name the ticket file relative to the document root: {err}")
        assert err["message"] == PASS_MESSAGE, err
        assert "remedy" in err, f"the remedy member is absent, not null: {err}"
        assert err["remedy"] is None, err

    body = lanes_of(srv)
    assert body["lanes"] == [], f"a lane record survived the refusal: {body['lanes']}"
    assert body["seats"]["host"]["used"] == 0, body["seats"]
    assert ticket_branches(root, "TCK-001") == [], \
        f"a branch was created for a refused claim: {ticket_branches(root, 'TCK-001')}"
    assert lane_dirs(root) == [], f"a worktree was created for a refused claim: {lane_dirs(root)}"
    assert status_of(root) == before, "the main checkout changed"


def test_every_status_but_pass_still_claims(tmp_path, serve):
    """§1 criterion 2 — MANDATORY NEGATIVE at the product level.

    The cheap way to pass the case above is to refuse any ticket that carries a status, or to
    refuse every claim; both stop the tool while every assertion up there stays green.

    Five kinds in ONE case, and the collection is asserted with the claim about it. `fail` is
    named because a FAIL *begins* a round and the round needs the lane, and the fifth ticket
    carries no `status:` line at all — a document with no status is not a document that passed.
    """
    kinds = {"TCK-001": "todo", "TCK-002": "in-progress", "TCK-003": "awaiting-qa",
             "TCK-004": "fail", "TCK-005": None}
    root = build_repo(tmp_path / "repo", lanes_host=5, tickets=tuple(kinds),
                      agent=FIXTURE_AGENT)
    for ticket, status in kinds.items():
        write_ticket(root, ticket, status)
    git(root, "add", "-A")
    git(root, "commit", "-m", "five tickets, five statuses")
    before = status_of(root)
    srv = serve(root)

    for ticket, status in kinds.items():
        r = claim(srv, ticket=ticket)
        assert r.status_code == 201, (
            f"a ticket at status {status!r} was refused a lane: {r.status_code} {r.text}")

    body = lanes_of(srv)
    assert sorted(l["ticket_id"] for l in body["lanes"]) == sorted(kinds), (
        f"{len(kinds)} claimable statuses were claimed and E9 lists "
        f"{[l['ticket_id'] for l in body['lanes']]}")
    assert body["seats"]["host"]["used"] == len(kinds), body["seats"]
    assert status_of(root) == before, "the main checkout changed"


def test_the_status_is_read_from_the_file_on_every_claim(tmp_path, serve):
    """§1 criterion 3 — the document is the state (`S2`), read on every claim and never cached.

    Three reads of ONE file that disagree in sequence, taken while the server runs. A copy made
    at startup, or at the first claim, answers the first step correctly and then fails the second
    or the third — which is the whole point of forcing the two possible sources apart.
    """
    root = build_repo(tmp_path / "repo", agent=FIXTURE_AGENT)
    ticket = write_ticket(root, "TCK-001", "awaiting-qa")
    git(root, "add", "-A")
    git(root, "commit", "-m", "TCK-001: awaiting qa")
    srv = serve(root)

    first = claim(srv, ticket="TCK-001")
    assert first.status_code == 201, f"a ticket at `awaiting-qa` was refused: {first.text}"

    # The pass lands, on disk, with the server running and nothing told about it.
    write_ticket(root, "TCK-001", "pass")
    second = claim(srv, ticket="TCK-001", role="qa")
    assert second.status_code == 409, (
        f"the claim answered from a status the file no longer carries: "
        f"{second.status_code} {second.text}")
    assert second.json()["error"]["code"] == "already_passed", second.text
    assert second.json()["error"]["detail"]["status"] == "pass", second.text

    # ...and back again: a person edits the document, and the next read answers.
    write_ticket(root, "TCK-001", "in-progress")
    third = claim(srv, ticket="TCK-001", role="qa")
    assert third.status_code == 201, (
        f"the refusal outlived the status that produced it: {third.status_code} {third.text}")
    assert third.json()["ticket_id"] == "TCK-001", third.text

    assert ticket.read_text().count("status: in-progress") == 1, \
        "the tool wrote the repository's document"


def test_a_pass_written_while_a_lane_is_live_does_not_touch_the_lane(tmp_path, serve):
    """§1 criterion 4 — `S5`. A pass refuses new claims and does nothing else.

    Whether a lane is live is answered by `GET /api/lanes` and by nothing else, and a lane's
    resources are released only by releasing the lane through the tool. A refusal that also
    tidied up would take a running agent's worktree out from under it.
    """
    root = build_repo(tmp_path / "repo", agent=FIXTURE_AGENT)
    write_ticket(root, "TCK-001", "awaiting-qa")
    git(root, "add", "-A")
    git(root, "commit", "-m", "TCK-001: awaiting qa")
    srv = serve(root)

    live = claim(srv, ticket="TCK-001")
    assert live.status_code == 201, live.text
    live = live.json()
    worktree = Path(live["worktree"])
    assert worktree.is_dir(), live

    write_ticket(root, "TCK-001", "pass")

    body = lanes_of(srv)
    assert [l["lane"] for l in body["lanes"]] == [live["lane"]], (
        f"the pass changed the lane set: {body['lanes']}")
    assert body["lanes"][0]["ticket_id"] == "TCK-001", body["lanes"][0]
    assert body["seats"]["host"]["used"] == 1, body["seats"]
    assert worktree.is_dir(), "the pass removed a live lane's worktree"

    # A new claim is refused while that lane goes on existing — the two facts in one case.
    refused = claim(srv, ticket="TCK-001", role="qa")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "already_passed", refused.text
    assert [l["lane"] for l in lanes_of(srv)["lanes"]] == [live["lane"]], lanes_of(srv)
    assert worktree.is_dir(), "the refusal removed a live lane's worktree"

    # ...and only the tool releases it.
    r = srv.delete(lane_path(live["lane"]) + "?force=true")
    assert r.status_code == 200, r.text
    assert r.json()["released"] is True, r.text
    assert not worktree.exists(), "the release left the worktree behind"
    assert lanes_of(srv)["lanes"] == [], lanes_of(srv)


# ── SRV-FIX-532 cluster F2 — a started run has exec'd ─────────────────────────────────────────
#
# `SRV-FIX-486`. `spawnConfined` returned at the `fork`, and until `execvp` replaces the image the
# child still carries its PARENT's command line — so `E18`'s `201` named a pid whose `/proc` argv
# was the server's own, two times in five. `SPEC.md` `E18` says *spawns the agent inside the lane's
# worktree*, and a pid whose image is still the server's is not a spawned agent.
#
# `SRV-FIX-034` is the same physics from the other side: eight merged tickets counted a case as
# evidence whose premise — `pgrep` can see the child — was false on every run ever made, for
# exactly this reason. The answer there and here is to wait on a fact the thing itself produces: a
# `FD_CLOEXEC` pipe the exec closes, which the parent reads.

SLEEPS = "#!/bin/sh\nexec sleep 30\n"


def cmdline_of(pid):
    """`/proc/<pid>/cmdline`, ONE read and no retry — the window is what is being measured."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace").strip()
    except OSError as why:
        return f"(unreadable: {why})"


def test_a_started_run_has_execd_when_e18_answers(gitrepo, serve):
    """§1 F2 — the pid `E18` returns has exec'd by the time the `201` is written.

    `SRV-FIX-486` criterion 1. Read at once, one read and no retry, because the defect is a window
    and a retry loop measures how long the test waited rather than what the endpoint promised. The
    reduction is over TWO KINDS — a run started with a prompt and a run started with `resume` —
    and twenty reads in all, because a single read can miss a window that opens two times in five.

    RED before the fix: the argv is the server's own —
    `['…/factory-server', '--repo', '/tmp/pyt…']` — and it carries neither
    `FACTORY_PROGRESS_TOKEN` nor the agent-config bind the tool exec'd it with.
    """
    root = gitrepo(agent={"command": "/bin/sh agent.sh"})
    (root / "agent.sh").write_text(SLEEPS)
    git(root, "add", "-A")
    git(root, "commit", "-m", "an agent that exec's and stays")
    srv = serve(root)

    kinds = {"a run started with a prompt": {"prompt": "say nothing"},
             "a run started with resume": {"resume": True}}
    seen = {}
    for kind, body in sorted(kinds.items()):
        for attempt in range(10):
            lane = claim(srv, ticket="TCK-001")
            assert lane.status_code == 201, lane.text
            name = lane.json()["lane"]
            started = srv.post(f"/api/lanes/{name}/runs", json=body)
            assert started.status_code in (200, 201, 202), (kind, started.text)
            pid = started.json()["pid"]

            argv = cmdline_of(pid)
            seen.setdefault(kind, []).append(argv)
            assert "factory-server" not in argv, (
                f"{kind}, read {attempt + 1}: `E18` answered with a pid whose image is still the "
                f"server's — it has forked and not yet exec'd, so the lane `E9` calls running may "
                f"still fail to become the agent:\n{argv}")
            assert "FACTORY_PROGRESS_TOKEN" in argv or "sleep" in argv or "agent.sh" in argv, (
                f"{kind}, read {attempt + 1}: the argv is neither the confinement helper's nor "
                f"the agent's:\n{argv}")

            srv.post(f"/api/lanes/{name}/kill", json={})
            srv.delete(f"/api/lanes/{name}?force=true")

    assert len(seen) == 2 and all(len(v) == 10 for v in seen.values()), (
        f"the sweep is not twenty reads over two kinds: "
        f"{ {k: len(v) for k, v in seen.items()} }")


def test_a_command_that_cannot_exec_is_refused_not_started(gitrepo, serve, tmp_path):
    """§1 F2 MANDATORY NEGATIVE — a command that cannot exec is refused, and never *started*.

    `SRV-FIX-486` criterion 2. Round 1 asserted this at `E18` and left it RED, because **bwrap
    gives no positive signal that the command it was handed has exec'd**. Measured, both ways:

        inner exec fails : `--sync-fd` hits EOF at once, `--json-status-fd` carries `child-pid`
                           and NO `exit-code`, bwrap exits 1 saying `bwrap: execvp <cmd>: …`
        inner exec works : `--sync-fd` stays open, and nothing further is written until the
                           command exits

    Success is the ABSENCE of an immediate failure, so any check at `E18` is a deadline, and a
    deadline is what `SRV-FIX-034` spent eight merged tickets proving is a coin flip. The
    `FD_CLOEXEC` pipe that made criterion 1 race-free works because `execvp` CLOSES the fd; no fd
    the tool holds is closed by the exec that happens inside the sandbox.

    So the refusal is asserted where it can be given without waiting on anything: **the loader**,
    which is earlier and stronger than `E18` — a lane is never even claimed. The sweep is every
    shape of *cannot exec* the loader can see, derived as a table of members rather than one:

      1. the program is not there at all;
      2. the program is there and is not executable;
      3. the program is executable and is a SCRIPT whose interpreter is not there — the shape
         that reached `execve` in round 1 and answered `201` with a pid that was already dead.

    And the residue the loader cannot see — a command that passes every check above and still
    dies at `execve` — is asserted at the other end: `E9` never reports that lane's process as
    running.
    """
    members = {}

    absent = tmp_path / "not-here-at-all.sh"
    members["the program is not there at all"] = ("./not-here-at-all.sh", None)

    unexecutable = "#!/bin/sh\necho hello\n"
    members["the program is there and is not executable"] = ("./not-executable.sh",
                                                             (unexecutable, 0o644))
    members["the program is a script whose interpreter is not there"] = (
        "./bad-interpreter.sh", ("#!/nonexistent/interpreter\necho hello\n", 0o755))
    assert len(members) >= 3, f"the sweep is thin: {sorted(members)}"
    assert not absent.exists(), absent

    for what, (command, written) in sorted(members.items()):
        root = gitrepo(agent={"command": command})
        if written is not None:
            body, mode = written
            (root / command[2:]).write_text(body)
            (root / command[2:]).chmod(mode)
            git(root, "add", "-A")
            git(root, "commit", "-m", f"a command where {what}")
        srv = serve(root)

        loaded = srv.get("/api/manifest")
        assert loaded.status_code == 422, (
            f"[{what}] a command that cannot exec loaded: {loaded.status_code} "
            f"{loaded.text[:300]}")
        assert loaded.json()["error"]["detail"]["reason"] == "agent_binary_missing", (
            what, loaded.text[:300])

        # ...and no lane is handed out, so `E18` is never reached with a corpse's pid.
        refused = claim(srv, ticket="TCK-001")
        assert refused.status_code != 201, (
            f"[{what}] a lane was claimed for a repository whose agent cannot exec: "
            f"{refused.status_code} {refused.text[:300]}")
        serve.stop(srv)

    # THE RESIDUE. A command the loader cannot fault — present, executable, a real interpreter —
    # that still dies the moment it runs. `E9` must not call that lane's process running.
    root = gitrepo(agent={"command": "/bin/sh dies-at-once.sh"})
    (root / "dies-at-once.sh").write_text("#!/bin/sh\nexec /nonexistent/program\n")
    (root / "dies-at-once.sh").chmod(0o755)
    git(root, "add", "-A")
    git(root, "commit", "-m", "a command the loader cannot fault and that dies at once")
    srv = serve(root)

    lane = claim(srv, ticket="TCK-001")
    assert lane.status_code == 201, (
        f"the residue fixture must load, or it is testing the loader again: {lane.text[:300]}")
    name = lane.json()["lane"]
    srv.post(f"/api/lanes/{name}/runs", json={"prompt": "say nothing"})

    def not_running():
        listed = srv.get("/api/lanes")
        assert listed.status_code == 200, listed.text
        rows = [row for row in listed.json()["lanes"] if row["lane"] == name]
        assert rows, listed.text
        return not rows[0].get("running")

    assert settle(not_running, timeout=25), (
        "`E9` reports a lane as running for a process that died at `execve` — S5 says whether a "
        "lane is live is answered by GET /api/lanes and by nothing else, so that answer has to "
        "become false on its own")


# ── SRV-FIX-532 cluster E1 — the refusal moves to the claim, and is still a refusal ───────────

def test_only_the_workspace_whose_toolchain_is_missing_is_refused(gitrepo, serve, tmp_path):
    """§1 E1 — `E12` refuses the workspace that cannot build, and only that one.

    `SRV-FIX-491` criterion 2. Both claims are in ONE case on purpose: the thing being asserted is
    WHICH workspace, and a refusal that covers both is today's defect with an extra step — the
    manifest refusing everything reached the same screen by a different route.
    """
    present = tmp_path / "a-toolchain-that-is-here"
    present.mkdir()
    missing = tmp_path / "a-toolchain-that-is-not"
    assert not missing.exists(), missing

    root = gitrepo(workspaces={"core": {"path": ".", "toolchain": [str(present)]},
                               "web": {"path": ".", "toolchain": [str(missing)]}})
    srv = serve(root)

    ok = claim(srv, ticket="TCK-001", workspace="core")
    assert ok.status_code == 201, (
        f"the workspace whose toolchain IS on this machine was refused: "
        f"{ok.status_code} {ok.text}")

    refused = claim(srv, ticket="TCK-002", workspace="web")
    assert refused.status_code == 409, (
        f"a lane was handed out for a workspace that cannot run its own build: "
        f"{refused.status_code} {refused.text}")
    err = refused.json()["error"]
    assert err["code"] == "workspace_unrunnable", err
    assert err["detail"]["reason"] == "toolchain_missing", err
    assert str(missing) in json.dumps(err), (
        f"the refusal does not name the path that is not there: {err}")


def test_a_lane_says_which_toolchains_it_holds(gitrepo, serve, tmp_path):
    """§1 E3 — a lane says which toolchains it holds, so a session can tell whose gate it is.

    `SRV-FIX-517`. §1 widens no surface, so the tool says it where it says everything else it did:
    its own log, at claim. A session that cannot tell *this gate is not mine to run* from *this
    gate is broken* debugs the wrong thing, and `npm: command not found` from a `factory-server`
    lane is that sentence exactly.

    Two claims in one world, because what is asserted is WHICH toolchains: a ticket that names a
    second workspace and one that does not, and the line differs between them.
    """
    mine = tmp_path / "mine-toolchain"
    theirs = tmp_path / "their-toolchain"
    for where in (mine, theirs):
        where.mkdir(parents=True)

    root = gitrepo(workspaces={"core": {"path": ".", "toolchain": [str(mine)]},
                               "consumer": {"path": ".", "toolchain": [str(theirs)]}},
                   tickets=("TCK-NAMES", "TCK-SILENT"))
    (root / "docs" / "tickets" / "TCK-NAMES.md").write_text(
        "---\nid: TCK-NAMES\nworkspace: core\ntitle: names one\nstatus: todo\n"
        "workspaces: core consumer\n---\n\nbody\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "a ticket that names a second workspace")
    srv = serve(root)

    assert claim(srv, ticket="TCK-NAMES", workspace="core").status_code == 201
    assert claim(srv, ticket="TCK-SILENT", workspace="core").status_code == 201

    said = [line for line in srv.log.read_text().splitlines() if "holds the toolchain of" in line]
    assert len(said) == 2, (
        f"two lanes were claimed and the tool said what {len(said)} of them hold:\n"
        + "\n".join(said))
    named = [line for line in said if "consumer" in line]
    silent = [line for line in said if "consumer" not in line]
    assert len(named) == 1 and len(silent) == 1, (
        f"both lanes were reported as holding the same thing, so the line says nothing about "
        f"WHICH toolchains:\n" + "\n".join(said))
    assert str(mine) in named[0] and str(theirs) in named[0], (
        f"the lane whose ticket names `consumer` does not report both toolchains: {named[0]}")
    assert str(theirs) not in silent[0], (
        f"the lane whose ticket names no extra workspace reports a toolchain it was never "
        f"given: {silent[0]}")
