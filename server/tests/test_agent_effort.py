"""SRV-FEAT-574 — the effort a role is launched at, applied at the launch and recorded in the run.

`CLAUDE.md`'s model rule says main and QA run at high effort. Until this ticket nothing the tool
launched carried an effort, so the rule was a sentence — the same shape as the 2026-08-22 ledger
row (*"Every lane signs its commits, and the rule forbidding it is only a sentence"*), whose
structural remedy was that a rule whose only mechanism is *the agent reads it* is marked unenforced.

Every case here is judged against a committed capture of the REAL program, because the thing under
test is an external program's interface:

  fixtures/cli-effort-2026-10-10-claude-drops-an-undeclared-effort.json
      `--effort <level>` is a real flag of claude 2.1.272, and an UNDECLARED value is NOT refused:
      the CLI warns on stderr, exits 0 and runs at its default. So "no silent drop to the default"
      is a property only E18 can hold, which is what `test_undeclared_effort_never_spawns` asserts.

  fixtures/cli-effort-2026-10-10-codex-turn-start-takes-an-effort.json
      of `TurnStartParams`, `ThreadStartParams` and `ThreadResumeParams`, codex-cli
      0.162.0-alpha.17.2 gives an `effort` to the first ONLY, and `ReasoningEffort` is a non-empty
      string with no enum — which is why the level names are the manifest's and never the tool's.

Re-capture both with:

    python3 server/tests/capture_effort_interface.py --out server/tests/fixtures

The flag name and the frame each case expects are READ OUT of those files rather than retyped, so a
provider that moves either turns these cases red instead of leaving them agreeing with the code.
"""

import json
import re
import time
from pathlib import Path

import pytest

import stand_in
from conftest import _start_instance, stop_process
from test_agent import assert_reaped
from test_agent_profiles import PEER, eventually, frames
from test_lane import build_repo, git


REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLAUDE_CAPTURE = json.loads(
    (FIXTURES / "cli-effort-2026-10-10-claude-drops-an-undeclared-effort.json").read_text())
CODEX_CAPTURE = json.loads(
    (FIXTURES / "cli-effort-2026-10-10-codex-turn-start-takes-an-effort.json").read_text())


def claude_flag():
    """The flag name, taken from the recorded `claude --help` rather than typed here."""
    assert CLAUDE_CAPTURE["fixture_verified"] is True, CLAUDE_CAPTURE
    lines = CLAUDE_CAPTURE["help_lines"]
    assert lines, f"the capture records no --effort line: {CLAUDE_CAPTURE['cli_version']}"
    flag = lines[0].split()[0]
    assert flag == CLAUDE_CAPTURE["flag"], (flag, CLAUDE_CAPTURE["flag"])
    return flag


def codex_carriers():
    """Which codex params the real app-server gives an `effort`, and which it does not."""
    assert CODEX_CAPTURE["fixture_verified"] is True, CODEX_CAPTURE
    params = CODEX_CAPTURE["params"]
    carries = {name for name, body in params.items() if body and body["has_effort"]}
    plain = {name for name, body in params.items() if body and not body["has_effort"]}
    assert carries and plain, (
        f"the capture distinguishes nothing: carries={carries} plain={plain}")
    return carries, plain


# The claude stand-in records the one thing this ticket is about — the argv the TOOL built — and
# then reads its stdin the way the captured CLI does, so the run stays alive to be ended, resumed
# and released like any other.
ARGV_AGENT = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
""" + stand_in.FRAME_READER + r"""
for a in "$@" ; do printf '%s\n' "$a" >> "./argv-${FACTORY_RUN_ID}.txt" ; done
printf '%s\n' "$@" >> ./argv-all.txt
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "token=${FACTORY_PROGRESS_TOKEN}" >> "$log"
echo "ready" >> "$log"
while : ; do
  if read_frame ; then
    printf '%s\n' "msg<<<${text}>>>" >> "$log"
    case "$text" in bye) break ;; esac
  else
    break
  fi
