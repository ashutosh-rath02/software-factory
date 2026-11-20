"""SRV-FEAT-010 — the transcript: parse it, stream it, replay it, and attribute what it cost.

**Every case here is driven by a real CLI stream.** `tests/fixtures/cli-stream-*.jsonl` were
captured from `claude 2.1.92` with `--output-format stream-json --verbose` and committed verbatim.
Frames this session invented would match the code this session wrote, and would prove nothing
about the CLI — so the stand-in agent below does not synthesise output, it replays a recording.

The three recordings, and what each is for:

  cli-stream-2026-07-27-simple.jsonl      Write · Read · text · result
  cli-stream-2026-07-27-tools.jsonl       Write · Edit(structuredPatch) · Bash ·
                                          a tool_result with is_error:true · text · result
  cli-stream-2026-07-27-two-turns.jsonl   two human messages over one session — two `result`
                                          frames, which is what proves `result` is end-of-turn
                                          and what proves `total_cost_usd` is CUMULATIVE.

The last one is the reason `test_cost_rolls_up` asserts a subtraction rather than a sum.
"""

import json
import os
import re
import time
from pathlib import Path

import jsonschema
import pytest

import stand_in
from test_lane import build_repo, git, status_of
from test_runs_index import ROLES_A, plant_repo, plant_run
from test_spec import remedy_table

DOCS = Path(__file__).resolve().parents[2] / "docs"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

SOCKET_SCHEMA = json.loads((DOCS / "technical/contracts/socket.schema.json").read_text())
SOCKET_VALIDATOR = jsonschema.Draft202012Validator(SOCKET_SCHEMA)

SIMPLE = FIXTURES / "cli-stream-2026-07-27-simple.jsonl"
TOOLS = FIXTURES / "cli-stream-2026-07-27-tools.jsonl"
TWO_TURNS = FIXTURES / "cli-stream-2026-07-27-two-turns.jsonl"


# ── the kinds in SPEC.md §4.6, read out of the specification itself ───────────────────────────
#
# Parsed rather than copied. A kind added to the table with no producer and no owner is exactly
# the "silent hole in the only window onto a run" this ticket exists to prevent, and a hand-copied
# list would go stale the first time the table grows.

def kinds_in_the_table():
    text = (DOCS / "product/SPEC.md").read_text()
    section = text.split("### 4.6 ")[1].split("\n- **Data**")[0]
    out = []
    for line in section.splitlines():
        m = re.match(r"^\| `([a-z_]+)`(?: expanded)? \|", line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


# What produces each kind. The partition is asserted against the table above, so this map cannot
# quietly fall behind it.
FROM_THE_STREAM = {"prose", "tool", "file_edit", "tool_result", "error", "turn"}
FROM_THE_TOOL = {"human", "held", "stopped", "ended", "killed", "silence"}
OWNED_ELSEWHERE = {
    "stage": "SRV-FEAT-012 — E22",
    "command": "SRV-FEAT-015 — E19 streams M10 and appends no entry",
    "permission_request": "SRV-FEAT-017 — F22",
    "permission_decision": "SRV-FEAT-017/018 — F22",
    "isolation_refusal": "F9 — M9 fires today; the entry is one composition away",
}


# ── the stand-in agent: it replays a recording, it does not invent one ────────────────────────

AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
""" + stand_in.FRAME_READER + r"""
# The log lives in the LANE's worktree and a lane has many runs, so every line a test waits on
# has to be attributable to the run that wrote it. Without this a second run in the same lane is
# raced against the first run's log, which is a green that means nothing.
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "ready" >> "$log"

if [ -f ./stream.jsonl ] ; then
  cat ./stream.jsonl
fi
echo "streamed" >> "$log"

interrupted=0
trap 'interrupted=1 ; echo "interrupted" >> "$log"' INT

while : ; do
  if read_frame ; then
    printf '%s\n' "in:$text" >> "$log"
    case "$text" in
      bye) break ;;
      emit:*)
        f=`printf '%s' "$text" | cut -d: -f2`
        echo "emit-at=`date +%s%3N`" >> "$log"
        cat "./$f"
        ;;
      slow:*) sleep "`printf '%s' "$text" | cut -d: -f2`" ; echo "turn-done" >> "$log" ;;
    esac
  else
    # `read` fails on end of input AND when a trapped signal interrupts it. Only the first of
    # those means the session is over — the same distinction SRV-FEAT-009's stand-in draws.
    if [ "$interrupted" = 1 ] ; then interrupted=0 ; continue ; fi
    break
  fi
done
echo "eof" >> "$log"
"""


@pytest.fixture
def env(tmp_path, serve):
    """A repository whose agent replays a committed recording, plus a server."""
    made = []

    class Env:
        def build(self, stream=None, **kw):
            root = build_repo(tmp_path / f"repo{len(made)}",
                              agent={"command": "/bin/sh agent.sh"}, **kw)
            (root / "agent.sh").write_text(AGENT_SH)
            if stream is not None:
                (root / "stream.jsonl").write_text(Path(stream).read_text())
            for extra in (SIMPLE, TOOLS, TWO_TURNS):
                (root / extra.name).write_text(extra.read_text())
            git(root, "add", "-A")
            git(root, "commit", "-m", "agent and recordings")
            self.root = root
            self.baseline = status_of(root)
            made.append((root, self.baseline))
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        def claim(self, ticket="TCK-001", workspace="core", **extra):
            body = {"ticket_id": ticket, "workspace": workspace}
            body.update(extra)
            r = self.srv.post("/api/lanes", json=body)
            assert r.status_code == 201, r.text
            return r.json()

        def start(self, lane, **body):
            r = self.srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)
            assert r.status_code == 201, r.text
            return r.json()

        def log(self, lane):
            try:
                return (Path(lane["worktree"]) / "agent-log.txt").read_text()
            except OSError:
                return ""

        def wait_log(self, lane, needle, timeout=20, run=None):
            """Wait for `needle` in the log OF THIS RUN.

            A lane's worktree holds one `agent-log.txt` and a lane can hold many runs, so a wait
            that does not check which run wrote the line can be satisfied by the previous run's
            log before the new child has even truncated it.
            """
            deadline = time.time() + timeout
            while time.time() < deadline:
                text = self.log(lane)
                mine = run is None or f"run={run['run_id']}" in text
                if mine and needle in text:
                    return text
                time.sleep(0.05)
            raise AssertionError(
                f"{needle!r} never reached the agent's log for "
                f"{run['run_id'] if run else 'this lane'} within {timeout}s:\n{self.log(lane)}")

        def run_dir(self, run):
            return self.root / ".factory" / "runs" / run["run_id"]

        def entries_on_disk(self, run, timeout=15, atleast=1):
            """The run's own log, as JSON — one entry per line, so a partial run still reads."""
            path = self.run_dir(run) / "transcript.jsonl"
            deadline = time.time() + timeout
            out = []
            while time.time() < deadline:
                try:
                    lines = [l for l in path.read_text().splitlines() if l.strip()]
                except OSError:
                    lines = []
                out = [json.loads(l) for l in lines]
                if len(out) >= atleast:
                    return out
                time.sleep(0.05)
            raise AssertionError(
                f"only {len(out)} entries reached {path} within {timeout}s "
                f"(wanted {atleast}): {[e.get('kind') for e in out]}")

        def wait_kind(self, run, kind, timeout=20):
            """Wait until an entry of `kind` has reached the run's own log."""
            deadline = time.time() + timeout
            seen = []
            while time.time() < deadline:
                path = self.run_dir(run) / "transcript.jsonl"
                try:
                    seen = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
                except (OSError, ValueError):
                    seen = []
                if any(x.get("kind") == kind for x in seen):
                    return seen
                time.sleep(0.05)
            raise AssertionError(
                f"no {kind!r} entry reached {run['run_id']} within {timeout}s: "
                f"{[x.get('kind') for x in seen]}")

        def replay(self, run):
            r = self.srv.get(f"/api/runs/{run['run_id']}/transcript?limit=1000")
            assert r.status_code == 200, r.text
            return r.json()

        def stop(self, lane):
            self.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()

    for root, before in made:
        assert status_of(root) == before, (
            f"the main checkout at {root} changed by the end of the test:\n"
            f"before:\n{before}\nafter:\n{status_of(root)}")


