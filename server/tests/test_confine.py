"""SRV-FEAT-039 — an agent can actually run in a lane.

Thirteen screens existed before this ticket and not one agent had ever run behind them. The
confinement mounted a tmpfs over `HOME` and read-bound `/usr`, `/etc` and the git directories, so
a confined agent had no binary to execute and no credentials to authenticate with — measured, and
quoted in this ticket's report.

Two read-only binds close that, and the whole argument for widening the boundary is that the third
path is still refused. So `test_a_lane_cannot_read_the_rest_of_your_home` is written against a
REAL read from inside a real lane, never against the argv: an argv assertion would stay green
against a bind that quietly widened, which is the only way this can go wrong.
"""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_isolation import (TOOLCHAIN_DECLARED, TOOLCHAIN_NAME,        # noqa: F401
                            TOOLCHAIN_UNDECLARED,
                            declared_programs, home_toolchain, laneenv,
                            run_in_lane, spawn_in_lane)
from test_lane import build_repo, git, status_of                   # noqa: F401

# The real agent, and the real credentials. Not a stand-in: this ticket's central case is whether
# the thing the tool exists to run can authenticate and speak from inside the boundary, and a
# stand-in cannot fail to authenticate.
REAL_AGENT = shutil.which("claude")
REAL_BINARY = os.path.realpath(REAL_AGENT) if REAL_AGENT else None
REAL_BINARY_DIR = os.path.dirname(REAL_BINARY) if REAL_BINARY else None
def _real_credentials():
    """Where the credentials the tool would hand an agent actually are — `SRV-FIX-466`.

    `$HOME/.claude` is the developer's machine, and inside a lane `$HOME` IS the sandbox: the
    lane's home is a tmpfs the confinement makes, so the probe looked in the one place the design
    guarantees is empty and six cases refused with `SKIPPED, AND THEREFORE RED` while a real,
    credentialed agent was running three directories away.

    The product's answer is read instead. `agent.credentials_dir` is what the manifest declares and
    `CLAUDE_CONFIG_DIR` is where the tool carried it for THIS session; a session that has one is a
    session whose agent configuration has been redirected, and that directory is the credential.
    Where neither exists the answer is still nothing, and `requires_real_agent` still refuses
    loudly — a machine that genuinely has no credentials is not a lane, and the two are told apart
    by where the session is, never by whether the check is inconvenient.
    """
    carried = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if carried and Path(carried).is_dir():
        return Path(carried)
    return Path.home() / ".claude"


def _operator_home():
    """The home of the account running the tool, which is NOT `$HOME` inside a lane.

    `SRV-FIX-466`: the lane's `$HOME` is `/home/lane`, a tmpfs the confinement makes, so a case
    reading `Path.home()` from inside a lane asserts about the sandbox it is asserting is out of
    reach — `'/home/lane' != '/home/lane'` was the whole ticket in one assertion. The passwd entry
    for the uid answers the same thing in both places, because `/etc` is carried in read-only and
    the uid is not remapped.
    """
    import pwd

    try:
        return Path(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))
    except (KeyError, OSError):
        return Path(os.path.realpath(Path.home()))


REAL_CREDENTIALS = _real_credentials()
REAL_HOME = _operator_home()
DECLARED_CREDENTIALS = REAL_HOME / ".claude"


def skipped(reason):
    """§1: 'skipped with skipped(...) — a RED check, never a silent pass.'

    `pytest.skip` is a silent pass in the summary, and `docs/operations/ESCAPES.md` already carries the row
    for a guarded section that reads as `n/n passed` without running. So a skip here costs a
    failure and names what was missing.
    """
    pytest.fail(f"SKIPPED, AND THEREFORE RED: {reason}")


def requires_real_agent():
    if os.environ.get("FACTORY_REAL_CLI") == "0":
        pytest.skip("FACTORY_REAL_CLI=0 opts out of authenticated live Claude (release tier)")
    if not REAL_AGENT:
        skipped("no `claude` on PATH — this environment cannot run a real agent")
    if not REAL_CREDENTIALS.is_dir():
        skipped(f"no credential directory at {REAL_CREDENTIALS}")


@pytest.fixture
def private_credentials(tmp_path, monkeypatch):
    home = tmp_path / "private-home"
    credential = home / ".claude" / ".credentials.json"
    credential.parent.mkdir(parents=True)
    credential.write_text("fixture credential; no live agent run\n")
    sentinel = home / ".ssh" / "id_fixture"
    sentinel.parent.mkdir()
    sentinel.write_text("private-home-sentinel\n")
    monkeypatch.setattr(sys.modules[__name__], "REAL_CREDENTIALS", credential.parent)
    return sentinel


# ── reading the argv the tool actually built ─────────────────────────────────────────────────
#
# `confine.cpp` already lets FACTORY_CONFINE_BIN name the helper, for exactly the reason a test
# needs one: the real way this fails is a helper that is not what you think. Pointing it at a
# recorder gives the argv the tool BUILT, lifted off disk rather than retyped — a test that writes
# down the argv it expects is a test of what the test believes.

RECORDER = """#!/bin/sh
printf '%s\\n' "$@" > {log}
exit 0
"""


