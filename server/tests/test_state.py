"""SRV-FEAT-011 — three axes, never flattened, and one counter that means something.

`process`, `motion` and `evidence` are derived from three different inputs and never overwrite one
another (`SPEC.md` §3.2). The prototype flattened them: a stale `quiet` wrote over a live `running`,
and a lane that was working looked like a lane that had stopped. That is the bug this axis model
exists to make impossible, and `test_axes_independent` is the case that would see it come back.

`needs_you` is the exact set in `SPEC.md` §3.3 — nothing more, nothing fewer. Its five clauses are
five cases here rather than one, so a clause that silently stops working is visible; and the
mandatory negative is what keeps the counter worth reading, because a "needs you" that is always on
is a notification channel a person learns to ignore.

**Nothing here waits 130 s.** Time-dependent cases advance an injected clock, which re-arms the real
silence timers against the new remaining time — so the mechanism under test is the mechanism that
ships and only the waiting is simulated.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import jsonschema

import pytest

from test_agent import assert_reaped
from test_lane import build_repo, git
from test_permissions import Ask, tracked_status, untracked_set
# The stand-in agent that REPLAYS A COMMITTED RECORDING rather than synthesising one: a turn ends
# because a real `result` frame says so, and the cost M4 reports is the cost that CLI reported
# (SRV-FEAT-010). Frames invented here would match this session's own code and prove nothing.
from test_transcript import AGENT_SH

DOCS = Path(__file__).resolve().parents[2] / "docs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOCKET_SCHEMA = json.loads((DOCS / "technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)

# One turn of a real CLI stream: it ends with a `result` frame, which is what ends a turn and what
# carries the cost M4 reports. Invented frames would prove nothing about the CLI (SRV-FEAT-010).
ONE_TURN = FIXTURES / "cli-stream-2026-07-27-simple.jsonl"

SILENCE_S = 120                       # SPEC.md §3.3, and it is not a manifest field


def valid(frame):
    SOCKET_VALIDATOR.validate(frame)
    return frame


@pytest.fixture
def env(tmp_path, serve):
    """A repository with the stand-in agent, a server with the test hooks on, and one lane.

    Teardown asserts the main checkout's TRACKED files are byte-identical and that every run a case
    started was reaped. A case that writes a report writes it as a document, which is untracked and
    named, so the "the tool wrote nothing" assertion stays exact.
    """
    made = []
    started = []
    conns = []

    class Env:
        def build(self, **extra_env):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent={"command": "/bin/sh agent.sh"})
            (root / "agent.sh").write_text(AGENT_SH)
            (root / "one-turn.jsonl").write_text(ONE_TURN.read_text())
            git(root, "add", "-A")
            git(root, "commit", "-m", "the agent and one recording")
            self.root = root
            self.baseline = tracked_status(root)
            self.written = set()
            made.append((root, self.baseline))
            self.srv = serve(root, FACTORY_TEST_HOOKS="1", **extra_env)
            return self

        # ── the lane, the run, and what a person does to it ──────────────────────────────────
        def claim(self, ticket="TCK-001", workspace="core"):
            r = self.srv.post("/api/lanes",
                              json={"ticket_id": ticket, "workspace": workspace})
            assert r.status_code == 201, r.text
            self.lane = r.json()["lane"]
            self.ticket = ticket
            return self.lane

        def start(self, prompt=None):
            body = {} if prompt is None else {"prompt": prompt}
            r = self.srv.post(f"/api/lanes/{self.lane}/runs", json=body)
            assert r.status_code == 201, r.text
            run = r.json()
            started.append(run["pid"])
            self.run = run
            return run

        def say(self, text, lane=None):
            r = self.srv.post(f"/api/lanes/{lane or self.lane}/messages", json={"text": text})
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["status"] == "delivered", (
                f"the message was {out['status']!r}: a turn was already running, so the agent "
                f"never saw it — SPEC.md F14 holds it until an interrupt")
            return out

        def kill(self, grace_s=1, lane=None):
            return self.srv.post(f"/api/lanes/{lane or self.lane}/kill",
                                 json={"grace_s": grace_s})

        # ── what the tool says about it ──────────────────────────────────────────────────────
        def lanes(self):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            return r.json()["lanes"]

        def row(self, lane=None):
            want = lane or self.lane
            for row in self.lanes():
                if row["lane"] == want:
                    return row
            raise AssertionError(f"{want} is not in {[r['lane'] for r in self.lanes()]}")

        def counter(self):
            """The top bar's `N lanes need you` — E9's set, counted the way §4.1 counts it."""
            return sum(1 for row in self.lanes() if row["needs_you"])

        def wait_process(self, want, timeout=20, lane=None):
            deadline = time.time() + timeout
            seen = None
            while time.time() < deadline:
                seen = self.row(lane)["state"]["process"]
                if seen == want:
                    return self.row(lane)
                time.sleep(0.05)
            raise AssertionError(f"the lane never reached process {want!r} within {timeout}s; "
                                 f"it is {seen!r}")

        # ── the clock ────────────────────────────────────────────────────────────────────────
        def advance(self, seconds):
            r = self.srv.post("/api/_test/clock", json={"advance_s": seconds})
            assert r.status_code == 202, r.text
            return r.json()

        def quiet_for(self, lane=None):
            """How long the lane has been quiet, by the tool's own clock."""
            since = self.row(lane)["state"]["motion"]["since"]
            assert since, "a lane with no `since` has no silence to measure"
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            now = datetime.strptime(self.advance(0)["now"], fmt)
            return (now - datetime.strptime(since, fmt)).total_seconds()

        def quiet(self, seconds, lane=None):
            """Make the lane have been quiet for EXACTLY `seconds`, and return by how much the
            clock moved. The seconds a case's own setup took are part of the silence: a case
            that advances by a fixed amount measures a different number on a loaded machine."""
            delta = seconds - self.quiet_for(lane)
            assert delta >= 0, (
                f"the lane has already been quiet for {self.quiet_for(lane)}s, which is past "
                f"{seconds}s — this case cannot go backwards")
            self.advance(delta)
            return delta

        # ── the socket ───────────────────────────────────────────────────────────────────────
        def subscribed(self, lane=None):
            c = self.srv.ws()
            hello = valid(c.recv())
            assert hello["m"] == "hello", hello
            c.send({"m": "subscribe", "scope": {"lane": lane or self.lane}})
            valid(c.wait_for("snapshot"))
            conns.append(c)
            return c

        def frames(self, conn, name, seconds=1.5):
            return [valid(f) for f in conn.drain(seconds) if f.get("m") == name]

        def settle(self, conn, seconds=0.5, tries=8):
            """Drain until nothing has arrived for `seconds`.

            The frames a subscription's own backlog produces are not this case's subject, and a
            fixed pause is a guess about how long three async hops take. This waits for the wire
            to be quiet instead — and asserts that it eventually is, which is its own small claim
            about a lane nobody is touching.
            """
            for _ in range(tries):
                if conn.drain(seconds) == []:
                    return
            raise AssertionError(
                f"frames kept arriving for {tries * seconds}s on a lane nobody touched")

        # ── documents ────────────────────────────────────────────────────────────────────────
        def report(self, text, ticket=None):
            path = (Path(self.root) / "docs" / "reports"
                    / f"{ticket or self.ticket}-report.md")
            path.write_text(text)
            self.written.add(str(path.relative_to(self.root)))
            return path

        def wait_evidence(self, want, timeout=20, lane=None):
            deadline = time.time() + timeout
            seen = None
            while time.time() < deadline:
                seen = self.row(lane)["state"]["evidence"]
                if seen == want:
                    return self.row(lane)
                time.sleep(0.05)
            raise AssertionError(f"evidence never became {want!r} within {timeout}s; it is {seen!r}")

        def log(self, lane=None):
            try:
                return (Path(self.row(lane)["worktree"]) / "agent-log.txt").read_text()
            except (OSError, AssertionError, KeyError):
                return ""

        def wait_log(self, needle, timeout=20, lane=None):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if needle in self.log(lane):
                    return self.log(lane)
                time.sleep(0.05)
            raise AssertionError(
                f"{needle!r} never reached the agent's log:\n{self.log(lane)}")

        # ── the run's own log (SRV-FEAT-032) ─────────────────────────────────────────────────
        #
        # The transcript is the surface a person is reading while they wait, and it is a FILE:
        # what a live frame carried is lost on reload, so every assertion below reads the log
        # `agent` wrote and then checks E8 replays it.
        def run_dir(self, run=None):
            return Path(self.root) / ".factory" / "runs" / (run or self.run)["run_id"]

        def entries(self, run=None):
            try:
                text = (self.run_dir(run) / "transcript.jsonl").read_text()
            except OSError:
                return []
            return [json.loads(l) for l in text.splitlines() if l.strip()]

        def silences(self, run=None, patch=None):
            """The `silence` entries. `patch=False` is the entry itself, `True` its closing
            frame — SPEC.md M5's one exception to append-only, same seq, never a second entry."""
            out = [x for x in self.entries(run) if x["kind"] == "silence"]
            if patch is None:
                return out
            return [x for x in out if bool(x.get("patch")) is patch]

        def wait_silences(self, atleast=1, patch=None, run=None, timeout=20):
            deadline = time.time() + timeout
            seen = []
            while time.time() < deadline:
                seen = self.silences(run, patch)
                if len(seen) >= atleast:
                    return seen
                time.sleep(0.05)
            raise AssertionError(
                f"only {len(seen)} `silence` entries (patch={patch}) reached "
                f"{self.run_dir(run)}/transcript.jsonl within {timeout}s, wanted {atleast}: "
                f"{[x['kind'] for x in self.entries(run)]}")

        def wait_turns(self, n, run=None, timeout=20):
            """Wait until `n` turns have really ENDED in this run — a `result` frame, parsed."""
            deadline = time.time() + timeout
            seen = 0
            while time.time() < deadline:
                seen = len([x for x in self.entries(run) if x["kind"] == "turn"])
                if seen >= n:
                    return
                time.sleep(0.05)
            raise AssertionError(f"only {seen} turns ended within {timeout}s, wanted {n}")

        def replay(self, run=None):
            """E8 — what a page reload gets. An entry that exists only in a live frame is an
            entry a reload loses, so nothing here is asserted from the socket alone."""
            r = self.srv.get(
                f"/api/runs/{(run or self.run)['run_id']}/transcript?limit=1000")
            assert r.status_code == 200, r.text
            return r.json()["entries"]

        def wrote_nothing(self):
            """The tool created no file of its own — only what this case wrote as a document."""
            assert untracked_set(self.root) - self.written == set(), (
                f"the tool wrote into the target repository: "
                f"{sorted(untracked_set(self.root) - self.written)}")

    yield Env()

    for root, before in made:
        assert tracked_status(root) == before, (
            f"THE MAIN CHECKOUT CHANGED at {root}\nbefore:\n{before}\n"
            f"after:\n{tracked_status(root)}")


