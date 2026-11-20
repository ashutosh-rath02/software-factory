"""SRV-FEAT-005 — the socket contract.

One socket at /ws carrying named JSON frames. A surface subscribes to a scope and gets a
snapshot for it, then that scope's deltas and no other's. A client that was asleep resumes
from the last sequence it holds, and when the log cannot cover the gap it is given a snapshot
and told to discard its tail.

Every frame these cases exchange is validated against `factory/docs/technical/contracts/socket.schema.json` — the
same file the client's types are generated from. A contract that is only checked by reading it
is not a contract.
"""

import json
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/technical/contracts/socket.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def valid(frame):
    """The frame, after the schema has judged it. Returns it so calls can be chained."""
    VALIDATOR.validate(frame)
    return frame


MANIFEST = {
    "factory": "0.1",
    "workspaces": {"only": {"path": "."}},
    "docs": {"epics": "product/epics", "tickets": "product/tickets",
             "reports": "product/reports"},
    "stages": ["ONE", "TWO"],
}


def _doc(id_, title, body="Some body."):
    return f"---\nid: {id_}\ntitle: {title}\nworkspace: only\nstatus: todo\n---\n\n{body}\n"


@pytest.fixture
def repo_with_docs(repo):
    repo.manifest(MANIFEST)
    repo.write("product/epics/REQ-001.md", _doc("REQ-001", "The first requirement"))
    repo.write("product/tickets/TKT-A.md", _doc("TKT-A", "Ticket A", "Body of A."))
    repo.write("product/tickets/TKT-B.md", _doc("TKT-B", "Ticket B", "Body of B."))
    repo.mkdir("product/reports")
    return repo


@pytest.fixture
def srv(repo_with_docs, serve):
    """A server with the transcript test hook on and a five-frame log, so the gap the
    resume fallback exists for can be produced in a test instead of waited for.

    It is a git repository because `M2 lane.state` carries SPEC.md §3.5's derived state, and a
    frame the contract describes has to be produced by something a case can actually do.
    """
    git(repo_with_docs.root, "init", "-b", "main")
    git(repo_with_docs.root, "config", "user.email", "t@example.invalid")
    git(repo_with_docs.root, "config", "user.name", "Test")
    git(repo_with_docs.root, "add", "-A")
    git(repo_with_docs.root, "commit", "-m", "the repository as it stands", "--no-gpg-sign")
    return serve(repo_with_docs.root, FACTORY_TEST_HOOKS="1", FACTORY_TRANSCRIPT_LOG="5",
                 FACTORY_DELIVERY_POLL_MS="200")


def git(root, *args):
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, f"git {' '.join(args)} → {out.stderr}"
    return out.stdout.strip()


def emit(srv, lane, run_id, kind="prose", **payload):
    """One transcript entry, through the hook a real run will replace (SRV-FEAT-010)."""
    r = srv.post("/api/_test/transcript",
                 json={"lane": lane, "run_id": run_id, "entry": dict(kind=kind, **payload)})
    assert r.status_code == 202, r.text
    return r.json()["seq"]


def opened(srv):
    """A connection past its greeting, with the greeting judged."""
    c = srv.ws()
    hello = valid(c.recv())
    assert hello["m"] == "hello", hello
    assert hello["factory"] == "0.1", hello
    return c


# ── the seven ────────────────────────────────────────────────────────────────────────────

def test_subscribe_replies_with_snapshot(srv):
    """M14 with a scope produces exactly one M1 for that scope, shaped like the endpoint
    that answers the same question, and nothing for any other scope."""
    c = opened(srv)

    c.send({"m": "subscribe", "scope": {"requirements": True}})
    snap = valid(c.wait_for("snapshot"))
    assert snap["scope"] == {"requirements": True}, snap
    assert snap["seq"] is None, "requirements is state; only a lane scope carries a sequence"
    assert snap["data"] == srv.get("/api/requirements").json(), snap["data"]

    d = opened(srv)
    d.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    tsnap = valid(d.wait_for("snapshot"))
    assert tsnap["scope"] == {"ticket": "TKT-A"}, tsnap
    assert tsnap["data"] == srv.get("/api/tickets/TKT-A").json(), tsnap["data"]

    # exactly one, and nothing for any other scope
    assert d.drain() == [], "a subscribe to one scope produced more than its own snapshot"
    Path(srv.root, "product/tickets/TKT-B.md").write_text(
        _doc("TKT-B", "Ticket B moved on", "Different body."))
    assert d.drain(1.5) == [], "a ticket scope received another ticket's delta"

    c.close()
    d.close()