def kinds_of(entries):
    return [e["kind"] for e in entries]


# ── 1 ────────────────────────────────────────────────────────────────────────────────────────

def test_every_kind_in_the_table_round_trips(env):
    """Every kind SPEC.md §4.6 names is accounted for, and the ones this run produces replay.

    A kind the table names and the parser drops is a silent hole in the only window onto a run,
    so the table is READ FROM THE SPEC and every row must be owned by somebody.
    """
    table = kinds_in_the_table()
    assert len(table) >= 15, f"§4.6's table did not parse; got {table}"

    unaccounted = set(table) - FROM_THE_STREAM - FROM_THE_TOOL - set(OWNED_ELSEWHERE)
    assert not unaccounted, (
        f"SPEC.md §4.6 names {sorted(unaccounted)} and nothing in this codebase produces them or "
        f"owns them. A kind with no producer and no owner is a hole nobody will notice.")
    invented = (FROM_THE_STREAM | FROM_THE_TOOL | set(OWNED_ELSEWHERE)) - set(table)
    assert not invented, (
        f"{sorted(invented)} is produced or claimed but is not in §4.6's table. The table is the "
        f"contract; a kind invented in code is what the ticket's pre-decided rule forbids.")

    e = env.build(stream=TOOLS)
    lane = e.claim()

    # Two more shapes, both REAL frames rather than invented ones:
    #   · a `tool_result` from a DIFFERENT recording, so its tool_use_id belongs to a call this
    #     run never saw — which is exactly the orphan §4.6 keeps a `tool_result` kind for, and
    #   · a real frame cut off in flight, which is what the wire does when a process dies.
    orphan = [l for l in SIMPLE.read_text().splitlines() if '"tool_result"' in l][0]
    truncated = TOOLS.read_text().splitlines()[1][:60]
    (Path(lane["worktree"]) / "extra.jsonl").write_text(orphan + "\n" + truncated + "\n")

    run = e.start(lane)
    e.wait_log(lane, "streamed")

    # ...and the kinds the tool itself produces, from the actions it already owns.
    assert e.srv.post(f"/api/lanes/{lane['lane']}/messages",
                      json={"text": "emit:extra.jsonl"}).json()["status"] == "delivered"
    e.wait_log(lane, "in:emit:extra.jsonl")
    assert e.srv.post(f"/api/lanes/{lane['lane']}/messages",
                      json={"text": "held one"}).json()["status"] == "held"
    assert e.srv.post(f"/api/lanes/{lane['lane']}/interrupt", json={}).status_code == 200
    assert e.srv.post(f"/api/lanes/{lane['lane']}/end", json={}).status_code == 202

    entries = e.entries_on_disk(run, atleast=16)
    seen = set(kinds_of(entries))

    # `killed` is the one kind a run that ended cleanly cannot have, so a second run has it —
    # and so is `silence`, which needs a run that goes quiet for 120 s and then never speaks
    # again. Both belong to the run that gets killed rather than to the one above, whose seqs
    # are asserted to be in order: closing a silence republishes an earlier seq (M5's one
    # exception to append-only), and this second run is asserted on its kinds alone.
    time.sleep(1.0)
    killed_run = e.start(lane)
    e.wait_log(lane, "streamed", run=killed_run)
    assert e.srv.post("/api/_test/clock", json={"advance_s": 130}).status_code == 202
    e.wait_kind(killed_run, "silence")
    assert e.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1}).status_code == 202
    seen |= set(kinds_of(e.wait_kind(killed_run, "killed")))

    missing = FROM_THE_STREAM - seen
    assert not missing, (
        f"the recording contains frames for {sorted(missing)} and the parser produced none of "
        f"them. Seen: {sorted(seen)}")
    missing = FROM_THE_TOOL - seen
    assert not missing, f"the tool's own kinds {sorted(missing)} were never written: {sorted(seen)}"

    # ...and E8 replays exactly what was written, in order, with no entry rewritten.
    replayed = e.replay(run)
    assert replayed["run_id"] == run["run_id"], replayed
    assert replayed["entries"] == entries, (
        "E8's replay is not byte-identical to the run's own log — "
        f"{len(replayed['entries'])} entries replayed against {len(entries)} written")
    seqs = [x["seq"] for x in entries]
    assert seqs == sorted(seqs), f"the log is not in sequence order: {seqs}"

    # ...and the frames that map to NOTHING are counted rather than silently dropped. This is the
    # ticket's own finding, made mechanical: "the CLI said something this tool has no kind for"
    # is a number a person can read, and it is what triggers a spec amendment instead of a
    # parser quietly inventing a kind.
    meta = json.loads((e.run_dir(run) / "meta.json").read_text())
    assert meta["run_id"] == run["run_id"], meta
    assert meta["outcome"] == "ended cleanly", meta
    assert meta["unmapped"], (
        f"the recording contains a `system` banner and a `rate_limit_event`, neither of which is "
        f"a kind in §4.6, and nothing counted them: {meta}")
    for frame in ("system", "rate_limit_event"):
        assert frame in meta["unmapped"], (
            f"{frame} frames were dropped without being counted: {meta['unmapped']}")
    e.unchanged()


