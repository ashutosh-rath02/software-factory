"""SRV-FEAT-012 — stages, rounds, and going backwards (E22, M3, F20).

`POST /api/progress` is why no `progress.sh` has to be copied into any repository: the URL and a
token arrive in the run's environment, so the agent reports the stage it reached over HTTP and
names nothing else. It is therefore also **the one place in the product that accepts input from a
process the tool started but does not otherwise trust**, and the mandatory negative below is
exactly there — a token that outlives its run is a way to write into someone else's history.

Two things this file is watching for, because they are what §10 says will decay first:

- **stage names are strings.** No built-in sequence, no meaning attached to `GATE` or `QA`, no cap
  on a round. A stage the ticket's sequence does not contain is `202`, recorded, and flagged — a
  mismatch is a fact about the repository, not an error.
- **the tool stores what was reported.** It never infers a stage from what the agent did.

The stand-in agent here posts to the endpoint with `curl` from inside its own confinement, which is
the whole claim under test: nothing was copied into the repository, and the agent never learns its
lane or its ticket id.
"""

import json
import time
from pathlib import Path

import jsonschema
import pytest

import stand_in
from test_lane import build_repo, git

DOCS = Path(__file__).resolve().parents[2] / "docs"
SOCKET_SCHEMA = json.loads((DOCS / "technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)

# `build_repo`'s manifest declares this and the tickets it writes declare no `stages:` of their
# own, so every case here runs against a fallback sequence — `stage.source` is `manifest`.
SEQUENCE = ["SPEC", "IMPL", "GATE"]


def valid(frame):
    SOCKET_VALIDATOR.validate(frame)
    return frame


# The stand-in reports its stage the way a real agent would: by reading two environment variables
# and making one HTTP request. It is given no lane name, no ticket id and no run id to send — if
# the tool cannot attribute the report from the token alone, these cases cannot pass.
AGENT = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
""" + stand_in.FRAME_READER + r"""
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "token=${FACTORY_PROGRESS_TOKEN}" >> "$log"
echo "url=${FACTORY_PROGRESS_URL}" >> "$log"
echo "ready" >> "$log"

while : ; do
  if read_frame ; then
    case "$text" in
      bye) break ;;
      post:*)
        stage=`printf '%s' "$text" | cut -d: -f2`
        body="{\"token\":\"${FACTORY_PROGRESS_TOKEN}\",\"stage\":\"${stage}\"}"
        echo "sent=$body" >> "$log"
        code=`/usr/bin/curl -s -o /dev/null -w '%{http_code}' -X POST \
              -H 'Content-Type: application/json' -d "$body" "${FACTORY_PROGRESS_URL}"`
        echo "posted=${stage} code=${code}" >> "$log"
        ;;
    esac
  else
    break
  fi