def test_deltas_follow_the_snapshot(srv):
    """After the snapshot, a change on disk reaches every connection subscribed to that
    scope — and only those."""
    a = opened(srv)
    b = opened(srv)
    elsewhere = opened(srv)
    for conn, scope in ((a, {"ticket": "TKT-A"}), (b, {"ticket": "TKT-A"}),
                        (elsewhere, {"ticket": "TKT-B"})):
        conn.send({"m": "subscribe", "scope": scope})
        valid(conn.wait_for("snapshot"))

    Path(srv.root, "product/tickets/TKT-A.md").write_text(
        _doc("TKT-A", "Ticket A, edited", "A second body."))

    for conn, who in ((a, "a"), (b, "b")):
        frame = valid(conn.wait_for("catalog.changed"))
        assert frame["kind"] == "ticket" and frame["id"] == "TKT-A", (who, frame)
        assert frame["change"] == "modified", (who, frame)
        assert frame["seq"] is None, "a document change is state, not a sequenced entry"

    assert elsewhere.drain(1.0) == [], "a delta reached a connection subscribed elsewhere"

    for conn in (a, b, elsewhere):
        conn.close()


def test_resume_after_gap_falls_back(srv):
    """SPEC.md F25, server side: when the log cannot cover `after_seq` the server sends a
    snapshot instead of a partial replay, so the client replaces its tail rather than
    merging into a hole."""
    for n in range(10):                       # log holds five; 1..5 are gone
        emit(srv, "lane/01", "run-1", text=f"entry {n + 1}")

    c = opened(srv)
    c.send({"m": "resume", "lane": "lane/01", "after_seq": 1})
    snap = valid(c.wait_for("snapshot"))
    assert snap["scope"] == {"lane": "lane/01"}, snap
    assert snap["replaces_tail"] is True, snap
    assert snap["seq"] == 10, snap
    assert [e["seq"] for e in snap["data"]["entries"]] == [6, 7, 8, 9, 10], snap["data"]
    assert c.drain() == [], "a fallback snapshot was followed by a partial replay as well"

    # a sequence that never existed is the same answer, not a silent nothing
    d = opened(srv)
    d.send({"m": "resume", "lane": "lane/01", "after_seq": 999})
    ahead = valid(d.wait_for("snapshot"))
    assert ahead["replaces_tail"] is True, ahead
    assert ahead["seq"] == 10, ahead

    # and the resumed connection is live afterwards, on the same socket
    emit(srv, "lane/01", "run-1", text="after the fallback")
    live = valid(c.wait_for("transcript"))
    assert live["seq"] == 11, live

    c.close()
    d.close()


def test_resume_within_log_replays_exactly_the_gap(srv):
    """With the log able to cover it: every frame after `after_seq`, in order, once, and
    nothing the client already had."""
    for n in range(4):
        emit(srv, "lane/01", "run-1", text=f"entry {n + 1}")

    c = opened(srv)
    c.send({"m": "resume", "lane": "lane/01", "after_seq": 1})
    frames = [valid(f) for f in c.drain(1.5)]

    assert [f["m"] for f in frames] == ["transcript"] * 3, frames
    assert [f["seq"] for f in frames] == [2, 3, 4], frames
    assert [f["entry"]["text"] for f in frames] == ["entry 2", "entry 3", "entry 4"], frames
    assert all(f["lane"] == "lane/01" and f["run_id"] == "run-1" for f in frames), frames

    # nothing to replay is not an error, and it is not a snapshot either
    d = opened(srv)
    d.send({"m": "resume", "lane": "lane/01", "after_seq": 4})
    assert d.drain() == [], "a client that missed nothing was sent something"

    # live afterwards, once, on both
    emit(srv, "lane/01", "run-1", text="entry 5")
    assert valid(c.wait_for("transcript"))["seq"] == 5
    assert valid(d.wait_for("transcript"))["seq"] == 5
    assert c.drain() == [] and d.drain() == [], "one entry arrived more than once"

    c.close()
    d.close()


def test_ping_gets_pong(repo_with_docs, serve):
    """M16 is answered with a bare pong carrying no state, and a connection that stops
    being answered is closed by the server rather than left half-open."""
    srv = serve(repo_with_docs.root, FACTORY_TEST_HOOKS="1", FACTORY_WS_IDLE_S="2")
    c = opened(srv)

    c.send({"m": "ping"})
    pong = valid(c.wait_for("pong"))
    assert set(pong) == {"m", "ts", "seq"}, f"pong carries state: {pong}"
    assert pong["seq"] is None, pong

    # a ping is not a subscription and does not resend anything
    assert c.drain() == [], "a ping produced more than a pong"

    # ...and the pong keeps it alive: two idle periods with pings, still open
    for _ in range(3):
        time.sleep(1.0)
        c.send({"m": "ping"})
        valid(c.wait_for("pong", timeout=3))

    silent = opened(srv)
    assert silent.closed_by_server(timeout=8), (
        "a connection that sent nothing for the idle deadline was left half-open")

    c.close()
    silent.close()