done
echo "eof" >> "$log"
"""

QA_PROMPT = "You are checking {ticket_id} at {ticket_path} in {repo_root}, from {triggered_by_run}.\n"

CLAUDE_PROFILE = "sonnet"
CODEX_PROFILE = "gpt"


class Env:
    """One repository, one server. A case that needs a second manifest gets a second Env.

    It is a separate object per `build`, not a mutated one: a fixture that reassigns `self.srv`
    hands both names to the LAST repository, and every assertion written before that call then
    measures the wrong server while still passing for the wrong reason.
    """

    def __init__(self, root, manifest_path, srv, serve):
        self.root = root
        self.manifest_path = manifest_path
        self.srv = srv
        self._serve = serve

    # ── acting ───────────────────────────────────────────────────────────────────────────────
    def claim(self, ticket="TCK-001", workspace="core"):
        r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})
        assert r.status_code == 201, r.text
        return r.json()

    def start(self, lane, profile=CLAUDE_PROFILE, prompt="hello", **body):
        payload = {"agent": profile}
        if prompt is not None:
            payload["prompt"] = prompt
        payload.update(body)
        return self.srv.post(f"/api/lanes/{lane['lane']}/runs", json=payload)

    def started(self, lane, profile=CLAUDE_PROFILE, **body):
        r = self.start(lane, profile, **body)
        assert r.status_code == 201, r.text
        return r.json()

    def end(self, lane):
        assert self.srv.post(f"/api/lanes/{lane['lane']}/end",
                             json={}).status_code == 202

    def release(self, lane, force=True):
        r = self.srv.delete(f"/api/lanes/{lane['lane']}"
                            + ("?force=true" if force else ""))
        assert r.status_code == 200, r.text
        return r.json()

    def report(self, lane, stage):
        body = {"token": self.token(lane), "stage": stage}
        r = self.srv.post("/api/progress", json=body)
        assert r.status_code == 202, r.text
        return r.json()

    # ── reading ──────────────────────────────────────────────────────────────────────────
    def argv(self, lane, run):
        """The argv the TOOL built, read out of the child's own record of it."""
        path = Path(lane["worktree"]) / f"argv-{run['run_id']}.txt"
        waited = eventually(lambda: path.exists(), bool)
        assert waited, path
        return path.read_text().splitlines()

    def log(self, lane):
        path = Path(lane["worktree"]) / "agent-log.txt"
        return path.read_text() if path.exists() else ""

    def token(self, lane):
        for line in eventually(lambda: self.log(lane),
                               lambda text: "token=" in text).splitlines():
            if line.startswith("token="):
                return line.split("=", 1)[1]
        raise AssertionError(self.log(lane))

    def runs(self, ticket="TCK-001"):
        r = self.srv.get(f"/api/tickets/{ticket}/runs")
        assert r.status_code == 200, r.text
        return r.json()["runs"]

    def row(self, run_id, ticket="TCK-001"):
        rows = [row for row in self.runs(ticket) if row["run_id"] == run_id]
        assert len(rows) == 1, (run_id, self.runs(ticket))
        return rows[0]

    def lanes(self):
        r = self.srv.get("/api/lanes")
        assert r.status_code == 200, r.text
        return r.json()["lanes"]

    def wait_lanes(self, n, timeout=25):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = self.lanes()
            if len(rows) == n:
                return rows
            time.sleep(0.05)
        raise AssertionError(f"expected {n} lane(s); there are "
                             f"{[(r['lane'], r['role']) for r in self.lanes()]}")

    def restart(self, name="effort-restart.log"):
        stop_process(self.srv.proc)
        if self.srv.handle is not None:
            self.srv.handle.close()
        self.srv = _start_instance(self.root, self.srv.log.parent / name)
        return self.srv

