"""SRV-FEAT-009 — an agent, spawn to reap.

Every case here drives a **real child process**. Not a mock, and not an agent CLI:

- a mock cannot wedge, cannot ignore `SIGTERM` and cannot leave a zombie, which is most of what
  this ticket is about, and
- an agent would make the test about what a model chooses to do, when what is under test is
  whether the tool can start one, steer it, and get it back.

Two assertions run in every case and they are the ones that matter most:

1. the main checkout's `git status` is byte-identical before and after (`docs/operations/ESCAPES.md` row 1), and
2. **every run is reaped** — `/proc/<pid>` is gone, or at the very least the process is not sitting
   in state `Z` while the tool reports it running. The prototype's "stop" left processes in `Z` and
   said they were running, because `os.kill(pid, 0)` succeeds on a zombie. So the check reads the
   state field of `/proc/<pid>/stat` and never asks `kill(pid, 0)`.
"""

import contextlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path

import jsonschema
import inspect
import pytest

import cli_conformance
import stand_in
from conftest import _start_instance, stop_process
from test_lane import build_repo, git, status_of

SOCKET_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)


# ── the stand-in for the agent CLI ───────────────────────────────────────────────────────────
#
# Committed into the repository, because a lane's worktree is a clean checkout: anything a lane
# is expected to run has to be in the repository, not lying beside it.
#
# It reads messages from stdin and does exactly what it is told, so that "the child's stdin
# received both, in the order sent" is a fact a test can read off the disk rather than infer.
#
# SRV-FIX-030 · it reads them THE WAY THE REAL CLI DOES, and that is not a detail of this file.
# The first version read plain lines because this test wrote it to, the tool wrote plain lines
# for the same reason, and against `claude 2.1.92` the whole of stdin is ONE prompt — so the
# steering these eight cases proved was one turn for a whole session. The reader now comes from
# `stand_in.py` and is judged against a capture of the real CLI by
# `test_the_stand_in_agrees_with_the_captured_cli`, so it cannot drift back into agreeing with
# whatever this repository happens to believe.

AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
{
  echo "cwd=$(pwd)"
  echo "argv=$*"
  echo "run=${FACTORY_RUN_ID}"
  echo "url=${FACTORY_PROGRESS_URL}"
  echo "token=${FACTORY_PROGRESS_TOKEN}"
} >> "$log"
""" + stand_in.FRAME_READER + r"""
wedge() {
  # Too wedged to hear any of it: SIGTERM ignored, stdin never read again, and a child of its own
  # so that "kill the group, not the leader" is a claim with something to disprove it.
  #
  # SRV-FIX-034 · the child announces ITSELF, and the cases below wait for that line instead of
  # for "wedged". `&` forks, and a forked process still carries its PARENT's command line: it
  # only starts matching `pgrep -f wedgechild-...` once it has EXEC'd. The parent reaches the
  # "wedged" line first, so "wedged is in the log" is the fact that the FORK HAPPENED, and what
  # the cases need is the fact that the CHILD EXISTS. Under a parallel batch's load those came
  # apart far enough to turn `test_kill_wedged_process` red 24 runs out of 25.
  #
  # What the child writes is ITS OWN `$0`, in one builtin `printf`. SRV-FIX-058: this line used
  # to be
  #
  #     { printf 'child-up:' ; tr '\000' ' ' < /proc/self/cmdline ; printf '\n' ; } >> $log
  #
  # and it was the flake. Measured, 300 concurrent rounds of that exact construct:
  # 191 carried the marker, **28 were a bare `child-up:`, 76 were an empty line, and 5 had two
  # announcements spliced into one**. Two mechanisms, both inherent to the construct:
  #
  #   · `tr` is EXTERNAL, so the shell forks and execs it. The fd on `/proc/self/cmdline` is
  #     opened by that forked child before the exec, and `/proc` regenerates its contents at READ
  #     time — after the execve that is replacing the very argv being read. The read therefore
  #     races the kernel installing the new mm, and comes back as the new argv, or as nothing.
  #   · three writes from two processes into one appended file interleave.
  #
  # The replacement has no external command, no `/proc` read at all, and ONE write. The marker is
  # passed as the shell's `$0`, which only the EXEC'D image has: the parent's `$0` is `agent.sh`
  # and carries no marker, so moving the announcement back into it — SRV-FIX-034's original
  # defect — makes this case red on the first round with no timing and no luck in it. `pgrep -f`
  # still matches, because `$0` is in argv.
  #
  # The first replacement tried announced `$$` and had the test look the pid up in `/proc`. It
  # failed on round 0 with `pid 8`: a lane runs under `--unshare-pid`, so the child's `$$` is a
  # number in ITS OWN pid namespace and means nothing in the host's `/proc`. That is why the
  # original read `/proc/self/cmdline` from the inside, and the replacement has to stay inside too.
  #
  # Measured on the replacement, same 300 concurrent rounds: 300 well-formed, 0 empty, 0 spliced.
  trap '' TERM
  marker=`printf '%s' "$1" | sed -n 's/.*marker=\([A-Za-z0-9]*\).*/\1/p'`
  /bin/sh -c "printf 'child-up:%s\n' \"\$0\" >> $log ; while : ; do sleep 1 ; done" "wedgechild-$marker" &
  printf '%s\n' "wedged" >> "$log"
  while : ; do sleep 1 ; done
}

echo "ready" >> "$log"
interrupted=0
trap 'interrupted=1 ; echo "interrupted" >> "$log"' INT

while : ; do
  if read_frame ; then
    printf '%s\n' "in:$text" >> "$log"
    case "$text" in
      wedge*)  wedge "$text" ;;
      write:*) printf 'proof\n' > "`printf '%s' "$text" | cut -d: -f2`" ;;
      slow:*)  sleep "`printf '%s' "$text" | cut -d: -f2`" ; echo "turn-done" >> "$log" ;;
      bye)     break ;;
    esac
  else
    # `read` fails on end of input AND when a trapped signal interrupts it. Only the first of
    # those means the session is over, so the trap says which one this was.
    if [ "$interrupted" = 1 ] ; then interrupted=0 ; continue ; fi
    break
  fi
done
echo "eof" >> "$log"
"""


# ── a stand-in that is still starting, and stays that way ────────────────────────────────────
#
# SRV-FIX-034 · `test_kill_during_startup_does_not_leak` has to deliver a kill BEFORE the child
# has produced its first output. Hitting that window by timing it would make the new case the same
# kind of coin-flip as the one this ticket is fixing, so the window is not hit — it is HELD. This
# stand-in writes nothing to stdout or to a log, ever, so "the child has not produced its first
# output" is true when the kill is sent and stays true for the rest of the test.
#
# It forks a marked child of its own for the same reason the wedge does: a supervisor that reaps
# the leader and leaves its descendants running is the failure worth writing a case about, and
# `startupchild-` is what gives that claim something to disprove.

STARTUP_AGENT_SH = r"""#!/bin/sh
/bin/sh -c "while : ; do sleep 1 ; done # startupchild-MARKER" &
while : ; do sleep 1 ; done
"""


# ── reading the process table, the way the prototype should have ─────────────────────────────

def proc_state(pid):
    """The state field of /proc/<pid>/stat, or None when the pid is gone.

    `comm` is in parentheses and may contain spaces and parentheses of its own, so the split is
    on the LAST ')' — the one place this parse is usually got wrong.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return raw.rsplit(")", 1)[1].split()[0]


def assert_reaped(pid, within=12, what=""):
    """No process, or at the very least no zombie — never `kill(pid, 0)`."""
    deadline = time.time() + within
    state = proc_state(pid)
    while time.time() < deadline:
        state = proc_state(pid)
        if state is None:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"the run{' ' + what if what else ''} (pid {pid}) was never reaped: /proc/{pid} still "
        f"exists in state {state!r}"
        + (" — THAT IS A ZOMBIE. The tool reported this process gone and anybody can still see it."
           if state == "Z" else ""))


def descendants_alive(marker):
    """Every process in the table whose command line carries `marker`.

    A sandboxed process is in its own PID namespace but is still perfectly visible from the host,
    which is what makes "its whole process group with it" checkable at all.
    """
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split()]


def cmdline_holders(marker):
    """The same question as `descendants_alive`, asked without forking anything.

    `pgrep` is a fork and an exec, and paying for one costs tens of milliseconds — the same order
    as the gap `test_the_wedge_child_is_observable_the_moment_it_is_announced` exists to measure.
    An instrument slower than the effect cannot see the effect, so that one case reads `/proc`
    itself. Every other case keeps `pgrep`, where the cost buys the same answer.
    """
    out = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as fh:
                if marker.encode() in fh.read():
                    out.append(int(entry.name))
        except OSError:
            continue
    return out


def wedge_child(env, lane, marker, timeout=20):
    """The wedge's own child, once it EXISTS — not once its parent says it forked one.

    SRV-FIX-034. The two cases that need this child used to take `"wedged"` in the agent's log as
    permission to look for it. That line is written by the PARENT, after `&` returns, and `&`
    returns at the fork: for the next few milliseconds the new process is still running the
    parent's command line and `pgrep -f wedgechild-...` cannot see it. The measured gap was 7-13
    ms idle and 21-29 ms under a parallel batch's load, and it was there in 25 rounds out of 25 —
    the premise was false on every run, and the case was green only because a WebSocket round trip
    happened to sit between the log line and the look.

    So the fact waited on here is the child's own first output, which its exec'd image writes and
    which therefore cannot precede the execve that gives it the command line being matched. This
    is a wait for a PRECONDITION, and the assertion is made once afterwards — never a retry of the
    assertion itself, which would hide exactly the defect the case exists to find.

    Its own pid is no use for this: every run is inside a PID namespace (`bwrap --unshare-pid`),
    so `$$` inside the sandbox is not the number the host's process table uses. Its command line
    is, and that is what it writes — see `AGENT_SH`.
    """
    env.wait_log(lane, "child-up:", timeout=timeout)
    return descendants_alive(f"wedgechild-{marker}")


def holders_of_stdin(path):
    """Every process whose fd 0 is `path` — literally "a child left with an open stdin"."""
    held = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(entry / "fd" / "0") == str(path):
                held.append(int(entry.name))
        except OSError:
            continue
    return held


# ── a repository with an agent, a lane, and a server ─────────────────────────────────────────

@pytest.fixture
def agentenv(tmp_path, serve):
    """A repository whose manifest declares the stand-in above as its agent, plus a server.

    Teardown asserts the main checkout is untouched AND that every run this test started was
    reaped, so a case that forgets to check cannot leave the finding to the next reader.
    """
    made = []
    started = []

    class Env:
        def build(self, script=AGENT_SH, name="agent.sh", agent_extra=None, **kw):
            agent = {"command": f"/bin/sh {name}"}
            agent.update(agent_extra or {})
            root = build_repo(tmp_path / f"repo{len(made)}", agent=agent, **kw)
            (root / name).write_text(script)
            git(root, "add", name)
            git(root, "commit", "-m", "agent")
            baseline = status_of(root)
            made.append((root, baseline))
            self.root = root
            self.baseline = baseline
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        def claim(self, ticket="TCK-001", workspace="core", **extra):
            body = {"ticket_id": ticket, "workspace": workspace}
            body.update(extra)
            r = self.srv.post("/api/lanes", json=body)
            assert r.status_code == 201, r.text
            return r.json()

        def start(self, lane, **body):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)
            assert r.status_code == 201, r.text
            run = r.json()
            started.append(run["pid"])
            return run

        def log(self, lane):
            p = Path(lane["worktree"]) / "agent-log.txt"
            try:
                return p.read_text()
            except OSError:
                return ""

        def wait_log(self, lane, needle, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                text = self.log(lane)
                if needle in text:
                    return text
                time.sleep(0.05)
            raise AssertionError(
                f"{needle!r} never reached the agent's log within {timeout}s; log so far:\n"
                f"{self.log(lane)}")

        def argv_of(self, lane, timeout=20):
            """The flags the TOOL built, read off the child that was started with them.

            Lifted rather than retyped: a test that writes the argv it expects is a test of what
            the test believes, which is the whole of `docs/operations/ESCAPES.md`'s row for this ticket.
            """
            text = self.wait_log(lane, "argv=", timeout=timeout)
            for line in text.splitlines():
                if line.startswith("argv="):
                    return line[len("argv="):].split()
            raise AssertionError(f"the child never reported its argv:\n{text}")

        def stdin_lines(self, lane, atleast=1, timeout=20):
            """The exact bytes the tool wrote to this run's FIFO, one entry per line."""
            deadline = time.time() + timeout
            raw = []
            while time.time() < deadline:
                raw = [l[len("raw:"):] for l in self.log(lane).splitlines()
                       if l.startswith("raw:")]
                if len(raw) >= atleast:
                    return raw
                time.sleep(0.05)
            raise AssertionError(
                f"only {len(raw)} message(s) reached the child's stdin within {timeout}s "
                f"(wanted {atleast}); the log is:\n{self.log(lane)}")

        def entries(self, run, timeout=15, atleast=1):
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

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()

    for pid in started:
        assert_reaped(pid, within=20, what="(checked in teardown)")
    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed by the end of the test:\n"
            f"before:\n{before}\nafter:\n{status_of(root)}")


# ── 1 · F10 ──────────────────────────────────────────────────────────────────────────────────

def test_run_starts_with_env(agentenv):
    """`POST …/runs` returns a run_id, and the child can prove what it was started with."""
    env = agentenv.build()
    lane = env.claim()

    run = env.start(lane)
    assert run["run_id"], run
    assert run["session_id"], run
    assert run["pid"] > 0, run

    text = env.wait_log(lane, "ready")
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    assert fields["run"] == run["run_id"], (
        f"FACTORY_RUN_ID is {fields['run']!r}, but the run is {run['run_id']!r} — a report citing "
        f"this run could never be matched to it (SPEC.md §3.4)")
    assert fields["url"] == f"http://127.0.0.1:{env.srv.port}/api/progress", fields["url"]
    assert len(fields["token"]) >= 16, f"FACTORY_PROGRESS_TOKEN is {fields['token']!r}"

    assert fields["cwd"] == os.path.realpath(lane["worktree"]), (
        f"the child started in {fields['cwd']!r}, which is not the lane's worktree "
        f"{lane['worktree']!r}")

    # The lane carries the run, which is the shape SRV-FEAT-008 emitted empty and named this
    # ticket as what fills.
    listed = env.srv.get("/api/lanes").json()["lanes"][0]
    assert listed["run_id"] == run["run_id"], listed
    assert listed["session_id"] == run["session_id"], listed

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 2 · F14 ──────────────────────────────────────────────────────────────────────────────────

def test_held_then_interrupt_delivers_in_order(agentenv):
    """Two messages during a turn are held; the interrupt delivers both, in order, nothing lost."""
    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    first = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": "one"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "delivered", first.json()
    env.wait_log(lane, "in:one")

    held = []
    for text in ("two", "three"):
        r = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "held", (
            f"{text!r} was sent mid-turn and the tool did not hold it: {r.json()}")
        held.append(r.json())
    assert [h["held_count"] for h in held] == [1, 2], held

    stopped = env.srv.post(f"/api/lanes/{lane['lane']}/interrupt", json={})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {"interrupted": True, "delivered": 2}, stopped.json()

    text = env.wait_log(lane, "in:three")
    lines = text.splitlines()

    assert "interrupted" in lines, f"the turn was never interrupted:\n{text}"
    for needle in ("in:one", "in:two", "in:three"):
        assert needle in lines, f"{needle} was DROPPED — the tool accepted it and lost it:\n{text}"
    assert lines.index("in:two") < lines.index("in:three"), (
        f"the held messages arrived out of order:\n{text}")
    assert lines.index("interrupted") < lines.index("in:two"), (
        f"a held message was delivered BEFORE the interrupt:\n{text}")

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 3 · F15 ──────────────────────────────────────────────────────────────────────────────────

def test_stop_keeps_disk_state(agentenv):
    """What already ran stays on disk; the session survives the stop and takes the next message."""
    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    proof = Path(lane["worktree"]) / "proof.txt"
    r = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": "write:proof.txt"})
    assert r.json()["status"] == "delivered", r.json()
    env.wait_log(lane, "in:write:proof.txt")
    deadline = time.time() + 10
    while not proof.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert proof.exists(), "the turn never got as far as writing the file this case is about"

    stopped = env.srv.post(f"/api/lanes/{lane['lane']}/interrupt", json={})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["interrupted"] is True, stopped.json()

    assert proof.read_text() == "proof\n", (
        "the interrupt reverted work that had already happened — SPEC.md E15 promises it does not")
    assert proc_state(run["pid"]) not in (None, "Z"), (
        "the interrupt killed the session; SPEC.md F15 says the session is still alive")

    # ...and the next message continues the same session.
    again = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": "after"})
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "delivered", (
        f"the turn did not end at the interrupt: {again.json()}")
    env.wait_log(lane, "in:after")

    listed = env.srv.get("/api/lanes").json()["lanes"][0]
    assert listed["session_id"] == run["session_id"], (
        f"the session changed across the stop: {listed['session_id']} != {run['session_id']}")

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 4 · F16 ──────────────────────────────────────────────────────────────────────────────────

