"""SRV-FEAT-017 — the standing policy, the request that names its rule, and the granted list.

Three pieces that only work together (`SPEC.md` §4.12, F22), and one of them exists because it
has already cost a run: an agent waited on an approval nobody could see, for a command a rule was
supposed to cover and did not. So the request does not merely say *may I* — it says **which rule
would be written** if you answer always, and **which rule nearly matched and why it did not**.

Two pre-decisions are asserted rather than described:

- **it fails closed** — a corrupt `.factory/permissions.json`, an unparseable pattern, any doubt at
  all, means ask; and
- **a denial is an answer, not a kill** — the agent is told it was refused and its run continues,
  which is checked by sending the child another message afterwards and watching it arrive.

Every socket frame these cases produce is validated against `factory/docs/technical/contracts/socket.schema.json`, the
same file the client's types are generated from. `test_ws.py` names this file as where `M6` and
`M7` are exercised, and these are the assertions that claim rests on.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import cli_conformance
import jsonschema
import pytest
import requests

from test_agent import AGENT_SH, assert_reaped, proc_state
# SRV-FIX-466 / SRV-FIX-532. `Path.home() / ".claude"` is the developer's machine, and inside a
# lane `$HOME` is the sandbox's own tmpfs — so the two cases below declared a credential directory
# that the design guarantees is empty and the manifest refused to load. `_real_credentials` is the
# product's own answer: the directory the tool CARRIED the credential into for this session.
from test_confine import REAL_CREDENTIALS
from test_lane import build_repo, git

SOCKET_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)


def valid(frame):
    SOCKET_VALIDATOR.validate(frame)
    return frame


def tracked_status(root):
    """The main checkout's TRACKED files, exactly as git reports them.

    Untracked is a separate question here and deliberately so: this ticket's whole job is to
    create one new file in the repository, and a baseline that forbade that would forbid the
    feature. `untracked_set` below asserts which one.
    """
    return subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1",
                           "--untracked-files=no"],
                          capture_output=True, text=True, timeout=30).stdout


def untracked_set(root):
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1",
                          "--untracked-files=all"],
                         capture_output=True, text=True, timeout=30).stdout
    return {line[3:] for line in out.splitlines() if line.startswith("?? ")}


def rules_file(root):
    return Path(root) / ".factory" / "permissions.json"


class Ask:
    """One tool use, asked on a thread, because a request that blocks is the point.

    The hook holds its HTTP response until the decision arrives — that is what makes "the agent
    was told it was refused" a fact the test reads rather than infers. So the ask has to be in
    flight while the case answers it.
    """

    def __init__(self, srv, lane, tool, inp):
        self.srv, self.lane, self.tool, self.input = srv, lane, tool, inp
        self.done = threading.Event()
        self.response = None
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.response = requests.post(
                self.srv.base + "/api/_test/permission-ask",
                json={"lane": self.lane, "tool": self.tool, "input": self.input},
                timeout=90)
        except Exception as exc:                       # noqa: BLE001 — reported, not swallowed
            self.error = exc
        finally:
            self.done.set()

    def result(self, timeout=30):
        assert self.done.wait(timeout), (
            f"the ask for {self.tool} never came back within {timeout}s")
        assert self.error is None, self.error
        assert self.response.status_code == 200, self.response.text
        return self.response.json()

    def waiting(self, seconds=0.0):
        if seconds:
            time.sleep(seconds)
        return not self.done.is_set()


@pytest.fixture
def permenv(tmp_path, serve):
    """A repository with the stand-in agent, a manifest, and a server with the hook on.

    Teardown asserts the main checkout's TRACKED files are byte-identical, and that every run a
    case started was reaped — the two assertions every lane and agent case in this suite carries.
    """
    made = []
    started = []
    conns = []

    class Env:
        def build(self, *, agent=None, permissions=None, **env):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent=agent or {"command": "/bin/sh agent.sh"})
            (root / "agent.sh").write_text(AGENT_SH)
            git(root, "add", "agent.sh")
            git(root, "commit", "-m", "agent")
            if permissions is not None:
                rules_file(root).write_text(permissions)
            self.root = root
            self.baseline = tracked_status(root)
            made.append((root, self.baseline))
            self.srv = serve(root, FACTORY_TEST_HOOKS="1", **env)
            return self

        def claim(self, ticket="TCK-001", workspace="core"):
            r = self.srv.post("/api/lanes",
                              json={"ticket_id": ticket, "workspace": workspace})
            assert r.status_code == 201, r.text
            self.lane = r.json()
            return r.json()["lane"]

        def start(self, lane, **body):
            r = self.srv.post(f"/api/lanes/{lane}/runs", json=body)
            assert r.status_code == 201, r.text
            run = r.json()
            started.append(run["pid"])
            return run

        # ── SRV-FEAT-057 — the run's own token and its own configuration directory ───────────
        #
        # Both read off `/proc/<pid>` of the process the tool started, rather than recomputed from
        # a rule this test would then be asserting against itself. The pid is bwrap's, and the
        # environment and argv it was exec'd with are the tool's own words.
        def _run_argv(self):
            for pid in reversed(started):
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as fh:
                        return pid, fh.read().decode("utf-8", "replace").split("\0")
                except OSError:
                    continue
            raise AssertionError("no live run to read an argv from")

        def token(self):
            # `--setenv NAME VALUE` in the helper's OWN argv: the run's environment is given to
            # the child by the confinement, so it is not in bwrap's `environ` — it is in the words
            # the tool exec'd it with.
            _, argv = self._run_argv()
            for i, a in enumerate(argv):
                if a == "FACTORY_PROGRESS_TOKEN" and i + 1 < len(argv):
                    return argv[i + 1]
            raise AssertionError("the run carries no FACTORY_PROGRESS_TOKEN")

        def agent_config_dir(self, lane):
            _, argv = self._run_argv()
            for i, a in enumerate(argv):
                if a == "/factory/agent-config" and i >= 1:
                    return argv[i - 1]
            raise AssertionError(f"the run's argv binds no agent config directory: {argv[:40]}")

        def subscribed(self, lane):
            c = self.srv.ws()
            hello = valid(c.recv())
            assert hello["m"] == "hello", hello
            c.send({"m": "subscribe", "scope": {"lane": lane}})
            valid(c.wait_for("snapshot"))
            conns.append(c)
            return c

        def listening(self):
            """A connection that subscribes to nothing — M7 and M12 go to every client."""
            c = self.srv.ws()
            valid(c.recv())
            conns.append(c)
            return c

        def rules(self):
            r = self.srv.get("/api/permissions/rules")
            assert r.status_code == 200, r.text
            return r.json()

        def answer(self, request_id, decision):
            return self.srv.post("/api/permissions/decisions",
                                 json={"request_id": request_id, "decision": decision})

        def lane_state(self, lane):
            lanes = self.srv.get("/api/lanes").json()["lanes"]
            for row in lanes:
                if row["lane"] == lane:
                    return row
            raise AssertionError(f"{lane} is not in {[r['lane'] for r in lanes]}")

        def log(self, lane_obj):
            try:
                return (Path(lane_obj["worktree"]) / "agent-log.txt").read_text()
            except OSError:
                return ""

        def wait_log(self, lane_obj, needle, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                text = self.log(lane_obj)
                if needle in text:
                    return text
                time.sleep(0.05)
            raise AssertionError(
                f"{needle!r} never reached the agent's log within {timeout}s:\n"
                f"{self.log(lane_obj)}")

        def unchanged(self):
            now = tracked_status(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()

    for c in conns:
        c.close()
    for pid in started:
        assert_reaped(pid, within=20, what="(checked in teardown)")
    for root, before in made:
        assert tracked_status(root) == before, (
            f"the main checkout at {root} changed by the end of the test:\n"
            f"before:\n{before}\nafter:\n{tracked_status(root)}")


# ── 1 · F22, and the case that has already cost a run ────────────────────────────────────────

def test_request_names_the_rule(permenv):
    """An out-of-policy tool use produces M6 carrying the rule that WOULD be written, and —
    when an existing rule nearly matched — which one and **why it did not**.

    `nearest_miss` is the whole reason this ticket exists. A rule that nearly matched and a rule
    that is missing are different problems with different fixes, and a request that cannot tell
    them apart leaves the person clicking `always` again.
    """
    env = permenv.build(agent={"command": "/bin/sh agent.sh",
                               "allowed_tools": ["Bash(npm run test:*)", "Read"]})
    lane = env.claim()
    c = env.subscribed(lane)

    ask = Ask(env.srv, lane, "Bash", {"command": "npm run build"})
    m6 = valid(c.wait_for("permission.request"))

    assert m6["lane"] == lane, m6
    assert m6["tool"] == "Bash", m6
    assert m6["expires_at"] is None, (
        "a request that can expire produces a run that failed for a reason nobody saw")
    assert m6["rule_that_would_be_written"] == "Bash(npm run build)", (
        f"the tool offered to write {m6['rule_that_would_be_written']!r}, which is not the "
        f"literal command it was asked about — `always` must write the rule it showed you and "
        f"nothing broader")

    miss = m6["nearest_miss"]
    assert miss["pattern"] == "Bash(npm run test:*)", miss
    assert miss["source"] == "manifest", miss
    assert miss["differs_on"] == "argument", miss
    assert miss["matched_prefix"] == "npm run ", (
        f"the pattern accepts `npm run ` and then diverges; the miss reports "
        f"{miss['matched_prefix']!r}")
    why = miss["why_it_did_not_match"]
    assert "npm run " in why and "test:" in why and "build" in why, (
        f"the reason has to name the prefix, what the pattern required next, and what the "
        f"command had there instead; it says: {why!r}")
    assert miss["rule_id"] == next(r["rule_id"] for r in env.rules()["rules"]
                                   if r["pattern"] == "Bash(npm run test:*)"), miss

    assert env.answer(m6["request_id"], "once").status_code == 200
    assert ask.result()["allowed"] is True

    # ...and a request no rule is anywhere near reports NO nearest miss. `Read(…)` did not nearly
    # match a Grep, and saying it did would be noise dressed up as help.
    far = Ask(env.srv, lane, "Grep", {"pattern": "TODO"})
    m6b = valid(c.wait_for("permission.request"))
    assert "nearest_miss" not in m6b, (
        f"nothing was close to this request and the tool claimed something was: {m6b}")
    assert m6b["rule_that_would_be_written"] == "Grep(TODO)", m6b
    assert env.answer(m6b["request_id"], "once").status_code == 200
    far.result()

    env.unchanged()


# ── 2 ────────────────────────────────────────────────────────────────────────────────────────

def test_allow_always_writes_one_readable_rule(permenv):
    """`once` writes nothing; `always` appends exactly one rule to a file a human can read in a
    diff; `deny` returns the denial to the agent **and the run continues**."""
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(env.lane, "ready")
    c = env.subscribed(lane)
    everyone = env.listening()

    # once — released, and nothing on disk
    once = Ask(env.srv, lane, "Bash", {"command": "echo one"})
    m6 = valid(c.wait_for("permission.request"))
    r = env.answer(m6["request_id"], "once")
    assert r.status_code == 200, r.text
    assert r.json() == {"decision": "once", "unblocked": True}, r.json()
    assert once.result()["allowed"] is True
    assert not rules_file(env.root).exists(), (
        "`once` wrote a standing rule; it releases this request and nothing else")

    resolved = valid(everyone.wait_for("permission.resolved"))
    assert resolved["request_id"] == m6["request_id"], resolved
    assert resolved["decision"] == "once", resolved

    # always — exactly one rule, and the file is readable
    always = Ask(env.srv, lane, "Bash", {"command": "echo two"})
    m6 = valid(c.wait_for("permission.request"))
    assert m6["rule_that_would_be_written"] == "Bash(echo two)", m6
    r = env.answer(m6["request_id"], "always")
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "always" and r.json()["unblocked"] is True, r.json()
    assert r.json()["rule_id"], r.json()
    assert always.result()["allowed"] is True

    written = json.loads(rules_file(env.root).read_text())
    assert [p["pattern"] for p in written["rules"]] == ["Bash(echo two)"], (
        f"`always` wrote {written['rules']} — it must write exactly the one rule it showed")
    assert written["rules"][0]["rule_id"] == r.json()["rule_id"], written
    assert "match_count" not in written["rules"][0], (
        "a counter in the committed file gives it a permanent diff, which is the opposite of "
        "the property the file exists for")
    text = rules_file(env.root).read_text()
    assert text.endswith("\n") and "\n  " in text, (
        f"the file is committed by a human and has to read in a diff:\n{text}")
    assert untracked_set(env.root) == {".factory/permissions.json"}, (
        f"the tool wrote something other than its one policy file: "
        f"{untracked_set(env.root)}")

    # deny — the answer is returned to the agent, and the run carries on
    denied = Ask(env.srv, lane, "Bash", {"command": "rm -rf /"})
    m6 = valid(c.wait_for("permission.request"))
    r = env.answer(m6["request_id"], "deny")
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "deny" and r.json()["unblocked"] is True, r.json()
    answer = denied.result()
    assert answer["allowed"] is False, (
        f"a denial has to reach the agent as an answer: {answer}")
    assert answer["decision"] == "deny", answer

    assert proc_state(run["pid"]) not in (None, "Z"), (
        "the denial killed the run; SPEC.md E23 says a denial is an answer, not a kill")
    row = env.lane_state(lane)
    assert row["state"]["process"] == "running", row
    msg = env.srv.post(f"/api/lanes/{lane}/messages", json={"text": "still-here"})
    assert msg.status_code == 200, msg.text
    env.wait_log(env.lane, "in:still-here")

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 3 ────────────────────────────────────────────────────────────────────────────────────────

def test_granted_rules_are_listable_and_revocable(permenv):
    """E11 lists a granted rule with a match count; E20 removes it and the next matching request
    asks again — **inside the same live run**, which is what makes revoking mean anything."""
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(env.lane, "ready")
    c = env.subscribed(lane)
    everyone = env.listening()

    ask = Ask(env.srv, lane, "Bash", {"command": "echo hello"})
    m6 = valid(c.wait_for("permission.request"))
    rule_id = env.answer(m6["request_id"], "always").json()["rule_id"]
    assert ask.result()["allowed"] is True

    m12 = valid(everyone.wait_for("rules"))
    assert [r["pattern"] for r in m12["rules"]] == ["Bash(echo hello)"], m12

    listed = env.rules()
    assert len(listed["rules"]) == 1, listed
    rule = listed["rules"][0]
    assert rule["rule_id"] == rule_id, rule
    assert rule["source"] == "granted", rule
    assert rule["revocable"] is True, rule
    assert rule["match_count"] == 0, (
        f"the rule has not matched anything yet; it was granted: {rule}")
    assert rule["granted_iso"], rule

    # it matches now, silently — no request, no block, and nothing in the transcript either:
    # a rule that matches is not an event, and a transcript that records every allowed tool call
    # twice is a transcript nobody reads.
    c.drain()                                  # the frames the `always` above produced
    again = Ask(env.srv, lane, "Bash", {"command": "echo hello"})
    quiet = again.result(timeout=10)
    assert quiet["allowed"] is True and quiet["asked"] is False, quiet
    assert quiet["reason"] == "rule", quiet
    assert c.drain(0.8) == [], "a rule matched and the tool asked anyway"
    assert env.rules()["rules"][0]["match_count"] == 1, env.rules()

    # revoke, and the SAME live run asks again
    r = env.srv.delete(f"/api/permissions/rules/{rule_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"revoked": True, "remaining": 0}, r.json()
    assert json.loads(rules_file(env.root).read_text())["rules"] == [], (
        "revoke has to rewrite the file, not merely forget in memory")
    valid(everyone.wait_for("rules"))

    third = Ask(env.srv, lane, "Bash", {"command": "echo hello"})
    m6 = valid(c.wait_for("permission.request"))
    assert m6["lane"] == lane, m6
    assert env.lane_state(lane)["run_id"] == run["run_id"], (
        "the revoke took effect in a different run; the criterion is the same live run")
    env.answer(m6["request_id"], "once")
    assert third.result()["allowed"] is True

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 4 ────────────────────────────────────────────────────────────────────────────────────────

def test_manifest_rule_not_revocable(permenv):
    """A rule from `agent.allowed_tools` lists as the manifest's and cannot be revoked here.

    A UI that silently overrides a committed file is how a repository stops meaning what it says.
    """
    env = permenv.build(agent={"command": "/bin/sh agent.sh",
                               "allowed_tools": ["Read", "Bash(npm test*)"]})
    listed = env.rules()
    assert [r["pattern"] for r in listed["rules"]] == ["Read", "Bash(npm test*)"], listed
    for rule in listed["rules"]:
        assert rule["source"] == "manifest", rule
        assert rule["revocable"] is False, rule
        assert rule["granted_iso"] is None, (
            f"a manifest rule was never granted, so it has no grant time: {rule}")

    r = env.srv.delete(f"/api/permissions/rules/{listed['rules'][0]['rule_id']}")
    assert r.status_code == 409, r.text
    body = r.json()["error"]
    assert body["code"] == "not_revocable", body
    assert ".factory/factory.json" in json.dumps(body), (
        f"the refusal has to name the file that declares it: {body}")

    assert len(env.rules()["rules"]) == 2, "the refused revoke removed it anyway"
    assert not rules_file(env.root).exists(), (
        "a refused revoke wrote a policy file")

    # and an unknown rule is a plain 404, not a 409
    assert env.srv.delete("/api/permissions/rules/rule-nosuchrule").status_code == 404
    env.unchanged()


# ── 5 ────────────────────────────────────────────────────────────────────────────────────────

def test_corrupt_rules_file_fails_closed(permenv):
    """An unparseable `.factory/permissions.json` yields an EMPTY rule set and asks about
    everything. Closed, never open.

    Half a policy salvaged out of a broken file is a permission nobody granted.
    """
    env = permenv.build(
        agent={"command": "/bin/sh agent.sh"},
        permissions='{"version": 1, "rules": [{"pattern": "Bash(echo hi)"  \n')

    listed = env.rules()
    assert listed["rules"] == [], (
        f"a corrupt policy file produced rules: {listed['rules']}")
    assert listed["warnings"], (
        "the file could not be read and nothing said so; a silent empty policy and a silent "
        "salvaged one look identical")
    assert any(".factory/permissions.json" in json.dumps(w) for w in listed["warnings"]), listed

    lane = env.claim()
    c = env.subscribed(lane)
    ask = Ask(env.srv, lane, "Bash", {"command": "echo hi"})
    m6 = valid(c.wait_for("permission.request"))
    assert m6["rule_that_would_be_written"] == "Bash(echo hi)", m6
    assert "nearest_miss" not in m6, (
        "a rule out of the corrupt file was used to compute a near miss, so it was read after all")
    env.answer(m6["request_id"], "once")
    ask.result()

    # a pattern that does not parse is not a rule either — same rule, one level down
    env2 = permenv.build(
        agent={"command": "/bin/sh agent.sh", "allowed_tools": ["Bash(unbalanced", ""]})
    listed2 = env2.rules()
    assert listed2["rules"] == [], (
        f"an unparseable pattern was loaded as a rule: {listed2['rules']}")
    assert listed2["warnings"], listed2

    env.unchanged()
    env2.unchanged()


# ── 6 ────────────────────────────────────────────────────────────────────────────────────────

def test_a_request_never_times_out(permenv):
    """`expires_at` is null and stays null, and a request left alone stays answerable.

    A request that times out into a denial produces a run that failed for a reason nobody saw —
    which is the failure this whole feature is a response to.
    """
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(env.lane, "ready")
    c = env.subscribed(lane)

    ask = Ask(env.srv, lane, "Bash", {"command": "sleep 1"})
    m6 = valid(c.wait_for("permission.request"))
    assert m6["expires_at"] is None, m6

    # the request's own transcript entry — the thing that makes it survive a page reload — and
    # then nothing else, ever, because there is no timer in this feature at all
    entry = valid(c.wait_for("transcript"))["entry"]
    assert entry["kind"] == "permission_request", entry
    assert entry["request_id"] == m6["request_id"], entry

    assert ask.waiting(4.0), "the request answered itself"
    # A `lane.state` frame reports the lane becoming `blocked` — a consequence of the REQUEST and
    # not of any clock (SRV-FEAT-011's M2), and it can arrive either side of the entry above.
    # What must never arrive is anything a timer could have produced, so the assertion is about
    # everything else; the state frames are checked for what they say instead.
    later = [valid(f) for f in c.drain(0.5)]
    assert [f["m"] for f in later if f["m"] != "lane.state"] == [], (
        f"something was emitted about a request nobody touched: {later}")
    assert all(f["state"]["process"] == "blocked" for f in later if f["m"] == "lane.state"), (
        f"the lane's state moved while nobody answered: {later}")
    row = env.lane_state(lane)
    assert row["state"]["process"] == "blocked", (
        f"the lane stopped being blocked without anybody answering: {row['state']}")
    assert row["state"]["blocked"]["request_id"] == m6["request_id"], row["state"]

    r = env.answer(m6["request_id"], "once")
    assert r.status_code == 200, (
        f"the request expired while nobody was looking: {r.text}")
    assert ask.result()["allowed"] is True

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 7 ────────────────────────────────────────────────────────────────────────────────────────

def test_blocked_is_the_lane_state(permenv):
    """A lane awaiting an answer is `blocked`, with the reason and the request, and it counts
    toward `needs_you`.

    `SPEC.md` F18: a correct agent waiting on a person is not a stalled one, so `blocked` and
    `running · silent` must not share a state or a colour.
    """
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(env.lane, "ready")
    c = env.subscribed(lane)

    before = env.lane_state(lane)
    assert before["state"]["process"] == "running", before
    assert before["state"].get("blocked") is None, before
    assert before["needs_you"] is False, before

    ask = Ask(env.srv, lane, "Bash", {"command": "echo blocked"})
    m6 = valid(c.wait_for("permission.request"))

    row = env.lane_state(lane)
    assert row["state"]["process"] == "blocked", (
        f"a lane waiting on a person reported {row['state']['process']!r}")
    assert row["state"]["blocked"] == {"reason": "permission",
                                       "request_id": m6["request_id"]}, row["state"]
    assert row["needs_you"] is True, (
        f"a blocked lane does not count toward needs_you: {row}")
    assert row["state"]["motion"]["value"] is None or row["state"]["motion"]["value"] != "silent", (
        f"blocked was flattened into silence: {row['state']}")

    env.answer(m6["request_id"], "once")
    assert ask.result()["allowed"] is True

    after = env.lane_state(lane)
    assert after["state"]["process"] == "running", after
    assert after["state"].get("blocked") is None, after
    assert after["needs_you"] is False, after

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    env.unchanged()


# ── 8 · NEGATIVE (mandatory) ─────────────────────────────────────────────────────────────────

def test_an_answer_for_a_dead_run_is_gone_not_applied(permenv):
    """A request whose run has since died answers `410 gone`, the transcript entry is marked
    voided rather than left waiting for ever, and **no rule is written**.

    Answering `always` into a dead run would grant a standing permission nobody meant to grant:
    it would sit in `.factory/permissions.json`, apply to every future run, and be committed. The
    two-tab race that produces it is ordinary — one tab shows a request from a run the other one
    has already killed.
    """
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    env.wait_log(env.lane, "ready")
    c = env.subscribed(lane)
    everyone = env.listening()

    ask = Ask(env.srv, lane, "Bash", {"command": "echo into-the-void"})
    m6 = valid(c.wait_for("permission.request"))
    assert m6["rule_that_would_be_written"] == "Bash(echo into-the-void)", m6

    # the other tab kills the run
    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])

    # the request is voided rather than left waiting for ever
    resolved = valid(everyone.wait_for("permission.resolved"))
    assert resolved["request_id"] == m6["request_id"], resolved
    assert resolved["decision"] == "voided", resolved
    entry = valid(c.wait_for("transcript", timeout=10))["entry"]
    while entry.get("kind") != "permission_decision":
        entry = valid(c.wait_for("transcript", timeout=10))["entry"]
    assert entry["decision"] == "voided", entry
    assert entry["request_id"] == m6["request_id"], entry
    assert ask.result()["allowed"] is False, ask.result()

    # ...and THIS is the assertion the whole case exists for
    r = env.answer(m6["request_id"], "always")
    assert r.status_code == 410, (
        f"answering into a dead run was accepted with {r.status_code}: {r.text}")
    assert r.json()["error"]["code"] == "gone", r.json()

    assert not rules_file(env.root).exists(), (
        f"A STANDING PERMISSION NOBODY MEANT TO GRANT was written for a run that was already "
        f"dead:\n{rules_file(env.root).read_text()}")
    assert env.rules()["rules"] == [], (
        f"the rule was not on disk but it is in force: {env.rules()['rules']}")
    assert untracked_set(env.root) == set(), (
        f"the tool created a file answering a dead run: {untracked_set(env.root)}")

    env.unchanged()


# ── the policy, E21 ──────────────────────────────────────────────────────────────────────────

def test_the_policy_defaults_from_the_manifest_and_never_guesses(permenv):
    """The manifest's `agent.permission_mode` is the default; a value that is none of the three
    is not guessed at, and E21 refuses one too."""
    env = permenv.build(agent={"command": "/bin/sh agent.sh", "permission_mode": "edit_free"})
    listed = env.rules()
    assert listed["policy"] == "edit_free", listed
    assert listed["policy_source"] == "manifest", listed

    lane = env.claim()
    c = env.subscribed(lane)
    free = Ask(env.srv, lane, "Read", {"file_path": "README.md"})
    answer = free.result(timeout=10)
    assert answer["allowed"] is True and answer["asked"] is False, answer
    assert answer["reason"] == "policy", answer

    asked = Ask(env.srv, lane, "Bash", {"command": "echo not-an-edit"})
    m6 = valid(c.wait_for("permission.request"))
    env.answer(m6["request_id"], "once")
    asked.result()

    everyone = env.listening()
    r = env.srv.put("/api/permissions/policy", json={"policy": "never_ask"})
    assert r.status_code == 200, r.text
    assert r.json() == {"policy": "never_ask", "applies_to": "new_runs"}, r.json()
    valid(everyone.wait_for("rules"))
    assert env.rules()["policy_source"] == "session", env.rules()

    bad = env.srv.put("/api/permissions/policy", json={"policy": "sure-why-not"})
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["code"] == "bad_policy", bad.json()
    assert "ask_all" in json.dumps(bad.json()), bad.json()
    assert env.rules()["policy"] == "never_ask", "a refused policy change was applied anyway"

    # the manifest is never rewritten by a click in a UI
    manifest = json.loads((Path(env.root) / ".factory" / "factory.json").read_text())
    assert manifest["agent"]["permission_mode"] == "edit_free", manifest
    env.unchanged()


def test_an_unknown_permission_mode_falls_back_to_asking(permenv):
    env = permenv.build(agent={"command": "/bin/sh agent.sh", "permission_mode": "yolo"})
    listed = env.rules()
    assert listed["policy"] == "ask_all", (
        f"an unrecognised permission_mode was interpreted rather than refused: {listed}")
    assert listed["warnings"], listed
    env.unchanged()


def test_answering_the_same_request_twice_is_the_ordinary_two_tab_race(permenv):
    """The second answer is a 404 that says what happened, not a second decision."""
    env = permenv.build()
    lane = env.claim()
    c = env.subscribed(lane)

    ask = Ask(env.srv, lane, "Bash", {"command": "echo twice"})
    m6 = valid(c.wait_for("permission.request"))
    assert env.answer(m6["request_id"], "once").status_code == 200
    ask.result()

    second = env.answer(m6["request_id"], "always")
    assert second.status_code == 404, second.text
    assert second.json()["error"]["code"] == "not_found", second.json()
    assert second.json()["error"]["detail"]["state"] == "answered", second.json()
    assert not rules_file(env.root).exists(), (
        "the second answer to an answered request wrote a rule")

    unknown = env.answer("req-nosuchrequest", "always")
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["detail"]["state"] == "unknown", unknown.json()
    assert not rules_file(env.root).exists()

    bad = env.answer(m6["request_id"], "maybe")
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["code"] == "bad_decision", bad.json()
    env.unchanged()


def test_always_never_widens_the_rule_it_shows(permenv):
    """A command containing the pattern language's one metacharacter is written literally.

    Without this, granting `rm *.tmp` once would quietly grant every `rm` of every path that
    happens to end in `.tmp` — a wider rule than the one the person read and agreed to.
    """
    env = permenv.build()
    lane = env.claim()
    c = env.subscribed(lane)

    ask = Ask(env.srv, lane, "Bash", {"command": "rm build/*.tmp"})
    m6 = valid(c.wait_for("permission.request"))
    shown = m6["rule_that_would_be_written"]
    env.answer(m6["request_id"], "always")
    ask.result()

    written = json.loads(rules_file(env.root).read_text())["rules"][0]["pattern"]
    assert written == shown, (
        f"the tool showed {shown!r} and wrote {written!r}; `always` writes the rule it showed")

    # the granted rule covers the command it was granted for...
    same = Ask(env.srv, lane, "Bash", {"command": "rm build/*.tmp"})
    assert same.result(timeout=10)["asked"] is False, "the rule it just wrote does not match"

    # ...and NOT a wider family of them
    wider = Ask(env.srv, lane, "Bash", {"command": "rm build/secrets.tmp"})
    m6b = valid(c.wait_for("permission.request"))
    assert m6b["tool"] == "Bash", m6b
    env.answer(m6b["request_id"], "deny")
    assert wider.result()["allowed"] is False
    env.unchanged()


# ── SRV-FEAT-057 ═════════════════════════════════════════════════════════════════════════════
#
# Everything above this line drives `permissions::ask` DIRECTLY, through a test hook. That is why
# `M6`, `PermissionsPage`, `E20`, `E23` and this whole module could be built, tested and merged for
# a request THAT COULD NOT PHYSICALLY ARRIVE: the CLI in a lane had no channel back, so the feature
# was unreachable rather than idle.
#
# The mechanism was MEASURED before it was designed — `--permission-prompt-tool` is not in
# `claude 2.1.220 --help` at all, and what exists is a `PreToolUse` hook declared in a settings
# file the CLI reads out of `CLAUDE_CONFIG_DIR`
# (`fixtures/cli-permission-2026-07-31-pretooluse-hook-vetoes-a-call.json`).
#
# These cases exercise the CHANNEL: the endpoint the hook posts to, the seed that puts the hook in
# a lane, and the environment that tells it where to knock. What the CLI does with a decision is in
# the capture, because asserting it here would mean spending a live model's turn on a fact already
# recorded.


def _hook_asks(env, run, tool="Bash", command="rm -rf /"):
    """A request arriving THE WAY A REAL AGENT'S HOOK SENDS IT — E24, token and all."""
    return env.srv.post("/api/permission", json={
        "token": run["token"] if "token" in run else env.token(),
        "tool": tool, "input": {"command": command}, "tool_use_id": "toolu_x"})


