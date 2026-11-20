"""SRV-FEAT-028 — F30: the screen shows what happened, not what the document claims.

`SPEC.md` §3.5. Every case builds its own git repository and points a server at it, because the
answer this module gives is a fact about a repository's history and a test that only passes
against the repository it was written in has learned that repository.

The document's `status:` field is a claim written by a session that may not have survived to
write it truthfully. These cases are the ones that actually happened here: a ticket that said
`pass` with nothing pushed, a QA session that died leaving `awaiting-qa` behind, and a first
version of this check that called five merged tickets unshipped because their branches were gone.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest


REQ_DIR = "product/requirements"
TKT_DIR = "product/work-items"
RPT_DIR = "product/deliveries"

# The set SPEC.md §3.5 permits. Nothing here writes, and the tool refuses anything outside it.
READ_ONLY_GIT = ["for-each-ref", "ls-tree", "rev-list", "rev-parse", "status", "worktree"]


def manifest_for(agent_command="claude", lanes=0):
    m = {
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        # Which `status:` values mean a session was launched. The tool has no default for this
        # and no opinion about the words: without the declaration nothing is ever `stalled`.
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": agent_command},
    }
    if lanes:
        m["lanes"] = {"host": lanes}
        m["stages"] = ["SPEC", "IMPL", "GATE"]
    return m


def doc(front, body="Body."):
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class Git:
    """The synthetic repository a case is about, built commit by commit."""

    def __init__(self, root):
        self.root = root

    def __call__(self, *args, **kw):
        out = subprocess.run(["git", "-C", str(self.root), *args],
                             capture_output=True, text=True, timeout=30, **kw)
        assert out.returncode == 0, f"git {' '.join(args)} → {out.returncode}\n{out.stderr}"
        return out.stdout.strip()

    def init(self):
        self("init", "-b", "main")
        self("config", "user.email", "t@example.invalid")
        self("config", "user.name", "Test")
        self("config", "commit.gpgsign", "false")
        return self

    def commit(self, message):
        self("add", "-A")
        self("commit", "-m", message, "--no-gpg-sign")
        return self("rev-parse", "HEAD")


@pytest.fixture
def gitrepo(repo):
    """`repo`, with a git history — and with the probe running fast enough to watch."""
    return Git(repo.root)


def serve_fast(serve, root, **env):
    """A server whose delivery probe re-reads the world often enough for a test to see a change.

    The interval is a knob for exactly this reason and for no other: the product's own default is
    seconds, and a test that slept for it would be a test nobody runs.
    """
    return serve(root, FACTORY_DELIVERY_POLL_MS="200", **env)


def ok(srv, path):
    r = srv.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"
    return r.json()


def delivery_of(srv, ticket_id):
    return ok(srv, f"/api/tickets/{ticket_id}")["delivery"]


def wait_for_state(srv, ticket_id, state, timeout=10):
    """The probe looks at the world on its own schedule; this waits for it to have looked."""
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        seen = delivery_of(srv, ticket_id)
        if seen["state"] == state:
            return seen
        time.sleep(0.1)
    raise AssertionError(f"{ticket_id} never reached {state!r}; last was {seen}")


def base_repo(repo, gitrepo, tickets):
    """A requirement, some tickets, a manifest, and one commit on `main` holding all of it."""
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-truth.md",
               doc({"id": "REQ-001", "title": "The board tells the truth", "status": "draft"}))
    for front in tickets:
        repo.write(f"{TKT_DIR}/{front['id']}.md", doc(front))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")
    return repo


# ── the case that has happened twice ──────────────────────────────────────────────────────────

def test_a_document_claiming_pass_with_nothing_pushed_is_unshipped(repo, gitrepo, serve):
    """`pass` in the frontmatter, commits on the branch, nothing on a remote.

    Two tickets in this repository said exactly this while their work sat in a worktree. The
    document is not evidence; the branch is.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
         "title": "Ship the thing", "status": "pass"},
    ])
    gitrepo("checkout", "-b", "ticket/TKT-1")
    repo.write("src/a.txt", "one\n")
    gitrepo.commit("TKT-1: the first half")
    repo.write("src/b.txt", "two\n")
    gitrepo.commit("TKT-1: the second half")
    gitrepo("checkout", "main")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-1", "unshipped")

    assert d["state"] == "unshipped", d
    assert "2" in d["detail"], f"detail must name the commit count: {d['detail']!r}"
    assert d["needs_you"] is True, d


# ── a session that was launched and is gone ───────────────────────────────────────────────────

def test_a_launched_session_that_died_is_stalled(repo, gitrepo, serve):
    """`awaiting-qa`, no live process, nothing committed since the launch.

    Three of the first four QA runs here ended like this. The document is the only thing that
    says a session was launched, and it is used for that one fact and for nothing else.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-2", "parent": "REQ-001", "workspace": "alpha",
         "title": "Hand it to QA", "status": "awaiting-qa"},
    ])
    # A branch was claimed and holds nothing beyond what is already on main — and then main
    # moved on underneath it, which makes the branch an ANCESTOR of main and therefore
    # "merged" into it by every reading of `git branch --merged`.
    #
    # This ticket's own branch read `done` on the first run against a real repository for
    # exactly that reason: nothing had been committed on it and main had advanced. Being an
    # ancestor of the base branch is not delivery, and a lane that was claimed and abandoned is
    # the case this whole feature exists to catch.
    gitrepo("branch", "ticket/TKT-2")
    repo.write("src/unrelated.txt", "somebody else's work\n")
    gitrepo.commit("something else entirely")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-2", "stalled")

    assert d["state"] == "stalled", d
    assert d["claimed"] == "awaiting-qa", d
    assert d["needs_you"] is True, d


# ── the false positive the prototype shipped with ─────────────────────────────────────────────

def test_a_merged_ticket_is_done_even_with_its_branch_deleted(repo, gitrepo, serve):
    """The forge deletes the branch on merge, and the ticket is still done.

    `factory/scripts/ticket-status.py` called five merged tickets `unshipped` for exactly this
    reason before it learned to look for the ticket's own artefacts on the base branch.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-3", "parent": "REQ-001", "workspace": "alpha",
         "title": "Land it", "status": "in-progress"},
    ])
    gitrepo("checkout", "-b", "ticket/TKT-3")
    repo.write(f"{RPT_DIR}/TKT-3-report.md", "# TKT-3 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-3: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-3", "ticket/TKT-3")
    gitrepo("branch", "-D", "ticket/TKT-3")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-3", "done")

    assert d["state"] == "done", d
    assert d["needs_you"] is False, d
    # the document still says in-progress, and that is the disagreement worth showing
    assert d["claimed"] == "in-progress", d
    assert d["disagrees"] is True, d


# ── SRV-FIX-453 · `running` is a lane the tool started, and nothing else ──────────────────────
#
# `SPEC.md` §3.5 rule 1 used to read *a live process whose program is the manifest's
# `agent.command` carries the ticket id on its command line*, and `probe.cpp` implemented exactly
# that by walking `/proc`. That set is not the set of lanes: it contains the operator's own
# session when its prompt names the ticket, a QA session started outside any lane, and any other
# process on the machine whose argv happens to name the id — and it does NOT contain a lane whose
# agent runs under a different program name, which is how a lane `running · producing` read
# `unshipped` on the same screen.
#
# `MODEL.md` §2 `S5` already says who answers this: *whether a lane is live is answered by
# `GET /api/lanes` and by nothing else*. So the cases below keep BOTH sources alive at once and
# make them disagree, because a fixture in which they agree passes identically whichever one the
# code read.