@pytest.fixture
def effortenv(tmp_path, serve):
    """Build one repository declaring both providers, with whatever effort keys a case needs.

    Both stand-ins are the ones the rest of this suite already holds to the recorded CLIs: the
    claude side reads framed stdin (`stand_in.FRAME_READER`) and the codex side is
    `test_agent_profiles.PEER`, which replays `fixtures/cli-app-server-codex-2026-09-18.json`.
    """
    made = []

    def build(*, efforts=("low", "medium", "high", "xhigh"),
              claude_efforts=None, codex_efforts=None,
              default_effort="low", run_effort=None, qa_effort=None,
              roles=True, lanes_host=3, tickets=("TCK-001", "TCK-002"),
              mutate=None, expect_start=True):
        root = build_repo(tmp_path / f"repo{len(made)}", lanes_host=lanes_host, tickets=tickets)
        credentials = tmp_path / f"codex-credentials{len(made)}"
        credentials.mkdir(exist_ok=True)
        (credentials / "auth.json").write_text('{"token":"fixture-secret"}\n')
        (root / "agent.sh").write_text(ARGV_AGENT)
        (root / "agent.py").write_text(PEER)
        capture = FIXTURES / "cli-app-server-codex-2026-09-18.json"
        (root / capture.name).write_bytes(capture.read_bytes())

        path = root / ".factory" / "factory.json"
        manifest = json.loads(path.read_text())
        claude = {"driver": "claude", "command": "/bin/sh agent.sh",
                  "models": ["claude-test"], "default_model": "claude-test"}
        codex = {"driver": "codex", "command": "/usr/bin/python3 agent.py",
                 "models": ["gpt-test"], "default_model": "gpt-test",
                 "credentials_dir": str(credentials)}
        # `None` inherits the case's `efforts`; `()` declares NONE, which is the profile that
        # has opted out of the whole mechanism and must still start.
        for profile, own in ((claude, claude_efforts), (codex, codex_efforts)):
            declared = efforts if own is None else own
            if declared:
                profile["efforts"] = list(declared)
                if default_effort is not None:
                    profile["default_effort"] = default_effort
        manifest["default_agent"] = CLAUDE_PROFILE
        manifest["agents"] = {CLAUDE_PROFILE: claude, CODEX_PROFILE: codex}
        if run_effort is not None:
            manifest["run_effort"] = run_effort
        if roles:
            manifest["roles"] = {"qa": {"start_on_stage": "QA", "lane": "new",
                                        "prompt_file": ".factory/prompts/qa.md"}}
            if qa_effort is not None:
                manifest["roles"]["qa"]["effort"] = qa_effort
            prompt = root / ".factory" / "prompts" / "qa.md"
            prompt.parent.mkdir(parents=True, exist_ok=True)
            prompt.write_text(QA_PROMPT)
        # The fault a load-time case is about is written BEFORE the server reads the file: the
        # manifest is loaded at startup, so a mutation afterwards is answered from what was
        # already parsed and the case would assert against a stale 200.
        if mutate is not None:
            mutate(manifest)
        path.write_text(json.dumps(manifest, indent=2))
        git(root, "add", "-A")
        git(root, "commit", "-m", "two providers, and the efforts they declare")

        env = Env(root, path, serve(root, expect_start=expect_start, FACTORY_TEST_HOOKS="1"),
                  serve)
        made.append(env)
        return env

    yield build
    for env in made:
        stop_process(env.srv.proc)
        if env.srv.handle is not None:
            env.srv.handle.close()


def effort_in(argv, flag):
    """Every value the argv gives that flag — a list, so `exactly once` is assertable."""
    return [argv[i + 1] for i, word in enumerate(argv)
            if word == flag and i + 1 < len(argv)]


def turn_starts(lane):
    return [f for f in frames(lane) if f.get("method") == "turn/start"]


def thread_frames(lane):
    return [f for f in frames(lane)
            if f.get("method") in ("thread/start", "thread/resume")]


# ── criterion 1 ───────────────────────────────────────────────────────────────────────────────

