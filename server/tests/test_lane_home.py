"""SRV-FIX-052 — a lane builds its own agent home.

`SRV-FEAT-039` pointed `CLAUDE_CONFIG_DIR` at the developer's `~/.claude` and `confine.cpp` binds
every declared agent path `--ro-bind`. So the directory the CLI resolves ALL of its state from —
credentials, trust, and the per-session scratch directory it needs in order to start a shell — was
the developer's home, mounted read-only, and every `Bash` call in a lane came back:

    EROFS: read-only file system, mkdir '/home/dev/.claude/session-env/<session>'

Reproduced before any edit and committed at
`docs/reports/SRV-FIX-052/reproduce.out` (`reproduce.py` re-runs it).

The lane stops borrowing a home and builds one: a directory per lane, outside the repository and
outside the developer's home, bound WRITABLE, carrying a trust file the tool generates and the
credential file alone — hard-linked when one filesystem allows it, copied `0600` when it does not.

`docs/tickets/_TEMPLATE.md` §2 — *A STAND-IN IS JUDGED AGAINST THE REAL THING* — applies: the
subject is an external program's use of its own configuration directory, so what the real CLI
writes there is measured and committed (`python3 tests/cli_home.py` →
`fixtures/cli-home-2026-07-30-*.json`) rather than reasoned about.
"""

import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

import pytest

from test_lane import build_repo, git, status_of                # noqa: F401
from test_isolation import run_in_lane

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = "cli-home-2026-07-30-"

REAL_AGENT = shutil.which("claude")
REAL_CONFIG = Path.home() / ".claude"
REAL_TOKEN = REAL_CONFIG / ".credentials.json"


def developers_home():
    """The home the PRODUCT means by *the developer's*, which is not always `Path.home()`.

    SRV-FIX-532 §1 E — the suite has to be runnable from inside a lane, and a lane's `HOME` is the
    directory the tool built FOR it. Reading `Path.home()` there asks whether a lane may write
    inside a lane, which is a different question and one the guard below is right to answer `yes`
    to. The tool resolves the real account's home from the passwd database in exactly that case
    (`lane/confine.cpp::developerHomeImpl`), so the assertion has to ask the same way or it is
    asserting about the environment it happens to run in.
    """
    home = os.environ.get("HOME", "")
    lane = os.environ.get("FACTORY_LANE_HOME", "")
    if home and (home == "/home/lane" or (lane and home == lane)):
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return Path.home()


def skipped(reason):
    """A skip is a RED check here, never a silent pass — `docs/operations/ESCAPES.md`, the row about a
    guarded section that reads as `n/n passed` without ever running."""
    pytest.fail(f"SKIPPED, AND THEREFORE RED: {reason}")


def requires_real_agent():
    if os.environ.get("FACTORY_REAL_CLI") == "0":
        pytest.skip("FACTORY_REAL_CLI=0 opts out of authenticated live Claude (release tier)")
    if not REAL_AGENT:
        skipped("no `claude` on PATH — this ticket's subject is that program's behaviour")
    if not REAL_TOKEN.is_file():
        skipped(f"no credentials at {REAL_TOKEN}; a lane cannot authenticate")


def capture(name):
    path = (FIXTURES / f"{CAPTURE}{name}").with_suffix(".json")
    if not path.is_file():
        skipped(f"the committed capture {path.name} is missing; re-run "
                f"`python3 tests/cli_home.py` from factory/server")
    return json.loads(path.read_text())


def digest(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


# A stand-in agent, for the cases whose subject is what the TOOL put in the directory rather than
# what the CLI does with it. It records the variable it was given and everything it can see under
# it, which is exactly the evidence those cases need and costs nothing.
STAND_IN_AGENT = """#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "config_dir=$CLAUDE_CONFIG_DIR" >> "$log"
ls -a "$CLAUDE_CONFIG_DIR" >> "$log" 2>&1
echo "ready" >> "$log"
sleep 3
"""


@pytest.fixture
def homeenv(tmp_path, serve):
    """A repository, a server, and a place the tool is told to keep its lanes' agent homes.

    `FACTORY_AGENT_CONFIG_ROOT` is a LOCATION lever and never a permission one: the tool refuses
    to build a lane's directory anywhere under the developer's home whatever this says, which is
    what `test_the_lane_cannot_write_the_developers_config` reads back.
    """
    made = []
    base = tmp_path / "agent-homes"

    class Env:
        base = None

        # A NEW handle per build, never `self`.
        #
        # The sibling fixtures this was modelled on return `self` and mutate it, which is
        # harmless while a case builds one repository. It is not harmless here: this ticket
        # needs two servers alive at once and a third started after one of them is killed, and
        # against a self-returning factory `killed`, `live` and `next` are one object — so
        # `killed.srv.proc.kill()` killed whichever server was built LAST. It cost an hour and
        # produced a failure that reads exactly like the sweep working backwards.
        def build(self, *, settings=None, agent=None, config_root=None, script=None,
                  HOME=..., **kw):
            root = build_repo(tmp_path / f"repo{len(made)}", agent=agent, **kw)
            if script is not None:
                (root / "agent.sh").write_text(script)
                git(root, "add", "agent.sh")
                git(root, "commit", "-m", "agent")
            if settings is not None:
                (root / ".claude").mkdir(exist_ok=True)
                (root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
                git(root, "add", ".claude/settings.json")
                git(root, "commit", "-m", "settings")
            baseline = status_of(root)
            made.append((root, baseline))
            handle = Env()
            handle.root = root
            handle.real = Path(os.path.realpath(root))
            handle.baseline = baseline
            handle.base = Path(config_root) if config_root else base
            extra = {} if HOME is ... else {"HOME": HOME}    # None ⇒ unset, "" ⇒ empty
            handle.srv = serve(root, FACTORY_TEST_HOOKS="1",
                               FACTORY_AGENT_CONFIG_ROOT=str(handle.base), **extra)
            return handle

        def claim(self, ticket="TCK-001", workspace="core"):
            r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})
            assert r.status_code == 201, r.text
            return r.json()

        def start(self, lane, **body):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)
            assert r.status_code == 201, r.text
            return r.json()

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
            """The host directory this lane's agent configuration lives in.

            Found rather than recomputed: the tool derives the middle component from the
            repository it was pointed at, and a test that reimplements that derivation is a test
            that passes when the tool changes it.
            """
            # Scoped by THIS env's repository, because two servers may share one location and
            # the tool derives the middle component from the repository it was pointed at.
            hits = sorted(self.base.glob(
                f"factory-agent-*/{Path(self.root).name}-*/{lane['lane']}"))
            assert len(hits) == 1, (
                f"expected exactly one agent home for {lane['lane']} under {self.base}, "
                f"found {hits}\ntree: {sorted(str(p) for p in self.base.rglob('*'))}")
            return hits[0]

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()
    for root, before in made:
        assert status_of(root) == before, f"the main checkout at {root} changed"