# ── 2 · F11 ──────────────────────────────────────────────────────────────────────────────────

def test_entries_stream_in_order_and_in_time(env):
    """M5 frames arrive with contiguous seq, within 200 ms of the child producing them."""
    e = env.build()
    lane = e.claim()
    run = e.start(lane)
    e.wait_log(lane, "streamed")

    ws = e.srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    ws.wait_for("snapshot")

    assert e.srv.post(f"/api/lanes/{lane['lane']}/messages",
                      json={"text": f"emit:{TOOLS.name}"}).status_code == 200

    frames = []
    deadline = time.time() + 20
    while time.time() < deadline:
        f = ws.wait_for("transcript", timeout=max(1, deadline - time.time()))
        frames.append((f, time.time()))
        if f["entry"]["kind"] == "turn":
            break
    ws.close()
    assert frames, "no transcript frame arrived at all"

    for f, _ in frames:
        SOCKET_VALIDATOR.validate(f)
        assert f["lane"] == lane["lane"], f
        assert f["run_id"] == run["run_id"], f

    seqs = [f["seq"] for f, _ in frames if not f.get("patch")]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), (
        f"transcript sequences are not contiguous: {seqs}")

    text = e.wait_log(lane, "emit-at=")
    emitted_ms = int(re.search(r"emit-at=(\d+)", text).group(1))
    arrived_ms = int(frames[0][1] * 1000)
    lag = arrived_ms - emitted_ms
    assert lag < 200, (
        f"the first entry took {lag} ms to reach the socket after the child produced it; F11 says "
        f"200 ms. A transcript that lags is a transcript nobody trusts to be live.")

    e.stop(lane)
    e.unchanged()


# ── 3 ────────────────────────────────────────────────────────────────────────────────────────

def test_a_tool_result_patches_its_call(env):
    """A result arrives as its own frame with the SAME seq and patch:true — never a second entry.

    Two entries for one call is how a transcript starts lying about how much happened.
    """
    e = env.build(stream=TOOLS)
    lane = e.claim()
    run = e.start(lane)

    ws = e.srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    ws.wait_for("snapshot")
    frames = []
    deadline = time.time() + 20
    while time.time() < deadline:
        f = ws.wait_for("transcript", timeout=max(1, deadline - time.time()))
        frames.append(f)
        if f["entry"]["kind"] == "turn":
            break
    ws.close()

    calls = [f for f in frames if f["entry"]["kind"] in ("tool", "file_edit") and not f.get("patch")]
    patches = [f for f in frames if f.get("patch")]
    assert calls, f"no tool call was parsed at all: {[f['entry']['kind'] for f in frames]}"
    assert patches, "no tool result arrived as a patch; SPEC.md M5 says it must"

    # The recording has four tool calls and four results.
    assert len(calls) == 4, [c["entry"].get("tool") for c in calls]
    assert len(patches) == len(calls), (
        f"{len(calls)} calls produced {len(patches)} patches — a result that became its own entry "
        f"double-counts the run")

    by_seq = {c["seq"]: c for c in calls}
    for p in patches:
        assert p["seq"] in by_seq, (
            f"a patch arrived at seq {p['seq']}, which is not the seq of any call: "
            f"{sorted(by_seq)}")
        assert p["entry"]["kind"] == by_seq[p["seq"]]["entry"]["kind"], (
            "a patch changed the kind of the entry it patches")

    entries = e.entries_on_disk(run, atleast=len(frames))
    ok = {en["seq"]: en.get("ok") for en in entries if en["kind"] in ("tool", "file_edit")}
    assert False in ok.values(), (
        f"the recording contains a tool_result with is_error:true and no entry reports ok:false: "
        f"{ok}")
    assert True in ok.values(), f"no successful call was marked ok:true: {ok}"

    e.stop(lane)
    e.unchanged()


# ── 4 · F13 ──────────────────────────────────────────────────────────────────────────────────

def test_edit_carries_counts_from_the_stream(env):
    """path, added, removed and diff come from the agent's output, not recomputed from disk."""
    e = env.build(stream=TOOLS)
    lane = e.claim()
    run = e.start(lane)
    e.wait_log(lane, "streamed")

    entries = e.entries_on_disk(run, atleast=10)
    edits = [x for x in entries if x["kind"] == "file_edit"]
    assert edits, f"no file_edit entry was produced: {kinds_of(entries)}"

    # The recording has both shapes, and they are different facts: a Write that CREATED the file
    # (every line added, nothing removed) and an Edit that changed one line.
    created = [x for x in edits if x.get("added") and not x.get("removed")]
    assert created, f"no file_edit reported a creation: {edits}"
    assert all(l.startswith("+") for l in created[0]["diff"].splitlines()), created[0]["diff"]
    assert created[0]["added"] == len(created[0]["diff"].splitlines()), created[0]

    edited = [x for x in edits if x.get("removed")]
    assert edited, f"no file_edit carried a real +n/−n from a structuredPatch: {edits}"
    one = edited[0]

    assert one["path"].endswith("poem.txt"), one
    assert one["added"] == 1 and one["removed"] == 1, one
    assert "the second line, edited." in one["diff"], (
        f"the diff does not carry the agent's own patch text: {one['diff']!r}")
    assert "-the world hums softly awake," in one["diff"], one["diff"]

    # The decisive half. The entry describes a file OUTSIDE this lane's worktree — the recording
    # was made somewhere else entirely — so nothing in the confined run could have opened it, and
    # a `+1 −1` recomputed from disk could not exist. And if a file of that name happens to be on
    # this machine, it no longer contains the line the diff says was removed, so even an
    # unconfined recompute could not have produced this diff.
    assert not one["path"].startswith(str(Path(lane["worktree"]).resolve())), (
        f"{one['path']} is inside the lane, so this case cannot tell a parsed diff from a "
        f"recomputed one")
    if Path(one["path"]).exists():
        assert "the world hums softly awake," not in Path(one["path"]).read_text(), (
            f"{one['path']} still holds the line the diff says was removed; this case cannot "
            f"tell a parsed diff from a recomputed one")

    e.stop(lane)
    e.unchanged()


