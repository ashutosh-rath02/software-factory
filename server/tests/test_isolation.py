"""SRV-FEAT-002 — an agent cannot touch anything outside its lane.

Every case here drives a **real process** in a real lane. Not a mock and not an agent:

- a mock cannot try what the process that caused this ticket tried, and
- an agent would be testing whether a model *chooses* to stay in its lane, which is the thing
  that already failed. What is under test is whether the confinement HOLDS. If
  `sh -c 'echo x > /outside'` gets through, so will a model with a large prompt.

The safety assertion runs in every case and it is the one that matters most: the main checkout's
`git status` is byte-identical before and after. That assertion is what the escape defeated
(`docs/operations/ESCAPES.md`, row 1), so it is checked first and last.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import jsonschema
import pytest

from test_lane import branch_exists, build_repo, git, status_of, worktree_list

SOCKET_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)

# A gate a real workspace might declare. It is deliberately not a toy: it writes a temporary
# file, compiles something, asks git a question and runs an interpreter — the four things a
# confinement is most likely to break.
GATE_SCRIPT = """#!/bin/sh
set -e
t=$(mktemp)                     # honours TMPDIR
echo 'int main(void){return 0;}' > "$t.c"
cc -o "$t.bin" "$t.c"
"$t.bin"
git status --porcelain >/dev/null
python3 -c "print('gate ok')"
echo done > gate-ran.txt
"""


# ── a lane you can run a real process in ─────────────────────────────────────────────────────

SECRET = "the main checkout's own uncommitted work\n"


@pytest.fixture
def laneenv(tmp_path, serve):
    """A repository whose main checkout holds a file that exists ONLY there, plus a server.

    The secret is written BEFORE the baseline `git status` is taken, so "the main checkout is
    unchanged" and "there is something in it worth stealing" are both true at once.
    """
    made = []

    class Env:
        def build(self, env=None, **kw):
            root = build_repo(tmp_path / f"repo{len(made)}", **kw)

            # The gate script is COMMITTED: a lane's worktree is a clean checkout, so anything
            # a lane is expected to run has to be in the repository, not lying beside it.
            (root / "gate.sh").write_text(GATE_SCRIPT)
            git(root, "add", "gate.sh")
            git(root, "commit", "-m", "gate")

            # ...and the secret is NOT, because "exists only in the main checkout" is the whole
            # point of it. It is written before the baseline, so the checkout is both untouched
            # and worth stealing from.
            (root / "SECRET.txt").write_text(SECRET)
            baseline = status_of(root)
            made.append((root, baseline))
            self.root = root
            self.baseline = baseline
            self.srv = serve(root, FACTORY_TEST_HOOKS="1", **(env or {}))
            return self

        def claim(self, ticket="TCK-001", workspace="core", **extra):
            body = {"ticket_id": ticket, "workspace": workspace}
            body.update(extra)
            r = self.srv.post("/api/lanes", json=body)
            assert r.status_code == 201, r.text
            return r.json()

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()

    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed by the end of the test:\n"
            f"before:\n{before}\nafter:\n{status_of(root)}")


def run_in_lane(env, lane, command, timeout=90):
    """Run a real command in a lane and return (exit_code, output).

    The result is collected through the lane's own worktree rather than from the server,
    because the worktree is writable in both worlds — before this ticket and after it — so the
    same test reads a genuine RED and a genuine GREEN.
    """
    wt = Path(lane["worktree"])
    rc_file, out_file = wt / ".probe-rc", wt / ".probe-out"
    for f in (rc_file, out_file):
        if f.exists():
            f.unlink()

    wrapped = f"{{ {command} ; }} > .probe-out 2>&1 ; echo $? > .probe-rc"
    r = env.srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": wrapped})
    assert r.status_code == 202, r.text

    deadline = time.time() + timeout
    while time.time() < deadline:
        if rc_file.exists():
            rc = rc_file.read_text().strip()
            if rc:
                return int(rc), out_file.read_text() if out_file.exists() else ""
        time.sleep(0.05)
    raise AssertionError(
        f"the process never finished within {timeout}s; output so far: "
        f"{out_file.read_text() if out_file.exists() else '(none)'}")


def test_every_declared_agent_binary_is_on_path_without_credential_directories(laneenv, tmp_path):
    bins = tmp_path / "declared-agent-binaries"
    bins.mkdir()
    for name in ("agent-one", "agent-two"):
        program = bins / name
        program.write_text("#!/bin/sh\necho " + name + "\n")
        program.chmod(0o755)
    credentials = tmp_path / "credentials-that-are-not-a-toolchain"
    credentials.mkdir()
    (credentials / "secret").write_text("not for lane commands\n")

    agents = {
        "one": {"driver": "claude", "command": "agent-one",
                "models": ["one-model"], "default_model": "one-model"},
        "two": {"driver": "codex", "command": "agent-two",
                "models": ["two-model"], "default_model": "two-model",
                "credentials_dir": str(credentials)},
    }
    env = laneenv.build(agents=agents,
                        env={"PATH": str(bins) + os.pathsep + os.environ["PATH"]})

    lane = env.claim()
    rc, out = run_in_lane(
        env, lane,
        "command -v agent-one && command -v agent-two && agent-one && agent-two")
    assert rc == 0, out
    assert "/factory/bin/agent-one" in out and "/factory/bin/agent-two" in out, out
    assert "agent-one" in out and "agent-two" in out, out

    rc, out = run_in_lane(env, lane, f"test ! -e {credentials} && echo CREDENTIALS-HIDDEN")
    assert rc == 0 and "CREDENTIALS-HIDDEN" in out, out
    env.unchanged()


def spawn_in_lane(env, lane, command):
    """Run a command with its output going where the tool can see it — no redirection."""
    r = env.srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": command})
    assert r.status_code == 202, r.text
    return r.json()["pid"]


# ── 1 · inside the lane, work happens ────────────────────────────────────────────────────────

def test_write_inside_lane_succeeds(laneenv):
    """The lane is a place work can actually be done, and the work lands in the lane."""
    env = laneenv.build()
    lane = env.claim()
    wt = Path(lane["worktree"])

    rc, out = run_in_lane(env, lane, "echo 'agent wrote this' > notes.txt")
    assert rc == 0, f"writing inside the lane failed: {out}"

    assert (wt / "notes.txt").read_text() == "agent wrote this\n"
    assert not (Path(env.root) / "notes.txt").exists(), \
        "the file appeared in the MAIN CHECKOUT instead of the lane"
    env.unchanged()


# ── 2 · THE assertion this ticket exists for ─────────────────────────────────────────────────

def test_write_outside_lane_is_refused(laneenv):
    """An absolute path into the main checkout FAILS.

    Not "is discouraged", not "lands somewhere harmless" — fails, with the file absent and the
    main checkout byte-for-byte unchanged. This is the assertion the whole ticket exists for.
    """
    env = laneenv.build()
    lane = env.claim()
    target = Path(env.root) / "PWNED.txt"

    rc, out = run_in_lane(env, lane, f"echo pwned > {target}")

    assert rc != 0, (
        f"THE WRITE SUCCEEDED. An agent told to write into the main checkout was not stopped.\n"
        f"output: {out!r}")
    assert not target.exists(), f"{target} was created in the main checkout"
    env.unchanged()

    # ...and the same for every other shape of "outside", including the tool's own source and
    # the developer's home. A boundary that only knows about one path is not a boundary.
    for path in (Path(env.root) / "README.md",
                 Path(env.root) / ".git" / "config",
                 Path.home() / ".factory-escape-probe"):
        rc, out = run_in_lane(env, lane, f"echo pwned >> {path}")
        assert rc != 0, f"writing {path} succeeded: {out!r}"
        assert not (Path.home() / ".factory-escape-probe").exists()

    assert (Path(env.root) / "README.md").read_text() == \
        "a repository the factory is pointed at\n"
    env.unchanged()


# ── 3 · reading is not harmless ──────────────────────────────────────────────────────────────

def test_read_outside_lane_is_refused(laneenv):
    """Reading is how an agent learns the paths it should not have."""
    env = laneenv.build()
    lane = env.claim()
    secret = Path(env.root) / "SECRET.txt"
    assert secret.read_text() == SECRET, "the fixture did not plant the secret"

    rc, out = run_in_lane(env, lane, f"cat {secret}")
    assert rc != 0, f"THE READ SUCCEEDED: {out!r}"
    assert "main checkout's own uncommitted work" not in out, \
        f"the secret was read out of the main checkout: {out!r}"

    # And it cannot be smuggled into the lane either — the interesting form of the attack, since
    # a file copied into the worktree survives the process that copied it.
    run_in_lane(env, lane, f"cp {secret} ./leaked.txt || cat {secret} > ./leaked.txt")
    leaked = Path(lane["worktree"]) / "leaked.txt"
    assert not leaked.exists() or SECRET.strip() not in leaked.read_text(), \
        "the main checkout's file was copied into the lane"

    # Nor can the main checkout be enumerated.
    rc, out = run_in_lane(env, lane, f"ls -a {env.root}")
    assert "SECRET.txt" not in out, f"the main checkout was listable: {out!r}"
    env.unchanged()


# ── 4 · git is part of the boundary ──────────────────────────────────────────────────────────

def test_commit_lands_on_the_lane_branch(laneenv):
    """A lane that commits produces a commit on `lane/<ticket>` and ZERO new commits on main."""
    env = laneenv.build()
    lane = env.claim()
    root = env.root
    branch = lane["branch"]
    assert branch == "ticket/TCK-001", lane

    main_before = git(root, "rev-parse", "main").stdout.strip()
    branch_before = git(root, "rev-parse", branch).stdout.strip()

    rc, out = run_in_lane(env, lane, (
        "echo 'work an agent did' > feature.txt && git add -A && "
        "git -c user.email=lane@test -c user.name=lane commit -q -m 'lane work'"))
    assert rc == 0, f"an ordinary commit in the lane failed: {out!r}"

    main_after = git(root, "rev-parse", "main").stdout.strip()
    branch_after = git(root, "rev-parse", branch).stdout.strip()

    assert branch_after != branch_before, "the commit did not land on the lane branch"
    assert main_after == main_before, "A COMMIT LANDED ON MAIN"
    count = git(root, "rev-list", "--count", f"{main_before}..main").stdout.strip()
    assert count == "0", f"{count} new commits on main"
    assert git(root, "log", "-1", "--format=%s", branch).stdout.strip() == "lane work"

    # Every route to main, tried explicitly. `git status` on the main checkout is asserted after
    # each one, because "it failed" and "it changed nothing" are different claims.
    for attempt in (
            "git update-ref refs/heads/main HEAD",
            "git branch -f main HEAD",
            f"git push {root} HEAD:main",
            f"git push --force {root} HEAD:refs/heads/main",
            "git symbolic-ref HEAD refs/heads/main",
            f"git --git-dir={root}/.git --work-tree={root} commit -q --allow-empty -m pwned",
            f"git -C {root} commit -q --allow-empty -m pwned"):
        run_in_lane(env, lane, attempt)
        assert git(root, "rev-parse", "main").stdout.strip() == main_before, \
            f"main moved after: {attempt}"
        env.unchanged()

    assert branch_exists(root, branch)
    env.unchanged()


# ── 5 · lanes cannot see each other ──────────────────────────────────────────────────────────

def test_two_lanes_cannot_see_each_other(laneenv):
    """Two agents at once, neither able to see the other's work or the main checkout."""
    env = laneenv.build()
    one = env.claim(ticket="TCK-001")
    two = env.claim(ticket="TCK-002")

    two_only = Path(two["worktree"]) / "lane2-only.txt"
    two_only.write_text("only lane two has this\n")

    rc, out = run_in_lane(env, one, f"cat {two_only}")
    assert rc != 0, f"lane one read lane two's file: {out!r}"
    assert "only lane two" not in out, out

    rc, out = run_in_lane(env, one, f"ls -a {Path(two['worktree']).parent}")
    assert "02" not in out, f"lane one can enumerate lane two: {out!r}"

    rc, out = run_in_lane(env, one, f"echo vandalism > {two_only}")
    assert rc != 0, "lane one wrote into lane two's worktree"
    assert two_only.read_text() == "only lane two has this\n", "lane two's file was modified"

    env.unchanged()