def entries_of(root, run_id, timeout=300, until=None):
    path = Path(root) / ".factory" / "runs" / run_id / "transcript.jsonl"
    deadline = time.time() + timeout
    entries = []
    while time.time() < deadline:
        try:
            entries = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        except OSError:
            entries = []
        if entries and (until is None or until(entries)):
            return entries
        time.sleep(0.2)
    return entries


# A stand-in developer home, and it may NOT be under /tmp.
#
# `confine.cpp` binds the declared agent paths and mounts a tmpfs over `/tmp` AFTERWARDS, so a
# bind whose destination is under /tmp is SHADOWED: every read of it fails, and it fails for a
# reason that has nothing to do with this ticket. A case built under `tmp_path` would therefore
# pass against the very patch the mandatory negative exists to kill — measured, and the reason
# this constant is here. `~/.cache` is the one writable place outside /tmp that behaves the way a
# developer's own `~/.claude` does, and every byte written under it is removed on teardown.
CACHE = Path.home() / ".cache" / "factory-har052"


@pytest.fixture
def devhome():
    """A stand-in for a developer's home: the files the CLI keeps there, and a token.

    A stand-in rather than the real `$HOME`, for the cases that write: an assertion that proves a
    write is refused must not corrupt the developer's own configuration on the day it regresses.
    The real `$HOME` is asserted about too, with `test -w`, which changes nothing.
    """
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    made = []

    def build():
        home = Path(tempfile.mkdtemp(prefix="devhome-", dir=CACHE))
        made.append(home)
        (home / ".claude").mkdir(parents=True, exist_ok=True)
        (home / ".claude.json").write_text(json.dumps({"projects": {}}, indent=2))
        (home / ".claude" / ".claude.json").write_text(json.dumps({"projects": {}}, indent=2))
        token = home / ".claude" / ".credentials.json"
        token.write_text(json.dumps({"token": "stand-in"}))
        token.chmod(0o600)
        (home / ".claude" / "mcp.json").write_text("{}")
        return home

    yield build
    for home in made:
        shutil.rmtree(home, ignore_errors=True)


# ── 1 · THE criterion ────────────────────────────────────────────────────────────────────────

def test_a_lane_can_run_a_command(homeenv, tmp_path):
    """A real agent, in a real lane, runs a shell command and comes back with its output.

    This is the criterion. It is red today with the exact `EROFS` above, and everything else in
    this ticket exists so that it stays green. Nothing is hand-edited to make it pass: the
    repository declares a credential directory and nothing else, and the trust the CLI reads is
    the one the tool generated for the root the lane is about.
    """
    requires_real_agent()
    nonce = "har052-ran-" + os.urandom(5).hex()
    before = digest(REAL_CONFIG / ".claude.json")

    env = homeenv.build(
        settings={"permissions": {"allow": [f"Bash(echo {nonce})"]}},
        agent={"command": "claude", "credentials_dir": str(REAL_CONFIG)})
    assert env.srv.get("/api/manifest").status_code == 200, env.srv.get("/api/manifest").text

    lane = env.claim()
    r = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={
        "prompt": f"Use the Bash tool to run exactly this command: echo {nonce}\n"
                  f"Then reply with the one line it printed."})
    assert r.status_code == 201, r.text
    run = r.json()

    entries = entries_of(env.root, run["run_id"],
                         until=lambda es: any(e.get("kind") == "turn" for e in es))
    blob = json.dumps(entries)

    erofs = [e for e in entries if "EROFS" in json.dumps(e)]
    assert not erofs, (
        "the agent's shell could not start, which is the defect this ticket is:\n"
        + "\n".join(json.dumps(e)[:400] for e in erofs))

    ran = [e for e in entries
           if e.get("kind") == "tool" and nonce in str(e.get("result", ""))]
    assert ran, (
        f"no tool RESULT in the transcript carries {nonce}, so the command never ran — a lane "
        f"that cannot run a command cannot build, test or gate anything.\n"
        f"kinds: {[e.get('kind') for e in entries]}\n{blob[:3000]}\n"
        f"server log tail:\n{env.srv.tail()}")

    # ...and the developer's own configuration was not touched to achieve it.
    assert digest(REAL_CONFIG / ".claude.json") == before, (
        f"{REAL_CONFIG / '.claude.json'} was modified by a run")

    env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={})
    env.unchanged()


# ── 2 · what a lane can no longer reach ──────────────────────────────────────────────────────