def test_every_frame_matches_the_schema(srv):
    """A session exercising every message type this ticket defines, each frame validated
    against the schema file, in the test, automatically."""
    seen_out, seen_in = set(), set()

    def send(conn, frame):
        VALIDATOR.validate(frame)          # the client half of the contract is checked too
        seen_out.add(frame["m"])
        conn.send(frame)

    def take(conn, name):
        frame = valid(conn.wait_for(name))
        seen_in.add(frame["m"])
        return frame

    c = srv.ws()
    seen_in.add(valid(c.recv())["m"])                            # hello

    send(c, {"m": "subscribe", "scope": {"requirements": True}})
    take(c, "snapshot")

    send(c, {"m": "ping"})
    take(c, "pong")

    # deliberately NOT through `send`: this frame is the one the schema must reject, and the
    # error it produces is itself a message type the schema has to describe.
    bad = {"m": "subscribe", "scope": {"nonsense": "x"}}
    assert not VALIDATOR.is_valid(bad), "the schema accepted a scope that does not exist"
    c.send(bad)
    take(c, "error")

    Path(srv.root, "product/epics/REQ-001.md").write_text(
        _doc("REQ-001", "The first requirement, edited"))
    take(c, "catalog.changed")

    send(c, {"m": "subscribe", "scope": {"lane": "lane/07"}})
    take(c, "snapshot")
    emit(srv, "lane/07", "run-9", text="hello from a run")
    take(c, "transcript")

    send(c, {"m": "resume", "lane": "lane/07", "after_seq": 0})

    # M2 — work appears on TKT-A's branch, so what became of TKT-A has changed, and the row is
    # told without anything on this side asking again. Nothing polls, including for a state the
    # tool learns by looking at the world (SPEC.md §3.5, §6 M2).
    git(srv.root, "checkout", "-b", "ticket/TKT-A")
    Path(srv.root, "work.txt").write_text("something nobody else can see\n")
    git(srv.root, "add", "-A")
    git(srv.root, "commit", "-m", "TKT-A: work", "--no-gpg-sign")
    git(srv.root, "checkout", "main")
    state = take(c, "lane.state")
    assert state["ticket_id"] == "TKT-A", state
    assert state["lane"] is None, "delivery is a fact about a ticket, not about a lane"
    assert state["delivery"]["state"] == "unshipped", state

    # M17 — the lane SET changed. This connection is subscribed to `requirements`, which is one
    # of the three scopes a membership frame is routed to, so it hears about a lane on a ticket it
    # never asked about — which is the whole point of the message (SPEC.md §6.3 M17).
    # TKT-B rather than TKT-A: a lane takes `ticket/<id>` as its branch and the M2 block above
    # needs that name free in the main checkout.
    claimed(srv, "TKT-B")
    membership = take(c, "lane.membership")
    assert membership["change"] == "claimed" and membership["ticket_id"] == "TKT-B", membership

    # M12 — the standing policy changed, so every client is told, subscribed to anything or not.
    # It needs no lane and no run: one PUT is the whole of it, so it is exercised here rather
    # than named as somebody else's (SPEC.md §6 M12, SRV-FEAT-017).
    assert srv.put("/api/permissions/policy",
                   json={"policy": "never_ask"}).status_code == 200
    take(c, "rules")

    assert seen_out == {"subscribe", "ping", "resume"}, seen_out
    assert seen_in == {"hello", "snapshot", "pong", "error", "catalog.changed",
                       "transcript", "lane.state", "lane.membership", "rules"}, seen_in
    # every name in the schema was exercised in one direction or the other — with six names
    # covered elsewhere, said out loud rather than dropped. Every one of them needs a real git
    # repository that this file does not build, and every one is validated against THIS schema
    # with THIS validator where it is produced:
    #   · `resource` (M8) needs a lane and a contended exclusive resource —
    #     test_lane.py::test_second_lane_queues_on_a_resource
    #   · `isolation` (M9) needs a lane whose confinement actually refuses a real process —
    #     test_isolation.py::test_refusal_is_reported
    #   · `command` (M10) needs a lane running the command its manifest declares —
    #     test_lane_command.py::test_gate_command_streams
    #   · `run` (M11) needs a real child process to start and then reach a terminal outcome —
    #     test_agent.py::test_end_lets_the_turn_finish, ::test_kill_wedged_process and
    #     ::test_every_run_is_reaped, which is where all five outcome words are produced
    #   · `permission.request` (M6) needs a lane and an out-of-policy tool use —
    #     test_permissions.py::test_request_names_the_rule, which is also where
    #     `nearest_miss` and `rule_that_would_be_written` are asserted
    #   · `permission.resolved` (M7) needs a request to answer or a run to die under one —
    #     test_permissions.py::test_allow_always_writes_one_readable_rule (once · always · deny)
    #     and ::test_an_answer_for_a_dead_run_is_gone_not_applied (voided), which is where all
    #     four decision words are produced
    #   · `lane.metrics` (M4) needs a real turn to end, which needs a lane, a run and an agent —
    #     test_state.py::test_clocks_tick_client_side, which is also where "and never on a
    #     timer" is asserted by advancing the clock and watching nothing arrive
    #   · `stage` (M3) needs a live run, because the token that attributes a stage report is
    #     injected into a run's environment and dies with it —
    #     test_progress.py::test_round_and_regression, which is where `round` and
    #     `regressed_from` are both produced
    #   · `lane.words` (M18) needs a lane, a run, and an agent that puts a prose or tool entry
    #     through the parser — test_lane.py::test_lane_words_reach_every_scope_a_lane_state_
    #     frame_reaches, which is also where "and no other scope" is asserted, and
    #     ::test_a_burst_of_entries_is_one_frame_carrying_the_last, which is where the
    #     coalescing is
    # A name in neither place is still a failure here.
    #
    # `lane.state` (M2) has two shapes and both are exercised: the ticket-shaped one above, and
    # the lane-shaped one carrying SPEC.md §3.2's three axes in test_state.py.
    named = {d["properties"]["m"]["const"] for d in SCHEMA["$defs"]["message"].values()}
    elsewhere = {"resource", "isolation", "command", "run", "lane.metrics",
                 "permission.request", "permission.resolved", "stage", "lane.words"}
    assert named - elsewhere == seen_out | seen_in, named ^ (seen_out | seen_in | elsewhere)

    c.close()