# ── 6 · a refusal is visible, not swallowed ──────────────────────────────────────────────────

def test_refusal_is_reported(laneenv):
    """SPEC.md F9 / M9 — a refused access reaches the lane's event stream with the path.

    A refusal that is only a failed syscall is a refusal nobody sees, which is half of what went
    wrong: nothing stopped it AND nothing reported it.
    """
    env = laneenv.build()
    lane = env.claim()
    target = Path(env.root) / "PWNED-REPORTED.txt"

    ws = env.srv.ws()
    try:
        ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
        ws.wait_for("snapshot")

        spawn_in_lane(env, lane, f"echo pwned > {target}")

        frame = ws.wait_for("isolation", timeout=15)
        SOCKET_VALIDATOR.validate(frame)
        assert frame["lane"] == lane["lane"], frame
        assert frame["refused"] is True, frame
        assert str(target) in frame["path"], frame
        assert frame["operation"] in ("write", "read", "access"), frame
    finally:
        ws.close()

    assert not target.exists()
    env.unchanged()


# ── 7 · MANDATORY NEGATIVE — the jail must not block the job ─────────────────────────────────

def test_confinement_does_not_break_normal_work(laneenv):
    """A jail that also blocks the job is not a solution.

    Ordinary work: read the ticket, edit source, run the workspace's declared gate command, use
    git, use a temporary directory, use the toolchain. All of it completes, and NO refusal is
    reported — a confinement that fires during honest work is a confinement nobody will keep.
    """
    env = laneenv.build(workspaces={"core": {"path": ".", "gate": "sh ./gate.sh"}})
    lane = env.claim()
    wt = Path(lane["worktree"])

    ws = env.srv.ws()
    try:
        ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
        ws.wait_for("snapshot")

        # 1 — read the ticket the lane was claimed for, out of its own checkout.
        rc, out = run_in_lane(env, lane, "cat docs/tickets/TCK-001.md")
        assert rc == 0 and "body of TCK-001" in out, (rc, out)

        # 2 — edit source, make a directory, use a temporary file.
        rc, out = run_in_lane(env, lane, (
            "mkdir -p src && echo 'int x;' > src/a.c && "
            "sed -i 's/int x;/int y;/' src/a.c && grep -q 'int y;' src/a.c && "
            "t=$(mktemp) && echo scratch > \"$t\" && cat \"$t\" >/dev/null"))
        assert rc == 0, f"ordinary editing failed: {out!r}"

        # 3 — the workspace's own declared gate command, whatever it happens to be.
        manifest = json.loads((Path(env.root) / ".factory" / "factory.json").read_text())
        gate = manifest["workspaces"]["core"]["gate"]
        rc, out = run_in_lane(env, lane, gate)
        assert rc == 0, f"the workspace's gate command failed inside the lane: {out!r}"
        assert "gate ok" in out, out
        assert (wt / "gate-ran.txt").exists()

        # 4 — git, in every form a normal session uses it.
        rc, out = run_in_lane(env, lane, (
            "git status --porcelain >/dev/null && git add -A && "
            "git -c user.email=l@t -c user.name=l commit -q -m 'ordinary work' && "
            "git log --oneline -1 >/dev/null && git diff HEAD~1 --stat >/dev/null && "
            "git branch --show-current"))
        assert rc == 0, f"ordinary git failed: {out!r}"
        assert "ticket/TCK-001" in out, out

        # ...and none of that produced a refusal.
        frames = [f for f in ws.drain(1.5) if f.get("m") == "isolation"]
        assert frames == [], f"honest work was reported as an isolation refusal: {frames}"
    finally:
        ws.close()

    env.unchanged()


# ── the confinement is not optional ──────────────────────────────────────────────────────────

def test_the_boundary_survives_being_attacked(laneenv):
    """The routes a process that WANTS out would take, rather than the ones it stumbles into.

    Everything above tests a process doing the wrong thing by accident — an absolute path in a
    prompt. This one assumes the process is trying. It is here because the whole class of
    failure this ticket exists for is "the boundary was described and never tried".
    """
    env = laneenv.build()
    lane = env.claim()
    root = Path(env.root)
    gitdir = root / ".git"
    main_before = git(env.root, "rev-parse", "main").stdout.strip()

    # 1 — hold no capability, so no mount call can succeed. This is the one that would undo
    # everything: a process able to remount `<repo>/.git` read-write owns `refs/heads/main`.
    rc, out = run_in_lane(env, lane, "grep -E '^CapEff|^CapBnd' /proc/self/status")
    assert rc == 0, out
    for line in out.strip().splitlines():
        assert line.split()[1].strip("0") == "", f"the lane holds capabilities: {line}"

    # 2 — ...and the remount attempt itself, driven for real rather than reasoned about.
    rc, out = run_in_lane(env, lane, f"mount -o remount,rw {gitdir} ; echo pwned > {gitdir}/refs/heads/main")
    assert rc != 0, f"the lane remounted its own boundary: {out!r}"
    assert git(env.root, "rev-parse", "main").stdout.strip() == main_before

    # 3 — symlinks and hardlinks do not create a path to something that is not mounted.
    for attempt in (f"ln -s {root}/SECRET.txt s && cat s",
                    f"ln {root}/SECRET.txt h && cat h",
                    f"ln -s {gitdir}/refs/heads/main m && echo pwned > m"):
        rc, out = run_in_lane(env, lane, attempt)
        assert rc != 0 or "uncommitted work" not in out, f"{attempt} → {out!r}"

    # 4 — walking up out of the worktree, and through /proc.
    for attempt in ("cat ../../../../SECRET.txt",
                    "cat /proc/1/root/SECRET.txt",
                    "cat /proc/self/root/SECRET.txt",
                    f"cat /proc/self/cwd/../../../../SECRET.txt"):
        rc, out = run_in_lane(env, lane, attempt)
        assert "uncommitted work" not in out, f"{attempt} escaped: {out!r}"

    assert git(env.root, "rev-parse", "main").stdout.strip() == main_before
    assert not (root / "PWNED.txt").exists()
    env.unchanged()


