"""SRV-FEAT-015 — the lane runs the workspace's own command, and shows what it changed.

Two halves, and both of them are about not having an opinion:

- `E19` runs **the exact string the manifest declares**, in the lane's worktree, and reports the
  exit code as a fact. A tool that invents a default build command runs the wrong one; a tool that
  decides a non-zero exit is a failure has taken over the repository's own gate.
- `E10` lists what the lane changed — files with `+n/-n` and commits — using **read-only git only**,
  because a review tool that writes into the repository it is reviewing is not a review tool.

Every case builds its own synthetic repository and points a server at it, and every case asserts
the main checkout is byte-identical afterwards.
"""

import json
import os
import stat
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema
import pytest

from test_lane import branch_exists, build_repo, git, lane_path, status_of

SOCKET_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)

# The five read-only subcommands `lane::diff` is allowed to run, written down here so the set is
# asserted against a list a person wrote rather than against whatever the code happens to do.
# `status` is deliberately absent: it refreshes the index, and refreshing the index is a write.
DIFF_GIT = ["diff", "log", "ls-files", "merge-base", "rev-parse"]

# A gate that reports what it was actually handed. Nothing about it is a toy: the four assertions
# it carries — argument count, argument values, quoting, working directory — are exactly the four
# ways "verbatim" can quietly stop being true.
GATE_SCRIPT = """#!/bin/sh
echo "argc=$#"
echo "arg1=[$1]"
echo "arg2=[$2]"
echo "arg3=[$3]"
echo "pwd=$(pwd)"
echo "gate ok"
"""

BUILD_SCRIPT = """#!/bin/sh
echo "build ran"
"""

# Exits non-zero, loudly. The repository's business, not the tool's.
EXIT7_SCRIPT = """#!/bin/sh
echo "seven lines of trouble"
exit 7
"""

# The one a repository declares wrong. It never returns, and it keeps printing while it does not.
HANG_SCRIPT = """#!/bin/sh
echo "hang: started"
i=0
while true; do
  i=$((i+1))
  echo "hang: tick $i"
  sleep 1
done
"""

# SRV-FEAT-500. One script, told which declared command it is standing for, that keeps printing
# for long enough that a client can go away and come back while it is still running. The three
# arms of `test_review_runs_and_replays_like_build` all drive this, so what is asserted about
# `review` is asserted about `build` and `gate` by the same lines.
SAY_SCRIPT = """#!/bin/sh
echo "ran: $1"
i=0
while [ $i -lt 4 ]; do
  i=$((i+1))
  echo "$1: tick $i"
  sleep 1
done
echo "done: $1"
"""

SCRIPTS = {
    "gate.sh": GATE_SCRIPT,
    "build.sh": BUILD_SCRIPT,
    "exit7.sh": EXIT7_SCRIPT,
    "hang.sh": HANG_SCRIPT,
    "say.sh": SAY_SCRIPT,
}

# The command keys `manifest.schema.json` declares. `test_manifest.py` has derived them since
# `SRV-FIX-202` and this file uses THAT function rather than a second copy of it: two derivations
# of one set are two answers waiting to disagree, which is the defect this module's own cases are
# about one level down.
from test_manifest import workspace_command_keys_202 as declared_command_keys_500  # noqa: E402


@pytest.fixture
def cmdrepo(tmp_path, serve):
    """A repository whose workspaces declare commands, and a server pointed at it.

    The scripts are COMMITTED: a lane's worktree is a clean checkout, so anything a lane is
    expected to run has to be in the repository rather than lying beside it.
    """
    made = []

    class Env:
        def build(self, workspaces=None, **kw):
            root = build_repo(tmp_path / f"repo{len(made)}", workspaces=workspaces, **kw)
            for name, text in SCRIPTS.items():
                p = root / name
                p.write_text(text)
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            git(root, "add", "-A")
            git(root, "commit", "-m", "scripts")

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

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()

    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed by the end of the test:\n"
            f"before:\n{before}\nafter:\n{status_of(root)}")


def workspace(gate=None, build=None, review=None, path="."):
    w = {"path": path}
    if gate is not None:
        w["gate"] = gate
    if build is not None:
        w["build"] = build
    if review is not None:
        w["review"] = review
    return {"core": w}


def run_command(env, lane, which):
    return env.srv.post(lane_path(lane) + "/commands", json={"which": which})


def collect(ws, timeout=60):
    """Every `command` frame until one carries an exit code. Each is schema-checked."""
    frames = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = ws.wait_for("command", timeout=max(1, deadline - time.time()))
        SOCKET_VALIDATOR.validate(frame)
        frames.append(frame)
        if "exit_code" in frame and frame["exit_code"] is not None:
            return frames
    raise AssertionError(f"no exit_code frame within {timeout}s; saw {frames}")


def output_of(frames):
    return "".join(f.get("chunk") or "" for f in frames)


def subscribed(env, lane):
    ws = env.srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane}})
    ws.wait_for("snapshot")
    return ws


# SRV-FIX-559: the command, not a warm-up agent, must prepare its profile.
CONFIG_REVIEW = r'''import json, os, sys
from pathlib import Path
plan = json.loads(Path('review-probe.json').read_text())
home = Path('/factory/agent-config')
denied = {}
for label, path in plan['forbidden'].items():
    try:
        Path(path).read_bytes()
        denied[label] = False
    except OSError:
        denied[label] = True
print('boundary=' + json.dumps(denied), flush=True)
credential = home / plan['credential']
if not credential.is_file():
    print('missing selected profile marker', flush=True)
    sys.exit(23)
marker = json.loads(credential.read_text())['marker']
config = home / plan['config']
trust = config.read_text()
if plan['driver'] == 'codex':
    trusted = ('[projects.' + json.dumps(os.getcwd()) + ']') in trust
else:
    trusted = json.loads(trust)['projects'][os.getcwd()]['hasTrustDialogAccepted']
state = home / 'review-local-state'
previous = state.read_text() if state.exists() else None
state.write_text('kept-by-review')
print('profile=' + json.dumps({'marker': marker, 'trusted': trusted,
    'previous': previous, 'home': str(home),
    'inode': [home.stat().st_dev, home.stat().st_ino]}), flush=True)
sys.exit(7 if previous else 0)
'''