def test_a_real_agent_asks_and_the_tool_is_the_one_that_answers(permenv):
    """THE assertion whose absence is the whole ticket: a request through the CHANNEL, not `ask`.

    The token is the run's own, exactly as E22's progress endpoint takes one — valid while the run
    is live and never after it.
    """
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    token = env.token()

    c = env.subscribed(lane)
    posted = []

    def ask():
        posted.append(env.srv.post("/api/permission", json={
            "token": token, "tool": "Bash",
            "input": {"command": "npm run gate"}, "tool_use_id": "toolu_1"}))

    t = threading.Thread(target=ask, daemon=True)
    t.start()

    frame = valid(c.wait_for("permission.request"))
    assert frame["tool"] == "Bash", frame
    assert frame["lane"] == lane, frame
    request_id = frame["request_id"]

    env.answer(request_id, "once")
    t.join(timeout=20)
    assert posted, "the endpoint never answered the hook"
    assert posted[0].status_code == 200, posted[0].text
    assert posted[0].json()["decision"] == "allow", posted[0].json()
    assert posted[0].json()["asked"] is True, posted[0].json()

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


def test_a_denial_reaches_the_agent_in_the_clis_own_vocabulary(permenv):
    """The other direction, and in the words the hook hands straight back to the CLI.

    `allow` / `deny` are the CLI's own `permissionDecision` values. `ask` is deliberately not one
    of them: this endpoint answers only when the answer exists, so there is nothing left to ask by
    the time it replies.
    """
    env = permenv.build()
    lane = env.claim()
    run = env.start(lane)
    token = env.token()

    c = env.subscribed(lane)
    posted = []
    t = threading.Thread(target=lambda: posted.append(env.srv.post(
        "/api/permission", json={"token": token, "tool": "Bash",
                                 "input": {"command": "rm -rf /"}, "tool_use_id": "t2"})),
                         daemon=True)
    t.start()

    frame = valid(c.wait_for("permission.request"))
    env.answer(frame["request_id"], "deny")
    t.join(timeout=20)

    assert posted and posted[0].status_code == 200, posted
    assert posted[0].json()["decision"] == "deny", posted[0].json()

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})