def test_a_bad_frame_does_not_kill_the_connection(srv):
    """Negative (mandatory). Malformed JSON, an unknown message name, and a subscribe
    naming a scope that does not exist each get an error frame — and the connection stays
    open and still delivers subsequent deltas.

    The tool serves a browser that is reloaded, hot-reloaded and left open for days. A
    socket that drops on the first surprise turns every later ticket into a debugging
    session.
    """
    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    valid(c.wait_for("snapshot"))

    c.send("{not json at all")
    err = valid(c.wait_for("error"))
    assert err["code"] == "bad_frame", err

    c.send({"m": "there-is-no-such-message"})
    err = valid(c.wait_for("error"))
    assert err["code"] == "unknown_message", err
    assert "there-is-no-such-message" in json.dumps(err), err

    c.send({"m": "subscribe", "scope": {"lantern": "lane/01"}})
    err = valid(c.wait_for("error"))
    assert err["code"] == "unknown_scope", err

    # a frame that is JSON but not an object, and one with no name at all
    c.send("[1,2,3]")
    assert valid(c.wait_for("error"))["code"] == "bad_frame"
    c.send({"scope": {"requirements": True}})
    assert valid(c.wait_for("error"))["code"] == "bad_frame"

    # the connection is still open AND still delivering the scope it subscribed to
    Path(srv.root, "product/tickets/TKT-A.md").write_text(
        _doc("TKT-A", "Ticket A survives", "Still here."))
    delta = valid(c.wait_for("catalog.changed"))
    assert delta["id"] == "TKT-A" and delta["change"] == "modified", delta

    c.send({"m": "ping"})
    valid(c.wait_for("pong"))

    c.close()


# ── attacking it from the client side ────────────────────────────────────────────────────
#
# §2 hands QA four attacks: a resume with a sequence that never existed, two subscriptions to
# overlapping scopes, a reconnect storm, and a frame the schema does not describe. Waiting for
# QA to run them would be cheaper for this session and worse for the ticket.

def test_overlapping_scopes_deliver_one_frame_not_two(srv):
    """A connection holding both `requirements` and `ticket:TKT-A` sees a ticket edit once.

    Both scopes route that change. A client that patches its list twice double-counts, and a
    client that renders a transcript twice is WEB-FEAT-014 debugging this ticket.
    """
    c = opened(srv)
    for scope in ({"requirements": True}, {"ticket": "TKT-A"}):
        c.send({"m": "subscribe", "scope": scope})
        valid(c.wait_for("snapshot"))

    Path(srv.root, "product/tickets/TKT-A.md").write_text(
        _doc("TKT-A", "Ticket A, once", "Once."))
    frames = [valid(f) for f in c.drain(1.5)]
    assert [f["m"] for f in frames] == ["catalog.changed"], frames
    assert frames[0]["id"] == "TKT-A", frames
    c.close()