def test_effort_reaches_each_provider(effortenv):
    """§1 C1 — a Claude run at `high` and a Codex run at `medium`, in both providers' own words.

    The two runs differ in provider AND value, so neither can be passing because the other did:
    a fixture where both carry the same string cannot say which launch the assertion read.

    The Codex half asserts the frame the REAL app-server takes an effort on, and the frames it does
    not, read out of the committed schema capture rather than typed here.
    """
    env = effortenv()
    flag = claude_flag()
    carries, plain = codex_carriers()

    claude_lane = env.claim("TCK-001")
    claude_run = env.started(claude_lane, CLAUDE_PROFILE, effort="high")
    argv = env.argv(claude_lane, claude_run)
    assert effort_in(argv, flag) == ["high"], (
        f"the Claude argv does not carry `{flag} high` exactly once: {argv}")
    assert claude_run["effort"] == "high", claude_run

    # The same launch, resumed: `--effort` is on the `--resume` form too, or a continued session
    # silently drops to the provider's default halfway through a ticket.
    env.end(claude_lane)
    assert_reaped(claude_run["pid"])
    resumed = env.started(claude_lane, CLAUDE_PROFILE, resume=True, effort="high")
    resumed_argv = env.argv(claude_lane, resumed)
    assert "--resume" in resumed_argv, resumed_argv
    assert effort_in(resumed_argv, flag) == ["high"], (
        f"the resumed Claude argv dropped `{flag}`: {resumed_argv}")
    env.end(claude_lane)
    assert_reaped(resumed["pid"])

    codex_lane = env.claim("TCK-002")
    codex_run = env.started(codex_lane, CODEX_PROFILE, effort="medium")
    first = eventually(lambda: turn_starts(codex_lane), bool)[0]
    assert first["params"].get("effort") == "medium", (
        f"the first turn/start carries no effort: {first}")
    assert codex_run["effort"] == "medium", codex_run

    assert "TurnStartParams" in carries, carries
    for name in plain:
        assert name in ("ThreadStartParams", "ThreadResumeParams"), name
    for frame in thread_frames(codex_lane):
        assert "effort" not in frame["params"], (
            f"{frame['method']} carries an effort the real app-server has no field for "
            f"(the capture gives one to {sorted(carries)} only): {frame}")

    env.end(codex_lane)
    assert_reaped(codex_run["pid"])
    again = env.started(codex_lane, CODEX_PROFILE, resume=True, effort="medium")
    after_resume = eventually(lambda: turn_starts(codex_lane), lambda rows: len(rows) >= 2)[-1]
    assert after_resume["params"].get("effort") == "medium", (
        f"the turn/start after a resume carries no effort: {after_resume}")
    assert [f["method"] for f in thread_frames(codex_lane)].count("thread/resume") == 1, (
        thread_frames(codex_lane))
    env.end(codex_lane)
    assert_reaped(again["pid"])

    assert (claude_run["driver"], claude_run["effort"]) != (codex_run["driver"],
                                                            codex_run["effort"])


# ── criterion 2 ───────────────────────────────────────────────────────────────────────────────