def test_a_lane_whose_policy_allows_it_asks_nobody(permenv):
    """NEGATIVE (mandatory). A channel that asks about everything is as unusable as one that asks
    about nothing — and it is the easy accident, because it makes every case above pass.

    Under `never_ask` the same call is allowed with no `M6` frame and no pending request.
    """
    env = permenv.build(agent={"command": "/bin/sh agent.sh", "permission_mode": "never_ask"})
    lane = env.claim()
    run = env.start(lane)
    token = env.token()

    c = env.subscribed(lane)
    r = env.srv.post("/api/permission", json={
        "token": token, "tool": "Bash", "input": {"command": "npm test"}, "tool_use_id": "t3"})

    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "allow", r.json()
    assert r.json()["asked"] is False, (
        f"a policy that never asks produced a request anyway: {r.json()}")
    assert not [f for f in c.drain(1.5) if f.get("m") == "permission.request"], (
        "M6 fired for a call the standing policy already allowed")

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})


def test_a_token_that_no_live_run_holds_is_refused(permenv):
    """NEGATIVE (mandatory). The door is the run's, not the loopback interface's.

    Anything on 127.0.0.1 can reach this endpoint, so an unknown token and a malformed one produce
    the SAME bytes — an endpoint that answers differently tells a stranger what a token looks like.
    """
    env = permenv.build()
    env.claim()

    unknown = env.srv.post("/api/permission", json={
        "token": "0" * 32, "tool": "Bash", "input": {"command": "id"}})
    malformed = env.srv.post("/api/permission", json={
        "token": "nonsense", "tool": "Bash", "input": {"command": "id"}})

    assert unknown.status_code == 404, unknown.text
    assert malformed.status_code == 404, malformed.text
    assert unknown.text == malformed.text, (
        f"an unknown token and a malformed one answer differently:\n{unknown.text}\n{malformed.text}")