def test_a_lane_without_confinement_refuses_to_run(laneenv):
    """Fail closed. A lane that cannot be confined does not run a process unconfined.

    The failure mode of a boundary must be "no run", never "run without the boundary" — that is
    exactly the state the tool was in when 659 lines landed on main. Driven by pointing the
    server at a confinement binary that is not there, which is the real way this happens.
    """
    env = laneenv.build(env={"FACTORY_CONFINE_BIN": "/nonexistent/bwrap"})
    lane = env.claim()

    r = env.srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": "echo x > /tmp/should-not-run"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "confinement_unavailable", r.text
    env.unchanged()


# ── SRV-FIX-062 ══════════════════════════════════════════════════════════════════════════════
#
# The leak guard decided a confinement helper was ITS OWN by matching the repository's path
# anywhere in the helper's command line. A lane belonging to a server the suite never started
# matches that exactly as well — so running the suite beside a live lane failed the suite with a
# leak that was not its own AND `os.kill(pid, 9)`ed a person's agent mid-turn, in a worktree with
# uncommitted work.
#
# Measured three times in one day: FEAT-040's runs died at 5, 7 and 8 seconds with `turns: 0` and
# `$0.00` while a suite ran in another terminal, and the same lane with nothing else running
# reached `completed`, 2 turns, $0.68, 310s.
#
# `SRV-FIX-038` closed this exact defect once already, in `app/delivery/probe.cpp` — "a ticket id
# matches any process on the machine". The reasoning never reached `conftest.py`, which does the
# same thing with a path instead of an id, and kills what it matches.

import conftest as _conftest