def test_subscribing_twice_to_one_scope_does_not_double_deliver(srv):
    """A surface that re-mounts sends `subscribe` again. That replaces the subscription; it
    does not add a second one."""
    c = opened(srv)
    for _ in range(3):
        c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
        valid(c.wait_for("snapshot"))
    assert c.drain() == [], "three subscribes produced more than three snapshots"

    Path(srv.root, "product/tickets/TKT-A.md").write_text(
        _doc("TKT-A", "Ticket A, still once", "Still once."))
    frames = [valid(f) for f in c.drain(1.5)]
    assert [f["m"] for f in frames] == ["catalog.changed"], frames
    c.close()


def test_a_resume_the_server_can_never_satisfy_is_still_an_answer(srv):
    """Sequences that never existed, in every shape a client can produce."""
    emit(srv, "lane/02", "run-2", text="one")

    for after in (0, 1, 2, 5, 10 ** 9):
        c = opened(srv)
        c.send({"m": "resume", "lane": "lane/02", "after_seq": after})
        frames = [valid(f) for f in c.drain(1.0)]
        if after == 1:
            assert frames == [], f"after_seq {after} replayed something already held: {frames}"
        elif after == 0:
            assert [f["seq"] for f in frames] == [1], (after, frames)
        else:
            assert [f["m"] for f in frames] == ["snapshot"], (after, frames)
            assert frames[0]["replaces_tail"] is True, (after, frames)
        c.close()

    # a lane nobody has ever run in: after_seq 0 is coverable and empty, anything else is not
    c = opened(srv)
    c.send({"m": "resume", "lane": "lane/never", "after_seq": 0})
    assert c.drain(1.0) == [], "an empty lane replayed something"
    c.send({"m": "resume", "lane": "lane/never", "after_seq": 3})
    assert valid(c.wait_for("snapshot"))["replaces_tail"] is True
    c.close()

    # a negative after_seq is not a frame the schema describes, and it is refused as one
    bad = {"m": "resume", "lane": "lane/02", "after_seq": -1}
    assert not VALIDATOR.is_valid(bad), "the schema accepted a negative after_seq"
    c = opened(srv)
    c.send(bad)
    assert valid(c.wait_for("error"))["code"] == "bad_frame"
    c.close()


def test_a_reconnect_storm_leaves_the_server_serving(srv):
    """Forty connections opened, subscribed and dropped without a close handshake — a laptop
    lid, forty times. The forty-first still gets its snapshot and its deltas."""
    for _ in range(40):
        c = srv.ws()
        c.recv()
        c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
        c.close()                       # no close frame: the rude drop, which is the real one

    survivor = opened(srv)
    survivor.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    valid(survivor.wait_for("snapshot"))
    Path(srv.root, "product/tickets/TKT-A.md").write_text(
        _doc("TKT-A", "Ticket A after the storm", "After."))
    assert valid(survivor.wait_for("catalog.changed"))["id"] == "TKT-A"
    assert srv.get("/api/health").status_code == 200, "the process did not survive the storm"
    survivor.close()


def test_a_ticket_scope_naming_nothing_answers_the_way_the_endpoint_does(srv):
    """Subscribing to a ticket that does not exist is not an error: the document may appear,
    and M13 will say so. The snapshot carries what E5 carries — the 404 body with its path —
    so the client renders §4.8's empty state from one code path, not two."""
    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-NOPE"}})
    snap = valid(c.wait_for("snapshot"))
    assert snap["data"] == srv.get("/api/tickets/TKT-NOPE").json(), snap["data"]
    assert snap["data"]["error"]["code"] == "not_found", snap["data"]

    # and it fills in when the document appears, with no polling and no resubscribe
    Path(srv.root, "product/tickets/TKT-NOPE.md").write_text(
        _doc("TKT-NOPE", "It exists now", "Here."))
    assert valid(c.wait_for("catalog.changed"))["change"] == "created"
    c.close()


def test_a_scope_object_with_two_keys_is_refused(srv):
    """`{lane, ticket}` is two questions. Answering one of them silently is how a surface ends
    up subscribed to something nobody asked for."""
    c = opened(srv)
    bad = {"m": "subscribe", "scope": {"ticket": "TKT-A", "lane": "lane/01"}}
    assert not VALIDATOR.is_valid(bad), "the schema accepted a two-key scope"
    c.send(bad)
    assert valid(c.wait_for("error"))["code"] == "unknown_scope"

    # and the connection did not quietly subscribe to either of them
    Path(srv.root, "product/tickets/TKT-A.md").write_text(_doc("TKT-A", "A", "A."))
    assert c.drain(1.0) == [], "a refused subscribe subscribed anyway"
    c.close()