def test_the_lane_is_seeded_with_the_hook_and_the_settings_that_declare_it(permenv):
    """The channel has to BE THERE, in the lane, for any of the above to mean anything.

    Measured, not assumed: the CLI reads `settings.json` out of `CLAUDE_CONFIG_DIR` with no
    `--settings` on the argv, so the seed is the whole of the wiring.

    The manifest declares a `permission_mode`, which is the opt-in — see the negative below for
    why that is not a detail.
    """
    env = permenv.build(agent={"command": "/bin/sh agent.sh", "permission_mode": "ask_all"})
    lane = env.claim()
    run = env.start(lane)

    cfg = Path(env.agent_config_dir(lane))
    settings = cfg / "settings.json"
    hook = cfg / "permission-hook.py"
    assert settings.is_file(), f"no settings.json in the lane's configuration directory: {cfg}"
    assert hook.is_file(), f"no permission hook in {cfg}"

    declared = json.loads(settings.read_text())
    entries = declared["hooks"]["PreToolUse"]
    assert entries and entries[0]["hooks"][0]["type"] == "command", declared
    assert "permission-hook.py" in entries[0]["hooks"][0]["command"], declared

    # ...and the hook fails OPEN when it cannot reach the tool, which is a decision: a factory
    # that is down is not a person saying no, and the two must not look the same to a run.
    body = hook.read_text()
    assert "sys.exit(0)" in body, body[:400]

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