def _a_helper_that_is_not_ours(env_extra=None):
    """A real confinement helper carrying REPO_ROOT in its argv, started by THIS TEST.

    Not by a server the session started, which is the whole distinction: to a rule that reads the
    command line it is indistinguishable from the suite's own, and to a rule that reads ownership
    it is obviously not.
    """
    env = dict(os.environ)
    env.pop(_conftest.SESSION_ENV, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(
        [_conftest.CONFINE_HELPER, "--ro-bind", "/", "/", "--chdir", str(_conftest.REPO_ROOT),
         "sleep", "30"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_the_leak_guard_ignores_a_helper_this_session_did_not_start():
    """A lane that is not ours is not our leak, and is not ours to kill.

    The second assertion is the one that matters: today the guard SIGKILLs it. A test suite that
    reaches outside its own processes is a test suite that destroys work, and this one destroyed a
    paid agent run three times before anybody noticed the pattern.
    """
    other = _a_helper_that_is_not_ours()
    try:
        time.sleep(0.4)
        assert other.poll() is None, "the stand-in helper died on its own; the case proves nothing"

        owned = _conftest.owned_confinement_helpers()
        assert other.pid not in owned, (
            f"the guard claims a helper this session never started:\n"
            f"  pid {other.pid}: {owned.get(other.pid, '')[:200]}\n"
            f"Ownership is established, never guessed from a path.")

        time.sleep(0.3)
        assert other.poll() is None, (
            "the guard KILLED a process it did not start — this is the half that cost a person "
            "three paid agent runs")
    finally:
        other.kill()
        other.wait(timeout=5)


def test_the_leak_guard_still_catches_its_own():
    """NEGATIVE (mandatory). SRV-FIX-034's criterion, restated.

    The cheap fix is to narrow the rule until nothing is ever caught. A helper carrying this
    session's own mark is still ours, still reported and still killed — because a suite that
    reports a leak and leaves it running makes the next run's baseline worse.
    """
    ours = _a_helper_that_is_not_ours({_conftest.SESSION_ENV: _conftest.SESSION_MARK})
    try:
        time.sleep(0.4)
        assert ours.poll() is None, "the stand-in helper died on its own; the case proves nothing"

        owned = _conftest.owned_confinement_helpers()
        assert ours.pid in owned, (
            f"a helper carrying this session's own mark was not recognised as ours — the guard "
            f"has been narrowed until it catches nothing. Owned: {sorted(owned)}")
    finally:
        ours.kill()
        ours.wait(timeout=5)


# ── SRV-FIX-063 ══════════════════════════════════════════════════════════════════════════════
#
# This repository declares an exclusive resource that IS A DEVICE — `board-master`, "one ESP32 on
# /dev/ttyUSB1" — and the tool queues lanes on it correctly and then puts the winner in a namespace
# where the device does not exist. `confine.cpp` mounts `--dev /dev`, a NEW minimal devtmpfs, so no
# firmware ticket can ever reach GATE in a lane.
#
# The user chose answer (A): the board goes in, declared, narrow, and only for the lane holding the
# resource. `--dev-bind /dev /dev` stays refused — it would hand a lane every disk on the machine.
#
# The devices these cases bind are FILES THE TEST MAKES, not the developer's board. `--dev-bind` is
# a path binding and does not care what is on the other end, so a path that is otherwise absent
# from the sandbox proves the mechanism completely — and opening somebody's ESP32 at five in the
# morning to assert a bind would be this suite reaching outside itself again, which is the defect
# `SRV-FIX-062` was filed for. The real `/dev/ttyUSB*` gets its own case below, and it is honest
# about what it does and does not do.

BOARD = {"board": {"exclusive": True, "needed_by": ["core"], "description": "the bench board",
                   "devices": []}}

# The nodes these cases use are the machine's own. A synthetic file cannot stand in: `--tmpfs /tmp`
# comes after the device binds, deliberately — a lane's `/tmp` is its own — so anything pytest puts
# under `tmp_path` is erased inside the sandbox. Reordering the product to suit a fixture would be
# the fixture writing the boundary, so the cases use real device nodes instead.
DEVICE_DECLARED = "/dev/ttyUSB0"          # named by the test's manifest
DEVICE_UNDECLARED = "/dev/ttyUSB3"        # exists on the host, named by nothing


def _major_minor(path):
    st = os.stat(path)
    return (os.major(st.st_rdev), os.minor(st.st_rdev))


def _with_device(path):
    r = json.loads(json.dumps(BOARD))
    r["board"]["devices"] = [str(path)]
    return r


needs_board = pytest.mark.skipif(
    not Path(DEVICE_DECLARED).exists(),
    reason=f"no {DEVICE_DECLARED} on this machine — THIS IS NOT A PASS, see SRV-FIX-063's report")


@needs_board
def test_a_lane_sees_the_devices_its_resource_declares(laneenv):
    """THE criterion: the node the resource named is THERE, in the lane, and is the same device.

    `test -c` and the major:minor pair, read from inside the sandbox where `--dev /dev` guarantees
    a fresh devtmpfs that has none of the host's nodes. A placeholder file would fail `-c`; a
    different node would fail the numbers.

    It is deliberately NOT opened, and that is a decision rather than an omission: opening a USB
    serial port raises DTR on most drivers and RESETS AN ESP32. The board is the developer's, this
    suite cannot know what is mid-flight on it, and `SRV-FIX-062` — one ticket old — was this suite
    reaching outside its own processes and destroying three paid runs. The bind is what is under
    test, and the bind is proved without touching what is on the other end of the wire.
    """
    env = laneenv.build(resources=_with_device(DEVICE_DECLARED))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"test -c {DEVICE_DECLARED} && echo CHARDEV || echo ABSENT")
    assert rc == 0 and "CHARDEV" in out, (
        f"the lane holds `board`, which declares {DEVICE_DECLARED}, and the node is not in its /dev: "
        f"rc={rc}\n{out}")

    rc2, out2 = run_in_lane(env, lane, f"stat -c '%t:%T' {DEVICE_DECLARED}")
    want = "%x:%x" % _major_minor(DEVICE_DECLARED)
    assert rc2 == 0 and want in out2.strip(), (
        f"the node in the lane is not the host's {DEVICE_DECLARED}: wanted {want}, got {out2!r}")
    env.unchanged()


# ── SRV-FIX-063 QA, finding 1 ────────────────────────────────────────────────────────────────
#
# §1: *"assert a process **inside** the lane can `open()` it"*, and it pre-empted the weaker form
# in the same sentence. §2's GATE repeated it. What shipped asserted the node is PRESENT — `test
# -c` and a major:minor pair — and never opened anything, which re-creates this ticket's own
# subject one level down: `SRV-FEAT-008` asserted the lock and never the use, and that is what
# shipped the bug.
#
# The stated reason was real — opening a USB serial raises DTR and resets an ESP32 — and it is
# answered rather than argued with. `--dev-bind` is a path binding and does not care what is on
# the other end, so the open is proved on a device that is safe to open, and the SERIAL open
# becomes an explicit opt-in that says loudly when it is skipped.
#
# The node is chosen at runtime, and it has to satisfy three things or the case skips with its
# reason on the record: it exists, this user can open it on the host, and a lane that declares
# NOTHING does not already have it. That last one is what makes the assertion mean anything —
# `bwrap --dev` builds a fresh devtmpfs carrying `null`, `zero`, `full`, `random`, `urandom`,
# `tty` and `ptmx`, so declaring one of those would prove the bind by asserting something that
# was already true.
OPENABLE_CANDIDATES = ("/dev/fuse", "/dev/net/tun", "/dev/kvm", "/dev/autofs", "/dev/gpiochip0")


def _host_holder_of(path):
    """The pid of a process on THIS host with `path` open, or None. `lsof`, without lsof.

    Read straight out of `/proc` so the case does not depend on a tool being installed, and so it
    asks the same question `lane::preflight` asks — a fixture that measured the premise a different
    way from the product would be measuring a different premise.
    """
    want = os.path.realpath(path)
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue                      # somebody else's process; not ours to read
        for fd in fds:
            try:
                if os.readlink(f"{fd_dir}/{fd}") == want:
                    return int(entry)
            except OSError:
                continue
    return None


def _openable_on_this_host():
    """A safe character device that this user can open AND that nothing on this host holds.

    The second half is SRV-FIX-077's doing and it is a premise, not a convenience. A `devices`
    entry may only sit on an EXCLUSIVE resource (`SRV-FIX-070`), which is the repository saying
    there is one of that thing and one user of it at a time — so the tool now refuses to hand the
    resource to a lane while a host process already has the node open, and a fixture declaring a
    node the desktop happens to be using is declaring a shared device as exclusive. `/dev/fuse` is
    held by `xdg-document-portal` on an ordinary desktop, which is exactly that.
    """
    for path in OPENABLE_CANDIDATES:
        if not Path(path).exists():
            continue
        if _host_holder_of(path) is not None:
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        os.close(fd)
        return path
    return None


OPENABLE = _openable_on_this_host()

needs_openable = pytest.mark.skipif(
    OPENABLE is None,
    reason="no safe openable character device on this machine out of "
           f"{OPENABLE_CANDIDATES} — THIS IS NOT A PASS. The bind is unproven for `open()` here; "
           "see SRV-FIX-063's report.")


@needs_openable
def test_a_lane_can_open_the_device_its_resource_declares(laneenv):
    """THE criterion, in §1's own words: a process INSIDE the lane can `open()` the declared node.

    Presence is not usability, and the difference between the two is the whole of this ticket.
    `test -c` passes for a node that is bound with the wrong permissions, bound read-only, or
    bound while something else holds it exclusively — every one of which is a lane that holds the
    resource and cannot use it, which is the sentence `SRV-FEAT-008` shipped.

    The open is a shell redirection, which is an `open(2)` and nothing more: no read, no write, no
    ioctl, no line settings. And it is done on a device chosen because it is safe to open, so the
    developer's board is never touched to prove a bind that has nothing to do with what is on the
    other end of the wire.
    """
    # the control: with nothing declared, this node is NOT in the lane. Without this the case
    # could pass on a device `--dev` had provided all along.
    bare = laneenv.build(resources=BOARD)
    lane0 = bare.claim(workspace="core")
    rc0, out0 = run_in_lane(bare, lane0, f"test -e {OPENABLE} && echo THERE || echo ABSENT")
    assert "ABSENT" in out0, (
        f"{OPENABLE} is in a lane that declares no device at all, so opening it in the lane below "
        f"would prove nothing about the bind: rc={rc0}\n{out0}")
    bare.unchanged()

    env = laneenv.build(resources=_with_device(OPENABLE))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(
        env, lane,
        f"if exec 3< {OPENABLE} ; then echo OPENED ; exec 3<&- ; else echo REFUSED ; fi")
    assert rc == 0 and "OPENED" in out, (
        f"the lane holds `board`, which declares {OPENABLE}, and a process inside it could not "
        f"open the node: rc={rc}\n{out}\n"
        f"This is the assertion §1 asked for and the one the first delivery replaced with a "
        f"presence check. A device the lane can see and cannot open is the same defect the "
        f"ticket was written about, one level down.")
    env.unchanged()


@needs_board
@pytest.mark.skipif(
    os.environ.get("FACTORY_TEST_OPEN_SERIAL") != "1",
    reason="opening a USB serial raises DTR and RESETS the board on most drivers, so this is "
           "opt-in: run with FACTORY_TEST_OPEN_SERIAL=1 and nothing mid-flight on the bench. "
           "THIS SKIP IS NOT A PASS — the serial open is unproven in this run. "
           "`test_a_lane_can_open_the_device_its_resource_declares` proves the BIND on a device "
           "that is safe to open; this case proves it on the real board.")
def test_a_lane_can_open_the_real_board(laneenv):
    """The same assertion, on the actual serial node, behind an explicit opt-in.

    §2's GATE rule: *"one case must open the real device on this machine, and say so when it is
    skipped for want of hardware — a skip that reads as a pass is how this defect survived in the
    first place."* The skip reason above says it in as many words, and this case is the one that
    can only be satisfied by hardware.
    """
    env = laneenv.build(resources=_with_device(DEVICE_DECLARED))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(
        env, lane,
        f"if exec 3< {DEVICE_DECLARED} ; then echo OPENED ; exec 3<&- ; else echo REFUSED ; fi")
    assert rc == 0 and "OPENED" in out, (
        f"the lane holds the board and cannot open {DEVICE_DECLARED}: rc={rc}\n{out}")
    env.unchanged()


@needs_board
def test_a_lane_without_the_resource_sees_no_device(laneenv):
    """NEGATIVE (mandatory). SRV-FEAT-002's guarantee, restated for devices.

    The cheap fix is `--dev-bind /dev /dev`, or binding every declared device into every lane.
    Either one passes the case above and fails this one.
    """
    env = laneenv.build(resources=_with_device(DEVICE_DECLARED),
                        workspaces={"core": {"path": "."}, "edge": {"path": "."}})
    lane = env.claim(workspace="edge")      # `edge` needs nothing, so it holds nothing

    rc, out = run_in_lane(env, lane, f"test -e {DEVICE_DECLARED} && echo THERE || echo ABSENT")
    assert "ABSENT" in out, (
        f"a lane that holds no resource reached a declared device anyway: rc={rc}\n{out}")
    env.unchanged()


@needs_board
def test_no_lane_can_open_a_device_no_resource_declared(laneenv):
    """NEGATIVE (mandatory). The tool binds what the manifest names and nothing else.

    A second node on the same host, never mentioned in `.factory/factory.json`, is not reachable
    from the lane holding the resource either — otherwise "declared" would mean "somewhere near
    something declared", and a manifest naming one port would hand over the machine's serial bus.
    """
    env = laneenv.build(resources=_with_device(DEVICE_DECLARED))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"test -e {DEVICE_UNDECLARED} && echo THERE || echo ABSENT")
    assert "ABSENT" in out, (
        f"a lane reached {DEVICE_UNDECLARED}, which no resource declares: rc={rc}\n{out}")

    # ...and `/dev` itself is still the sandbox's own, not the host's.
    rc2, out2 = run_in_lane(env, lane, "ls /dev | wc -l")
    assert rc2 == 0 and int(out2.strip()) < 40, (
        f"the lane's /dev has {out2.strip()} entries — that is the host's, not a devtmpfs with one "
        f"bind in it")
    env.unchanged()


# ── SRV-FIX-069 ──────────────────────────────────────────────────────────────────────────────
#
# A board is declared the stable way — `/dev/serial/by-id/usb-…-port0`, a symlink whose target
# `ttyUSBn` shuffles between boots — and the lane got `/dev/ttyUSBn` while the path the manifest
# actually wrote did not exist inside the lane at all. The lock is honoured and the DECLARED path
# is not there, which is `SRV-FIX-063`'s defect one name down.
#
# The answer chosen, of the two §1 left open: bind at the path the manifest named. Refusing the
# symlink at load would send a person back to `ttyUSBn`, which is the unstable name `by-id` exists
# to replace — the tool would be refusing the correct way to write the manifest.

# The machine's own `by-id` entry for DECLARED. A symlink the test makes cannot stand in: it would
# have to live under `tmp_path`, and `--tmpfs /tmp` comes after the device binds — deliberately, a
# lane's `/tmp` is its own — so anything there is erased inside the sandbox. Same reason the cases
# above use real device nodes.
def _by_id_for(node):
    root = Path("/dev/serial/by-id")
    if not root.is_dir():
        return None
    want = os.path.realpath(node)
    for link in root.iterdir():
        if os.path.realpath(link) == want:
            return link
    return None


BY_ID = _by_id_for(DEVICE_DECLARED)

needs_by_id = pytest.mark.skipif(
    BY_ID is None,
    reason=f"no /dev/serial/by-id entry resolves to {DEVICE_DECLARED} on this machine — THIS IS NOT A "
           f"PASS. The declared-name bind is unproven here; see SRV-FIX-069's report.")


@needs_board
@needs_by_id
def test_a_device_declared_by_a_symlink_is_reachable_under_the_declared_name(laneenv):
    """THE criterion: the lane has the node at the path the manifest WROTE.

    Not at the path it resolves to — that is what shipped, and it is a lane holding a lock on a
    device whose declared name meets `ENOENT`. Every build script and prompt that uses the
    manifest's own string is written against the declared name.
    """
    link = BY_ID
    env = laneenv.build(resources=_with_device(link))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"test -c {link} && echo CHARDEV || echo ABSENT")
    assert rc == 0 and "CHARDEV" in out, (
        f"the manifest declared {link} and the lane does not have it: rc={rc}\n{out}\n"
        f"The tool reports the resource held while the declared path is not there.")

    rc2, out2 = run_in_lane(
        env, lane, f"if exec 3< {link} ; then echo OPENED ; exec 3<&- ; else echo REFUSED ; fi")
    assert rc2 == 0 and "OPENED" in out2, (
        f"the node is at the declared path and cannot be opened: rc={rc2}\n{out2}")

    rc3, out3 = run_in_lane(env, lane, f"stat -c '%t:%T' {link}")
    want = "%x:%x" % _major_minor(DEVICE_DECLARED)
    assert rc3 == 0 and want in out3.strip(), (
        f"the node at the declared path is not the device it resolves to: wanted {want}, "
        f"got {out3!r}")
    env.unchanged()


