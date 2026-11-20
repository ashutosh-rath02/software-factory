"""SRV-FEAT-415 — a lane has a life after its round ends.

Everything the tool knows is about a lane that is RUNNING. When the round stops — for any reason,
good or bad — the lane freezes in a state that means nothing and holds its seat until a person
opens a worktree and works out what happened.

Measured on this repository on 2026-09-05: seven lanes, every one reading `idle · waiting for you`,
with four genuinely different situations behind the one label — a failed verdict, work already
landed, a passed round with nowhere to put itself, and a round that stopped early. `WEB-FIX-337`
sat like that for 287 minutes and `SRV-FIX-379` for 356, holding seats, at $27.22 and $14.66, both
having PASSED QA.

`process == idle` is `state.cpp:60`'s "the agent's process is alive and its turn is over", so an
agent that FINISHED and one that stopped to ask a question are the same value; `needs_you` is a
boolean with no age, so four minutes and four days read identically; and §3.3's silence alarm only
measures a LIVE process, so the one state meaning *this has stopped and needs somebody* is the
state no alarm covers.

**Releasing is deliberately not the rule.** A lane at `delivery: unshipped` holds work nobody else
can see, and letting it go strands commits. The rule is release when there is provably nothing left
in it, which §3.5's `done` already computes — `test_a_lane_holding_unshipped_work_is_kept` is the
assertion that keeps the difference honest.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
import requests

from test_delivery import CAPTURED_RESULT_FRAME

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


def doc(front, body="Body."):
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines + ["---", "", body, ""])


class Git:
    def __init__(self, root):
        self.root = root

    def __call__(self, *args):
        out = subprocess.run(["git", "-C", str(self.root), *args],
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, f"git {' '.join(args)} → {out.returncode}\n{out.stderr}"
        return out.stdout.strip()

    def init(self):
        self("init", "-q", "-b", "main")
        self("config", "user.email", "t@example.invalid")
        self("config", "user.name", "T")
        return self

    def commit(self, message):
        self("add", "-A")
        self("commit", "-q", "-m", message)


@pytest.fixture
def world(repo, serve):
    """One repository, one ticket, one claimed lane — and no run ever started in it.

    A lane with no process is exactly the state this ticket is about: the round is over (or never
    began), and the question is what the tool then does about the lane.
    """
    class World:
        def build(self, ticket_status="awaiting-qa", land=False):
            repo.write(".factory/factory.json", __import__("json").dumps(manifest_for()))
            repo.write(f"{REQ_DIR}/REQ-001.md", doc({"id": "REQ-001", "title": "R"}))
            repo.write(f"{TKT_DIR}/TKT-1.md", doc({
                "id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
                "title": "One ticket", "status": ticket_status}))
            self.git = Git(repo.root).init()
            self.git.commit("the repository")

            if land:
                # a ticket whose work is ON MAIN — §3.5's `done`, the only safe release signal
                self.git("checkout", "-q", "-b", "ticket/TKT-1")
                repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — PASS\n\nIt landed.\n")
                self.git.commit("TKT-1: deliver")
                self.git("checkout", "-q", "main")
                self.git("merge", "-q", "--no-ff", "-m", "merge TKT-1", "ticket/TKT-1")
            else:
                # work that exists and nobody else can see — §3.5's `unshipped`
                self.git("checkout", "-q", "-b", "ticket/TKT-1")
                repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — PASS\n\nHeld here.\n")
                self.git.commit("TKT-1: deliver")
                self.git("checkout", "-q", "main")

            self.srv = serve(repo.root)
            return self

        def claim(self, ticket="TKT-1"):
            r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": "alpha"})
            assert r.status_code == 201, r.text
            self.lane = r.json()["lane"]
            return self.lane

        def pass_ticket(self):
            repo.write(f"{TKT_DIR}/TKT-1.md", doc({
                "id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
                "title": "One ticket", "status": "pass"}))
            self.git.commit("TKT-1: pass after claim")

        def lanes(self):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            return r.json()["lanes"]

        def row(self, lane=None):
            want = lane or self.lane
            return next((x for x in self.lanes() if x["lane"] == want), None)

        def delivery(self, ticket="TKT-1"):
            r = self.srv.get(f"/api/tickets/{ticket}")
            assert r.status_code == 200, r.text
            return (r.json().get("delivery") or {}).get("state")

        def wait_delivery(self, want, timeout=15):
            end = time.time() + timeout
            seen = None
            while time.time() < end:
                seen = self.delivery()
                if seen == want:
                    return
                time.sleep(0.05)
            raise AssertionError(f"delivery never became {want!r}; last was {seen!r}")

    return World()


# ── criterion 1 ───────────────────────────────────────────────────────────────────────────────

def test_a_stopped_lane_names_its_reason_and_its_age(world):
    """Four situations may not share one word.

    A person looking at a lane whose round is over must be able to read WHICH of them it is and
    HOW LONG it has been so, off the lane, without opening a worktree.
    """
    w = world.build(ticket_status="awaiting-qa", land=False)
    w.claim()
    row = w.row()

    # `stopped` is a member of `state`, beside process/motion/evidence/alarm — it is an AXIS.
    # It was briefly a sibling, and WEB-FIX-349 is what that cost: the client reads the state
    # object, so a field outside it is a field the screen cannot see.
    stopped = (row.get("state") or {}).get("stopped")
    assert stopped is not None, (
        "a lane with no live process carries no account of why it stopped: the row is "
        f"{sorted(row)}. `process: idle` is 'alive, turn over' and says nothing about whether the "
        "round finished, failed, or is waiting on a person.")
    assert stopped.get("reason"), f"the lane stopped and named no reason: {stopped}"
    assert isinstance(stopped.get("seconds"), int), (
        f"the lane carries no age, so four minutes and four days read the same: {stopped}")


# ── criterion 2 ───────────────────────────────────────────────────────────────────────────────

def test_a_lane_whose_work_has_landed_is_released(world):
    """`delivery: done` is the tool's own statement that the work is on `main`.

    A lane holding nothing, with no process alive in it, is a seat spent on finished work. The next
    round should be able to start where it used to sit — without anybody asking.
    """
    w = world.build(ticket_status="todo", land=True)
    lane = w.claim()
    w.pass_ticket()
    w.wait_delivery("done")

    end = time.time() + 20
    while time.time() < end:
        if w.row(lane) is None:
            break
        time.sleep(0.1)

    assert w.row(lane) is None, (
        f"{lane} still holds a seat with its work already on main "
        f"(delivery={w.delivery()!r}) and no process in it")
    seats = w.srv.get("/api/lanes").json()["seats"]["host"]
    assert seats["used"] == 0, f"the seat was not returned: {seats}"


# ── criterion 3 — the guard on criterion 2 ────────────────────────────────────────────────────

def test_a_lane_holding_unshipped_work_is_kept(world):
    """Never release a lane whose commits nobody else can see.

    This is the assertion that stops criterion 2 becoming 'release anything idle'. `unshipped` is
    §3.5's *work exists that nobody else can see*; letting that lane go strands it.
    """
    w = world.build(ticket_status="todo", land=False)
    lane = w.claim()
    w.pass_ticket()
    w.wait_delivery("unshipped")

    time.sleep(3)
    assert w.row(lane) is not None, (
        f"{lane} was released while its ticket is {w.delivery()!r} — the branch's commits are on "
        f"no other ref, so the lane was the only thing pointing at them")


# ── criterion 4 ───────────────────────────────────────────────────────────────────────────────

def test_needs_you_excludes_a_lane_that_wants_nothing(landed):
    """A counter that is always on is a notification channel a person learns to ignore.

    `test_state.py` says that in its own docstring, and then §3.3 counts every `idle` lane —
    including one whose work has landed and which is asking for nothing at all.

    The lane's session RAN and finished its turn, and ignores the end of its stdin, so it stays
    `idle` on landed work — the state this rule is about. This case first claimed a lane and
    never started a run in it; `not_started` is never counted in `needs_you`, so it passed with
    the rule deleted (SRV-FIX-600, break B8).
    """
    w = landed.build({"TKT-1": "in-progress"})
    lane = w.claim("TKT-1")
    w.mark(lane, "never-exits")
    w.start(lane)
    w.wait_process(lane, "idle")
    w.land("TKT-1")
    w.set_status("TKT-1", "pass")

    time.sleep(2)
    body = w.lanes()
    row = w.row(lane, body)
    assert row is not None and row["state"]["process"] == "idle", (
        f"the case needs the session up and idle on landed work: {row}")
    assert row["needs_you"] is False, (
        "the lane is counted in `needs you` with its work already on main and nothing asked of "
        f"anybody: {row.get('state')}")

# ── SRV-FIX-600 · a landed ticket's lanes are released even when their sessions are still up ──
#
# Every case above claims a lane and never starts a run in it. Every real round ends differently:
# the session writes its verdict, finishes its turn, and STAYS UP — `process: idle`, a live pid —
# and `SRV-FEAT-415`'s rule keyed on "no process alive", so the lane was held for ever
# (`docs/operations/ESCAPES.md`, 2026-10-20). The cases below run a stand-in that behaves like the
# CLI: it prints the captured `result` frame, which ends its turn, and then reads stdin until EOF,
# which is how the real program exits when `E16` closes it — measured, not assumed:
# `fixtures/cli-eof-2026-10-20-a-finished-session-exits-when-stdin-closes.json`, and
# `test_the_cli_shaped_stand_in_agrees_with_the_captured_eof` holds the stand-in to it. Two gitignored marker files, written into
# one lane's own worktree, give that lane a different behaviour: `stay-up` never ends its turn
# (`running`), `never-exits` ends its turn and ignores EOF (a session slow to go).

AGENT_LIKE_THE_CLI = """#!/bin/sh
if [ -f ./stay-up ] ; then while : ; do sleep 0.2 ; done ; fi
cat ./turn.json
if [ -f ./never-exits ] ; then while : ; do sleep 0.2 ; done ; fi
cat > /dev/null
"""

QA_PROMPT = "You are checking {ticket_id}.\n"


EOF_CAPTURE = json.loads((Path(__file__).resolve().parent / "fixtures"
                          / "cli-eof-2026-10-20-a-finished-session-exits-when-stdin-closes.json")
                         .read_text())


def test_the_cli_shaped_stand_in_agrees_with_the_captured_eof(tmp_path):
    """The stand-in gives the verdicts `claude` gave: alive after its `result` while stdin is
    open, and a clean exit once stdin closes — on both argv modes the capture took."""
    (tmp_path / "turn.json").write_text(CAPTURED_RESULT_FRAME + "\n")
    agent = tmp_path / "claude"
    agent.write_text(AGENT_LIKE_THE_CLI)
    agent.chmod(0o755)
    real = EOF_CAPTURE["runs"]
    assert [r["argv_mode"] for r in real] == ["--session-id", "--resume"], real
    for run in real:
        p = subprocess.Popen([str(agent)], cwd=tmp_path, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        first = json.loads(p.stdout.readline())
        time.sleep(2)
        alive = p.poll() is None
        p.stdin.close()
        try:
            rc = p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = None
        mine = {"result": first.get("type") == "result",
                "alive_2s_after_result_stdin_open": alive, "returncode": rc}
        theirs = {"result": run["result"] is not None,
                  "alive_2s_after_result_stdin_open": run["alive_2s_after_result_stdin_open"],
                  "returncode": run["returncode"]}
        assert mine == theirs, f"{run['argv_mode']}: stand-in {mine}, captured CLI {theirs}"


def landed_manifest(resource=False):
    m = manifest_for()
    m["lanes"] = {"host": 4}
    m["stages"] = ["SPEC", "IMPL", "GATE"]
    m["roles"] = {"qa": {"start_on_stage": "GATE", "lane": "new",
                         "prompt_file": ".factory/prompts/qa.md"}}
    if resource:
        m["resources"] = {"rig": {"exclusive": True, "needed_by": ["alpha"],
                                  "description": "one rig, one lane at a time"}}
    return m


def pid_is_gone(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def wait_for_exit(pids, timeout=20):
    """Waits on `/proc`, the consequence — never on the endpoint whose answer is asserted."""
    end = time.time() + timeout
    while time.time() < end:
        if all(pid_is_gone(p) for p in pids):
            return
        time.sleep(0.1)
    raise AssertionError(f"these session processes never exited: "
                         f"{[p for p in pids if not pid_is_gone(p)]}")


class PermissionAsk:
    """A pending `PreToolUse` ask on a lane — the lane reads `blocked` until it is answered."""

    def __init__(self, srv, lane):
        self.response = None
        self._thread = threading.Thread(target=self._run, args=(srv, lane), daemon=True)
        self._thread.start()

    def _run(self, srv, lane):
        try:
            self.response = requests.post(srv.base + "/api/_test/permission-ask",
                                          json={"lane": lane, "tool": "Bash",
                                                "input": {"command": "true"}}, timeout=90)
        except Exception:                              # noqa: BLE001 — the server stopped first
            pass


@pytest.fixture
def landed(repo, serve):
    """A repository whose tickets' lanes run a CLI-shaped session, and a way to land each one."""
    class Landed:
        def build(self, tickets, resource=False):
            repo.write(".factory/factory.json", json.dumps(landed_manifest(resource)))
            repo.write(".gitignore", ".factory/worktrees/\n.factory/runs/\n.factory/lanes/\n"
                                     ".factory/ids.json\nstay-up\nnever-exits\n")
            repo.write(".factory/prompts/qa.md", QA_PROMPT)
            repo.write("bin/claude", AGENT_LIKE_THE_CLI).chmod(0o755)
            repo.write("turn.json", CAPTURED_RESULT_FRAME + "\n")
            repo.write(f"{REQ_DIR}/REQ-001.md", doc({"id": "REQ-001", "title": "R"}))
            for tid, status in tickets.items():
                self.write_ticket(tid, status)
            self.git = Git(repo.root).init()
            self.git.commit("the repository")
            for tid in tickets:
                self.git("checkout", "-q", "-b", f"ticket/{tid}")
                repo.write(f"{RPT_DIR}/{tid}-report.md", f"# {tid} — PASS\n\nDone.\n")
                self.git.commit(f"{tid}: deliver")
                self.git("checkout", "-q", "main")
            return self.restart(first=True)

        def restart(self, first=False):
            if not first:
                serve.stop(self.srv)
            self.srv = serve(repo.root, FACTORY_DELIVERY_POLL_MS="200", FACTORY_TEST_HOOKS="1",
                             PATH=f"{repo.root}/bin:{os.environ['PATH']}")
            return self

        def write_ticket(self, tid, status):
            repo.write(f"{TKT_DIR}/{tid}.md", doc({
                "id": tid, "parent": "REQ-001", "workspace": "alpha",
                "title": f"Ticket {tid}", "status": status}))

        def claim(self, tid, role=None):
            body = {"ticket_id": tid, "workspace": "alpha"}
            if role:
                body["role"] = role
            r = self.srv.post("/api/lanes", json=body)
            assert r.status_code == 201, r.text
            return r.json()["lane"]

        def mark(self, lane, marker):
            Path(self.row(lane)["worktree"], marker).write_text("")

        def start(self, lane):
            r = self.srv.post(f"/api/lanes/{lane}/runs", json={})
            assert r.status_code == 201, r.text
            return r.json()["pid"]

        def wait_process(self, lane, process, timeout=25):
            end = time.time() + timeout
            seen = None
            while time.time() < end:
                seen = (self.row(lane) or {}).get("state", {}).get("process")
                if seen == process:
                    return
                time.sleep(0.1)
            raise AssertionError(f"{lane} never reached {process!r}; last was {seen!r}")

        def land(self, tid):
            self.git("merge", "-q", "--no-ff", "-m", f"land {tid}", f"ticket/{tid}")

        def set_status(self, tid, status):
            self.write_ticket(tid, status)
            self.git.commit(f"{tid}: {status}")

        def lanes(self):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            return r.json()

        def row(self, lane, body=None):
            rows = (body or self.lanes())["lanes"]
            return next((x for x in rows if x["lane"] == lane), None)

        def delivery(self, tid):
            r = self.srv.get(f"/api/tickets/{tid}")
            assert r.status_code == 200, r.text
            return r.json()["delivery"]

    return Landed()