# The `result` frame a real `claude 2.1.92` printed, taken verbatim from the committed capture
# `fixtures/cli-stream-2026-07-27-simple.jsonl`. A stand-in that ended its turn some other way
# would agree with this repository rather than with the CLI, which is the shape `SRV-FEAT-009`
# shipped and `docs/operations/ESCAPES.md` records; re-capture by re-recording that file.
CAPTURED_RESULT_FRAME = next(
    line for line in reversed(
        (Path(__file__).resolve().parent / "fixtures"
         / "cli-stream-2026-07-27-simple.jsonl").read_text().splitlines())
    if '"type":"result"' in line)

# A lane agent that starts, stays up, and never ends its turn: `E9` reports `running`.
AGENT_STAYS_UP = """#!/bin/sh
while : ; do sleep 0.2 ; done
"""

# The same, after printing the captured `result` frame — its turn ends while its process is
# alive, which is `E9`'s `idle` (`SPEC.md` §3.2).
AGENT_ENDS_ITS_TURN = """#!/bin/sh
cat ./turn.json
while : ; do sleep 0.2 ; done
"""


def lane_repo(repo, gitrepo, tickets, agent_sh=AGENT_STAYS_UP):
    """`base_repo`, plus what a lane needs: seats, stages, and an agent a lane can really run.

    `agent.command` is the bare word `claude`, resolved on the server's PATH. That is not a
    convenience — it is the shape the defect needs: `probe.cpp` compared a process's program
    BASENAME against the whole of `agent.command`, so only a one-word command could ever have
    matched, and only a one-word command lets the lookalike these cases spawn look like the
    real thing.
    """
    repo.manifest(manifest_for(lanes=2))
    repo.write(".gitignore",
               ".factory/worktrees/\n.factory/runs/\n.factory/lanes/\n.factory/ids.json\n")
    repo.write("bin/claude", agent_sh).chmod(0o755)
    repo.write("turn.json", CAPTURED_RESULT_FRAME + "\n")
    repo.write(f"{REQ_DIR}/REQ-001-truth.md",
               doc({"id": "REQ-001", "title": "The board tells the truth", "status": "draft"}))
    for front in tickets:
        repo.write(f"{TKT_DIR}/{front['id']}.md", doc(front))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")
    return repo


def serve_with_lanes(serve, repo, **env):
    """`serve_fast`, with the repository's own `bin/` first on PATH so `claude` resolves there."""
    return serve_fast(serve, repo.root,
                      PATH=f"{repo.root}/bin:{os.environ['PATH']}", **env)


def lookalike(tmp_path_factory, ticket_id, program="claude"):
    """A live process the tool did not start, named after the agent, naming the ticket.

    A real binary copied under that name, not a shebang script: the kernel rewrites a script's
    argv to its interpreter, so `#!/bin/sh` would make this read as `sh` — and the whole point is
    which program the process is actually running. It is built OUTSIDE the repository, because a
    file inside it would dirty the checkout the same cases read delivery from.
    """
    d = tmp_path_factory.mktemp("lookalike")
    shutil.copy("/bin/sh", d / program)
    return subprocess.Popen([str(d / program), "-c",
                             f"sleep 300  # -p /run-ticket {ticket_id}"])


def claim_and_start(srv, ticket_id, workspace="alpha"):
    """`E12` then `E18` — the two calls that make a lane the tool owns, and its run."""
    claimed = srv.post("/api/lanes", json={"ticket_id": ticket_id, "workspace": workspace})
    assert claimed.status_code == 201, f"E12 → {claimed.status_code} {claimed.text}"
    lane = claimed.json()["lane"]
    started = srv.post(f"/api/lanes/{lane}/runs", json={})
    assert started.status_code == 201, f"E18 → {started.status_code} {started.text}"
    return lane


def lane_row(srv, lane):
    for row in ok(srv, "/api/lanes")["lanes"]:
        if row["lane"] == lane:
            return row
    raise AssertionError(f"E9 does not list {lane}")


def wait_for_process(srv, lane, process, timeout=25):
    """`E9`'s own `state.process`, which `S5` makes the only authority on whether a lane is live."""
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        seen = lane_row(srv, lane)["state"]["process"]
        if seen == process:
            return seen
        time.sleep(0.1)
    raise AssertionError(f"{lane} never reached process {process!r}; last was {seen!r}")


def wait_for_not_state(srv, ticket_id, state, timeout=25):
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        seen = delivery_of(srv, ticket_id)
        if seen["state"] != state:
            return seen
        time.sleep(0.1)
    raise AssertionError(f"{ticket_id} never left {state!r}; last was {seen}")


def next_delivery_frame(client, ticket_id, accept, timeout=30):
    """The next `M2 lane.state` frame for this ticket whose `delivery.state` the caller accepts.

    Nothing here re-reads `E3`: the only bytes this assertion looks at arrived on the socket.
    """
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        client.sock.settimeout(max(0.05, deadline - time.time()))
        try:
            frame = client.recv()
        except (socket.timeout, OSError):
            break
        if frame.get("m") != "lane.state" or frame.get("ticket_id") != ticket_id:
            continue
        value = (frame.get("delivery") or {}).get("state")
        seen.append(value if "delivery" in frame else "(lane-shaped frame, no delivery)")
        if accept(value):
            return frame
    raise AssertionError(
        f"no M2 lane.state for {ticket_id} carrying an accepted delivery.state; saw {seen}")


def test_running_is_a_lane_the_tool_started_and_a_lookalike_is_not(
        repo, gitrepo, serve, tmp_path_factory):
    """Two sources that used to agree are forced apart, and only the tool's own counts.

    A process named after the manifest's agent, carrying the ticket id in its argv, is alive for
    the whole case — the only thing the deleted rule could ever see, and it never moves. Every
    change in `delivery.state` below is therefore a lane the tool started or stopped, and a
    fixture in which the two sources agree could not have told them apart.
    """
    lane_repo(repo, gitrepo, [
        {"id": "TKT-40", "parent": "REQ-001", "workspace": "alpha",
         "title": "Do the work", "status": "in-progress"},
    ])
    stray = lookalike(tmp_path_factory, "TKT-40")
    lane = None
    try:
        srv = serve_with_lanes(serve, repo)
        assert stray.poll() is None, "the lookalike died before the case started"

        # 1 · the lookalike is the only such process, and it is not evidence of anything
        alone = wait_for_state(srv, "TKT-40", "stalled")
        assert alone["state"] != "running", (
            f"a `claude` this tool did not start reported work in progress: {alone}")

        # 2 · a lane the tool claimed and started IS
        lane = claim_and_start(srv, "TKT-40")
        live = wait_for_state(srv, "TKT-40", "running")
        assert lane in live["detail"], (
            f"`running` must name the lane holding it: {live}")
        assert stray.poll() is None

        # 3 · and the ticket leaves `running` when that lane's run is killed, while the
        #     lookalike is still alive
        killed = srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
        assert killed.status_code in (200, 202), f"E17 → {killed.status_code} {killed.text}"
        gone = wait_for_not_state(srv, "TKT-40", "running")
        assert gone["state"] != "running", gone
        assert stray.poll() is None, (
            "the lookalike died during the case, so nothing here was forced apart")
    finally:
        stray.kill()
        stray.wait(timeout=5)