# ── F17 · the three axes are independent ─────────────────────────────────────────────────────

def test_axes_independent(env):
    """A lane that is `running`, silent for 130 s, AND carrying a report that claims done
    reports all three axes separately — and needs a person for it.

    The prototype flattened these and a stale `quiet` overwrote a live `running`. Every
    assertion below is about one axis holding its own value while another one moves.
    """
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")

    fresh = e.wait_process("running")
    assert fresh["state"]["motion"]["value"] == "producing", fresh["state"]
    assert fresh["state"]["evidence"] == "nothing_claimed", fresh["state"]

    # a report that claims done and cites nothing — the evidence axis moves, alone
    e.report("# TCK-001 — done\n\nEverything passed.\n")
    claimed = e.wait_evidence("claimed_unproven")
    assert claimed["state"]["process"] == "running", (
        f"the evidence axis overwrote the process axis: {claimed['state']}")
    assert claimed["state"]["motion"]["value"] == "producing", claimed["state"]

    # ...and now the motion axis moves, alone
    e.advance(130)
    quiet = e.row()
    assert quiet["state"]["process"] == "running", (
        f"silence overwrote a live process — this is the flattening bug: {quiet['state']}")
    assert quiet["state"]["motion"]["value"] == "silent", quiet["state"]
    assert quiet["state"]["motion"]["silent_for_s"] >= 130, quiet["state"]
    assert quiet["state"]["evidence"] == "claimed_unproven", quiet["state"]

    # Three axes, three values, one object — and never a field that FLATTENS them.
    #
    # The member set is read from the contract rather than typed here. A typed set says "these five
    # and no more", which is a different claim from "nothing merges the axes" and answers wrongly in
    # both directions: it went red when `stopped` was added (SRV-FEAT-415 — a fourth FACT, not a
    # flattening, and one this case had no opinion about), and it would say nothing at all about a
    # member that genuinely merged `process` and `motion` under a name the list happened to contain.
    contract = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/technical/contracts/http.schema.json").read_text())
    declared = set(contract["$defs"]["laneState"]["properties"])
    assert set(quiet["state"]) == declared, (
        f"the state object and the contract disagree about its members: "
        f"{sorted(quiet['state'])} vs {sorted(declared)}")

    # ...and the teeth: every axis still holds its OWN value, which is what flattening destroys.
    assert quiet["state"]["process"] == "running", quiet["state"]
    assert quiet["state"]["motion"]["value"] == "silent", quiet["state"]
    assert quiet["state"]["evidence"] == "claimed_unproven", quiet["state"]
    assert isinstance(quiet["state"].get("stopped"), (dict, type(None))), (
        f"`stopped` is a fact about a lane that has ENDED and may not carry a live lane's state: "
        f"{quiet['state']}")

    assert quiet["needs_you"] is True, quiet
    assert quiet["state"]["alarm"] is True, (
        "`running + silent` is one of the two combinations SPEC.md §3.2 calls an alarm")

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