def test_a_landed_tickets_idle_sessions_are_ended_and_released(landed):
    """A run lane and a QA lane, both alive with their turns over, and the ticket passes and lands.

    Two lanes of two roles in one case: a rule that lets go of the first lane it finds, or of the
    `run` lane only, leaves the other one holding its seat and goes red here.
    """
    w = landed.build({"TKT-1": "in-progress"})
    run = w.claim("TKT-1")
    run_pid = w.start(run)
    w.wait_process(run, "idle")
    qa = w.claim("TKT-1", role="qa")
    qa_pid = w.start(qa)
    w.wait_process(qa, "idle")
    assert w.lanes()["seats"]["host"]["used"] == 2

    w.land("TKT-1")
    w.set_status("TKT-1", "pass")

    wait_for_exit([run_pid, qa_pid])
    time.sleep(1.5)
    body = w.lanes()
    left = [(r["lane"], r["role"], r["state"]["process"]) for r in body["lanes"]]
    assert left == [] and body["seats"]["host"]["used"] == 0, (
        f"TKT-1 is `pass` with its work on main and its sessions finished, and the tool still "
        f"holds {left} with seats {body['seats']['host']}")


def test_a_landed_idle_holder_hands_its_resource_to_the_queue(landed):
    """The holder is a finished session on landed work; the waiter is another ticket's lane.

    `_TEMPLATE.md` §1: a deferral owes a criterion that the deferred thing HAPPENS, with the holder
    released by the mechanism under test — nothing in this case releases anything.
    """
    w = landed.build({"TKT-1": "in-progress", "TKT-2": "todo"}, resource=True)
    holder = w.claim("TKT-1")
    holder_pid = w.start(holder)
    w.wait_process(holder, "idle")
    waiter = w.claim("TKT-2")
    before = w.row(waiter)
    assert before["state"]["process"] == "queued" and before["resources_held"] == [], before

    w.land("TKT-1")
    w.set_status("TKT-1", "pass")

    wait_for_exit([holder_pid])
    time.sleep(1.5)
    body = w.lanes()
    after = w.row(waiter, body)
    assert w.row(holder, body) is None and after["resources_held"] == ["rig"] \
        and after["state"]["process"] != "queued", (
        f"the rig stayed with a finished session on landed work: holder={w.row(holder, body)} "
        f"waiter={after}")