def parent_of(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    return int(raw.rsplit(")", 1)[1].split()[1])


def agent_pid_in(srv, worktree):
    """The pid the tool spawned for this lane, read off the machine rather than off a response.

    `E9` publishes the DERIVATION of that pid — `state.process` — and not the number, so the
    number is taken from `/proc` the way `conftest.owned_confinement_helpers` does: only helpers
    descended from a server THIS pytest session started, named after this lane's worktree, and
    then the one the SERVER forked. `--unshare-pid` makes the helper fork a child of its own, and
    the record holds the outer pid; anything else on the machine is somebody's real work.
    """
    from conftest import owned_confinement_helpers

    deadline = time.time() + 25
    found = []
    while time.time() < deadline:
        found = [pid for pid, cmd in owned_confinement_helpers().items()
                 if str(worktree) in cmd and parent_of(pid) == srv.pid]
        if len(found) == 1:
            return found[0]
        time.sleep(0.1)
    raise AssertionError(
        f"no single helper the server forked for {worktree}; found {found}")


def test_a_lane_whose_agent_died_is_never_running(repo, gitrepo, serve):
    """The process `E9` reports for a started lane is SIGKILLed, and the two answers agree.

    `E9` is asked FIRST and the ticket second, so a badge still saying `running` after `E9` has
    said `crashed` is a real disagreement and not an artefact of the reading order.
    """
    lane_repo(repo, gitrepo, [
        {"id": "TKT-41", "parent": "REQ-001", "workspace": "alpha",
         "title": "Die mid-run", "status": "in-progress"},
    ])
    srv = serve_with_lanes(serve, repo)
    lane = claim_and_start(srv, "TKT-41")
    wait_for_process(srv, lane, "running")

    worktree = lane_row(srv, lane)["worktree"]
    # The group, not the leader alone. `lane/git.cpp:365` calls `setsid()` before exec, so the
    # process the tool started IS a session of its own, and that is what dies when an agent dies.
    # Signalling only the leader leaves the sandbox's inner init holding the stdout pipe open —
    # measured, and filed as SRV-FIX-459, which is a lane defect and not this ticket's.
    os.killpg(agent_pid_in(srv, worktree), signal.SIGKILL)

    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        process = lane_row(srv, lane)["state"]["process"]
        badge = delivery_of(srv, "TKT-41")
        last = (process, badge)
        if process == "crashed":
            assert badge["state"] != "running", (
                f"E9 says the lane crashed and the badge still says running: {last}")
            return
        time.sleep(0.1)
    raise AssertionError(f"E9 never reported the killed lane as crashed; last was {last}")


def test_an_idle_lane_is_still_running_for_delivery(repo, gitrepo, serve):
    """`idle` is an alive process with its turn over, and the badge answers *is it alive*."""
    lane_repo(repo, gitrepo, [
        {"id": "TKT-42", "parent": "REQ-001", "workspace": "alpha",
         "title": "End the turn", "status": "in-progress"},
    ], agent_sh=AGENT_ENDS_ITS_TURN)
    srv = serve_with_lanes(serve, repo)
    lane = claim_and_start(srv, "TKT-42")

    wait_for_process(srv, lane, "idle")
    badge = delivery_of(srv, "TKT-42")
    assert badge["state"] == "running", (
        f"a lane whose agent is alive with its turn over is still work in progress: {badge}")
    assert lane in badge["detail"], badge


def test_an_idle_lane_on_a_landed_pass_is_done(repo, gitrepo, serve):
    """SRV-FIX-600 — a session that finished its turn is not work in progress once the work landed.

    `AGENT_ENDS_ITS_TURN` ignores the end of its stdin, so its session is still up, `idle`, when
    the badge is read — the case above with the ticket passed and its work merged into `main`.
    """
    lane_repo(repo, gitrepo, [
        {"id": "TKT-44", "parent": "REQ-001", "workspace": "alpha",
         "title": "Land with the session up", "status": "in-progress"},
    ], agent_sh=AGENT_ENDS_ITS_TURN)
    gitrepo("checkout", "-b", "ticket/TKT-44")
    repo.write(f"{RPT_DIR}/TKT-44-report.md", "# TKT-44 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-44: deliver")
    gitrepo("checkout", "main")
    srv = serve_with_lanes(serve, repo)
    lane = claim_and_start(srv, "TKT-44")
    wait_for_process(srv, lane, "idle")

    gitrepo("merge", "--no-ff", "-m", "land TKT-44", "ticket/TKT-44")
    repo.write(f"{TKT_DIR}/TKT-44.md", doc({
        "id": "TKT-44", "parent": "REQ-001", "workspace": "alpha",
        "title": "Land with the session up", "status": "pass"}))
    gitrepo.commit("TKT-44: pass")

    time.sleep(2)
    badge = delivery_of(srv, "TKT-44")
    process = lane_row(srv, lane)["state"]["process"]
    assert (badge["state"], process) == ("done", "idle"), (
        f"the work is on main and the ticket says pass, and the badge reads {badge} while the "
        f"lane's session is {process!r}")


def test_delivery_running_follows_the_lane_on_the_socket(repo, gitrepo, serve):
    """`M2` carries the change, and no surface has to re-read `E3` to hear it."""
    lane_repo(repo, gitrepo, [
        {"id": "TKT-43", "parent": "REQ-001", "workspace": "alpha",
         "title": "Say it on the wire", "status": "in-progress"},
    ])
    srv = serve_with_lanes(serve, repo)
    client = srv.ws()
    try:
        client.send({"m": "subscribe", "scope": {"requirements": True}})
        client.wait_for("snapshot")

        lane = claim_and_start(srv, "TKT-43")
        entered = next_delivery_frame(client, "TKT-43", lambda s: s == "running")
        assert lane in entered["delivery"]["detail"], entered
        assert entered["lane"] is None, "the ticket shape of M2 carries lane: null"

        killed = srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
        assert killed.status_code in (200, 202), f"E17 → {killed.status_code} {killed.text}"
        left = next_delivery_frame(client, "TKT-43", lambda s: s not in (None, "running"))
        assert left["delivery"]["state"] != "running", left
    finally:
        client.close()


# ── negative (mandatory) · the case that asserted the defect as a feature ─────────────────────

def test_a_foreign_agent_process_naming_the_ticket_is_not_running(
        repo, gitrepo, serve, tmp_path_factory):
    """A live `claude` naming the ticket, that this tool did not start, is not work in progress.

    This is `SRV-FEAT-028`'s `test_a_live_session_is_running_and_a_stray_process_is_not`, kept and
    inverted rather than deleted: the same lookalike, the same worktree, and the assertion the
    other way round. That case BUILT the process this ticket refuses — `/bin/sh` copied to a file
    named `claude`, run with the id in its argv — and asserted the ticket became `running`. It
    could not tell the proxy from the thing, because nothing in it ever claimed a lane.

    The dev server it also spawned stays: a `vite` living in the ticket's worktree is still not
    work in progress, now for the reason this ticket gives rather than because of its name.
    """
    lane_repo(repo, gitrepo, [
        {"id": "TKT-44", "parent": "REQ-001", "workspace": "alpha",
         "title": "Do the work", "status": "in-progress"},
    ])
    gitrepo("branch", "ticket/TKT-44")

    dev_server = tmp_path_factory.mktemp("devserver")
    shutil.copy("/bin/sh", dev_server / "vite")
    stray = subprocess.Popen([str(dev_server / "vite"), "-c",
                              "sleep 300  # preview --root /tmp/worktrees/TKT-44/client"])
    agent = lookalike(tmp_path_factory, "TKT-44")
    try:
        srv = serve_with_lanes(serve, repo)
        seen = wait_for_state(srv, "TKT-44", "stalled")
        assert seen["state"] != "running", (
            f"a `claude` naming TKT-44 that no lane holds reported work in progress: {seen}")
        assert stray.poll() is None and agent.poll() is None, (
            "a stray died before the assertion, so this proved nothing")
    finally:
        for p in (stray, agent):
            p.kill()
            p.wait(timeout=5)


# ── the disagreement is the finding ───────────────────────────────────────────────────────────

def test_the_derived_state_wins_and_the_disagreement_is_reported(repo, gitrepo, serve):
    """Document says `pass`, git says `unshipped`: the response carries both and marks them."""
    base_repo(repo, gitrepo, [
        {"id": "TKT-5", "parent": "REQ-001", "workspace": "alpha",
         "title": "Claim it", "status": "pass"},
    ])
    gitrepo("checkout", "-b", "ticket/TKT-5")
    repo.write("src/c.txt", "three\n")
    gitrepo.commit("TKT-5: work")
    gitrepo("checkout", "main")

    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-5", "unshipped")

    ticket = ok(srv, "/api/tickets/TKT-5")
    assert ticket["status"] == "pass", "the document's own field is reported untouched"
    assert ticket["delivery"]["state"] == "unshipped", ticket["delivery"]
    assert ticket["delivery"]["claimed"] == "pass", ticket["delivery"]
    assert ticket["delivery"]["disagrees"] is True, ticket["delivery"]

    # and the same pair reaches the two list surfaces, which is where a person reads it
    row = ok(srv, "/api/requirements/REQ-001")["groups"][0]["tickets"][0]
    assert row["delivery"]["state"] == "unshipped" and row["delivery"]["disagrees"] is True, row
    req = ok(srv, "/api/requirements")["requirements"][0]
    assert req["delivery"]["state"] == "unshipped", req["delivery"]

    # the tool does not edit the repository it is pointed at
    on_disk = (repo.root / TKT_DIR / "TKT-5.md").read_text()
    assert "status: pass" in on_disk, "a status field the tool corrects is a second writer"


# ── §3.3 ──────────────────────────────────────────────────────────────────────────────────────

def test_needs_you_counts_stalled_and_unshipped(repo, gitrepo, serve):
    """The counter in the top bar and the requirement's pill include both."""
    base_repo(repo, gitrepo, [
        {"id": "TKT-6", "parent": "REQ-001", "workspace": "alpha",
         "title": "Stalled one", "status": "awaiting-qa"},
        {"id": "TKT-7", "parent": "REQ-001", "workspace": "alpha",
         "title": "Unshipped one", "status": "pass"},
        {"id": "TKT-8", "parent": "REQ-001", "workspace": "alpha",
         "title": "Untouched one", "status": "todo"},
    ])
    gitrepo("checkout", "-b", "ticket/TKT-7")
    repo.write("src/d.txt", "four\n")
    gitrepo.commit("TKT-7: work")
    gitrepo("checkout", "main")

    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-7", "unshipped")
    wait_for_state(srv, "TKT-6", "stalled")
    assert delivery_of(srv, "TKT-8")["state"] == "not_started"

    req = ok(srv, "/api/requirements")["requirements"][0]
    assert req["needs_you"]["count"] == 2, req["needs_you"]
    assert req["needs_you"]["label"] == "2 need you", req["needs_you"]

    detail = ok(srv, "/api/requirements/REQ-001")
    assert detail["needs_you"]["count"] == 2, detail["needs_you"]

    filtered = ok(srv, "/api/requirements?filter=needs")
    assert [r["id"] for r in filtered["requirements"]] == ["REQ-001"], filtered


# ── negative (mandatory) ──────────────────────────────────────────────────────────────────────

def test_a_repository_with_no_git_still_serves(repo, serve):
    """Not a git repository: every endpoint answers, says it cannot tell, and nothing is 500.

    The screen is what the person came for. Degrading to "I cannot tell you" keeps the rest of
    the row on screen; degrading to a 500 takes the whole surface away.
    """
    base = repo.root
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-truth.md",
               doc({"id": "REQ-001", "title": "No history here", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-9.md",
               doc({"id": "TKT-9", "parent": "REQ-001", "workspace": "alpha",
                    "title": "Nothing to see", "status": "pass"}))
    assert not (base / ".git").exists()

    srv = serve_fast(serve, base)

    health = ok(srv, "/api/health")
    assert health["delivery"]["available"] is False, health["delivery"]
    assert health["delivery"].get("reason"), "it must say WHY it cannot tell"

    paths = ["/api/health", "/api/manifest", "/api/requirements",
             "/api/requirements/REQ-001", "/api/tickets/TKT-9"]
    for path in paths:
        r = srv.get(path)
        assert r.status_code != 500, f"{path} answered 500: {r.text}"
        assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"

    for body in (ok(srv, "/api/requirements")["requirements"][0],
                 ok(srv, "/api/requirements/REQ-001")["groups"][0]["tickets"][0],
                 ok(srv, "/api/tickets/TKT-9")):
        assert body["delivery"]["state"] == "unknown", body["delivery"]
        assert body["delivery"].get("reason"), body["delivery"]
        assert body["delivery"]["needs_you"] is False, body["delivery"]


# ── read-only, and provably ───────────────────────────────────────────────────────────────────

def test_only_read_only_git_subcommands_are_used(repo, gitrepo, serve):
    """The set is served so it can be asserted, and the log shows nothing outside it."""
    base_repo(repo, gitrepo, [
        {"id": "TKT-10", "parent": "REQ-001", "workspace": "alpha",
         "title": "Read it", "status": "todo"},
    ])
    gitrepo("checkout", "-b", "ticket/TKT-10")
    repo.write("src/e.txt", "five\n")
    gitrepo.commit("TKT-10: work")
    gitrepo("checkout", "main")

    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-10", "unshipped")

    health = ok(srv, "/api/health")
    assert health["delivery"]["available"] is True, health["delivery"]
    assert health["delivery"]["git"] == READ_ONLY_GIT, health["delivery"]

    # and what it actually ran, from the running server's own log
    log = srv.log.read_text()
    used = set()
    for line in log.splitlines():
        if "delivery: pass used git" not in line:
            continue
        # glog's own prefix ends in `]`, so the list is found forwards from its `[`
        start = line.index("[")
        used.update(json.loads(line[start:line.index("]", start) + 1]))
    assert used, f"the probe never reported which subcommands it ran:\n{log[-2000:]}"
    assert used <= set(READ_ONLY_GIT), f"a subcommand outside the allowlist ran: {used}"

    # the repository is untouched: no work tree change, no new ref, no reflog entry
    dirty = subprocess.run(["git", "-C", str(repo.root), "status", "--porcelain"],
                           capture_output=True, text=True).stdout
    assert dirty == "", f"the tool changed the repository it was pointed at:\n{dirty}"


def test_a_ticket_id_is_not_matched_as_a_prefix_of_another(repo, gitrepo, serve, tmp_path):
    """SRV-FIX-038. `"TKT-1"` is a substring of `"TKT-155"`.

    `probe.cpp` decided *"somebody is working on this ticket now"* with a bare `find()` over every
    agent command line on the host, and rule 1 outranks everything — so while any process anywhere
    mentioned `TKT-155`, ticket `TKT-1` reported `running` and silently replaced whatever git
    actually showed.

    Found by `SRV-FIX-034`'s gate, which met it as a failure it had not caused: the browser gate's
    own fixture leaves a `# /run-ticket TKT-155` process alive for ten minutes, so any server suite
    overlapping a client gate sees it. It gets worse with every ticket this repository files —
    `TKT-1` today, `TKT-15` at a hundred and fifty.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
         "title": "Ship the thing", "status": "pass"},
    ])
    gitrepo("checkout", "-b", "ticket/TKT-1")
    repo.write("src/a.txt", "one\n")
    gitrepo.commit("TKT-1: the first half")
    gitrepo("checkout", "main")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy("/bin/sh", bin_dir / "claude")

    # A real agent, working on a DIFFERENT ticket whose id happens to start with this one's.
    other = subprocess.Popen([str(bin_dir / "claude"), "-c",
                              "sleep 300  # -p /run-ticket TKT-155"])
    try:
        srv = serve_fast(serve, repo.root)
        d = wait_for_state(srv, "TKT-1", "unshipped")
        assert d["state"] != "running", (
            f"TKT-155 is being worked on and TKT-1 reports somebody working on IT: {d}")
        assert d["state"] == "unshipped", (
            f"TKT-1 has a commit no remote has, and the answer came from a process named after a "
            f"different ticket instead: {d}")
    finally:
        other.terminate()
        other.wait(timeout=10)


# ── SRV-FIX-128 · a `git status` that failed is not a clean worktree ──────────────────────────

def test_a_failed_git_status_is_not_a_clean_worktree(repo, gitrepo, serve):
    """`dirty = status.ok && !status.text.empty()` reads *could not ask* as *nothing to report*.

    Uncommitted changes are the strongest local evidence this module has — nothing on a remote or
    a forge can bear on them — and a `git status` that fails silently discards it. `index.lock`
    held by another process, a timeout, or a directory chmod'd while a person works in the same
    checkout are all ordinary.

    Measured before the fix, same repository, one second apart:

        readable   -> unshipped · "uncommitted changes in …/wt-50"
        unreadable -> stalled   · "no process is running and nothing new is committed"

    The uncommitted file never moved. The second row is a statement about a worktree nobody could
    look at, and it is the reason `SRV-FIX-105` gave the reap a tri-state: a failed `stat` is not
    an answer.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-51", "parent": "REQ-001", "workspace": "alpha",
         "title": "Dirty and unreadable", "status": "in-progress"},
    ])
    gitrepo("worktree", "add", "-b", "ticket/TKT-51", str(repo.root / "wt-51"))
    (repo.root / "wt-51" / "uncommitted.txt").write_text("work that exists only here\n")

    srv = serve_fast(serve, repo.root)
    seen = wait_for_state(srv, "TKT-51", "unshipped")
    assert "uncommitted" in seen["detail"], seen

    before = os.stat(repo.root / "wt-51").st_mode
    try:
        os.chmod(repo.root / "wt-51", 0o000)
        time.sleep(1.0)                                  # a pass runs blind in this window
        d = delivery_of(srv, "TKT-51")
    finally:
        os.chmod(repo.root / "wt-51", before)

    assert "nothing new is committed" not in d["detail"], (
        f"a worktree whose `git status` could not run was described as having nothing "
        f"uncommitted: {d}\nThe file is still on disk; nothing was measured that says otherwise.")