@pytest.fixture
def command_profiles(tmp_path, serve):
    from test_agent_profiles import CAPTURE, PEER

    made = []

    class Env:
        def build(self, driver='codex', legacy=False):
            self.root = build_repo(tmp_path / 'repo')
            self.config_root = tmp_path / 'owned-config'
            self.source_bytes = {}
            declared = {}
            for name, kind in (('reviewer', driver), ('worker', 'codex')):
                directory = tmp_path / (name + '-source')
                directory.mkdir()
                filename = 'auth.json' if kind == 'codex' else '.credentials.json'
                source = directory / filename
                source.write_text(json.dumps({'marker': name + '-only'}))
                self.source_bytes[source] = source.read_bytes()
                declared[name] = {'driver': kind, 'command': '/usr/bin/python3 agent.py',
                                  'models': [name + '-model'],
                                  'default_model': name + '-model',
                                  'credentials_dir': str(directory)}
            self.secret = tmp_path / 'undeclared-host-marker'
            self.secret.write_text('host-only')
            self.credential = 'auth.json' if driver == 'codex' else '.credentials.json'
            plan = {'driver': driver, 'credential': self.credential,
                    'config': 'config.toml' if driver == 'codex' else '.claude.json',
                    'forbidden': {'other-profile-source': str(tmp_path / 'worker-source/auth.json'),
                                  'selected-profile-source': str(tmp_path / 'reviewer-source' / self.credential),
                                  'undeclared-host': str(self.secret)}}
            (self.root / 'review-probe.py').write_text(CONFIG_REVIEW)
            (self.root / 'review-probe.json').write_text(json.dumps(plan))
            # The recorded peer drives only the alternate-profile E18 control. It never
            # implements command preparation, which is performed solely by Factory.
            prelude = """import json, os
from pathlib import Path
home = Path(os.environ['CODEX_HOME'])
Path('alternate-profile.json').write_text(json.dumps({
    'marker': json.loads((home / 'auth.json').read_text())['marker'],
    'inode': [home.stat().st_dev, home.stat().st_ino]}))
"""
            (self.root / 'agent.py').write_text(prelude + PEER)
            (self.root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
            manifest_path = self.root / '.factory/factory.json'
            manifest = json.loads(manifest_path.read_text())
            manifest.pop('agent', None)
            if legacy:
                manifest['agent'] = {'command': '/bin/cat',
                                     'credentials_dir': declared['reviewer']['credentials_dir']}
            else:
                manifest.update(default_agent='reviewer', agents=declared)
            variable = 'CODEX_HOME' if driver == 'codex' else 'CLAUDE_CONFIG_DIR'
            self.command = variable + '=/factory/agent-config /usr/bin/python3 review-probe.py'
            manifest['workspaces']['core']['review'] = self.command
            manifest_path.write_text(json.dumps(manifest))
            git(self.root, 'add', '.')
            git(self.root, 'commit', '-m', 'synthetic command profiles')
            self.before = status_of(self.root)
            self.srv = serve(self.root, FACTORY_AGENT_CONFIG_ROOT=str(self.config_root))
            claimed = self.srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
            assert claimed.status_code == 201, claimed.text
            self.lane = claimed.json()
            made.append(self)
            return self

        def review(self):
            ws = subscribed(self, self.lane['lane'])
            try:
                started = run_command(self, self.lane['lane'], 'review')
                assert started.status_code == 202, started.text
                body = started.json()
                assert body['command'] == self.command, body
                frames = []
                while True:
                    frame = ws.wait_for('command', timeout=15)
                    SOCKET_VALIDATOR.validate(frame)
                    if frame['command_id'] != body['command_id']:
                        continue
                    assert frame['lane'] == self.lane['lane'] and frame['which'] == 'review'
                    frames.append(frame)
                    if frame.get('exit_code') is not None:
                        break
            finally:
                ws.close()
            output = output_of(frames)
            facts = {line.split('=', 1)[0]: json.loads(line.split('=', 1)[1])
                     for line in output.splitlines() if line.startswith(('boundary=', 'profile='))}
            return frames[-1]['exit_code'], facts, output

        def selected(self, result, exit_code=0, previous=None):
            code, facts, output = result
            assert code == exit_code, output
            assert facts['profile']['marker'] == 'reviewer-only', output
            assert facts['profile']['trusted'] is True, output
            assert facts['profile']['previous'] == previous, output
            assert facts['profile']['home'] == '/factory/agent-config', output
            return facts

    yield lambda **kw: Env().build(**kw)
    for env in made:
        assert status_of(env.root) == env.before
        assert env.secret.read_text() == 'host-only'
        for source, original in env.source_bytes.items():
            assert source.read_bytes() == original, source


@pytest.mark.parametrize('driver', ['codex', 'claude'])
def test_a_cold_declared_command_receives_its_default_profile_config(command_profiles, driver):
    env = command_profiles(driver=driver)
    assert not list(env.config_root.rglob(env.credential)), 'fixture started warm'
    env.selected(env.review())
    assert not (Path(env.lane['worktree']) / 'alternate-profile.json').exists()


def test_a_declared_command_does_not_reuse_the_previous_profiles_config(command_profiles):
    from test_agent import assert_reaped
    env = command_profiles()
    path = lane_path(env.lane['lane'])
    started = env.srv.post(path + '/runs', json={'agent': 'worker', 'prompt': 'hello'})
    assert started.status_code == 201, started.text
    alternate = json.loads((Path(env.lane['worktree']) / 'alternate-profile.json').read_text())
    assert alternate['marker'] == 'worker-only'
    assert env.srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(started.json()['pid'])
    other_files = list(env.config_root.rglob('auth.json'))
    assert len(other_files) == 1 and json.loads(other_files[0].read_text())['marker'] == 'worker-only'
    plan_path = Path(env.lane['worktree']) / 'review-probe.json'
    plan = json.loads(plan_path.read_text())
    plan['forbidden']['prepared-other-profile'] = str(other_files[0])
    plan_path.write_text(json.dumps(plan))
    facts = env.selected(env.review())
    assert facts['profile']['inode'] != alternate['inode']
    assert facts['boundary']['prepared-other-profile'] is True


def test_a_cold_command_cannot_read_other_profile_or_undeclared_configuration(command_profiles):
    env = command_profiles()
    result = env.review()
    assert result[1]['boundary'] and all(result[1]['boundary'].values()), result[2]
    env.selected(result)


def test_repeated_declared_commands_preserve_profile_local_state_and_exit_status(command_profiles):
    env = command_profiles()
    first = env.selected(env.review())
    second = env.selected(env.review(), exit_code=7, previous='kept-by-review')
    assert first['profile']['inode'] == second['profile']['inode']


def test_a_restarted_server_prepares_a_recovered_lanes_cold_command(command_profiles, serve):
    from conftest import stop_process
    env = command_profiles()
    first = env.review()
    assert list(env.config_root.rglob('reviewer')), 'first command did not create its directory'
    stop_process(env.srv.proc)
    env.srv = serve(env.root, FACTORY_AGENT_CONFIG_ROOT=str(env.config_root))
    assert not list(env.config_root.rglob('reviewer')), 'startup did not sweep old configuration'
    second = env.review()
    env.selected(first)
    env.selected(second)


def test_a_cold_legacy_command_keeps_the_singular_profile_contract(command_profiles):
    env = command_profiles(driver='claude', legacy=True)
    env.selected(env.review())
    assert not list(env.config_root.rglob('profiles'))


# ── F21 · the workspace's own command ────────────────────────────────────────────────────────

def test_gate_command_streams(cmdrepo):
    """SPEC.md F21 / E19 — the EXACT string the manifest declares, in the lane's worktree,
    streaming M10 chunks, finishing with the real exit code.

    The command is asserted from INSIDE itself. `argc`, the two arguments and `pwd` are the four
    ways "the string came through verbatim" can quietly stop being true: an argument the tool
    appended, a re-quoting that split `two three`, a shell wrapper of the tool's own, or a `cd`
    the tool decided to add.
    """
    declared = './gate.sh one "two three"'
    env = cmdrepo.build(workspaces=workspace(gate=declared, build="./build.sh"))
    lane = env.claim()

    ws = subscribed(env, lane["lane"])
    try:
        started = time.monotonic()
        r = run_command(env, lane["lane"], "gate")
        answered = time.monotonic() - started

        assert r.status_code == 202, r.text
        body = r.json()
        assert body["command"] == declared, (
            f"the tool did not run the manifest's string verbatim: {body['command']!r}")
        assert body["which"] == "gate", body
        assert body["lane"] == lane["lane"], body
        assert body["command_id"], body
        assert answered < 5, f"the endpoint took {answered:.1f}s to answer 202"

        frames = collect(ws)
    finally:
        ws.close()

    assert all(f["lane"] == lane["lane"] for f in frames), frames
    assert all(f["command_id"] == body["command_id"] for f in frames), frames
    assert all(f["which"] == "gate" for f in frames), frames

    chunks = [f for f in frames if f.get("chunk")]
    assert chunks, f"no output streamed at all: {frames}"

    out = output_of(frames)
    assert "argc=2" in out, f"the tool added arguments of its own: {out!r}"
    assert "arg1=[one]" in out, out
    assert "arg2=[two three]" in out, f"the tool re-quoted the declared string: {out!r}"
    assert "arg3=[]" in out, f"something extra was appended: {out!r}"
    assert f"pwd={lane['worktree']}" in out, f"it did not run in the lane's worktree: {out!r}"
    assert "gate ok" in out, out

    last = frames[-1]
    assert last["exit_code"] == 0, last
    assert not last.get("chunk"), "the exit frame is its own frame"

    # ...and `build` is a different declared string, not a variation on the same one.
    ws = subscribed(env, lane["lane"])
    try:
        r = run_command(env, lane["lane"], "build")
        assert r.status_code == 202, r.text
        assert r.json()["command"] == "./build.sh", r.text
        out = output_of(collect(ws))
        assert "build ran" in out, out
    finally:
        ws.close()

    env.unchanged()


# ── E19 · nothing is invented ────────────────────────────────────────────────────────────────

def test_a_workspace_with_no_such_command_is_404_not_declared(cmdrepo):
    """A workspace that declares no `gate` gets a 404 naming the manifest key that would have to
    exist. A tool that invents a default build command is a tool that runs the wrong one."""
    env = cmdrepo.build(workspaces=workspace(build="./build.sh"))
    lane = env.claim()

    r = run_command(env, lane["lane"], "gate")
    assert r.status_code == 404, r.text
    err = r.json()["error"]
    assert err["code"] == "not_declared", err
    assert err["detail"]["field"] == "workspaces.core.gate", err
    assert err["detail"]["workspace"] == "core", err

    # The one it does declare still runs — the refusal is about the key, not about the endpoint.
    r = run_command(env, lane["lane"], "build")
    assert r.status_code == 202, r.text

    # An unknown lane, and a `which` that is neither.
    r = env.srv.post("/api/lanes/lane/99/commands", json={"which": "gate"})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found", r.text

    r = run_command(env, lane["lane"], "deploy")
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "bad_which", err
    assert err["detail"]["declared"] == declared_command_keys_500(), err

    env.unchanged()


def test_the_tool_has_no_opinion_about_the_exit_code(cmdrepo):
    """A non-zero exit is reported as a fact, not as a failure the tool interprets.

    Whether a gate passing matters is the repository's business (SPEC.md §10). So: the request is
    still accepted, the exit code is the real one, no frame says pass or fail, and the lane is in
    exactly the state a zero would have left it in.
    """
    env = cmdrepo.build(workspaces=workspace(gate="./exit7.sh", build="./build.sh"))
    lane = env.claim()

    ws = subscribed(env, lane["lane"])
    try:
        r = run_command(env, lane["lane"], "gate")
        assert r.status_code == 202, f"a command that will exit 7 was refused up front: {r.text}"
        frames = collect(ws)
    finally:
        ws.close()

    last = frames[-1]
    assert last["exit_code"] == 7, f"the real exit code was not reported: {last}"
    assert "seven lines of trouble" in output_of(frames), frames

    verdict = {"ok", "failed", "passed", "success", "error", "status"}
    for f in frames:
        assert not (verdict & set(f)), f"the tool formed an opinion about the exit code: {f}"

    # The lane is untouched by the number: the next command runs normally.
    ws = subscribed(env, lane["lane"])
    try:
        r = run_command(env, lane["lane"], "build")
        assert r.status_code == 202, r.text
        assert collect(ws)[-1]["exit_code"] == 0, "a lane was left unusable by a non-zero exit"
    finally:
        ws.close()

    env.unchanged()


# ── F23 · what the lane changed ──────────────────────────────────────────────────────────────

def test_diff_lists_files_and_commits(cmdrepo):
    """SPEC.md F23 / E10 — two edits and one commit come back as both files with `+n/-n` and the
    commit with its subject and sha; `?path=` returns that file's patch.

    Deciding whether to keep an agent's work is the point of the exercise, and it cannot be done
    from a transcript.
    """
    env = cmdrepo.build(workspaces=workspace(gate="./gate.sh"))
    lane = env.claim()
    wt = Path(lane["worktree"])
    base_sha = git(env.root, "rev-parse", "main").stdout.strip()

    (wt / "README.md").write_text("a repository the factory is pointed at\nand a lane edited it\n")
    (wt / "notes.txt").write_text("one\ntwo\nthree\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "lane work")
    commit_sha = git(wt, "rev-parse", "HEAD").stdout.strip()

    r = env.srv.get(lane_path(lane["lane"]) + "/diff")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["lane"] == lane["lane"], body
    assert body["branch"] == "ticket/TCK-001", body
    assert body["base"] == base_sha, body

    files = {f["path"]: f for f in body["files"]}
    assert set(files) == {"README.md", "notes.txt"}, body["files"]
    assert files["README.md"]["added"] == 1, files["README.md"]
    assert files["README.md"]["removed"] == 0, files["README.md"]
    assert files["README.md"]["status"] == "modified", files["README.md"]
    assert files["notes.txt"]["added"] == 3, files["notes.txt"]
    assert files["notes.txt"]["removed"] == 0, files["notes.txt"]
    assert files["notes.txt"]["status"] == "added", files["notes.txt"]

    assert len(body["commits"]) == 1, body["commits"]
    commit = body["commits"][0]
    assert commit["sha"] == commit_sha, commit
    assert commit["subject"] == "lane work", commit
    assert commit["iso"].startswith("20"), commit

    assert body["totals"]["files"] == 2, body["totals"]
    assert body["totals"]["added"] == 4, body["totals"]
    assert body["totals"]["removed"] == 0, body["totals"]
    assert body["totals"]["commits"] == 1, body["totals"]

    # One file's patch.
    r = env.srv.get(lane_path(lane["lane"]) + "/diff", params={"path": "notes.txt"})
    assert r.status_code == 200, r.text
    patch = r.json()["patch"]
    assert "+one" in patch and "+two" in patch and "+three" in patch, patch
    assert "notes.txt" in patch, patch
    assert "README.md" not in patch, "the patch for one file carried another file's diff"

    # Uncommitted work is still the lane's work: a person deciding about it must see it.
    (wt / "notes.txt").write_text("one\ntwo\nthree\nfour\n")
    (wt / "scratch.txt").write_text("not committed\n")
    r = env.srv.get(lane_path(lane["lane"]) + "/diff")
    assert r.status_code == 200, r.text
    body = r.json()
    files = {f["path"]: f for f in body["files"]}
    assert set(files) == {"README.md", "notes.txt", "scratch.txt"}, body["files"]
    assert files["notes.txt"]["added"] == 4, files["notes.txt"]
    assert files["scratch.txt"]["added"] == 1, files["scratch.txt"]
    assert files["scratch.txt"]["committed"] is False, files["scratch.txt"]
    assert body["totals"]["commits"] == 1, body["totals"]

    assert branch_exists(env.root, "ticket/TCK-001")
    env.unchanged()


def test_a_lane_that_changed_nothing_says_so(cmdrepo):
    """Empty arrays and totals of zero — which the screen renders as "Nothing changed in this
    lane yet". NOT a 404, because the lane exists."""
    env = cmdrepo.build(workspaces=workspace(gate="./gate.sh"))
    lane = env.claim()

    r = env.srv.get(lane_path(lane["lane"]) + "/diff")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == [], body
    assert body["commits"] == [], body
    assert body["totals"] == {"files": 0, "added": 0, "removed": 0, "commits": 0}, body
    assert body["lane"] == lane["lane"], body
    assert body["branch"] == "ticket/TCK-001", body

    # ...and a lane that does not exist is the 404 the convention asks for.
    r = env.srv.get("/api/lanes/lane/99/diff")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found", r.text

    env.unchanged()


# ── the read-only promise, enforced rather than described ────────────────────────────────────

def test_the_diff_is_read_only_git(cmdrepo):
    """Every git subcommand the diff runs is in the declared set, and every invocation carries
    `--no-optional-locks`.

    Without that flag `git status` refreshes the index — a write into a worktree another session
    is working in. The set is asserted against a written-down list, and the two index files are
    asserted byte-identical, because "read-only" is a claim about what happened on disk.
    """
    env = cmdrepo.build(workspaces=workspace(gate="./gate.sh"))
    lane = env.claim()
    wt = Path(lane["worktree"])

    (wt / "notes.txt").write_text("one\ntwo\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "lane work")

    gitdir = Path((wt / ".git").read_text().split("gitdir:")[1].strip())
    indices = [p for p in (gitdir / "index", Path(env.root) / ".git" / "index") if p.exists()]
    assert indices, "no index file to watch"
    before = {p: p.read_bytes() for p in indices}

    for _ in range(3):
        assert env.srv.get(lane_path(lane["lane"]) + "/diff").status_code == 200
    assert env.srv.get(lane_path(lane["lane"]) + "/diff",
                       params={"path": "notes.txt"}).status_code == 200

    for p in indices:
        assert p.read_bytes() == before[p], f"{p} was rewritten by a read-only operation"

    log = env.srv.log.read_text()

    allows, used = set(), set()
    invocations = 0
    for line in log.splitlines():
        if "lane: diff allows " in line:
            head = line.index("lane: diff allows ")
            rest = line[head + len("lane: diff allows "):]
            allowed, ran = rest.split(" used ", 1)
            allows.update(json.loads(allowed))
            used.update(json.loads(ran))
        marker = "lane: diff git "
        if marker in line:
            invocations += 1
            argv = line[line.index(marker) + len(marker):].split()
            assert argv[0] == "--no-optional-locks", (
                f"a git invocation without --no-optional-locks: {line}")
            assert argv[1] in DIFF_GIT, f"a subcommand outside the allowlist ran: {line}"

    assert invocations, f"the diff never reported which git it ran:\n{log[-3000:]}"
    assert sorted(allows) == DIFF_GIT, f"the declared set is not the one in the report: {allows}"
    assert used, f"the diff never reported a subcommand it used:\n{log[-3000:]}"
    assert used <= set(DIFF_GIT), f"a subcommand outside the allowlist ran: {used}"

    env.unchanged()


# ── the mandatory negative ───────────────────────────────────────────────────────────────────

def test_a_command_that_never_exits_is_not_the_tool_s_problem(cmdrepo):
    """MANDATORY NEGATIVE — a declared command that hangs forever leaves the lane usable.

    A repository can declare any string it likes, and the one it declares wrong is the one that
    never returns. So four of them are started at once — `core::Engine` runs FOUR threads on one
    io_context, so if a single hanging command held its handler thread, four would hold every
    thread the server has and the whole surface would stop answering.

    Four things are asserted while all four are still hanging:
      · the endpoint already answered `202`;
      · the run is MARKED — a second command in the same lane is refused, naming the live one;
      · the output KEEPS streaming — later ticks arrive, not just the first;
      · nothing is wedged — health, the lane list and this ticket's own diff all still answer.
    """
    env = cmdrepo.build(workspaces=workspace(gate="./hang.sh", build="./build.sh"),
                        lanes_host=6,
                        tickets=[f"TCK-{i:03d}" for i in range(1, 6)])
    lanes = [env.claim(ticket=f"TCK-{i:03d}") for i in range(1, 5)]
    sockets = [subscribed(env, l["lane"]) for l in lanes]

    def accept(name):
        """A `202` that never arrives is the failure this case exists for, said out loud."""
        try:
            return run_command(env, name, "gate")
        except Exception as exc:
            raise AssertionError(
                f"POST {lane_path(name)}/commands NEVER ANSWERED while the declared command was "
                f"running — the endpoint waited for the process: {type(exc).__name__}") from exc

    try:
        started = time.monotonic()
        answers = [accept(l["lane"]) for l in lanes]
        answered = time.monotonic() - started

        for r in answers:
            assert r.status_code == 202, r.text
        assert answered < 10, (
            f"four hanging commands took {answered:.1f}s to be accepted — a handler waited")

        # 1 — the output is really streaming, and keeps streaming.
        first = [ws.wait_for("command", timeout=20) for ws in sockets]
        for frame in first:
            SOCKET_VALIDATOR.validate(frame)
            assert "exit_code" not in frame or frame["exit_code"] is None, frame

        time.sleep(2.5)

        # 2 — with all four still hanging, the surface answers. Concurrently, and more requests
        # than the engine has threads, so a held thread cannot hide behind a fast one.
        def probe(path):
            began = time.monotonic()
            try:
                return path, env.srv.get(path).status_code, time.monotonic() - began
            except Exception as exc:                      # a wedged server is a timeout here
                return path, f"{type(exc).__name__}: {exc}", time.monotonic() - began

        paths = (["/api/health", "/api/lanes"] * 3
                 + [lane_path(l["lane"]) + "/diff" for l in lanes])
        with ThreadPoolExecutor(max_workers=len(paths)) as pool:
            results = list(pool.map(probe, paths))
        for path, status, took in results:
            assert status == 200, f"{path} answered {status} while commands were hanging"
            assert took < 10, f"{path} took {took:.1f}s while commands were hanging"

        # 3 — the run is marked: a second command in a lane that holds one is refused, and the
        # refusal names the command that is still running.
        second = run_command(env, lanes[0]["lane"], "build")
        assert second.status_code == 409, second.text
        err = second.json()["error"]
        assert err["code"] == "wrong_state", err
        assert err["detail"]["command_id"] == answers[0].json()["command_id"], err

        # 4 — and the output is STILL arriving, after all of that.
        ticks = []
        deadline = time.time() + 20
        while time.time() < deadline and len(ticks) < 2:
            frame = sockets[0].wait_for("command", timeout=10)
            SOCKET_VALIDATOR.validate(frame)
            if "tick" in (frame.get("chunk") or ""):
                ticks.append(frame)
        assert len(ticks) >= 2, f"the output stopped streaming: {ticks}"
    finally:
        for ws in sockets:
            ws.close()

    # The lane is usable to the end: a forced release kills the wedged command and takes the
    # lane back. A lane nobody can release is a lane that has to be fixed with a reboot.
    for l in lanes:
        r = env.srv.delete(lane_path(l["lane"]) + "?force=true")
        assert r.status_code == 200, r.text
        assert r.json()["killed"] is True, r.text

    assert env.srv.get("/api/lanes").json()["lanes"] == []
    env.unchanged()


# ── SRV-FEAT-500 · REVIEW is a command the tool runs ─────────────────────────────────────────

def test_review_runs_and_replays_like_build(cmdrepo):
    """Criterion 2 — `review` starts EXACTLY as `build` starts, and so does `gate`.

    The three names are not typed here. They are asked of the product — E19's own refusal for a
    name outside the set carries `detail.declared` — and cross-checked against the command keys
    `manifest.schema.json` declares, so a fourth command joins this sweep the day it is declared
    and a set that quietly shrank fails instead of passing faster (`SRV-FIX-351`).

    Every arm runs the same assertions, so nothing here is true of `review` and untrue of the two
    that already worked:

      · `202`, and the response carries the string THAT key declares, not another key's;
      · `command` frames on the LANE scope, every one of them naming the key;
      · the exit status in the last frame;
      · and the frames a client that went away and came back receives — a second connection,
        subscribed while the command is still producing, gets the tail and the exit code. A route
        is not a state (`WEB-FIX-051`): the journey is walked with `command` frames on the wire
        and the case says so.
    """
    env = cmdrepo.build(workspaces=workspace(build="./say.sh build", gate="./say.sh gate",
                                             review="./say.sh review"))
    lane = env.claim()

    refused = run_command(env, lane["lane"], "deploy")
    assert refused.status_code == 400, refused.text
    names = refused.json()["error"]["detail"]["declared"]
    assert names == declared_command_keys_500(), (
        f"the endpoint declares {names} and the manifest schema declares "
        f"{declared_command_keys_500()}. One of them is the set a person edits and the other is "
        f"the set the tool enforces, and a sweep over either alone proves nothing about the "
        f"other.")
    assert {"build", "gate", "review"} <= set(names), (
        f"the sweep below would run over {names}, which does not name all three: SRV-FEAT-500 "
        f"is about the third one")
    assert len(names) >= 3, (
        f"the sweep below has {len(names)} member(s): {names}. A floor, not an equality — the "
        f"docstring above promises a fourth command joins this case the day it is declared, and "
        f"an equality would turn that promise into a red run.")

    for which in names:
        declared = f"./say.sh {which}"

        ws = subscribed(env, lane["lane"])
        try:
            r = run_command(env, lane["lane"], which)
            assert r.status_code == 202, f"{which} was refused: {r.text}"
            body = r.json()
            assert body["command"] == declared, (
                f"{which} ran {body['command']!r}, which is not the string its own key "
                f"declares: {declared!r}")
            assert body["which"] == which, body
            assert body["lane"] == lane["lane"], body
            assert body["command_id"], body

            first = ws.wait_for("command", timeout=20)
            SOCKET_VALIDATOR.validate(first)
            assert first["which"] == which, first
            assert first["lane"] == lane["lane"], first
            assert first["command_id"] == body["command_id"], first
            # WHAT MUST BE ON THE WIRE WHILE THE JOURNEY IS WALKED. `WEB-FIX-051` asserted an
            # away-and-back over a lane that never drew the kind it was about, and 148/148 was
            # green over a defect that destroyed every command panel. So: the first client is
            # holding a CHUNK and NOT an exit, which is the only state in which "went away
            # mid-command" means anything. A remount after the exit replays nothing and passes
            # faster.
            assert first.get("exit_code") is None, (
                f"{which} was already over when the first client left, so the remount below is "
                f"not a mid-command journey at all: {first}")
            assert first.get("chunk"), (
                f"the first client held no output when it went away: {first}")
            seen_before = first["chunk"]
        finally:
            ws.close()

        # ...and now the client is gone. A new connection subscribes to the same lane while the
        # command is still running and must receive the rest of it, exit code included.
        remounted = subscribed(env, lane["lane"])
        try:
            frames = collect(remounted)
        finally:
            remounted.close()

        assert all(f["which"] == which for f in frames), frames
        assert all(f["command_id"] == body["command_id"] for f in frames), frames

        tail = output_of(frames)
        assert "tick" in tail, (
            f"the client that came back received no output at all for {which}, only an exit: "
            f"{frames}")
        assert f"done: {which}" in tail, (
            f"the remounted client missed the end of {which}: {tail!r}")
        assert len([f for f in frames if f.get("chunk")]) >= 1, frames
        assert tail != seen_before, (
            f"the remounted client received exactly what the first one already had, so nothing "
            f"was still being produced when it subscribed: {tail!r}")

        last = frames[-1]
        assert last["exit_code"] == 0, last
        assert not last.get("chunk"), "the exit frame is its own frame"

    env.unchanged()


def test_an_unknown_command_and_an_undeclared_review_are_refused(cmdrepo):
    """Criterion 3, the mandatory negative — three refusals and one non-verdict.

    1. a name outside the declared set is refused, and the refusal LISTS the three;
    2. `review` on a workspace that declares none names the workspace and the manifest key a
       person would have to write — the same refusal `gate` gets, from the same code path, so
       there is no review-shaped special case to keep in step;
    3. a review that exits non-zero is reported as its exit code and as nothing else. The tool
       has no opinion (SPEC.md §10), and the skill that reads this number is required to call it
       a failed review rather than a review with no findings.
    """
    env = cmdrepo.build(workspaces=workspace(build="./build.sh"))
    lane = env.claim()

    # 1 — a name that is not one of the three.
    r = run_command(env, lane["lane"], "deploy")
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["detail"]["declared"] == declared_command_keys_500(), err
    assert "review" in err["detail"]["declared"], (
        f"the refusal for an unknown command does not list `review`, so nothing tells a person "
        f"the third name exists: {err}")

    # 2 — `review` where none is declared, beside `gate` where none is declared: one shape.
    undeclared = {}
    for which in ("review", "gate"):
        rr = run_command(env, lane["lane"], which)
        assert rr.status_code == 404, f"{which}: {rr.text}"
        e = rr.json()["error"]
        assert e["detail"]["field"] == f"workspaces.core.{which}", e
        assert e["detail"]["workspace"] == "core", e
        assert e["detail"]["path"].endswith("factory.json"), e
        undeclared[which] = (rr.status_code, e["code"], e["message"].replace(which, "<which>"),
                             e["detail"]["field"].replace(which, "<which>"))
    assert len(set(undeclared.values())) == 1, (
        f"an undeclared `review` is refused differently from an undeclared `gate`: {undeclared}\n"
        f"Two refusals that differ in wording are two implementations, and the next one is the "
        f"one nobody wrote.")

    # 3 — a review that exits non-zero. Reported, never interpreted.
    env2 = cmdrepo.build(workspaces=workspace(build="./build.sh", review="./exit7.sh"))
    lane2 = env2.claim()
    ws = subscribed(env2, lane2["lane"])
    try:
        rr = run_command(env2, lane2["lane"], "review")
        assert rr.status_code == 202, f"a review that will exit 7 was refused up front: {rr.text}"
        frames = collect(ws)
    finally:
        ws.close()

    last = frames[-1]
    assert last["exit_code"] == 7, f"the real exit code of a review was not reported: {last}"
    assert last["which"] == "review", last
    verdict = {"ok", "failed", "passed", "success", "error", "status", "findings"}
    for f in frames:
        assert not (verdict & set(f)), f"the tool formed an opinion about a review: {f}"

    # ...and the skill that reads that number says what it means. A non-zero review is a review
    # that failed, and a report saying "no findings" over a `127` is the defect this clause names.
    skill = (Path(__file__).resolve().parents[2] / ".claude" / "skills" / "run-ticket"
             / "SKILL.md").read_text()
    assert "review failed" in skill, (
        "run-ticket/SKILL.md never says what a non-zero review means, so the report it writes "
        "is free to read a crashed review as a clean one")

    env2.unchanged()

    # 4 — and THE REAL SCRIPT, in a real lane, through the real endpoint, with no reviewer to
    # run. `sh .claude/scripts/review.sh` resolves — `sh` is under /usr — so the load-time check
    # says nothing, and a lane's PATH does not carry `claude` unless the workspace declared where
    # it is. Found by driving this ticket by hand on 2026-09-11: the script swallowed the
    # reviewer's failure and printed nothing at exit 0, which is byte-identical to a clean
    # review. `SRV-FIX-221` is the same shape one level up — a command whose PROGRAM resolves and
    # whose RUN cannot.
    here = Path(__file__).resolve().parents[2]
    outcomes = {}
    # `4` and not `3`: `3` is `review.sh`'s OWN code for an answer it could not read, and an arm
    # whose reviewer exits with it cannot tell the two rows of REVIEW.md §4's table apart.
    for arm, rc, prints in (("failed", 4, ""),
                            ("unreadable", 0, 'echo "I read the diff and it looks fine to me."\n'),
                            ("clean", 0, 'echo "minor a.cpp:1 nothing much"\n'
                                         'echo "blocker b.cpp:2 something real"\n')):
        # The reviewer is COMMITTED INTO THE REPOSITORY and put on the command's own PATH by the
        # declared string, so what this case measures is the script's own distinction and not
        # whether this machine happens to have `claude` anywhere. Round 1 asserted the failing
        # arm against the ambient absence of the CLI, which is a case that reports its
        # environment and flips the day `SRV-FIX-505` lands.
        env3 = cmdrepo.build(workspaces=workspace(
            build="./build.sh", review="PATH=./bin:$PATH sh .claude/scripts/review.sh"))
        for rel in (".claude/scripts/review.sh", "docs/technical/workflow/REVIEW.md"):
            dest = env3.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((here / rel).read_bytes())
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
        stand_in = env3.root / "bin" / "claude"
        stand_in.parent.mkdir(parents=True, exist_ok=True)
        stand_in.write_text(f"#!/bin/sh\ncat > /dev/null\n{prints}exit {rc}\n")
        stand_in.chmod(0o755)
        (env3.root / "reviewed.txt").write_text("a line the branch changed\n")
        git(env3.root, "add", "-A")
        git(env3.root, "commit", "-m", "the review script and its reviewer")
        lane3 = env3.claim()

        wt = Path(lane3["worktree"])
        (wt / "reviewed.txt").write_text("a line the branch changed\nand another\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-m", "TCK-001: something to review")

        ws = subscribed(env3, lane3["lane"])
        try:
            rr = run_command(env3, lane3["lane"], "review")
            assert rr.status_code == 202, rr.text
            frames = collect(ws, timeout=180)
        finally:
            ws.close()
        outcomes[arm] = (frames[-1]["exit_code"], output_of(frames))
        env3.unchanged()

    unread_code_expected = 3
    failed_code, failed_said = outcomes["failed"]
    clean_code, clean_said = outcomes["clean"]

    # `docs/technical/workflow/REVIEW.md` §4: the OUTCOME is a word, because the reviewer is an arbitrary program and
    # may exit with any number this script might have wanted for itself. So the three arms are
    # required to name three DIFFERENT outcomes, and that is what a person and a report read.
    said_outcome = {}
    for arm, (_, text) in outcomes.items():
        words = [l.split("outcome=", 1)[1].strip()
                 for l in text.splitlines() if "review: outcome=" in l]
        assert len(words) == 1, (
            f"the {arm} arm named {words} outcomes; exactly one `review: outcome=` line is the "
            f"whole of how a report tells these apart:\n{text!r}")
        said_outcome[arm] = words[0]
    assert len(set(said_outcome.values())) == len(said_outcome), (
        f"two arms reported the same outcome: {said_outcome}. A reviewer that failed, one whose "
        f"answer could not be read and one that ran are three facts, and §4's table exists "
        f"because they are indistinguishable by exit code alone.")
    assert said_outcome["clean"] == "ran", said_outcome

    assert failed_code == 4, (
        f"the reviewer exited 4 and the command reported {failed_code} with this on the wire: "
        f"{failed_said!r}\nAn exit of 0 and no findings is what a CLEAN review looks like, so a "
        f"review that could not run would be reported as a review that found nothing; and the "
        f"number has to be the REVIEWER'S OWN, because §10 says the tool reports it and does not "
        f"interpret it.")
    assert clean_code == 0, (
        f"a reviewer that ran and answered made the command exit {clean_code}: findings do not "
        f"approve or block, so a review that RAN is an exit 0 whatever it found: {clean_said!r}")
    assert clean_said.index("blocker b.cpp:2") < clean_said.index("minor a.cpp:1"), (
        f"the findings did not reach the wire blocker-first: {clean_said!r}")

    unread_code, unread_said = outcomes["unreadable"]
    assert unread_code == unread_code_expected, (
        f"a reviewer that answered in prose made review.sh exit {unread_code}, not "
        f"{unread_code_expected}: REVIEW.md §4's table gives that case its own code so a person "
        f"reading a frame can tell it from a reviewer that never ran ({failed_code})")
    assert unread_code != 0, (
        f"a reviewer that answered in prose and named no finding made the command exit "
        f"{unread_code} with {unread_said!r} — which is what a clean review looks like. An "
        f"answer nothing could be made of is not an answer of nothing.")
    assert "looks fine to me" in unread_said, (
        f"the unreadable answer was dropped instead of shown: {unread_said!r}")


# ── SRV-FIX-532 cluster E3 — a lane runs the build its ticket names ───────────────────────────
#
# `SRV-FIX-456` and `SRV-FIX-517`. `docs/technical/contracts/http.schema.json` and `docs/technical/contracts/socket.schema.json` are read
# BY the client and changed BY server tickets, and `test_schema_consumers.py` exists to force the
# consuming workspace's build and gate the moment a `factory-server` ticket edits one. From a lane
# it could not: the toolchain that workspace declares was not bound, so the check a schema change
# is supposed to pay for was a check no lane could run.
#
# THE DECISION, pre-decided by §1 and not re-opened here: **a ticket's `workspaces:` frontmatter
# line names the extra workspaces whose toolchain the lane binds.** Absent means the ticket's own
# workspace only. The manifest gains nothing.

TWO_WORKSPACE_SCRIPTS = {
    "mine.sh": "#!/bin/sh\necho mine-ran\n",
    "theirs.sh": "#!/bin/sh\nexec \"$CONSUMER_TOOL/bin/consume\"\n",
}


def two_workspaces(consumer_toolchain):
    return {
        "core": {"path": ".", "build": "sh mine.sh"},
        "consumer": {"path": ".", "build": "sh theirs.sh",
                     "toolchain": [str(consumer_toolchain)]},
    }


def a_toolchain_at(where):
    """A directory holding one executable, standing in for `~/.nvm` — a real thing on disk."""
    (where / "bin").mkdir(parents=True, exist_ok=True)
    tool = where / "bin" / "consume"
    tool.write_text("#!/bin/sh\necho consumer-toolchain-ran\n")
    tool.chmod(0o755)
    return where


def ticket_naming(root, tid, workspaces):
    """A ticket document whose frontmatter names extra workspaces — or does not."""
    front = [f"id: {tid}", "parent: REQ-001", "workspace: core",
             f"title: ticket {tid}", "status: todo"]
    if workspaces is not None:
        front.append(f"workspaces: {workspaces}")
    (root / "docs" / "tickets" / f"{tid}.md").write_text(
        "---\n" + "\n".join(front) + "\n---\n\nbody\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", f"file {tid}")


def ran_in_lane(env, lane, command):
    """Run a command INSIDE the lane and answer what it printed — the real sandbox, not an argv.

    §1 widens no surface, so *a lane runs a second workspace's build* is asserted where it
    actually has to be true: a process in the lane, with the lane's own PATH and binds.
    """
    import time

    r = env.srv.post("/api/_test/lane-process",
                     json={"lane": lane["lane"], "command": command})
    assert r.status_code == 202, r.text
    out = Path(lane["worktree"]) / "ran.txt"
    deadline = time.time() + 25
    while time.time() < deadline:
        if out.is_file() and out.read_text().strip():
            return out.read_text()
        time.sleep(0.1)
    return out.read_text() if out.is_file() else ""


def test_a_lane_runs_a_second_workspaces_build(cmdrepo, tmp_path):
    """§1 E3 — a lane runs a second workspace's build when its ticket names it, and not otherwise.

    `SRV-FIX-456`. The consuming workspace's toolchain is a real directory holding a real
    executable and its declared build EXECs that executable, so what is asserted is the command
    RUNNING inside the sandbox — not a bind appearing in an argv.

    Two tickets in one world, because the claim being asserted is *which ticket*: one names the
    second workspace in `workspaces:` and one does not. A world where every ticket names it
    cannot tell the declaration from a bind everybody gets.
    """
    toolchain = a_toolchain_at(tmp_path / "consumer-toolchain")
    env = cmdrepo.build(workspaces=two_workspaces(toolchain), tickets=("TCK-NAMES", "TCK-SILENT"))
    ticket_naming(env.root, "TCK-NAMES", "core consumer")
    ticket_naming(env.root, "TCK-SILENT", None)

    build = None
    for name, spec in two_workspaces(toolchain).items():
        if name == "consumer":
            build = spec["build"]
    assert build, "the consuming workspace declares no build, so this case is about nothing"
    command = f"{toolchain}/bin/consume > ran.txt 2>&1"

    named = env.claim("TCK-NAMES", workspace="core")
    said = ran_in_lane(env, named, command)
    assert "consumer-toolchain-ran" in said, (
        f"a lane whose ticket names `consumer` could not run that workspace's own toolchain. "
        f"That is the check a schema change is supposed to pay for:\n{said!r}")

    silent = env.claim("TCK-SILENT", workspace="core")
    said = ran_in_lane(env, silent, command)
    assert "consumer-toolchain-ran" not in said, (
        f"a lane whose ticket names NO extra workspace was given the consuming toolchain anyway, "
        f"so `workspaces:` declares nothing and every lane gets everything:\n{said!r}")