# ── 5 · F27 ──────────────────────────────────────────────────────────────────────────────────

def test_cost_rolls_up(env):
    """Turn costs sum to the run; the run's cost is one number from one source, on all three
    surfaces.

    `result.total_cost_usd` is the SESSION total, not the turn's — measured, in
    cli-stream-2026-07-27-two-turns.jsonl. Summing it across turns overstates the run.
    """
    recorded = [json.loads(l) for l in TWO_TURNS.read_text().splitlines() if l.strip()]
    totals = [f["total_cost_usd"] for f in recorded if f.get("type") == "result"]
    assert len(totals) == 2, f"the two-turn recording no longer has two result frames: {totals}"
    assert totals[1] > totals[0], totals

    e = env.build(stream=TWO_TURNS)
    lane = e.claim(ticket="TCK-001")
    run = e.start(lane)
    e.wait_log(lane, "streamed")

    entries = e.entries_on_disk(run, atleast=len(recorded) - 3)
    turns = [x for x in entries if x["kind"] == "turn"]
    assert len(turns) == 2, f"two `result` frames produced {len(turns)} turn entries"

    assert turns[0]["cost_usd"] == pytest.approx(totals[0], rel=1e-9), turns[0]
    assert turns[1]["cost_usd"] == pytest.approx(totals[1] - totals[0], rel=1e-6), (
        f"turn 2 reports {turns[1]['cost_usd']} — `total_cost_usd` is cumulative, so the turn's "
        f"own cost is {totals[1] - totals[0]}. Reporting the total makes every run after the "
        f"first one cost more than it did.")

    summed = sum(t["cost_usd"] for t in turns)
    assert summed == pytest.approx(totals[-1], rel=1e-6), (
        f"the turns sum to {summed} and the run cost {totals[-1]}")

    e.srv.post(f"/api/lanes/{lane['lane']}/end", json={})

    def costs():
        runs = e.srv.get("/api/tickets/TCK-001/runs")
        assert runs.status_code == 200, runs.text
        row = [r for r in runs.json()["runs"] if r["run_id"] == run["run_id"]]
        assert row, runs.json()
        ticket = e.srv.get("/api/tickets/TCK-001").json()
        card = [l for l in e.srv.get("/api/lanes").json()["lanes"]
                if l["lane"] == lane["lane"]]
        return row[0]["cost_usd"], ticket["runs"]["cost_usd"], (card[0]["cost_usd"] if card else None)

    deadline = time.time() + 15
    while time.time() < deadline:
        table, header, card = costs()
        if table == pytest.approx(totals[-1], rel=1e-6):
            break
        time.sleep(0.1)

    table, header, card = costs()
    assert table == pytest.approx(totals[-1], rel=1e-6), f"the RUNS table says {table}"
    assert header == pytest.approx(table, rel=1e-9), (
        f"the ticket header says {header} and the RUNS table says {table} — F27 asks for the same "
        f"number from the same source")
    assert card == pytest.approx(table, rel=1e-9), (
        f"the lane card says {card} and the RUNS table says {table}")

    e.stop(lane)
    e.unchanged()


# ── 6 · F6 ───────────────────────────────────────────────────────────────────────────────────

def test_history_survives_a_restart(env):
    """Two runs, the tool restarted, and E7 still lists both with their outcomes and costs."""
    from conftest import _start_instance, stop_process

    e = env.build(stream=SIMPLE)
    lane = e.claim(ticket="TCK-001")

    first = e.start(lane)
    e.wait_log(lane, "streamed", run=first)
    e.wait_kind(first, "turn")
    e.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    time.sleep(1.0)

    second = e.start(lane)
    e.wait_log(lane, "streamed", run=second)
    # The run has to have finished a TURN before it is killed, or a cost of zero is the honest
    # answer and this case would be asserting against its own race rather than against a restart.
    e.wait_kind(second, "turn")
    e.srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    time.sleep(1.0)

    before = e.srv.get("/api/tickets/TCK-001/runs").json()["runs"]
    assert {r["run_id"] for r in before} == {first["run_id"], second["run_id"]}, before

    stop_process(e.srv.proc)
    restarted = _start_instance(e.root, e.srv.log.parent / "restart.log", FACTORY_TEST_HOOKS="1")
    try:
        after = restarted.get("/api/tickets/TCK-001/runs")
        assert after.status_code == 200, after.text
        rows = after.json()["runs"]
        assert {r["run_id"] for r in rows} == {first["run_id"], second["run_id"]}, (
            f"the restart lost run history: {rows}")
        by_id = {r["run_id"]: r for r in rows}
        assert by_id[first["run_id"]]["outcome"] == "ended cleanly", by_id[first["run_id"]]
        assert by_id[second["run_id"]]["outcome"] == "killed by you", by_id[second["run_id"]]
        for r in rows:
            assert r["cost_usd"] > 0, f"a run lost its cost across the restart: {r}"
            assert r["session_id"], r
        assert [r["run_id"] for r in rows][0] == second["run_id"], (
            f"E7 is not newest-first: {[r['run_id'] for r in rows]}")
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()
    e.unchanged()


# ── 7 ────────────────────────────────────────────────────────────────────────────────────────

def test_a_run_whose_log_was_deleted_is_honest(env):
    """E8 is 404 naming the path it looked for; E7 still lists the run from its meta.json."""
    e = env.build(stream=SIMPLE)
    lane = e.claim(ticket="TCK-001")
    run = e.start(lane)
    e.wait_log(lane, "streamed")
    e.entries_on_disk(run, atleast=3)
    e.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    time.sleep(1.0)

    log = e.run_dir(run) / "transcript.jsonl"
    assert log.exists(), log
    log.unlink()

    gone = e.srv.get(f"/api/runs/{run['run_id']}/transcript")
    assert gone.status_code == 404, gone.text
    body = gone.json()["error"]
    assert body["code"] == "not_found", body
    assert body["detail"]["path"].endswith("transcript.jsonl"), body
    assert run["run_id"] in body["detail"]["path"], body

    listed = e.srv.get("/api/tickets/TCK-001/runs").json()["runs"]
    assert [r["run_id"] for r in listed] == [run["run_id"]], (
        f"deleting the log made the run disappear: {listed}. History is expendable; pretending "
        f"the run never happened is not.")

    unknown = e.srv.get("/api/runs/run-999-deadbeef/transcript")
    assert unknown.status_code == 404, unknown.text
    e.unchanged()