def test_a_clean_worktree_is_still_clean(repo, gitrepo, serve):
    """NEGATIVE (mandatory) — §1 named this case and round 1 did not write it.

    The cheap wrong fix for a tri-state is to answer `Unknown` whenever anything is uncertain,
    which would make every ordinary lane read "the worktree could not be read". A `git status`
    that RAN and printed nothing means clean, and must keep meaning clean.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-60", "parent": "REQ-001", "workspace": "alpha",
         "title": "Nothing uncommitted", "status": "pass"},
    ])
    gitrepo("worktree", "add", "-b", "ticket/TKT-60", str(repo.root / "wt-60"))
    (repo.root / "wt-60" / "committed.txt").write_text("committed work\n")
    subprocess.run(["git", "-C", str(repo.root / "wt-60"), "add", "-A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo.root / "wt-60"), "commit", "-m", "TKT-60: work",
                    "--no-gpg-sign"], check=True, capture_output=True)

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-60", "unshipped")
    assert "could not be read" not in d["detail"], (
        f"a worktree whose `git status` ran and printed nothing was reported as unreadable: {d}")
    assert "uncommitted" not in d["detail"], d


def test_a_worktree_directory_that_is_gone_is_not_called_unreadable(repo, gitrepo, serve):
    """QA round 1, finding 2 — transcribed from its reproduction. A regression this fix introduced.

    `git worktree list` keeps reporting a checkout whose directory was deleted until somebody
    prunes it, so `git status` there fails — and round 1 read that failure as *unreadable*. An
    absent directory is not an unreadable one: that is the distinction `git::Presence` exists for,
    and this ticket reintroduced the conflation inside the fix for it.

    Before the change the row read `stalled · the document says in-progress …` with
    `disagrees: True`. Both fields are asserted, because the state going wrong and the
    disagreement going silent are two different losses.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-61", "parent": "REQ-001", "workspace": "alpha",
         "title": "Removed lane", "status": "in-progress"},
    ])
    gitrepo("worktree", "add", "-b", "ticket/TKT-61", str(repo.root / "wt-61"))

    srv = serve_fast(serve, repo.root)
    time.sleep(1.0)
    shutil.rmtree(repo.root / "wt-61")                     # gone; `.git/worktrees` still lists it
    time.sleep(1.0)
    d = delivery_of(srv, "TKT-61")

    assert "could not be read" not in d["detail"], (
        f"a worktree directory that is GONE was described as unreadable: {d}")
    assert d["disagrees"] is True, (
        f"the document says in-progress and nothing is running; the disagreement went silent: {d}")