def test_end_lets_the_turn_finish(agentenv):
    """`end` mid-turn lets the turn complete, then closes stdin and reaps. Session id retained."""
    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    ws = env.srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    ws.wait_for("snapshot")

    r = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": "slow:3"})
    assert r.json()["status"] == "delivered", r.json()
    env.wait_log(lane, "in:slow:3")

    ended = env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    assert ended.status_code == 202, ended.text
    assert ended.json() == {"ending": True}, ended.json()

    text = env.wait_log(lane, "eof", timeout=25)
    assert "turn-done" in text, (
        f"`end` cut the turn short; SPEC.md E16 lets the current turn finish:\n{text}")
    assert text.splitlines().index("turn-done") < text.splitlines().index("eof"), text

    assert_reaped(run["pid"], what="after end")

    frame = ws.wait_for("run", timeout=20)
    SOCKET_VALIDATOR.validate(frame)
    assert frame["outcome"] == "ended cleanly", frame
    assert frame["run_id"] == run["run_id"], frame
    ws.close()

    # The session id is retained, so a later run resumes it.
    second = env.start(lane, resume=True)
    assert second["session_id"] == run["session_id"], (
        f"the session was not retained: {second['session_id']} != {run['session_id']}")
    assert second["run_id"] != run["run_id"], "a second run reused the first run's id"

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(second["pid"])
    env.unchanged()


# ── 5 · F16 ──────────────────────────────────────────────────────────────────────────────────

def test_kill_wedged_process(agentenv):
    """A child that ignores SIGTERM and reads no stdin is gone — its whole group with it."""
    env = agentenv.build(resources=ORPHAN_MANIFEST)
    lane = env.claim()
    assert lane["resources_held"] == [ORPHAN_RESOURCE], (
        f"the fixture's claim did not take the exclusive resource, so `resources_held == []` "
        f"after the restart would be true of a lane that never held anything: {lane}")
    marker = uuid.uuid4().hex[:12]
    run = env.start(lane, prompt=f"wedge marker={marker}")
    env.wait_log(lane, "wedged")          # the parent has trapped SIGTERM and stopped reading

    # ...and the CHILD exists, which `"wedged"` does not say — see `wedge_child` (SRV-FIX-034).
    # This is read before the socket work below, not after it: the round trip used to be the only
    # reason this case cleared the gap, and a case that depends on an unrelated delay is a case
    # that reports whatever the machine was doing at the time.
    child = wedge_child(env, lane, marker)
    assert child, "the wedged agent never started the child that makes this case worth running"

    ws = env.srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    ws.wait_for("snapshot")

    grace = 2
    started = time.time()
    killed = env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": grace})
    assert killed.status_code == 202, killed.text
    assert killed.json() == {"killing": True}, killed.json()

    assert_reaped(run["pid"], within=grace + 1, what="(wedged, ignores SIGTERM)")
    took = time.time() - started
    assert took <= grace + 1, f"the kill took {took:.1f}s, past grace_s + 1 = {grace + 1}"

    deadline = time.time() + 3
    left = descendants_alive(f"wedgechild-{marker}")
    while left and time.time() < deadline:
        time.sleep(0.05)
        left = descendants_alive(f"wedgechild-{marker}")
    assert not left, (
        f"the leader was killed and its children were not: {left} still running. A build that "
        f"keeps going with nobody watching it is exactly what killing the group prevents.")

    frame = ws.wait_for("run", timeout=20)
    SOCKET_VALIDATOR.validate(frame)
    assert frame["outcome"] == "killed by you", frame
    ws.close()
    env.unchanged()


# ── 5a · SRV-FIX-034 · NEGATIVE (mandatory) ──────────────────────────────────────────────────

def test_the_line_the_wedge_cases_wait_on_is_written_by_the_child(agentenv, tmp_path):
    """The two cases above wait for a log line and then look for a process. Who writes the line?

    That question is the whole of `SRV-FIX-034`. They used to wait for `"wedged"`, which the
    wedge's PARENT writes immediately after `&` — and `&` returns at the fork, not at the exec.
    Until the forked process execs it still carries its parent's command line, so
    `pgrep -f wedgechild-…` cannot see it yet. The premise *"the line is there, therefore the child
    is there"* was false on every run ever made.

    ── SRV-FIX-058 · what was actually flaky, measured rather than guessed ──────────────────────

    This case then failed about once in 70 rounds for two days, and was twice explained by
    hypotheses that its own experiments disproved. The third attempt WITHDREW it, on the theory
    that `SRV-FIX-062`'s cross-session kill had been the cause; QA reproduced the failure three
    times in 204 rounds on a `HEAD` containing `062`, and every one of them landed on round 4 of 6.

    The cause was in the announcement itself:

        { printf 'child-up:' ; tr '\\000' ' ' < /proc/self/cmdline ; printf '\n' ; } >> $log

    300 concurrent rounds of that construct: **191 carried the marker, 28 were a bare
    `child-up:`, 76 were an empty line, 5 were two announcements spliced together.** `tr` is an
    external command, so the shell forks and execs it; the fd on `/proc/self/cmdline` is opened
    before that exec and `/proc` regenerates its contents at READ time, so the read races the very
    execve that is replacing the argv it is reading. And three writes from two processes into one
    appended file interleave. The failure QA kept seeing — a BARE `child-up:` — is that read
    returning nothing, which is not the parent writing the line at all, and the old failure
    message said it was.

    The announcement is now one builtin `printf` of the shell's own `$$`, and the identity is
    checked HERE, in `/proc`, against a process that is wedged and going nowhere. 300 concurrent
    rounds of the replacement: 300 well-formed, 0 empty, 0 spliced.

    ── what this case now asserts ──────────────────────────────────────────────────────────────

    Two phases, because they answer different questions and §1 asks for both:

      A · the PROPERTY, 200 rounds, under load THIS CASE PRODUCES — six fork-churning shells for
          the duration, because "6 is what let this reach `main` twice" and the load must not be
          hoped for. It exercises the announcement mechanism, which is the thing that was flaky.
      B · the PRODUCT PATH, six lane rounds end to end: claim, start, announce, find the child in
          `/proc`, kill, reap.
    """
    # ── A · the announcement, 200 rounds, under deliberate load ──────────────────────────────
    log = tmp_path / "announce.log"
    churn = [subprocess.Popen(
        ["/bin/sh", "-c", "while : ; do (: ) ; ( /bin/true ) ; done"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(6)]
    try:
        markers = [uuid.uuid4().hex[:12] for _ in range(200)]
        kids = []
        for marker in markers:
            kids.append(subprocess.Popen(
                ["/bin/sh", "-c",
                 f"printf 'child-up:%s\n' \"$0\" >> {log} ; while : ; do sleep 1 ; done",
                 f"wedgechild-{marker}"]))
        deadline = time.time() + 60
        lines = []
        while time.time() < deadline:
            lines = [l for l in log.read_text().splitlines() if l]
            if len(lines) >= 200:
                break
            time.sleep(0.05)

        shape = re.compile(r"child-up:wedgechild-[0-9a-f]{12}$")
        bad = [l for l in lines if not shape.fullmatch(l)]
        assert len(lines) == 200 and not bad, (
            f"{len(lines)} announcements out of 200 under load, {len(bad)} of them malformed. "
            f"A line that is not exactly one child's own `$0` means the announcement was written "
            f"by more than one process, or in more than one write. That is SRV-FIX-058's own "
            f"defect coming back — the construct it replaced produced a bare `child-up:`, an "
            f"empty line, or two announcements spliced together, 109 times in 300.\n  "
            + "\n  ".join(repr(l) for l in bad[:5]))

        announced = {l.split(":", 1)[1] for l in lines}
        assert announced == {f"wedgechild-{m}" for m in markers}, (
            f"{len(announced)} distinct identities for 200 children. Missing: "
            f"{sorted({f'wedgechild-{m}' for m in markers} - announced)[:5]}; unexpected: "
            f"{sorted(announced - {f'wedgechild-{m}' for m in markers})[:5]}")
    finally:
        for p in kids:
            p.kill()
        for p in kids:
            p.wait(timeout=10)
        for p in churn:
            p.kill()
        for p in churn:
            p.wait(timeout=10)

    # ── B · the product path, unchanged in shape ─────────────────────────────────────────────
    env = agentenv.build(lanes_host=8)
    checked = 0
    for i in range(6):
        lane = env.claim(ticket="TCK-001", role=f"round{i}")
        marker = uuid.uuid4().hex[:12]
        run = env.start(lane, prompt=f"wedge marker={marker}")

        text = env.wait_log(lane, "child-up:")
        announced = [l for l in text.splitlines() if l.startswith("child-up:")]
        assert len(announced) == 1, f"round {i}: {announced}"
        assert announced[0] == f"child-up:wedgechild-{marker}", (
            f"round {i}: the announcement is not exactly the wedge child's own `$0`:\n"
            f"  got:      {announced[0]!r}\n"
            f"  expected: {f'child-up:wedgechild-{marker}'!r}\n"
            f"`$0` belongs to the EXEC'D image and to nothing else — the parent that forked it is "
            f"running `agent.sh` and its `$0` carries no marker at all. So a line missing the "
            f"marker is the announcement being made by the parent (SRV-FIX-034's original "
            f"defect); a line that is malformed rather than missing is more than one write "
            f"landing in one place (SRV-FIX-058's). This message names only what the two "
            f"readings above can tell apart.")

        # ...and the fact that proves: the child is findable now, with no waiting at all.
        assert cmdline_holders(f"wedgechild-{marker}"), (
            f"round {i}: the child announced its own command line and /proc does not have it")

        checked += 1
        env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
        assert_reaped(run["pid"], what=f"round {i}")

    assert checked == 6
    env.unchanged()


def test_the_announcement_cannot_be_made_by_the_parent(agentenv):
    """NEGATIVE (mandatory, SRV-FIX-058 · §1). Put SRV-FIX-034's defect back, and the case above
    must go red on the FIRST round, on an idle machine, with no load and no luck involved.

    That is the whole property. A fix that made the case tolerant enough to stop being flaky would
    also make it tolerant of the bug it exists to catch — which is why §1 forbids widening a
    timeout or wrapping a retry, and why the flake had to be diagnosed rather than absorbed.

    The defect, exactly: the announcement is written by the PARENT, immediately after `&`. `&`
    returns at the fork, so the line is there before the child has exec'd and before `pgrep -f`
    can see it. Every case that waits on the line is then looking for a process that does not
    exist yet. `$0` is what tells the two apart and it costs nothing to read: the exec'd shell was
    handed `wedgechild-<marker>` as its `$0`, and the parent's is `agent.sh`.
    """
    LIVE = ('  /bin/sh -c "printf \'child-up:%s\\n\' \\"\\$0\\" >> $log ; '
            'while : ; do sleep 1 ; done" "wedgechild-$marker" &')
    ANNOUNCED_BY_THE_PARENT = (
        '  /bin/sh -c "while : ; do sleep 1 ; done" "wedgechild-$marker" &\n'
        '  printf \'child-up:%s\\n\' "$0" >> "$log"')
    assert LIVE in AGENT_SH, (
        "the negative could not find the announcement line to break — it has been reworded, and "
        "this case is now asserting nothing. Re-point it before trusting the case above.")
    broken = AGENT_SH.replace(LIVE, ANNOUNCED_BY_THE_PARENT, 1)

    env = agentenv.build(script=broken)
    lane = env.claim(ticket="TCK-001", role="round0")
    marker = uuid.uuid4().hex[:12]
    run = env.start(lane, prompt=f"wedge marker={marker}")

    text = env.wait_log(lane, "child-up:")
    announced = [l for l in text.splitlines() if l.startswith("child-up:")]
    assert announced, text
    assert announced[0] != f"child-up:wedgechild-{marker}", (
        "the parent produced the child's own announcement, so the case above cannot tell the two "
        "apart and SRV-FIX-034's defect would pass it:\n  " + announced[0])

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


# ── 5b · SRV-FIX-034 · E17 while the run is still starting ───────────────────────────────────

def test_kill_during_startup_does_not_leak(agentenv):
    """A kill that arrives before the child has produced any output still reaps everything.

    The window is HELD, not hit. `STARTUP_AGENT_SH` writes nothing to stdout and nothing to a log,
    ever, so "the kill was delivered before the child's first output" is true when the kill is
    sent and stays true afterwards — no timing assumption, and none of the flakiness of the case
    this ticket was filed about.

    The leak assertion is not vacuous even if the kill lands before the stand-in's own fork: the
    confinement helper certainly exists, the tool returned its pid, and every process inside the
    sandbox carries the lane's worktree in its command line (`conftest.owned_confinement_helpers`). So
    what is asserted is that nothing of the sandbox is left — including the marked grandchild,
    which is the process a supervisor that reaps only the leader would leave running.
    """
    marker = uuid.uuid4().hex[:12]
    env = agentenv.build(script=STARTUP_AGENT_SH.replace("MARKER", marker),
                         name="startup-agent.sh")
    lane = env.claim()

    ws = env.srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    ws.wait_for("snapshot")

    run = env.start(lane)
    killed = env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert killed.status_code == 202, killed.text
    assert killed.json() == {"killing": True}, killed.json()

    assert_reaped(run["pid"], what="killed while it was still starting")

    for what, needle in (("the marked grandchild", f"startupchild-{marker}"),
                         ("the sandbox", lane["worktree"])):
        deadline = time.time() + 8
        left = descendants_alive(needle)
        while left and time.time() < deadline:
            time.sleep(0.05)
            left = descendants_alive(needle)
        assert not left, (
            f"the kill arrived while the run was still starting and {what} survived it: {left} "
            f"still running. A kill during startup that leaks is worse than one that fails — the "
            f"tool has told the operator the run is gone and left a process nobody now owns.")

    # The kill really did land before any first output: this stand-in has no path that writes one.
    assert not (Path(lane["worktree"]) / "agent-log.txt").exists(), (
        "the startup stand-in produced output, so this case no longer covers the window it names")

    # The subscription predates the run, so the first `run` frame is the one announcing it as
    # `running`. The interesting one is the terminal frame — the same read `test_every_run_is_
    # reaped` makes, for the same reason.
    deadline = time.time() + 20
    frame = ws.wait_for("run", timeout=20)
    while frame["outcome"] == "running" and time.time() < deadline:
        frame = ws.wait_for("run", timeout=max(1, deadline - time.time()))
    SOCKET_VALIDATOR.validate(frame)
    assert frame["outcome"] == "killed by you", (
        f"a run killed during startup was reported as {frame.get('outcome')!r}; SPEC.md §3.2 "
        f"distinguishes what a person did from what the process did, and this one was killed")
    assert frame["run_id"] == run["run_id"], frame
    ws.close()

    for entry in env.srv.get("/api/lanes").json()["lanes"]:
        assert entry["state"]["process"] != "running", entry
    env.unchanged()


# ── 6 · E17's missing case ───────────────────────────────────────────────────────────────────

def test_kill_with_no_process_is_not_an_error(agentenv):
    """The desired state already holds. An error here is pedantry at the worst possible moment."""
    env = agentenv.build()
    lane = env.claim()

    r = env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"killing": False}, r.json()

    # ...and it stays true after a run has been and gone.
    run = env.start(lane)
    env.wait_log(lane, "ready")
    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])

    again = env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={})
    assert again.status_code == 200, again.text
    assert again.json() == {"killing": False}, again.json()

    gone = env.srv.post("/api/lanes/lane/99/kill", json={})
    assert gone.status_code == 404, gone.text
    env.unchanged()


# ── 7 · every run is reaped ──────────────────────────────────────────────────────────────────