# ── F18 · blocked on a person is not silent ──────────────────────────────────────────────────

def test_blocked_is_not_silent(env):
    """A lane awaiting a permission answer is `blocked`, and it is still `blocked` 130 s later.

    A correct agent waiting on a person is not a stalled one, and the two must not share a state
    or a colour. `blocked` is decided before `running` and is never derived from silence.
    """
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_log("ready")
    c = e.subscribed()

    before = e.wait_process("running")
    assert before["state"]["blocked"] is None, before["state"]
    assert before["needs_you"] is False, before

    ask = Ask(e.srv, e.lane, "Bash", {"command": "echo blocked"})
    m6 = valid(c.wait_for("permission.request"))

    held = e.row()
    assert held["state"]["process"] == "blocked", (
        f"a lane waiting on a person reported {held['state']['process']!r}")
    assert held["state"]["blocked"] == {"reason": "permission",
                                        "request_id": m6["request_id"]}, held["state"]
    assert held["state"]["motion"]["value"] != "silent", (
        f"blocked was flattened into silence: {held['state']}")
    assert held["needs_you"] is True, held

    # 130 s of nothing arriving, because a person has not answered yet
    e.advance(130)
    later = e.row()
    assert later["state"]["process"] == "blocked", (
        f"after {SILENCE_S}s of waiting on a person the lane was reclassified as "
        f"{later['state']['process']!r} — F18 is exactly this")
    assert later["state"]["motion"]["value"] != "silent", (
        f"a lane waiting on a person was called stalled: {later['state']}")
    assert later["state"]["alarm"] is False, (
        "`blocked` is not one of the two alarm combinations; only a person is missing")
    assert later["needs_you"] is True, later

    # the answer releases it, and the silence clock starts again from the answer
    assert e.srv.post("/api/permissions/decisions",
                      json={"request_id": m6["request_id"], "decision": "once"}
                      ).status_code == 200
    assert ask.result()["allowed"] is True
    freed = e.wait_process("running")
    assert freed["state"]["blocked"] is None, freed["state"]
    assert freed["state"]["motion"]["value"] == "producing", (
        f"unblocking flashed an alarm for silence the person was answering: {freed['state']}")
    assert freed["needs_you"] is False, freed

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