def test_an_unreadable_parent_does_not_make_a_dirty_worktree_clean(repo, gitrepo, serve):
    """QA round 2, finding 1 — transcribed from its reproduction.

    Round 2 split *absent* from *unreadable* with `fs::exists(path, ec)`, whose `error_code`
    overload returns `false` for **both** *this path is not there* and *I could not find out*
    (`EACCES` on a parent, `ELOOP`, an unmounted path) — and the `error_code` was declared, passed
    and never read. So the split that exists to remove the ambiguity was decided by it.

    This is `SRV-FIX-109`'s escape reintroduced: that ticket **deleted** `git::pathExists`
    (`fs::exists(p, ec)`) *"so there is no longer a cheap way to ask the ambiguous question"*, and
    this one wrote the same call by hand in another module.

    The chmod goes on the PARENT. A case that chmods the worktree itself reproduces nothing,
    because `git status` and the `stat` then fail together and the row is `Unknown` either way.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-70", "parent": "REQ-001", "workspace": "alpha",
         "title": "Unreadable parent", "status": "in-progress"},
    ])
    box = repo.root / "box"
    box.mkdir()
    gitrepo("worktree", "add", "-b", "ticket/TKT-70", str(box / "wt-70"))
    (box / "wt-70" / "uncommitted.txt").write_text("work that exists only here\n")

    srv = serve_fast(serve, repo.root)
    seen = wait_for_state(srv, "TKT-70", "unshipped")
    assert "uncommitted" in seen["detail"], seen

    before = os.stat(box).st_mode
    try:
        os.chmod(box, 0o000)                 # the PARENT, so the stat on the worktree fails
        time.sleep(1.0)
        d = delivery_of(srv, "TKT-70")
    finally:
        os.chmod(box, before)

    assert "could not be read" in d["detail"], (
        f"an unreadable worktree was recorded as clean, because the absent/unknown split was "
        f"decided by `fs::exists(p, ec)` — the ambiguous call this class is made of: {d}")


# ── SRV-FIX-129 · an empty branch outranks one with twenty commits ────────────────────────────

def test_a_dirty_empty_branch_does_not_outrank_one_with_commits(repo, gitrepo, serve):
    """`branchFor` picks between candidate branches, and every verdict downstream is about the one
    it returned.

    The tie-break reads `(b.dirty && !best->dirty)` with no `ahead` guard, so a branch with NO
    commits and one stray edited file beats a branch with twenty. The comment one line above says
    the rule exists *"so a claimed-but-empty branch cannot hide a real one"* — which is what it
    does. The shape that triggers it is a lane claimed and abandoned beside a lane that did the
    work, which is the case `SRV-FEAT-028` was written for.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-80", "parent": "REQ-001", "workspace": "alpha",
         "title": "Two branches, one abandoned", "status": "in-progress"},
    ])
    # The abandoned one must be evaluated SECOND, or the `ahead` comparison wins before the
    # unguarded `dirty` clause is reached — `s.branches` is a std::map, so the order is the branch
    # names sorted. `wip/…` sorts after `ticket/…`; a fixture that names it `TKT-80` proves
    # nothing, which is what the first cut of this case did.
    gitrepo("branch", "wip/TKT-80")
    gitrepo("worktree", "add", str(repo.root / "wt-80"), "wip/TKT-80")
    (repo.root / "wt-80" / "stray.txt").write_text("one stray edited file\n")

    # the real one: two commits, clean, and sorted first
    gitrepo("checkout", "-q", "-b", "ticket/TKT-80")
    repo.write("src/real-one.txt", "the first half\n")
    gitrepo.commit("TKT-80: the first half")
    repo.write("src/real-two.txt", "the second half\n")
    gitrepo.commit("TKT-80: the second half")
    gitrepo("checkout", "-q", "main")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-80", "unshipped")
    assert "2 commits" in d["detail"], (
        f"the verdict is about the abandoned empty branch, not the one holding the work: {d}")