def test_the_developers_config_directory_is_not_bound_into_the_lane(homeenv, devhome):
    """The strongest statement this ticket can make is about what a lane can no longer reach.

    The mount table is read out of a real confined process — a description of the binds is what
    `SRV-FEAT-039` had, and it was accurate about the one path it was aimed at.
    """
    home = devhome()
    env = homeenv.build(agent={"command": "claude", "credentials_dir": str(home / ".claude")})
    lane = env.claim()

    rc, mounts = run_in_lane(env, lane, "cat /proc/self/mountinfo")
    assert rc == 0, mounts
    offenders = [l for l in mounts.splitlines()
                 if str(home / ".claude") in l or str(REAL_CONFIG) in l]
    assert not offenders, (
        "the lane's mount table still carries a branch of the developer's configuration "
        "directory:\n  " + "\n  ".join(offenders))

    # ...and it is ABSENT, not merely denied.
    for target in (home / ".claude" / ".claude.json",
                   home / ".claude" / ".credentials.json",
                   home / ".claude" / "mcp.json",
                   REAL_CONFIG / ".claude.json"):
        rc, out = run_in_lane(env, lane, f"cat {target}")
        assert rc != 0, f"the lane read {target}: {out!r}"
        assert "stand-in" not in out, f"the lane read the token at {target}: {out!r}"

    env.unchanged()


# ── 3 · the trust is the tool's, and it is in force ──────────────────────────────────────────

def test_the_trust_the_agent_reads_is_the_one_the_tool_generated(homeenv, tmp_path):
    """The tool generates the trust for the root it is about, in a file it owns.

    Three assertions, and the third is the one that cannot be reworded away: the file is the
    tool's and names this repository's root; the transcript carries no `Ignoring … has not been
    trusted` line; and a permission THE REPOSITORY DECLARED is in force, proven by the agent
    getting the output of a command no built-in rule would have allowed without a question.
    """
    requires_real_agent()
    nonce = "har052-trust-" + os.urandom(5).hex()
    before = digest(REAL_CONFIG / ".claude.json")

    env = homeenv.build(
        settings={"permissions": {"allow": [f"Bash(echo {nonce})"]}},
        agent={"command": "claude", "credentials_dir": str(REAL_CONFIG)})
    lane = env.claim()

    r = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={
        "prompt": f"Use the Bash tool to run exactly this command: echo {nonce}\n"
                  f"Then reply with the one line it printed."})
    assert r.status_code == 201, r.text
    run = r.json()
    entries = entries_of(env.root, run["run_id"],
                         until=lambda es: any(e.get("kind") == "turn" for e in es))

    home = env.home_of(lane)
    doc = json.loads((home / ".claude.json").read_text())
    assert doc.get("projects", {}).get(str(env.real), {}).get("hasTrustDialogAccepted") is True, (
        f"the config directory the lane runs against does not carry the trust the tool was "
        f"supposed to generate for {env.real}: {doc}")

    dropped = [e for e in entries if "has not been trusted" in json.dumps(e)]
    assert not dropped, (
        "the run opened by announcing that this repository's declarations were thrown away:\n"
        + "\n".join(json.dumps(e)[:300] for e in dropped))

    ran = [e for e in entries
           if e.get("kind") == "tool" and nonce in str(e.get("result", ""))]
    assert ran, (
        f"the permission this repository declared was not in force — the absence of a warning "
        f"is not evidence, the declared Bash rule RUNNING is.\n"
        f"{json.dumps(entries)[:3000]}\nserver log tail:\n{env.srv.tail()}")

    assert digest(REAL_CONFIG / ".claude.json") == before, "the developer's config was written"

    env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={})
    env.unchanged()


# ── 4 · the credential crosses the boundary as one file ──────────────────────────────────────

def test_the_credential_is_carried_without_being_copied_where_it_can_be(homeenv, devhome):
    """One file, by hard link when the filesystem allows one, `0600` when it does not.

    A hard link is a second name for the same inode with the same owner and mode: nothing is
    duplicated, the token's permissions travel with it, and there is no second copy to leave
    behind. A copy is the fallback across filesystems and it is made `0600` explicitly, because
    a token that arrives world-readable is worse than one that does not arrive.
    """
    home = devhome()
    token = home / ".claude" / ".credentials.json"
    env = homeenv.build(script=STAND_IN_AGENT,
                        agent={"command": "/bin/sh agent.sh",
                               "credentials_dir": str(home / ".claude")})
    lane = env.claim()

    # An agent RUN, because the credential travels with the thing that needs it: a declared build
    # or gate command asks for no configuration directory and is given no token.
    env.start(lane)
    log = env.wait_log(lane, "ready")
    assert "config_dir=/factory/agent-config" in log, (
        f"the agent was not told where its configuration directory is:\n{log}")
    assert ".credentials.json" in log, (
        f"the agent cannot see the credential from inside the lane:\n{log}")

    carried = env.home_of(lane) / ".credentials.json"
    assert carried.is_file(), f"no credential was carried into {env.home_of(lane)}"

    src, dst = token.stat(), carried.stat()
    mode = stat.S_IMODE(dst.st_mode)
    if src.st_dev == dst.st_dev:
        assert dst.st_ino == src.st_ino and dst.st_nlink >= 2, (
            f"the same filesystem holds both and the credential was COPIED anyway: "
            f"src ino {src.st_ino} dst ino {dst.st_ino} nlink {dst.st_nlink}")
    else:
        assert mode == 0o600, f"a copied credential is mode {oct(mode)}, not 0600"
    assert mode & 0o077 == 0, f"the carried credential is readable by others: {oct(mode)}"
    assert stat.S_IMODE(env.home_of(lane).stat().st_mode) == 0o700, (
        f"the lane's agent home is {oct(stat.S_IMODE(env.home_of(lane).stat().st_mode))}, "
        f"not 0700")

    # ...and only that file. The rest of the declared directory does not travel.
    carried_names = sorted(p.name for p in env.home_of(lane).iterdir())
    assert "mcp.json" not in carried_names, (
        f"more than the credential was carried out of the developer's directory: {carried_names}")

    env.unchanged()