# ── F19 · the threshold, and exactly one frame ───────────────────────────────────────────────

def test_silence_threshold(env):
    """119 s is `producing`; 121 s is `silent`, announced by exactly ONE M2 carrying `since`;
    and while it stays silent no further frame is sent."""
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_process("running")
    c = e.subscribed()
    e.settle(c)                           # whatever the subscription's own backlog produced

    # 119 s of quiet is a compile, and this clause is about the STATE.
    #
    # It deliberately does NOT assert that no frame has arrived yet. `motion.since` is on the wire
    # at second resolution, so "make it 119 s" carries up to a second of error either way — and an
    # assertion about which side of the threshold a frame's ARRIVAL falls on, measured with a
    # ruler that cannot see a second, is a coin toss that reports itself as a defect. The frame
    # count is asserted below, across the whole transition, where it is exact and stronger: one
    # frame, and one only, however the second lands.
    e.quiet(SILENCE_S - 1)
    assert e.row()["state"]["motion"]["value"] == "producing", (
        f"{SILENCE_S - 1}s of quiet is a compile, not a stall")

    e.quiet(SILENCE_S + 1)                # 121 s
    frames = e.frames(c, "lane.state")
    assert len(frames) == 1, (
        f"the whole crossing of the threshold sent {len(frames)} frames, not one: {frames}")
    state = frames[0]["state"]
    assert state["motion"]["value"] == "silent", frames[0]
    assert state["motion"]["since"], f"a silence with no `since` cannot be counted client-side: {state}"
    assert frames[0]["lane"] == e.lane and frames[0]["ticket_id"] == e.ticket, frames[0]
    assert frames[0]["needs_you"] is True, frames[0]

    e.advance(300)
    assert e.frames(c, "lane.state") == [], (
        "a lane that stayed silent kept sending frames — that is the periodic tick SPEC.md §6 "
        "forbids, wearing a state change's clothes")

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


# ── F28 · claimed is not proven ──────────────────────────────────────────────────────────────

def test_unproven_claim_is_an_alarm(env):
    """A report naming no run id is `claimed_unproven`, needs a person, and is an alarm. The
    same report with the run id in it is `claimed_proven`, needs nobody, and is not."""
    e = env.build()
    e.claim()
    run = e.start()
    e.wait_log("ready")
    e.say("bye")
    e.wait_process("finished")

    e.report("# TCK-001 — done\n\nIt all passed. Trust me.\n")
    unproven = e.wait_evidence("claimed_unproven")
    assert unproven["state"]["process"] == "finished", unproven["state"]
    assert unproven["needs_you"] is True, unproven
    assert unproven["state"]["alarm"] is True, (
        "`finished + claimed_unproven` is the second of SPEC.md §3.2's two alarms")

    e.report(f"# TCK-001 — done\n\nGate run: `{run['run_id']}`.\n")
    proven = e.wait_evidence("claimed_proven")
    assert proven["state"]["process"] == "finished", proven["state"]
    assert proven["needs_you"] is False, (
        f"a report that cites its run still asked for a person: {proven}")
    assert proven["state"]["alarm"] is False, proven["state"]

    assert_reaped(run["pid"])
    e.wrote_nothing()


