"""SRV-FIX-096 — a run that died unreaped says `running` on disk and `not started` on screen.

The two facts this file is about are both persistent and both wrong: `meta.json` keeps the
non-terminal word `running` for ever, and `processOf()` maps everything it does not recognise to
`not_started` — so the most expensive runs in a repository render as though nothing ever happened.
Measured on this repository, 2026-08-06: two runs, 6354 s and 6340 s, $2.65 together, two turns
each, both `outcome: "running"` with `ended_iso: null` and no process alive.
"""

import json
import os
import signal
import time
from pathlib import Path

import jsonschema
import pytest

from test_agent import agentenv, proc_state  # noqa: F401
from test_delivery import base_repo, gitrepo, serve_fast  # noqa: F401


FACTORY = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((FACTORY / "docs/technical/contracts/http.schema.json").read_text())
SOCKET_SCHEMA = json.loads((FACTORY / "docs/technical/contracts/socket.schema.json").read_text())

# The words each contract names, read off the documents rather than typed here. SRV-FIX-344 is
# exactly a disagreement between two of these sets, so a set written down in this file would be a
# third opinion and could only ever agree with whoever wrote it.
SOCKET_OUTCOMES = set(SOCKET_SCHEMA["$defs"]["message"]["run"]["properties"]["outcome"]["enum"])


# The shape a real `meta.json` has — every field `history::write` emits. QA round 1's finding is
# that the FIXTURE SHAPE is the check: a planted meta carrying only the fields a test happens to
# assert on cannot show that the settle drops the ones it does not.
FULL_SHAPE = {
    # `signal: null` is the tool's canonical "no signal" — `history::write` emits null for
    # anything <= 0 on purpose (`a signal: 0 next to exit_code: 9 is how a record says
    # "returned 9"`). Planting 0 would assert that the settle preserves a value the writer never
    # produces, and correcting the fixture is not the same as weakening the check: every other
    # field below still has a non-default value that must survive untouched.
    "exit_code": 3, "signal": None,
    "rate_limit": {"type": "rate_limit_event", "rate_limit_info": {
        "status": "rejected", "overageStatus": "rejected",
        "overageDisabledReason": "org_level_disabled", "utilization": 0.95}},
    "unmapped": {"rate_limit_event": 1, "system": 1},
}


def plant_run(root, ticket, run_id="run-1-deadbeef", outcome="running", ended=None, **extra):
    """A run record as `agent::Store` writes one, planted without a process behind it."""
    d = Path(root) / ".factory" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id, "lane": "lane/01", "ticket_id": ticket, "session_id": "s1", "n": 1,
        "started_iso": "2026-08-06T10:00:00Z", "ended_iso": ended, "outcome": outcome,
        "duration_s": 6354, "cost_usd": 1.86, "turns": 2, "exit_code": None, "signal": None,
    }
    meta.update(extra)
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "transcript.jsonl").write_text("")
    return d


def meta_of(d):
    return json.loads((Path(d) / "meta.json").read_text())