def test_effort_resolution_order(effortenv):
    """§1 C2 — one manifest, four values that disagree, and each step read where they disagree.

    The profile default, `run_effort`, `roles.qa.effort` and the explicit request are FOUR distinct
    strings. Three cannot distinguish *the request beat the role* from *the request beat the
    default*, and a fixture in which two of them hold the same value passes identically whichever
    one the code read.
    """
    env = effortenv(default_effort="low", run_effort="medium", qa_effort="high")
    flag = claude_flag()

    # 2 — no request: the run's own role is `run`, so `run_effort` wins over the profile default.
    lane = env.claim("TCK-001")
    run = env.started(lane, CLAUDE_PROFILE)
    assert effort_in(env.argv(lane, run), flag) == ["medium"], env.argv(lane, run)
    assert run["effort"] == "medium", run

    # 1 — an explicit request beats `run_effort` AND the profile default.
    env.end(lane)
    assert_reaped(run["pid"])
    asked = env.started(lane, CLAUDE_PROFILE, resume=False, effort="xhigh")
    assert effort_in(env.argv(lane, asked), flag) == ["xhigh"], env.argv(lane, asked)
    assert asked["effort"] == "xhigh", asked

    # 2 — the QA role's own effort, on the lane the TOOL started for it, not one this test built.
    env.report(lane, "QA")
    role_lane = [row for row in env.wait_lanes(2) if row["lane"] != lane["lane"]][0]
    role_run = eventually(
        lambda: [row for row in env.runs() if row["lane"] == role_lane["lane"]], bool)[0]
    assert role_run["effort"] == "high", (
        f"the QA role launched at {role_run['effort']!r}, not the role's own `high`: {role_run}")
    role_argv = env.argv({"worktree": role_lane["worktree"]}, role_run)
    assert effort_in(role_argv, flag) == ["high"], role_argv

    # 2 — a lane whose role NOBODY DECLARED is a `run`, which is what `api::runKind`
    # (`app/api/lane-view.cpp:249`) already calls it, so it takes `run_effort` and not the
    # profile default. The two differ here, so the case can tell which one was read.
    stray = env.srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "core",
                                             "role": "nobody-declared-this"})
    assert stray.status_code == 201, stray.text
    stray_lane = stray.json()
    stray_run = env.started(stray_lane, CLAUDE_PROFILE)
    assert effort_in(env.argv(stray_lane, stray_run), flag) == ["medium"], (
        f"an undeclared role took the profile default instead of run_effort: "
        f"{env.argv(stray_lane, stray_run)}")
    env.end(stray_lane)
    assert_reaped(stray_run["pid"])

    # 3 — the fallthrough: with no `run_effort` and no role effort, the profile default is reached.
    plain = effortenv(default_effort="low", run_effort=None, qa_effort=None)
    bare = plain.claim("TCK-001")
    bare_run = plain.started(bare, CLAUDE_PROFILE)
    assert effort_in(plain.argv(bare, bare_run), flag) == ["low"], plain.argv(bare, bare_run)
    assert bare_run["effort"] == "low", bare_run


# ── criterion 3 ───────────────────────────────────────────────────────────────────────────────

def test_history_records_sent_effort(effortenv):
    """§1 C3 — E7 shows what was SENT, across a restart and across a recycled lane name.

    The lane half is the 2026-08-24 `SRV-FIX-247` question put to this record: a lane is the one
    thing designed to be destroyed and its name reused, so the two runs are given DIFFERENT efforts
    and the first row must still read its own after the name belongs to somebody else.
    """
    env = effortenv(default_effort="low", run_effort="medium")
    flag = claude_flag()

    lane = env.claim("TCK-001")
    first = env.started(lane, CLAUDE_PROFILE, effort="high")
    assert env.row(first["run_id"])["effort"] == "high", env.runs()
    env.end(lane)
    assert_reaped(first["pid"])

    env.release(lane)
    env.wait_lanes(0)
    again = env.claim("TCK-001")
    assert again["lane"] == lane["lane"], (
        f"the lane name was not recycled, so this case measures nothing: {again}")
    second = env.started(again, CLAUDE_PROFILE, effort="xhigh")
    assert env.row(second["run_id"])["effort"] == "xhigh", env.runs()
    assert env.row(first["run_id"])["effort"] == "high", (
        "the first run's recorded effort changed when its lane name was reused: "
        f"{env.runs()}")
    env.end(again)
    assert_reaped(second["pid"])

    # The record outlives the process that made it, so it outlives the server too.
    env.restart()
    after = {row["run_id"]: row["effort"] for row in env.runs()}
    assert after[first["run_id"]] == "high" and after[second["run_id"]] == "xhigh", after

    # A profile that declares no efforts carries none, whatever is declared above it — and `null`
    # is what the record says, not the tool's guess at the provider's default. Asserted on BOTH
    # providers, because "argv / RPC carry no effort" is two different wires and a fixture that
    # leaves the codex profile declaring one never checks the RPC half at all.
    quiet = effortenv(claude_efforts=(), codex_efforts=(), default_effort="low",
                      run_effort="high")
    lane = quiet.claim("TCK-001")
    run = quiet.started(lane, CLAUDE_PROFILE)
    argv = quiet.argv(lane, run)
    assert flag not in argv, f"a profile declaring no efforts was launched with one: {argv}"
    assert run["effort"] is None, run
    assert quiet.row(run["run_id"])["effort"] is None, quiet.runs()
    quiet.end(lane)
    assert_reaped(run["pid"])

    codex_lane = quiet.claim("TCK-002")
    codex_run = quiet.started(codex_lane, CODEX_PROFILE)
    first = eventually(lambda: turn_starts(codex_lane), bool)[0]
    assert "effort" not in first["params"], (
        f"a codex profile declaring no efforts still sent one on turn/start: {first}")
    assert codex_run["effort"] is None, codex_run
    assert quiet.row(codex_run["run_id"], "TCK-002")["effort"] is None, quiet.runs("TCK-002")
    quiet.end(codex_lane)
    assert_reaped(codex_run["pid"])