# ── §3.3 · the counter is exactly this set — five clauses, five cases ─────────────────────────
#
# One case per clause, each turning the counter on BY ITSELF. Written as five rather than one
# because a clause that silently stops working inside a single case is invisible: the assertion
# passes on the strength of the other four.

def test_needs_you_is_exactly_the_specified_set_1_idle(env):
    """Clause 1 — `process == idle`: the agent asked and stopped.

    `idle` is a turn that ENDED (SPEC.md §3.2), not the absence of one — so this case runs a turn
    to its end rather than merely starting a process and calling the quiet `idle`.
    """
    e = env.build()
    e.claim()
    run = e.start()
    e.wait_log("ready")
    assert e.counter() == 0, "a run that has not finished a turn yet asks for nobody"

    e.say("emit:one-turn.jsonl")          # a real `result` frame is what ends a turn
    row = e.wait_process("idle")
    assert row["state"]["motion"]["value"] == "producing", row["state"]
    assert row["state"]["evidence"] == "nothing_claimed", row["state"]
    assert row["needs_you"] is True, f"an idle lane asked nobody for anything: {row}"
    assert e.counter() == 1, e.lanes()

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_needs_you_is_exactly_the_specified_set_2_blocked(env):
    """Clause 2 — `process == blocked`: it is waiting on your answer."""
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_log("ready")
    c = e.subscribed()
    e.wait_process("running")
    assert e.counter() == 0, e.lanes()

    ask = Ask(e.srv, e.lane, "Bash", {"command": "echo one"})
    m6 = valid(c.wait_for("permission.request"))
    row = e.row()
    assert row["state"]["process"] == "blocked", row["state"]
    assert row["needs_you"] is True, row
    assert e.counter() == 1, e.lanes()

    e.srv.post("/api/permissions/decisions",
               json={"request_id": m6["request_id"], "decision": "once"})
    ask.result()
    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_needs_you_is_exactly_the_specified_set_3_crashed(env):
    """Clause 3 — `process == crashed`: it died and nobody has looked."""
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_log("ready")
    e.wait_process("running")
    assert e.counter() == 0, e.lanes()

    os.kill(run["pid"], 9)                # it died on its own, as far as the tool is concerned
    row = e.wait_process("crashed")
    assert row["needs_you"] is True, f"a crashed run asked nobody to look: {row}"
    assert e.counter() == 1, e.lanes()

    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_needs_you_is_exactly_the_specified_set_4_finished_unproven(env):
    """Clause 4 — `finished ∧ claimed_unproven`: it says done and cites nothing."""
    e = env.build()
    e.claim()
    run = e.start()
    e.wait_log("ready")
    e.say("bye")
    e.wait_process("finished")
    assert e.counter() == 0, (
        "a finished run with no report at all is not in §3.3's set — the clause is "
        "`claimed_unproven`, and only that")

    e.report("# TCK-001 — done\n\nNo run cited.\n")
    row = e.wait_evidence("claimed_unproven")
    assert row["needs_you"] is True, row
    assert e.counter() == 1, e.lanes()

    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_needs_you_is_exactly_the_specified_set_5_running_silent(env):
    """Clause 5 — `running ∧ silent`: it is alive and going nowhere."""
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_process("running")
    assert e.counter() == 0, e.lanes()

    e.advance(SILENCE_S + 1)
    row = e.row()
    assert row["state"]["process"] == "running" and row["state"]["motion"]["value"] == "silent", (
        row["state"])
    assert row["needs_you"] is True, row
    assert e.counter() == 1, e.lanes()

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


# ── the clocks are the client's ──────────────────────────────────────────────────────────────