def test_a_landed_release_is_announced_per_lane(landed):
    """A second observer — a socket client, and the server's own log — hears of each release."""
    w = landed.build({"TKT-1": "in-progress"})
    run = w.claim("TKT-1")
    run_pid = w.start(run)
    w.wait_process(run, "idle")
    qa = w.claim("TKT-1", role="qa")
    qa_pid = w.start(qa)
    w.wait_process(qa, "idle")

    client = w.srv.ws()
    try:
        client.send({"m": "subscribe", "scope": {"ticket": "TKT-1"}})
        client.wait_for("snapshot")
        w.land("TKT-1")
        w.set_status("TKT-1", "pass")
        wait_for_exit([run_pid, qa_pid])

        released = []
        end = time.time() + 15
        while time.time() < end and len(released) < 2:
            try:
                frame = client.wait_for("lane.membership", timeout=max(0.1, end - time.time()))
            except AssertionError:
                break
            if frame.get("change") == "released":
                released.append(frame["lane"])
    finally:
        client.close()

    assert sorted(released) == sorted([run, qa]), (
        f"one `lane.membership released` frame per lane was owed, saw {released}")
    log = Path(w.srv.log).read_text()
    for lane in (run, qa):
        said = [ln for ln in log.splitlines()
                if f"lane {lane} " in ln and "TKT-1" in ln and "ending its session" in ln]
        assert len(said) == 1, (
            f"the log should name {lane}, TKT-1 and why its session was ended exactly once: {said}")