def test_a_repository_that_declares_no_policy_gets_no_hook(permenv):
    """NEGATIVE (mandatory), and it is the one that protects every lane that already works.

    A hook that can hold a tool call can WEDGE A LANE NOBODY IS WATCHING — measured: seeded
    unconditionally, with the default policy `ask_all`, every existing lane blocked on its first
    call (`process: blocked`, `reason: permission`, a real `request_id`) and four cases in this
    suite went from green to hung, waiting for a person who was never going to arrive.

    So the channel is opt-in, and `agent.permission_mode` is the opt-in. A repository that
    declares none has never asked this tool to arbitrate, and nothing about its lanes changes.
    """
    env = permenv.build()                    # no permission_mode: the default this repo ships
    lane = env.claim()
    run = env.start(lane)

    cfg = Path(env.agent_config_dir(lane))
    assert not (cfg / "settings.json").exists(), (
        f"a repository that declared no policy had a permission hook seeded into its lane — every "
        f"lane on the machine now blocks on its first tool call: {sorted(p.name for p in cfg.iterdir())}")
    assert not (cfg / "permission-hook.py").exists(), sorted(p.name for p in cfg.iterdir())

    # ...and the trust and the credential SRV-FIX-052 put there are untouched by any of this.
    assert (cfg / ".claude.json").is_file(), sorted(p.name for p in cfg.iterdir())

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