# ── 8 · NEGATIVE (mandatory) ─────────────────────────────────────────────────────────────────

def test_an_unparseable_frame_does_not_stop_the_stream(env):
    """A malformed frame mid-run is recorded raw and everything after it still arrives.

    The CLI's format will change under us. A parser that stops at the first surprise turns a live
    session into a blank screen, which is worse than an ugly line — and it fails silently, so
    nobody finds out until somebody is watching a run that is going nowhere.
    """
    lines = [l for l in TOOLS.read_text().splitlines() if l.strip()]
    assert len(lines) > 6, lines
    corrupted = list(lines)
    corrupted.insert(3, '{"type":"assistant","message":{"content":[{"type":"tex')  # truncated
    corrupted.insert(4, "this is not JSON at all")
    corrupted.insert(5, '{"type":"assistant","message":{"content":[{"type":"quantum","x":1}]}}')

    e = env.build()
    lane = e.claim()
    (Path(lane["worktree"]) / "broken.jsonl").write_text("\n".join(corrupted) + "\n")
    run = e.start(lane)
    e.wait_log(lane, "streamed")

    assert e.srv.post(f"/api/lanes/{lane['lane']}/messages",
                      json={"text": "emit:broken.jsonl"}).status_code == 200

    entries = e.entries_on_disk(run, atleast=len(lines) - 2)
    kinds = kinds_of(entries)

    raw = [x for x in entries if x["kind"] == "error" and x.get("raw")]
    assert len(raw) >= 2, (
        f"the two unparseable frames were swallowed — nothing records that the CLI said something "
        f"this tool could not read. Entries: {kinds}")
    assert any("not JSON at all" in x.get("text", "") for x in raw), (
        f"the raw entry does not carry the bytes that could not be parsed: {raw}")

    # ...and the stream did not stop. Everything after the corruption still arrived.
    assert "turn" in kinds, (
        f"the run's final `result` never became a turn entry — the parser stopped at the first "
        f"surprise and the rest of the run is gone. Entries: {kinds}")
    assert kinds.index("turn") == len(kinds) - 1, kinds
    for expected in ("tool", "file_edit", "prose"):
        assert expected in kinds[kinds.index("error"):], (
            f"no {expected} entry arrived AFTER the unparseable frame: {kinds}")

    replayed = e.replay(run)["entries"]
    assert kinds_of(replayed) == kinds, "E8 replays a different stream than the one written"

    e.stop(lane)
    e.unchanged()


# ── 9 · the boundary SPEC.md §10 rests on ────────────────────────────────────────────────────

def test_only_the_agent_module_knows_the_cli(env):
    """Nothing in `api`, `lane`, `catalog` or `delivery` pattern-matches on the CLI's frames.

    This is what makes a second agent backend a change in one directory. It is asserted rather
    than reviewed because it is the kind of boundary that decays one convenient line at a time.
    """
    app = Path(__file__).resolve().parents[1] / "app"
    # `CLAUDE_CONFIG_DIR` is SRV-FEAT-039's: the lane binds the credential directory, and the CLI
    # cannot find it from the lane's tmpfs `HOME` without being told. Being told is the CLI's own
    # vocabulary, so it belongs here with the frame names — the tempting place to put it was
    # `lane/confine.cpp`, next to the bind, which would have taught the confinement one agent's
    # environment variable.
    needles = ("tool_use", "tool_result", "total_cost_usd", "structuredPatch", "stream-json",
               "CLAUDE_CONFIG_DIR")
    offenders = []
    for path in sorted(app.rglob("*")):
        if path.suffix not in (".cpp", ".h") or path.parent.name == "agent":
            continue
        text = path.read_text()
        for n in needles:
            if n in text:
                offenders.append(f"{path.relative_to(app.parent)}: {n}")
    assert not offenders, (
        "the CLI's wire format leaked out of app/agent/ (SPEC.md §10):\n  " +
        "\n  ".join(offenders))


# ── 10 · SRV-FIX-049 — the lane snapshot's entries are ENTRIES ────────────────────────────────
#
# SPEC.md E8: `entries` is `[{seq,ts,kind,…payload}]`. A lane scope's snapshot calls itself "the
# E8-shaped body", the client reads `data.entries` as entries, and `Transcript.tsx` renders an
# undeclared kind as nothing — so an `entries` list of anything else is a blank screen.
#
# These three drive the `publishTranscriptAt` path: a real run, entries the agent produced.
# `test_ws.py`'s two drive `publishTranscript`, which assigns the sequence itself. Both paths
# produce that list, so both are asserted — one concept rendered twice is the whole defect.

def _lane_snapshot(srv, lane):
    """The raw snapshot frame for a lane scope, schema-judged, on its own connection."""
    ws = srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane}})
    snap = ws.wait_for("snapshot")
    SOCKET_VALIDATOR.validate(snap)
    ws.close()
    return snap


def test_a_lane_snapshot_carries_entries_not_frames(env):
    """EVERY member of `snapshot.data.entries` is an entry: a `kind` in §4.6's table, and no
    `m` and no nested `entry`.

    Not the first one — every one. A list whose first member is right and whose rest are frames
    is the same blank screen, and it is the shape a `first entry` assertion cannot see.
    """
    e = env.build(stream=TOOLS)
    lane = e.claim()
    run = e.start(lane)
    e.wait_kind(run, "turn")
    on_disk = e.entries_on_disk(run, atleast=5)

    snap = _lane_snapshot(e.srv, lane["lane"])
    entries = snap["data"]["entries"]
    assert len(entries) >= len(on_disk), (
        f"the lane log holds {len(entries)} but the run wrote {len(on_disk)}: {snap['data']}")

    table = kinds_in_the_table()
    for i, entry in enumerate(entries):
        assert "m" not in entry, (
            f"entries[{i}] is a FRAME, not an entry — it carries m={entry.get('m')!r}. "
            f"SPEC.md E8 says entries is [{{seq,ts,kind,…payload}}].\n{entry}")
        assert "entry" not in entry, (
            f"entries[{i}] wraps an entry instead of being one: {entry}")
        assert entry.get("kind") in table, (
            f"entries[{i}] has kind={entry.get('kind')!r}, which is not in SPEC.md §4.6's "
            f"table {table} — the client renders an undeclared kind as NOTHING, deliberately.\n"
            f"{entry}")
        assert isinstance(entry.get("seq"), int) and entry["seq"] >= 1, entry
        assert isinstance(entry.get("ts"), str) and entry["ts"], entry

    e.stop(lane)
    e.unchanged()