@needs_board
@needs_by_id
def test_a_symlink_declaration_does_not_widen_the_lane(laneenv):
    """NEGATIVE (mandatory). Binding the by-id DIRECTORY would be the cheap fix.

    QA of this ticket measured that the shipped version of this case could not fail on it. It
    asserted only that `/dev/ttyUSB0` is absent — and under `--bind /dev/serial/by-id
    /dev/serial/by-id` every entry in that directory is a symlink to a `ttyUSBn` the fresh
    devtmpfs does not have, so the assertion still passes while every other board on the machine
    is LISTED inside the lane. Coverage that stops exactly where the risk starts.

    §1 says what the case has to assert instead, and it is a set equality rather than an absence:
    `/dev/serial` inside the lane holds exactly the one declared entry and nothing else. That is
    `SRV-FEAT-002`'s guarantee restated — a lane sees what its manifest named, and no more.
    """
    link = BY_ID
    env = laneenv.build(resources=_with_device(link))
    lane = env.claim(workspace="core")

    # The SET first, because it is the stronger statement and the one §1 asks for: an absence can
    # be true while the lane still lists every other board on the machine. `ls -a` so a directory
    # bound whole cannot hide behind an empty listing, `sort` so the answer is a set, not an order.
    rc2, out2 = run_in_lane(env, lane, "ls -a /dev/serial 2>&1 | sort | tr '\\n' ' '")
    assert rc2 == 0, f"/dev/serial is not readable in the lane: rc={rc2}\n{out2}"
    assert set(out2.split()) == {".", "..", "by-id"}, (
        f"/dev/serial in the lane holds more than the one declared entry's directory: {out2!r}")

    rc3, out3 = run_in_lane(env, lane, "ls -a /dev/serial/by-id 2>&1 | sort | tr '\\n' ' '")
    assert rc3 == 0, f"/dev/serial/by-id is not readable in the lane: rc={rc3}\n{out3}"
    assert set(out3.split()) == {".", "..", Path(link).name}, (
        f"the lane can SEE devices no manifest declared. Only {Path(link).name} was named; "
        f"/dev/serial/by-id holds {out3!r}. This is the by-id directory bound whole — the cheap "
        f"fix this case exists to refuse.")

    # ...and the canonical node itself, which is the other way the same widening shows up.
    rc, out = run_in_lane(env, lane, f"test -e {DEVICE_DECLARED} && echo THERE || echo ABSENT")
    assert "ABSENT" in out, (
        f"{DEVICE_DECLARED} is in the lane and no manifest named it — only "
        f"{link} was declared: rc={rc}\n{out}")
    env.unchanged()


# ── SRV-FIX-077 ══════════════════════════════════════════════════════════════════════════════
#
# `SRV-FIX-063` carried the device in and stopped there. Everything ELSE a workspace needs from the
# host — its toolchain, the bus behind its adapter, and the ability to see who is already using the
# board — did not travel, so a `master` lane won the exclusive board lock and could not compile.
#
# The fixture toolchain lives in the developer's REAL home and is declared with `~/`. That is not
# convenience: inside a lane `~` is `/home/lane`, so binding at the host's absolute path would pass
# an argv assertion while `firmware/master/build.sh`'s `. ~/environment/…/export.sh` still met
# ENOENT — `SRV-FIX-069`'s defect, one kind of path over. It is removed on teardown.

TOOLCHAIN_NAME = f".factory-toolchain-test-{os.getpid()}"

TOOLCHAIN_HELLO = """#!/bin/sh
echo TOOLCHAIN-RAN-INSIDE-THE-LANE
"""


@pytest.fixture
def home_toolchain():
    """A directory in the real home holding a script and a file, gone by the end of the test."""
    root = Path(os.path.realpath(Path.home())) / TOOLCHAIN_NAME
    if root.exists():
        shutil.rmtree(root)
    (root / "bin").mkdir(parents=True)
    script = root / "bin" / "hello"
    script.write_text(TOOLCHAIN_HELLO)
    script.chmod(script.stat().st_mode | 0o111)
    (root / "VERSION").write_text("v5.5.1\n")

    class Fixture:
        host = root
        declared = f"~/{TOOLCHAIN_NAME}"
        inside = f"/home/lane/{TOOLCHAIN_NAME}"

        def workspaces(self, declaring=True):
            core = {"path": "."}
            if declaring:
                core["toolchain"] = [self.declared]
            return {"core": core, "edge": {"path": "."}}

    try:
        yield Fixture()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_lane_sees_no_toolchain_it_did_not_declare(laneenv, home_toolchain):
    """NEGATIVE (mandatory). The cheap fix is to bind the developer's home wholesale.

    It would pass every other case in this ticket — ESP-IDF, the python env and the espressif tools
    are all under that home — and it would hand every lane the ssh keys, the git credentials and the
    rest of somebody's machine. So the case that decides the design is the lane that declares
    NOTHING: its `$HOME` is still an empty tmpfs, and the toolchain another workspace declared is
    ABSENT rather than merely unreadable.

    It fails against a home bind, against a `~`-expansion that ignores the declaration, and against
    binding every workspace's toolchain into every lane.
    """
    env = laneenv.build(workspaces=home_toolchain.workspaces(declaring=False))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, "ls -A $HOME | wc -l")
    assert rc == 0 and out.strip() == "0", (
        f"a lane whose workspace declares no toolchain has something in its HOME: {out!r}")

    for probe in (f"$HOME/{TOOLCHAIN_NAME}", str(home_toolchain.host)):
        rc2, out2 = run_in_lane(env, lane, f"test -e {probe} && echo THERE || echo ABSENT")
        assert "ABSENT" in out2, (
            f"a lane that declares no toolchain reached {probe}: rc={rc2}\n{out2}")

    # ...and a workspace that declares one does not hand it to the OTHER workspace's lane either.
    env2 = laneenv.build(workspaces=home_toolchain.workspaces())
    other = env2.claim(workspace="edge")
    rc3, out3 = run_in_lane(env2, other, f"test -e $HOME/{TOOLCHAIN_NAME} && echo THERE || echo ABSENT")
    assert "ABSENT" in out3, (
        f"`edge` declares no toolchain and reached the one `core` declared: rc={rc3}\n{out3}\n"
        f"A toolchain belongs to the workspace that declared it, exactly as a device belongs to the "
        f"lane holding the resource.")
    env2.unchanged()
    env.unchanged()


# ── who holds the board ──────────────────────────────────────────────────────────────────────
#
# `_TEMPLATE.md` §2 and `TICKET-WORKFLOW.md` both make two commands mandatory before any flash:
#
#     lsof /dev/ttyUSB1
#     pgrep -af "idf.py monitor|agent-build.sh"
#
# Inside a lane `--unshare-pid` hides every process that is not the lane's own, so BOTH return
# clean no matter who is using the board. Measured: the owner ran `./build.sh flash monitor` on the
# host while `lane/02` ran both and saw nothing. The protocol is not weakened inside a lane, it is
# INOPERATIVE, and it fails open — it says "free" when it means "I cannot see".