done
echo "eof" >> "$log"
"""


@pytest.fixture
def env(tmp_path, serve):
    """A repository with the reporting stand-in, a server, and helpers for one lane and run."""
    conns = []
    made = []

    class Env:
        def build(self, prepare=None):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent={"command": "/bin/sh agent.sh"})
            (root / "agent.sh").write_text(AGENT)
            if prepare:
                prepare(root)
            git(root, "add", "-A")
            git(root, "commit", "-m", "the reporting agent")
            self.root = root
            made.append(root)
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        # ── a lane, a run, and the token the run was given ───────────────────────────────────
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
            self.run = r.json()
            return self.run

        def worktree(self):
            for row in self.lanes():
                if row["lane"] == self.lane:
                    return Path(row["worktree"])
            raise AssertionError(f"{self.lane} is gone")

        def log(self):
            try:
                return (self.worktree() / "agent-log.txt").read_text()
            except (OSError, AssertionError):
                return ""

        def wait_log(self, needle, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if needle in self.log():
                    return self.log()
                time.sleep(0.05)
            raise AssertionError(f"{needle!r} never reached the agent log; it holds:\n{self.log()}")

        def token(self):
            """The token this run was handed — read from what the AGENT saw, never from the
            tool's own memory. A token the test learned some other way would prove nothing
            about what was injected."""
            for line in self.wait_log("ready").splitlines():
                if line.startswith("token="):
                    return line.split("=", 1)[1]
            raise AssertionError(f"the agent never saw a token:\n{self.log()}")

        def say(self, text):
            r = self.srv.post(f"/api/lanes/{self.lane}/messages", json={"text": text})
            assert r.status_code == 200, r.text
            return r.json()

        # ── the endpoint ─────────────────────────────────────────────────────────────────────
        def report(self, stage, token=None, **extra):
            body = {"token": self.token() if token is None else token, "stage": stage}
            body.update(extra)
            return self.srv.post("/api/progress", json=body)

        def accepted(self, stage, **extra):
            r = self.report(stage, **extra)
            assert r.status_code == 202, r.text
            return r.json()

        # ── what the tool says about it afterwards ───────────────────────────────────────────
        def lanes(self):
            r = self.srv.get("/api/lanes")
            assert r.status_code == 200, r.text
            return r.json()["lanes"]

        def ticket_stage(self, ticket=None):
            r = self.srv.get(f"/api/tickets/{ticket or self.ticket}")
            assert r.status_code == 200, r.text
            return r.json()["stage"]

        def lane_stage(self):
            for row in self.lanes():
                if row["lane"] == self.lane:
                    return row["stage"]
            raise AssertionError(f"{self.lane} is gone")

        def transcript(self, run_id=None, missing_ok=False):
            r = self.srv.get(f"/api/runs/{run_id or self.run['run_id']}/transcript")
            # E8 answers 404 for a run that produced no entries at all — a run with an empty
            # history and a run nobody can find share an answer there. For "nothing was written
            # into it" that is the strongest form of the claim, so a case asserting an absence
            # accepts it; every other call still demands the 200.
            if missing_ok and r.status_code == 404:
                return []
            assert r.status_code == 200, r.text
            return r.json()["entries"]

        def wait_run_gone(self, timeout=20):
            deadline = time.time() + timeout
            while time.time() < deadline:
                for row in self.lanes():
                    if row["lane"] == self.lane and not row["run_id"]:
                        return row
                    if row["lane"] == self.lane and row["state"]["process"] in (
                            "finished", "crashed", "killed"):
                        return row
                time.sleep(0.05)
            raise AssertionError("the run never reached a terminal state")

        # ── the socket ───────────────────────────────────────────────────────────────────────
        def subscribed(self, scope=None):
            c = self.srv.ws()
            assert valid(c.recv())["m"] == "hello"
            c.send({"m": "subscribe", "scope": scope or {"lane": self.lane}})
            valid(c.wait_for("snapshot"))
            conns.append(c)
            return c

        def frames(self, conn, name, seconds=1.5):
            return [valid(f) for f in conn.drain(seconds) if f.get("m") == name]

    e = Env()
    yield e
    for c in conns:
        c.close()


@pytest.fixture
def run(env):
    """A repository, a lane, and one live run — the state every case below starts from."""
    env.build()
    env.claim()
    env.start()
    env.wait_log("ready")
    return env


# ── F20 · the round, and going backwards ────────────────────────────────────────────────────

def test_round_and_regression(run):
    """SPEC.md F20. GATE, then IMPL, then GATE: the second frame carries `regressed_from:"GATE"`,
    the third carries `round:2`, and E5 reports `stage.round:2`.

    Going backwards is the thing a stage strip cannot show and the thing that actually happens —
    a gate fails and the work returns to IMPL. A model that only counts forwards reports the
    second GATE as if nothing had gone wrong in between.
    """
    c = run.subscribed()

    run.accepted("GATE")
    run.accepted("IMPL")
    run.accepted("GATE")

    frames = run.frames(c, "stage", seconds=3)
    assert len(frames) == 3, [f["stage"] for f in frames]

    first, second, third = frames
    assert first["stage"]["name"] == "GATE", first
    assert first["stage"]["round"] == 1, first
    assert "regressed_from" not in first, "nothing preceded the first report"

    assert second["stage"]["name"] == "IMPL", second
    assert second["regressed_from"] == "GATE", second
    assert second["stage"]["index"] == 2, second

    assert third["stage"]["name"] == "GATE", third
    assert third["stage"]["round"] == 2, third
    assert "regressed_from" not in third, "GATE is later than IMPL, so this went forwards"

    assert run.ticket_stage()["round"] == 2, run.ticket_stage()
    assert run.ticket_stage()["index"] == 3, run.ticket_stage()
    assert run.ticket_stage()["total"] == len(SEQUENCE), run.ticket_stage()
    assert run.ticket_stage()["source"] == "manifest", run.ticket_stage()


def test_the_token_identifies_the_run(run):
    """The stage is attributed without the agent knowing its lane or its ticket.

    An agent that has to be told its lane is an agent that can lie about it — and one that has to
    be told is one a repository has to teach, which is the `progress.sh` this endpoint exists to
    delete. So the report here is made BY THE AGENT, from inside its confinement, with nothing but
    the two environment variables it was handed.
    """
    run.say("post:IMPL")
    run.wait_log("posted=IMPL")

    sent = [l for l in run.log().splitlines() if l.startswith("sent=")]
    assert sent, run.log()
    body = json.loads(sent[-1].split("=", 1)[1])
    assert set(body) == {"token", "stage"}, body
    assert run.lane not in sent[-1], "the agent named its lane"
    assert run.ticket not in sent[-1], "the agent named its ticket"

    assert "posted=IMPL code=202" in run.log(), run.log()

    # ...and the tool worked out both of them from the token alone.
    assert run.lane_stage()["index"] == 2, run.lane_stage()
    assert run.ticket_stage()["index"] == 2, run.ticket_stage()


def test_an_unknown_token_is_404_not_403(run):
    """Deliberately not 403: this endpoint is reachable by anything on loopback and must not
    confirm token shapes. A well-formed token nobody holds and a token that is not one at all
    get the same answer, byte for byte."""
    nonsense = run.report("GATE", token="not-a-token-at-all")
    shaped = run.report("GATE", token="0" * 32)

    for r in (nonsense, shaped):
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "not_found", r.text

    assert nonsense.text == shaped.text, (
        "the two answers differ, so the endpoint confirms what a token looks like:\n"
        f"{nonsense.text}\n{shaped.text}")

    assert run.ticket_stage()["index"] == 0, "a refused report was recorded anyway"


def test_an_unknown_stage_is_accepted_and_flagged(run):
    """SPEC.md §10, at the endpoint most tempting to make clever. A stage the ticket's sequence
    does not contain is 202, recorded, and reported with `unknown_stage:true`.

    The tool has no opinion about stage names. A repository whose process has a `REVIEW` step the
    manifest never listed is a repository the tool describes, not one it corrects — a mismatch is
    a fact about the repository, not an error.
    """
    known = run.accepted("SPEC")
    assert known["unknown_stage"] is False, known

    out = run.accepted("REVIEW")
    assert out["accepted"] is True, out
    assert out["unknown_stage"] is True, out

    # Recorded — not swallowed. It is in the transcript and it is what the ticket reports now.
    kinds = [e for e in run.transcript() if e["kind"] == "stage"]
    assert [e["stage"] for e in kinds] == ["SPEC", "REVIEW"], kinds

    stage = run.ticket_stage()
    assert stage["index"] == 0, ("a name outside the sequence has no position in it, and "
                                 f"inventing one is the opinion §10 forbids: {stage}")

    # ...and it counts as a round of its own, like any other reported name.
    again = run.accepted("REVIEW")
    assert again["unknown_stage"] is True, again
    assert run.ticket_stage()["round"] == 2, run.ticket_stage()


def test_no_retry_cap(run):
    """`round:5` is recorded as `5`. A retry limit belongs to the repository's process, not to
    this tool — and `of` is whatever the reporter said, so a caption reading `round 2 of 4` gets
    it because the agent said so and not because the tool counted to four."""
    out = run.accepted("IMPL", round=5, of=9)
    assert out["accepted"] is True, out

    stage = run.ticket_stage()
    assert stage["round"] == 5, stage
    assert stage["of"] == 9, stage

    # ...and again, past anything anyone would call a limit.
    run.accepted("IMPL", round=97)
    assert run.ticket_stage()["round"] == 97, run.ticket_stage()

    assert run.lane_stage()["round"] == 97, run.lane_stage()


def test_the_stage_appears_in_the_transcript(run):
    """Every accepted report also appends a `stage` entry, so the full history of transitions is
    readable where the entries already are (SPEC.md §4.6, §8 D2).

    The strip is linear and shows one active chip; the history of how the work got there is the
    transcript's, and it has to survive a page reload — so the entry goes through the run's own
    sequence and into `transcript.jsonl`, not only onto the wire.
    """
    c = run.subscribed()

    run.accepted("SPEC")
    run.accepted("IMPL", note="the gate sent it back")

    live = [f for f in run.frames(c, "transcript", seconds=3)
            if f["entry"]["kind"] == "stage"]
    assert [f["entry"]["stage"] for f in live] == ["SPEC", "IMPL"], live
    assert live[0]["entry"]["round"] == 1, live[0]
    assert live[1]["entry"]["note"] == "the gate sent it back", live[1]
    assert live[0]["seq"] and live[1]["seq"] > live[0]["seq"], "entries carry the run's sequence"

    # ...and E8 reads back what the wire carried, which is the half a reload depends on.
    replay = [e for e in run.transcript() if e["kind"] == "stage"]
    assert [e["stage"] for e in replay] == ["SPEC", "IMPL"], replay
    assert [e["seq"] for e in replay] == [f["seq"] for f in live], (replay, live)


# ── the mandatory negative ──────────────────────────────────────────────────────────────────

def test_a_token_from_a_finished_run_is_refused(run):
    """NEGATIVE (mandatory). A run ends, its token is replayed, and the report is refused rather
    than attributed to whatever occupies that lane now.

    This is the only endpoint in the product that takes input from a process the tool started but
    does not otherwise trust, and a token that outlives its run is a way to write into someone
    else's history: the lane is reused, a second run is started in it, and a stale token from the
    first would land its stage on the second's ticket. The refusal is what makes the token an
    identity for a RUN rather than a key to a lane.
    """
    stale = run.token()
    assert run.accepted("SPEC")["accepted"] is True, "the token works while its run is live"

    run.say("bye")
    run.wait_run_gone()

    # A second run in the same lane, with a token of its own.
    second = run.start()
    run.wait_log(f"run={second['run_id']}")
    fresh = run.token()
    assert fresh != stale, "two runs were handed the same token"

    refused = run.report("GATE", token=stale)
    assert refused.status_code == 404, refused.text
    assert refused.json()["error"]["code"] == "not_found", refused.text

    # ...and nothing was written into the run that is actually there. `SPEC` is index 1 and the
    # replayed report named `GATE`, which is index 3.
    assert run.ticket_stage()["index"] == 1, (
        "the replayed report reached the ticket anyway: " + str(run.ticket_stage()))
    assert run.ticket_stage()["round"] == 1, run.ticket_stage()
    written = run.transcript(second["run_id"], missing_ok=True)
    assert [e["stage"] for e in written if e["kind"] == "stage"] == [], \
        "the replayed report was written into the second run's transcript"

    # The live token still works, which is what makes the refusal above about the RUN and not
    # about the endpoint having simply stopped accepting anything.
    assert run.report("GATE", token=fresh).status_code == 202


# ── the tool's own record, across a restart ─────────────────────────────────────────────────

def test_the_reported_stage_survives_a_restart(run):
    """THE point of E22. A stage nobody can read back tomorrow was never reported.

    `stages_` (`lane/store.h:501`) is a `map<ticket, vector<Report>>` in memory, written at E22 and
    loaded from nothing. Every report is ALSO appended durably to the run's `transcript.jsonl`, so
    the tool writes the record and then rebuilds its index of it from zero: shut the server down
    and the strip is ten grey chips again, on a ticket twelve runs deep.

    `test_history_survives_a_restart` asserts E7 keeps the runs and `test_agent.py` asserts a
    restarted tool adopts no lanes — both true, both passing, and neither asks E5 what the TICKET
    says afterwards. This is that question.
    """
    from conftest import _start_instance, stop_process

    run.accepted("SPEC")
    run.accepted("IMPL")

    before = run.srv.get(f"/api/tickets/{run.ticket}").json()["stage"]
    assert before["index"] > 0, f"E22 was accepted and E5 shows nothing: {before}"

    stop_process(run.srv.proc)
    restarted = _start_instance(run.root, run.srv.log.parent / "restart-stage.log",
                                FACTORY_TEST_HOOKS="1")
    try:
        answer = restarted.get(f"/api/tickets/{run.ticket}")
        assert answer.status_code == 200, answer.text
        after = answer.json()["stage"]
        assert after["index"] == before["index"], (
            f"the restart lost the reported stage: it was {before}, it is now {after}")
        assert after["round"] == before["round"], (before, after)
        assert after["total"] == before["total"], (before, after)
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


def test_a_restart_does_not_invent_a_stage_nobody_reported(run):
    """NEGATIVE (mandatory). The cheap fix is to read the runs and guess.

    A ticket whose runs exist and reported NOTHING must still answer index 0 after a restart. A
    tool that inferred `SPEC` from the existence of a run would be holding an opinion about what
    a stage name means, which is the one thing `progress.h` forbids in writing (`SPEC.md` §10) —
    and it would light a chip for work nobody did.
    """
    from conftest import _start_instance, stop_process

    run.accepted("SPEC")                       # a DIFFERENT ticket reports, so the map is not empty
    quiet = run.srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "core"})
    assert quiet.status_code == 201, quiet.text

    stop_process(run.srv.proc)
    restarted = _start_instance(run.root, run.srv.log.parent / "restart-quiet.log",
                                FACTORY_TEST_HOOKS="1")
    try:
        answer = restarted.get("/api/tickets/TCK-002")
        assert answer.status_code == 200, answer.text
        after = answer.json()["stage"]
        assert after["index"] == 0, f"a stage was invented for a ticket that reported none: {after}"
        assert after["round"] == 1, after
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


def test_a_ticket_whose_run_ended_does_not_call_itself_not_started(run):
    """`render.cpp` writes `"process":"not_started"` as a STRING CONSTANT whenever a document has
    no live lane — so a ticket thirteen runs deep answers `not started`, whose own vocabulary note
    reads *"no lane yet"*, beside a `runs` object saying thirteen.

    The tool already recorded what happened: `meta.json` holds the outcome it wrote when it reaped
    the process. The axis has a value for each one (`finished`, `crashed`, `killed`), so reading it
    back is a lookup in the vocabulary rather than an opinion about what happened.
    """
    from conftest import _start_instance, stop_process

    run.accepted("SPEC")
    run.srv.post(f"/api/lanes/{run.lane}/kill", json={"grace_s": 1})
    time.sleep(1.5)

    stop_process(run.srv.proc)
    restarted = _start_instance(run.root, run.srv.log.parent / "restart-state.log",
                                FACTORY_TEST_HOOKS="1")
    try:
        body = restarted.get(f"/api/tickets/{run.ticket}").json()
        assert body["runs"]["count"] > 0, body["runs"]
        assert body["state"]["process"] != "not_started", (
            f"a ticket with {body['runs']['count']} run(s) calls itself never started: "
            f"{body['state']}")
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


def test_a_ticket_nobody_ever_ran_still_says_not_started(run):
    """NEGATIVE (mandatory). The cheap fix is to stop writing `not_started` at all.

    It is the honest answer for a ticket with no runs, and it has to survive the change that stops
    it being the answer for every ticket.
    """
    from conftest import _start_instance, stop_process

    run.accepted("SPEC")
    stop_process(run.srv.proc)
    restarted = _start_instance(run.root, run.srv.log.parent / "restart-untouched.log",
                                FACTORY_TEST_HOOKS="1")
    try:
        body = restarted.get("/api/tickets/TCK-002").json()
        assert body["runs"]["count"] == 0, body["runs"]
        assert body["state"]["process"] == "not_started", body["state"]
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


def test_a_ticket_row_says_what_became_of_its_run_too(run):
    """The row and the ticket page must not disagree.

    `renderTicket` reads the newest run's outcome (E5); `renderRequirement`'s ROWS did not, so a
    requirement page listed a ticket as `not started` while the ticket's own page said `killed`.
    That split — list right, detail wrong, or the reverse — has now cost three fixes this week.

    The cost objection is real and is answered by walking the runs directory ONCE per render and
    indexing it, which is the same thing `catalog::Store::evidenceFor` already does with
    `runIds()`: calling `history::forTicket` per row re-reads every `meta.json` per row.
    """
    from conftest import _start_instance, stop_process

    run.accepted("SPEC")
    run.srv.post(f"/api/lanes/{run.lane}/kill", json={"grace_s": 1})
    time.sleep(1.5)

    stop_process(run.srv.proc)
    restarted = _start_instance(run.root, run.srv.log.parent / "restart-rows.log",
                                FACTORY_TEST_HOOKS="1")
    try:
        page = restarted.get(f"/api/tickets/{run.ticket}").json()
        on_page = page["state"]["process"]
        assert on_page != "not_started", f"E5 regressed: {page['state']}"

        # The fixture's tickets declare no `parent:`, so they land in the synthesised group
        # WEB-FEAT-041 makes for exactly that — which is the E4 page a person actually opens here.
        parent = page["parent"] or "standalone"
        req = restarted.get(f"/api/requirements/{parent}")
        assert req.status_code == 200, req.text
        # E4 groups its rows by workspace; the row is what a person sees, whichever group it is in.
        rows = [t for g in req.json()["groups"] for t in g["tickets"] if t["id"] == run.ticket]
        assert rows, f"{run.ticket} is not on {parent}: {req.text[:300]}"
        assert rows[0]["state"]["process"] == on_page, (
            f"the row says {rows[0]['state']['process']!r} and the ticket page says "
            f"{on_page!r} about the same run")
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


# ── SRV-FIX-599 · how far a lane is, and how long it has left ───────────────────────────────
#
# `TL-FEAT-041` sat at `status: pass` with both lanes drawing `7/10 · 70%`: nothing read the
# status, the stage just ENTERED counted as finished, and the denominator was the manifest's length
# while the numerator was a position in the ticket's own sequence. Every case below lays its
# repository down BEFORE the server starts — manifest, documents and run ledgers — because the
# durations an estimate is built from are the `ts` of stage entries the tool already wrote, and a
# test cannot wait ten minutes to produce one.

HTTP_SCHEMA = json.loads((DOCS / "technical/contracts/http.schema.json").read_text())
LANE_VALIDATOR = jsonschema.Draft202012Validator(
    {"$ref": "#/$defs/lane", "$defs": HTTP_SCHEMA["$defs"]})

SERVER = ["SPEC", "TEST", "IMPL", "CONVENTION", "BUILD", "GATE", "REVIEW", "QA", "MERGE", "VERIFY"]
WORKSPACES = {"core": {"path": "."}, "other": {"path": "."}}
DAY_AGO = time.time() - 86400


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def write_manifest(root, *, stages=SERVER, done=("pass",), status=None):
    path = root / ".factory" / "factory.json"
    manifest = json.loads(path.read_text())
    manifest["stages"] = list(stages)
    manifest["workspaces"] = WORKSPACES
    manifest.pop("status", None)
    if status is not None:
        manifest["status"] = status
    elif done is not None:
        manifest["status"] = {"done": list(done)}
    path.write_text(json.dumps(manifest, indent=2))


def write_ticket(root, tid, *, status="todo", workspace="core", stages=SERVER, title=None):
    (root / "docs" / "tickets" / f"{tid}.md").write_text(
        f"---\nid: {tid}\ntitle: {title or 'ticket ' + tid}\nworkspace: {workspace}\n"
        f"status: {status}\nstages: {' '.join(stages)}\n---\n\nbody of {tid}\n")


_seeded = iter(range(1, 10_000))


def seed_run(root, tid, spans, *, ended=True, start=DAY_AGO):
    """A run ledger exactly as the tool writes one. `spans` is `[(stage, seconds)]`: each stage is
    reported, and the next one `seconds` later. When `ended`, the run's `ended_iso` closes the last
    stage; otherwise the run never says it ended, and its last stage was never left."""
    n = next(_seeded)
    run_id = f"run-{n}-seed{n:04x}"
    d = root / ".factory" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    t = start
    lines = []
    for i, (stage, seconds) in enumerate(spans):
        lines.append(json.dumps({"seq": i + 1, "ts": iso(t), "kind": "stage",
                                 "stage": stage, "round": 1}, separators=(",", ":")))
        t += seconds
    meta = {"run_id": run_id, "lane": "lane/90", "ticket_id": tid, "role": "run",
            "branch": f"ticket/{tid}", "branch_tip": "", "session_id": f"sess-{run_id}",
            "n": n, "started_iso": iso(start + n), "cost_usd": 0, "turns": 1,
            "outcome": "completed" if ended else "killed", "exit_code": 0, "signal": None,
            "unmapped": {}}
    if ended:
        meta["ended_iso"] = iso(t)
        meta["duration_s"] = int(t - start)
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "transcript.jsonl").write_text("\n".join(lines) + "\n")
    return run_id


def finished_run(gate_s):
    """One ticket's run to MERGE, every stage 30 s but GATE, MERGE closed by the run's end, and
    VERIFY never reported — the shape of every run in `example-platform` that reached a merge."""
    return [(s, gate_s if s == "GATE" else 30) for s in SERVER[:9]]


def to(stage, seconds=30):
    """Stages reported up to and including `stage`, in the server sequence."""
    return [(s, seconds) for s in SERVER[:SERVER.index(stage) + 1]]


def seed_history(root, gates=(60, 100, 600)):
    for i, gate in enumerate(gates, 1):
        write_ticket(root, f"HIST-{i}")
        seed_run(root, f"HIST-{i}", finished_run(gate))


def lane_rows(env):
    return {row["lane"]: row for row in env.lanes()}


def conform(rows):
    """E9's rows against `http.schema.json` — asserted after each case's own claim, so a RED run
    says what the criterion is about before it says what the schema is missing."""
    for row in rows.values():
        LANE_VALIDATOR.validate(row)


def claim(env, ticket, workspace="core", role=None):
    body = {"ticket_id": ticket, "workspace": workspace}
    if role:
        body["role"] = role
    r = env.srv.post("/api/lanes", json=body)
    assert r.status_code == 201, r.text
    return r.json()["lane"]


def wait_stage(env, conn, accept, timeout=8):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        for f in env.frames(conn, "stage", seconds=0.3):
            seen.append(f)
            if accept(f):
                return f
    raise AssertionError(f"no stage frame matched; these arrived: {seen}")


def test_a_done_ticket_is_full_on_every_lane_and_a_failed_one_is_not(env):
    """§1.1 — two tickets, both reported to REVIEW (7 of 10), one at `pass` with a `run` and a
    `qa` lane, one at `fail`. The done one is full on BOTH lanes; the failed one is its share."""
    def prepare(root):
        write_manifest(root)
        for tid in ("DONE-1", "FAIL-1"):
            write_ticket(root, tid)
            seed_run(root, tid, to("REVIEW"), ended=False)
    env.build(prepare)

    run_lane = claim(env, "DONE-1")
    qa_lane = claim(env, "DONE-1", role="qa")
    failed = claim(env, "FAIL-1")
    write_ticket(env.root, "DONE-1", status="pass")
    write_ticket(env.root, "FAIL-1", status="fail")

    rows = lane_rows(env)
    got = [(rows[l]["stage"]["index"], rows[l]["progress_pct"], rows[l].get("remaining_s", "absent"))
           for l in (run_lane, qa_lane, failed)]
    assert (got[0][1:], got[1][1:], got[2][:2]) == ((100, 0), (100, 0), (7, 60)) \
        and (got[2][2] is None or isinstance(got[2][2], int)), got
    conform(lane_rows(env))


def test_a_status_entering_done_pushes_a_full_stage_frame(env):
    """§1.2 — the document changing on disk is the trigger; the subscriber never asks E9."""
    def prepare(root):
        write_manifest(root)
        write_ticket(root, "DONE-1")
        seed_run(root, "DONE-1", to("REVIEW"), ended=False)
    env.build(prepare)
    lane = claim(env, "DONE-1")
    conn = env.subscribed({"lane": lane})

    write_ticket(env.root, "DONE-1", status="pass")
    full = wait_stage(env, conn, lambda f: f["lane"] == lane and f["progress_pct"] == 100)
    write_ticket(env.root, "DONE-1", status="todo")
    back = wait_stage(env, conn, lambda f: f["lane"] == lane and f["progress_pct"] != 100)

    assert (full.get("remaining_s", "absent"), full["stage"]["index"], back["progress_pct"],
            back["stage"]["index"]) == (0, 7, 60, 7), (full, back)
    conform(lane_rows(env))


def test_the_percent_counts_stages_finished_not_entered(env):
    """§1.3 — a live run reports every stage of a ten-stage ticket under a three-stage manifest.
    The share after each report is the stages BEFORE it, on E9 and on M3 alike."""
    env.build(lambda root: write_ticket(root, "TCK-001"))
    env.claim()
    env.start()
    env.wait_log("ready")
    conn = env.subscribed()

    on_e9, on_m3 = [], []
    for stage in SERVER:
        env.accepted(stage)
        frame = wait_stage(env, conn, lambda f, s=stage: f["stage"]["name"] == s)
        on_m3.append((frame["stage"]["total"], frame["progress_pct"]))
        row = lane_rows(env)[env.lane]
        on_e9.append((row["stage"]["total"], row["progress_pct"]))

    expected = [(10, 10 * i) for i in range(10)]
    assert (on_e9, on_m3) == (expected, expected), (on_e9, on_m3)
    conform(lane_rows(env))


def test_the_total_is_the_tickets_own_sequence(env):
    """§1.4 — manifest of ten, ticket of eight, reported to its fifth: 50, where the manifest's
    length would give 40. On E9, and on the M3 a document change sends."""
    eight = SERVER[:8]

    def prepare(root):
        write_manifest(root)
        write_ticket(root, "EIGHT-1", stages=eight)
        seed_run(root, "EIGHT-1", to("BUILD"), ended=False)
    env.build(prepare)
    lane = claim(env, "EIGHT-1")
    conn = env.subscribed({"lane": lane})

    row = lane_rows(env)[lane]
    write_ticket(env.root, "EIGHT-1", stages=eight, title="renamed")
    frame = wait_stage(env, conn, lambda f: f["lane"] == lane)

    assert ((row["stage"]["index"], row["stage"]["total"], row["progress_pct"]),
            (frame["stage"]["index"], frame["stage"]["total"], frame["progress_pct"])) \
        == ((5, 8, 50), (5, 8, 50)), (row, frame)
    conform(lane_rows(env))


def test_time_left_is_the_median_of_measured_stages_minus_time_spent(env):
    """§1.5 — GATE measured at 60/100/600 s (median 100, mean 253), REVIEW/QA/MERGE at 30 s,
    VERIFY never. A live run enters GATE, the clock moves 40 s: 100 - 40 + 3 × 30 + 0 = 150."""
    def prepare(root):
        write_manifest(root)
        seed_history(root)
        write_ticket(root, "TCK-001")
    env.build(prepare)
    env.claim("TCK-001")
    env.start()
    env.wait_log("ready")
    for stage in SERVER[:SERVER.index("GATE") + 1]:
        env.accepted(stage)
    r = env.srv.post("/api/_test/clock", json={"advance_s": 40})
    assert r.status_code == 202, r.text

    left = lane_rows(env)[env.lane].get("remaining_s", "absent")
    assert isinstance(left, int) and 145 <= left <= 150, left
    conform(lane_rows(env))


def test_time_left_never_borrows_another_workspaces_history(env):
    """§1.6 — the same history, two lanes at GATE: one in the workspace that measured it, one in
    a workspace that measured nothing."""
    def prepare(root):
        write_manifest(root)
        seed_history(root)
        write_ticket(root, "CORE-1")
        seed_run(root, "CORE-1", to("GATE"), ended=False)
        write_ticket(root, "ELSE-1", workspace="other")
        seed_run(root, "ELSE-1", to("GATE"), ended=False)
    env.build(prepare)
    here = claim(env, "CORE-1")
    there = claim(env, "ELSE-1", workspace="other")

    rows = lane_rows(env)
    assert (rows[here].get("remaining_s", "absent"), rows[there].get("remaining_s", "absent")) == (90, None), rows
    conform(lane_rows(env))


def test_time_left_is_null_below_three_samples_and_a_stage_never_left_is_not_one(env):
    """§1.7 — NEGATIVE. GATE is measured twice; a third run reached GATE and never said it ended,
    so that GATE is not a measurement. And a lane whose every stage ahead was never measured has
    no estimate — not zero."""
    def prepare(root):
        write_manifest(root)
        seed_history(root, gates=(60, 100))
        write_ticket(root, "HIST-3")
        seed_run(root, "HIST-3", [(s, t) for s, t in finished_run(0) if s != "GATE"])
        write_ticket(root, "HIST-4")
        seed_run(root, "HIST-4", to("GATE"), ended=False)
        write_ticket(root, "LIVE-1")
        seed_run(root, "LIVE-1", to("GATE"), ended=False)
        write_ticket(root, "NONE-1", workspace="other")
        seed_run(root, "NONE-1", to("SPEC"), ended=False)
    env.build(prepare)
    short = claim(env, "LIVE-1")
    nothing = claim(env, "NONE-1", workspace="other")

    rows = lane_rows(env)
    assert ([k in rows[short] for k in ("remaining_s",)], rows[short].get("remaining_s", "absent"),
            rows[nothing].get("remaining_s", "absent")) == ([True], None, None), rows
    conform(lane_rows(env))


def test_an_unknown_stage_has_no_share_and_no_estimate(env):
    """§1.9 — NEGATIVE. A reported name the sequence does not contain stays index 0: no share, and
    no estimate, even with a measured history beside it."""
    def prepare(root):
        write_manifest(root)
        seed_history(root)
        write_ticket(root, "ODD-1")
        seed_run(root, "ODD-1", [("SPEC", 30), ("DEPLOY", 30)], ended=False)
    env.build(prepare)
    lane = claim(env, "ODD-1")

    row = lane_rows(env)[lane]
    assert (row["stage"]["index"], row["progress_pct"], row.get("remaining_s", "absent")) == (0, 0, None), row
    conform(lane_rows(env))


def test_a_reused_lane_name_does_not_inherit_done(env):
    """§1.10 — a lane of a done ticket is released and its name claimed for a ticket at its
    second stage. The name is the same; the bar is the new ticket's."""
    def prepare(root):
        write_manifest(root)
        write_ticket(root, "DONE-1")
        seed_run(root, "DONE-1", to("REVIEW"), ended=False)
        write_ticket(root, "NEXT-1")
        seed_run(root, "NEXT-1", to("TEST"), ended=False)
    env.build(prepare)
    lane = claim(env, "DONE-1")
    write_ticket(env.root, "DONE-1", status="pass")
    before = lane_rows(env)[lane]["progress_pct"]

    r = env.srv.delete("/api/lanes/" + lane)
    assert r.status_code == 200, r.text
    # A name is never reused inside one process (`test_lane.py`'s `nextName_` case); a restart is
    # what hands it out again, and it is where a record keyed by the name would carry over.
    from conftest import _start_instance, stop_process
    stop_process(env.srv.proc)
    old = env.srv
    env.srv = _start_instance(env.root, old.log.parent / "restart-reuse.log", FACTORY_TEST_HOOKS="1")
    try:
        again = claim(env, "NEXT-1")
        row = lane_rows(env)[again]
        assert (before, again, row["ticket_id"], row["progress_pct"]) \
            == (100, lane, "NEXT-1", 10), row
        conform(lane_rows(env))
    finally:
        stop_process(env.srv.proc)
        env.srv.handle.close()
        env.srv = old