def test_every_run_is_reaped(agentenv):
    """Each way a run can end, and after each one /proc says the process is really gone.

    `os.kill(pid, 0)` succeeds on a zombie, so it is never asked here. This is the check the
    prototype did not have, and the reason it reported processes running that were in state `Z`.
    """
    env = agentenv.build()
    outcomes = {}

    ws = env.srv.ws()
    ws.send({"m": "subscribe", "scope": {"requirements": True}})
    ws.wait_for("snapshot")

    # a · ended cleanly
    lane_a = env.claim(ticket="TCK-001")
    run_a = env.start(lane_a)
    env.wait_log(lane_a, "ready")
    assert env.srv.post(f"/api/lanes/{lane_a['lane']}/end", json={}).status_code == 202
    assert_reaped(run_a["pid"], what="ended")
    outcomes[run_a["run_id"]] = "ended cleanly"

    # b · killed by you
    lane_b = env.claim(ticket="TCK-002")
    run_b = env.start(lane_b)
    env.wait_log(lane_b, "ready")
    assert env.srv.post(f"/api/lanes/{lane_b['lane']}/kill",
                        json={"grace_s": 1}).status_code == 202
    assert_reaped(run_b["pid"], what="killed")
    outcomes[run_b["run_id"]] = "killed by you"

    # c · the agent ends its own session — nobody asked it to
    lane_c = env.claim(ticket="TCK-001", role="second")
    run_c = env.start(lane_c)
    env.wait_log(lane_c, "ready")
    assert env.srv.post(f"/api/lanes/{lane_c['lane']}/messages",
                        json={"text": "bye"}).status_code == 200
    assert_reaped(run_c["pid"], what="exited on its own")
    outcomes[run_c["run_id"]] = "completed"

    seen = {}
    deadline = time.time() + 25
    while len(seen) < len(outcomes) and time.time() < deadline:
        frame = ws.wait_for("run", timeout=max(1, deadline - time.time()))
        SOCKET_VALIDATOR.validate(frame)
        if frame["outcome"] != "running":
            seen[frame["run_id"]] = frame["outcome"]
    ws.close()
    assert seen == outcomes, f"the outcomes reported are not the outcomes that happened: {seen}"

    # ...and no lane still believes it holds a process.
    for entry in env.srv.get("/api/lanes").json()["lanes"]:
        assert entry["state"]["process"] != "running", entry
    env.unchanged()


# ── 8 · NEGATIVE (mandatory) ─────────────────────────────────────────────────────────────────
#
# SRV-FIX-465 · what a restart does is `docs/product/SPEC.md` §5 `E9`, **"A checkout no record describes"**,
# and that paragraph is `SRV-FIX-432`'s:
#
#   > At startup the tool reads `.factory/lanes/*.json` back and then sweeps `<worktrees>/lane/`
#   > itself, and every entry directly under it is accounted for exactly once: it is a lane, or it
#   > is a warning. […] `state.process` `"not_started"` because adoption starts nothing.
#
# The three cases below asserted, until 2026-09-11, that a restart adopts NOTHING — the behaviour
# `SRV-FIX-432` deliberately inverted and `SPEC.md` now specifies. The property that assertion was
# protecting is still wanted and still true: *a run the tool cannot supervise must not be reported
# as LIVE*. It is simply not carried by `lanes == []`. It is carried by `state.process` and
# `run_id`, which is where it is asserted now.

# An exclusive resource, so that `resources_held == []` after a restart is a fact about the restart
# rather than the identity over a manifest that declares none.
ORPHAN_RESOURCE = "board-master"
ORPHAN_MANIFEST = {ORPHAN_RESOURCE: {"exclusive": True, "needed_by": ["core"],
                                     "description": "one physical thing, and two holders is one "
                                                    "corrupted run"}}


def lane_root_of(root):
    return Path(root) / ".factory" / "worktrees" / "lane"


def rows_of(body):
    return {l["lane"]: l for l in body["lanes"]}


def unaccountable(body):
    """Every `unaccountable_worktree` warning E9 is carrying, by real path."""
    return {os.path.realpath(w["path"]): w for w in body.get("warnings", [])
            if w.get("code") == "unaccountable_worktree" and "path" in w}


def test_the_tool_dying_does_not_orphan_a_run(agentenv):
    """Kill the factory itself mid-run, restart it, and account for what it left behind.

    This is the failure mode that costs a person their machine: a supervisor that leaks children
    turns every crash into a process leak. It is the case nobody writes because it requires
    killing the thing under test, so it is written first and asserted hardest.

    `SIGKILL`, not `SIGTERM` — a shutdown path that gets to run its own cleanup proves nothing
    about a tool that dies.
    """
    env = agentenv.build()
    lane = env.claim()
    marker = uuid.uuid4().hex[:12]
    run = env.start(lane, prompt=f"wedge marker={marker}")
    env.wait_log(lane, "wedged")

    # The same wait for the same reason as `test_kill_wedged_process` — SRV-FIX-034. This case
    # carried the identical race: it is the same stand-in, the same log line and the same look at
    # the process table, and leaving one of the two behind would have left the class alive in the
    # file the fix was written in.
    assert wedge_child(env, lane, marker), "nothing to orphan — the run never started"
    assert proc_state(run["pid"]) not in (None, "Z"), "the run was already gone before the test"

    stdin_path = None
    for entry in Path("/proc").iterdir():
        if entry.name == str(run["pid"]):
            try:
                stdin_path = os.readlink(entry / "fd" / "0")
            except OSError:
                stdin_path = None

    os.kill(env.srv.proc.pid, signal.SIGKILL)
    env.srv.proc.wait(timeout=10)

    assert_reaped(run["pid"], within=15, what="ORPHANED by the tool's death")

    deadline = time.time() + 15
    left = descendants_alive(f"wedgechild-{marker}")
    while left and time.time() < deadline:
        time.sleep(0.1)
        left = descendants_alive(f"wedgechild-{marker}")
    assert not left, (
        f"the tool died and left {left} running. Every crash of the tool is now a process leak, "
        f"and nothing on this machine will ever clean these up.")

    if stdin_path:
        holders = holders_of_stdin(stdin_path)
        assert not holders, (
            f"processes {holders} are still holding {stdin_path} open as stdin, and the tool that "
            f"owned the other end is gone — this is a child with an open stdin nobody owns")

    # …and the restart reports what `SPEC.md` §5 `E9` says it reports — the paragraph quoted at the
    # head of this section, which is `SRV-FIX-432`'s. The checkout is still on disk and `S5` makes
    # this endpoint the only authority on what is in that directory, so a row is the right answer
    # and `[]` was the wrong one. Every field below is §E9's own word for it.
    log = env.srv.log.parent / "restart.log"
    restarted = _start_instance(env.root, log, FACTORY_TEST_HOOKS="1")
    try:
        body = restarted.get("/api/lanes").json()
        rows = rows_of(body)
        on_disk = sorted(p.name for p in lane_root_of(env.root).iterdir())
        assert set(rows) == {lane["lane"]}, (
            f"the restart answered about {sorted(rows)} while the lane root holds {on_disk} — "
            f"`S5` makes GET /api/lanes the only authority on what is in that directory")

        row = rows[lane["lane"]]
        assert (row["branch"], row["ticket_id"], row["role"]) \
            == (lane["branch"], "TCK-001", "run"), (
            f"the restart did not read the lane back off the branch on disk: {row}")
        assert row["state"]["process"] == "not_started", (
            f"the restart reports {row['state']['process']!r} for a run it did not inherit — "
            f"§E9: `state.process` `not_started` because adoption starts nothing: {row['state']}")
        assert row["run_id"] is None and row["session_id"] is None, (
            f"the restart is reporting a run and a session it cannot supervise: "
            f"run_id={row['run_id']!r} session_id={row['session_id']!r}")
        assert row["resources_held"] == [], (
            f"the restart handed {row['resources_held']} back to a lane in which nothing is "
            f"running; `{ORPHAN_RESOURCE}` is exclusive, and the next lane to claim it would be "
            f"the second holder of one physical thing")

        assert os.path.realpath(row["worktree"]) not in unaccountable(body), (
            f"the lane's own checkout is BOTH a lane and a warning, and §E9 accounts for every "
            f"entry under the lane root exactly once: {body.get('warnings')}")
        assert not descendants_alive(f"wedgechild-{marker}"), "the restart resurrected the orphan"
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()

    # …and §E9's other half, on the same lane. With the records gone — the state measured on this
    # repository on 2026-09-06, `.factory/lanes/` empty and the checkout on disk — the restart
    # reaches this lane by SWEEPING the lane root rather than by reading a file back, and answers
    # the same way. A case that only ever left the record behind cannot tell the two apart, which
    # is `SRV-FIX-432`'s own finding about the three cases `test_lane_recovery.py` had.
    for record in (Path(env.root) / ".factory" / "lanes").glob("*.json"):
        record.unlink()
    adopt_log = env.srv.log.parent / "adopt.log"
    swept = _start_instance(env.root, adopt_log, FACTORY_TEST_HOOKS="1")
    try:
        body = swept.get("/api/lanes").json()
        rows = rows_of(body)
        assert set(rows) == {lane["lane"]}, (
            f"with no record beside it the checkout went unanswered: {sorted(rows)} — the lane "
            f"set is the set of CHECKOUTS under the lane root, never the set of files the last "
            f"process happened to leave")
        row = rows[lane["lane"]]
        assert (row["branch"], row["ticket_id"], row["role"]) \
            == (lane["branch"], "TCK-001", "run"), (
            f"the sweep did not read the lane back off the branch on disk: {row}")
        assert row["state"]["process"] == "not_started", (
            f"§E9: `state.process` `not_started` because adoption starts nothing: {row['state']}")
        assert row["run_id"] is None and row["resources_held"] == [], (
            f"an adopted lane was given a run id or an exclusive resource: {row}")
    finally:
        stop_process(swept.proc)
        swept.handle.close()

    env.unchanged()


# ── 8b · SRV-FIX-465 · adopted is not supervised ─────────────────────────────────────────────

def test_an_adopted_lane_is_not_reported_as_supervised(agentenv):
    """The property `lanes == []` used to carry, on the axis that actually carries it.

    Two lanes that DISAGREE, because `state.process != "running"` is the identity over a fixture
    in which nothing is running and would pass on a tool that had forgotten how to start an agent
    at all:

      · `lane/01` — claimed, run, and orphaned by the tool's own `SIGKILL`, with its record
        removed afterwards so the restart reaches it through §E9's ADOPTION half. This is the
        state measured on this repository on 2026-09-06 and written into `docs/operations/ESCAPES.md`:
        `.factory/lanes/` empty, checkouts on disk.
      · a second lane the RESTARTED tool claims and runs itself, which it is supervising.

    Adopted and supervised are then two answers from one call, and the assertion can tell them
    apart.
    """
    env = agentenv.build(resources=ORPHAN_MANIFEST)
    orphan = env.claim(ticket="TCK-001")
    marker = uuid.uuid4().hex[:12]
    run = env.start(orphan, prompt=f"wedge marker={marker}")
    env.wait_log(orphan, "wedged")
    assert wedge_child(env, orphan, marker), "nothing to orphan — the run never started"

    os.kill(env.srv.proc.pid, signal.SIGKILL)
    env.srv.proc.wait(timeout=10)
    assert_reaped(run["pid"], within=15, what="ORPHANED by the tool's death")

    # The records go, the checkout stays: `adopt_()` is what answers about it now, and a case that
    # left the record behind would be asserting `recover_()` while naming adoption.
    records = Path(env.root) / ".factory" / "lanes"
    for f in records.glob("*.json"):
        f.unlink()
    assert lane_root_of(env.root).is_dir(), "the fixture lost the checkout it is about"

    log = env.srv.log.parent / "restart.log"
    restarted = _start_instance(env.root, log, FACTORY_TEST_HOOKS="1")
    live_pid = None
    try:
        claimed = restarted.post("/api/lanes", json={"ticket_id": "TCK-002",
                                                     "workspace": "core"})
        assert claimed.status_code == 201, claimed.text
        mine = claimed.json()
        started = restarted.post(f"/api/lanes/{mine['lane']}/runs", json={})
        assert started.status_code == 201, started.text
        live = started.json()
        live_pid = live["pid"]

        deadline = time.time() + 20
        while time.time() < deadline:
            rows = rows_of(restarted.get("/api/lanes").json())
            if rows.get(mine["lane"], {}).get("state", {}).get("process") == "running":
                break
            time.sleep(0.1)

        body = restarted.get("/api/lanes").json()
        rows = rows_of(body)
        assert set(rows) == {orphan["lane"], mine["lane"]}, (
            f"the fixture needs both kinds and E9 answered about {sorted(rows)}")

        supervised = rows[mine["lane"]]
        assert supervised["state"]["process"] == "running", (
            f"the lane this tool started itself is not running, so `not running` below would be "
            f"the identity over this fixture: {supervised['state']}")
        assert supervised["run_id"] == live["run_id"], supervised

        adopted = rows[orphan["lane"]]
        assert adopted["state"]["process"] != "running", (
            f"the restart claims to be supervising a run it cannot: {adopted['state']} — the "
            f"process it would be steering was killed with the tool that owned it")
        assert adopted["run_id"] is None, (
            f"the restart is reporting run {adopted['run_id']!r} on a lane whose agent it could "
            f"not inherit; there is no process on the other end of that id")
        assert adopted["state"]["process"] != supervised["state"]["process"], (
            f"both rows read {adopted['state']['process']!r}, so this fixture cannot tell an "
            f"adopted lane from a supervised one")
    finally:
        if live_pid is not None:
            with contextlib.suppress(Exception):
                restarted.post(f"/api/lanes/{mine['lane']}/kill", json={"grace_s": 1})
        stop_process(restarted.proc)
        restarted.handle.close()

    if live_pid is not None:
        assert_reaped(live_pid, within=20, what="started by the restarted tool")
    assert not descendants_alive(f"wedgechild-{marker}"), "the restart resurrected the orphan"
    env.unchanged()


# ── 8c · SRV-FIX-465 · NEGATIVE (mandatory) ──────────────────────────────────────────────────