# One selector, one rule: `OPENABLE` is already "safe to open and held by nobody", which is
# precisely what this case needs to hold open itself.
UNHELD = OPENABLE

needs_unheld = pytest.mark.skipif(
    UNHELD is None,
    reason=f"no safe, currently-unheld character device out of {OPENABLE_CANDIDATES} on this "
           "machine — THIS IS NOT A PASS. The board-claim check is unproven here; see "
           "SRV-FIX-077's report.")


@needs_unheld
def test_a_lane_can_see_who_holds_the_device_it_is_about_to_use(laneenv):
    """With a host process holding the declared node, the lane is not told the board is free.

    Today it is: `lsof` and `pgrep` both answer clean from inside the namespace, unconditionally,
    which is worse than wrong — a wrong answer varies, and this one is the same answer whatever the
    board is doing. The tool is not in the namespace and can see, so the tool answers: it refuses
    the claim and names the holder, and the board is never locked by a lane that would flash it
    under somebody else's monitor.
    """
    resources = {"board": {"exclusive": True, "needed_by": ["core"], "devices": [UNHELD],
                           "description": "the bench board"}}
    env = laneenv.build(resources=resources)

    fd = os.open(UNHELD, os.O_RDONLY | os.O_NONBLOCK)
    try:
        r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        assert r.status_code == 409, (
            f"a lane was given `board` while THIS process held {UNHELD} (pid {os.getpid()}): "
            f"{r.status_code} {r.text}\n"
            f"Inside the lane `lsof` and `pgrep` answer clean under --unshare-pid, so the mandatory "
            f"board-claim check cannot answer this question — the tool has to.")
        err = r.json()["error"]
        assert err["code"] == "device_busy", err
        assert err["detail"]["device"] == UNHELD, err
        assert err["detail"]["pid"] == os.getpid(), (
            f"the refusal does not name the process that actually holds the device: {err}")
        assert err["detail"]["resource"] == "board", err

        # ...and the board is still free, which is the half that matters: a refused start may not
        # leave the exclusive resource locked behind it.
        lanes = env.srv.get("/api/lanes").json()
        assert lanes["lanes"] == [], f"a lane record survived the refusal: {lanes}"
        held = [x for x in lanes["resources"] if x["name"] == "board"][0]
        assert held["held_by"] is None, f"the refused claim locked the board anyway: {held}"
        assert not list((Path(env.root) / ".factory" / "worktrees").glob("lane/*")), \
            "a worktree was created for a refused claim"
    finally:
        os.close(fd)

    # NEGATIVE half, in the same case: with nothing holding it, the same claim succeeds. Without
    # this the fix could be "always refuse", which passes the assertion above and stops the product.
    ok = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert ok.status_code == 201, (
        f"the device is free and the claim was still refused: {ok.status_code} {ok.text}")
    assert ok.json()["resources_held"] == ["board"], ok.text
    env.unchanged()


# ── SRV-FEAT-002 round 3 ═════════════════════════════════════════════════════════════════════
#
# QA's FAIL of 2026-09-07, finding 2, in its own words: *"a lane of `factory-server` cannot run
# its own gate or its own agent, and criterion 7's behaviour is red on the real repository."*
#
# `test_confinement_does_not_break_normal_work` has been green since 2026-07-27 and the behaviour
# under it was false the whole time. Its gate is `sh ./gate.sh` — `mktemp`, `cc`, `git`, `python3`,
# every one of which arrives in the lane with `--ro-bind /usr /usr`. The gate
# `.factory/factory.json` actually declares for `factory-server` is `cd server && pytest tests/ -v`
# and the agent it declares is `claude`; on an ordinary workstation both of those resolve only under
# the developer's home, which a lane deliberately does not get. So inside a lane neither name
# resolves: the workspace cannot run its gate, and a factory pointed at this repository from inside
# one of its own lanes refuses the manifest with `agent_binary_missing` before any lane exists.
# That is `docs/operations/ESCAPES.md`'s *"a test that injects convenient inputs instead of the module's real
# defaults"*, and it is the case §1 says will decide the design: a jail that blocks the job.
#
# The declaration door already exists — a workspace's `toolchain` — and it is bound. What is
# missing is that a bound toolchain is not a REACHABLE one: `$HOME` inside a lane is the
# confinement's own synthesised home, `~/x` is bound under it, and `PATH` still names the host's
# home directories, none of which exist in the namespace. Nothing on `PATH` therefore resolves.
#
# The two constants are read out of the source rather than retyped, because a test that writes
# down the path it expects is a test of what the test believes.

CONFINE_SOURCE = (Path(__file__).resolve().parents[1] / "app" / "lane" / "confine.cpp").read_text()


def confine_constant(name):
    found = re.search(rf'{name}\s*=\s*"([^"]+)"', CONFINE_SOURCE)
    assert found, (
        f"{name} is no longer a string constant in app/lane/confine.cpp — these cases read the "
        f"path the confinement synthesises out of the source so they cannot drift from it")
    return found.group(1)


HOME_INSIDE = confine_constant("HOME_INSIDE")
SCRATCH_INSIDE = confine_constant("SCRATCH_INSIDE")

TOOLCHAIN_DECLARED = f".factory-declared-{os.getpid()}"
TOOLCHAIN_UNDECLARED = f".factory-undeclared-{os.getpid()}"


def test_device_and_toolchain_fixture_domains_cannot_overwrite_one_another():
    assert DEVICE_DECLARED == "/dev/ttyUSB0"
    assert DEVICE_UNDECLARED == "/dev/ttyUSB3"
    assert TOOLCHAIN_DECLARED != DEVICE_DECLARED
    assert TOOLCHAIN_UNDECLARED != DEVICE_UNDECLARED


PROGRAM = """#!/bin/sh
echo {tag}-RAN-INSIDE-THE-LANE
"""


@pytest.fixture
def declared_programs():
    """Programs that exist ONLY under the developer's home, on the server's PATH.

    This is the shape every real toolchain on a workstation has, and the shape the fixture's own
    `sh ./gate.sh` does not: `claude` under `~/.local/share/claude/versions`, `pytest` under
    `~/.local/bin`, node under `~/.nvm`. One directory is declared by the workspace and one is
    not, so the same fixture carries the criterion and its mandatory negative.

    Both are put on the SERVER's `PATH`, because that is how `agent.command` resolves at manifest
    load and how a developer's shell finds the gate — and it is the state in which a lane still
    cannot reach either one.

    The home is the server's own `HOME`, declared by the fixture rather than borrowed from the
    session, and it lives under `TMPDIR` rather than under pytest's `--basetemp`. Both of those
    are the opposite of convenience. Inside a lane `$HOME` already IS `HOME_INSIDE`, so a fixture
    using `Path.home()` makes the declared path and the in-lane path the same string by accident
    and every case below passes without the confinement doing anything; and a `--basetemp` inside
    a lane's own worktree is bound into the lane, so a home under it is reachable by its HOST
    path and `command -v` answers from the developer's directory instead of the lane's. A
    workstation has neither coincidence, and neither does this fixture.
    """
    home = Path(tempfile.mkdtemp(prefix="factory-devhome-"))
    declared, undeclared = home / TOOLCHAIN_DECLARED, home / TOOLCHAIN_UNDECLARED
    for root in (declared, undeclared):
        (root / "bin").mkdir(parents=True)

    def program(root, name, tag):
        path = root / "bin" / name
        path.write_text(PROGRAM.format(tag=tag))
        path.chmod(path.stat().st_mode | 0o111)
        return path

    program(declared, "factory-oven", "GATE")
    program(declared, "factory-agentling", "AGENT")
    program(undeclared, "factory-cellar", "UNDECLARED")

    class Fixture:
        gate = "factory-oven"
        agent = "factory-agentling"
        agent_host = str(declared / "bin" / agent)
        hidden = "factory-cellar"
        declared_dir = f"~/{TOOLCHAIN_DECLARED}/bin"
        hidden_host = str(undeclared / "bin" / "factory-cellar")
        host_home = str(home)

        def env(self, with_declared=True):
            dirs = [str(declared / "bin")] if with_declared else []
            dirs += [str(undeclared / "bin"), os.environ["PATH"]]
            return {"HOME": str(home), "PATH": os.pathsep.join(dirs)}

        def workspaces(self):
            return {"core": {"path": ".", "gate": self.gate,
                             "toolchain": [self.declared_dir]}}

    try:
        yield Fixture()
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_lane_can_run_the_gate_its_workspace_declares(laneenv, declared_programs):
    """§1's mandatory negative, on THIS repository's shape of gate rather than the fixture's.

    *"running the workspace's gate command — completes without a refusal. A jail that also blocks
    the job is not a solution, and this is the case that will actually decide the design."*

    The workspace declares its gate and declares the directory the gate's program lives in. Both
    declarations are honoured today — the directory IS bound, under the lane's own `$HOME` — and
    the gate still cannot run, because nothing on the lane's `PATH` exists inside the namespace.
    This is the case that is red for `pytest` on the real manifest.
    """
    env = laneenv.build(workspaces=declared_programs.workspaces(),
                        env=declared_programs.env())
    lane = env.claim(workspace="core")

    gate = json.loads(
        (Path(env.root) / ".factory" / "factory.json").read_text())["workspaces"]["core"]["gate"]
    assert gate == declared_programs.gate

    rc, out = run_in_lane(env, lane, gate)
    assert rc == 0 and "GATE-RAN-INSIDE-THE-LANE" in out, (
        f"the workspace declares the gate {gate!r} and declares "
        f"{declared_programs.declared_dir!r}, and a process inside its lane could not run it: "
        f"rc={rc}\n{out}\n"
        f"A lane that cannot run the gate its own manifest declares cannot gate anything, which "
        f"is §1's mandatory negative — the jail blocking the job.")
    env.unchanged()