def test_clocks_tick_client_side(env):
    """A quiet lane produces NO server traffic: the numbers come from `since` timestamps.

    And the one metrics frame there is (`M4`) arrives when a turn ends — never on a timer. A
    counter that needs a frame per second is a counter that dies with the socket.
    """
    e = env.build()
    e.claim()
    run = e.start()                       # no prompt, so the message below starts the turn
    e.wait_log("ready")
    c = e.subscribed()
    c.drain(0.4)

    # a turn starts and ends: exactly the moment M4 is allowed to speak
    e.say("emit:one-turn.jsonl")
    metrics = None
    deadline = time.time() + 20
    while time.time() < deadline and metrics is None:
        for f in c.drain(0.5):
            if valid(f).get("m") == "lane.metrics":
                metrics = f
    assert metrics is not None, "a turn ended and no M4 was sent"
    assert metrics["lane"] == e.lane, metrics
    assert metrics["turn"]["n"] >= 1, metrics
    assert metrics["cost_usd"] > 0, f"a turn that cost something reported nothing: {metrics}"
    assert metrics["elapsed_s"] >= 0, metrics

    # ...and then nothing happens for four minutes of clock, twice over
    e.wait_process("idle")
    c.drain(1.0)
    row_before = e.row()
    for _ in range(2):
        e.advance(SILENCE_S + 1)
        assert e.frames(c, "lane.metrics") == [], (
            "M4 arrived with no turn behind it — that is a timer")

    # the silence number moved without a single frame carrying it
    row_after = e.row()
    assert row_after["state"]["motion"]["since"] == row_before["state"]["motion"]["since"], (
        "`since` moved, so the client cannot tick from it")
    assert (row_after["state"]["motion"]["silent_for_s"]
            > row_before["state"]["motion"]["silent_for_s"]), (
        "the silence number did not move at all")

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


# ── NEGATIVE (mandatory) ─────────────────────────────────────────────────────────────────────

def test_a_lane_nobody_touched_needs_nobody(env):
    """A healthy lane contributes ZERO to the counter, and so does a finished lane whose report
    cites its run.

    This is the case that keeps the counter meaningful. A "needs you" that is always on is a
    notification channel a person learns to ignore, and every clause added later has to be
    re-checked against this test.
    """
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_log("ready")

    # 1 · producing, unclaimed, un-reported: nothing about it needs a person
    healthy = e.wait_process("running")
    assert healthy["state"]["motion"]["value"] == "producing", healthy["state"]
    assert healthy["state"]["evidence"] == "nothing_claimed", healthy["state"]
    assert healthy["state"]["alarm"] is False, healthy["state"]
    assert healthy["needs_you"] is False, f"a healthy lane asked for a person: {healthy}"
    assert e.counter() == 0, e.lanes()

    # ...and it stays zero while it is working. Deliberately not balanced on the threshold: `since`
    # is second-resolution, so a case aiming at 119 s can land on either side of 120 — and whether
    # THIS case sits one second before the boundary is not what it is about. F19 owns the boundary.
    e.quiet(SILENCE_S - 5)
    assert e.counter() == 0, e.lanes()

    # 2 · a second lane, finished, with a report that cites the run it was done in
    second = e.claim(ticket="TCK-002")
    run2 = e.start()                      # no prompt: nothing is mid-turn, so `bye` is delivered
    e.wait_log("ready", lane=second)
    e.say("bye", lane=second)
    e.wait_process("finished", lane=second)
    e.report(f"# TCK-002 — done\n\nThe gate ran in `{run2['run_id']}`.\n", ticket="TCK-002")
    done = e.wait_evidence("claimed_proven", lane=second)
    assert done["state"]["process"] == "finished", done["state"]
    assert done["state"]["alarm"] is False, done["state"]
    assert done["needs_you"] is False, (
        f"a finished ticket whose report cites its run still asked for a person: {done}")
    assert e.counter() == 0, (
        f"two lanes, neither of which needs anybody, and the counter says otherwise: {e.lanes()}")

    e.kill(lane=healthy["lane"])
    assert_reaped(run["pid"])
    assert_reaped(run2["pid"])
    e.wrote_nothing()


# ── SRV-FEAT-032 · SPEC.md §4.6's `silence` entry — the rule a person actually reads ──────────
#
# SRV-FEAT-011 built the axis: the lane CARD says a lane is quiet. The transcript is the surface
# somebody is looking at while they wait, and it said nothing at all — a stream that simply stops,
# and a reader who has to look somewhere else to find out why.
#
# The entry marks a TRANSITION, and it brackets ONE stretch of quiet: opened when the stretch
# crosses 120 s, closed at the same seq when output arrives again. Both halves are asserted from
# the run's own `transcript.jsonl` and from E8's replay, never from a live frame alone.