# ── 5 · a lane's writes die with the lane ────────────────────────────────────────────────────

def test_a_lanes_writes_die_with_the_lane(homeenv, devhome):
    """What the CLI writes under its config directory is the lane's, and it is gone after it.

    Run twice, because "each lane has its own" is the assertion that matters: two lanes sharing
    one session directory is the same class of defect as one lane sharing the developer's.
    """
    home = devhome()
    env = homeenv.build(agent={"command": "claude", "credentials_dir": str(home / ".claude")})
    before = sorted(str(p) for p in Path.home().glob(".claude*"))

    one, two = env.claim(ticket="TCK-001"), env.claim(ticket="TCK-002")
    dirs = {}
    for lane in (one, two):
        rc, out = run_in_lane(
            env, lane,
            'echo "$HOME"; d=/factory/agent-config; ls -d "$d" && '
            f'echo {lane["lane"]!r} > "$d/session-probe"')
        assert rc == 0, (
            f"the lane could not write its own agent configuration directory: {out!r}")
        # The lane's own home — not the developer's, and not a directory that CONTAINS it. This
        # was the literal `/home/lane` until `SRV-FEAT-002` round 3, which stopped the
        # confinement synthesising a path the world it is created from already owns: inside a
        # lane the developer's home is itself under `/home/lane`, and a tmpfs mounted there
        # turns a write that must fail into one that succeeds somewhere harmless.
        lane_home = out.splitlines()[0]
        assert lane_home.startswith("/home/lane"), out
        assert lane_home != str(home) and not lane_home.startswith(str(home) + "/"), (
            f"the lane's home is the developer's own or inside it: {lane_home}")
        assert not str(home).startswith(lane_home.rstrip("/") + "/"), (
            f"the lane's home {lane_home} contains the developer's home {home}, so the tmpfs "
            f"over it hides that home instead of leaving it absent")
        dirs[lane["lane"]] = env.home_of(lane)
        assert (dirs[lane["lane"]] / "session-probe").is_file(), (
            f"what the lane wrote under its config directory did not land in "
            f"{dirs[lane['lane']]}: {sorted(p.name for p in dirs[lane['lane']].iterdir())}")

    assert dirs[one["lane"]] != dirs[two["lane"]], "two lanes share one agent home"
    for lane, other in ((one, two), (two, one)):
        rc, out = run_in_lane(env, lane, 'cat /factory/agent-config/session-probe')
        assert other["lane"] not in out, f"{lane['lane']} can see {other['lane']}'s session state"

    for lane in (one, two):
        r = env.srv.delete(f"/api/lanes/{lane['lane']}?force=true")
        assert r.status_code == 200, r.text
        assert not dirs[lane["lane"]].exists(), (
            f"the lane was released and its agent home survives at {dirs[lane['lane']]}")

    assert sorted(str(p) for p in Path.home().glob(".claude*")) == before, (
        "a lane's configuration reached the developer's home")
    env.unchanged()


# ── 6 · MANDATORY NEGATIVE — the cheap fix hands over the developer's machine ─────────────────
#
# The cheapest way to make `EROFS` go away is one word: make `confine.cpp`'s `--ro-bind` of the
# declared agent paths a `--bind`. It works, and it gives every lane a WRITE handle on the
# developer's credentials, trust store and MCP configuration. This case is what makes that
# unavailable; §1 pre-decided that the read-only bind is never made writable, "not as a fallback,
# not just for session-env".

def test_the_lane_cannot_write_the_developers_config(homeenv, devhome):
    """A write aimed at the developer's configuration fails, however the lane spells it.

    The destructive half runs against a STAND-IN home the manifest declares, so that the day this
    regresses the evidence is a red test and not a corrupted `~/.claude.json`. The real one is
    asserted about with `test -w`, which changes nothing.
    """
    home = devhome()
    env = homeenv.build(agent={"command": "claude", "credentials_dir": str(home / ".claude")})
    lane = env.claim()

    for target in (home / ".claude" / ".claude.json",
                   home / ".claude.json",
                   home / ".claude" / ".credentials.json"):
        keep = target.read_text()
        rc, out = run_in_lane(env, lane, f"echo pwned >> {target}")
        assert rc != 0, f"THE LANE WROTE {target}: {out!r}"
        assert target.read_text() == keep, f"{target} was modified by a lane"

    for target in (REAL_CONFIG / ".claude.json", Path.home() / ".claude.json",
                   REAL_CONFIG / ".credentials.json"):
        rc, out = run_in_lane(env, lane, f"test -w {target}")
        assert rc != 0, f"the lane holds a write handle on {target}"

    env.unchanged()


# ── 7 · the location is the tool's, and it is not the developer's home ───────────────────────