@pytest.fixture
def argv_recorder(tmp_path):
    log = tmp_path / "confine-argv.txt"
    script = tmp_path / "record-bwrap"
    script.write_text(RECORDER.format(log=log))
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    class Recorder:
        bin = str(script)

        def argv(self, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if log.exists():
                    text = log.read_text()
                    if text:
                        return text.splitlines()
                time.sleep(0.05)
            raise AssertionError(f"the confinement helper was never invoked within {timeout}s")

    return Recorder()


def ro_binds(argv):
    """Every (src, dst) the argv read-binds, in order."""
    out = []
    for i, word in enumerate(argv):
        if word in ("--ro-bind", "--ro-bind-try"):
            out.append((argv[i + 1], argv[i + 2]))
    return out


def test_native_sandbox_socket_has_only_a_private_runtime_mount(laneenv, argv_recorder):
    """SRV-FIX-560: native sandbox socket setup works while the rest of /tmp stays read-only."""
    env = laneenv.build(
        agent={"driver": "codex", "command": "/bin/true", "models": ["gpt-6-sol"],
               "default_model": "gpt-6-sol"},
        env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")
    argv = argv_recorder.argv()
    daemon = f"/tmp/codex-daemon-{os.geteuid()}"
    remount = argv.index("--remount-ro", argv.index("--tmpfs") + 1)
    assert argv[remount + 1] == "/"
    assert ["--tmpfs", "/tmp"] in [argv[i:i + 2] for i in range(remount)]
    tmp_remount = argv.index("--remount-ro", remount + 1)
    assert argv[tmp_remount + 1] == "/tmp"
    assert ["--dir", daemon] in [argv[i:i + 2] for i in range(tmp_remount)]
    assert ["--tmpfs", daemon, "--chmod", "0700", daemon] in [
        argv[i:i + 5] for i in range(tmp_remount + 2, len(argv) - 4)]
    assert ["--bind", "/tmp", "/tmp"] not in [argv[i:i + 3] for i in range(len(argv) - 2)]
    assert ["--ro-bind", "/tmp", "/tmp"] not in [argv[i:i + 3] for i in range(len(argv) - 2)]
    env.unchanged()


def test_other_agent_does_not_receive_a_codex_runtime_mount(laneenv, argv_recorder):
    """A driver's private runtime path is declared by that driver, not by every lane."""
    env = laneenv.build(env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")
    argv = argv_recorder.argv()
    socket = f"/tmp/codex-daemon-{os.geteuid()}"
    assert ["--tmpfs", socket] not in [argv[i:i + 2] for i in range(len(argv) - 1)]
    env.unchanged()


def test_private_runtime_tmp_hides_unbound_host_bytes(laneenv, tmp_path):
    """The socket carveout does not expose host temp files or writable sibling paths."""
    host_sentinel = tmp_path / "host-temp-sentinel"
    host_sentinel.write_text("outside-lane marker\n")
    env = laneenv.build(agent={"driver": "codex", "command": "/bin/true",
                               "models": ["gpt-6-sol"], "default_model": "gpt-6-sol"})
    lane = env.claim()
    rc, out = run_in_lane(
        env, lane,
        f"test ! -e {shlex.quote(str(host_sentinel))} && "
        "if printf bad > /tmp/unbound-runtime-probe; then exit 42; fi; "
        "printf private-runtime-ok")
    assert rc == 0 and "private-runtime-ok" in out, out
    assert host_sentinel.read_text() == "outside-lane marker\n"
    env.unchanged()


def agent_manifest(credentials=True):
    block = {"command": "claude"}
    if credentials:
        block["credentials_dir"] = str(REAL_CREDENTIALS)
    return block


# ── 1 · THE case the whole ticket exists for ─────────────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("FACTORY_REAL_CLI") == "0",
                    reason="FACTORY_REAL_CLI=0 opts out of authenticated live Claude")
def test_an_agent_starts_and_answers(laneenv):
    """A lane started with the real `agent.command` produces a first transcript entry.

    Not that the process spawned — that it authenticated and spoke. Before this ticket the child
    died with `claude: not found`; a bind alone leaves it at `Not logged in`. Only the binds plus
    the credential pointer get a sentence back out of the boundary.
    """
    requires_real_agent()
    env = laneenv.build(agent=agent_manifest())
    lane = env.claim()

    r = env.srv.post(f"/api/lanes/{lane['lane']}/runs",
                     json={"prompt": "Reply with exactly the word PONG and nothing else."})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["pid"] > 0, run

    # The AGENT'S OWN entry, not the tool's. `human` is written for the prompt the moment the run
    # starts and is there whether or not the child ever executed — the first version of this case
    # asserted "the transcript is non-empty" and PASSED against a lane where the binary did not
    # exist. What proves it authenticated and spoke is a `prose` entry carrying the word it was
    # asked for.
    path = Path(env.root) / ".factory" / "runs" / run["run_id"] / "transcript.jsonl"
    deadline = time.time() + 180
    entries, spoke = [], []
    while time.time() < deadline:
        try:
            entries = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        except OSError:
            entries = []
        spoke = [e for e in entries if e.get("kind") == "prose"]
        if spoke:
            break
        time.sleep(0.2)

    blob = json.dumps(entries)
    for dead in ("not found", "Not logged in", "Please run /login"):
        assert dead not in blob, (
            f"the agent started and immediately failed with {dead!r} — it reached the lane "
            f"without what it needs to be itself:\n{blob[:2000]}")

    assert spoke, (
        f"the agent produced no `prose` entry within 180s — it never authenticated and spoke.\n"
        f"This is the assertion the whole ticket exists for.\n"
        f"kinds seen: {[e.get('kind') for e in entries]}\n"
        f"transcript: {path}\n{blob[:2000]}\n"
        f"server log tail:\n{env.srv.tail()}")

    assert "PONG" in blob.upper(), (
        f"the agent spoke, but not the word it was asked for — the run did not carry the prompt "
        f"through the boundary:\n{blob[:2000]}")

    # `--input-format stream-json` reads stdin to EOF, so the run does not end on its own.
    # `end` closes the channel; `kill` is the backstop, because the autouse leak check fails any
    # test that leaves a confinement helper running.
    env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={})
    env.unchanged()


# ── 2 · declared binary origins, and no third ────────────────────────────────────────────────

def test_the_binary_is_the_only_home_path_bound(laneenv, argv_recorder, private_credentials):
    """The argv read-binds only the declared binary's directory and any exact file fallback.

    Asserted against the argv on purpose, so a later convenience bind cannot be added without
    this going red. It is the companion of case 5, never a substitute for it.

    SRV-FIX-052 changed the number and it was two before. This ticket bound the credential
    DIRECTORY read-only so the agent could authenticate, and the directory it bound is the one
    the CLI resolves all of its state from — including a per-session scratch directory it must
    WRITE before it can start a shell, which read-only made impossible. The credential now
    travels as one file into the lane's own configuration directory, so the developer's
    `~/.claude` is not bound at all and `test_lane_home.py` asserts that from inside a lane.
    """
    requires_real_agent()
    env = laneenv.build(agent=agent_manifest(),
                        env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")

    argv = argv_recorder.argv()
    binds = ro_binds(argv)
    original_directory = (REAL_BINARY_DIR, REAL_BINARY_DIR)
    declared_file_alias = (REAL_BINARY, "/factory/bin/claude")
    declared_link_alias = ["--symlink", REAL_BINARY, "/factory/bin/claude"]
    is_link = declared_link_alias in [argv[i:i + 3] for i in range(len(argv) - 2)]
    is_file = declared_file_alias in binds
    assert original_directory in binds, (
        f"the original binary directory is not bound at its declared path: {binds}")
    assert is_link != is_file, (
        f"the declared binary needs exactly one projection at its lane alias: {argv}")

    under_home = [(src, dst) for src, dst in binds
                  if src == str(REAL_HOME) or src.startswith(str(REAL_HOME) + "/")]
    origins = [original_directory] + ([declared_file_alias] if is_file else [])
    expected_under_home = {pair for pair in origins
                           if pair[0] == str(REAL_HOME)
                           or pair[0].startswith(str(REAL_HOME) + "/")}

    assert set(under_home) == expected_under_home, (
        f"expected only the declared binary's directory and exact file fallback under {REAL_HOME}:\n"
        + "\n".join(f"  --ro-bind {s} {d}" for s, d in under_home)
        + f"\n\nfull argv:\n{' '.join(argv)}")

    # ...and nothing writable was added under the home to go with them.
    for i, word in enumerate(argv):
        if word == "--bind":
            src = argv[i + 1]
            assert not (src == str(REAL_HOME) or src.startswith(str(REAL_HOME) + "/")), \
                f"a WRITABLE bind under the developer's home: --bind {src} {argv[i + 2]}"

    env.unchanged()


# ── 3 · the lane's home is still its own ─────────────────────────────────────────────────────

def test_home_is_still_a_tmpfs(laneenv, private_credentials):
    """§1, pre-decided: `HOME` inside stays a tmpfs — empty, writable, and not the real one."""
    requires_real_agent()
    env = laneenv.build(agent=agent_manifest())
    lane = env.claim()

    rc, out = run_in_lane(env, lane, "echo $HOME")
    assert rc == 0, out
    inside_home = out.strip()
    assert inside_home != str(REAL_HOME), \
        f"the lane's HOME is the developer's own home: {inside_home}"

    rc, out = run_in_lane(env, lane, "ls -A $HOME | wc -l")
    assert rc == 0, out
    assert out.strip() == "0", f"the lane's HOME is not empty; it holds:\n{out}"

    marker = f"factory-039-{os.getpid()}"
    rc, out = run_in_lane(env, lane, f"echo written > $HOME/{marker} && cat $HOME/{marker}")
    assert rc == 0 and "written" in out, f"the lane's HOME is not writable: {out!r}"

    assert not (REAL_HOME / marker).exists(), \
        f"a write to the lane's HOME landed in the developer's real home: {REAL_HOME / marker}"
    assert not (Path(inside_home) / marker).exists() or inside_home != str(REAL_HOME)

    env.unchanged()


# ── 4 · a path that is not there is a manifest error, at load ────────────────────────────────

def test_a_missing_path_is_a_manifest_error(laneenv, tmp_path):
    """`422` at load naming the path, and NO lane is ever claimed.

    A tool that starts and then fails per-lane makes the developer debug six identical failures
    instead of reading one.
    """
    for block, pointer, what in (
            ({"command": "definitely-not-an-agent-on-this-machine"},
             "/agent/command", "the binary"),
            ({"command": "claude", "credentials_dir": str(tmp_path / "no-such-credentials")},
             "/agent/credentials_dir", "the credential directory")):
        env = laneenv.build(agent=block)

        r = env.srv.get("/api/manifest")
        assert r.status_code == 422, (
            f"{what} does not exist, and the manifest loaded anyway "
            f"({r.status_code}):\n{r.text}")
        body = r.json()["error"]
        assert body["code"] == "invalid_manifest", body
        assert body["detail"]["pointer"] == pointer, body
        assert body["detail"]["path"], f"the error does not name the path that is missing: {body}"

        # ...and no lane is ever claimed against it.
        r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        assert r.status_code >= 400, (
            f"a lane was claimed against a manifest that is invalid: {r.status_code} {r.text}")

        assert not list((Path(env.root) / ".factory" / "worktrees").glob("*")), \
            "a worktree was created against an invalid manifest"
        env.unchanged()


# ── 4b · the resolver, and ONLY the resolver ─────────────────────────────────────────────────

def test_the_only_other_path_carried_in_is_the_resolver(laneenv, argv_recorder,
                                                       private_credentials):
    """`/etc/resolv.conf` is a symlink into `/run` on this host, and `/run` is not mounted.

    So `--ro-bind /etc` left the link dangling and a lane had a network it could not resolve a
    name on — measured as the agent failing with `Unable to connect to API (ConnectionRefused)`.
    §1 pre-decided that a third path is a finding and a decision rather than an edit, so this
    case pins the decision: ONE regular file, read-only, and nothing else outside `/usr`, `/etc`,
    the repository and the agent's two. A later convenience bind fails here.
    """
    requires_real_agent()
    env = laneenv.build(agent=agent_manifest(),
                        env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")

    argv = argv_recorder.argv()
    allowed_roots = ("/usr", "/etc", str(Path(env.root)), os.path.realpath(env.root),
                     REAL_BINARY_DIR, os.path.realpath(REAL_CREDENTIALS))
    extra = [(src, dst) for src, dst in ro_binds(argv)
             if not any(src == r or src.startswith(r + "/") for r in allowed_roots)]

    assert len(extra) == 1, (
        f"expected exactly one read-only bind outside /usr, /etc, the repository and the "
        f"agent's two; got {len(extra)}:\n"
        + "\n".join(f"  --ro-bind {s} {d}" for s, d in extra))

    src, dst = extra[0]
    assert src == dst, f"the resolver is bound at a different path inside the lane: {src} -> {dst}"
    assert src == os.path.realpath("/etc/resolv.conf"), (
        f"the one extra bind is not the resolver `/etc/resolv.conf` points at: {src}")
    assert Path(src).is_file(), f"a DIRECTORY was carried in, not the single resolver file: {src}"

    env.unchanged()

    # ...and it is what makes a name resolvable, which is the only reason it is there. A second
    # lane, under the REAL helper: the recorder above answers the argv question and runs nothing,
    # so it cannot answer this one.
    env.build(agent=agent_manifest())
    live = env.claim()
    rc, out = run_in_lane(env, live, "getent hosts api.anthropic.com")
    assert rc == 0 and out.strip(), f"a name still does not resolve inside the lane: {out!r}"

    env.unchanged()


# ── 5 · MANDATORY NEGATIVE — the third path is still refused ─────────────────────────────────

def test_a_lane_cannot_read_the_rest_of_your_home(laneenv, private_credentials):
    """From inside a lane, the rest of the developer's home is ABSENT.

    Written against a real read, never against the argv, because the whole argument for binding
    two paths is that the third is still refused — and an argv assertion stays green against a
    bind that quietly widened to `$HOME`. This is the case the developer's decision rests on.

    F9 uses a second controlled home with an existing secret so the refusal can be distinguished
    from an absent host file in a headless outer lane.
    """
    requires_real_agent()
    env = laneenv.build(agent=agent_manifest())
    lane = env.claim()

    # Real files in the real home, chosen because they are the ones that would actually hurt.
    targets = [private_credentials, REAL_HOME / ".ssh", REAL_HOME / ".bashrc", REAL_HOME / ".gitconfig",
               REAL_HOME / ".claude.json", REAL_HOME / "PLV"]
    present = [t for t in targets if t.exists()]
    assert present, f"the fixture proves nothing: none of {targets} exists on this machine"

    for target in present:
        rc, out = run_in_lane(env, lane, f"cat {target} 2>&1 || ls -a {target} 2>&1")
        assert rc != 0 or "No such file" in out or "Permission denied" in out, (
            f"THE LANE READ {target} OUT OF THE DEVELOPER'S HOME.\nrc={rc}\noutput: {out[:800]!r}")

    # The home itself is not enumerable — the two bound branches are all that is in it.
    rc, out = run_in_lane(env, lane, f"ls -A {REAL_HOME}")
    listed = set(out.split()) if rc == 0 else set()
    forbidden = listed - {".local", ".claude"}
    assert not forbidden, (
        f"the developer's home is enumerable from inside the lane; it shows {sorted(forbidden)}\n"
        f"full listing: {out!r}")

    # ...and the OPERATOR'S OWN credential directory is not writable from inside the lane.
    #
    # `SRV-FIX-466`: the directory the agent writes to inside a lane is the tool's per-lane copy
    # at `CLAUDE_CONFIG_DIR`, which is bound WRITABLE on purpose — `SRV-FIX-052` is the day every
    # lane could read, reason and not run one command because that directory was read-only and the
    # program has to create a session scratch in it before it can start a shell. So the thing this
    # probe is about is the operator's own `~/.claude`, which is a different path, and the case
    # asserts they ARE different before it asserts anything about either.
    assert REAL_CREDENTIALS != DECLARED_CREDENTIALS or not os.environ.get("CLAUDE_CONFIG_DIR"), (
        f"the session's credentials and the operator's own are the same directory "
        f"({REAL_CREDENTIALS}), so a write refused there says nothing about the boundary")
    rc, out = run_in_lane(env, lane, f"echo pwned > {DECLARED_CREDENTIALS}/factory-039-probe")
    assert rc != 0, (
        f"the operator's own credential directory {DECLARED_CREDENTIALS} is WRITABLE from inside "
        f"the lane: {out!r}")
    assert not (DECLARED_CREDENTIALS / "factory-039-probe").exists()
    env.unchanged()

    private_home_boundary(laneenv, private_credentials.parents[1], expect_event=True)


def private_home_boundary(laneenv, home, *, expect_event=False):
    credential = home / ".claude" / ".credentials.json"
    sentinel = home / ".ssh" / "id_fixture"
    assert credential.is_file(), f"incomplete private-home fixture: missing credential {credential}"
    assert sentinel.is_file(), f"incomplete private-home fixture: missing sentinel {sentinel}"
    assert sentinel.read_text() == "private-home-sentinel\n"

    program = home / ".local" / "bin" / "fixture-agent"
    program.parent.mkdir(parents=True, exist_ok=True)
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    env = laneenv.build(agent={"command": str(program)},
                        workspaces={"core": {"path": ".", "toolchain": [str(program)]}},
                        env={"HOME": str(home)})
    lane = env.claim()
    rc, out = run_in_lane(env, lane, f"cat {shlex.quote(str(sentinel))}")
    assert rc != 0 and "No such file or directory" in out and "private-home-sentinel" not in out, (
        f"the lane read the private home sentinel: rc={rc}, output={out!r}")
    rc, out = run_in_lane(env, lane, f"ls -A {shlex.quote(str(home))}")
    listed = set(out.split()) if rc == 0 else set()
    assert not (listed - {".local"}), f"private home was enumerable: {out!r}"
    probe = credential.parent / "factory-039-probe"
    rc, out = run_in_lane(env, lane, f"echo pwned > {shlex.quote(str(probe))}")
    assert rc != 0 and not probe.exists(), f"private credentials were writable: {out!r}"
    if expect_event:
        ws = env.srv.ws()
        try:
            ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
            ws.wait_for("snapshot")
            spawn_in_lane(env, lane, f"cat {shlex.quote(str(sentinel))}")
            frame = ws.wait_for("isolation", timeout=15)
            assert frame["refused"] is True and frame["path"] == str(sentinel), frame
            assert frame["operation"] == "read", frame
        finally:
            ws.close()
    env.unchanged()


@pytest.mark.parametrize("removed", ["credential", "sentinel"])
def test_an_incomplete_private_home_fixture_cannot_report_a_pass(laneenv, tmp_path, removed):
    home = tmp_path / "private-home"
    credential = home / ".claude" / ".credentials.json"
    sentinel = home / ".ssh" / "id_fixture"
    credential.parent.mkdir(parents=True)
    credential.write_text("fixture credential\n")
    sentinel.parent.mkdir()
    sentinel.write_text("private-home-sentinel\n")

    private_home_boundary(laneenv, home)
    if removed == "credential":
        credential.unlink()
        credential.parent.rmdir()
    else:
        sentinel.unlink()
    with pytest.raises(AssertionError, match="incomplete private-home fixture"):
        private_home_boundary(laneenv, home)


# ── SRV-FIX-063 · where its device cases live ────────────────────────────────────────────────
#
# §1 named them `test_confine.py::…` and they are in `test_isolation.py`, beside the other cases
# about what a lane can and cannot reach. QA called that minor and judged the ticket on substance,
# and it is still one more thing a grep cannot check — so the grep is given something to find:
#
#   test_isolation.py::test_a_lane_sees_the_devices_its_resource_declares
#   test_isolation.py::test_a_lane_can_open_the_device_its_resource_declares    <- §1's criterion
#   test_isolation.py::test_a_lane_can_open_the_real_board                      <- opt-in, hardware
#   test_isolation.py::test_a_lane_without_the_resource_sees_no_device          <- negative
#   test_isolation.py::test_no_lane_can_open_a_device_no_resource_declared      <- negative


# ── SRV-FIX-077 ══════════════════════════════════════════════════════════════════════════════
#
# `SRV-FEAT-039` gave a lane the agent's own binary. `SRV-FIX-063` gave it the device its resource
# declares. Neither gave it the toolchain that talks to that device: ESP-IDF is installed under the
# developer's home, a lane's home is a tmpfs, and a `master` lane therefore won the board lock and
# could not run `cd firmware/master && ./build.sh app` at all.
#
# The fixture toolchain lives in the DEVELOPER'S REAL HOME and is declared with `~/`, because that
# is the shape the real one has and the shape that decides the answer: inside a lane `~` is
# `/home/lane`, so a bind at the host's absolute path would satisfy an argv assertion and leave
# `build.sh`'s `. ~/environment/esp-idf-v5.5.1/export.sh` meeting ENOENT — this ticket's own defect
# one name down (`SRV-FIX-069`'s lesson). It is removed on teardown, and the case asserts the rest
# of the home is still absent.

def test_a_lane_can_run_the_toolchain_its_workspace_declares(laneenv, home_toolchain):
    """THE criterion, in §1's words: a process INSIDE the lane EXECUTES a binary from it.

    Not that the argv carries a flag, and not that the directory is listable — `SRV-FIX-063`'s own
    rule, which that ticket wrote and then satisfied with `test -c`. A toolchain that is present and
    cannot be run is a lane holding the board with nothing to build it with, which is the sentence
    this ticket exists to delete.

    The path executed is the DECLARED one, `$HOME/<name>/bin/hello`, because that is the path
    `firmware/master/build.sh` uses and the one whose absence was measured in §1.
    """
    env = laneenv.build(workspaces=home_toolchain.workspaces())
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"$HOME/{TOOLCHAIN_NAME}/bin/hello")
    assert rc == 0 and "TOOLCHAIN-RAN-INSIDE-THE-LANE" in out, (
        f"the workspace declares {home_toolchain.declared} and a process inside its lane could not "
        f"execute a binary from it: rc={rc}\n{out}\n"
        f"This is the assertion the whole ticket exists for: a lane that holds the board and cannot "
        f"run the toolchain that talks to it is a lane that cannot gate.")

    # ...and the home is otherwise still the empty tmfps SRV-FEAT-039 pre-decided. What travelled is
    # exactly what the manifest named, and nothing beside it.
    rc2, out2 = run_in_lane(env, lane, "ls -A $HOME | sort | tr '\\n' ' '")
    assert rc2 == 0 and set(out2.split()) == {TOOLCHAIN_NAME}, (
        f"the lane's HOME holds more than the one declared entry: {out2!r}")

    env.unchanged()


def test_a_declared_toolchain_is_read_only(laneenv, home_toolchain):
    """A lane may execute it and read it and may NOT write it.

    `SRV-FEAT-002`'s guarantee, restated one directory over: a lane that can edit the developer's
    ESP-IDF changes what the NEXT lane builds with, and nothing downstream would ever say so.
    """
    env = laneenv.build(workspaces=home_toolchain.workspaces())
    lane = env.claim(workspace="core")

    rc, out = run_in_lane(env, lane, f"cat $HOME/{TOOLCHAIN_NAME}/VERSION")
    assert rc == 0 and "v5.5.1" in out, f"the declared toolchain is not readable: rc={rc}\n{out}"

    rc2, out2 = run_in_lane(env, lane, f"echo pwned > $HOME/{TOOLCHAIN_NAME}/VERSION")
    assert rc2 != 0, (
        f"THE LANE WROTE THE DEVELOPER'S TOOLCHAIN: rc={rc2}\n{out2}")
    assert (home_toolchain.host / "VERSION").read_text() == "v5.5.1\n", (
        "the file in the developer's home changed")

    rc3, out3 = run_in_lane(env, lane, f"touch $HOME/{TOOLCHAIN_NAME}/bin/new-tool")
    assert rc3 != 0, f"the lane created a file inside the developer's toolchain: {out3!r}"
    assert not (home_toolchain.host / "bin" / "new-tool").exists()

    env.unchanged()


# ── the bus behind the adapter ───────────────────────────────────────────────────────────────
#
# `hciconfig hci0` prints `UP RUNNING` inside a lane today, so the adapter APPEARS usable. It is
# not: `bleak` is `esp_prov.py`'s only Linux BLE transport in IDF 5.x and it reaches BlueZ over the
# D-Bus SYSTEM BUS, a socket under `/run` that nothing binds. Raw HCI answering is a different
# socket family answering a different question — `_TEMPLATE.md` §2's *"the thing started, therefore
# it is visible"* coin flip. So the assertion is a round trip to BlueZ, not a status line.

DBUS_SOCKET = "/run/dbus/system_bus_socket"


def _bluez_on_this_host():
    if not Path(DBUS_SOCKET).exists() or not shutil.which("busctl"):
        return False
    out = subprocess.run(
        ["busctl", "--system", "--no-pager", "call", "org.bluez", "/",
         "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"],
        capture_output=True, text=True, timeout=30)
    return out.returncode == 0 and "org.bluez.Adapter1" in out.stdout


HAS_BLUEZ = _bluez_on_this_host()

needs_bluez = pytest.mark.skipif(
    not HAS_BLUEZ,
    reason=f"no BlueZ adapter answering on {DBUS_SOCKET} on this machine — THIS IS NOT A PASS. "
           "The lane's BLE path is unproven in this run; see SRV-FIX-077's report.")


@needs_bluez
def test_a_lane_holding_a_bluetooth_resource_can_reach_the_bus_that_drives_it(laneenv):
    """A declared `hci` resource is worth nothing without the bus that drives it.

    An adapter has no `/dev` node, so `resources.<name>.devices` — `SRV-FIX-063`'s whole answer —
    cannot carry it. What a BLE client actually needs is `/run/dbus/system_bus_socket`, and the
    workspace declares it like any other host path.

    The assertion is a completed round trip that names an adapter, deliberately: `hciconfig hci0`
    prints `UP RUNNING` inside a lane today, while every D-Bus client in it is dead.
    """
    workspaces = {"core": {"path": ".", "toolchain": [DBUS_SOCKET]}}
    resources = {"bluetooth": {"exclusive": True, "needed_by": ["core"],
                               "description": "one hci0 on this bench"}}

    # the control: without the declaration the round trip FAILS, so the case below cannot pass on a
    # bus that was reachable all along.
    bare = laneenv.build(resources=resources)
    lane0 = bare.claim(workspace="core")
    rc0, out0 = run_in_lane(
        bare, lane0,
        "busctl --system --no-pager call org.bluez / "
        "org.freedesktop.DBus.ObjectManager GetManagedObjects")
    assert rc0 != 0 or "org.bluez.Adapter1" not in out0, (
        f"a lane that declares nothing already reaches the system bus, so the case below would "
        f"prove nothing: rc={rc0}\n{out0[:400]}")
    bare.unchanged()

    env = laneenv.build(workspaces=workspaces, resources=resources)
    lane = env.claim(workspace="core")
    assert lane["resources_held"] == ["bluetooth"], lane

    rc, out = run_in_lane(
        env, lane,
        "busctl --system --no-pager call org.bluez / "
        "org.freedesktop.DBus.ObjectManager GetManagedObjects")
    assert rc == 0 and "org.bluez.Adapter1" in out, (
        f"the lane holds `bluetooth` and cannot complete a BlueZ round trip: rc={rc}\n{out[:800]}\n"
        f"`esp_prov.py` reaches BLE through `bleak` -> BlueZ -> this socket and nothing else.")
    env.unchanged()


# ── SRV-FIX-078 · the refusal asserted AT THE BOUNDARY it protects ────────────────────────────

def test_no_declared_toolchain_can_open_the_developers_home_to_a_lane(laneenv):
    """THE criterion, and the one whose absence let `/home` through.

    `SRV-FIX-077` asserted the status code and stopped. A load-time refusal is a statement about
    the loader; what the guard exists to protect is the boundary, and only a process inside a lane
    can answer whether it still holds. §3 of this ticket: *a refusal about a boundary is asserted
    at the boundary*.

    So for each too-broad spelling: either the manifest is refused and no lane can be claimed at
    all, or a lane can be claimed and it must not be able to reach the developer's `~/.ssh`.
    Both are acceptable outcomes; reaching the keys is not.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    parent = os.path.dirname(home)

    for entry in (parent, "~/..", "/"):
        env = laneenv.build(workspaces={"core": {"path": ".", "toolchain": [entry]}})
        r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        if r.status_code != 201:
            # Refused before a lane exists — nothing can reach the home. Recorded, because a
            # `continue` that fires for the WRONG reason (a 404 on a ticket the fixture does not
            # have, say) makes this case assert nothing at all while staying green.
            assert r.status_code in (409, 422), (
                f"the claim for {entry!r} failed for an unexpected reason and this case then "
                f"asserts nothing: {r.status_code} {r.text}")
            continue
        lane = r.json()
        # the developer's home by its literal path: inside a lane `$HOME` is `/home/lane`, so
        # asking about `$HOME` would ask about the tmpfs and always answer "no".
        rc, out = run_in_lane(
            env, lane, f"test -e {home}/.ssh && echo REACHED-SSH || echo no-ssh")
        assert "REACHED-SSH" not in out, (
            f"a workspace declaring {entry!r} produced a lane that can read {home}/.ssh:\n{out}\n"
            f"That is SRV-FEAT-002's guarantee removed by one manifest line.")


def test_a_symlinked_home_cannot_be_opened_to_a_lane_either(laneenv, tmp_path):
    """Round 2's receiver half — this ticket's own §3 rule applied to its own second defect.

    Round 1 refused `/home` at load and asserted it at the boundary, and that receiver case is
    why the hole was found. The symlink variant is the same refusal reached by a different
    spelling, so it earns the same assertion: a `422` is a fact about the loader.
    """
    real = tmp_path / "export" / "homes" / "dev"
    (real / ".ssh").mkdir(parents=True)
    (real / ".ssh" / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE KEY\n")
    (tmp_path / "home").symlink_to(tmp_path / "export" / "homes")
    spelled_home = str(tmp_path / "home" / "dev")
    canonical_parent = str(tmp_path / "export" / "homes")

    env = laneenv.build(env={"HOME": spelled_home},
                        workspaces={"core": {"path": ".", "toolchain": [canonical_parent]}})
    r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    if r.status_code != 201:
        assert r.status_code in (409, 422), (
            f"the claim failed for an unexpected reason, so this case asserts nothing: "
            f"{r.status_code} {r.text}")
        return
    rc, out = run_in_lane(
        env, r.json(), f"cat {real}/.ssh/id_rsa 2>/dev/null || echo no-key")
    assert "PRIVATE KEY" not in out, (
        f"a lane read the developer's private key through a symlinked home: {out}")


def test_no_declared_host_path_can_open_the_developers_home_to_a_lane(laneenv, tmp_path):
    """The receiver, generalised off `toolchain` to every key that becomes a read-only bind.

    `SRV-FIX-078` §3 wrote the rule as *a refusal about a boundary is asserted at the boundary*,
    and then wrote exactly one receiver case whose name and whose loop both start at `toolchain`.
    The boundary does not care which manifest key opened it: `agent.command`'s parent directory is
    bound by the same line (`confine.cpp`, `for (const std::string& ro : p.hostReadOnly)`).

    The CONTROL is not optional. Without it a confinement that simply failed to build — no lane, no
    bind, nothing reachable — reads as a pass, and this case would go green for the one reason that
    means nothing.
    """
    import shutil as _shutil, stat as _stat
    home = os.path.realpath(os.path.expanduser("~"))

    # CONTROL — the ordinary declaration. The lane must exist, must run, and must reach nothing.
    control = laneenv.build(workspaces={"core": {"path": "."}})
    got = control.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert got.status_code == 201, f"the control lane could not be claimed: {got.text}"
    rc, out = run_in_lane(control, got.json(),
                          f"test -e {home}/.ssh && echo REACHED-SSH || echo no-ssh")
    assert rc == 0 and "no-ssh" in out, (
        f"CONTROL: an ordinarily-configured lane could not run, or reached the home: rc={rc} {out!r}\n"
        f"Everything below is meaningless unless this line holds.")

    # ...and the same boundary, opened through `agent.command` instead of `toolchain`.
    probe = Path(home) / ".har131-agent-at-home-root"
    _shutil.copy("/bin/sh", probe)
    probe.chmod(probe.stat().st_mode | _stat.S_IEXEC)
    try:
        env = laneenv.build(agent={"command": str(probe)}, workspaces={"core": {"path": "."}})
        r = env.srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        if r.status_code != 201:
            # QA round 1, minor: read the CODE, not the status. A 409 raised for an exhausted lane
            # pool or a held resource would otherwise read as a pass on this branch.
            code = (r.json().get("error") or {}).get("code", "")
            assert code == "invalid_manifest", (
                f"the claim failed for a reason unrelated to the refusal this case is about, so "
                f"it asserts nothing: {r.status_code} {r.text}")
            return
        rc2, out2 = run_in_lane(env, r.json(),
                                f"test -e {home}/.ssh && echo REACHED-SSH || echo no-ssh")
        assert "REACHED-SSH" not in out2, (
            f"a lane read the developer's ~/.ssh through agent.command's directory: {out2!r}")
    finally:
        probe.unlink(missing_ok=True)


def test_no_bind_the_policy_builds_can_contain_the_developers_home(laneenv, argv_recorder,
                                                                  private_credentials):
    """QA round 1, finding 1 — derived from what is MOUNTED, not from a list of keys.

    Round 1 wrote a case named for host paths that hand-listed exactly one key in exactly one
    spelling, while its docstring claimed the generalisation it did not perform. §1 pre-decided
    the opposite in these words: *"the question is asked of every path that becomes a bind,
    present and future … by construction rather than by somebody remembering to add a loop
    entry."*

    QA demonstrated the gap with one line — `hostReadOnly.push_back(agent.credentialsAbs)`, the
    line `SRV-FIX-052` removed — and a manifest declaring `credentials_dir` as the whole home: a
    lane read `~/.ssh` while both of round 1's new criteria reported green.

    So this asks the argv the tool actually built. Any future key that adds a `--ro-bind` is
    covered the day it is added, because nothing here names a key at all.
    """
    requires_real_agent()
    home = os.path.realpath(os.path.expanduser("~"))
    env = laneenv.build(agent=agent_manifest(),
                        env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")

    argv = argv_recorder.argv()
    binds = ro_binds(argv)
    assert binds, f"the policy bound nothing read-only; this case would prove nothing: {argv}"

    offenders = []
    for src, _dst in binds:
        real = os.path.realpath(src)
        if real == "/" or real == home or (home + "/").startswith(real.rstrip("/") + "/"):
            offenders.append(src)
    assert not offenders, (
        f"the policy read-binds a path that IS or CONTAINS the developer's home {home!r}: "
        f"{offenders}\nEvery bind in the argv:\n  " + "\n  ".join(f"{s} -> {d}" for s, d in binds))


# ── SRV-FEAT-002 round 3 ═════════════════════════════════════════════════════════════════════
#
# The two argv cases above are what stops a convenience bind being added under the developer's
# home, and both call `requires_real_agent()` — so inside a lane, which has no `~/.claude` and by
# this ticket's design never will, they are RED and prove nothing. SRV-FEAT-002's round 3 was
# implemented in exactly that environment: it made a workspace's declared toolchain and its
# declared agent REACHABLE inside a lane, which is one rewritten `PATH` and one symlink, and the
# obvious wrong way to do it is another bind. So the guard is asked again here, of the same argv,
# in a form a lane can run.

def test_making_a_declared_toolchain_reachable_adds_no_bind(laneenv, declared_programs,
                                                            argv_recorder):
    """Reachability is `PATH` and a symlink. Every bind is still declared, and none is the home.

    Not a restatement of the two cases above: those name `REAL_HOME` and the real agent, and this
    one names the home the fixture itself declared, so it holds wherever the suite runs.
    """
    env = laneenv.build(workspaces=declared_programs.workspaces(),
                        agent={"command": declared_programs.agent},
                        env={**declared_programs.env(),
                             "FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim(workspace="core")
    spawn_in_lane(env, lane, "true")

    argv = argv_recorder.argv()
    home = declared_programs.host_home
    declared_bin = os.path.realpath(Path(home) / TOOLCHAIN_DECLARED / "bin")
    declared_agent = os.path.realpath(declared_programs.agent_host)
    declared_agent_projection = (declared_agent, f"/factory/bin/{declared_programs.agent}")

    def under_home(src):
        real = os.path.realpath(src)
        return real == home or real.startswith(home + "/")

    home_binds = {(os.path.realpath(s), d) for s, d in ro_binds(argv) if under_home(s)}
    offenders = {(s, d) for s, d in home_binds
                 if s != declared_bin and (s, d) != declared_agent_projection}
    assert not offenders, (
        f"the policy read-binds an undeclared developer-home path:\n"
        + "\n".join(f"  --ro-bind {s} {d}" for s, d in sorted(offenders)))
    assert declared_agent_projection in home_binds, (
        f"the declared agent was not projected at /factory/bin: {sorted(home_binds)}")

    for i, word in enumerate(argv):
        if word == "--bind":
            assert not under_home(argv[i + 1]), \
                f"a WRITABLE bind under the declared home: --bind {argv[i + 1]} {argv[i + 2]}"

    for src, dst in ro_binds(argv):
        real = os.path.realpath(src)
        assert not (real == "/" or real == home
                    or (home + "/").startswith(real.rstrip("/") + "/")), (
            f"the policy read-binds a path that IS or CONTAINS the declared home {home!r}: {src}")

    joined = " ".join(argv)
    assert TOOLCHAIN_UNDECLARED not in joined, (
        f"the undeclared directory reached the argv at all:\n{joined}")
    assert declared_programs.hidden_host not in joined, (
        f"an undeclared sibling executable was projected into the lane:\n{joined}")

    # ...and the reachability itself is where it should be: a PATH and a symlink, nothing else.
    assert "--setenv" in argv and "PATH" in argv, f"the lane was given no PATH of its own: {argv}"
    path = argv[argv.index("PATH") + 1]
    assert f"{TOOLCHAIN_DECLARED}/bin" in path, (
        f"the declared toolchain is bound and still not on the lane's PATH: {path}")
    assert TOOLCHAIN_UNDECLARED not in path, f"an undeclared directory is on the lane's PATH: {path}"
    env.unchanged()


# ── SRV-FIX-532 cluster E2 — the isolation floor refers to the SANDBOX, not to `$HOME` ────────
#
# `SRV-FIX-466`. Both halves of the floor took *the developer's machine* as their reference point,
# and inside a lane that reference point IS the sandbox: `'/home/lane' != '/home/lane'` is the
# whole ticket in one assertion. The credential probe is answered above by `_real_credentials`
# and `_operator_home`; this is the other half — a probe path chosen from the POLICY the lane was
# actually given, so a case can never again prove confinement by writing inside the sandbox.


def writable_set_of(argv):
    """Every path the policy makes writable, read off the argv the tool BUILT.

    Derived from the argv rather than from a list of constants, because the list is what a reader
    believes and the argv is what the lane got. A `--bind` is read-write, `--ro-bind` is not, and
    `--tmpfs` / `--dir` / `--proc` / `--dev` each make their destination writable.
    """
    out, i = [], 0
    while i < len(argv):
        token = argv[i]
        if token == "--bind":
            out.append(argv[i + 2])
            i += 3
        elif token in ("--ro-bind", "--ro-bind-try", "--symlink", "--bind-try"):
            i += 3
        elif token in ("--tmpfs", "--dir", "--proc", "--dev"):
            out.append(argv[i + 1])
            i += 2
        else:
            i += 1
    assert out, f"no writable entry found in the policy, so the chooser has nothing to avoid:\n{argv}"
    return out


def a_path_the_lane_cannot_write(argv, candidate):
    """`candidate`, once it is shown to be outside EVERY writable entry the policy builds.

    The chooser `SRV-FIX-466` criterion 1 asks for, and its refusal is the criterion-3 negative: a
    candidate inside the writable set is refused HERE, naming the entry it collides with, rather
    than being handed to a lane and reported as a successful refusal.
    """
    candidate = str(candidate)
    for entry in writable_set_of(argv):
        if candidate == entry or candidate.startswith(entry.rstrip("/") + "/"):
            raise AssertionError(
                f"{candidate} is inside the writable entry {entry!r} that this lane's policy "
                f"builds, so a write refused there would prove nothing about confinement — it is "
                f"the defect `SRV-FIX-466` is about, arriving from the other side")
    return candidate


def test_a_probe_inside_the_writable_set_is_refused_with_the_reason(laneenv, argv_recorder,
                                                                   private_credentials):
    """§1 E2 — a case that proves confinement by writing INSIDE the sandbox cannot be written.

    `SRV-FIX-466` criterion 3, the mandatory negative, driven in both directions on one policy:

      * every writable entry the policy builds is refused by the chooser, by name, and the sweep
        is asserted non-empty before anything is concluded from it;
      * the operator's own home — derived from the passwd entry, which is the same answer inside a
        lane and outside one — is chosen, and a write to it from inside a real lane fails.
    """
    requires_real_agent()
    env = laneenv.build(agent=agent_manifest(),
                        env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")
    argv = argv_recorder.argv()

    writable = writable_set_of(argv)
    assert len(writable) > 1, (
        f"the policy makes {len(writable)} path(s) writable, so this sweep cannot disagree with "
        f"itself: {writable}")
    for entry in writable:
        with pytest.raises(AssertionError) as why:
            a_path_the_lane_cannot_write(argv, entry.rstrip("/") + "/factory-466-probe")
        assert entry in str(why.value), (
            f"the chooser refused a path inside {entry!r} without naming the entry: {why.value}")

    chosen = a_path_the_lane_cannot_write(argv, REAL_HOME / ".factory-466-escape-probe")
    assert str(REAL_HOME) in chosen, chosen

    real = laneenv.build(agent=agent_manifest())
    running = real.claim()
    rc, out = run_in_lane(real, running, f"echo pwned > {chosen}")
    assert rc != 0, (
        f"a path the policy makes writable NOWHERE was written from inside the lane: "
        f"{chosen} rc={rc} {out!r}")
    assert not Path(chosen).exists(), f"{chosen} exists on the host after the probe"


# ── SRV-FIX-532 cluster C1 — a lane holds no forge credential, asserted at the boundary ───────
#
# `SRV-FIX-438` criterion 2. A refusal about a boundary is asserted AT the boundary: the manifest
# case above shows the loader said 422, and that is a criterion about a status code. This one runs
# INSIDE a real lane and asks the three questions a lane would have to answer `yes` to for the
# credential to be reachable.


def test_a_lane_cannot_reach_the_forge_or_push(laneenv):
    """§1 C1 NEGATIVE — from inside a real lane, the forge is out of reach and a push fails.

    Three probes, each a real process in a real lane, never an assertion about the argv:

      * the forge's credential directory under the operator's home is not there;
      * `gh auth status` exits non-zero (or `gh` is not on the lane's PATH at all, which is the
        same answer arrived at one step earlier);
      * `git push origin <the lane's own branch>` exits non-zero.

    The push is the one that matters and it is the one an argv assertion cannot make: `origin` is
    reachable and unauthenticated by design — the mechanism is the CREDENTIAL, not the network,
    because the agent needs the network to reach its own API. So the failure is asserted, never the
    route.
    """
    env = laneenv.build()
    lane = env.claim()

    # A control, first: a command that MUST succeed, so three non-zero exits below are three
    # refusals and not three ways of never having run anything.
    rc, out = run_in_lane(env, lane, "git rev-parse --is-inside-work-tree")
    assert rc == 0 and "true" in out, (
        f"nothing ran inside the lane, so every refusal below is vacuous: rc={rc} {out!r}")

    rc, out = run_in_lane(env, lane, f"ls -A {REAL_HOME}/.config/gh")
    assert rc != 0, (
        f"the forge's credential directory is READABLE from inside the lane: {out!r}")

    rc, out = run_in_lane(env, lane, "gh auth status")
    assert rc != 0, f"`gh auth status` succeeded from inside the lane: {out!r}"

    branch = run_in_lane(env, lane, "git branch --show-current")[1].strip()
    assert branch, "the lane is on no branch, so the push below would prove nothing"
    rc, out = run_in_lane(env, lane, f"git push origin {branch}")
    assert rc != 0, (
        f"a lane PUSHED its own branch to the forge: branch={branch!r} {out!r}")

    env.unchanged()


def test_an_undeclared_workspace_binds_nothing(laneenv, argv_recorder, tmp_path):
    """§1 E-NEG — a ticket that names no extra workspace gets its own toolchain and no other.

    `SRV-FIX-456`'s mandatory negative. `workspaces:` declares something only if its ABSENCE
    declares nothing, and the obvious wrong fix — bind every workspace's toolchain into every lane
    — passes every positive case in this cluster while handing a `factory-server` lane the
    client's Chrome and a credential directory's worth of surface.

    Asserted from INSIDE the lane as well as against the argv: a bind that is in the policy and
    unreachable, or reachable and not in the policy, are both the same lie one layer apart.
    """
    mine = tmp_path / "mine-toolchain"
    theirs = tmp_path / "their-toolchain"
    for where in (mine, theirs):
        (where / "bin").mkdir(parents=True)
        (where / "bin" / "tool").write_text("#!/bin/sh\necho ran\n")
        (where / "bin" / "tool").chmod(0o755)

    env = laneenv.build(workspaces={"core": {"path": ".", "toolchain": [str(mine)]},
                                    "consumer": {"path": ".", "toolchain": [str(theirs)]}},
                        env={"FACTORY_CONFINE_BIN": argv_recorder.bin})
    lane = env.claim()
    spawn_in_lane(env, lane, "true")
    argv = argv_recorder.argv()

    bound = [src for src, _dst in ro_binds(argv)]
    assert str(mine) in bound, (
        f"the lane's OWN workspace toolchain is not bound, so this negative is about a lane that "
        f"gets nothing rather than one that gets only its own: {bound}")
    assert str(theirs) not in bound, (
        f"a workspace this ticket never named was bound into the lane: {theirs} in {bound}\n"
        f"`workspaces:` declares something only if its absence declares nothing.")

    # ...and from inside, which is where a bind is either real or it is not.
    live = laneenv.build(workspaces={"core": {"path": ".", "toolchain": [str(mine)]},
                                     "consumer": {"path": ".", "toolchain": [str(theirs)]}})
    running = live.claim()
    rc, _out = run_in_lane(live, running, f"test -x {mine}/bin/tool")
    assert rc == 0, f"the lane cannot reach its own declared toolchain at {mine}"
    rc, out = run_in_lane(live, running, f"test -e {theirs}")
    assert rc != 0, (
        f"a lane reached the toolchain of a workspace its ticket never named: {theirs} ({out!r})")