def test_silence_appends_one_entry(env):
    """A lane whose motion crosses 120 s appends exactly ONE `silence` entry, carrying
    `silent_for_s` — and E8 replays it in the same position.

    An entry that exists only in a frame that has already gone past is an entry a page reload
    loses, which is the one thing the RUNS tab exists to survive.
    """
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_process("running")
    assert e.silences() == [], (
        f"a lane that has just started already announced silence: {e.entries()}")

    e.quiet(SILENCE_S + 1)
    row = e.row()
    assert row["state"]["motion"]["value"] == "silent", row["state"]

    written = e.wait_silences(1)
    assert len(written) == 1, f"the crossing appended {len(written)} entries, not one: {written}"
    one = written[0]
    assert one["kind"] == "silence", one
    assert one["silent_for_s"] >= SILENCE_S, (
        f"the entry says the lane was quiet for {one['silent_for_s']}s, which is inside the "
        f"{SILENCE_S}s threshold that produced it: {one}")
    assert not one.get("patch"), f"the entry that OPENS a stretch is not a patch: {one}"
    assert one["seq"] >= 1 and one["ts"], one

    # ...and a reload gets it, in the position it was written in.
    log = e.entries()
    replayed = e.replay()
    assert replayed == log, (
        f"E8 replays a different stream than the run's own log: {replayed} vs {log}")
    assert [x["kind"] for x in replayed].index("silence") == \
           [x["kind"] for x in log].index("silence"), (replayed, log)

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_silence_entry_is_not_repeated(env):
    """Five more minutes of the injected clock, and no second entry appears.

    The entry marks a transition. A transcript that repeats itself while nothing happens is the
    periodic tick SPEC.md §6 forbids, wearing a state change's clothes — and the socket's own
    dedup cannot save it, because this one is appended to a FILE.
    """
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_process("running")
    c = e.subscribed()
    e.settle(c)

    e.quiet(SILENCE_S + 1)
    first = e.wait_silences(1)
    assert len(first) == 1, first

    # The crossing's own frame, taken off the wire HERE — a "no second frame" assertion made
    # without draining the first one is satisfied by the first one, which is a green that means
    # nothing.
    opened = [f for f in e.frames(c, "transcript") if f["entry"]["kind"] == "silence"]
    assert len(opened) == 1, f"the crossing sent {len(opened)} `silence` frames: {opened}"
    assert not opened[0].get("patch"), opened[0]

    for _ in range(5):
        e.advance(60)
        time.sleep(0.3)

    still = e.silences()
    assert len(still) == 1, (
        f"five more minutes of quiet appended {len(still)} `silence` entries: {still}. One per "
        f"transition, never one per interval.")
    assert still[0] == first[0], f"the entry was rewritten in place: {still[0]} vs {first[0]}"
    assert [f for f in e.frames(c, "transcript")
            if f["entry"]["kind"] == "silence"] == [], (
        "a second `silence` frame went out for a lane that never stopped being quiet")

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_a_lane_that_never_goes_quiet_has_no_silence_entry(env):
    """NEGATIVE (§1) — a run producing throughout, and a run that ends inside the threshold,
    both replay with no `silence` entry at all."""
    e = env.build()
    e.claim()
    run = e.start()
    e.wait_log("ready")

    # Four stretches of 90 s, each ended by real output. Six minutes of run, and never 120 s of
    # quiet in a row — the number that matters is the GAP, not the elapsed time.
    for n in range(1, 5):
        e.say("emit:one-turn.jsonl")
        e.wait_turns(n)
        assert e.silences() == [], f"output was arriving and the tool called it silence: {n}"
        e.quiet(SILENCE_S - 30)
        assert e.silences() == [], (
            f"{SILENCE_S - 30}s of quiet is a compile, not a stall (stretch {n})")

    assert e.silences() == [], f"a lane that never went quiet: {e.entries()}"
    assert [x for x in e.replay() if x["kind"] == "silence"] == [], e.replay()
    e.kill()
    assert_reaped(run["pid"])

    # ...and a run that ends WELL inside the threshold, then a clock that walks past it. A run
    # that is over cannot fall silent, and a lane whose process is gone has no motion at all.
    second = e.claim(ticket="TCK-002")
    run2 = e.start()
    e.wait_log("ready", lane=second)
    e.say("bye", lane=second)
    e.wait_process("finished", lane=second)
    assert e.silences(run2) == [], f"a run that ended inside the threshold: {e.entries(run2)}"

    e.advance(SILENCE_S * 3)
    time.sleep(1.0)
    assert e.silences(run2) == [], (
        f"the clock walked past the threshold and a FINISHED run was announced silent: "
        f"{e.entries(run2)}")
    assert [x for x in e.replay(run2) if x["kind"] == "silence"] == [], e.replay(run2)

    assert_reaped(run2["pid"])
    e.wrote_nothing()


# ── NEGATIVE (mandatory) ─────────────────────────────────────────────────────────────────────