# ── SRV-FEAT-057 · END TO END, with the real CLI ══════════════════════════════════════════════
#
# The five cases above prove the CHANNEL — the endpoint, the seed, the token, the policy. These
# two prove the LOOP, and they are the ones §1 asked for by name: grant it and the agent's tool
# call **actually runs**; deny it and the side effect is **absent**. A side effect the tool can
# verify from outside the agent, which is `_TEMPLATE.md`'s rule and has now been the answer four
# times in four days.
#
# They cost a live model's turn each, which is a real cost this repository has just learned to
# respect (`SRV-FIX-042`). They earn it: everything cheaper asserts the tool's half of a contract
# whose other half is a program nobody here wrote.

REAL_AGENT_PROMPT = (
    "Use the Bash tool to run exactly: printf 'OK' > PROOF.txt\n"
    "Do nothing else. Do not use Write or any other tool.")


def _answer_everything(conn, env, decision, deadline_s=120):
    """Answer every `M6` this run raises, with the decision under test, until it stops raising them.

    Off the socket, which is the path a person's browser uses. The seeded hook declares
    `matcher: ""` — every tool asks — because which calls need asking about is the standing
    policy's business and not the settings file's, so a real turn raises several and a case that
    answered only the first would hang on the second.
    """
    seen, deadline = [], time.time() + deadline_s
    while time.time() < deadline:
        try:
            frame = conn.wait_for("permission.request", timeout=25)
        except AssertionError:
            return seen                      # nothing more is being asked
        seen.append(frame["request_id"])
        env.answer(frame["request_id"], decision)
    return seen


@pytest.mark.skipif(cli_conformance.NO_REAL_CLI is not None,
                    reason=cli_conformance.NO_REAL_CLI or "")
def test_the_answer_reaches_the_agent_and_the_tool_call_then_runs(permenv):
    """Granted, and the file the agent was told to write IS THERE.

    Not a frame saying it was allowed — the file, on disk, in the lane's worktree, put there by a
    process the tool did not run itself.
    """
    # The credential directory is declared, exactly as `test_lane_home.py` declares it: without
    # it the CLI comes back "Not logged in" and dies before it can ask anything, which looks
    # identical to a channel that is not wired.
    env = permenv.build(agent={"command": cli_conformance.CLAUDE,
                               "credentials_dir": str(REAL_CREDENTIALS),
                               "permission_mode": "ask_all"})
    lane = env.claim()
    c = env.subscribed(lane)
    run = env.start(lane, prompt=REAL_AGENT_PROMPT)

    answered = _answer_everything(c, env, "once")
    assert answered, "the real agent raised no permission request at all — the channel is not wired"

    proof = Path(env.lane["worktree"]) / "PROOF.txt"
    assert proof.is_file(), (
        f"the request was granted and the tool call never ran: {sorted(Path(env.lane['worktree']).iterdir())[:12]}")
    assert proof.read_text() == "OK", proof.read_text()

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


@pytest.mark.skipif(cli_conformance.NO_REAL_CLI is not None,
                    reason=cli_conformance.NO_REAL_CLI or "")