def state_of(srv, requirement, ticket):
    """The ticket's own row inside E4's requirement, which nests rows under `groups`.

    Falling back to the requirement's own `state` would assert about the aggregate and pass for
    the wrong reason — the first cut of this helper did exactly that.
    """
    body = srv.get(f"/api/requirements/{requirement}").json()
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("id") == ticket and "state" in node:
                found.append(node["state"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(body)
    assert found, f"{ticket} has no row in the requirement response: {sorted(body.keys())}"
    return found[0]


def test_a_stale_running_meta_is_settled_at_startup(repo, gitrepo, serve):
    """A run whose process is gone must stop claiming to be running, on disk and on screen.

    `outcome: "running"` is not a verdict — it is the word written when the run STARTED, and
    nothing rewrites it if the agent dies without a reap. After a restart the lane table is empty
    (lanes are live state, deliberately), so this file is all that remains, and it says the run is
    still going while `processOf` renders the word it does not recognise as `not_started`.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-90", "parent": "REQ-001", "workspace": "alpha",
         "title": "Died unreaped", "status": "in-progress"},
    ])
    d = plant_run(repo.root, "TKT-90")

    srv = serve(repo.root)
    st = state_of(srv, "REQ-001", "TKT-90")
    assert st.get("process") != "not_started", (
        f"a run of 6354 s and $1.86 renders as never started: {st}")

    settled = meta_of(d)
    assert settled["outcome"] != "running", (
        f"the meta still claims to be running with no process behind it: {settled}")
    assert settled["ended_iso"], f"a settled run has no end time: {settled}"


def test_a_live_run_is_not_settled(agentenv):
    """The settle is for ORPHANS, and the live set is the whole of its correctness.

    The sweep runs at startup AND whenever a lane frees, so it fires with other runs alive — which
    is exactly when a missing guard would do damage. Rewriting a live run's record would make the
    tool lie about the one case it can actually see (`SRV-FIX-081`: live lookup wins).

    A run is started for real, a second lane is claimed and released to trigger a sweep, and the
    live run's record must be untouched.
    """
    env = agentenv.build()
    holder = env.claim(ticket="TCK-001")
    run = env.start(holder)
    env.wait_log(holder, "ready")

    meta_path = Path(env.root) / ".factory" / "runs" / run["run_id"] / "meta.json"
    assert json.loads(meta_path.read_text())["outcome"] == "running", meta_path.read_text()

    # A second lane that actually RUNS and then dies: the sweep hangs off a run ending, so a lane
    # released without ever starting one triggers nothing. The first cut of this case did that and
    # passed with the guard deleted — it never reached the code it was written for.
    second = env.claim(ticket="TCK-002")
    env.start(second)
    env.wait_log(second, "ready")
    assert env.srv.delete(f"/api/lanes/{second['lane']}?force=true").status_code == 200
    time.sleep(1.5)

    after = json.loads(meta_path.read_text())
    assert after["outcome"] == "running", (
        f"a run whose lane is alive was settled by the orphan sweep: {after}")
    assert "settled_from" not in after, after

    assert env.srv.delete(f"/api/lanes/{holder['lane']}?force=true").status_code == 200


def test_the_settle_keeps_every_field_it_did_not_come_for(repo, gitrepo, serve):
    """QA round 1 — §1's Safety clause, enforced by a check instead of by care.

    *"The settle rewrites `meta.json` — it may touch ONLY the `outcome`, `ended_iso` and a
    `settled` note; every other field is evidence and immutable."*

    It did not. `readMeta` parses the fields E7's rows need and no others, so a settle that read a
    record and wrote the struct back erased `rate_limit`, `unmapped`, `exit_code` and `signal`.
    Measured on this repository's own `.factory/runs`: four runs lost the `rate_limit` that was
    the recorded reason they stalled, and it is not recoverable.

    The fixture is the finding. A planted meta carrying only what a test asserts on cannot show
    what the settle drops — which is why the first cut of this file passed while the loss was
    happening.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-93", "parent": "REQ-001", "workspace": "alpha",
         "title": "Full shape", "status": "in-progress"},
    ])
    d = plant_run(repo.root, "TKT-93", run_id="run-1-fullshape", **FULL_SHAPE)
    before = meta_of(d)

    srv = serve(repo.root)
    srv.get("/api/requirements/REQ-001")
    after = meta_of(d)

    assert after["outcome"] == "orphaned", after
    assert after["settled_from"] == "running", after
    assert after["ended_iso"], after

    for key, was in before.items():
        if key in ("outcome", "ended_iso"):
            continue
        assert key in after, f"the settle DROPPED {key!r}: it was {was!r}"
        assert after[key] == was, f"the settle CHANGED {key!r}: {was!r} -> {after[key]!r}"


def test_the_runs_tab_says_what_happened(repo, gitrepo, serve):
    """E16's row for a settled run carries the settled outcome, not a blank."""
    base_repo(repo, gitrepo, [
        {"id": "TKT-92", "parent": "REQ-001", "workspace": "alpha",
         "title": "Shown on the runs tab", "status": "in-progress"},
    ])
    plant_run(repo.root, "TKT-92", run_id="run-1-shown")

    srv = serve(repo.root)
    rows = srv.get("/api/tickets/TKT-92/runs").json()
    runs = rows.get("runs", rows if isinstance(rows, list) else [])
    assert runs, f"the runs tab shows nothing for a ticket with a run: {rows}"
    assert runs[0].get("outcome"), f"the row carries no outcome: {runs[0]}"
    assert runs[0]["outcome"] != "running", (
        f"the runs tab still reports a dead run as running: {runs[0]}")