def test_the_snapshot_and_the_live_frames_describe_the_same_entry(env):
    """The entry delivered live as M5 and the same entry read back in a snapshot are EQUAL,
    field for field.

    This is the assertion whose absence is the whole defect: two paths for one concept and
    nothing that ever compared them. A test that subscribes and then listens sees only the
    second path.
    """
    e = env.build()
    lane = e.claim()
    run = e.start(lane)
    e.wait_log(lane, "streamed")

    live = e.srv.ws()
    live.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    live.wait_for("snapshot")

    assert e.srv.post(f"/api/lanes/{lane['lane']}/messages",
                      json={"text": f"emit:{TOOLS.name}"}).status_code == 200

    by_seq = {}
    deadline = time.time() + 20
    while time.time() < deadline:
        f = live.wait_for("transcript", timeout=max(1, deadline - time.time()))
        SOCKET_VALIDATOR.validate(f)
        by_seq[f["seq"]] = f["entry"]          # a patch replaces its call, at the same seq
        if f["entry"]["kind"] == "turn":
            break
    live.close()
    assert by_seq, "no live M5 frame arrived at all"

    snap = _lane_snapshot(e.srv, lane["lane"])
    from_snapshot = {x["seq"]: x for x in snap["data"]["entries"]
                     if isinstance(x.get("seq"), int)}

    shared = sorted(set(by_seq) & set(from_snapshot))
    assert shared, (
        f"the snapshot and the live frames share no sequence at all: "
        f"live {sorted(by_seq)} vs snapshot {sorted(from_snapshot)}")
    for seq in shared:
        assert from_snapshot[seq] == by_seq[seq], (
            f"seq {seq} is a different value depending on which path delivered it.\n"
            f"  live  M5 entry: {json.dumps(by_seq[seq], sort_keys=True)}\n"
            f"  snapshot entry: {json.dumps(from_snapshot[seq], sort_keys=True)}")

    e.stop(lane)
    e.unchanged()


def test_the_snapshot_and_E8_agree(env):
    """`snapshotData` calls itself "the E8-shaped body for a lane scope". Make the claim
    executable: for one run, E8 over HTTP and the lane snapshot over the socket return the same
    entries for the sequences they share.
    """
    e = env.build(stream=TOOLS)
    lane = e.claim()
    run = e.start(lane)
    e.wait_kind(run, "turn")
    e.entries_on_disk(run, atleast=5)

    replay = e.replay(run)
    snap = _lane_snapshot(e.srv, lane["lane"])

    over_http = {x["seq"]: x for x in replay["entries"]}
    over_socket = {x["seq"]: x for x in snap["data"]["entries"]
                   if isinstance(x.get("seq"), int)}

    assert set(snap["data"]) >= {"run_id", "entries", "next_after_seq", "complete"}, (
        f"the lane snapshot's data is not E8's body: {sorted(snap['data'])}")
    assert snap["data"]["run_id"] == replay["run_id"] == run["run_id"], snap["data"]

    shared = sorted(set(over_http) & set(over_socket))
    assert len(shared) >= 5, (
        f"E8 and the snapshot share only {len(shared)} sequences: "
        f"E8 {sorted(over_http)} vs snapshot {sorted(over_socket)}")
    for seq in shared:
        assert over_socket[seq] == over_http[seq], (
            f"seq {seq} differs between E8 and the lane snapshot.\n"
            f"  E8       : {json.dumps(over_http[seq], sort_keys=True)}\n"
            f"  snapshot : {json.dumps(over_socket[seq], sort_keys=True)}")

    e.stop(lane)
    e.unchanged()


# ── SRV-FIX-056 ══════════════════════════════════════════════════════════════════════════════
#
# Moved here from `test_agent.py` after QA's F3: §1 named
# `test_transcript.py::test_a_rate_limit_event_is_kept_where_a_crash_can_be_read_next_to_it`, and
# a criterion is a name AND a location — a case that answers the right question from the wrong
# file is a case the ticket cannot be grepped for.
#
# It belongs here on its own merits too: what is under test is a decision in
# `agent/transcript.cpp`, which is the one module allowed to know the CLI's wire format.

# `agentenv` is a fixture and travels with the import, which is how the two cases below reach the
# same stand-in machinery they were written against.
from test_agent import (                                             # noqa: E402,F401
    EXIT_AGENT_SH, _meta_when_over, agentenv, assert_reaped,
)