# ── criterion 4 · MANDATORY NEGATIVE ──────────────────────────────────────────────────────────

def test_undeclared_effort_never_spawns(effortenv):
    """§1 C4 — the refusals, asserted at the boundary rather than at the status code.

    The real `claude` does NOT refuse an undeclared level: it warns and runs at its default, which
    the capture records. So each `400` below also asserts that NOTHING started — no process, no run
    record, no run directory — because a refusal that answered 400 after the spawn is the defect
    `SRV-FIX-476`/`486` are about, and a status code cannot tell the two apart.
    """
    assert CLAUDE_CAPTURE["refuses_an_undeclared_value"] is False, (
        "the captured CLI now refuses an undeclared effort itself; this criterion's premise has "
        f"changed and the fixture must be re-read: {CLAUDE_CAPTURE['probes']}")
    assert CLAUDE_CAPTURE["warns_and_defaults_instead"] is True, CLAUDE_CAPTURE["probes"]

    env = effortenv(efforts=("low", "high"), default_effort="low")
    lane = env.claim("TCK-001")
    runs_dir = Path(env.root) / ".factory" / "runs"
    before = sorted(p.name for p in runs_dir.iterdir()) if runs_dir.exists() else []

    refused = {
        "an effort the profile never declared": {"effort": "xhigh"},
        "an empty effort": {"effort": ""},
        "an effort that is not a string": {"effort": 3},
    }
    for why, body in refused.items():
        r = env.start(lane, CLAUDE_PROFILE, **body)
        assert r.status_code == 400, f"{why}: {r.status_code} {r.text}"
        assert r.json()["error"]["code"] == "invalid_agent_selection", (why, r.text)

        # the boundary, not the code: nothing was launched and nothing was recorded
        assert env.runs() == [], f"{why} left a run record: {env.runs()}"
        after = sorted(p.name for p in runs_dir.iterdir()) if runs_dir.exists() else []
        assert after == before, f"{why} created a run directory: {set(after) - set(before)}"
        assert env.log(lane) == "", f"{why} started the agent: {env.log(lane)}"
        process = [row for row in env.lanes() if row["lane"] == lane["lane"]]
        assert process and process[0]["state"]["process"] != "running", (why, process)

    # An effort asked of a profile that declares none is the same refusal, not a quiet drop.
    quiet = effortenv(claude_efforts=(), codex_efforts=("low",), default_effort="low")
    quiet_lane = quiet.claim("TCK-001")
    r = quiet.start(quiet_lane, CLAUDE_PROFILE, effort="high")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_agent_selection", r.text
    assert quiet.runs() == [], quiet.runs()

    # The ordinary case still works, or the refusal has become "no run may name an effort".
    ok = env.started(lane, CLAUDE_PROFILE, effort="high")
    assert effort_in(env.argv(lane, ok), claude_flag()) == ["high"], env.argv(lane, ok)
    env.end(lane)
    assert_reaped(ok["pid"])