def test_a_stranger_under_the_lane_root_is_a_warning_not_a_lane(agentenv):
    """MANDATORY NEGATIVE — adopting everything under the lane root passes the two cases above.

    §E9 accounts for every entry under the lane root exactly once: *it is a lane, or it is a
    warning*. A tool that answered the first two cases by listing whatever it found would delete
    the second half of that sentence, and the row it invented would carry a ticket id nobody
    filed, a branch this repository does not have, and a seat.

    The stranger here is a checkout of a DIFFERENT repository, on a `ticket/*` branch, sitting
    directly under the lane root. `test_lane_recovery.py` already covers a directory git has never
    heard of and a worktree of THIS repository on a branch outside `ticket/*`; a foreign checkout
    has a `.git`, has the right branch name, and is the one shape that separates *a registered
    worktree of this repository* from *something that looks like one*.
    """
    env = agentenv.build(resources=ORPHAN_MANIFEST)
    mine = env.claim(ticket="TCK-001")

    stranger = lane_root_of(env.root) / "77"
    stranger.mkdir(parents=True)
    git(stranger, "init", "-b", "ticket/TCK-999")
    git(stranger, "config", "user.email", "stranger@test.local")
    git(stranger, "config", "user.name", "somebody else")
    git(stranger, "config", "commit.gpgsign", "false")
    (stranger / "not-ours.txt").write_text("a checkout of another repository\n")
    git(stranger, "add", "-A")
    git(stranger, "commit", "-m", "a repository the tool was never pointed at")
    assert (stranger / ".git").exists(), "the fixture's stranger is not a checkout at all"

    stop_process(env.srv.proc)
    log = env.srv.log.parent / "stranger.log"
    restarted = _start_instance(env.root, log, FACTORY_TEST_HOOKS="1")
    try:
        body = restarted.get("/api/lanes").json()
        rows = rows_of(body)

        entries = sorted(p for p in lane_root_of(env.root).iterdir())
        assert len(entries) >= 2, (
            f"this case swept {entries} and needs a real lane beside the stranger, or it cannot "
            f"tell a refusal from a tool that refuses everything")

        here = os.path.realpath(stranger)
        assert here not in {os.path.realpath(r["worktree"]) for r in rows.values()}, (
            f"a checkout of another repository was adopted as a lane: "
            f"{ {k: v['branch'] for k, v in rows.items()} }")
        assert mine["lane"] in rows, (
            f"the tool's own lane went missing while the stranger was refused: {sorted(rows)} — "
            f"a refusal that also refuses the real thing is not a refusal")

        refused = unaccountable(body)
        assert here in refused, (
            f"{here} is in neither `lanes` nor `warnings`, and §E9 accounts for every entry under "
            f"the lane root exactly once — silence is the answer SRV-FIX-432 exists to remove\n"
            f"  lanes: {sorted(rows)}\n  warnings: {body.get('warnings')}")
        assert len([w for w in body.get("warnings", [])
                    if w.get("code") == "unaccountable_worktree"
                    and os.path.realpath(w["path"]) == here]) == 1, (
            f"the stranger is reported more than once, which is a rail that grows a row per "
            f"boot: {body.get('warnings')}")

        # …and adoption is a READ.
        assert (stranger / "not-ours.txt").read_text() == \
            "a checkout of another repository\n", f"{stranger} was written to"
        assert git(stranger, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() \
            == "ticket/TCK-999", (
            f"{stranger} was moved off its own branch")
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()

    env.unchanged()


# ── SRV-FIX-030 ══════════════════════════════════════════════════════════════════════════════
#
# The four cases below exist because the eight above were all green while the tool could not
# steer a real agent at all. They are the contract the stand-in could not check for itself, and
# every frame in them comes from `fixtures/cli-stdin-2026-07-28-*.json` — captured from
# `claude 2.1.92` by `cli_conformance.py`, which is committed so anybody can re-capture them.


# ── 9 · the argv and the framing, which only land together ───────────────────────────────────

def test_the_argv_frames_stdin_as_the_cli_reads_it(agentenv):
    """`--input-format stream-json`, and one message as one line of JSON the CLI would take."""
    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    argv = env.argv_of(lane)
    assert "--input-format" in argv, (
        "the CLI reads stdin TO EOF AS ONE PROMPT without this flag, so every message after the "
        f"first is silently folded into the first turn: {argv}")
    assert argv[argv.index("--input-format") + 1] == "stream-json", argv
    assert "--print" in argv, (
        f"--input-format and --output-format are documented as working only with --print: {argv}")

    # Measured, not assumed — `cli-stdin-2026-07-28-replay-user-messages.json`: the flag echoes
    # the human's own text back as a `user` frame, which this tool's parser renders as `prose`.
    # The tool already appends the `human` entry itself, with the delivered/held mark the echo
    # cannot carry, so passing it would put every message on the transcript twice.
    assert "--replay-user-messages" not in argv, argv

    # ...and a prompt in the argv is silently DROPPED once the input is framed
    # (`cli-stdin-2026-07-28-argv-prompt-is-dropped.json`), so it travels down the FIFO instead.
    assert "-p" not in argv[argv.index("--print") + 1:], (
        f"a prompt passed in the argv is answered by nobody once the input is framed: {argv}")

    text = "the argv and the framing land together"
    r = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "delivered", r.json()
    env.wait_log(lane, f"in:{text}")

    raw = env.stdin_lines(lane, atleast=1)
    assert len(raw) == 1, f"one message reached the child's stdin as {len(raw)} lines: {raw}"
    frame = json.loads(raw[0])
    assert frame["type"] == "user", frame
    assert frame["message"]["role"] == "user", frame
    assert frame["message"]["content"][0]["type"] == "text", frame
    assert frame["message"]["content"][0]["text"] == text, frame

    # The shape is the CLI's, not this test's: the same keys, in the same nesting, as the line
    # the real CLI answered a turn to.
    def shape(node):
        if isinstance(node, dict):
            return {k: shape(v) for k, v in sorted(node.items())}
        if isinstance(node, list):
            return [shape(v) for v in node]
        return type(node).__name__

    captured = json.loads(cli_conformance.load("stream-json-two-messages")["stdin"][0])
    assert shape(frame) == shape(captured), (
        f"the tool frames a message differently from the line the CLI took:\n"
        f"tool     {json.dumps(shape(frame), sort_keys=True)}\n"
        f"captured {json.dumps(shape(captured), sort_keys=True)}")

    # One message, one `human` entry. If the CLI were echoing user messages back this would be
    # two, which is the whole reason --replay-user-messages is refused above.
    humans = [e for e in env.entries(run, atleast=1) if e.get("kind") == "human"]
    assert [e["text"] for e in humans] == [text], humans

    # ...and the prompt at spawn goes down the same channel, because the argv cannot carry it.
    second = env.claim(ticket="TCK-002")
    run2 = env.start(second, prompt="the prompt is a message like any other")
    env.wait_log(second, "in:the prompt is a message like any other")
    first_line = json.loads(env.stdin_lines(second, atleast=1)[0])
    assert first_line["message"]["content"][0]["text"] == \
        "the prompt is a message like any other", first_line

    for held in (lane, second):
        env.srv.post(f"/api/lanes/{held['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    assert_reaped(run2["pid"])
    env.unchanged()


# ── the CLI's permission mode, which is not the factory's ────────────────────────────────────
#
# TWO VOCABULARIES SHARING ONE KEY, and until this case they were the same key. The manifest's
# `agent.permission_mode` is the FACTORY's standing policy — `ask_all` / `edit_free` /
# `never_ask` (SPEC.md §4.12, E11 `policy_source:"manifest"`) — and the agent CLI's
# `--permission-mode` takes none of those three words. Measured on `claude 2.1.220`, its choices
# are `acceptEdits|auto|bypassPermissions|manual|dontAsk|plan`.
#
# So the value was handed to a flag that cannot take it, and SPEC.md's own documented manifest
# (`"permission_mode": "edit_free"`, §14) built an argv the CLI refuses. A repository that
# declared nothing — this one, until now — got the CLI's DEFAULT mode instead, and under `--print`
# with no `--permission-prompt-tool` and no TTY that mode denies every call it cannot auto-allow
# and there is nobody to ask: an agent that can read and can run almost nothing, silently.
#
# The mode is the tool's decision and not the repository's, because it is a fact about how this
# tool drives the CLI rather than about the repository being worked on (SPEC.md §10).

def test_a_lane_runs_the_cli_in_auto_and_never_hands_it_the_factory_policy(agentenv):
    """`--permission-mode auto`, and the manifest's own policy word stays out of the argv."""
    env = agentenv.build(agent_extra={"permission_mode": "edit_free"})
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    argv = env.argv_of(lane)
    assert "--permission-mode" in argv, (
        "the lane's CLI runs in its default mode, which under --print with no prompt tool and no "
        f"TTY denies every call it cannot auto-allow and asks nobody: {argv}")
    assert argv[argv.index("--permission-mode") + 1] == "auto", argv

    # THE NEGATIVE, and the defect itself: the factory's policy word reaching a flag whose
    # choices do not include it.
    assert "edit_free" not in argv, (
        "the manifest's `agent.permission_mode` reached the CLI's --permission-mode, which takes "
        f"none of ask_all/edit_free/never_ask: {argv}")

    # ...and it is still the factory's policy, read by the module that owns it.
    manifest = env.srv.get("/api/manifest").json()
    assert manifest["agent"]["permission_mode"] == "edit_free", manifest

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 10 · NEGATIVE (mandatory) ────────────────────────────────────────────────────────────────

def test_a_message_that_is_not_valid_json_never_reaches_stdin(agentenv):
    """A quote, a newline and a backslash arrive at the child intact, and as ONE line.

    The failure this guards is not a garbled message. Measured against `claude 2.1.92`
    (`cli-stdin-2026-07-28-a-line-that-is-not-json.json`): a line stdin cannot parse makes the
    CLI print `Error parsing streaming input line` and **exit 1** with no frames at all. So a
    framing that breaks on an apostrophe does not lose a word — it ends the session, mid-run,
    with the run's whole transcript missing and nothing on screen to say why.
    """
    ruined = cli_conformance.load("a-line-that-is-not-json")
    assert ruined["rc"] != 0 and not ruined["results"], (
        f"the capture this case rests on says a bad line was survivable: {ruined}")
    assert "Error parsing streaming input line" in ruined["stderr"], ruined["stderr"]

    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    nasty = 'he said "hi" \\ then a backslash\nand a second line\ttabbed'
    r = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": nasty})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "delivered", r.json()

    raw = env.stdin_lines(lane, atleast=1)
    assert len(raw) == 1, (
        f"a message containing a newline was written to stdin as {len(raw)} lines. The CLI reads "
        f"ONE JSON object per line, so the second line is garbage and the run dies: {raw}")
    try:
        frame = json.loads(raw[0])
    except ValueError as why:
        raise AssertionError(
            f"the tool wrote a line that is not JSON, and against {ruined['cli_version']} that "
            f"does not garble a message — it exits {ruined['rc']} with no frames and no "
            f"transcript.\n  {why}\n  the line: {raw[0]}") from None
    assert frame["message"]["content"][0]["text"] == nasty, (
        f"the text did not survive the framing:\n sent    {nasty!r}\n arrived "
        f"{frame['message']['content'][0]['text']!r}")

    # ...and the run is still alive, which is the half a byte-comparison cannot show. A faithful
    # stand-in exits on a line it cannot parse, exactly as the CLI does, so a broken framing
    # means this next message is never answered.
    assert "input-error" not in env.log(lane), (
        f"the child rejected the line the tool wrote:\n{env.log(lane)}")
    stopped = env.srv.post(f"/api/lanes/{lane['lane']}/interrupt", json={})
    assert stopped.status_code == 200, stopped.text
    again = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": "still here"})
    assert again.status_code == 200, f"{again.text}\nthe child's log:\n{env.log(lane)}"
    assert again.json()["status"] == "delivered", again.json()
    env.wait_log(lane, "in:still here")

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 11 · the stand-in, judged against the real thing ─────────────────────────────────────────

def test_the_stand_in_agrees_with_the_captured_cli(tmp_path):
    """Every line the CLI took, the stand-in takes; every line it died on, the stand-in dies on.

    This is the structural half of `docs/operations/ESCAPES.md`'s row for this ticket. The defect was not
    the missing flag — it was that the stand-in and the code under test were written from one
    assumption, so the suite could only ever confirm it. The arbiter is a recording of the
    dependency, and this case is where the stand-in is held against it.
    """
    script = tmp_path / "agent.sh"
    script.write_text(AGENT_SH)

    def stand_in_verdict(lines):
        work = tmp_path / f"work{len(list(tmp_path.iterdir()))}"
        work.mkdir()
        done = subprocess.run(["/bin/sh", str(script)], cwd=work, text=True,
                              input="".join(l + "\n" for l in lines),
                              capture_output=True, timeout=60)
        log = (work / "agent-log.txt").read_text()
        read = [l[len("in:"):] for l in log.splitlines() if l.startswith("in:")]
        return done.returncode, read, done.stderr

    checked = 0
    for name in ("stream-json-two-messages", "quote-newline-backslash", "a-line-that-is-not-json"):
        capture = cli_conformance.load(name)
        rc, read, err = stand_in_verdict(capture["stdin"])
        cli_took_it = capture["rc"] == 0
        assert (rc == 0) is cli_took_it, (
            f"{name}: the real CLI exited {capture['rc']} on these bytes and the stand-in exited "
            f"{rc}. A stand-in more forgiving than the dependency is how SRV-FEAT-009 passed "
            f"eight criteria against a session that could only ever have one turn.\n"
            f"stand-in stderr: {err.strip()[:300]}\n"
            f"CLI stderr:      {capture['stderr'][:300]}")
        if cli_took_it:
            wanted = [json.loads(l)["message"]["content"][0]["text"] for l in capture["stdin"]]
            assert read == wanted, (
                f"{name}: the CLI answered a turn to each of {wanted} and the stand-in read "
                f"{read}")
            assert len(capture["results"]) == len(capture["stdin"]), (
                f"{name}: {len(capture['stdin'])} messages produced "
                f"{len(capture['results'])} turns — the capture itself says the CLI folded them")
        checked += 1
    assert checked == 3


# ── 12 · E14 and E15, against the real CLI ───────────────────────────────────────────────────

# SRV-FIX-042 QA, finding 3 — the assertion, extracted so the negative can run the SAME one.
#
# The mandatory negative used to assert `len(results) != len(stdin)` over a committed constant
# file, offline, in 0.09s, referencing nothing in the product. Its own docstring conceded it:
# *"If somebody deletes `len(results) == len(sent)` upstairs, this case still describes what it
# was for."* A negative that stays green when the thing it guards is deleted is documentation with
# an `assert` in front of it, and `ESCAPES.md` already lists that as a way a gate lies.
#
# One function now. The live case calls it against a real CLI; the negative calls it against the
# capture of the collapse and requires it to RAISE, and then requires the live case to still call
# it. Delete the call upstairs and the negative goes red.
def one_turn_per_message(sent, out):
    """E14 is one message per turn or it is nothing. Raises `AssertionError` when it is not."""
    assert len(out["results"]) == len(sent), (
        f"{len(sent)} messages steered {len(out['results'])} turn(s) out of "
        f"{out.get('cli_version')}. E14 is one message per turn or it is nothing.\n"
        f"stdin:   {json.dumps(sent, indent=1)[:600]}\n"
        f"results: {out['results']}\n"
        f"stderr:  {(out.get('stderr') or '')[:400]}")


@pytest.mark.skipif(cli_conformance.NO_REAL_CLI is not None,
                    reason=cli_conformance.NO_REAL_CLI or "")
def test_the_real_cli_takes_the_bytes_this_tool_writes(agentenv):
    """A real run's own stdin bytes, replayed into the real CLI: one turn per message.

    `SRV-FEAT-009`'s `E14`/`E15` criteria were green against a script that echoed what it was
    given. This is the same steering — one delivered, two held, an interrupt that flushes them —
    checked against the dependency: the bytes are the tool's, the flags are the tool's, and the
    only thing this case supplies is the count of turns that came back.
    """
    # What SRV-FEAT-009 shipped does the opposite, and this is not an argument, it is a capture:
    # its argv and its three stdin lines, lifted out of the RED run's own child log and replayed
    # into the same CLI. Three messages, ONE turn, `"one\ntwo\nthree"`.
    collapsed = cli_conformance.load("srv-feat-009-steering-collapses")
    assert len(collapsed["stdin"]) == 3, collapsed
    assert len(collapsed["results"]) == 1, (
        f"the capture no longer shows the collapse this ticket fixes: {collapsed['results']}")
    assert (collapsed["results"][0] or "").count("\n") >= 2, collapsed["results"]
    assert "--input-format" not in collapsed["argv_tail"], collapsed["argv_tail"]

    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")
    argv = env.argv_of(lane)

    texts = ["say only the word one", "say only the word two", "say only the word three"]
    first = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": texts[0]})
    assert first.json()["status"] == "delivered", first.json()
    env.wait_log(lane, f"in:{texts[0]}")
    for text in texts[1:]:
        r = env.srv.post(f"/api/lanes/{lane['lane']}/messages", json={"text": text})
        assert r.json()["status"] == "held", r.json()
    stopped = env.srv.post(f"/api/lanes/{lane['lane']}/interrupt", json={})
    assert stopped.json() == {"interrupted": True, "delivered": 2}, stopped.json()
    env.wait_log(lane, f"in:{texts[2]}")

    sent = env.stdin_lines(lane, atleast=3)
    assert len(sent) == 3, sent
    # SRV-FIX-042 QA, finding 1 — THE ORDER OF THE BYTES, actually asserted.
    #
    # The comment forty lines down said the order "is asserted above" and nothing above asserted
    # it: `len(sent) == 3` is true of three copies of the same message, of the three in any order,
    # and of three empty strings. A prose claim in place of an assertion is the same defect this
    # whole ticket is about, one layer up — the case reads green and carries no information.
    #
    # This is the deterministic, tool-owned fact §1 asked for: the bytes came off the CHILD's log,
    # in the order the tool wrote them, and the tool chose every one of them.
    assert [json.loads(line)["message"]["content"][0]["text"] for line in sent] == texts, (
        f"the tool wrote the three messages to the child in the wrong order, or wrote one of them "
        f"twice — which `len(sent) == 3` cannot see:\n{json.dumps(sent, indent=1)}")

    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()

    out = cli_conformance.drive(argv, sent)
    assert not out["timed_out"], out["stderr"]
    assert out["rc"] == 0, f"the CLI refused this run's own stdin: {out['stderr']}"
    one_turn_per_message(sent, out)
    # SRV-FIX-042 — AND HERE IS WHERE THIS CASE USED TO ASSERT A MODEL'S WORD CHOICE.
    #
    # It read `"one" in results[0]`, `"two" in results[1]`, `"three" in results[2]`, and failed
    # about one run in three when a live model answered the second question twice. A green run
    # and a red run described the same, correct, tool behaviour: the assertion carried information
    # about the model and none about the product.
    #
    # §1 asked for an identity the TOOL controls that ties a turn to the message that caused it,
    # and said that if there is none on the wire, THE ABSENCE IS THE FINDING. There is none.
    # Measured against the committed captures: a `result` frame carries `uuid`, `session_id`,
    # `num_turns`, `total_cost_usd` and the prose — and nothing that names its input. The CLI emits
    # no `user` frame at all without `--replay-user-messages` (`user_frames: []` in every capture),
    # and this tool refuses that flag because it would print every message twice.
    #
    # So what is asserted is exactly what the tool owns:
    #   · the COUNT — N messages down stdin, N turns back. That is E14, and it is the whole of the
    #     defect SRV-FEAT-009 shipped: three messages, one turn.
    #   · the ORDER OF THE BYTES — `sent` comes off the child's own log, in the order the tool
    #     wrote them, and the list is compared to `texts` element by element above. That sentence
    #     used to say "is asserted above" while nothing above asserted it (QA, finding 1).
    #   · that every turn ANSWERED — a `result` with nothing in it is a turn that did not happen.
    #
    # What is NOT asserted, deliberately and on the record: which turn answers which message. It is
    # not observable on this wire, so a case that claimed it was reading the model. A reorder of
    # three messages would still produce three turns and this case would not see it — that is the
    # honest limit, and the report says so rather than a green run implying otherwise.
    assert all((got or "").strip() for got in out["results"]), (
        f"a turn came back with nothing in it — the CLI answered no message: {out['results']}")



def test_the_count_still_catches_the_collapse_har_feat_009_shipped():
    """NEGATIVE (mandatory, SRV-FIX-042). Taking the model's words out must not take the DEFECT out.

    `SRV-FEAT-009` wrote one message per line to a run's FIFO and expected one turn each; with the
    default input format `claude 2.1.92` read the whole of stdin as ONE prompt, so a session's worth
    of steering became a single turn. That is the failure the case above exists to catch, and the
    cheap way to stop a flaky assertion is to stop asserting — so this case holds the count against
    the committed capture of the collapse itself.

    Static, offline, no CLI: the capture is the evidence, and the arithmetic is the assertion the
    live case makes. If somebody deletes `len(results) == len(sent)` upstairs, this case still
    describes what it was for, and `test_the_real_cli_takes_the_bytes_this_tool_writes` is the one
    that proves the tool no longer does it.
    """
    collapsed = cli_conformance.load("srv-feat-009-steering-collapses")

    assert len(collapsed["stdin"]) == 3, collapsed["stdin"]
    assert len(collapsed["results"]) == 1, (
        f"the capture no longer shows the collapse: {collapsed['results']}")

    # THE ASSERTION THE LIVE CASE MAKES — the same function, not a restatement of it — run
    # against the defect it is for. It must RAISE.
    with pytest.raises(AssertionError):
        one_turn_per_message(collapsed["stdin"], collapsed)

    # ...and the live case must still be the one calling it. Without this line, deleting the call
    # upstairs leaves this negative green, which is exactly what QA found: a case that cannot fail
    # on the regression it names.
    live = inspect.getsource(test_the_real_cli_takes_the_bytes_this_tool_writes)
    assert "one_turn_per_message(sent, out)" in live, (
        "the live case no longer runs the assertion this negative guards — deleting it is the "
        "cheap way to stop a flaky check, and it is the regression this case exists for")

    # ...and the words the old assertion read are all present in that ONE turn, which is why a
    # word-based check could never have been the thing that caught it.
    only = (collapsed["results"][0] or "").lower()
    assert all(w in only for w in ("one", "two", "three")), (
        f"the collapsed turn does not contain all three messages: {only!r}")

# ── SRV-FIX-048 ══════════════════════════════════════════════════════════════════════════════
#
# Starting a lane did not run the ticket. `app/agent/session.cpp` wrote the prompt to stdin only
# `if (!prompt.empty())`, and the argv frames stdin (`--input-format stream-json`, SRV-FIX-030),
# so a start with an empty composer left a CLI blocked on a pipe nobody would ever write to —
# for ever. On disk, `.factory/runs/run-2-e4a97df3/`: `turns: 0`, `outcome: "running"`, two
# stderr lines, then a `silence` rule at 120 s. The agent worked. It was never given anything.
#
# The mechanism these cases check is deliberately NOT a new one: `agent.start_prompt_file` is
# F31's `prompt_file` for the start a PERSON asks for — one reader, one substituter, one set of
# four placeholders, one load-time refusal (`app/api/prompt.h`). A second implementation of it
# would be this ticket's own defect, one layer up.

START_STREAM = Path(__file__).resolve().parent / "fixtures" / "cli-stream-2026-07-27-simple.jsonl"

# A stand-in that ANSWERS. The criterion is `turns >= 1` without anybody sending a message, and a
# turn ends when the agent says so with a `result` frame — so this replays a committed recording
# of the real CLI rather than inventing one (SRV-FEAT-010's rule, and `docs/operations/ESCAPES.md`'s row for
# a stand-in written from the same assumption as the code under test). The reader is
# `stand_in.FRAME_READER`, held against the captures by the case above.
START_AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
""" + stand_in.FRAME_READER + stand_in.PATH_PROBE + r"""
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "ready" >> "$log"

while : ; do
  if read_frame ; then
    printf '%s\n' "in:$text" >> "$log"
    probe_paths
    cat ./stream.jsonl
    printf '%s\n' "answered" >> "$log"
  else
    break
  fi
done
echo "eof" >> "$log"
"""

# The repository's own words. `{not_a_placeholder}` is here on purpose: a tool that blanks what it
# does not recognise is a tool silently editing somebody's prompt (F31's rule, asserted here
# rather than assumed to carry over).
START_PROMPT = """Run ticket {ticket_path}.

Its id is {ticket_id} and this repository is at {repo_root}.
Triggered by run: {triggered_by_run}.
Leave {not_a_placeholder} exactly as it is.
"""

START_PROMPT_REL = ".factory/prompts/run-ticket.md"


@pytest.fixture
def startenv(tmp_path, serve):
    """A repository that declares what starting a lane MEANS, plus a server.

    Its own synthetic repository, never this one: a start prompt that only works against the
    repository the feature was written in has learned that repository (SPEC.md §10).

    `build()` returns `self` and REBUILDS in place, exactly as the other fixtures here do — a
    case needing two repositories takes them one after the other and checks each before moving
    on, which is what the negative below does.
    """
    made = []
    started = []

    class Env:
        def build(self, *, declare=True, prompt=START_PROMPT, write_prompt=True,
                  prompt_rel=START_PROMPT_REL, start_preflight=None):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent={"command": "/bin/sh agent.sh"})
            (root / "agent.sh").write_text(START_AGENT_SH)
            (root / "stream.jsonl").write_text(START_STREAM.read_text())

            manifest = json.loads((root / ".factory" / "factory.json").read_text())
            if declare:
                manifest["agent"]["start_prompt_file"] = prompt_rel
            if start_preflight is not None:
                manifest["workspaces"]["core"]["start_preflight"] = start_preflight
            (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
            if write_prompt:
                p = root / prompt_rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(prompt)

            git(root, "add", "-A")
            git(root, "commit", "-m", "an agent that answers, and words this repository owns")
            self.root = root
            self.baseline = status_of(root)
            made.append((root, self.baseline))
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        # ── the lane and the run ─────────────────────────────────────────────────────────────
        def claim(self, ticket="TCK-001", workspace="core"):
            r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})
            assert r.status_code == 201, r.text
            return r.json()

        def start(self, lane, **body):
            """E18. The body is passed through, so `start(lane)` IS an empty composer."""
            r = self.srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)
            assert r.status_code == 201, r.text
            run = r.json()
            started.append(run["pid"])
            return run

        def ticket_path(self, ticket_id="TCK-001"):
            """Where the ticket is in the MAIN checkout — the tool's own filesystem."""
            r = self.srv.get(f"/api/tickets/{ticket_id}")
            assert r.status_code == 200, r.text
            return r.json()["path"]

        def lane_ticket_path(self, lane, ticket_id="TCK-001"):
            """Where the same ticket is IN THE LANE — the only filesystem the agent has.

            SRV-FIX-055. Computed here from the two facts a test already knows, so the
            expectation is the SPEC's rule — *the same document, under the worktree the agent
            stands in* — and not whatever the implementation happens to join together.
            """
            rel = os.path.relpath(self.ticket_path(ticket_id), str(self.srv.root))
            return os.path.join(lane["worktree"], rel)

        # ── what the child was actually handed ───────────────────────────────────────────────
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

        def stdin_lines(self, lane):
            """Every line the tool wrote to this run's FIFO, as the CHILD read it.

            Read off the child's log and never off `.factory/runs/<run>/stdin`: that path is a
            named FIFO the server holds open, and reading it from a test blocks for ever.
            """
            return [l[len("raw:"):] for l in self.log(lane).splitlines() if l.startswith("raw:")]

        def delivered(self, lane, atleast=1, timeout=25):
            deadline = time.time() + timeout
            raw = []
            while time.time() < deadline:
                raw = self.stdin_lines(lane)
                if len(raw) >= atleast:
                    return [json.loads(l)["message"]["content"][0]["text"] for l in raw]
                time.sleep(0.05)
            raise AssertionError(
                f"only {len(raw)} message(s) reached the child's stdin within {timeout}s "
                f"(wanted {atleast}); the log is:\n{self.log(lane)}")

        # ── the run's own record, which is where run-2 said `turns: 0` ───────────────────────
        def meta(self, run):
            path = self.root / ".factory" / "runs" / run["run_id"] / "meta.json"
            try:
                return json.loads(path.read_text())
            except (OSError, ValueError):
                return {}

        def wait_turns(self, run, lane, atleast=1, timeout=25):
            deadline = time.time() + timeout
            seen = None
            while time.time() < deadline:
                seen = self.meta(run).get("turns")
                if seen is not None and seen >= atleast:
                    return self.meta(run)
                time.sleep(0.05)
            raise AssertionError(
                f"{run['run_id']} never reached {atleast} turn(s) within {timeout}s; its "
                f"meta.json says turns={seen!r} and {len(self.stdin_lines(lane))} line(s) "
                f"reached its stdin. The child's log:\n{self.log(lane)}")

        def fifo(self, run):
            """The run's stdin channel — STAT'd, never read. Opening it from here blocks."""
            return self.root / ".factory" / "runs" / run["run_id"] / "stdin"

        def kill(self, lane):
            return self.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})

        def row(self, lane):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            for row in r.json()["lanes"]:
                if row["lane"] == lane["lane"]:
                    return row
            raise AssertionError(f"{lane['lane']} is gone: {r.text}")

        def wait_process(self, lane, want, timeout=25):
            deadline = time.time() + timeout
            seen = None
            while time.time() < deadline:
                seen = self.row(lane)["state"]["process"]
                if seen == want:
                    return self.row(lane)
                time.sleep(0.05)
            raise AssertionError(
                f"{lane['lane']} never reached process {want!r} within {timeout}s; it is "
                f"{seen!r}. The child's log:\n{self.log(lane)}")

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()

    for pid in started:
        assert_reaped(pid, within=20, what="(checked in teardown)")
    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed by the end of the test:\n"
            f"before:\n{before}\nafter:\n{status_of(root)}")