# ── SRV-FIX-599 round 2 · QA F1: a sample belongs to ONE run ──────────────────────────────────

def test_an_unended_runs_last_stage_is_not_a_sample_when_a_later_run_exists(env):
    """§1.7, QA round 1 reproduction P2 (`SRV-FIX-599-qa.md` F1), transcribed.

    GATE is measured exactly twice by ended runs, so §1.7 says `null`.

    SPLIT-1 adds a run that reached GATE and never said it ended, followed by a LATER run of the
    same ticket. If the un-ended GATE is closed by the next RUN's first entry, GATE has three
    samples and the answer stops being `null`.
    """
    def prepare(root):
        write_manifest(root)
        # Exactly §1.7's history: GATE measured twice, every other stage three times.
        seed_history(root, gates=(60, 100))
        write_ticket(root, "HIST-3")
        seed_run(root, "HIST-3", [(s, t) for s, t in finished_run(0) if s != "GATE"])
        # The one thing §1.7's fixture never builds: an un-ended run FOLLOWED by another run of
        # the same ticket. Both lanes of one ticket (§1.1's own shape) produce exactly this.
        write_ticket(root, "SPLIT-1")
        seed_run(root, "SPLIT-1", to("GATE"), ended=False, start=DAY_AGO)
        seed_run(root, "SPLIT-1", [("SPEC", 30), ("TEST", 30)], ended=True, start=DAY_AGO + 5000)
        write_ticket(root, "LIVE-1")
        seed_run(root, "LIVE-1", to("GATE"), ended=False)
    env.build(prepare)
    live = claim(env, "LIVE-1")

    row = lane_rows(env)[live]
    assert row["remaining_s"] is None, (
        "an un-ended run's last stage became a sample because another run followed it: " + str(row))