def test_a_lanes_home_is_outside_the_repository_and_outside_the_developers_home(homeenv,
                                                                                devhome):
    """Where the lane's own directory lives, asserted rather than described.

    Two places it may not be, and both have already cost this repository something: inside the
    repository, where it would appear in somebody's `git status` (the assertion every lane test
    ends with), and inside the developer's home, which is the whole of this ticket.
    """
    home = devhome()
    env = homeenv.build(agent={"command": "claude", "credentials_dir": str(home / ".claude")})
    lane = env.claim()
    rc, out = run_in_lane(env, lane, "true")
    assert rc == 0, out

    where = env.home_of(lane).resolve()
    assert not str(where).startswith(str(env.real)), f"{where} is inside the repository"
    for forbidden in {Path.home(), developers_home()}:
        assert not str(where).startswith(str(forbidden) + "/"), (
            f"{where} is inside {forbidden}")

    # ...and pointing the location lever INTO the developer's home does not make it one. A lever
    # that relocates is not a lever that relaxes: the lane is refused, and nothing is created.
    inside = developers_home() / ".cache" / "har052-should-never-be-used"
    shutil.rmtree(inside, ignore_errors=True)   # what matters is that THIS run creates none
    env2 = homeenv.build(agent={"command": "claude", "credentials_dir": str(home / ".claude")},
                         config_root=inside)
    lane2 = env2.claim()
    r = env2.srv.post("/api/_test/lane-process",
                      json={"lane": lane2["lane"], "command": "echo x"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "confinement_unavailable", r.text
    assert str(developers_home()) in r.json()["error"]["message"], r.json()["error"]["message"]
    assert not inside.exists(), f"a lane's directory was built at {inside}"
    env.unchanged()


def test_the_home_guard_holds_when_the_server_has_no_home(homeenv, devhome):
    """...and the guard above does not depend on the SERVER's environment.

    `HOME` is a hint, not a fact. A systemd system unit has none at all, and a guard written as
    `if (const char* home = getenv("HOME"))` does not run there — so the invariant would hold on
    the path somebody drove and be silently absent on the path nobody did, which is precisely the
    shape §3 of this ticket exists to punish. Measured: with `HOME` unset the tool built a lane's
    writable directory inside the developer's home and answered `202`.

    The answer is that the developer's home is resolved from the passwd database for the account
    this process actually runs as, and when even that cannot answer the lane is REFUSED rather
    than run against a guard that could not be evaluated.

    Both shapes, because they reach the code differently: unset, and set to the empty string.
    """
    home = devhome()
    inside = developers_home() / ".cache" / "har052-no-home-should-never-be-used"
    shutil.rmtree(inside, ignore_errors=True)

    for label, value in (("unset", None), ("empty", "")):
        env = homeenv.build(agent={"command": "claude",
                                   "credentials_dir": str(home / ".claude")},
                            config_root=inside, HOME=value)
        lane = env.claim()
        r = env.srv.post("/api/_test/lane-process",
                         json={"lane": lane["lane"], "command": "echo x"})
        assert r.status_code == 409, (
            f"[HOME {label}] a lane ran against a writable directory inside the developer's "
            f"home: {r.status_code} {r.text}")
        assert r.json()["error"]["code"] == "confinement_unavailable", r.text
        assert str(developers_home()) in r.json()["error"]["message"], r.json()["error"]["message"]
        assert not inside.exists(), (
            f"[HOME {label}] a lane's directory was built at {inside}")
    env.unchanged()


# ── 8 · what a killed server leaves behind ───────────────────────────────────────────────────

def test_a_lane_directory_left_by_a_killed_server_is_swept_by_the_next_one(homeenv, devhome,
                                                                          tmp_path):
    """A lane's writes die with the lane on EVERY exit, not only on a clean release.

    Release removes a lane's directory, and a server that is SIGKILLed never reaches one. The
    first sweep this ticket shipped removed only the killed server's OWN per-repository subtree
    at its next start — so a factory pointed at a different repository afterwards, or never
    started against that path again, left the directory for ever. Measured on this machine after
    one batch: 353 directories under `/tmp/factory-agent-1000` and **ten orphaned hard links to
    the live OAuth token**, under names derived from repositories that no longer exist.

    So the sweep is by OWNERSHIP, not by name: every subtree carries a lock its server holds open
    for its whole life, and a starting server removes each sibling whose lock it can take. A dead
    server holds no locks — the kernel drops them however it died — and a LIVE one holds its own,
    which is the other half of this case: two factories on two checkouts is how this repository
    runs parallel tickets, and the sweep must not touch the live one.
    """
    home = devhome()
    shared = tmp_path / "shared-agent-homes"
    agent = {"command": "claude", "credentials_dir": str(home / ".claude")}

    # A server that dies without releasing anything.
    killed = homeenv.build(agent=agent, config_root=shared)
    lane = killed.claim()
    rc, out = run_in_lane(killed, lane, "true")
    assert rc == 0, out
    orphan = killed.home_of(lane)
    assert orphan.is_dir(), orphan

    # ...and a LIVE server, on a third repository, whose directory must survive the sweep.
    live = homeenv.build(agent=agent, config_root=shared)
    live_lane = live.claim()
    rc, out = run_in_lane(live, live_lane, "true")
    assert rc == 0, out
    protected = live.home_of(live_lane)
    assert protected.is_dir(), protected

    killed.srv.proc.kill()
    killed.srv.proc.wait(timeout=10)
    assert orphan.is_dir(), (
        "the fixture did not reproduce the case: a killed server is supposed to leave its lane "
        "directory behind")

    # The next factory, pointed at a DIFFERENT repository, sharing only the location.
    nxt = homeenv.build(agent=agent, config_root=shared)
    assert nxt.srv.get("/api/health").status_code == 200

    deadline = time.time() + 10
    while time.time() < deadline and orphan.exists():
        time.sleep(0.05)
    assert not orphan.exists(), (
        f"a lane directory left by a killed server survived the next server's start: {orphan}\n"
        f"tree: {sorted(str(p) for p in shared.rglob('*'))[:40]}")
    assert protected.is_dir(), (
        f"the sweep removed a directory a LIVE server still owns: {protected}. Two factories on "
        f"two checkouts at once is how this repository runs parallel tickets")
    # `protected` is `<subtree>/lane/01`; the lock that saved it belongs to the subtree.
    assert (protected.parent.parent / ".owner.lock").is_file(), (
        f"the live server's lock is gone, so the next sweep will take its lanes: {protected}")

    killed.unchanged()


# ── 9 · SRV-FIX-174 — a credential is BORROWED, and borrowed state is re-carried ──────────────
#
# `furnish()` carried the credential in by hard link and skipped any name that was already there.
# A hard link is a second name for one inode, which is exactly right — for a file its writer
# updates IN PLACE. The agent CLI refreshes its OAuth token by atomic replace: write a temporary
# file, `rename` over the target. **A rename severs a hard link by definition**, so the link is a
# ONE-SHOT share: the first refresh on either side splits one credential into two files, each
# carrying its own refresh token, and OAuth rotates refresh tokens on use. Whichever side refreshes
# second is holding a superseded token, and because the skip made a new run inherit the dead one,
# kill-and-start could never fix it. `lane/03` answered fourteen hours of messages with
# `Failed to authenticate: OAuth session expired and could not be refreshed`.
#
# Every case below severs the link THE WAY ITS WRITER DOES — `os.rename` over the name — because
# that is the operation the whole defect turns on, and it is the one operation no assertion in
# `SRV-FEAT-039`, `SRV-FIX-052` or `test_agent_trust.py` ever performed. Time was never in the
# assertion set; a lane that authenticates at t=0 and dies at t=6h satisfied all of them.

APP = Path(__file__).resolve().parents[1] / "app"

# A stand-in agent that PROVES the process got the token, rather than the host directory looking
# right: it copies what it can read at `$CLAUDE_CONFIG_DIR` from INSIDE the sandbox, under its own
# run id, and appends — never truncates — so two runs' evidence cannot be confused for one.
CARRY_AGENT = """#!/bin/sh
log=./agent-log.txt
for f in "$CLAUDE_CONFIG_DIR"/.credentials.json "$CLAUDE_CONFIG_DIR"/*.borrowed; do
    [ -f "$f" ] || continue
    cp "$f" "./carried-$FACTORY_RUN_ID-$(basename "$f")" 2>/dev/null
done
echo "ran=$FACTORY_RUN_ID" >> "$log"
sleep 30
"""


def stand_in_home(where):
    """A developer's `~/.claude`, built where the CALLER says.

    `devhome` fixes its home under `CACHE` for the mandatory negative's sake — a stand-in the LANE
    has to be able to name. These cases never name it from inside a lane; what they need is control
    over WHICH FILESYSTEM holds it, because that is what decides between the hard link and the
    `0600` copy, and both are this ticket's subject.
    """
    home = Path(where)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({"projects": {}}, indent=2))
    return home


def write_token(token, payload):
    """Create the credential the first time — a plain write, as `claude login` leaves it."""
    token.write_text(json.dumps(payload))
    token.chmod(0o600)
    return token.stat()


def refresh_as_the_cli_does(token, payload):
    """Replace the host's credential EXACTLY the way the program that owns it replaces it.

    Write a temporary file beside the target and `rename` over it. This is one `os.rename`, and it
    is the whole defect: it severs every hard link to the old inode, so a name carried once now
    points at a file nobody will ever write to again.
    """
    tmp = token.with_name(token.name + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.chmod(0o600)
    os.rename(tmp, token)
    return token.stat()


def start_run(env, lane, timeout=30):
    """Start a run, waiting out a lane still holding the last one's process.

    `furnish()` runs once per process start and that is the moment under test, so each case starts
    a REAL run rather than asking an endpoint to re-furnish — there is no such endpoint, and a
    test that invented one would be measuring itself.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={})
        if r.status_code == 201:
            run = r.json()
            env.wait_log(lane, f"ran={run['run_id']}")
            return run
        last = r
        time.sleep(0.2)
    raise AssertionError(f"no run could be started in {lane['lane']}: "
                         f"{last.status_code if last else '-'} {last.text if last else ''}")


def end_run(env, lane):
    r = env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert r.status_code in (200, 202), r.text


def carried_in_the_lane(env, lane, run, name=".credentials.json"):
    """What the AGENT read at `$CLAUDE_CONFIG_DIR`, from inside its own sandbox."""
    path = Path(lane["worktree"]) / f"carried-{run['run_id']}-{name}"
    assert path.is_file(), (
        f"the agent could not read {name} inside the lane on run {run['run_id']}: "
        f"{sorted(p.name for p in Path(lane['worktree']).glob('carried-*'))}")
    return path.read_text()


def test_a_severed_credential_is_re_carried_on_the_next_run(homeenv, tmp_path):
    """THE criterion. The host's credential is replaced the way its writer replaces it, and the
    next run in the same lane starts on the CURRENT one.

    Both files are under `tmp_path`, so one filesystem holds both and the hard link is genuinely
    available — this case is about the link being a SNAPSHOT, not about a fallback.
    """
    home = stand_in_home(tmp_path / "devhome")
    token = home / ".claude" / ".credentials.json"
    first = write_token(token, {"refreshToken": "one", "expiresAt": 1})

    env = homeenv.build(script=CARRY_AGENT,
                        agent={"command": "/bin/sh agent.sh",
                               "credentials_dir": str(home / ".claude")})
    lane = env.claim()
    run1 = start_run(env, lane)
    carried = env.home_of(lane) / ".credentials.json"

    assert carried.stat().st_dev == token.stat().st_dev, (
        "this case needs one filesystem to hold both, or it measures the fallback instead")
    assert carried.stat().st_ino == first.st_ino, (
        f"the first carry did not link: host inode {first.st_ino}, lane inode "
        f"{carried.stat().st_ino}")
    end_run(env, lane)

    # The refresh. One `os.rename`, and the lane's name is now a second inode nothing will write.
    second = refresh_as_the_cli_does(token, {"refreshToken": "two", "expiresAt": 2})
    assert carried.stat().st_ino != second.st_ino, (
        "the fixture did not reproduce the defect: the rename left the lane's name on the host's "
        "inode, so there is nothing here to be stale")
    assert json.loads(carried.read_text())["refreshToken"] == "one", carried.read_text()

    run2 = start_run(env, lane)

    assert carried.stat().st_ino == token.stat().st_ino, (
        f"THE DEFECT: a new run in the same lane inherited the severed credential — lane inode "
        f"{carried.stat().st_ino}, host inode {token.stat().st_ino}. Nothing a person can do from "
        f"the product fixes this: kill-and-start lands here again.")
    assert carried.stat().st_nlink >= 2, (
        f"the re-carried credential is not a second NAME for the host's file: nlink "
        f"{carried.stat().st_nlink}")
    assert carried.read_text() == token.read_text(), (
        f"the lane's bytes are not the host's current bytes:\n  lane {carried.read_text()}\n"
        f"  host {token.read_text()}")

    # ...and the process itself read them, which is the statement the product is about.
    assert json.loads(carried_in_the_lane(env, lane, run2))["refreshToken"] == "two", (
        f"the agent in the lane read a superseded token: "
        f"{carried_in_the_lane(env, lane, run2)}")
    assert json.loads(carried_in_the_lane(env, lane, run1))["refreshToken"] == "one", (
        "the first run is supposed to have read the first token; if it did not, this case proves "
        "nothing about the second")

    end_run(env, lane)
    env.unchanged()


def test_the_generated_files_are_still_written_only_once(homeenv, tmp_path):
    """The cheap fix — re-write everything every run — throws away the lane's own state.

    The trust file is where the CLI keeps its project state, the hook and its `settings.json` are
    the permission channel a lane may be mid-session on, and the directory is the one the CLI
    writes into before it can start a shell. Seeded files are the LANE's; borrowed files are the
    developer's; only the second kind is re-carried.
    """
    home = stand_in_home(tmp_path / "devhome")
    token = home / ".claude" / ".credentials.json"
    write_token(token, {"refreshToken": "one"})

    env = homeenv.build(script=CARRY_AGENT,
                        agent={"command": "/bin/sh agent.sh",
                               "credentials_dir": str(home / ".claude"),
                               "permission_mode": "ask_all"})
    lane = env.claim()
    start_run(env, lane)
    where = env.home_of(lane)
    end_run(env, lane)

    seeded = {}
    for name in (".claude.json", "settings.json"):
        path = where / name
        assert path.is_file(), (
            f"{name} was never seeded, so this case cannot tell a survivor from an absence: "
            f"{sorted(p.name for p in where.iterdir())}")
        doc = json.loads(path.read_text())
        doc["har174Mark"] = f"written-by-the-lane-{name}"
        path.write_text(json.dumps(doc))
        seeded[name] = path.read_text()

    # ...and a refresh in the same window, so the run that must KEEP the marks is the same run
    # that must REPLACE the credential.
    refresh_as_the_cli_does(token, {"refreshToken": "two"})
    start_run(env, lane)

    for name, before in seeded.items():
        assert (where / name).read_text() == before, (
            f"a re-furnish threw away what the lane's own process wrote in {name}:\n"
            f"  before {before}\n  after  {(where / name).read_text()}")
    assert (where / ".credentials.json").read_text() == token.read_text(), (
        "the borrowed file was not re-carried in the same run that kept the seeded ones")

    end_run(env, lane)
    env.unchanged()


def second_filesystem(dev, tmp_path):
    """A writable directory on a filesystem that is NOT `dev`.

    Searched rather than named: `/tmp` and `$HOME` are one ext4 on the machine this defect was
    found on and three different tmpfs mounts inside a lane, so a hardcoded pair measures the copy
    fallback on one machine and the hard link on the next.
    """
    for candidate in (Path("/dev/shm"), Path.home() / ".cache", Path("/var/tmp"),
                      Path(tempfile.gettempdir())):
        try:
            if not candidate.is_dir() or candidate.stat().st_dev == dev:
                continue
            probe = Path(tempfile.mkdtemp(prefix="har174-", dir=candidate))
        except OSError:
            continue
        return probe
    return None


def test_a_credential_that_moves_filesystems_is_still_carried(homeenv, tmp_path):
    """The `0600` copy fallback lands the CURRENT bytes on a re-furnish, and stays `0600`.

    Every machine whose `/tmp` is a tmpfs takes this path on every run — the credential cannot be
    hard-linked onto a filesystem that does not hold it — so there the lane starts from a copy,
    which is the same defect one step earlier and needs the same answer.
    """
    base = tmp_path / "agent-homes"
    base.mkdir(parents=True, exist_ok=True)
    elsewhere = second_filesystem(base.stat().st_dev, tmp_path)
    if elsewhere is None:
        skipped("no second writable filesystem on this machine, so the copy fallback — the path "
                "every tmpfs machine takes on every run — cannot be reached")

    try:
        home = stand_in_home(elsewhere / "devhome")
        token = home / ".claude" / ".credentials.json"
        write_token(token, {"refreshToken": "one"})

        env = homeenv.build(script=CARRY_AGENT,
                            agent={"command": "/bin/sh agent.sh",
                                   "credentials_dir": str(home / ".claude")})
        lane = env.claim()
        start_run(env, lane)
        carried = env.home_of(lane) / ".credentials.json"
        assert carried.stat().st_dev != token.stat().st_dev, (
            "this case needs two filesystems, or it measures the hard link instead")
        assert carried.stat().st_ino != token.stat().st_ino, (
            "a hard link across filesystems is impossible; the fixture is wrong")
        end_run(env, lane)

        refresh_as_the_cli_does(token, {"refreshToken": "two"})
        run2 = start_run(env, lane)

        assert carried.read_text() == token.read_text(), (
            f"the copy fallback carried a superseded token:\n  lane {carried.read_text()}\n"
            f"  host {token.read_text()}")
        mode = stat.S_IMODE(carried.stat().st_mode)
        assert mode == 0o600, f"the re-carried copy is mode {oct(mode)}, not 0600"
        assert json.loads(carried_in_the_lane(env, lane, run2))["refreshToken"] == "two", (
            f"the agent read a superseded token: {carried_in_the_lane(env, lane, run2)}")

        end_run(env, lane)
        env.unchanged()
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


# ── MANDATORY NEGATIVE — a lane may not rotate the developer's token out from under them ─────
#
# The cheapest way to keep two names in step is to sync BOTH ways. It would pass every case above
# and it would hand every lane a write on the one file the person running the tool cannot afford
# to lose: OAuth rotates refresh tokens on use, so a lane's refreshed token landing in
# `~/.claude/.credentials.json` logs the developer out of their own machine. Watched RED against
# exactly that implementation.

def test_re_carrying_does_not_touch_the_developers_file(homeenv, tmp_path):
    """After any number of furnishes — with the lane refreshing ITS copy in between, the way the
    CLI does — the host's credential is the same inode, the same mode and the same bytes."""
    home = stand_in_home(tmp_path / "devhome")
    token = home / ".claude" / ".credentials.json"
    write_token(token, {"refreshToken": "the-developers", "expiresAt": 1})
    before = token.stat()
    before_bytes = token.read_text()
    before_names = sorted(p.name for p in (home / ".claude").iterdir())

    # The real one, on machines that have one. The stand-in carries the assertion everywhere else;
    # this is the file the defect would actually cost somebody.
    real_before = (REAL_TOKEN.stat(), digest(REAL_TOKEN)) if REAL_TOKEN.is_file() else None

    env = homeenv.build(script=CARRY_AGENT,
                        agent={"command": "/bin/sh agent.sh",
                               "credentials_dir": str(home / ".claude")})
    lane = env.claim()
    carried = None
    for round_no in range(3):
        start_run(env, lane)
        carried = env.home_of(lane) / ".credentials.json"
        end_run(env, lane)
        # The LANE's own CLI refreshes its token — same operation, same direction, inside the
        # lane's directory. A two-way sync propagates this to the host on the next furnish.
        lane_tmp = carried.with_name(carried.name + ".tmp")
        lane_tmp.write_text(json.dumps({"refreshToken": f"the-lanes-{round_no}"}))
        lane_tmp.chmod(0o600)
        os.rename(lane_tmp, carried)

    start_run(env, lane)

    now = token.stat()
    assert now.st_ino == before.st_ino, (
        f"the developer's credential was REPLACED — inode {before.st_ino} → {now.st_ino}. A lane "
        f"rotating the token out from under the person running the tool logs them out of their "
        f"own machine.")
    assert stat.S_IMODE(now.st_mode) == stat.S_IMODE(before.st_mode), (
        f"the developer's credential changed mode: {oct(stat.S_IMODE(before.st_mode))} → "
        f"{oct(stat.S_IMODE(now.st_mode))}")
    assert token.read_text() == before_bytes, (
        f"the lane's token was written into the developer's file:\n  before {before_bytes}\n"
        f"  after  {token.read_text()}")
    assert sorted(p.name for p in (home / ".claude").iterdir()) == before_names, (
        f"the tool left files in the developer's directory: "
        f"{sorted(p.name for p in (home / '.claude').iterdir())} != {before_names}")

    if real_before is not None:
        assert REAL_TOKEN.stat().st_ino == real_before[0].st_ino, (
            f"{REAL_TOKEN} was replaced by a lane")
        assert stat.S_IMODE(REAL_TOKEN.stat().st_mode) == stat.S_IMODE(real_before[0].st_mode), (
            f"{REAL_TOKEN} changed mode")
        assert digest(REAL_TOKEN) == real_before[1], f"{REAL_TOKEN} was written by a lane"

    end_run(env, lane)
    env.unchanged()


# ── THE STRUCTURAL HALF (§3) — the distinction is named, and the whole list is checked ────────

def borrowed_declared():
    """Every file the tool BORROWS, read off the one place it is declared.

    Enumerated from the declaration rather than named here, because the class this ticket is in is
    *borrowed host state, sampled once and assumed stable* — and the next borrowed file will be
    added by somebody who has never read this file. A list that does not grow with the code is a
    list that goes back to covering one case.
    """
    text = (APP / "agent" / "launch.cpp").read_text()
    return re.findall(r'seed\.borrowed\.emplace_back\(\s*"([^"]+)"', text)


def test_every_borrowed_file_is_re_carried_after_its_owner_replaces_it(homeenv, tmp_path):
    """The borrowed list is named in the code, and every entry on it survives a rename.

    Red until `furnish()`'s inputs are split into SEEDED (written once, the lane's own) and
    BORROWED (re-carried every run, the developer's) as a named distinction — a loop that happens
    to skip is not one, and it is what let a rotating credential be treated like a trust file.
    """
    names = borrowed_declared()
    assert names, (
        "app/agent/launch.cpp does not name a BORROWED list, so nothing enumerates the host state "
        "this tool samples. `SRV-FIX-174`: a credential is not state a lane owns — it is borrowed, "
        "and its owner redefines it at a time nothing here controls.")

    home = stand_in_home(tmp_path / "devhome")
    for name in names:
        write_token(home / ".claude" / name, {"borrowed": name, "generation": "one"})

    env = homeenv.build(script=CARRY_AGENT,
                        agent={"command": "/bin/sh agent.sh",
                               "credentials_dir": str(home / ".claude")})
    lane = env.claim()
    start_run(env, lane)
    where = env.home_of(lane)
    for name in names:
        assert (where / name).is_file(), (
            f"{name} is declared borrowed and was never carried into {where}: "
            f"{sorted(p.name for p in where.iterdir())}")
    end_run(env, lane)

    for name in names:
        refresh_as_the_cli_does(home / ".claude" / name,
                                {"borrowed": name, "generation": "two"})
    start_run(env, lane)

    stale = [name for name in names
             if (where / name).read_text() != (home / ".claude" / name).read_text()]
    assert not stale, (
        f"these borrowed files were sampled once and never re-carried: {stale}. Each is a name in "
        f"a lane pointing at an inode its owner has already replaced.")

    end_run(env, lane)
    env.unchanged()