# ── SRV-FIX-049 — the two shapes, and which of them each path carries ─────────────────────────
#
# `snapshot.data.entries` is E8's list of ENTRIES; the SequenceLog holds whole FRAMES because
# `resume` replays frames. Both are true at once and each has a case here, because the cheap fix
# for either one silently breaks the other.

def _kinds_in_the_table():
    """§4.6's kinds, read out of the specification rather than copied from it."""
    section = ((Path(__file__).resolve().parents[2] / "docs/product/SPEC.md").read_text()
               .split("### 4.6 ")[1].split("\n- **Data**")[0])
    return frozenset(line.split("`")[1] for line in section.splitlines()
                     if line.startswith("| `"))


KINDS_IN_THE_TABLE = _kinds_in_the_table()


def test_replaces_tail_hands_over_readable_entries(srv):
    """The `replaces_tail` snapshot is the one the client is told to DISCARD ITS TAIL for, so a
    blank one here is worse than the reported bug: it replaces a correct transcript with nothing.

    Force the ring to overflow so a resume cannot be covered, then judge the entries it hands
    over — every one of them, not the first.
    """
    for n in range(10):                       # the log holds five; 1..5 are gone
        emit(srv, "lane/01", "run-1", text=f"entry {n + 1}")

    c = opened(srv)
    c.send({"m": "resume", "lane": "lane/01", "after_seq": 1})
    snap = valid(c.wait_for("snapshot"))
    assert snap["replaces_tail"] is True, snap

    entries = snap["data"]["entries"]
    assert [e["seq"] for e in entries] == [6, 7, 8, 9, 10], snap["data"]
    for i, entry in enumerate(entries):
        assert "m" not in entry, (
            f"entries[{i}] is a FRAME, not an entry — m={entry.get('m')!r}. The client "
            f"discards what it holds on this frame and renders THIS instead.\n{entry}")
        assert "entry" not in entry, (
            f"entries[{i}] wraps an entry instead of being one: {entry}")
        assert entry.get("kind") in KINDS_IN_THE_TABLE, (
            f"entries[{i}] has kind={entry.get('kind')!r}, not in SPEC.md §4.6's table — "
            f"the client renders an undeclared kind as NOTHING, deliberately.\n{entry}")
        assert isinstance(entry.get("ts"), str) and entry["ts"], entry
        assert entry["text"] == f"entry {entry['seq']}", entry

    c.close()


def test_a_second_run_in_one_lane_hands_the_column_over(srv):
    """A LANE'S SEQUENCE BELONGS TO A RUN, and the log holds one lane.

    `agent/session.cpp` numbers entries per RUN — `++run->entrySeq`, from 1, for every run — so
    the second run in a lane starts at 1 while the log still holds the first run's tail ending at
    some higher number. Two things then go wrong at once, and the second is the one people see:
    the log's `last` never comes back down, so the new run's entries are filed UNDER a sequence
    the client already holds; and the client, applying §4.6's own rule, drops every one of them
    as `arrived.seq <= lastSeq` — a replay of what it already has.

    The screen that produced this case: `PROCESS · RUNNING`, `Agent is producing`, the Stop
    button, and not one line for four minutes — while `run-2-dccfbe54/transcript.jsonl` held
    eighteen entries the agent had already written, including its answer and a second message the
    person typed because they could not see the first.

    So a new run REPLACES the tail: the log starts its numbering over and every subscriber is
    handed a snapshot carrying `replaces_tail`, which is the frame the contract already has for
    "what you hold cannot be merged into what comes next" (SPEC.md §6, M1).
    """
    for n in range(3):
        emit(srv, "lane/01", "run-1", text=f"first {n + 1}")

    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"lane": "lane/01"}})
    snap = valid(c.wait_for("snapshot"))
    assert [e["seq"] for e in snap["data"]["entries"]] == [1, 2, 3], snap["data"]
    assert snap["data"]["run_id"] == "run-1", snap["data"]

    seq = emit(srv, "lane/01", "run-2", text="second 1")
    assert seq == 1, (
        f"the second run's first entry was filed at seq {seq}: the lane's numbering carried over "
        "from a run that is finished, so the run's own seq and the log's disagree")

    handover = valid(c.wait_for("snapshot"))
    assert handover["replaces_tail"] is True, (
        f"a new run arrived and the client was never told to discard the old one's tail: "
        f"{handover}")
    assert handover["data"]["run_id"] == "run-2", handover["data"]
    assert handover["data"]["entries"] == [], (
        f"the handover carries the finished run's entries: {handover['data']}")

    # THE FIELD THE WHOLE BUG TURNS ON. The client keeps `next_after_seq` as its `lastSeq` and
    # drops everything at or below it. A handover that says 3 here is a handover that empties the
    # column and then discards the new run's first three entries — the same freeze, with a blank
    # screen instead of a stale one.
    assert handover["data"]["next_after_seq"] == 0, handover["data"]
    assert handover["data"]["complete"] is True, handover["data"]

    frame = valid(c.wait_for("transcript"))
    assert frame["run_id"] == "run-2" and frame["seq"] == 1, frame
    assert frame["entry"]["text"] == "second 1", frame

    c.close()