def substituted(prompt, *, ticket_id, ticket_path, repo_root, triggered_by_run=""):
    """The four the tool defines, and no fifth. Written out here rather than imported, so the
    expectation is the SPEC's list and not whatever the implementation happens to substitute."""
    return (prompt
            .replace("{ticket_id}", ticket_id)
            .replace("{ticket_path}", ticket_path)
            .replace("{repo_root}", repo_root)
            .replace("{triggered_by_run}", triggered_by_run))


# ── 13 · E18 with an empty composer runs the repository's declared start prompt ───────────────

def test_start_preflight_refuses_before_run_record_and_rechecks_each_start(startenv):
    env = startenv.build(start_preflight={"command": "test -f readiness.ok"})
    lane = env.claim()
    endpoint = f"/api/lanes/{lane['lane']}/runs"
    refused = env.srv.post(endpoint, json={"prompt": "go"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "start_preflight_failed"
    runs = env.srv.get("/api/tickets/TCK-001/runs")
    assert runs.status_code == 200, runs.text
    assert not list((env.root / ".factory" / "runs").glob("run-*"))
    assert not env.log(lane)

    (Path(lane["worktree"]) / "readiness.ok").write_text("ready")
    run = env.start(lane)
    env.wait_log(lane, "ready")
    env.kill(lane)
    assert_reaped(run["pid"])
    (Path(lane["worktree"]) / "readiness.ok").unlink()
    refused_again = env.srv.post(endpoint, json={"prompt": "go", "resume": True})
    assert refused_again.status_code == 409, refused_again.text
    assert refused_again.json()["error"]["code"] == "start_preflight_failed"
    assert len(list((env.root / ".factory" / "runs").glob("run-*"))) == 1
    env.unchanged()


def test_start_preflight_exact_ticket_exemption(startenv):
    env = startenv.build(start_preflight={"command": "exit 7",
                                            "exempt_ticket_ids": ["TCK-001"]})
    lane = env.claim()
    run = env.start(lane, prompt="go")
    env.wait_log(lane, "ready")
    env.kill(lane)
    assert_reaped(run["pid"])
    unrelated = env.claim(ticket="TCK-002")
    refused = env.srv.post(f"/api/lanes/{unrelated['lane']}/runs", json={"prompt": "go"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "start_preflight_failed"
    env.unchanged()


def test_start_preflight_failure_keeps_full_capture_outside_run_history(startenv):
    env = startenv.build(start_preflight={"command":
        "python3 -c 'print(\"x\" * 5000)' ; exit 7"})
    lane = env.claim()
    response = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={"prompt": "go"})
    assert response.status_code == 409, response.text
    detail = response.json()["error"]["detail"]
    assert detail["exit_code"] == 7, detail
    assert detail["output_truncated"] is True, detail
    assert len(detail["output"]) == 4096, detail
    assert Path(detail["capture_path"]).read_text().startswith("x" * 5000)
    assert not list((env.root / ".factory" / "runs").glob("run-*"))
    env.unchanged()


def test_start_preflight_timeout_reaps_child_and_releases_pending_start(startenv):
    marker = "preflight_562_marker"
    command = ("test -f ready.ok || { sh -c 'while :; do sleep 30; done' "
               + marker + " & echo started > preflight-child.pid; wait; }")
    env = startenv.build(start_preflight={"command": command, "timeout_seconds": 1})
    lane = env.claim()
    endpoint = f"/api/lanes/{lane['lane']}/runs"
    child_file = Path(lane["worktree"]) / "preflight-child.pid"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(env.srv.post, endpoint, json={"prompt": "go"})
        deadline = time.time() + 5
        while not child_file.exists() and time.time() < deadline:
            time.sleep(.02)
        assert child_file.exists(), "the confined check never started its child"
        children = descendants_alive(marker)
        assert children, "the preflight's descendant was not visible on the host"
        release = env.srv.delete(f"/api/lanes/{lane['lane']}?force=true")
        assert release.status_code == 409, release.text
        assert release.json()["error"]["code"] == "wrong_state"
        concurrent = env.srv.post(endpoint, json={"prompt": "go"})
        assert concurrent.status_code == 409, concurrent.text
        assert concurrent.json()["error"]["code"] == "start_preflight_running"
        timed_out = pending.result(timeout=10)
    assert timed_out.status_code == 504, timed_out.text
    assert timed_out.json()["error"]["code"] == "start_preflight_timeout"
    for child in children:
        assert_reaped(child, what="preflight child")
    assert not descendants_alive(marker)
    assert not list((env.root / ".factory" / "runs").glob("run-*"))

    (Path(lane["worktree"]) / "ready.ok").write_text("ready")
    run = env.start(lane, prompt="go")
    env.wait_log(lane, "ready")
    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()


def test_server_shutdown_kills_active_preflight_group(startenv):
    marker = "preflight_562_shutdown_marker"
    command = "sh -c 'while :; do sleep 30; done' " + marker + " & wait"
    env = startenv.build(start_preflight={"command": command, "timeout_seconds": 60})
    lane = env.claim()
    endpoint = f"/api/lanes/{lane['lane']}/runs"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(env.srv.post, endpoint, json={"prompt": "go"})
        deadline = time.time() + 5
        children = []
        while not children and time.time() < deadline:
            children = descendants_alive(marker)
            time.sleep(.02)
        assert children, "the confined preflight did not start"
        stop_process(env.srv.proc)
        with contextlib.suppress(Exception):
            pending.result(timeout=7)
    for child in children:
        assert_reaped(child, what="shutdown preflight child")
    assert not descendants_alive(marker)
    assert not list((env.root / ".factory" / "runs").glob("run-*"))
    env.unchanged()


def test_a_start_with_no_prompt_runs_the_declared_start_prompt(startenv):
    """`POST …/runs` with NO `prompt` field, and the run is working without anybody typing.

    This is the whole ticket. A person who pressed Start has already said what they want done;
    the ticket is on the screen behind the composer, and making them retype it is asking a
    question the tool already has the answer to.
    """
    env = startenv.build()
    lane = env.claim()

    run = env.start(lane)                          # the empty composer — no `prompt` field at all
    env.wait_log(lane, "ready")

    delivered = env.delivered(lane, atleast=1)
    assert len(delivered) == 1, (
        f"a start sent {len(delivered)} message(s) down stdin: {delivered}")

    expected = substituted(START_PROMPT,
                           ticket_id="TCK-001",
                           ticket_path=env.lane_ticket_path(lane),
                           repo_root=lane["worktree"]).strip()
    assert delivered[0] == expected, (
        f"delivered:\n{delivered[0]!r}\nexpected:\n{expected!r}")

    # ...and the run REACHES A TURN with nobody sending it anything. `run-2-e4a97df3/meta.json`
    # said `turns: 0` while reporting `running`, and this is that fact inverted.
    meta = env.wait_turns(run, lane, atleast=1)
    assert meta["turns"] >= 1, meta
    assert meta["run_id"] == run["run_id"], meta

    # The channel is still SRV-FEAT-009's named FIFO — stat'd, never read.
    assert env.fifo(run).is_fifo(), f"{env.fifo(run)} is not the run's stdin channel"

    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()


# ── 14 · the words are the repository's, byte for byte ───────────────────────────────────────

def test_the_start_prompt_is_the_repositorys_own_text(startenv):
    """Only the four placeholders the tool defines are substituted; one it does not define is
    left exactly as the repository wrote it rather than blanked.

    F31's rule, asserted here rather than assumed to carry over — the two callers share one
    substituter (`app/api/prompt.cpp`), and this is the case that notices if they stop.
    """
    env = startenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    delivered = env.delivered(lane, atleast=1)[0]
    expected = substituted(START_PROMPT,
                           ticket_id="TCK-001",
                           ticket_path=env.lane_ticket_path(lane),
                           repo_root=lane["worktree"]).strip()
    assert delivered == expected, f"delivered:\n{delivered!r}\nexpected:\n{expected!r}"

    assert "{not_a_placeholder}" in delivered, (
        "a placeholder the tool does not define was blanked instead of left alone — the tool "
        "silently edited somebody's prompt")
    # `{triggered_by_run}` IS one of the four, and for a start a person asked for, its value is
    # nothing. Substituted to empty, not left standing: a token the tool defines must never
    # reach the agent as a token.
    assert "{triggered_by_run}" not in delivered, delivered
    assert "Triggered by run:" in delivered, delivered
    # ...and nothing was added in front or behind. The tool has no opinion about what a ticket
    # is (SPEC.md §10), so it contributes no sentence of its own.
    assert delivered.startswith("Run ticket "), delivered
    assert delivered.endswith("exactly as it is."), delivered

    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()


# ── SRV-FIX-055 ══════════════════════════════════════════════════════════════════════════════
#
# The prompt named a path the agent could not see. `{ticket_path}` was `catalog::Document::path` —
# an absolute path in the MAIN checkout — and a lane binds `.git`, the worktree metadata and its
# own worktree, and nothing else. So the FIRST instruction every lane's agent has ever been given
# named a file that could not exist in the only filesystem it has, while the identical file sat in
# its own working directory the whole time. `{repo_root}` was the main root for the same reason.
#
# Cases 13 and 14 above asserted the old values and were green. They are part of the finding:
# a shared substituter with no test that asks whether the shared value MEANS anything where it
# lands is not shared, it is merely written once.


def test_the_prompt_names_a_path_the_agent_can_actually_open(startenv):
    """THE assertion this whole ticket exists for: the agent OPENS what it was told to read.

    Not "the string looks right" — the stand-in tries every absolute `.md` path in its prompt and
    records `opened:` or `unreadable:`. It runs where a real agent runs, so a path from the tool's
    own filesystem comes back unreadable here for exactly the reason it did in the run that found
    this (`SRV-FIX-053`'s escape answer: a gate proving a run produced OUTPUT proves nothing about
    what it can DO).
    """
    env = startenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "answered")

    probes = [l for l in env.log(lane).splitlines()
              if l.startswith(("opened:", "unreadable:"))]
    assert probes, (
        f"the stand-in found no absolute .md path in its prompt at all; the log is:\n"
        f"{env.log(lane)}")
    assert all(l.startswith("opened:") for l in probes), (
        f"the agent was told to read a file it cannot open:\n  " + "\n  ".join(probes)
        + f"\n\nits working directory is {lane['worktree']}, which is the only filesystem it has.")

    # ...and it is the LANE's copy, not a second path that happens to resolve. The ticket the
    # agent works on has to be the one on the branch it will commit to.
    opened = probes[0][len("opened:"):]
    assert opened == env.lane_ticket_path(lane), (
        f"opened {opened!r}, but the lane's own copy is {env.lane_ticket_path(lane)!r}")

    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()


def test_the_main_checkouts_ticket_path_never_reaches_the_agent(startenv):
    """NEGATIVE (mandatory). The defect, named as the exact string that must be gone.

    The cheap fix is a test that reads whatever the tool produced and calls it correct. This one
    knows what the wrong answer was — `catalog::Document::path`, the main checkout's — and fails
    if it is anywhere in what went down the run's stdin.
    """
    env = startenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    delivered = env.delivered(lane, atleast=1)[0]
    main = env.ticket_path()
    assert main not in delivered, (
        f"the main checkout's ticket path reached the agent:\n  {main}\n"
        f"a lane binds its worktree and not the main checkout, so this path cannot be opened by "
        f"the process that was handed it.\n\ndelivered:\n{delivered}")
    assert str(env.srv.root) + "\n" not in delivered and not delivered.endswith(str(env.srv.root)), (
        f"the main checkout's root reached the agent as {{repo_root}}:\n{delivered}")

    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()


# ── 15 · a person overriding the default is not a second message ─────────────────────────────

def test_an_explicit_prompt_still_wins(startenv):
    """E18 *with* a `prompt` sends that text and NOT the declared file.

    The typed box is a person overriding the default. Appending the declared prompt to it would
    make every deliberate instruction arrive wearing the repository's standing one.
    """
    env = startenv.build()
    lane = env.claim()

    typed = "ignore the standing instruction and only say the word one"
    run = env.start(lane, prompt=typed)
    env.wait_log(lane, "ready")

    delivered = env.delivered(lane, atleast=1)
    assert delivered == [typed], (
        f"an explicit prompt was joined to the declared one instead of replacing it: {delivered}")
    assert "Run ticket " not in delivered[0], delivered[0]
    assert "{not_a_placeholder}" not in delivered[0], delivered[0]

    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()


# ── 16 · NEGATIVE (mandatory) ────────────────────────────────────────────────────────────────

def test_a_started_run_that_produces_nothing_is_not_reported_as_working(startenv):
    """A start the repository HAS declared words for must not produce `run-2` again.

    `run-2-e4a97df3` is the shape: `outcome: "running"`, `turns: 0`, a FIFO nobody ever wrote to,
    and a `silence` rule at 120 s as the only thing that ever said so. Two minutes of a lane
    looking healthy while doing nothing at all — that is the half of the defect that let it live,
    because one word was reported for both "the process is up" and "work is happening"
    (SPEC.md §3.2).

    So the assertion is that the lane LEAVES `running` on its own. `idle` is a turn that ENDED
    (SPEC.md §3.2) and it is unreachable without one, so a lane that reaches it has demonstrably
    done work and is now asking for a person — the state `run-2` never reached in two minutes.
    Revert the fix and nothing is written, no turn ever begins or ends, and the lane sits at
    `running` with `turns: 0` until somebody types: precisely `run-2`, and precisely red.
    """
    env = startenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    meta = env.wait_turns(run, lane, atleast=1)
    assert env.stdin_lines(lane), (
        f"the repository declared what a start means and the tool wrote NOTHING to the run's "
        f"stdin — a CLI reading a framed stdin blocks on an empty one for ever (SRV-FIX-030). "
        f"This is run-2 again:\n{env.log(lane)}")
    assert meta["turns"] >= 1, (
        f"the run reports {meta.get('outcome')!r} with turns={meta.get('turns')!r} — the exact "
        f"pair `run-2-e4a97df3`'s own record carried while the lane sat at PROCESS · RUNNING "
        f"for two minutes: {meta}")

    # ...and the lane leaves `running` BY ITSELF, which is the half a number in a file cannot
    # show. `idle` is a turn that ended; a run nobody ever fed cannot reach it, and `run-2` did
    # not — it reported `running` until it was killed.
    done = env.wait_process(lane, "idle")
    assert done["needs_you"] is True, (
        f"a lane that finished its turn and is waiting for a person asked for nobody: {done}")

    env.kill(lane)
    assert_reaped(run["pid"])
    env.unchanged()

    # ...and the control, which is the OTHER half: a repository that has declared nothing is not
    # made to claim anything either. No bytes are invented, `turns` stays 0, and the lane is one
    # waiting for a person with its composer enabled — E18 for a repository that has never heard
    # of this key, unchanged. `build()` rebuilds in place, so this runs after the first is done.
    quiet = startenv.build(declare=False, write_prompt=False)
    silent_lane = quiet.claim()
    silent_run = quiet.start(silent_lane)
    quiet.wait_log(silent_lane, "ready")
    time.sleep(1.5)                     # long enough that a message which WAS coming would have
    assert quiet.stdin_lines(silent_lane) == [], (
        f"the tool invented a prompt for a repository that declared none:\n"
        f"{quiet.log(silent_lane)}")
    assert quiet.meta(silent_run).get("turns") == 0, quiet.meta(silent_run)

    quiet.kill(silent_lane)
    assert_reaped(silent_run["pid"])
    quiet.unchanged()


# ── SRV-FIX-060 ══════════════════════════════════════════════════════════════════════════════
#
# A lane's worktree is a checkout of a BRANCH; the catalog reads the WORKING TREE. So a ticket
# written and not yet committed is listed, offered a Start button, and handed to the agent as a
# path under a worktree where it has never existed. `SRV-FIX-055` put the path in the right
# filesystem; the file is still not there.
#
# Found by that ticket's own QA session, which re-ran the criteria and then asked what else the
# same first sentence could do:
#
#     PROBE LINES: ['unreadable:…/worktrees/lane/01/docs/tickets/TCK-099.md']
#
# This is the exact user action that produced SRV-FIX-055 — `/new-ticket` writes the file, a
# person clicks Start — and it happened to FEAT-040 twice, both times diagnosed as "the ticket is
# untracked, commit it" and moved on. That is a workaround for a tool handing over a path it never
# checked.


@contextlib.contextmanager
def _uncommitted_ticket(env, ticket_id="TCK-099"):
    """A ticket the catalog can see and the lane's branch cannot — `/new-ticket`'s own output.

    Removed on the way out: every fixture here ends by asserting the main checkout is byte
    -identical, and that assertion is `ESCAPES.md` row 1 and is not weakened for a test's
    convenience.
    """
    p = env.root / "docs" / "tickets" / f"{ticket_id}.md"
    p.write_text(f"---\nid: {ticket_id}\ntitle: written just now\nstatus: todo\n---\n\nbody\n")
    try:
        r = env.srv.get(f"/api/tickets/{ticket_id}")
        assert r.status_code == 200, "the catalog cannot see it, so this case proves nothing"
        yield ticket_id
    finally:
        p.unlink(missing_ok=True)


def test_a_ticket_the_lanes_branch_does_not_have_never_reaches_an_agent_as_a_dead_path(startenv):
    """A start that can never work does not begin, and says which document and which branch.

    E18 already refuses an unreadable `agent.start_prompt_file` with `500 start_prompt_unreadable`
    rather than starting a run that has nothing to run — this is the same rule for the other file
    the prompt names.
    """
    env = startenv.build()
    with _uncommitted_ticket(env) as ticket:
        lane = env.claim(ticket=ticket)

        r = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={})
        assert r.status_code >= 400, (
            f"a run was started against a ticket its own lane cannot open: "
            f"{r.status_code} {r.text}")
        assert r.json()["error"]["code"] == "ticket_not_on_branch", r.json()

        # ...and NOTHING was started. A refusal that leaves a process behind is not a refusal.
        assert env.srv.get("/api/lanes").json()["lanes"], "the lane should still be claimed"
        runs = list((env.root / ".factory" / "runs").glob("*/meta.json"))
        assert not runs, f"a run record exists for a start that was refused: {runs}"


def test_the_answer_names_the_ticket_and_the_branch(startenv):
    """Whichever way it goes, a person is told which document and which branch — not that
    something went wrong."""
    env = startenv.build()
    with _uncommitted_ticket(env) as ticket:
        lane = env.claim(ticket=ticket)

        r = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={})
        detail = r.json()["error"].get("detail", {})
        assert detail.get("ticket_id") == ticket, detail
        assert ticket in str(detail.get("path", "")), detail
        assert lane["branch"] in str(detail.get("branch", "")), detail