def test_two_live_lanes_of_one_ticket_do_not_fabricate_a_sample_for_the_stage_neither_left(env):
    """§1.7, QA round 1 reproduction P3 (`SRV-FIX-599-qa.md` F1), transcribed.

    The same defect with no seeded ledger at all — §1.1's own two-lane shape, live.

    GATE is measured twice, so a lane at GATE answers `null` (§1.7). Then the ticket's `run` lane
    reports GATE and its `qa` lane reports QA. Neither run has ended and neither has left GATE,
    so §1.7 says no new sample exists and the other lane must still answer `null`.
    """
    def prepare(root):
        write_manifest(root)
        seed_history(root, gates=(60, 100))
        write_ticket(root, "HIST-3")
        seed_run(root, "HIST-3", [(s, t) for s, t in finished_run(0) if s != "GATE"])
        write_ticket(root, "LIVE-1")
        seed_run(root, "LIVE-1", to("GATE"), ended=False)
        write_ticket(root, "TCK-001")
    env.build(prepare)

    live = claim(env, "LIVE-1")
    before = lane_rows(env)[live]["remaining_s"]
    assert before is None, f"the fixture does not start from null: {before}"

    env.lane, env.ticket = claim(env, "TCK-001"), "TCK-001"
    env.start()
    env.wait_log("ready")
    env.accepted("GATE")

    qa = claim(env, "TCK-001", role="qa")
    env.lane = qa
    env.start()
    env.wait_log("ready")
    env.accepted("QA")

    after = lane_rows(env)[live]["remaining_s"]
    assert after is None, (
        f"GATE gained a sample from a run that never left it: it was {before}, it is now {after}")