RATE_LIMIT_AGENT_SH = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "run=${FACTORY_RUN_ID}" >> "$log"
echo "ready" >> "$log"
printf '%s\n' '{"type":"rate_limit_event","status":"rejected","resetsAt":"2026-07-31T04:00:00Z","message":"usage limit reached"}'
sleep 0.3
exit 7
"""


def test_a_rate_limit_event_is_kept_where_a_crash_can_be_read_next_to_it(agentenv):
    """The one frame that could say why a run died, counted and discarded.

    `transcript.cpp` drops `rate_limit_event` with a reason written in the code — *"a fact about
    an account, not about this run"* — and that reasoning is right for a healthy run. It is
    exactly backwards for a dying one: if an account limit is what ended the run, the tool has
    just deleted the only frame that said so. Every crashed run in this repository's history
    carries `rate_limit_event: 1` in `unmapped` and nothing a person can read.

    The count stays. What is added is the payload, on the run's own record, beside the exit code —
    which is the whole point: the two facts are read together or neither is worth having.
    """
    env = agentenv.build(script=RATE_LIMIT_AGENT_SH)
    lane = env.claim()
    run = env.start(lane)

    meta = _meta_when_over(env, run)
    assert meta["outcome"] == "crashed", meta
    assert meta.get("exit_code") == 7, meta

    assert meta.get("unmapped", {}).get("rate_limit_event") == 1, (
        f"the count is part of the contract and must not be traded for the payload: {meta}")

    kept = meta.get("rate_limit")
    assert kept, (
        f"the run died and the frame that could say why is a number: {meta}")
    assert "usage limit reached" in json.dumps(kept), (
        f"the frame was kept but not what it said: {kept}")
    env.unchanged()


def test_a_run_that_was_never_rate_limited_says_nothing(agentenv):
    """NEGATIVE (mandatory). A field that is always there is a field nobody reads.

    The cheap fix is to write the key on every run — empty, null, `{}` — which turns "this run hit
    a limit" into something a reader has to compare rather than notice.
    """
    env = agentenv.build(script=EXIT_AGENT_SH)
    lane = env.claim()
    run = env.start(lane)

    meta = _meta_when_over(env, run)
    assert meta["outcome"] == "crashed", meta
    assert "rate_limit" not in meta, (
        f"a run that was never rate limited carries the key anyway: {meta}")
    env.unchanged()


def test_a_clip_never_splits_a_character_and_the_document_still_decodes(env):
    """SRV-FIX-049 QA — the snapshot that could not be parsed AT ALL.

    QA's break attempt on `SRV-FIX-049`: on this real repository the lane `snapshot` frame is not
    valid UTF-8, and both the E8 body and the socket frame fail to decode. The cause is
    `agent::clipped`, which cut at a BYTE index: a multi-byte character straddling the limit left a
    truncated sequence behind.

    The failure is not one ugly entry. A `JSON.parse` that throws takes the whole document with it,
    so one clipped character in one tool result blanks the entire transcript — which is the same
    screen, and the same silence, that `SRV-FIX-049` and `WEB-FIX-051` were both about.

    Driven through the raw-line path, whose limit is 400: a non-JSON line with `é` (0xC3 0xA9)
    beginning at byte 399, so the clip lands inside the character. Every clip in this file shares
    the one function, and this is the shortest input that reaches it.
    """
    straddling = "x" * 399 + "é" + "y" * 200
    assert len(straddling.encode()) > 400
    assert straddling.encode()[400] & 0xC0 == 0x80, "the fixture must put a continuation byte AT 400"

    e = env.build()
    lane = e.claim()
    (Path(lane["worktree"]) / "wide.jsonl").write_text(straddling + "\n")
    run = e.start(lane)
    e.wait_log(lane, "streamed")
    assert e.srv.post(f"/api/lanes/{lane['lane']}/messages",
                      json={"text": "emit:wide.jsonl"}).status_code == 200

    entries = e.entries_on_disk(run, atleast=2)
    raw = [x for x in entries if x["kind"] == "error" and x.get("raw")]
    assert raw, f"the wide line was not recorded raw: {kinds_of(entries)}"

    clipped = raw[0]["text"]
    assert clipped.endswith("…"), clipped[-10:]
    # the character was DROPPED, not halved — the body before the ellipsis is whole
    assert clipped[:-1] == "x" * 399, repr(clipped[-12:])

    # and the thing that actually broke: the document decodes.
    body = e.srv.get(f"/api/runs/{run['run_id']}/transcript")
    assert body.status_code == 200, body.text
    body.content.decode("utf-8")          # raises on a split sequence — this is the assertion
    assert json.loads(body.content.decode("utf-8"))["entries"], body.text


# ── SRV-FIX-343 ·  a cleaned transcript is a terminal state, and says so ──────────────────────
#
# `WEB-FEAT-176` decided that a remedy becomes the CONTROL that ends the state. Reloading the
# manifest re-reads `.factory/factory.json`; it cannot restore a `transcript.jsonl` somebody
# deleted, so the button's only possible outcome is the same `404` again — `SRV-FIX-193`'s shape
# (a control that reports success having done nothing) one layer up.
#
# The fixture below is the one `SRV-FEAT-315` plants on purpose: `meta.json` present,
# `transcript.jsonl` absent. The row is the record, the log is the evidence, and losing the
# evidence is not losing the record.

ERROR_BODY_VALIDATOR = jsonschema.Draft202012Validator(
    {"$schema": json.loads((DOCS / "technical/contracts/http.schema.json").read_text())["$schema"],
     "$defs": json.loads((DOCS / "technical/contracts/http.schema.json").read_text())["$defs"],
     "$ref": "#/$defs/errorBody"})

CLEANED = "run-1-cleaned"
INTACT = "run-2-intact"


def cleaned_and_intact(repo):
    """A repository holding two runs: one whose log was cleaned, one whose log is there.

    Two runs, not one. A fix that turned every `E8` into a `404` satisfies a fixture with only
    the cleaned run in it, and `min`, `any` and `all` are indistinguishable on a singleton.
    """
    plant_repo(repo.root, roles=ROLES_A)
    gone = plant_run(repo.root, CLEANED, "TKT-001", started="2026-08-30T09:00:00Z",
                     ended="2026-08-30T09:40:00Z", role="qa", n=3)
    plant_run(repo.root, INTACT, "TKT-002", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:10:00Z")
    (gone / "transcript.jsonl").unlink()
    assert not (gone / "transcript.jsonl").exists(), gone
    assert (gone / "meta.json").exists(), (
        "the fixture must leave the RECORD behind — a run with no meta.json is a run E7 never "
        "lists, and this ticket is about the one that is still listed")
    return repo.root


# ── 12 ───────────────────────────────────────────────────────────────────────────────────────

def test_a_cleaned_transcript_offers_no_remedy(repo, serve):
    """Open a run whose log has been cleaned: the screen is told what happened and is offered
    no button, because no button the tool could draw would bring the log back.

    Asserted on `null` SPECIFICALLY. A missing `remedy` key fails here too: `remedy: null` and
    `remedy` absent are different answers, and only the first one is a statement.
    """
    root = cleaned_and_intact(repo)
    srv = serve(root)

    gone = srv.get(f"/api/runs/{CLEANED}/transcript")
    assert gone.status_code == 404, gone.text
    body = gone.json()
    ERROR_BODY_VALIDATOR.validate(body)
    error = body["error"]

    assert error["code"] == "not_found", error
    assert "remedy" in error, (
        f"the `remedy` member is absent from E8's 404. Absent is not `null`: a reader cannot "
        f"tell a state with no way out from a server that forgot to say: {error}")
    assert error["remedy"] is None, (
        f"E8's 404 for a cleaned log offers {error['remedy']!r}. A cleaned log is a terminal "
        f"state; the only outcome of that control is this same 404 again: {error}")


# ── 13 ───────────────────────────────────────────────────────────────────────────────────────

def test_the_message_and_the_path_are_unchanged(repo, serve):
    """The same response still says what happened and where the tool looked.

    This ticket removes a control, not an explanation. The sentence and `detail.path` are the
    whole of what a person has left once the button is gone, so they are asserted in the same
    case as the removal rather than trusted to survive it.
    """
    root = cleaned_and_intact(repo)
    srv = serve(root)

    error = srv.get(f"/api/runs/{CLEANED}/transcript").json()["error"]

    assert error["message"] == (
        "no transcript was recorded for that run, or it has been cleaned"), error
    assert error["detail"]["path"].endswith(f"{CLEANED}/transcript.jsonl"), error
    assert error["remedy"] is None, error


# ── 14 · NEGATIVE (mandatory) ────────────────────────────────────────────────────────────────

def test_a_run_that_still_has_its_log_is_not_touched(repo, serve):
    """A run whose `transcript.jsonl` is there still replays, in the same repository and in the
    same call as the cleaned one.

    The cheap way to make the two cases above green is to answer `404` to every `E8`. This is
    the case that refuses it, and it is asserted beside the cleaned run rather than against a
    fixture of its own: a `404` for a missing log means nothing unless a present log is a `200`
    a foot away.
    """
    root = cleaned_and_intact(repo)
    srv = serve(root)

    ok = srv.get(f"/api/runs/{INTACT}/transcript")
    assert ok.status_code == 200, ok.text
    replay = ok.json()
    assert replay["run_id"] == INTACT, replay
    assert replay["entries"], (
        f"the intact run replayed with no entries, so a 404 for the cleaned one would be "
        f"indistinguishable from E8 answering nothing at all: {replay}")
    assert "remedy" not in json.dumps(replay), (
        f"a successful replay carries a remedy — a control offered for a state that is not "
        f"wrong: {replay}")

    # and the cleaned one, in the same server, in the same breath
    assert srv.get(f"/api/runs/{CLEANED}/transcript").status_code == 404


# ── 15 · NEGATIVE (mandatory) ────────────────────────────────────────────────────────────────

RELOAD_CALL = "remedy::reloadManifest()"


def reload_manifest_sites():
    """Every `remedy::reloadManifest()` call site under `server/app`, as `path:line`.

    Swept out of the tree, never listed here. A list written into this file is bounded by what
    its author already thought of, and what this case exists to catch is a blanket delete —
    which agrees with any list short enough to have been typed.
    """
    app = Path(__file__).resolve().parents[1] / "app"
    found = []
    for path in sorted(app.rglob("*")):
        if path.suffix not in (".cpp", ".h"):
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if RELOAD_CALL in line:
                found.append(f"{path.relative_to(app.parent)}:{n}")
    return found


def codes_offering_reload_manifest():
    """The codes §11 check 12's remedy table says offer `reload_manifest`, read off SPEC.md.

    The pairing of code to verb comes from the document on every run. A pair typed into this
    file would agree with a server that had stopped offering it.
    """
    table = remedy_table((DOCS / "product/SPEC.md").read_text())
    return sorted(code for code, offered in table.items()
                  if any(action == "reload_manifest" for action, _ in offered))


def offered_action(response):
    """The `action` a refusal offers, or `None` when it offers no remedy at all.

    A refusal that has LOST its remedy answers `null`, and reading `["action"]` off that is a
    TypeError rather than the sentence the assertion was written to print. The distinction this
    ticket is about is exactly null-versus-a-verb, so the reader has to be told which one arrived.
    """
    remedy = response.json()["error"].get("remedy")
    return remedy["action"] if isinstance(remedy, dict) else remedy


def test_no_other_reload_manifest_remedy_was_removed_with_it(repo, serve):
    """`reload_manifest` is still offered everywhere a manifest really is the thing to reload.

    Two halves, because a blanket `sed` over the tree passes either one alone. The source still
    carries the call, at more than one site and in more than one module — and a live server
    still SENDS it, for codes read off §11's own remedy table rather than named here.
    """
    sites = reload_manifest_sites()
    assert sites, (
        f"{RELOAD_CALL} has left `server/app` entirely. This ticket removes ONE site; a tree "
        f"with none is a blanket delete, and every manifest refusal is now a dead end")
    assert "app/api/_transcript.cpp" not in {s.rsplit(":", 1)[0] for s in sites}, (
        f"E8 still attaches the manifest remedy: {[s for s in sites if '_transcript' in s]}")
    modules = {s.split("/")[1] for s in sites}
    assert len(sites) > 1 and len(modules) > 1, (
        f"the sweep found {len(sites)} site(s) in {sorted(modules)}; before this ticket the "
        f"call was made from several modules, and a sweep that has collapsed to one is the "
        f"blanket delete arriving one file at a time")

    # THE WIRE. Both codes are asserted here because §11's own table says they offer it — the
    # pairing is read off the document, so a server that stopped sending the verb cannot be
    # excused by a pairing this file invented.
    offering = codes_offering_reload_manifest()
    assert {"not_found", "not_declared"} <= set(offering), (
        f"§11's remedy table no longer says these codes offer `reload_manifest`, so the two "
        f"provocations below are asserting something the document does not claim: {offering}")

    root = build_repo(Path(repo.root) / "wire", agent={"command": "/bin/sh"})
    srv = serve(root)

    # `not_found` — the SAME code E8 now answers with `null`, still remediable at another site.
    # That is the whole shape of this ticket: a remedy belongs to a site, not to a code.
    missing_lane = srv.get("/api/lanes/lane/98/diff")
    assert missing_lane.status_code == 404, missing_lane.text
    assert missing_lane.json()["error"]["code"] == "not_found", missing_lane.text
    assert offered_action(missing_lane) == "reload_manifest", (
        f"E10's 404 for an unclaimed lane stopped offering the manifest reload, and it is the "
        f"same code this ticket made terminal at E8: {missing_lane.text}")

    # `not_declared` — a second code, from a second module, so the sweep above is not the only
    # thing standing between a blanket delete and a green gate.
    claimed = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()["lane"]
    try:
        undeclared = srv.post(f"/api/lanes/{lane}/commands", json={"which": "build"})
        assert undeclared.status_code >= 400, undeclared.text
        assert undeclared.json()["error"]["code"] == "not_declared", undeclared.text
        assert offered_action(undeclared) == "reload_manifest", (
            f"E19's refusal of a command the workspace declares none of stopped offering the "
            f"manifest reload: {undeclared.text}")
    finally:
        srv.delete(f"/api/lanes/{lane}?force=true")