def test_the_silence_entry_does_not_repeat_and_does_not_linger(env):
    """The whole ticket, in one case: the entry appears once, and it stops claiming the lane is
    quiet the moment output arrives again.

    A transcript that says NO OUTPUT under a run that is producing again is worse than one that
    says nothing: it teaches the reader to ignore the line, and after that the rule is decoration.
    So the stretch is CLOSED — SPEC.md M5's one exception to append-only, the same seq with
    `patch:true`, carrying the length the gap really was — and a LATER stretch of quiet is its own
    entry rather than a resurrection of the first.
    """
    e = env.build()
    e.claim()
    run = e.start()                        # no prompt: the message below starts the turn
    e.wait_log("ready")
    c = e.subscribed()
    e.settle(c)

    e.quiet(SILENCE_S + 1)
    opened = e.wait_silences(1)
    assert len(opened) == 1, opened
    seq = opened[0]["seq"]

    # ...it stays quiet for another two minutes, so the gap is nothing like the threshold
    e.advance(130)
    assert e.row()["state"]["motion"]["value"] == "silent", e.row()["state"]

    # ...and now output arrives.
    e.say("emit:one-turn.jsonl")
    e.wait_turns(1)

    closed = e.wait_silences(1, patch=True)
    entries = e.entries()
    silences = [x for x in entries if x["kind"] == "silence"]

    # 1 · it did not appear twice
    assert len([x for x in silences if not x.get("patch")]) == 1, (
        f"output resumed and a second `silence` entry was appended: {silences}")
    assert {x["seq"] for x in silences} == {seq}, (
        f"the close is a second ENTRY rather than the same one: {silences}")

    # 2 · it did not linger: the stretch was closed, with the length the gap really was
    assert len(closed) == 1, closed
    assert closed[0]["seq"] == seq, closed[0]
    assert closed[0]["silent_for_s"] >= SILENCE_S + 130, (
        f"the closed entry reports {closed[0]['silent_for_s']}s for a gap of at least "
        f"{SILENCE_S + 130}s — a rule that under-reports the gap is a rule nobody can act on: "
        f"{closed[0]}")
    assert e.row()["state"]["motion"]["value"] == "producing", e.row()["state"]

    # ...and the run really is producing underneath it
    after = entries[[x["seq"] for x in entries].index(seq) + 1:]
    assert [x for x in after if x["kind"] in ("prose", "tool", "file_edit", "turn")], (
        f"nothing arrived after the silence, so this case is not about a lane that resumed: "
        f"{[x['kind'] for x in entries]}")

    # ...and the client is TOLD, on the same seq, rather than being left ticking a dead counter
    frame = None
    deadline = time.time() + 20
    while time.time() < deadline and frame is None:
        for f in c.drain(0.5):
            if (valid(f).get("m") == "transcript" and f["entry"]["kind"] == "silence"
                    and f.get("patch")):
                frame = f
    assert frame is not None, (
        "the stretch was closed on disk and no M5 frame carried it — a client that never hears "
        "the close goes on counting a silence that ended")
    assert frame["seq"] == seq, frame

    # 3 · a SECOND stretch of quiet is its own entry, not the first one coming back
    e.quiet(SILENCE_S + 1)
    again = e.wait_silences(2, patch=False)
    assert len(again) == 2, again
    assert again[1]["seq"] > seq, (
        f"the second stretch of quiet reused the first entry's seq: {again}")
    assert e.replay() == e.entries(), "E8 replays a different stream than the log"

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()


def test_a_lane_blocked_on_a_person_gets_no_silence_rule(env):
    """F18, on the surface a person reads: a lane waiting on an answer is quiet, and it is not
    silent — so no rule is drawn under it, however long nobody answers.

    The timer that decides the crossing does NOT know what a permission is, on purpose
    (`lane::Store::arm_`): under SRV-FEAT-011 an early fire cost one recomputation that published
    nothing, because the render is what holds the blocked set. An ENTRY is not deduped by a render
    — it is appended to a file — so the same early fire would write `NO OUTPUT` under a correct
    agent that is waiting on a person, which is the one thing SPEC.md F18 says these two states
    must never share.
    """
    e = env.build()
    e.claim()
    run = e.start(prompt="do the work")
    e.wait_log("ready")
    c = e.subscribed()
    e.wait_process("running")

    ask = Ask(e.srv, e.lane, "Bash", {"command": "echo blocked"})
    m6 = valid(c.wait_for("permission.request"))
    assert e.row()["state"]["process"] == "blocked", e.row()["state"]

    e.advance(SILENCE_S * 3)               # six minutes, and nobody answers
    time.sleep(1.0)
    assert e.row()["state"]["motion"]["value"] != "silent", e.row()["state"]
    assert e.silences() == [], (
        f"a lane waiting on a PERSON had `NO OUTPUT` written under it: {e.entries()}. Nothing is "
        f"stalled — somebody is being asked a question, which is already on the process axis.")

    assert e.srv.post("/api/permissions/decisions",
                      json={"request_id": m6["request_id"], "decision": "once"}
                      ).status_code == 200
    assert ask.result()["allowed"] is True
    e.wait_process("running")
    assert e.silences() == [], (
        f"answering the question drew a silence rule for the time the person took: {e.entries()}")

    e.kill()
    assert_reaped(run["pid"])
    e.wrote_nothing()