# ── SRV-FIX-344 · one record, and every surface that serves it able to say the word ──────────
#
# `settleOrphans` writes a sixth word, `orphaned`, over `running` when nothing is behind the
# record any more (`history.cpp:352-380`), and `renderRow` puts it on E7's wire verbatim. E7's
# own node in `http.schema.json` named five words, so from the first restart with a run in flight
# every row for that run violated the schema the client's types are generated from — permanently,
# because the record outlives the process.
#
# EVERY CASE BELOW READS THE ENUM OFF THE DOCUMENT. A case that named the six words itself would
# pass against a schema that still names five, which is the whole shape of the defect.

E7_NODE = SCHEMA["responses"]["E7"]
E7_VALIDATOR = jsonschema.Draft202012Validator(
    dict(E7_NODE, **{"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"]}))
E31_VALIDATOR = jsonschema.Draft202012Validator(
    dict(SCHEMA["responses"]["E31"], **{"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"]}))


def runs_of(srv, ticket):
    r = srv.get(f"/api/tickets/{ticket}/runs")
    assert r.status_code == 200, f"E7 for {ticket} → {r.status_code} {r.text}"
    return r.json()


def row_named(body, run_id):
    found = [row for row in body.get("runs", []) if row.get("run_id") == run_id]
    assert len(found) == 1, f"{run_id} is not a single row of {body}"
    return found[0]


def wait_settled(path, timeout=15):
    """The record on disk, once the sweep has been over it.

    The sweep is posted to a worker (`session.cpp:103`), so the startup that triggers it returns
    before it has run. Waiting on the FACT the sweep produces — the word in the file — rather than
    on time passing is `SRV-FIX-034`'s rule; a `time.sleep` here is a coin flip.
    """
    deadline = time.time() + timeout
    meta = {}
    while time.time() < deadline:
        try:
            meta = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            meta = {}
        if meta.get("outcome") and meta["outcome"] != "running":
            return meta
        time.sleep(0.05)
    raise AssertionError(f"{path} was never settled within {timeout}s; it reads {meta}")