def test_a_running_or_blocked_lane_on_a_landed_ticket_is_kept(landed):
    """A live turn is never interrupted — mid-turn, or waiting on a person.

    TKT-1 is the criterion's world: its QA lane mid-turn and its run lane asking a permission.
    TKT-3 has ONLY a blocked lane, so the `blocked` guard is asked on its own — in TKT-1 the
    running lane would keep the delivery badge at `running` and hide whether `blocked` is honoured.
    """
    w = landed.build({"TKT-1": "in-progress", "TKT-3": "in-progress"})
    run = w.claim("TKT-1")
    run_pid = w.start(run)
    w.wait_process(run, "idle")
    PermissionAsk(w.srv, run)
    w.wait_process(run, "blocked")
    qa = w.claim("TKT-1", role="qa")
    w.mark(qa, "stay-up")
    qa_pid = w.start(qa)
    w.wait_process(qa, "running")
    alone = w.claim("TKT-3")
    alone_pid = w.start(alone)
    w.wait_process(alone, "idle")
    PermissionAsk(w.srv, alone)
    w.wait_process(alone, "blocked")

    for tid in ("TKT-1", "TKT-3"):
        w.land(tid)
        w.set_status(tid, "pass")

    time.sleep(3)
    body = w.lanes()
    kept = {lane: (w.row(lane, body) or {}).get("state", {}).get("process")
            for lane in (run, qa, alone)}
    assert kept == {run: "blocked", qa: "running", alone: "blocked"}, (
        f"a live turn on landed work was ended or released: {kept}")
    dead = [p for p in (run_pid, qa_pid, alone_pid) if pid_is_gone(p)]
    assert dead == [], f"a session mid-turn or waiting on a person was stopped: pids {dead}"