def test_the_agent_the_manifest_declares_resolves_by_name_inside_a_lane(laneenv,
                                                                        declared_programs):
    """A factory pointed at this repository from inside one of its own lanes must be able to load
    its own manifest.

    `manifest::Store` resolves `agent.command` by walking `PATH` and refuses the WHOLE manifest
    with `agent_binary_missing` when the name does not resolve — by design, so lanes do not die
    one at a time. Inside a lane the agent's binary is present (its directory is read-bound) but
    reachable under no name on any `PATH` entry that exists, so the refusal fires and not one lane
    can be claimed. That is what QA measured: every case in this file returned
    `409 invalid_manifest` before it ran.
    """
    env = laneenv.build(workspaces=declared_programs.workspaces(),
                        agent={"command": declared_programs.agent},
                        env=declared_programs.env())
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"command -v {declared_programs.agent}")
    assert rc == 0 and declared_programs.host_home not in out, (
        f"`{declared_programs.agent}` is the agent this manifest declares and it resolves to no "
        f"path inside the lane: rc={rc}\n{out}\n"
        f"A factory pointed at this repository from inside one of its own lanes refuses the "
        f"manifest with `agent_binary_missing`, so no lane can be claimed at all.")

    rc, out = run_in_lane(env, lane, declared_programs.agent)
    assert rc == 0 and "AGENT-RAN-INSIDE-THE-LANE" in out, (
        f"the declared agent resolved but would not run inside the lane: rc={rc}\n{out}")
    env.unchanged()


def test_a_declared_toolchain_reaches_the_lane_when_the_server_never_had_it_on_PATH(
        laneenv, declared_programs):
    """The declaration is the reason the toolchain is on the lane's PATH — not the developer's shell.

    Found by hand-driving this repository's own manifest one level down, and it is the case that
    matters most, because it is the only one a nested factory ever meets. A lane's `PATH` is built
    by the confinement, so a factory running INSIDE a lane inherits a clean `PATH` with no home
    directory on it at all. Deriving the lane's `PATH` from the inherited one therefore works at
    depth one and drops the whole toolchain at depth two — the workspace declares it, the
    confinement binds it, and nothing can find it.

    Same fixture, one thing removed: the declared directory is NOT on the server's `PATH`.
    """
    env = laneenv.build(workspaces=declared_programs.workspaces(),
                        env=declared_programs.env(with_declared=False))
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"command -v {declared_programs.gate}")
    assert rc == 0 and declared_programs.host_home not in out, (
        f"the workspace declares {declared_programs.declared_dir!r} and the server\'s own PATH "
        f"never named it, so the lane cannot find it: rc={rc}\n{out}")

    rc, out = run_in_lane(env, lane, declared_programs.gate)
    assert rc == 0 and "GATE-RAN-INSIDE-THE-LANE" in out, (
        f"the declared toolchain is on the lane\'s PATH and would not run: rc={rc}\n{out}")
    env.unchanged()


def test_a_lane_cannot_reach_a_program_its_workspace_did_not_declare(laneenv,
                                                                     declared_programs):
    """NEGATIVE (mandatory). The cheap fix for the two cases above is to hand the lane the
    developer's `PATH` and home back.

    It would pass both of them — `pytest`, `claude` and node are all under that home — and it
    would undo the whole ticket. So the case that decides the design is the program that is on the
    server's `PATH`, in the same home, one directory over, and NOT declared by the workspace: it
    must resolve to nothing and be ABSENT rather than merely unreadable.
    """
    env = laneenv.build(workspaces=declared_programs.workspaces(),
                        env=declared_programs.env())
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"command -v {declared_programs.hidden} || echo NOWHERE")
    assert "NOWHERE" in out, (
        f"`{declared_programs.hidden}` is on the server's PATH and the workspace never declared "
        f"it, and it resolves inside the lane: rc={rc}\n{out}")

    for probe in (declared_programs.hidden_host, f"$HOME/{TOOLCHAIN_UNDECLARED}"):
        rc, out = run_in_lane(env, lane, f"test -e {probe} && echo THERE || echo ABSENT")
        assert "ABSENT" in out, (
            f"a lane reached {probe}, which its workspace never declared: rc={rc}\n{out}")

    rc, out = run_in_lane(env, lane, "ls -A $HOME")
    assert rc == 0 and out.split() == [TOOLCHAIN_DECLARED], (
        f"the lane's home holds something other than the one tree its workspace declared: {out!r}")
    env.unchanged()


def test_the_home_a_lane_gets_is_not_the_home_the_server_is_using(laneenv):
    """The lane's home is INVENTED, and a path the server can already see is not invented.

    `HOME_INSIDE` is a string constant, so when the server is itself inside a lane — which is
    where every ticket agent now runs — the home it invents for its lanes is the home it is using
    itself. The inner lane then gets a fresh, writable tmpfs mounted exactly over it, and a write
    to `$HOME` that must FAIL exits `0` instead. Nothing leaves the boundary, and that is not the
    standard: this report's own §1.5 says *a refusal that succeeds somewhere harmless is not a
    refusal*, and `test_write_outside_lane_is_refused` asserts `rc != 0` for precisely that reason.

    Trivially true on a developer workstation, where `/home/lane` does not exist. It is the
    statement of the rule, and it is red in the environment the defect lives in.
    """
    env = laneenv.build()
    lane = env.claim()
    server_home = os.path.realpath(os.path.expanduser("~"))

    rc, out = run_in_lane(env, lane, 'echo "$HOME"')
    assert rc == 0, out
    assert out.strip() != server_home, (
        f"the lane's home is the home the server itself is using ({server_home}) — the "
        f"confinement mounted a writable tmpfs over it, so every write the lane aims at the "
        f"server's home succeeds instead of failing")

    probe = Path(server_home) / f".factory-escape-probe-{os.getpid()}"
    rc, out = run_in_lane(env, lane, f"echo pwned > {probe}")
    assert rc != 0, f"writing the server's own home at {probe} succeeded: {out!r}"
    assert not probe.exists(), f"{probe} was created"
    env.unchanged()