def test_the_run_that_is_still_running_hands_nothing_over(srv):
    """NEGATIVE (mandatory). Only a CHANGE of run replaces the tail.

    The cheap fix is a snapshot before every frame, which is correct and unreadable: the column
    would blank and redraw on every line the agent writes, and `replaces_tail` — the one frame
    that tells a client to throw away what it holds — would become the most common message in the
    contract.
    """
    emit(srv, "lane/01", "run-1", text="first")

    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"lane": "lane/01"}})
    valid(c.wait_for("snapshot"))

    for n in range(3):
        emit(srv, "lane/01", "run-1", text=f"more {n + 1}")

    frames = c.drain(1.5)
    assert [f["m"] for f in frames] == ["transcript"] * 3, (
        f"an entry of the run already on screen replaced the tail: {frames}")
    assert [f["seq"] for f in frames] == [2, 3, 4], frames

    c.close()


def test_a_resume_still_replays_whole_frames(srv):
    """NEGATIVE (mandatory). A resume the log CAN cover replays complete M5 frames — `m` and
    all — and the client can apply them in order.

    The cheap fix for this ticket is to make the SequenceLog store entries instead of frames,
    which makes the snapshot right and silently guts `resume`: the client would receive bare
    entry objects it has no `m` to dispatch on. The log holds frames; the snapshot renders
    entries; this case is what stops the first sentence being traded away for the second.
    """
    for n in range(4):
        emit(srv, "lane/01", "run-1", text=f"entry {n + 1}")

    c = opened(srv)
    c.send({"m": "resume", "lane": "lane/01", "after_seq": 1})
    frames = c.drain(1.5)

    assert [f.get("m") for f in frames] == ["transcript"] * 3, (
        f"a resume replayed something that is not a whole M5 frame: {frames}")
    for f in frames:
        valid(f)                              # the schema judges it AS a transcript frame
        assert set(f) >= {"m", "ts", "seq", "lane", "run_id", "entry"}, f
        assert f["lane"] == "lane/01" and f["run_id"] == "run-1", f
        assert isinstance(f["entry"], dict) and f["entry"].get("kind") == "prose", f

    # …and the client can apply them in order, which is the only reason `resume` sends frames.
    assert [f["seq"] for f in frames] == [2, 3, 4], frames
    assert [f["entry"]["text"] for f in frames] == ["entry 2", "entry 3", "entry 4"], frames

    c.close()


# ── M17 · the lane SET changes, and a screen that never heard of the lane is told ─────────
#
# SRV-FIX-080. Every other publisher is an edge on a lane that already exists, so until this
# message a lane claimed while a page was open reached it only on a reload — and F31's whole
# point is that the TOOL claims a lane nobody asked for, on a screen somebody is watching.
# `E12`'s `201` is a lane row for the browser that POSTed it and for nobody else.

def claimed(srv, ticket, workspace="only", role=None):
    body = {"ticket_id": ticket, "workspace": workspace}
    if role:
        body["role"] = role
    r = srv.post("/api/lanes", json=body)
    assert r.status_code == 201, r.text
    return r.json()["lane"]


def test_a_claim_announces_the_lane_to_a_ticket_subscriber(srv):
    """A lane claimed by SOMETHING ELSE reaches a surface that has never heard of it.

    Subscribed to the ticket, not to the lane — a scope keyed on the lane cannot be subscribed
    to before the lane exists, which is exactly the case this frame is for.
    """
    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    assert c.wait_for("snapshot")["m"] == "snapshot"

    lane = claimed(srv, "TKT-A")

    frame = valid(c.wait_for("lane.membership"))
    assert frame["change"] == "claimed", frame
    assert frame["lane"] == lane, frame
    assert frame["ticket_id"] == "TKT-A", frame
    assert frame["workspace"] == "only", frame
    assert frame["seq"] is None, frame
    c.close()