def test_a_committed_ticket_still_starts_and_still_opens(startenv):
    """NEGATIVE (mandatory). The cheap fix is to refuse anything the lane cannot see.

    A rule that also refuses the normal path has replaced one dead end with another — and the
    normal path is every ticket this repository has ever run.
    """
    env = startenv.build()
    lane = env.claim()                      # TCK-001, committed by the fixture
    run = env.start(lane)
    env.wait_log(lane, "answered")

    probes = [l for l in env.log(lane).splitlines()
              if l.startswith(("opened:", "unreadable:"))]
    assert probes and all(l.startswith("opened:") for l in probes), (
        f"a committed ticket no longer reaches its agent: {probes}\n{env.log(lane)}")

    env.kill(lane)
    assert_reaped(run["pid"])
# ── SRV-FIX-056 ══════════════════════════════════════════════════════════════════════════════
#
# `crashed` is a word with nothing behind it. `noteExit` receives the wait status the kernel gave
# it, maps it to one of four words, and DISCARDS THE NUMBER — and logs nothing at all, so a real
# crash left no line in the server's log between the run's `201` and six minutes of probe output.
# A session was spent on a five-second crash and could not answer why.
#
# These cases are the difference between a tool that observes a process and one that can be asked
# what happened.

EXIT_AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "ready" >> "$log"
exit 3
"""

# Ends the way E16 does — stdin closes, the agent stops, rc 0. The negative below needs a run that
# finished properly to prove the new fields do not dress it up as a failure.
CLEAN_AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "ready" >> "$log"
while read -r line ; do
  printf '%s\n' "raw:$line" >> "$log"
done
echo "eof" >> "$log"
exit 0
"""


def _meta_when_over(env, run, timeout=25):
    """The run's own record, once it has a terminal outcome."""
    path = env.root / ".factory" / "runs" / run["run_id"] / "meta.json"
    deadline = time.time() + timeout
    seen = {}
    while time.time() < deadline:
        try:
            seen = json.loads(path.read_text())
        except (OSError, ValueError):
            seen = {}
        if seen.get("outcome") and seen["outcome"] != "running":
            return seen
        time.sleep(0.05)
    raise AssertionError(f"{run['run_id']} never reached a terminal outcome; meta.json: {seen}")


def test_a_nonzero_exit_records_its_code(agentenv):
    """The number the kernel handed the tool survives to disk.

    `outcome: "crashed"` is all a person has today, on the card, in `meta.json` and on the RUNS
    tab — the same word three times, carrying nothing. An exit code is the one fact that says
    which crash it was.
    """
    env = agentenv.build(script=EXIT_AGENT_SH)
    lane = env.claim()
    c = env.srv.ws()
    assert c.recv()["m"] == "hello"
    c.send({"m": "subscribe", "scope": {"requirements": True}})
    c.wait_for("snapshot")
    run = env.start(lane)

    meta = _meta_when_over(env, run)
    frames = [f for f in c.drain(2.0) if f.get("m") == "run"]
    assert meta["outcome"] == "crashed", meta
    assert meta.get("exit_code") == 3, (
        f"the run exited 3 and its record cannot say so: {meta}")
    assert meta.get("signal") in (None, 0), (
        f"an ordinary exit was recorded as a signal: {meta}")

    # ...and §1's other half: the M11 frame carries it too. Found unguarded by QA (F2) —
    # `run-view.cpp` could have been reverted with the whole suite green.
    terminal = [f for f in frames if f.get("outcome") == "crashed"]
    assert terminal, f"no terminal M11 run frame arrived: {[f.get('outcome') for f in frames]}"
    assert terminal[-1].get("exit_code") == 3, (
        f"meta.json says 3 and the socket says nothing: {terminal[-1]}")
    assert terminal[-1].get("signal") is None, terminal[-1]
    env.unchanged()