def test_a_repository_under_the_path_the_confinement_synthesises_still_gets_a_lane(serve,
                                                                                  tmp_path):
    """A repository that happens to live under the confinement's own scratch path still runs.

    `SCRATCH_INSIDE` is a string constant too, and `p.writable` holds it in the same list as the
    host paths the policy binds. Both comparisons made against that list therefore compare two
    namespaces: the ancestor guard refuses the lane with *"writable path /tmp/factory is above the
    repository root"*, and `--tmpfs /tmp/factory` would mount straight over the worktree it just
    bound. On a workstation the two namespaces never collide; when the factory is pointed at
    itself from inside its own lane, `TMPDIR` is that constant and they always do — which is why
    this suite needs `--basetemp` before a single case will run.

    Driven from a real directory under that path rather than from an argv assertion, because the
    refusal is a `409` a person sees and the shadowing is a mount nobody sees.
    """
    root = Path(SCRATCH_INSIDE) / f"srv-feat-002-{os.getpid()}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    try:
        repo = build_repo(root / "repo")
        before = status_of(repo)
        srv = serve(repo, FACTORY_TEST_HOOKS="1")

        r = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        assert r.status_code == 201, (
            f"a repository under {SCRATCH_INSIDE} was refused a lane: {r.status_code} {r.text}\n"
            f"The confinement invented that path; the repository was there first.")
        lane = r.json()

        wt = Path(lane["worktree"])
        rc_file = wt / ".probe-rc"
        wrapped = "{ echo inside > notes.txt ; } > .probe-out 2>&1 ; echo $? > .probe-rc"
        p = srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": wrapped})
        assert p.status_code == 202, p.text
        deadline = time.time() + 60
        while time.time() < deadline and not (rc_file.exists() and rc_file.read_text().strip()):
            time.sleep(0.05)
        assert rc_file.exists() and rc_file.read_text().strip() == "0", (
            f"the lane's worktree was shadowed by the confinement's own scratch tmpfs: "
            f"{(wt / '.probe-out').read_text() if (wt / '.probe-out').exists() else '(no output)'}")
        assert (wt / "notes.txt").read_text() == "inside\n"

        # ...and the boundary still holds for a repository that lives there.
        target = repo / "PWNED.txt"
        srv.post("/api/_test/lane-process",
                 json={"lane": lane["lane"],
                       "command": f"{{ echo pwned > {target} ; }} > .probe-out 2>&1 ; "
                                  f"echo $? > .probe-rc2"})
        deadline = time.time() + 60
        rc2 = wt / ".probe-rc2"
        while time.time() < deadline and not (rc2.exists() and rc2.read_text().strip()):
            time.sleep(0.05)
        assert rc2.exists() and rc2.read_text().strip() != "0", \
            "the write into the main checkout succeeded"
        assert not target.exists(), f"{target} was created in the main checkout"
        assert status_of(repo) == before, f"the main checkout under {SCRATCH_INSIDE} changed"
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── SRV-FIX-532 cluster C2 — a lane's branch ref is its own ───────────────────────────────────
#
# `SRV-FIX-449`. The confinement bound `refs/heads/ticket` and `logs/refs/heads/ticket` read-WRITE:
# one directory, shared, holding EVERY lane's branch. Measured from `lane/11` with four other lanes
# running — 15 refs listed, `test -w …/refs/heads/ticket/SRV-FEAT-437` → WRITABLE, while that
# branch was held by `lane/01` at stage 8 of 10 and 15092 s in. One `git update-ref` from an
# unrelated lane discards it, silently, and the lane that did it is not the lane that will be
# blamed.
#
# **Why it is not the one-line narrowing, measured rather than argued.** `git update-ref` writes
# `<ref>.lock` in the ref's OWN directory and renames it over the ref. Binding the lane's ref
# read-write inside a read-only directory therefore stops the lane's own commit:
#
#     fatal: cannot lock ref 'HEAD': Unable to create '…/refs/heads/ticket/MINE.lock':
#     Read-only file system
#
# What works is the other way round: the directory stays writable, so the lane's own lock is fine,
# and every OTHER lane's ref is bound read-only over itself. A rename or an unlink over a bind
# mount is `EBUSY`, which is the kernel refusing rather than the tool asking nicely.

TICKET_REFS = ".git/refs/heads/ticket"


def ref_of(env, branch):
    return (Path(env.root) / ".git" / "refs" / "heads" / branch).read_text().strip()


def two_lanes(env, commit_in_first=False):
    """Two lanes on two tickets, each on its own branch, both live.

    `commit_in_first` FORCES THE TWO APART. Both branches are cut from `main`, so without it a
    lane's `HEAD` and the other lane's ref hold the same commit and `git update-ref` is a no-op
    that succeeds without touching the file — the attack would be refused for a reason that has
    nothing to do with the boundary, and the case would pass whatever the policy said.
    """
    first = env.claim("TCK-001")
    second = env.claim("TCK-002")
    assert first["branch"] != second["branch"], (first, second)
    if commit_in_first:
        rc, out = run_in_lane(
            env, first,
            "echo apart > apart.txt && git add apart.txt && "
            "git -c user.email=l@e.i -c user.name=L commit -qm apart")
        assert rc == 0, f"the fixture could not move lane one's HEAD: {out!r}"
        assert ref_of(env, first["branch"]) != ref_of(env, second["branch"]), (
            "the two lanes still hold the same commit, so an update-ref between them is a no-op")
    return first, second


def test_each_lane_still_moves_its_own_branch(laneenv):
    """§1 C2 — the fix does not cost a lane the write it actually needs.

    `SRV-FIX-449` criterion 1. Two lanes, both commit, both branches advance. This is the case the
    obvious narrowing fails: binding a lane's own ref read-write inside a read-only directory
    refuses the `<ref>.lock` git writes beside it, and the lane cannot commit at all.
    """
    env = laneenv.build()
    first, second = two_lanes(env)

    before = {lane["branch"]: ref_of(env, lane["branch"]) for lane in (first, second)}
    for lane, text in ((first, "one"), (second, "two")):
        rc, out = run_in_lane(
            env, lane,
            f"echo {text} > work.txt && git add work.txt && "
            f"git -c user.email=l@e.i -c user.name=L commit -qm '{text}'")
        assert rc == 0, f"{lane['lane']} could not commit on its own branch: {out!r}"

    for lane in (first, second):
        assert ref_of(env, lane["branch"]) != before[lane["branch"]], (
            f"{lane['lane']} committed and its branch {lane['branch']} did not move")


def test_a_lane_cannot_move_another_lanes_branch(laneenv):
    """§1 C2 — one lane cannot move another's ref, and the victim's branch is unchanged.

    `SRV-FIX-449` criterion 2. The victim's ref is read first and last, which is §1's safety
    clause: what matters is not that the write was refused but that the other lane's work is where
    it was.
    """
    env = laneenv.build()
    first, second = two_lanes(env, commit_in_first=True)
    was = ref_of(env, second["branch"])

    rc, out = run_in_lane(
        env, first, f"git update-ref refs/heads/{second['branch']} HEAD")
    assert rc != 0, (
        f"lane one moved lane two's branch and the tool did not refuse: rc={rc} {out!r}")
    assert ref_of(env, second["branch"]) == was, (
        f"{second['branch']} moved from {was} to {ref_of(env, second['branch'])} — the other "
        f"lane's work is gone and the lane that did it is not the one that will be blamed")


def test_a_lane_cannot_delete_or_forge_another_lanes_ref(laneenv):
    """§1 C2 — the other two shapes of the attack, and the ref directory afterwards.

    `SRV-FIX-449` criterion 3. Deleting through git, deleting the file directly, and creating a
    ref under another lane's name. Each fails, and the directory afterwards holds exactly the
    refs it held before — asserted as a SET, so a shape nobody thought of is caught by the same
    assertion.
    """
    env = laneenv.build()
    first, second = two_lanes(env, commit_in_first=True)
    refs = Path(env.root) / TICKET_REFS
    before = sorted(p.name for p in refs.iterdir())
    assert len(before) >= 2, f"the fixture holds {before}, so there is nothing to protect"
    was = ref_of(env, second["branch"])

    attacks = {
        "git update-ref -d": f"git update-ref -d refs/heads/{second['branch']}",
        # `.git` in a linked worktree is a FILE, so the ref lives under the COMMON dir — asked
        # of git rather than typed, or the attack misses and the case passes having done nothing.
        "rm on the ref file":
            f"rm \"$(git rev-parse --git-common-dir)/refs/heads/{second['branch']}\"",
        "git branch -f": f"git branch -f {second['branch']} HEAD",
        "writing the ref file directly":
            f"git rev-parse HEAD > \"$(git rev-parse --git-common-dir)/refs/heads/"
            f"{second['branch']}\"",
    }
    refused = {}
    for what, command in sorted(attacks.items()):
        rc, out = run_in_lane(env, first, command)
        refused[what] = (rc, out)

    for what, (rc, out) in sorted(refused.items()):
        assert rc != 0, f"`{what}` from another lane succeeded: {out!r}"

    assert ref_of(env, second["branch"]) == was, (
        f"{second['branch']} was destroyed: {was} -> {ref_of(env, second['branch'])}\n"
        f"{refused}")
    after = sorted(p.name for p in refs.iterdir())
    assert after == before, (
        f"the ref directory changed under {len(attacks)} attacks from another lane:\n"
        f"  before {before}\n  after  {after}")


def test_narrowing_the_ref_bind_does_not_break_ordinary_git(laneenv):
    """§1 C-NEG — a boundary that also blocks the job is not a solution.

    `SRV-FIX-449` criterion 5, mandatory negative, and the case that DECIDES the design: the
    obvious fix — binding only the lane's own ref file — must not break git's ref locking, which
    writes `<ref>.lock` in the same directory.

    Every verb the workflow actually uses in a lane is driven, and the sweep is asserted non-empty
    before anything is concluded from it.
    """
    env = laneenv.build()
    lane = env.claim("TCK-001")

    ordinary = {
        "commit": ("echo a > w.txt && git add w.txt && "
                   "git -c user.email=l@e.i -c user.name=L commit -qm a"),
        "branch --show-current": "git branch --show-current",
        "log": "git log --oneline -1",
        "diff": "git diff --stat HEAD~1 2>/dev/null || git diff --stat",
        "status": "git status --porcelain",
        "merge a branch from the shared object store": (
            "git -c user.email=l@e.i -c user.name=L merge --no-edit main"),
        "rebase": "git -c user.email=l@e.i -c user.name=L rebase main",
    }
    assert len(ordinary) >= 6, f"the sweep is thin: {sorted(ordinary)}"

    for what, command in ordinary.items():
        rc, out = run_in_lane(env, lane, command)
        assert rc == 0, (
            f"a lane doing ordinary git work was refused `{what}`: rc={rc} {out!r}\n"
            f"A boundary that also blocks the job is not a solution.")