def test_a_dirty_branch_still_wins_when_neither_has_commits(repo, gitrepo, serve):
    """NEGATIVE (mandatory). The tie-break was added for a reason and must keep working.

    Deleting the `dirty` clause passes the case above and loses the behaviour it exists for: with
    neither branch ahead, the one somebody is actually working in is the one to report.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-81", "parent": "REQ-001", "workspace": "alpha",
         "title": "Neither is ahead", "status": "in-progress"},
    ])
    gitrepo("branch", "TKT-81")                       # clean, no commits
    gitrepo("branch", "ticket/TKT-81")
    gitrepo("worktree", "add", str(repo.root / "wt-81"), "ticket/TKT-81")
    (repo.root / "wt-81" / "being-worked-on.txt").write_text("somebody is here\n")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-81", "unshipped")
    assert "uncommitted" in d["detail"], (
        f"neither branch is ahead and the dirty one was not preferred: {d}")


# ── SRV-FEAT-165 · filter=done ────────────────────────────────────────────────────────────────

def three_delivery_states(repo, gitrepo):
    """Three requirements, one ticket each, in three of §3.5's states.

    `base_repo` writes one requirement and this needs three, so the shape is spelled out here.
    The states are built the way the cases above build them — merged into `main` with the branch
    deleted, commits on a branch no remote has, and a ticket nobody has touched — because a
    filter over `delivery.state` is only worth asserting against states a real repository
    produces.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-landed.md",
               doc({"id": "REQ-001", "title": "Land the rollback path", "status": "draft"}))
    repo.write(f"{REQ_DIR}/REQ-002-guard.md",
               doc({"id": "REQ-002", "title": "Guard the stale artifact", "status": "draft"}))
    repo.write(f"{REQ_DIR}/REQ-003-untouched.md",
               doc({"id": "REQ-003", "title": "Nothing has begun here", "status": "draft"}))
    # TKT-3's status is `todo` and not `in-progress`: the manifest declares `in-progress` to mean
    # a session was launched, so an untouched ticket claiming it is `stalled` — a fourth state,
    # and not the one this fixture is for.
    for tid, parent, status in (("TKT-1", "REQ-001", "in-progress"),
                                ("TKT-2", "REQ-002", "in-progress"),
                                ("TKT-3", "REQ-003", "todo")):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": parent, "workspace": "alpha",
                        "title": f"The work of {tid}", "status": status}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")

    # done · TKT-1 merged into main, its branch deleted the way a forge deletes it
    gitrepo("checkout", "-b", "ticket/TKT-1")
    repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-1: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-1", "ticket/TKT-1")
    gitrepo("branch", "-D", "ticket/TKT-1")

    # unshipped · TKT-2 has commits on its branch and no remote has them
    gitrepo("checkout", "-b", "ticket/TKT-2")
    repo.write("src/guard.txt", "work nobody outside this machine can see\n")
    gitrepo.commit("TKT-2: the guard")
    gitrepo("checkout", "main")

    # not_started · TKT-3, untouched


def test_filter_done_keeps_only_the_requirements_that_landed(repo, gitrepo, serve):
    """`?filter=done` is the question `needs you` cannot ask: what actually landed."""
    three_delivery_states(repo, gitrepo)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")
    wait_for_state(srv, "TKT-2", "unshipped")
    assert delivery_of(srv, "TKT-3")["state"] == "not_started"

    body = ok(srv, "/api/requirements?filter=done")
    assert [r["id"] for r in body["requirements"]] == ["REQ-001"], body
    assert body["total"] == 1, "the count line and the rows must be the same number"
    assert body["requirements"][0]["delivery"]["state"] == "done", body["requirements"][0]

    # the filter drops rows; it does not change what the list is
    assert len(ok(srv, "/api/requirements?filter=all")["requirements"]) == 3