def test_a_signalled_run_records_its_signal(agentenv):
    """A run KILLED and a run that RETURNED the same number are different facts.

    SPEC.md §3.2 makes that distinction one level up — `crashed` and `killed` are shown
    differently — and this is it one level down. Without a guard, an edit that collapsed `signal`
    back into `exit_code` would leave the whole suite green, which is exactly the escape class this
    ticket was opened against.

    THE SIGNAL GOES TO THE PROCESS THE TOOL REAPS, not to something inside the sandbox. This
    ticket's first report argued the sandbox made the case unprovable; QA showed that is wrong as
    stated. It is true only for a process that kills itself INSIDE the namespace — `bwrap` is PID 1
    there and reports its child's death as its own ordinary exit of `128+n` — and false for the
    confinement helper itself, which is the pid `noteExit` waits on. `SRV-FIX-062` is one ticket
    old and is about a suite signalling what it does not own; this signals the run this case
    started, by the pid the tool handed back, and nothing else.

    Not through E17: `kill` sets `killing`, and the outcome would be `killed by you`. What is under
    test is a run that died of a signal the tool did not ask for — `crashed`, WITH the number.
    """
    env = agentenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    os.kill(run["pid"], 9)

    meta = _meta_when_over(env, run)
    assert meta["outcome"] == "crashed", meta
    assert meta.get("signal") == 9, (
        f"a run died on SIGKILL and its record cannot say so: {meta}")
    assert meta.get("exit_code") is None, (
        f"a signalled run was recorded as an ordinary exit — 'returned 9' and 'killed with 9' are "
        f"the same record: {meta}")

    # ...and the log says which, in the same line the ordinary case uses.
    deadline = time.time() + 10
    while time.time() < deadline:
        text = env.srv.log.read_text()
        if run["run_id"] in text and "signal" in text:
            break
        time.sleep(0.1)
    assert [l for l in text.splitlines() if run["run_id"] in l and "signal 9" in l], (
        f"the log says a run crashed and not that a signal did it:\n{text[-2000:]}")
    env.unchanged()


def test_a_run_that_ends_says_so_in_the_log(agentenv):
    """One line, naming the run, the lane and how it ended.

    Reproduced with the server's log open, a crash produced NOT ONE LINE between
    `POST /api/lanes/lane/01/runs 201` and six minutes of delivery-probe output. A terminal
    outcome is a change, and SPEC.md §6's own rule for the socket is one message per change.
    """
    env = agentenv.build(script=EXIT_AGENT_SH)
    lane = env.claim()
    run = env.start(lane)
    _meta_when_over(env, run)

    deadline = time.time() + 10
    text = ""
    while time.time() < deadline:
        text = env.srv.log.read_text()
        if run["run_id"] in text and "crashed" in text:
            break
        time.sleep(0.1)

    lines = [l for l in text.splitlines() if run["run_id"] in l and "crashed" in l]
    assert lines, (
        f"a run ended and the server's log does not mention it. Searched for "
        f"{run['run_id']!r} and 'crashed' in:\n{text[-3000:]}")
    assert any(lane["lane"] in l for l in lines), lines
    assert any("3" in l for l in lines), (
        f"the log says a run crashed and not with what: {lines}")
    env.unchanged()


def test_a_clean_exit_is_not_dressed_as_a_failure(agentenv):
    """NEGATIVE (mandatory). The normal path stays normal.

    The cheap fix is to record and log loudly on every exit. That turns the ordinary end of every
    run into noise, and a field everybody learns to ignore is worse than no field: the whole point
    is that a person reading `crashed` can find out which crash it was.
    """
    env = agentenv.build(script=CLEAN_AGENT_SH)
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(lane, "ready")

    r = env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    assert r.status_code in (200, 202), r.text

    meta = _meta_when_over(env, run)
    assert meta["outcome"] == "ended cleanly", meta
    assert meta.get("exit_code") == 0, meta
    assert meta.get("signal") in (None, 0), meta

    text = env.srv.log.read_text()
    for shout in ("ERROR", "E0", "crashed", "FATAL"):
        assert shout not in text.split(run["run_id"])[-1][:400], (
            f"a clean end was announced as {shout!r}:\n{text[-1500:]}")
    env.unchanged()


# ── SRV-FIX-105 · a reaped lane leaves its agent process alive ────────────────────────────────

def test_removing_a_lane_record_kills_the_process_it_started(agentenv):
    """E13 destroys a lane. Nothing of that lane's may outlive the record.

    The observation this comes from: after `lane/01` had left `GET /api/lanes`, three pids were
    still alive under bwrap with `--chdir` into a directory that no longer existed, still consuming
    tokens, and pointed at by no surface — because the holder was no longer a lane.
    """
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    gone = env.srv.delete(f"/api/lanes/{lane['lane']}?force=true")
    assert gone.status_code == 200, gone.text
    assert all(l["lane"] != lane["lane"] for l in env.srv.get("/api/lanes").json()["lanes"])
    assert_reaped(run["pid"], what="its lane record was removed")


def test_a_lane_whose_worktree_vanished_is_reaped_not_stranded(agentenv):
    """The exact state that produced this ticket.

    A worktree removed under a running lane leaves the tool reporting `crashed` with
    `detail.reason: "worktree_missing"` — a diagnosis, not a reaping. The process kept running, and
    it kept HOLDING what it held: the lane behind it read
    `waiting_for {resource: board-master, position: 1}` on a board a corpse still owned.
    """
    import shutil
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    shutil.rmtree(lane["worktree"], ignore_errors=True)
    assert not os.path.exists(lane["worktree"])

    # The wait is on `/proc` and nowhere else. This case used to open with a loop that called
    # `GET /api/lanes` up to 60 times, which is what made round 1 green while the defect stood —
    # the loop was written to observe the state and turned out to cause it. Deleted rather than
    # out-voted by a new case beside it (`docs/TICKET-WORKFLOW.md`, *"An assertion may not be
    # driven by the mechanism it measures"*, which this ticket landed).
    assert_reaped(run["pid"], within=SWEEP_GRACE_S, what="its worktree was removed under it")

    # The resource half is asserted in `test_a_queued_lane_advances_when_the_holder_loses_its_
    # worktree`, against a manifest that actually declares one. The check that stood here read
    # `resources_held` on a fixture with no `resources:` at all, so it was `[]` for every lane
    # however the code behaved, twice over — the lane is not in `lanes` at this point either.
    assert all(l["lane"] != lane["lane"] for l in env.srv.get("/api/lanes").json()["lanes"])


# ── SRV-FIX-105 QA round 1 · the reap may not be something only a reader triggers ─────────────
#
# Every case below is transcribed from the probes the QA session committed under
# `factory/docs/reports/SRV-FIX-105/` — `test_qa105_probe.py` (PROBE-A, PROBE-B) and
# `test_qa105_probe2.py` (PROBE-C, PROBE-D) — not from the criteria's names.
#
# THE LOAD-BEARING PART OF EVERY ONE OF THEM IS THE ABSENCE OF `GET /api/lanes`, not a call to it.
# Round 1 put the reap inside `lane::Store::list`, which is E9's handler, and the criterion's own
# assertion polled E9 up to 60 times inside its wait loop: the test drove the mechanism it was
# measuring. QA removed the polling and the original defect reproduced against that build —
# `after 20s with no E9 call: /proc/400793 state = 'S'`. So a wait here is a wait on `/proc`, or a
# `sleep`, and never a request.

SWEEP_GRACE_S = 35   # two confirming sweeps of the store's own cadence, plus a loaded machine