@pytest.mark.parametrize("pointer,mutate", [
    ("/agents/sonnet/default_effort",
     lambda m: m["agents"][CLAUDE_PROFILE].update(default_effort="xhigh")),
    ("/run_effort", lambda m: m.update(run_effort="xhigh")),
    ("/roles/qa/effort", lambda m: m["roles"]["qa"].update(effort="xhigh")),
])
def test_an_effort_no_profile_declares_is_refused_at_load(effortenv, pointer, mutate):
    """§1 C4's third refusal, and the two of the same class the role caller needs.

    `default_effort` outside its own `efforts` is §1's. `run_effort` and `roles.<r>.effort` are the
    same fault one level up, and they are refused HERE rather than at the start because
    `api/roles.cpp::abandon` turns a start refusal into a `LOG(WARNING)` and a released lane: a
    role has no screen to be refused on, so an effort no declaring profile can honour would become
    exactly the silent drop to the default that C4 forbids.
    """
    env = effortenv(efforts=("low", "high"), default_effort="low", run_effort="high",
                    qa_effort="high", mutate=mutate, expect_start=False)

    r = env.srv.get("/api/manifest")
    assert r.status_code == 422, f"{pointer} loaded: {r.status_code} {r.text}"
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["pointer"] == pointer, err
    assert "xhigh" in json.dumps(err), err


# ── criterion 5 ───────────────────────────────────────────────────────────────────────────────

MODEL_RULE_ROW = re.compile(
    r"^\s*\|\s*Main \(run / QA / orchestration\)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|")


def main_row_effort():
    """The effort `CLAUDE.md`'s model-rule table gives the main role, parsed out of the table.

    Read rather than restated: a criterion that retypes the value it checks agrees with whatever
    was typed, and the whole point of this ticket is that the table was the only mechanism.
    """
    for line in (REPO / "CLAUDE.md").read_text().splitlines():
        found = MODEL_RULE_ROW.match(line)
        if found:
            return found.group(3).strip().strip("`")
    raise AssertionError("CLAUDE.md has no `Main (run / QA / orchestration)` model-rule row")


def test_this_repository_runs_main_and_qa_at_high(serve, tmp_path):
    """§1 C5 — this repository's own manifest, through the real loader, matching the real table."""
    expected = main_row_effort()
    assert expected, "the model rule's Main row names no effort"

    manifest = json.loads((REPO / ".factory" / "factory.json").read_text())
    assert manifest.get("run_effort") == expected, (
        f"CLAUDE.md's model rule runs main at {expected!r} and `run_effort` is "
        f"{manifest.get('run_effort')!r}")
    assert manifest["roles"]["qa"].get("effort") == expected, (
        f"the model rule runs QA at {expected!r} and `roles.qa.effort` is "
        f"{manifest['roles']['qa'].get('effort')!r}")

    declared = {name: profile.get("efforts") for name, profile in manifest["agents"].items()}
    assert declared and all(efforts and expected in efforts
                            for efforts in declared.values()), (
        f"a profile cannot honour the model rule's {expected!r}: {declared}")

    # The loader, not the file: a manifest that parses in python and 422s in the tool is not a
    # manifest this repository runs on.
    # The loader resolves `credentials_dir` and each agent command on the machine it runs on. A
    # lane has neither `~/.codex` nor the developer's PATH, so the fixture supplies both and the
    # case asks only about the declarations (SRV-FEAT-574 QA round 3).
    home = tmp_path / "home"
    for profile in manifest["agents"].values():
        if profile.get("credentials_dir", "").startswith("~/"):
            (home / profile["credentials_dir"][2:]).mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for profile in manifest["agents"].values():
        stub = bindir / profile["command"].split()[0]
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    srv = serve(REPO, HOME=str(home), PATH=f"{bindir}:/usr/bin:/bin")
    answered = srv.get("/api/manifest")
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["run_effort"] == expected, body
    for name, profile in body["agents"].items():
        assert expected in profile["efforts"], (name, profile)
        assert profile["default_effort"] == expected, (name, profile)