def test_an_idle_lane_is_kept_unless_passed_and_landed(landed):
    """Both halves of the condition, each world missing exactly one of them."""
    w = landed.build({"TKT-A": "awaiting-qa", "TKT-B": "in-progress"})
    a = w.claim("TKT-A")
    a_pid = w.start(a)
    w.wait_process(a, "idle")
    b = w.claim("TKT-B")
    b_pid = w.start(b)
    w.wait_process(b, "idle")

    w.land("TKT-A")
    w.set_status("TKT-B", "pass")

    time.sleep(3)
    body = w.lanes()
    kept = {lane: (w.row(lane, body) or {}).get("state", {}).get("process") for lane in (a, b)}
    assert kept == {a: "idle", b: "idle"}, (
        f"landed-but-not-passed (TKT-A) or passed-but-not-landed (TKT-B) let go of a lane: {kept}")
    assert [p for p in (a_pid, b_pid) if pid_is_gone(p)] == [], "a session was ended"
    assert w.delivery("TKT-B")["state"] != "done", w.delivery("TKT-B")


def test_a_reused_lane_name_is_not_released_for_the_old_ticket(landed):
    """Lane names are reused (`SRV-FIX-247`, `SRV-FIX-256`): the old ticket's landing is not the
    new occupant's.

    Within one process the tool numbers lanes upward; a name comes back when the tool starts again
    and finds that lane's worktree gone, so that is the world built here.
    """
    w = landed.build({"TKT-1": "in-progress", "TKT-2": "todo"})
    first = w.claim("TKT-1")
    first_pid = w.start(first)
    w.wait_process(first, "idle")
    w.land("TKT-1")
    w.set_status("TKT-1", "pass")
    wait_for_exit([first_pid])
    time.sleep(1.5)
    assert w.row(first) is None, f"{first} was never released, so its name cannot be reused"

    w.restart()
    again = w.claim("TKT-2")
    assert again == first, f"the case needs the name reused; got {again} after {first}"
    again_pid = w.start(again)
    w.wait_process(again, "idle")

    time.sleep(3)
    row = w.row(again)
    assert row is not None and row["ticket_id"] == "TKT-2" \
        and row["state"]["process"] == "idle" and not pid_is_gone(again_pid), (
        f"{again}'s new occupant was ended or released for TKT-1's landing: {row}")