def test_filter_done_and_a_query_compose(repo, gitrepo, serve):
    """A filter that ignores `q` is a filter that lies about `total`."""
    three_delivery_states(repo, gitrepo)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")
    wait_for_state(srv, "TKT-2", "unshipped")

    hit = ok(srv, "/api/requirements?q=rollback&filter=done")
    assert [r["id"] for r in hit["requirements"]] == ["REQ-001"], hit
    assert hit["total"] == 1, hit

    # REQ-002 matches the query and not the filter; REQ-001 matches the filter and not the query
    for query in ("guard", "begun"):
        miss = ok(srv, f"/api/requirements?q={query}&filter=done")
        assert miss["requirements"] == [], (query, miss)
        assert miss["total"] == 0, (query, miss)


def test_filter_done_on_a_repository_with_no_git_returns_nothing_not_everything(repo, serve):
    """NEGATIVE (mandatory). `unknown` is not `done`.

    Without a `.git` the tool cannot tell what landed and says so — every row's delivery state is
    `unknown` (`test_a_repository_with_no_git_still_serves`). A `done` filter that reads *cannot
    tell* as *done* answers the one question this chip exists for with every row in the
    repository, which is worse than answering it with none.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-truth.md",
               doc({"id": "REQ-001", "title": "No history here", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-9.md",
               doc({"id": "TKT-9", "parent": "REQ-001", "workspace": "alpha",
                    "title": "Nothing to see", "status": "pass"}))
    assert not (repo.root / ".git").exists()

    srv = serve_fast(serve, repo.root)

    # the row exists and the tool cannot tell what became of it
    everything = ok(srv, "/api/requirements?filter=all")
    assert [r["id"] for r in everything["requirements"]] == ["REQ-001"], everything
    assert everything["requirements"][0]["delivery"]["state"] == "unknown", everything

    body = ok(srv, "/api/requirements?filter=done")
    assert body["requirements"] == [], body
    assert body["total"] == 0, body


def test_filter_done_drops_the_standalone_row_when_its_work_has_not_landed(repo, gitrepo, serve):
    """The row no document backs obeys the filter exactly as a real row does.

    E3 already states this for `filter=needs`; a row that is exempt from one filter and not the
    other is a row a person cannot reason about. Both halves are asserted — this case that it is
    DROPPED, and the next that it is KEPT — because a `done` filter that simply skips the
    standalone block passes the first on its own.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-landed.md",
               doc({"id": "REQ-001", "title": "Land the rollback path", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-1.md",
               doc({"id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
                    "title": "The work of TKT-1", "status": "in-progress"}))
    repo.write(f"{TKT_DIR}/TKT-70.md",
               doc({"id": "TKT-70", "parent": "standalone", "workspace": "alpha",
                    "title": "Nobody claims this one", "status": "todo"}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")
    gitrepo("checkout", "-b", "ticket/TKT-1")
    repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-1: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-1", "ticket/TKT-1")

    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")
    assert delivery_of(srv, "TKT-70")["state"] == "not_started"

    everything = ok(srv, "/api/requirements?filter=all")
    assert "standalone" in [r["id"] for r in everything["requirements"]], everything

    landed = ok(srv, "/api/requirements?filter=done")
    assert [r["id"] for r in landed["requirements"]] == ["REQ-001"], landed
    assert landed["total"] == 1, landed


def test_filter_done_keeps_the_standalone_row_when_its_work_did_land(repo, gitrepo, serve):
    """The other half. A filter that skips the synthetic row wholesale fails here."""
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-002-guard.md",
               doc({"id": "REQ-002", "title": "Guard the stale artifact", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-2.md",
               doc({"id": "TKT-2", "parent": "REQ-002", "workspace": "alpha",
                    "title": "The work of TKT-2", "status": "in-progress"}))
    repo.write(f"{TKT_DIR}/TKT-71.md",
               doc({"id": "TKT-71", "parent": "standalone", "workspace": "alpha",
                    "title": "Nobody claims this one either", "status": "in-progress"}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")
    gitrepo("checkout", "-b", "ticket/TKT-71")
    repo.write(f"{RPT_DIR}/TKT-71-report.md", "# TKT-71 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-71: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-71", "ticket/TKT-71")
    gitrepo("checkout", "-b", "ticket/TKT-2")
    repo.write("src/guard.txt", "work nobody outside this machine can see\n")
    gitrepo.commit("TKT-2: the guard")
    gitrepo("checkout", "main")

    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-71", "done")
    wait_for_state(srv, "TKT-2", "unshipped")

    landed = ok(srv, "/api/requirements?filter=done")
    assert [r["id"] for r in landed["requirements"]] == ["standalone"], landed
    assert landed["total"] == 1, landed
    assert landed["requirements"][0]["delivery"]["state"] == "done", landed


# ── SRV-FIX-166 · a requirement is done only when every ticket is ─────────────────────────────

def one_requirement_two_tickets(repo, gitrepo, second_lands):
    """`REQ-001` with `TKT-1` merged into `main`, and `TKT-2` merged or untouched.

    Two tickets under ONE requirement is the shape no fixture in this suite built, which is why
    `SRV-FEAT-165` could specify its aggregate rule against a repository that never exercised it.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-landed.md",
               doc({"id": "REQ-001", "title": "Land the rollback path", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-1.md",
               doc({"id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
                    "title": "The half that landed", "status": "in-progress"}))
    repo.write(f"{TKT_DIR}/TKT-2.md",
               doc({"id": "TKT-2", "parent": "REQ-001", "workspace": "alpha",
                    "title": "The half nobody started", "status": "todo"}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")

    gitrepo("checkout", "-b", "ticket/TKT-1")
    repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-1: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-1", "ticket/TKT-1")

    if second_lands:
        gitrepo("checkout", "-b", "ticket/TKT-2")
        repo.write(f"{RPT_DIR}/TKT-2-report.md", "# TKT-2 — PASS\n\nIt landed too.\n")
        gitrepo.commit("TKT-2: deliver")
        gitrepo("checkout", "main")
        gitrepo("merge", "--no-ff", "-m", "merge TKT-2", "ticket/TKT-2")


def test_a_requirement_with_one_landed_ticket_and_one_never_started_is_not_done(repo, gitrepo, serve):
    """The defect, stated as the filter's own promise: `done` means the work is on the base branch.

    One ticket of two landed. `SRV-FEAT-165` kept this row because `urgency()` ranks `done` = 4
    ahead of `not_started` = 5 and the aggregate takes the lowest.
    """
    one_requirement_two_tickets(repo, gitrepo, second_lands=False)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")
    assert delivery_of(srv, "TKT-2")["state"] == "not_started"

    row = ok(srv, "/api/requirements?filter=all")["requirements"][0]
    assert row["delivery"]["state"] != "done", (
        f"one ticket of two landed and the row calls itself done: {row['delivery']}")

    landed = ok(srv, "/api/requirements?filter=done")
    assert landed["requirements"] == [], landed
    assert landed["total"] == 0, landed


def test_a_requirement_is_done_only_when_every_ticket_is(repo, gitrepo, serve):
    """The other half — the fix may not be "never aggregate to done"."""
    one_requirement_two_tickets(repo, gitrepo, second_lands=True)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")
    wait_for_state(srv, "TKT-2", "done")

    landed = ok(srv, "/api/requirements?filter=done")
    assert [r["id"] for r in landed["requirements"]] == ["REQ-001"], landed
    assert landed["total"] == 1, landed
    assert landed["requirements"][0]["delivery"]["state"] == "done", landed


def test_the_row_and_its_own_page_agree_on_delivery(repo, gitrepo, serve):
    """E3's row and E4's header read the same aggregate, so a person cannot be told two things."""
    one_requirement_two_tickets(repo, gitrepo, second_lands=False)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")

    for row in ok(srv, "/api/requirements?filter=all")["requirements"]:
        page = ok(srv, f"/api/requirements/{row['id']}")
        assert page["delivery"]["state"] == row["delivery"]["state"], (
            f"{row['id']}: row says {row['delivery']['state']}, "
            f"its own page says {page['delivery']['state']}")

    for row in ok(srv, "/api/requirements?filter=done")["requirements"]:
        page = ok(srv, f"/api/requirements/{row['id']}")
        assert page["delivery"]["state"] == "done", (row["id"], page["delivery"])


def test_a_more_urgent_ticket_still_wins_the_badge(repo, gitrepo, serve):
    """NEGATIVE (mandatory). Triage survives the fix.

    `stalled` and `unshipped` are the two states that need somebody (§3.3), and they must keep
    beating `done`. A fix that inverts `urgency()`, or that drops `done` into a "least urgent"
    bucket, satisfies the two cases above and destroys the badge: this requirement would then
    report `done` while a session that died is sitting under it. Watched RED against exactly that
    inversion before this was committed.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-002-mixed.md",
               doc({"id": "REQ-002", "title": "One landed, one died", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-3.md",
               doc({"id": "TKT-3", "parent": "REQ-002", "workspace": "alpha",
                    "title": "The one that landed", "status": "in-progress"}))
    # `awaiting-qa` is declared in_flight, and nothing is running or committed → stalled
    repo.write(f"{TKT_DIR}/TKT-4.md",
               doc({"id": "TKT-4", "parent": "REQ-002", "workspace": "alpha",
                    "title": "The one that died", "status": "awaiting-qa"}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")
    gitrepo("checkout", "-b", "ticket/TKT-3")
    repo.write(f"{RPT_DIR}/TKT-3-report.md", "# TKT-3 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-3: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-3", "ticket/TKT-3")

    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-3", "done")
    wait_for_state(srv, "TKT-4", "stalled")

    row = ok(srv, "/api/requirements?filter=all")["requirements"][0]
    assert row["delivery"]["state"] == "stalled", (
        f"a stalled ticket must still win the badge: {row['delivery']}")
    assert row["delivery"]["needs_you"] is True, row["delivery"]
    # and the detail is the stalled child's own, not a sentence about the one that landed
    assert "main" not in row["delivery"]["detail"], row["delivery"]


# ── SRV-FEAT-557 · a monitor decides a ticket against the repository that holds it ─────────────

def test_a_ticket_merged_in_a_declared_repository_is_done_on_the_monitor(repo, gitrepo, serve):
    """The robotics root declares nine repositories; a ticket merged into `software`'s main showed
    `not_started` on the root because the probe read the root's git alone. The probe now scans each
    declared repository that is its own git checkout and unions its branches, merged tips and
    report artefacts, so the ticket's own repository answers for it.
    """
    repo.manifest({
        "factory": "0.1",
        "repos": {
            "root": {"path": ".", "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}},
            "team": {"path": "team", "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}},
        },
        "workspaces": {"alpha": {"repo": "root", "path": "."}, "beta": {"repo": "team", "path": "."}},
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": "claude"},
    })
    repo.write(f"{REQ_DIR}/REQ-001-truth.md",
               doc({"id": "REQ-001", "title": "The board tells the truth", "status": "draft"}))
    repo.mkdir(TKT_DIR)
    repo.mkdir(RPT_DIR)
    gitrepo.init()
    gitrepo.commit("the root as it stands")

    team = Git(Path(repo.root) / "team")
    repo.write(f"team/{TKT_DIR}/TKT-9.md",
               doc({"id": "TKT-9", "parent": "REQ-001", "workspace": "beta",
                    "title": "Merged in the team repository", "status": "pass"}))
    repo.mkdir(f"team/{REQ_DIR}")
    repo.mkdir(f"team/{RPT_DIR}")
    team.init()
    team.commit("the team repository as it stands")
    team("checkout", "-b", "ticket/TKT-9")
    repo.write(f"team/{RPT_DIR}/TKT-9-report.md", "# TKT-9 — PASS\n\nIt landed.\n")
    team.commit("TKT-9: deliver")
    team("checkout", "main")
    team("merge", "--no-ff", "-m", "merge TKT-9", "ticket/TKT-9")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-9", "done")
    assert d["state"] == "done", d
    assert d["disagrees"] is False and d["needs_you"] is False, d


def test_a_ticket_unmerged_in_a_declared_repository_is_not_done_on_the_monitor(repo, gitrepo, serve):
    """NEGATIVE for SRV-FEAT-557 — the union adds facts, never a verdict: a team branch with
    commits on no remote is `unshipped` on the monitor, exactly as it is on the team instance."""
    repo.manifest({
        "factory": "0.1",
        "repos": {
            "root": {"path": ".", "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}},
            "team": {"path": "team", "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}},
        },
        "workspaces": {"alpha": {"repo": "root", "path": "."}, "beta": {"repo": "team", "path": "."}},
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": "claude"},
    })
    repo.write(f"{REQ_DIR}/REQ-001-truth.md",
               doc({"id": "REQ-001", "title": "The board tells the truth", "status": "draft"}))
    repo.mkdir(TKT_DIR)
    repo.mkdir(RPT_DIR)
    gitrepo.init()
    gitrepo.commit("the root as it stands")

    team = Git(Path(repo.root) / "team")
    repo.write(f"team/{TKT_DIR}/TKT-8.md",
               doc({"id": "TKT-8", "parent": "REQ-001", "workspace": "beta",
                    "title": "Still on its branch", "status": "pass"}))
    repo.mkdir(f"team/{REQ_DIR}")
    repo.mkdir(f"team/{RPT_DIR}")
    team.init()
    team.commit("the team repository as it stands")
    team("checkout", "-b", "ticket/TKT-8")
    repo.write(f"team/{RPT_DIR}/TKT-8-report.md", "# TKT-8 — PASS\n\nNot landed.\n")
    team.commit("TKT-8: deliver")
    team("checkout", "main")

    srv = serve_fast(serve, repo.root)
    d = wait_for_state(srv, "TKT-8", "unshipped")
    assert d["state"] == "unshipped", d
    assert d["disagrees"] is True and d["needs_you"] is True, d