def test_a_release_announces_it_too(srv):
    """A set that only ever grows is half a fix: E13 says so as well."""
    lane = claimed(srv, "TKT-A")

    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    c.wait_for("snapshot")

    r = srv.delete(f"/api/lanes/{lane}?force=true")
    assert r.status_code == 200, r.text

    frame = valid(c.wait_for("lane.membership"))
    assert frame["change"] == "released", frame
    assert frame["lane"] == lane and frame["ticket_id"] == "TKT-A", frame
    c.close()


def test_a_claim_is_not_announced_to_another_tickets_scope(srv):
    """MANDATORY NEGATIVE.

    A fix that broadcast membership to every connection would pass both cases above and quietly
    undo §6.2's scoping — the property the whole subscribe model rests on. The subscriber here
    is on a DIFFERENT ticket and must hear nothing about this lane at all.
    """
    mine = opened(srv)
    mine.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    mine.wait_for("snapshot")

    elsewhere = opened(srv)
    elsewhere.send({"m": "subscribe", "scope": {"ticket": "TKT-B"}})
    elsewhere.wait_for("snapshot")

    lane = claimed(srv, "TKT-A")
    assert valid(mine.wait_for("lane.membership"))["lane"] == lane

    stray = [f for f in elsewhere.drain(1.5) if f.get("m") == "lane.membership"]
    assert stray == [], f"a claim on TKT-A reached a subscriber on TKT-B: {stray}"
    mine.close()
    elsewhere.close()


def test_the_lane_the_frame_names_is_in_E9_when_the_client_reads_it(srv):
    """The frame is only useful if the read it triggers can see the lane.

    The client's answer to `claimed` is to re-read E9 (SPEC.md §6.3 M17), so a frame published
    before the lane is in the list would produce a refetch that finds nothing and a card that
    never appears — the same defect with an extra round trip.
    """
    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    c.wait_for("snapshot")

    lane = claimed(srv, "TKT-A")
    valid(c.wait_for("lane.membership"))

    rows = srv.get("/api/lanes").json()["lanes"]
    assert any(row["lane"] == lane for row in rows), rows
    c.close()


def test_membership_reaches_a_connection_once_however_many_scopes_it_holds(srv):
    """One connection, two matching scopes, ONE frame.

    `Socket::deliver` breaks after the first scope a connection is subscribed to, and this is the
    case that says so out loud: a client on both the lane and its ticket would otherwise re-read
    E9 twice for one event, which is a poll with extra steps.
    """
    lane = claimed(srv, "TKT-A")

    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    c.wait_for("snapshot")
    c.send({"m": "subscribe", "scope": {"lane": lane}})
    c.wait_for("snapshot")

    assert srv.delete(f"/api/lanes/{lane}?force=true").status_code == 200

    membership = [f for f in c.drain(2.0) if f.get("m") == "lane.membership"]
    assert len(membership) == 1, membership
    assert valid(membership[0])["change"] == "released", membership[0]
    c.close()


def test_no_frame_is_sent_when_nothing_changed(srv):
    """The dedup in `Socket::publishLaneState` — §1's "Pre-decided: it stays", made executable.

    `publishLaneState` is deliberately over-eager: every edge that COULD have moved an axis simply
    asks, and the socket compares what it last sent for that lane and sends nothing when the answer
    is equal. That is what makes SPEC.md §6's *"one message per change, never a periodic tick"* a
    property of the code rather than a rule every caller has to remember — and `M17` arriving
    beside it must not turn a re-publish into traffic.

    Driven by a REPORT touched twice: `manager.cpp` re-publishes every lane's state on any report
    change (`publishAllLaneStates`), so the second touch asks the same question again with nothing
    about the lane having moved. Without the dedup, the same state goes out twice.
    """
    lane = claimed(srv, "TKT-A")

    c = opened(srv)
    c.send({"m": "subscribe", "scope": {"ticket": "TKT-A"}})
    c.wait_for("snapshot")

    report = Path(srv.root, "product/reports/TKT-A-report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# TKT-A\n\nrun-1 did it.\n")
    c.drain(1.5)                      # the FIRST touch may legitimately move `evidence`

    settled = [f for f in c.drain(1.5) if f.get("m") == "lane.state"]
    assert settled == [], f"the lane was still moving before the case began: {settled}"

    # ...and now the same answer, asked again. Twice, so a single stray frame is not a coin flip.
    for _ in range(2):
        report.write_text("# TKT-A\n\nrun-1 did it.\n")
        time.sleep(0.4)

    repeats = [f for f in c.drain(2.0) if f.get("m") == "lane.state"]
    assert repeats == [], (
        f"{len(repeats)} lane.state frame(s) for a lane whose state did not move — the dedup at "
        f"`Socket::publishLaneState` is not holding: {repeats}")
    assert lane, lane
    c.close()