def test_e7_serves_a_settled_run_and_the_schema_admits_it(repo, gitrepo, serve):
    """A person opens the RUNS tab of a ticket whose run was settled, and the body they are served
    is one the contract describes.

    The fixture is `settleOrphans`'s own output, field for field — `outcome: "orphaned"` beside
    `settled_from: "running"` — so the row under the validator is the row the writer produces.
    Validated against `$defs.runRow` READ OFF `docs/technical/contracts/http.schema.json` at test time: the words are
    never typed here, because a case that typed them would agree with itself.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-344", "parent": "REQ-001", "workspace": "alpha",
         "title": "Settled while nobody was watching", "status": "in-progress"},
    ])
    plant_run(repo.root, "TKT-344", run_id="e7probe-orphan", outcome="orphaned",
              ended="2026-09-02T10:06:05Z", settled_from="running")

    srv = serve(repo.root)
    body = runs_of(srv, "TKT-344")

    assert body["runs"], f"the sweep's own record is not a row at all: {body}"
    assert row_named(body, "e7probe-orphan")["outcome"] == "orphaned", body

    E7_VALIDATOR.validate(body)

    # …and it was E7's own node, resolving to the row `runRow` really is
    assert E7_NODE["path"] == "/api/tickets/{id}/runs", E7_NODE
    assert E7_NODE["method"] == "GET" and E7_NODE["status"] == 200, E7_NODE
    assert E7_NODE["properties"]["runs"]["items"]["$ref"] == "#/$defs/runRow", E7_NODE


def test_the_settle_sweep_writes_only_words_the_wire_can_carry(agentenv, serve):
    """Restarting the server while a run is in flight leaves a row a person can still be served.

    Nothing is planted. A real agent is started through the product, the SERVER is killed without
    a chance to reap or record anything — which is what leaves `running` on disk with no process
    behind it — the orphaned child is killed too, and a second server is started on the same
    repository so `agent::Store::start` runs the sweep for real. The row E7 then serves is
    validated against the document.

    Criterion 1 proves the schema; this proves the WRITER and the schema agree, which is the
    defect. A fixture cannot: the fixture is written by the same session as the assertion.
    """
    env = agentenv.build()
    lane = env.claim(ticket="TCK-001")
    run = env.start(lane)
    env.wait_log(lane, "ready")

    meta_path = Path(env.root) / ".factory" / "runs" / run["run_id"] / "meta.json"
    assert json.loads(meta_path.read_text())["outcome"] == "running", meta_path.read_text()

    # SIGKILL, not the shutdown path: a server that got to run its shutdown would have written a
    # terminal outcome, and the state this case is about is the one where nobody wrote anything.
    env.srv.proc.kill()
    env.srv.proc.wait(timeout=20)
    # The child may already have gone with the write end of its FIFO; either way it must be gone
    # before the second server starts, because a live process behind the record is the one state
    # the sweep is required NOT to touch.
    try:
        os.kill(run["pid"], signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.time() + 20
    while time.time() < deadline and proc_state(run["pid"]) not in (None, "Z"):
        time.sleep(0.05)
    assert proc_state(run["pid"]) in (None, "Z"), (
        f"the run is still alive in state {proc_state(run['pid'])!r}, so the sweep would be right "
        f"to leave its record alone and this case would prove nothing")

    assert json.loads(meta_path.read_text())["outcome"] == "running", (
        "the record no longer says `running`, so this case never reached the sweep: "
        + meta_path.read_text())

    again = serve(env.root)
    settled = wait_settled(meta_path)
    assert settled["outcome"] == "orphaned", settled
    assert settled["settled_from"] == "running", settled

    body = runs_of(again, "TCK-001")
    assert row_named(body, run["run_id"])["outcome"] == "orphaned", body
    E7_VALIDATOR.validate(body)


def test_a_word_the_schema_does_not_name_still_fails(repo, gitrepo, serve):
    """(negative) An outcome nobody declared is refused by the same validator that admitted
    `orphaned`.

    E7 serves `meta.outcome` verbatim — there is no sanitising step — so a record carrying
    `"vanished"` reaches the wire, and the contract has to be the thing that says no. Without this
    case, criterion 1 passes just as well against an enum widened to `{"type": "string"}`, which
    deletes the check instead of fixing it.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-345", "parent": "REQ-001", "workspace": "alpha",
         "title": "A word the tool never wrote", "status": "in-progress"},
    ])
    plant_run(repo.root, "TKT-345", run_id="e7probe-vanished", outcome="vanished",
              ended="2026-09-02T11:00:00Z")

    srv = serve(repo.root)
    body = runs_of(srv, "TKT-345")
    assert row_named(body, "e7probe-vanished")["outcome"] == "vanished", (
        "the server did not serve the invented word, so this case is not measuring the schema: "
        + json.dumps(body))

    with pytest.raises(jsonschema.ValidationError) as refused:
        E7_VALIDATOR.validate(body)
    assert "'vanished' is not one of" in str(refused.value), str(refused.value)

    # and the enum it was refused against is the DOCUMENT's, still a closed set of words
    outcome = SCHEMA["$defs"]["runRow"]["properties"]["outcome"]
    assert "enum" in outcome, (
        "`runRow.outcome` no longer names its words — an open string admits `vanished` too: "
        + json.dumps(outcome))
    assert "vanished" not in outcome["enum"], outcome["enum"]