def cmdline_of(pid):
    """`/proc/<pid>/cmdline` as a string, or '' — what a command-line pattern match reads."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def a_bystander_the_tool_did_not_start(lane):
    """A process carrying the lane, the ticket and the worktree **in its argv**.

    `SRV-FIX-062`'s own bystander is the model — `test_isolation.py::_a_helper_that_is_not_ours`,
    *"a real confinement helper carrying REPO_ROOT in its argv"*. This ticket's first two rounds
    put the three strings in `env=` instead, where no command-line match reads them, and then
    claimed in a docstring that the bystander defended against a command-line match. QA measured
    it: `PROBE-F bystander pid=456323 argv='sleep 600'` ·
    `pgrep -f 'lane/01' -> [456319, 456320] (bystander 456323 ABSENT)`.

    So the shape is the assertion. Every caller checks the precondition below before it asserts
    anything about survival — a negative whose fixture never built the state is not a negative.
    """
    # `sh -c <script> <argv0> <args…>` and deliberately no `exec`: the shell stays alive carrying
    # all three markers in its own argv, which is what a pattern match reads. `sleep 600 lane/01
    # …` was the first attempt and `sleep` rejects the extra arguments — the precondition below
    # caught it, which is the whole point of having one.
    return subprocess.Popen(
        ["/bin/sh", "-c", "sleep 600", lane["lane"], "TCK-001", lane["worktree"]], cwd="/tmp")


def assert_the_bystander_is_reachable_by_a_pattern_match(bystander, lane, timeout=15):
    """The precondition, transcribed from QA's PROBE-F. RED whenever the markers leave argv.

    The wait is on the PRECONDITION and the assertion is made once afterwards — the distinction
    `wedge_child` above is written about, and for the same reason. `Popen` returns at the *fork*,
    so for the next few milliseconds the child is still running the parent's command line and
    `/proc/<pid>/cmdline` is empty or python's. This was measured by the gate rather than
    reasoned about: the case passed on an idle machine and failed inside the full suite with
    `argv is ''`, which is the flake, not the defect.

    Retrying the assertion under test would hide a real regression. Waiting for the fixture to
    finish building its state is what lets the assertion mean anything, and a fixture that never
    reaches the state fails here rather than silently testing nothing.
    """
    markers = (lane["lane"], "TCK-001", lane["worktree"])
    deadline = time.time() + timeout
    argv = ""
    while time.time() < deadline:
        argv = cmdline_of(bystander.pid)
        if all(m in argv for m in markers) and bystander.pid in descendants_alive(lane["lane"]):
            return
        if bystander.poll() is not None:
            break                       # it exited; whatever argv it had is all there will be
        time.sleep(0.05)
    raise AssertionError(
        f"the negative's bystander is not reachable by a command-line pattern match within "
        f"{timeout}s, so the cheap fix it exists to reject — kill anything whose command line "
        f"mentions the lane, the ticket or the worktree — would leave it alone and this case "
        f"would pass without testing anything.\n"
        f"  pid {bystander.pid}, exited={bystander.poll()!r}\n"
        f"  argv is {argv!r}\n"
        f"  wanted all of {markers!r}\n"
        f"  pgrep -f {lane['lane']!r} -> {descendants_alive(lane['lane'])}")


def test_a_vanished_worktree_is_reaped_with_nobody_calling_e9(agentenv):
    """PROBE-A, and 062's guarantee re-asserted on the path that does the reaping.

    QA's raw output against round 1's build:

        PROBE-A after 20s with no E9 call: /proc/400793 state = 'S'
        AssertionError: the run whose worktree vanished (pid 400793) is still alive in state 'S'
        20s later, because nobody called GET /api/lanes

    `assert_reaped` reads `/proc` and nothing else, so this case never touches the API after the
    worktree goes. The bystander is here rather than in a case of its own because the sweep is a
    SECOND route into the kill — `test_reaping_never_touches_a_process_it_did_not_start` covers
    E13's, and a broad pattern match added to this one would pass that case untouched.
    """
    import shutil
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    bystander = a_bystander_the_tool_did_not_start(lane)
    try:
        assert_the_bystander_is_reachable_by_a_pattern_match(bystander, lane)
        shutil.rmtree(lane["worktree"], ignore_errors=True)
        assert not os.path.exists(lane["worktree"])

        assert_reaped(run["pid"], within=SWEEP_GRACE_S,
                      what="whose worktree vanished while nobody called GET /api/lanes")
        assert bystander.poll() is None, "the sweep killed a process it did not start"
    finally:
        bystander.kill()
        bystander.wait(timeout=10)


def test_a_queued_lane_advances_when_the_holder_loses_its_worktree(agentenv):
    """PROBE-B — §1's *"why it matters beyond the tokens"*, with nobody asking E9.

    QA's raw output against round 1's build:

        PROBE-B second claim: 201 {"lane":"lane/02",…"state":{"process":"queued",…
        PROBE-B after 20s, no E9 call: /proc/401071 state = 'S'
        AssertionError: pid 401071 still 'S' while a lane queues on what it holds

    The E9 call at the end is made AFTER the process is already gone, so it cannot be what freed
    the board; before that point this case asks the API nothing.
    """
    import shutil
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    holder = env.claim(ticket="TCK-001")
    run = env.start(holder)
    env.wait_log(holder, "ready")

    queued = env.srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "core"})
    assert queued.status_code == 201, queued.text
    assert queued.json()["state"]["process"] == "queued", queued.text
    waiter = queued.json()["lane"]

    shutil.rmtree(holder["worktree"], ignore_errors=True)

    assert_reaped(run["pid"], within=SWEEP_GRACE_S,
                  what="holding an exclusive resource a second lane is queued on")

    body = env.srv.get("/api/lanes").json()
    rows = {l["lane"]: l for l in body["lanes"]}
    assert holder["lane"] not in rows, f"the reaped lane is still a lane: {body['lanes']}"
    assert rows[waiter]["resources_held"] == ["board"], (
        f"the queue never moved: {rows[waiter]}")
    assert [r for r in body["resources"] if r["name"] == "board"][0]["held_by"] == waiter, (
        f"a corpse still holds the board: {body['resources']}")


def test_a_claimed_lane_that_never_ran_releases_the_resource_it_holds(agentenv):
    """PROBE-D — the resource half of the same defect, with no process in it at all.

    QA's raw output against round 1's build, whose sweep was gated on `record.pid > 0`:

        PROBE-D lanes after its worktree vanished: [{'lane': 'lane/01', …
          'state': {'process': 'crashed', …}, 'detail': {'reason': 'worktree_missing', …},
          'resources_held': ['board'], …}]
        AssertionError: a lane with no worktree still holds
          [{'name': 'board', …, 'held_by': 'lane/01', 'queue': []}]

    There is no `/proc` entry to watch here, so the discriminator is WHICH call sees the free
    board: this case waits in silence and then makes exactly ONE request, which must ALREADY show
    the seat free. A reap that happens inside E9's handler cannot satisfy that — the snapshot it
    answers with is taken before its own release lands.
    """
    import shutil
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    lane = env.claim(ticket="TCK-001")
    assert lane["resources_held"] == ["board"], lane

    shutil.rmtree(lane["worktree"], ignore_errors=True)
    time.sleep(SWEEP_GRACE_S)                       # deliberately NOT a poll of anything

    body = env.srv.get("/api/lanes").json()         # the FIRST call since the worktree went
    held = [r for r in body["resources"] if r.get("held_by")]
    assert not held, f"a lane with no worktree still holds {held}"
    assert all(l["lane"] != lane["lane"] for l in body["lanes"]), (
        f"the lane whose worktree vanished is still claimed: {body['lanes']}")


def test_e9_stays_answerable_while_a_vanished_lane_is_being_reaped(agentenv):
    """PROBE-C — recorded by QA as a thing that is RIGHT, and kept so it stays right.

    Eight concurrent readers while a lane is reaped underneath them: the server keeps answering
    and the release does not run twice.

    Round 2 also asserted `lanes == []` here, which was this ticket writing the `SPEC.md` §E9
    violation into the suite as a requirement. What the row does is
    `test_lane.py::test_a_vanished_worktree_is_reported_before_it_is_removed`'s business, and it
    is asserted there against the documented grace rather than against whatever this build does.
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    shutil.rmtree(lane["worktree"], ignore_errors=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = [f.result().status_code
                 for f in [pool.submit(env.srv.get, "/api/lanes") for _ in range(8)]]
    assert codes == [200] * 8, codes

    assert_reaped(run["pid"], within=SWEEP_GRACE_S, what="raced by eight concurrent readers")
    alive = env.srv.get("/api/lanes")
    assert alive.status_code == 200, alive.text
    assert "remove_failed" not in env.srv.log.read_text(), (
        "the reap released the same lane twice; the second one found the directory already gone")


# ── SRV-FIX-105 QA round 3 · the sweep's decision, every cell of it ───────────────────────────
#
# Three rounds, three defects, all in one function, and each round's fix opened the next one:
#
#   round 1  the reap only ran when somebody called GET /api/lanes
#   round 2  one ambiguous `stat` authorised a SIGKILL — `chmod 000` destroyed two lanes
#   round 3  the guard that fixed round 2 refused on `Absent` as well as on `Unknown`, so
#            deleting the lane root stranded every lane under it — §1's defect, one directory up
#
# That is not three unlucky bugs. Every round asserted the case the last verdict named and left the
# rest of the decision unexamined — and round 3's root guard was shipped with NO case that reached
# it at all: `test_a_worktree_that_is_merely_unreadable_is_never_reaped` chmods the lane's immediate
# parent, which leaves the root `Present`, so what that test proved was the per-lane `Unknown`
# branch and never the guard beside it.
#
# So the subject here is the decision, not the third instance of getting it wrong. The sweep reads
# two paths and a counter; this is every combination that a fixture can reach, and each row names
# the case that holds it. A row with no case is a branch nobody is asserting.
#
#   root      | lane worktree | what must happen                    | asserted by
#   ----------|---------------|-------------------------------------|-------------------------------
#   Present   | Present       | nothing; counter resets             | every other case in this suite
#   Present   | Absent        | reap, after two confirmations       | …is_reaped_with_nobody_calling_e9
#   Present   | Unknown       | nothing at all                      | …merely_unreadable_is_never_reaped
#   Absent    | (all gone)    | reap every lane under it            | …lane_root_that_was_deleted…
#   Unknown   | (unaskable)   | nothing at all                      | …lane_root_that_cannot_be_read…
#   Present   | Absent→Unknown→Absent | the two confirmations must be CONSECUTIVE | …confirmations_must_be_consecutive
#
# The last row is QA's own observation, which it recorded and explicitly declined to file as a
# finding because it built no fixture for it: *"a lane that reads `Absent` once and then `Unknown`
# for an hour appears to keep its single confirmation."* Under default-refuse an unreproduced
# suspicion is not evidence — so this is the fixture, and it decides the question either way.


def test_a_lane_root_that_was_deleted_reaps_every_lane_under_it(agentenv):
    """QA round 3, PROBE-G — and the way a person actually causes this.

    Root `CLAUDE.md`'s warning is about a session that removed four lane worktrees at once. That is
    `rm -rf .factory/worktrees`, not the removal of one lane's own directory, and against the
    round-3 build it stranded everything under it:

        PROBE-G removing the lane root …/repo0/.factory/worktrees
        PROBE-G after 35s with no E9 call: /proc/632915 state = 'S'
        PROBE-G resources: [{'name': 'board', …, 'held_by': 'lane/01', 'queue': []}]

    A live process, a held board and `crashed · worktree_missing` — §1's opening paragraph, one
    directory up. `Absent` is not the filesystem declining to answer; it is the filesystem saying
    every worktree beneath it is gone.
    """
    import shutil
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    # the manifest's `worktreesDir` — NOT the lane's worktree and NOT its immediate parent, which
    # is what round 3's own case removed and is why its guard was never reached
    root = os.path.dirname(os.path.dirname(lane["worktree"]))
    assert root.endswith("/.factory/worktrees"), root
    shutil.rmtree(root, ignore_errors=True)

    assert_reaped(run["pid"], within=SWEEP_GRACE_S,
                  what="whose worktree AND lane root were deleted")

    body = env.srv.get("/api/lanes").json()
    assert body["lanes"] == [], f"the lane outlived the root it lived under: {body['lanes']}"
    assert not [r for r in body["resources"] if r.get("held_by")], (
        f"a lane with no root still holds {body['resources']}")


def test_a_lane_root_that_cannot_be_read_reaps_nothing(agentenv):
    """The other half of the same line, and the half round 3 got right.

    `Unknown` for the root has to keep behaving as it does for a lane: an unreadable parent, a dead
    mount or a stalled network filesystem is not a deletion, and nothing may be killed on it. Both
    cases have to exist together or the next round fixes one by breaking the other — which is
    exactly what happened between rounds 2 and 3.
    """
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    root = os.path.dirname(os.path.dirname(lane["worktree"]))
    above = os.path.dirname(root)                       # <repo>/.factory — the root's own parent
    before = os.stat(above).st_mode
    try:
        os.chmod(above, 0o000)                          # now even `stat(<root>)` cannot answer
        time.sleep(SWEEP_GRACE_S)
    finally:
        os.chmod(above, before)

    assert proc_state(run["pid"]) is not None, (
        f"the run (pid {run['pid']}) was killed because the lane root could not be READ; "
        f"nothing was deleted")
    body = env.srv.get("/api/lanes").json()
    assert [l["lane"] for l in body["lanes"]] == [lane["lane"]], body["lanes"]
    assert [r for r in body["resources"] if r["name"] == "board"][0]["held_by"] == lane["lane"], (
        f"the board was freed because a stat could not answer: {body['resources']}")

    assert env.srv.delete(f"/api/lanes/{lane['lane']}?force=true").status_code == 200
    assert_reaped(run["pid"], what="released by the test that proved it survived")


def test_the_two_confirmations_must_be_consecutive(agentenv):
    """QA round 3's recorded-but-unreproduced observation, given the fixture it lacked.

    `absentPasses` counts sweeps that found the worktree gone, and the reap needs two. The question
    QA raised: does an `Unknown` in between preserve the earlier count, so that two observations an
    hour apart can authorise a kill? They must not — the grace `SPEC.md` §E9 documents is a
    CONSECUTIVE window, and a confirmation separated from its predecessor by an unreadable interval
    has confirmed nothing about the interval it could not see.

    The cost of the safe answer is stated rather than hidden: a filesystem that flaps between
    `Absent` and `Unknown` for ever is never reaped, and the lane has to be released by hand. That
    is the correct direction after round 2 — an unforced kill destroyed two lanes with every file
    intact, and a lane that outlives its grace is recoverable while a killed one is not.
    """
    import shutil
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    parent = os.path.dirname(lane["worktree"])
    before = os.stat(parent).st_mode
    shutil.rmtree(lane["worktree"], ignore_errors=True)
    time.sleep(6)                                       # one sweep sees it Absent: count is 1

    try:
        os.chmod(parent, 0o000)                         # every sweep after this reads Unknown
        time.sleep(14)
    finally:
        os.chmod(parent, before)

    # The instant readability returns, one `Absent` is available again. If the earlier count
    # survived the blind interval, that single observation reaps — twenty seconds after the
    # confirmation it is being paired with.
    time.sleep(6)
    assert proc_state(run["pid"]) is not None, (
        f"the run (pid {run['pid']}) was reaped on two confirmations that were not consecutive: "
        f"one before a 14s interval the tool could not see through, one after it")

    # ...and it is still reaped once two consecutive passes do see it gone.
    assert_reaped(run["pid"], within=SWEEP_GRACE_S,
                  what="after two consecutive confirmations")


def _sweep_passes(env):
    """Every sweep pass the store accounted for, as (candidates, absent, present, unknown).

    The store emits one of these per pass in which anything was absent or unreadable. It is the
    only way a test can see the pass as a WHOLE rather than through one of its outcomes, which is
    the gap four rounds of this ticket kept falling into: each round asserted the cell it was told
    about, and the branch beside it went unlooked-at because nothing could observe the pass itself.
    """
    out = []
    for line in env.srv.log.read_text().splitlines():
        m = re.search(r"sweep: candidates=(\d+) absent=(\d+) present=(\d+) unknown=(\d+)", line)
        if m:
            out.append(tuple(int(g) for g in m.groups()))
    return out


def test_every_sweep_pass_accounts_for_every_lane(agentenv):
    """THE INVARIANT, asserted directly — not another cell of the table.

    Four rounds, four defects, all in `sweep_`, and every one of them was a lane that some pass
    never reached a verdict about:

        round 2  a failed `stat` was a verdict, and it was the wrong one
        round 3  the root guard returned before any lane was judged — `Absent` and `Unknown` alike
        round 4  the root-`Unknown` return still skipped the counter reset for every lane

    Each round fixed the case QA named. Round 4 even wrote the decision out as a table and the
    root-`Unknown` × counter combination still had no row — so the artefact built to stop branches
    hiding had one hiding in it. The pattern is not the individual branch; it is that a pass may
    silently decline to judge a lane, and nothing can tell.

    So this asserts the property rather than the instances: **in every pass, every candidate gets
    exactly one verdict.** `absent + present + unknown == candidates`, whatever the filesystem says
    and wherever it says it. A future early return cannot satisfy this by handling the case it
    knows about, because the arithmetic is over the lanes the pass STARTED with.
    """
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    first = env.claim(ticket="TCK-001")
    second = env.claim(ticket="TCK-002")
    run = env.start(first)
    env.wait_log(first, "ready")

    root = os.path.dirname(os.path.dirname(first["worktree"]))
    above = os.path.dirname(root)

    # (a) one lane's worktree gone, root readable → that lane absent, the other present
    import shutil
    shutil.rmtree(second["worktree"], ignore_errors=True)
    time.sleep(7)

    # (b) the root itself unreadable → NO lane can be judged, and the pass must still account
    #     for both of them
    before = os.stat(above).st_mode
    try:
        os.chmod(above, 0o000)
        time.sleep(12)
    finally:
        os.chmod(above, before)

    passes = _sweep_passes(env)
    assert passes, (
        "the store never accounted for a sweep pass; a pass that cannot be observed as a whole is "
        "how four rounds of this ticket each missed the branch beside the one they fixed:\n"
        + env.srv.log.read_text()[-2000:])

    for candidates, absent, present, unknown in passes:
        assert absent + present + unknown == candidates, (
            f"a sweep pass judged {absent + present + unknown} of {candidates} lane(s) — "
            f"absent={absent} present={present} unknown={unknown}. A lane the pass never reached a "
            f"verdict about keeps whatever state it had, which is exactly how a confirmation "
            f"survives an interval the tool could not see through.\nall passes: {passes}")

    assert any(unknown == candidates and candidates > 0 for _, _, _, unknown in passes), (
        f"no pass reported every lane as unreadable while the lane root was chmod 000 — the "
        f"fixture never built the state this case is about: {passes}")

    assert env.srv.delete(f"/api/lanes/{first['lane']}?force=true").status_code == 200
    assert_reaped(run["pid"], what="released by the invariant case")


def test_an_unreadable_lane_root_also_resets_the_confirmations(agentenv):
    """QA round 4, PROBE-H — the rule `SPEC.md` §E9 states, broken one directory up.

    `test_the_two_confirmations_must_be_consecutive` asserts that an interval the tool could not
    see through resets the count. It chmods the lane's own parent, which leaves the ROOT readable,
    so it only ever exercised the per-lane branch. QA asked the same question of the root, against
    a control differing by one line:

        PROBE-H   chmod 000 <repo>/.factory   → reaped 4.98s later, 1 confirmation line
        CONTROL   chmod 000 …/worktrees/lane  → reaped 10.00s later, 2 confirmation lines

    One sweep is 5.0s. The control needed two fresh confirmations; PROBE-H needed one, having
    carried a confirmation across twenty seconds it could not see through.

    The instrument is the store's own log, never an endpoint: `(1/2)` is written once per FIRST
    confirmation, so counting those lines answers *did it start again* without asking the tool
    anything.
    """
    import shutil
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    root = os.path.dirname(os.path.dirname(lane["worktree"]))
    above = os.path.dirname(root)
    assert root.endswith("/.factory/worktrees"), root

    def confirmations():
        return [l for l in env.srv.log.read_text().splitlines()
                if lane["lane"] in l and "1/2" in l]

    shutil.rmtree(lane["worktree"], ignore_errors=True)
    deadline = time.time() + 20
    while time.time() < deadline and not confirmations():
        time.sleep(0.02)
    assert confirmations(), (
        "the store never logged a first confirmation, so the premise of this case was never "
        f"built:\n{env.srv.log.read_text()[-2000:]}")

    before = os.stat(above).st_mode
    try:
        os.chmod(above, 0o000)          # even `stat(<root>)` cannot answer now
        time.sleep(20)
        assert proc_state(run["pid"]) is not None, (
            f"pid {run['pid']} was reaped WHILE the lane root could not be read")
    finally:
        os.chmod(above, before)

    assert_reaped(run["pid"], within=25, what="after the blind interval ended")
    seen = confirmations()
    assert len(seen) >= 2, (
        f"the reap paired a confirmation made before a 20s unreadable interval with the first one "
        f"made after it: only {len(seen)} '(1/2)' line(s) were ever logged. `SPEC.md` §E9 — "
        f"written by this ticket — says an interval the tool could not see through resets the "
        f"count rather than being counted through.\n" + "\n".join(l.strip() for l in seen))


def test_a_worktree_that_is_merely_unreadable_is_never_reaped(agentenv):
    """A `stat` that could not answer is not a worktree that is gone.

    `git::pathExists` is `fs::exists(p, ec)`, which returns `false` for *deleted*, *unreadable*,
    *parent unmounted* and *symlink loop* alike — and the sweep treats that one `false` as
    authority to SIGKILL a process group and `remove_all` the lane's configuration directory.
    Measured against the round-2 build, with NOTHING deleted:

        >>> chmod 000 for 8s — NOTHING IS DELETED
        >>> permissions restored; worktrees on disk: ['01', '02'] | files intact: [True, True]
        E9 lanes now: []
        E9 resources now: [{'name':'board', ..., 'held_by': None, 'queue': []}]
        W store.cpp:680] lane lane/01 lost its worktree — reaping it and freeing what it held
        W store.cpp:680] lane lane/02 lost its worktree — reaping it and freeing what it held

    Before this ticket, that kill required an operator to type `?force=true`. A transient NFS
    stall, a `chmod`, or a `mv` of the lane root is indistinguishable from `rm -rf` to the check
    that authorises it, and this is the case that says so.
    """
    env = agentenv.build(
        resources={"board": {"exclusive": True, "needed_by": ["core"], "description": "one board"}})
    holder = env.claim(ticket="TCK-001")
    run = env.start(holder)
    env.wait_log(holder, "ready")
    queued = env.srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "core"})
    assert queued.status_code == 201, queued.text
    waiter = queued.json()["lane"]

    work = Path(holder["worktree"]) / "work-nobody-may-lose.txt"
    work.write_text("an agent wrote this and did not commit it\n")

    root = os.path.dirname(holder["worktree"])          # .../.factory/worktrees/lane
    before = os.stat(root).st_mode
    try:
        os.chmod(root, 0o000)
        time.sleep(SWEEP_GRACE_S)                       # every sweep in that window is blind
    finally:
        os.chmod(root, before)

    assert work.read_text().startswith("an agent wrote"), "the worktree lost a file"
    assert proc_state(run["pid"]) is not None, (
        f"the run (pid {run['pid']}) was killed because a directory could not be read; "
        f"nothing was ever deleted")

    body = env.srv.get("/api/lanes").json()
    rows = {l["lane"]: l for l in body["lanes"]}
    assert holder["lane"] in rows and waiter in rows, (
        f"lanes were reaped over an unreadable directory: {body['lanes']}")
    assert rows[holder["lane"]]["resources_held"] == ["board"], rows[holder["lane"]]
    assert [r for r in body["resources"] if r["name"] == "board"][0]["held_by"] == holder["lane"], (
        f"the board was freed because a stat failed: {body['resources']}")

    # This case's whole point is that the run is STILL RUNNING, so it is the one case that has to
    # end its own lane — `agentenv`'s teardown reaps nothing and only checks.
    assert env.srv.delete(f"/api/lanes/{holder['lane']}?force=true").status_code == 200
    assert_reaped(run["pid"], what="released by the test that proved it survived")


def test_a_ticket_can_be_claimed_again_after_its_worktree_vanished(agentenv):
    """The reap leaves git's registration behind, and the next claim adopts the corpse.

    Measured against the round-2 build — three consecutive re-claims of the same ticket:

        round 1: POST /api/lanes -> 201 lane=lane/01 worktree=.../lane/01 exists_on_disk=False
           ...and it was reaped again at t=5.06s

    `release()` returns on `worktree_missing` before it prunes anything, so
    `git worktree list --porcelain` keeps reporting the dead path as `prunable`;
    `worktreeForBranch` finds it, the claim's adopt branch answers `ok` **without stat'ing**, and
    `worktreeReady` is set on a directory that does not exist. The lane is then reaped again on
    the next sweep, for ever. This is the state a person is in immediately after the incident §1
    is about, doing the obvious next thing.
    """
    import shutil
    env = agentenv.build()
    first = env.claim(ticket="TCK-001")
    shutil.rmtree(first["worktree"], ignore_errors=True)
    time.sleep(SWEEP_GRACE_S)
    assert all(l["lane"] != first["lane"] for l in env.srv.get("/api/lanes").json()["lanes"])

    again = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert again.status_code == 201, again.text
    lane = again.json()
    assert os.path.isdir(lane["worktree"]), (
        f"the claim answered 201 for a lane whose worktree is not on disk: {lane['worktree']}")

    time.sleep(SWEEP_GRACE_S)                           # and it must still be there afterwards
    rows = [l["lane"] for l in env.srv.get("/api/lanes").json()["lanes"]]
    assert lane["lane"] in rows, (
        f"the re-claimed lane was reaped again — the claim/reap loop is closed: {rows}")
    assert os.path.isdir(lane["worktree"]), lane["worktree"]


def test_reaping_never_touches_a_process_it_did_not_start(agentenv):
    """NEGATIVE (mandatory). `SRV-FIX-062`'s guarantee, re-asserted.

    The cheap fix for the two above is a broader pattern match — kill anything whose command line
    mentions the lane, the ticket or the worktree. That is exactly the escape 062 exists to stop,
    and it is why this bystander carries all three in its argv.
    """
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    bystander = a_bystander_the_tool_did_not_start(lane)
    try:
        assert_the_bystander_is_reachable_by_a_pattern_match(bystander, lane)
        assert env.srv.delete(f"/api/lanes/{lane['lane']}?force=true").status_code == 200
        assert_reaped(run["pid"], what="the lane was released")
        assert bystander.poll() is None, "the reap killed a process it did not start"
    finally:
        bystander.kill()
        bystander.wait(timeout=10)