def test_a_denial_reaches_the_agent_and_the_call_does_not_run(permenv):
    """Denied, and the side effect is ABSENT — the assertion a frame cannot make.

    A denial is an answer and not a kill (SPEC.md E23), so the run is expected to carry on and say
    it was refused. What must not exist is the file.
    """
    # The credential directory is declared, exactly as `test_lane_home.py` declares it: without
    # it the CLI comes back "Not logged in" and dies before it can ask anything, which looks
    # identical to a channel that is not wired.
    env = permenv.build(agent={"command": cli_conformance.CLAUDE,
                               "credentials_dir": str(REAL_CREDENTIALS),
                               "permission_mode": "ask_all"})
    lane = env.claim()
    c = env.subscribed(lane)
    run = env.start(lane, prompt=REAL_AGENT_PROMPT)

    answered = _answer_everything(c, env, "deny")
    assert answered, "the real agent raised no permission request at all — the channel is not wired"

    proof = Path(env.lane["worktree"]) / "PROOF.txt"
    assert not proof.exists(), (
        "the request was DENIED and the tool call ran anyway — the veto is decorative")

    env.srv.post(f"/api/lanes/{lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


# ── SRV-FEAT-230 · the four registrations append, so a second subscriber REACHES the event ───

def test_a_second_subscriber_reaches_a_permission_request(permenv):
    """`permissions::Store::onRequest` and its three siblings used to REPLACE: a second
    registration discarded the first, silently — no compile error, no runtime error, one screen
    that stopped updating.

    With `FACTORY_TEST_HOOKS` set there are TWO subscribers on the request event, registered in
    order and doing DIFFERENT observable things: the product's publishes M6 to `/ws` and marks the
    lane blocked; the hook's appends to a list its route reads back. One ask must produce BOTH.

    Under the old `requestHandler_ = std::move(handler)` this is deterministically red — the hook
    registers second, so M6 disappears and only the list is filled. That is the whole point of
    making the two subscribers disagree: with `first-only`, `last-only` and `all` producing three
    distinguishable outcomes, the case can tell which one the code implements.
    """
    env = permenv.build()
    lane = env.claim()
    c = env.subscribed(lane)

    before = env.srv.get("/api/_test/permission-subscribers")
    assert before.status_code == 200, before.text
    assert before.json()["subscribers"] == 2, (
        f"the event carries {before.json()['subscribers']} subscriber(s); the product's and the "
        f"hook's are both registered, so a registry that replaces would report 1")

    ask = Ask(env.srv, lane, "Bash", {"command": "npm run build"})
    m6 = valid(c.wait_for("permission.request"))

    seen = env.srv.get("/api/_test/permission-subscribers").json()["seen"]
    assert seen == [f"{lane}/{m6['request_id']}"], (
        f"the second subscriber did not reach the event: {seen}")

    assert env.answer(m6["request_id"], "once").status_code == 200
    assert ask.result()["allowed"] is True
    env.unchanged()


# ═══════════════════════════════════════════════════════════════════════════════════════════
# SRV-FEAT-499 — after the stage the manifest names, a file §1 names as evidence asks
# ═══════════════════════════════════════════════════════════════════════════════════════════
#
# Every case below drives `E24` with the STAND-IN (`SRV-FEAT-057`'s ledger row: a criterion that
# asserts a live model chose to make a tool call reads green or red depending on the run), and it
# drives the real endpoint the agent's own hook posts to — `POST /api/permission`, with the token
# the tool put in the run's environment — rather than the `_test` hook, which takes a lane name
# nobody outside this suite has.

FROZEN_STAGES = ["SPEC", "TEST", "IMPL", "GATE"]

# The two evidence paths live in two different workspaces' directories on purpose: the freeze is
# a fact about §1's words, not about the workspace the ticket is claimed for.
EVIDENCE_TICKET = """---
id: TCK-EV
title: a ticket whose criteria name what evidences them
status: todo
---

## 1. Request

- **Pass criteria**

  ```
  1. A person opens the server surface and the row it drew is there.
     Evidence: server/tests/test_x.py::test_a
  2. A person opens the client surface and the same row is there.
     Evidence: client/web/tests/y.test.tsx::renders
  ```
"""

# The same document with the evidence lines taken out, and nothing else changed.
# The same document with the prefix taken off the two lines and NOTHING else changed: both files
# are still named, in prose, inside the criteria. A ticket that merely mentions a path has not
# said it is evidence, and a parser that read the paths rather than the PREFIX would freeze them.
NO_EVIDENCE_TICKET = """---
id: TCK-NOEV
title: a ticket whose criteria name files without calling any of them evidence
status: todo
---

## 1. Request

- **Pass criteria**

  ```
  1. A person opens the server surface and the row it drew is there, the way
     server/tests/test_x.py::test_a describes it.
  2. A person opens the client surface and the same row is there, the way
     client/web/tests/y.test.tsx::renders describes it.
  ```
"""


class AskE24:
    """One tool use, put to the endpoint the agent's own hook puts it to, on a thread.

    The token is the one the tool injected into the run — read out of what the STAND-IN saw, so
    a case cannot pass by asking on behalf of a run that does not exist.
    """

    def __init__(self, srv, token, tool, inp):
        self.srv, self.token, self.tool, self.input = srv, token, tool, inp
        self.done = threading.Event()
        self.response = None
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.response = requests.post(
                self.srv.base + "/api/permission",
                json={"token": self.token, "tool": self.tool, "input": self.input}, timeout=90)
        except Exception as exc:                       # noqa: BLE001 — reported, not swallowed
            self.error = exc
        finally:
            self.done.set()

    def result(self, timeout=30):
        assert self.done.wait(timeout), f"the ask for {self.tool} never came back in {timeout}s"
        assert self.error is None, self.error
        assert self.response.status_code == 200, self.response.text
        return self.response.json()

    def settled(self, seconds=0.5):
        return self.done.wait(seconds)


@pytest.fixture
def frozenenv(tmp_path, serve):
    """A repository that declares its evidence convention, a stand-in, a lane and a run."""
    made = []
    started = []
    conns = []
    lanes = []

    class Env:
        def build(self, *, policy="never_ask", evidence=True, stages=FROZEN_STAGES):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent={"command": "/bin/sh agent.sh",
                                     "permission_mode": policy})
            (root / "agent.sh").write_text(AGENT_SH)

            manifest = json.loads((root / ".factory" / "factory.json").read_text())
            manifest["stages"] = stages
            if evidence:
                manifest["evidence"] = {"line_prefix": "Evidence:", "frozen_after": "TEST"}
            (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))

            (root / "docs" / "tickets" / "TCK-EV.md").write_text(EVIDENCE_TICKET)
            (root / "docs" / "tickets" / "TCK-NOEV.md").write_text(NO_EVIDENCE_TICKET)
            for rel in ("server/tests/test_x.py", "server/tests/test_x_helpers.py",
                        "client/web/tests/y.test.tsx"):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("the case this criterion names\n")

            git(root, "add", "-A")
            git(root, "commit", "-m", "the evidence convention, the stand-in and the cases")
            self.root = root
            self.baseline = tracked_status(root)
            made.append((root, self.baseline))
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        def claim(self, ticket="TCK-EV", workspace="core"):
            r = self.srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})
            assert r.status_code == 201, r.text
            self.lane = r.json()["lane"]
            self.worktree = Path(r.json()["worktree"])
            lanes.append((self.srv, self.lane))
            return self.lane

        def start(self, lane=None):
            lane = lane or self.lane
            r = self.srv.post(f"/api/lanes/{lane}/runs", json={})
            assert r.status_code == 201, r.text
            run = r.json()
            started.append(run["pid"])
            self.run = run
            return run

        def log(self, worktree=None):
            try:
                return ((worktree or self.worktree) / "agent-log.txt").read_text()
            except OSError:
                return ""

        def wait_log(self, needle, worktree=None, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                text = self.log(worktree)
                if needle in text:
                    return text
                time.sleep(0.05)
            raise AssertionError(f"{needle!r} never reached the agent log:\n{self.log(worktree)}")

        def token(self, worktree=None):
            for line in self.wait_log("ready", worktree).splitlines():
                if line.startswith("token="):
                    return line.split("=", 1)[1]
            raise AssertionError(f"the run saw no token:\n{self.log(worktree)}")

        def report(self, stage, token=None):
            r = self.srv.post("/api/progress",
                              json={"token": token or self.token(), "stage": stage})
            assert r.status_code == 202, r.text
            return r.json()

        def ask(self, tool, inp, token=None):
            return AskE24(self.srv, token or self.token(), tool, inp)

        def subscribed(self, lane=None):
            c = self.srv.ws()
            assert valid(c.recv())["m"] == "hello"
            c.send({"m": "subscribe", "scope": {"lane": lane or self.lane}})
            valid(c.wait_for("snapshot"))
            conns.append(c)
            return c

        def rules(self):
            r = self.srv.get("/api/permissions/rules")
            assert r.status_code == 200, r.text
            return r.json()

        def answer(self, request_id, decision):
            r = self.srv.post("/api/permissions/decisions",
                              json={"request_id": request_id, "decision": decision})
            assert r.status_code == 200, r.text
            return r.json()

    yield Env()

    for c in conns:
        c.close()
    # These runs sit in a `read` on their stdin for as long as the lane lives; the cases here
    # never say `bye`, because what they are about is a decision taken while the run is alive.
    # So the teardown ends them the way E17 does, and only then asks whether they went.
    for srv, lane in lanes:
        srv.post(f"/api/lanes/{lane}/kill", json={})
    for pid in started:
        assert_reaped(pid, within=20, what="(checked in teardown)")
    for root, before in made:
        assert tracked_status(root) == before, (
            f"the main checkout at {root} changed:\nbefore:\n{before}\nafter:\n"
            f"{tracked_status(root)}")


def path_in(worktree, rel):
    """The evidence path as the agent would spell it — absolute, inside its own worktree."""
    return str(Path(worktree) / rel)


# ── 499 · 1 — before the stage, nothing changes ──────────────────────────────────────────────

def test_before_the_frozen_stage_nothing_changes(frozenenv):
    """§1 criterion 2. A run that has not yet reported the stage the manifest names is decided by
    the standing policy exactly as it was before this ticket existed.

    The fixture forces the two possible sources apart: the policy is **allow-all**, so a freeze
    firing early would be visible as `asked` rather than hidden behind an answer the policy was
    going to give anyway. The ask-all half is the same call under the other policy, and there the
    request frame is read and asserted to carry no `frozen_by` — an absence nobody looks at is an
    absence nobody has measured.
    """
    env = frozenenv.build(policy="never_ask")
    env.claim("TCK-EV")
    env.start()
    env.wait_log("ready")
    evidence = path_in(env.worktree, "server/tests/test_x.py")

    # A stage IS reported first, and it is not the one the manifest names. Without this the case
    # holds no stage report at all, and a freeze that armed on ANY report would pass it — measured
    # against exactly that break, which sailed through the first version of this case.
    env.report("SPEC")

    ask = env.ask("Edit", {"file_path": evidence})
    out = ask.result()
    assert out["decision"] == "allow" and out["asked"] is False, (
        f"the freeze fired before the stage that arms it was ever reported: {out}")
    assert out["reason"] == "policy", out

    # ...and under ask-all the same call is asked, and the frame carries nothing new.
    other = frozenenv.build(policy="ask_all")
    other.claim("TCK-EV")
    other.start()
    other.wait_log("ready")
    other.report("SPEC")
    c = other.subscribed()
    other_evidence = path_in(other.worktree, "server/tests/test_x.py")
    asking = other.ask("Edit", {"file_path": other_evidence})
    m6 = valid(c.wait_for("permission.request"))
    assert "frozen_by" not in m6, (
        f"a request nothing froze was labelled as frozen: {m6}")
    other.answer(m6["request_id"], "once")
    assert asking.result()["decision"] == "allow", asking.result()


# ── 499 · 2 — after the stage, every time, and no grant answers it ───────────────────────────

def test_after_the_frozen_stage_an_evidence_path_asks_every_time(frozenenv):
    """§1 criterion 3. The run reports the stage, and from then on the operator decides each
    touch of a file §1 named — once per request, and `always` does not buy the next one.

    The policy is allow-all throughout, so every `asked` below is the freeze and nothing else.
    """
    env = frozenenv.build(policy="never_ask")
    env.claim("TCK-EV")
    env.start()
    env.wait_log("ready")
    c = env.subscribed()
    evidence = path_in(env.worktree, "server/tests/test_x.py")

    env.report("TEST")

    first = env.ask("Edit", {"file_path": evidence})
    m6 = valid(c.wait_for("permission.request"))
    assert m6["frozen_by"] == {"ticket_id": "TCK-EV", "criterion": 1}, (
        f"the request does not name the criterion whose evidence this file is: {m6}")
    assert not first.settled(0.5), (
        "the request was answered without anybody being asked, so the frame was decoration")

    # `always` — the grant is written, and it does not answer this file again.
    granted = env.answer(m6["request_id"], "always")
    assert first.result()["decision"] == "allow", first.result()
    listed = [r for r in env.rules()["rules"] if r.get("rule_id") == granted["rule_id"]]
    assert listed, f"E11 does not list the rule `always` wrote: {env.rules()}"

    second = env.ask("Edit", {"file_path": evidence})
    m6b = valid(c.wait_for("permission.request"))
    assert m6b["request_id"] != m6["request_id"], m6b
    assert m6b["frozen_by"] == {"ticket_id": "TCK-EV", "criterion": 1}, m6b
    assert [r for r in env.rules()["rules"] if r.get("rule_id") == granted["rule_id"]], (
        "the grant vanished; the freeze must leave the standing policy alone and merely outrank "
        "it for this one path")
    env.answer(m6b["request_id"], "once")
    assert second.result()["decision"] == "allow", second.result()


# ── 499 · 3 — every evidence line, every writing tool, and reads left alone ───────────────────

def test_every_evidence_line_freezes_and_reads_do_not(frozenenv):
    """§1 criterion 4. The frozen set is derived from EVERY line of §1 carrying the declared
    prefix — the two here sit in two different workspaces' directories — and the match is on the
    argument the rule engine already extracts, so a `Bash` command that names the file and a
    `Write` at it are asked as well.

    Reading is not touching: `Read` and `Grep` over the same file are answered by the policy.
    """
    env = frozenenv.build(policy="never_ask")
    env.claim("TCK-EV")
    env.start()
    env.wait_log("ready")
    c = env.subscribed()
    server_case = path_in(env.worktree, "server/tests/test_x.py")
    client_case = path_in(env.worktree, "client/web/tests/y.test.tsx")

    env.report("TEST")

    frozen = [
        ("Bash", {"command": f"sed -i 's/assert True/assert False/' {server_case}"}, 1),
        ("Write", {"file_path": server_case}, 1),
        ("Edit", {"file_path": client_case}, 2),
    ]
    for tool, inp, criterion in frozen:
        ask = env.ask(tool, inp)
        m6 = valid(c.wait_for("permission.request"))
        assert m6["tool"] == tool, m6
        assert m6["frozen_by"] == {"ticket_id": "TCK-EV", "criterion": criterion}, (
            f"{tool} on an evidence path was not attributed to the criterion that names it: {m6}")
        env.answer(m6["request_id"], "once")
        assert ask.result()["decision"] == "allow", ask.result()

    for tool, inp in (("Read", {"file_path": server_case}),
                      ("Grep", {"pattern": "assert", "path": server_case})):
        out = env.ask(tool, inp).result()
        assert out["asked"] is False and out["decision"] == "allow", (
            f"{tool} only reads the file, and the freeze stopped it: {out}")
    quiet = c.drain(0.6)
    assert [f for f in quiet if f.get("m") == "permission.request"] == [], (
        f"a read of a frozen path raised a permission request: {quiet}")


# ── 499 · 4 — the mandatory negative ─────────────────────────────────────────────────────────

def test_a_neighbouring_path_and_a_ticket_with_no_evidence_freeze_nothing(frozenenv):
    """§1 criterion 5, MANDATORY NEGATIVE, in both directions this feature can be too wide in.

    A file whose name merely STARTS with an evidence path's name is a different file, and a
    ticket whose §1 names nothing has nothing to freeze — reporting the stage in that lane leaves
    the run behaving exactly as criterion 2 describes.
    """
    env = frozenenv.build(policy="never_ask")
    env.claim("TCK-EV")
    env.start()
    env.wait_log("ready")
    env.report("TEST")

    neighbour = path_in(env.worktree, "server/tests/test_x_helpers.py")
    out = env.ask("Edit", {"file_path": neighbour}).result()
    assert out["asked"] is False and out["decision"] == "allow", (
        f"{neighbour} shares a prefix with an evidence path and is not one: {out}")

    # ...and the same file, one component along, IS frozen — so the case above is measuring the
    # boundary rather than a freeze that never armed.
    c = env.subscribed()
    frozen = env.ask("Edit", {"file_path": path_in(env.worktree, "server/tests/test_x.py")})
    m6 = valid(c.wait_for("permission.request"))
    env.answer(m6["request_id"], "once")
    assert frozen.result()["decision"] == "allow", frozen.result()

    # A ticket that names nothing.
    bare = frozenenv.build(policy="never_ask")
    bare.claim("TCK-NOEV")
    bare.start()
    bare.wait_log("ready")
    bare.report("SPEC")
    bare.report("TEST")
    for rel in ("server/tests/test_x.py", "client/web/tests/y.test.tsx"):
        out = bare.ask("Edit", {"file_path": path_in(bare.worktree, rel)}).result()
        assert out["asked"] is False and out["decision"] == "allow", (
            f"a ticket whose §1 carries no evidence line froze {rel}: {out}")