def test_e7_and_e31_describe_one_record_the_same_way(repo, gitrepo, serve):
    """The same run, asked for through the ticket's tab and through the repository-wide list, comes
    back with one word in both.

    Two kinds in the population, because a reduction over one kind is the identity: one run the
    sweep settles (`running` on disk, no process) and one it must leave alone (`completed`). Both
    endpoints are asked about both, so the two agreeing cannot be an endpoint answering with a
    constant.
    """
    base_repo(repo, gitrepo, [
        {"id": "TKT-346", "parent": "REQ-001", "workspace": "alpha",
         "title": "One record, two lists", "status": "in-progress"},
    ])
    settling = plant_run(repo.root, "TKT-346", run_id="e7probe-both-settled", outcome="running")
    plant_run(repo.root, "TKT-346", run_id="e7probe-both-done", outcome="completed",
              ended="2026-09-01T09:01:00Z", started_iso="2026-09-01T09:00:00Z")

    srv = serve(repo.root)
    wait_settled(settling / "meta.json")

    e7 = runs_of(srv, "TKT-346")
    index = srv.get("/api/runs")
    assert index.status_code == 200, index.text
    e31 = index.json()

    E7_VALIDATOR.validate(e7)
    E31_VALIDATOR.validate(e31)

    both = {"e7probe-both-settled": "orphaned", "e7probe-both-done": "completed"}
    for run_id, expected in both.items():
        seven = row_named(e7, run_id)["outcome"]
        thirty_one = row_named(e31, run_id)["outcome"]
        assert seven == thirty_one, (
            f"{run_id} is {seven!r} through E7 and {thirty_one!r} through E31 — two shapes for one "
            f"record is what SPEC.md §5 E31 says must not happen")
        assert seven == expected, f"{run_id} is {seven!r}, wanted {expected!r}"

    # byte-identical on the wire, not merely equal after two parsers had a go at them
    for run_id in both:
        needle = f'"run_id":"{run_id}"'
        assert needle in index.text and needle in srv.get("/api/tickets/TKT-346/runs").text
    assert '"outcome":"orphaned"' in index.text, index.text
    assert '"outcome":"orphaned"' in srv.get("/api/tickets/TKT-346/runs").text


def test_the_settle_is_silent_on_the_socket_that_m11_speaks_on(agentenv):
    """§1's second Pre-decided item, answered with the product rather than with a reading.

    `M11 run` is published from `agent::Store::publish_` and from nowhere else; the settle sweep
    writes `meta.json` and has no run object to publish from. So the socket cannot carry
    `orphaned` — and `socket.schema.json`'s five words are right, while `runRow`'s DESCRIPTION,
    which claimed E7 carried "the same five M11 carries", is what was wrong.

    Two connections, because a case where nothing arrives cannot tell silence from a broken
    socket: one subscribed to the ticket whose record the sweep settles, one to the ticket whose
    run really ends. The second MUST see `run` frames; the first must see none carrying the word.
    """
    env = agentenv.build()

    stale = plant_run(env.root, "TCK-002", run_id="m11probe-stale", outcome="running")

    settled_scope = env.srv.ws()
    live_scope = env.srv.ws()
    try:
        settled_scope.send({"m": "subscribe", "scope": {"ticket": "TCK-002"}})
        settled_scope.wait_for("snapshot")
        live_scope.send({"m": "subscribe", "scope": {"ticket": "TCK-001"}})
        live_scope.wait_for("snapshot")

        # a real run on the other ticket, ended through the product: ending one is the sweep's
        # second trigger (`session.cpp:706`), so the settle happens with both sockets open
        lane = env.claim(ticket="TCK-001")
        run = env.start(lane)
        env.wait_log(lane, "ready")
        assert env.srv.post(f"/api/lanes/{lane['lane']}/kill",
                            json={"grace_s": 1}).status_code in (200, 202)

        live = [f for f in live_scope.drain(6.0) if f.get("m") == "run"]
        settled_frames = settled_scope.drain(2.0)

        assert live, (
            "no `run` frame reached a connection subscribed to the ticket whose run just ended — "
            "this case cannot tell silence from a socket that carries nothing")
        assert all(f["outcome"] in SOCKET_OUTCOMES for f in live), (
            f"M11 carried a word `socket.schema.json` does not name: "
            f"{[f['outcome'] for f in live]} against {sorted(SOCKET_OUTCOMES)}")

        assert wait_settled(stale / "meta.json")["outcome"] == "orphaned", (
            "the sweep never ran, so nothing here measured what it publishes")

        orphan_frames = [f for f in settled_frames if f.get("outcome") == "orphaned"]
        assert orphan_frames == [], (
            f"the settle published a frame after all, so M11 IS the socket half of this defect "
            f"and its enum must widen with E7's: {orphan_frames}")
        assert "orphaned" not in SOCKET_OUTCOMES, (
            "`socket.schema.json` names a word M11 has no way to send")
    finally:
        settled_scope.close()
        live_scope.close()
