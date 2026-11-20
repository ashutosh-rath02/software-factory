"""SRV-FEAT-315 — `E31 GET /api/runs`: every run in the repository, newest first.

`SPEC.md` §4.19 and §5 `E31`. This is `E7` without the ticket filter and deliberately the same
row plus the three a repository-wide list needs — `ticket_id`, `ticket_title` and `kind`.

Two seams are what this file actually watches:

- **`kind` is the MANIFEST's own `roles`, with `run` as the fallback.** The tool has no built-in
  notion of a QA run; `F31` already says a role name is a string it compares. So every assertion
  about `kind` below reads the declared names OFF THE MANIFEST at test time, and one case runs a
  SECOND repository whose roles are named nothing like the first's. A handler carrying `"qa"` as
  a literal passes a test written against a list, and fails these.
- **the row is the record and the log is the evidence.** `.factory/runs/` is not committed, so
  `committed` is served as `false` rather than described in a component, and a run whose
  `transcript.jsonl` has been cleaned keeps its row while `E8` for it answers `404`. A row that
  vanished with its log would make this index disagree with the ticket's own runs tab.

Every case builds its own synthetic repository. Never this one: an index that only answers about
the repository it was written in has learned that repository (`SPEC.md` §10).
"""

import json
import time
from pathlib import Path

import jsonschema
import pytest

from test_lane import git, gitrepo  # noqa: F401


FACTORY = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((FACTORY / "docs/technical/contracts/http.schema.json").read_text())

# `responses.E31` as a schema in its own right — `$defs` carried along so `runIndexRow` resolves.
# `path`, `method`, `status` and `title` are not keywords and are ignored by the validator.
E31_BODY = dict(SCHEMA["responses"]["E31"],
                **{"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"]})
E31_VALIDATOR = jsonschema.Draft202012Validator(E31_BODY)


# Two role sets that share not one name, so nothing below can be satisfied by a literal. The
# first is the ticket's own — `qa` and one other; the second exists to break a handler that
# learned the first.
ROLES_A = {"qa":       {"start_on_stage": "GATE", "lane": "new",
                        "prompt_file": ".factory/prompts/qa.md"},
           "reviewer": {"start_on_stage": "IMPL", "lane": "new",
                        "prompt_file": ".factory/prompts/reviewer.md"}}

ROLES_B = {"safety":  {"start_on_stage": "GATE", "lane": "new",
                       "prompt_file": ".factory/prompts/safety.md"},
           "linguist": {"start_on_stage": "IMPL", "lane": "new",
                        "prompt_file": ".factory/prompts/linguist.md"}}

TICKETS = [("TKT-001", "The board tells the truth"),
           ("TKT-002", "The rollback protects the fleet"),
           ("TKT-003", "Nothing has ever run on this one")]


def plant_repo(root, *, roles=None, tickets=TICKETS, make_runs_dir=False):
    """A repository the server can be pointed at: a manifest, some tickets, and no git."""
    root = Path(root)
    (root / ".factory").mkdir(parents=True, exist_ok=True)
    manifest = {
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
        "stages": ["SPEC", "TEST", "IMPL", "GATE"],
    }
    if roles is not None:
        manifest["roles"] = roles
        for name, decl in roles.items():
            p = root / decl["prompt_file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"You are the {name}. Check {{ticket_id}}.\n")
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))

    for d in ("docs/epics", "docs/tickets", "docs/reports"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for tid, title in tickets:
        (root / "docs" / "tickets" / f"{tid}.md").write_text(
            f"---\nid: {tid}\ntitle: {title}\nworkspace: core\nstatus: todo\n---\n\nbody\n")
    if make_runs_dir:
        (root / ".factory" / "runs").mkdir(parents=True, exist_ok=True)
    return root


def declared_roles(root):
    """The role names THIS repository declares, read off its manifest at test time.

    Never a list written into this file. `F31`: the tool compares a string the repository wrote,
    so a check that names the roles itself would pass against a handler that names them too.
    """
    return sorted(json.loads((Path(root) / ".factory" / "factory.json").read_text())["roles"])


def plant_run(root, run_id, ticket, *, started, ended=None, outcome="completed", role=None,
              lane="lane/01", n=1, cost=1.25, duration=90, session=None, log=True):
    """One run as `agent::history::write` records one, planted without a process behind it."""
    d = Path(root) / ".factory" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": run_id, "lane": lane, "ticket_id": ticket,
            "session_id": session or f"sess-{run_id}", "n": n,
            "started_iso": started, "ended_iso": ended, "outcome": outcome,
            "duration_s": duration, "cost_usd": cost, "turns": 4,
            "exit_code": 0, "signal": None, "unmapped": {}}
    if role is not None:
        meta["role"] = role
    (d / "meta.json").write_text(json.dumps(meta))
    if log:
        (d / "transcript.jsonl").write_text(
            json.dumps({"seq": 1, "ts": started, "kind": "text", "text": "hello"}) + "\n")
    return d


def index(srv, query=""):
    r = srv.get("/api/runs" + query)
    assert r.status_code == 200, f"/api/runs{query} → {r.status_code} {r.text}"
    body = r.json()
    E31_VALIDATOR.validate(body)
    return body


def rows_by_id(body):
    return {row["run_id"]: row for row in body["runs"]}


def live_lane(root, srv, ticket="TCK-001", **claim):
    """Claim a lane and start a REAL run in it, and hand back its id and its record's path.

    A planted `running` meta.json cannot stand in for a live run: `F6`'s settle sweep runs at
    startup, finds no process behind the record and rewrites it to `orphaned` — measured here,
    which is why the live cases below drive an agent instead of a fixture.
    """
    claimed = srv.post("/api/lanes", json=dict({"ticket_id": ticket, "workspace": "core"},
                                               **claim))
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()["lane"]
    started = srv.post(f"/api/lanes/{lane}/runs", json={})
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]

    meta_path = Path(root) / ".factory" / "runs" / run_id / "meta.json"
    deadline = time.time() + 10
    while time.time() < deadline and not meta_path.exists():
        time.sleep(0.05)
    assert meta_path.exists(), f"no meta.json was written for {run_id}"
    return lane, run_id, meta_path


def repo_with_a_stand_in_agent(gitrepo, roles=None):
    """A git repository whose declared agent is a process that starts and stays up."""
    root = gitrepo(agent={"command": "/bin/sh agent.sh"})
    (root / "agent.sh").write_text("#!/bin/sh\nwhile : ; do sleep 1 ; done\n")
    if roles is not None:
        manifest = json.loads((root / ".factory" / "factory.json").read_text())
        manifest["roles"] = roles
        (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
        for decl in roles.values():
            p = root / decl["prompt_file"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("Check {ticket_id}.\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "a stand-in agent this run can really start")
    return root


# ── 1 ────────────────────────────────────────────────────────────────────────────────────────

def test_every_run_in_the_repository_appears_newest_first(repo, serve):
    """**`test_every_run_in_the_repository_appears_newest_first`** — across **three tickets with
    two, one and zero runs**, so a total implementing `first` or `any` is separable from a real
    count, and the ordering is visible.
    """
    plant_repo(repo.root, roles=ROLES_A)
    plant_run(repo.root, "run-1-aaaa", "TKT-001", started="2026-08-30T09:00:00Z", n=1,
              ended="2026-08-30T09:05:00Z")
    plant_run(repo.root, "run-2-bbbb", "TKT-001", started="2026-08-30T11:00:00Z", n=2,
              ended="2026-08-30T11:20:00Z")
    plant_run(repo.root, "run-3-cccc", "TKT-002", started="2026-08-30T10:00:00Z", n=1,
              ended="2026-08-30T10:30:00Z")
    # TKT-003 has none, deliberately.

    srv = serve(repo.root)
    body = index(srv)

    # THREE, not one and not "some": two tickets contribute and a third contributes nothing.
    assert body["total"] == 3, body
    assert [row["run_id"] for row in body["runs"]] == [
        "run-2-bbbb", "run-3-cccc", "run-1-aaaa"], body["runs"]

    # every run in the REPOSITORY — the rows span more than one ticket, which is the whole
    # difference between this and E7
    assert sorted({row["ticket_id"] for row in body["runs"]}) == ["TKT-001", "TKT-002"], body
    assert "TKT-003" not in {row["ticket_id"] for row in body["runs"]}, (
        "a ticket with no runs contributed a row")

    # the two fields a repository-wide list needs and a per-ticket row does not carry
    row = rows_by_id(body)["run-2-bbbb"]
    assert row["ticket_id"] == "TKT-001", row
    assert row["ticket_title"] == "The board tells the truth", row

    # and the rest of E7's row travels with it, unchanged
    assert row["n"] == 2 and row["lane"] == "lane/01", row
    assert row["outcome"] == "completed", row
    assert row["started_iso"] == "2026-08-30T11:00:00Z", row
    assert row["ended_iso"] == "2026-08-30T11:20:00Z", row
    assert row["duration_s"] == 90 and row["cost_usd"] == 1.25, row
    assert row["session_id"] == "sess-run-2-bbbb", row

    # the same three runs asked for per ticket: E7 is the slice, and it is not replaced
    seven = srv.get("/api/tickets/TKT-001/runs")
    assert seven.status_code == 200, seven.text
    assert [r["run_id"] for r in seven.json()["runs"]] == ["run-2-bbbb", "run-1-aaaa"], seven.text
    assert srv.get("/api/tickets/TKT-003/runs").json()["runs"] == []


def test_a_page_after_a_cursor_is_the_rows_after_it(repo, serve):
    """`?limit=<int, default 100, max 500>&before=<run_id, optional>` for the page after a given
    row. Newest first, always.

    The positive control for criterion 6: a `before` that names a run the tool HAS must page, or
    the refusal below would prove only that `before` never works.
    """
    plant_repo(repo.root, roles=ROLES_A)
    for i in range(5):
        plant_run(repo.root, f"run-{i}-x", "TKT-001", n=i + 1,
                  started=f"2026-08-30T1{i}:00:00Z", ended=f"2026-08-30T1{i}:30:00Z")

    srv = serve(repo.root)
    every = index(srv)
    assert [r["run_id"] for r in every["runs"]] == [
        "run-4-x", "run-3-x", "run-2-x", "run-1-x", "run-0-x"], every

    page = index(srv, "?limit=2")
    assert [r["run_id"] for r in page["runs"]] == ["run-4-x", "run-3-x"], page
    assert page["total"] == 5, "`total` is every run recorded, never the page's length"

    after = index(srv, "?limit=2&before=run-3-x")
    assert [r["run_id"] for r in after["runs"]] == ["run-2-x", "run-1-x"], after
    assert after["total"] == 5, after

    tail = index(srv, "?before=run-1-x")
    assert [r["run_id"] for r in tail["runs"]] == ["run-0-x"], tail

    # the last cursor pages off the end and that is an empty page, not a restart at the newest
    end = index(srv, "?before=run-0-x")
    assert end["runs"] == [], end
    assert end["total"] == 5, end


# ── 2 ────────────────────────────────────────────────────────────────────────────────────────

def test_kind_is_a_declared_role_or_the_fallback(repo, serve, tmp_path):
    """**`test_kind_is_a_declared_role_or_the_fallback`** — with a manifest declaring `qa` and one
    other role, a run in each and a plain run: three `kind` values, two of them the manifest's
    strings and one `run`. **A literal `"qa"` anywhere in the handler fails this** — assert
    against the manifest's declared names, read at test time, not against a list in the test.
    """
    plant_repo(repo.root, roles=ROLES_A)
    first, second = declared_roles(repo.root)          # read off the manifest, not written here
    assert first != second and len(declared_roles(repo.root)) == 2

    plant_run(repo.root, "run-1-a", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z", role=first)
    plant_run(repo.root, "run-2-b", "TKT-001", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:01:00Z", role=second)
    plant_run(repo.root, "run-3-c", "TKT-002", started="2026-08-30T11:00:00Z",
              ended="2026-08-30T11:01:00Z")            # nobody declared a role for it

    srv = serve(repo.root)

    # the server's own view of the manifest declares exactly those names — so the values asserted
    # below are the repository's word travelling through the tool, not the test's
    served = srv.get("/api/manifest").json()
    assert sorted(served["roles"]) == [first, second], served["roles"]

    rows = rows_by_id(index(srv))
    assert rows["run-1-a"]["kind"] == first, rows["run-1-a"]
    assert rows["run-2-b"]["kind"] == second, rows["run-2-b"]
    assert rows["run-3-c"]["kind"] == "run", rows["run-3-c"]
    assert sorted({r["kind"] for r in index(srv)["runs"]}) == sorted([first, second, "run"])

    # A SECOND repository whose roles share no name with the first. `qa` is not among them, and a
    # handler that knows the word `qa` cannot answer this one at all.
    other = plant_repo(tmp_path / "other", roles=ROLES_B)
    names = declared_roles(other)
    assert not set(names) & set(declared_roles(repo.root)), (names, declared_roles(repo.root))
    plant_run(other, "run-1-a", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z", role=names[0])
    plant_run(other, "run-2-b", "TKT-002", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:01:00Z", role=names[1])

    elsewhere = rows_by_id(index(serve(other)))
    assert elsewhere["run-1-a"]["kind"] == names[0], elsewhere["run-1-a"]
    assert elsewhere["run-2-b"]["kind"] == names[1], elsewhere["run-2-b"]


def test_a_repository_that_declares_no_role_sees_one_kind(repo, serve, tmp_path):
    """§4.19: *a repository that declares a `qa` role sees `qa` here because it wrote that word,
    and one that declares none sees one kind.*

    The negative half of criterion 2. `kind` may not be invented from a word the manifest does
    not carry, so a role name no longer declared falls back rather than being echoed.
    """
    plant_repo(repo.root, roles=None)
    plant_run(repo.root, "run-1-a", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z", role="qa")
    plant_run(repo.root, "run-2-b", "TKT-002", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:01:00Z")

    srv = serve(repo.root)
    assert "roles" not in srv.get("/api/manifest").json(), srv.get("/api/manifest").json()

    body = index(srv)
    assert {r["kind"] for r in body["runs"]} == {"run"}, (
        "a repository declaring no role saw more than one kind: " + json.dumps(body["runs"]))


def test_the_writer_records_the_role_the_index_reads(gitrepo, serve, tmp_path):
    """The fixture shape is a check too: a planted `meta.json` proves nothing about `kind` unless
    the tool really writes what the fixture plants.

    So one real lane, claimed in a declared role, one real run, and the record on disk is read
    back — then `E31` is asked what kind it was.
    """
    root = repo_with_a_stand_in_agent(gitrepo, roles=ROLES_A)
    role = declared_roles(root)[0]
    srv = serve(root)

    lane, run_id, meta_path = live_lane(root, srv, role=role)
    try:
        recorded = json.loads(meta_path.read_text())

        assert recorded.get("role") == role, (
            "the run record does not carry the role its lane was claimed in, so every planted "
            f"fixture in this file would be a shape the tool never writes: {recorded}")

        row = rows_by_id(index(srv)).get(run_id)
        assert row, f"{run_id} is not in the index: {index(srv)}"
        assert row["kind"] == role, row
    finally:
        srv.delete(f"/api/lanes/{lane}?force=true")


# ── 3 ────────────────────────────────────────────────────────────────────────────────────────

def test_a_run_whose_log_was_removed_keeps_its_row(repo, serve):
    """**`test_a_run_whose_log_was_removed_keeps_its_row`** — delete one run's log; its row
    survives with its recorded fields, and `E8` for that `run_id` answers `404`. A row that
    vanishes with its log fails: the index would then disagree with the ticket's own runs tab.
    """
    plant_repo(repo.root, roles=ROLES_A)
    kept = plant_run(repo.root, "run-1-keeps", "TKT-001", started="2026-08-30T09:00:00Z",
                     ended="2026-08-30T09:40:00Z", role="qa", cost=2.5, duration=2400, n=3)
    plant_run(repo.root, "run-2-intact", "TKT-002", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:10:00Z")
    (kept / "transcript.jsonl").unlink()

    srv = serve(repo.root)
    body = index(srv)

    assert body["total"] == 2, body
    row = rows_by_id(body).get("run-1-keeps")
    assert row, f"the row went away with its log: {body}"

    # WITH ITS RECORDED FIELDS — a surviving row that has lost what it recorded is not a record
    assert row["n"] == 3 and row["kind"] == "qa" and row["ticket_id"] == "TKT-001", row
    assert row["outcome"] == "completed", row
    assert row["duration_s"] == 2400 and row["cost_usd"] == 2.5, row
    assert row["started_iso"] == "2026-08-30T09:00:00Z", row
    assert row["ended_iso"] == "2026-08-30T09:40:00Z", row
    assert row["ticket_title"] == "The board tells the truth", row

    # and the evidence really is gone: E8 for the same run_id is the 404 §4.10 draws as a sentence
    replay = srv.get("/api/runs/run-1-keeps/transcript")
    assert replay.status_code == 404, replay.text
    assert replay.json()["error"]["code"] == "not_found", replay.text
    assert replay.json()["error"]["detail"]["path"].endswith(
        "run-1-keeps/transcript.jsonl"), replay.text

    # the run whose log is intact still replays, so the 404 above is about the missing log
    assert srv.get("/api/runs/run-2-intact/transcript").status_code == 200

    # and the ticket's own runs tab agrees with this index about the run that lost its log
    seven = srv.get("/api/tickets/TKT-001/runs").json()["runs"]
    assert [r["run_id"] for r in seven] == ["run-1-keeps"], seven


def test_a_missing_transcript_keeps_the_path_of_the_history_that_recorded_it(repo, serve):
    """E8 resolves a cleaned run through its surviving history row, not an empty fallback.

    The vector-ledger adapter is shared by the repository index and ticket history. A missing
    file is therefore the important negative: the adapter must retain the path produced by the
    ledger that still owns the run's ``meta.json`` even though no successful replay can return.
    """
    root = plant_repo(repo.root, roles=ROLES_A)
    run = plant_run(root, "run-1-cleaned", "TKT-001", started="2026-08-30T09:00:00Z",
                    ended="2026-08-30T09:40:00Z")
    original = run / "transcript.jsonl"
    renamed = run / "transcript.saved"
    original.rename(renamed)

    srv = serve(root)
    assert "run-1-cleaned" in rows_by_id(index(srv))
    assert [row["run_id"] for row in srv.get("/api/tickets/TKT-001/runs").json()["runs"]] == [
        "run-1-cleaned"]

    replay = srv.get("/api/runs/run-1-cleaned/transcript")
    assert replay.status_code == 404, replay.text
    assert replay.json()["error"]["detail"]["path"] == str(original), replay.text
    assert renamed.exists(), "the fixture must rename rather than delete the transcript bytes"


# ── 4 ────────────────────────────────────────────────────────────────────────────────────────

def test_the_answer_says_the_history_is_not_committed(repo, serve):
    """**`test_the_answer_says_the_history_is_not_committed`** — `committed` is `false`, always,
    because `.factory/` is gitignored. A hardcoded `true` fails, and so does omitting the field.
    """
    plant_repo(repo.root, roles=ROLES_A)
    plant_run(repo.root, "run-1-a", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z")

    srv = serve(repo.root)

    for query in ("", "?limit=1", "?before=run-1-a"):
        body = index(srv, query)
        assert "committed" in body, f"the field is missing from /api/runs{query}: {body}"
        assert body["committed"] is False, (
            f"/api/runs{query} claims the history is committed: {body['committed']!r}")

    # and on the empty answer too, where there is no row to carry it
    other = index(serve(plant_repo(repo.root.parent / "empty", roles=ROLES_A)))
    assert other["committed"] is False, other


# ── 5 · negative (mandatory) ─────────────────────────────────────────────────────────────────

def test_no_runs_at_all_is_an_empty_list_and_not_an_error(repo, serve, tmp_path):
    """**(negative) `test_no_runs_at_all_is_an_empty_list_and_not_an_error`** — `.factory/runs/`
    absent, then present and empty: `200`, `total: 0`, both times. History is expendable (§2) and
    its absence is a state.
    """
    absent = plant_repo(tmp_path / "absent", roles=ROLES_A, make_runs_dir=False)
    assert not (absent / ".factory" / "runs").exists()

    r = serve(absent).get("/api/runs")
    assert r.status_code == 200, f"an absent history was an error: {r.status_code} {r.text}"
    body = r.json()
    E31_VALIDATOR.validate(body)
    assert body["runs"] == [] and body["total"] == 0, body

    empty = plant_repo(tmp_path / "empty", roles=ROLES_A, make_runs_dir=True)
    assert (empty / ".factory" / "runs").is_dir()

    r = serve(empty).get("/api/runs")
    assert r.status_code == 200, f"an empty history was an error: {r.status_code} {r.text}"
    body = r.json()
    E31_VALIDATOR.validate(body)
    assert body["runs"] == [] and body["total"] == 0, body


# ── 6 · negative (mandatory) ─────────────────────────────────────────────────────────────────

def test_a_before_cursor_naming_a_run_that_does_not_exist_is_refused(repo, serve):
    """**(negative) `test_a_before_cursor_naming_a_run_that_does_not_exist_is_refused`** — `400`
    naming the parameter, rather than silently returning the newest page. A cursor the server
    cannot honour must not look like a cursor it did.
    """
    plant_repo(repo.root, roles=ROLES_A)
    plant_run(repo.root, "run-1-a", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z")
    plant_run(repo.root, "run-2-b", "TKT-002", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:01:00Z")

    srv = serve(repo.root)
    r = srv.get("/api/runs?before=run-9-nothing")

    assert r.status_code == 400, (
        f"an unknown cursor answered {r.status_code}: {r.text}")

    # NAMING THE PARAMETER — a refusal that does not say which member was wrong sends the caller
    # to read a log
    error = r.json()["error"]
    assert error["code"] == "bad_request", error
    named = json.dumps(error)
    assert "before" in named, f"the refusal does not name `before`: {named}"
    assert error["detail"].get("param") == "before", error
    assert error["detail"].get("before") == "run-9-nothing", error

    # and it is NOT the newest page wearing a 400 — nothing was returned
    assert "runs" not in r.json(), r.text

    # the same request with a cursor the tool has is honoured, so the refusal is about the value
    assert srv.get("/api/runs?before=run-2-b").status_code == 200


# ── 7 ────────────────────────────────────────────────────────────────────────────────────────

def test_the_response_validates_against_http_schema_json(repo, serve):
    """**`test_the_response_validates_against_http_schema_json`** — through
    `test_schema_consumers.py`.

    That file runs the consuming workspace's own build and gate when `http.schema.json` changes,
    which is the client half. This is the server half of the same claim, and it is the half that
    can say the LIVE body matches: `responses.E31` is loaded off the document and every response
    in this module goes through `index()`, which validates. Here it is asserted directly, over a
    body carrying every optional shape at once.
    """
    plant_repo(repo.root, roles=ROLES_A)
    plant_run(repo.root, "run-1-done", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z", role="qa", cost=0.0475)
    plant_run(repo.root, "run-2-killed", "TKT-002", started="2026-08-30T09:30:00Z",
              ended="2026-08-30T09:31:00Z", outcome="killed by you")
    plant_run(repo.root, "run-3-orphan", "TKT-002", started="2026-08-30T08:00:00Z",
              ended="2026-08-30T08:30:00Z", outcome="orphaned")

    srv = serve(repo.root)
    r = srv.get("/api/runs")
    assert r.status_code == 200, r.text
    E31_VALIDATOR.validate(r.json())

    # the node really is in the document, and it is E31's own, not another endpoint's
    node = SCHEMA["responses"]["E31"]
    assert node["path"] == "/api/runs" and node["method"] == "GET" and node["status"] == 200, node
    assert node["properties"]["runs"]["items"]["$ref"] == "#/$defs/runIndexRow", node

    # and it did NOT collide with E7's row: two shapes for one record is how a list and a detail
    # come to disagree, so E31's row is its own `$defs` key and its own generated interface
    assert SCHEMA["$defs"]["runRow"]["title"] == "RunRow"
    assert SCHEMA["$defs"]["runIndexRow"]["title"] == "RunIndexRow"

    # a body the schema must refuse, so the validator above is not vacuous
    with pytest.raises(jsonschema.ValidationError):
        E31_VALIDATOR.validate({"runs": [], "total": 0, "committed": True})


# ── what a broken history does to this surface ───────────────────────────────────────────────

def test_a_record_that_cannot_be_read_does_not_take_the_index_with_it(repo, serve):
    """History is expendable (§2), and half a history is still a history.

    Measured against the real binary while breaking this on purpose: a `meta.json` that will not
    parse, a directory with no record in it at all, a run whose ticket document has been deleted,
    and query values no client would send. None of them may turn the surface into an error page,
    because the answer to *what has run* is what is left, not nothing.
    """
    plant_repo(repo.root, roles=ROLES_A)
    plant_run(repo.root, "run-1-ok", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z")
    plant_run(repo.root, "run-2-gone-ticket", "TKT-404", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:01:00Z")
    broken = plant_run(repo.root, "run-3-broken", "TKT-001", started="2026-08-30T11:00:00Z",
                       ended="2026-08-30T11:01:00Z")
    (broken / "meta.json").write_text("{not json")
    (Path(repo.root) / ".factory" / "runs" / "run-4-empty-dir").mkdir()

    srv = serve(repo.root)
    body = index(srv)

    # the readable records are all there, and the unreadable one is not a row and not a crash
    assert [r["run_id"] for r in body["runs"]] == ["run-2-gone-ticket", "run-1-ok"], body
    assert body["total"] == 2, (
        "a record that cannot be parsed is not a record, and `total` counts records: " + str(body))

    # a run whose ticket document is gone keeps its row: the row is the record, and the document
    # is not what recorded it
    orphan_row = rows_by_id(body)["run-2-gone-ticket"]
    assert orphan_row["ticket_id"] == "TKT-404", orphan_row
    assert orphan_row["ticket_title"] == "", orphan_row

    # and query values no client sends fall back rather than refusing — only `before` refuses,
    # because only `before` can be a claim about a thing that is not there
    for query in ("?limit=0", "?limit=-3", "?limit=abc", "?limit=99999", "?before="):
        assert index(srv, query)["total"] == 2, query
    assert [r["run_id"] for r in index(srv, "?limit=abc")["runs"]] == [
        "run-2-gone-ticket", "run-1-ok"], "a limit that is not a number lost rows"

    # `before` is compared against run ids and is never a path
    traversal = srv.get("/api/runs?before=../../etc/passwd")
    assert traversal.status_code == 400, traversal.text
    assert traversal.json()["error"]["detail"]["param"] == "before", traversal.text


# ── pre-decided: a live run's clock ──────────────────────────────────────────────────────────

def test_a_live_run_is_measured_to_now_and_has_no_end(gitrepo, serve):
    """Pre-decided: *`duration_s` for a live run is measured to now, and `ended_iso` is absent —
    not zero, not the start time.*

    A REAL run, because a planted one cannot be live: `F6`'s settle sweep runs at startup, sees
    no process behind a `running` record and rewrites it to `orphaned` before the first request
    is served. Measured here — the first cut of this case planted the record and read back
    `outcome: 'orphaned'`, `ended_iso: '<the moment the server started>'`.

    The discriminator is that the run's OWN RECORD still says `duration_s: 0`, because nothing
    rewrites it between turns. A handler that echoed the record would report 0 here.
    """
    root = repo_with_a_stand_in_agent(gitrepo)
    srv = serve(root)
    lane, run_id, meta_path = live_lane(root, srv)
    try:
        time.sleep(3)
        row = rows_by_id(index(srv))[run_id]
        recorded = json.loads(meta_path.read_text())

        assert recorded["outcome"] == "running" and recorded["ended_iso"] is None, recorded
        assert row["outcome"] == "running", row

        assert "ended_iso" not in row, (
            f"a live run carries an end time: {row.get('ended_iso')!r} — the pre-decision is "
            f"absent, not null, not the start time")

        assert recorded["duration_s"] == 0, (
            f"the record moved on its own, so the comparison below proves nothing: {recorded}")
        assert row["duration_s"] >= 2, (
            f"the row reports the record's stale {recorded['duration_s']} rather than the time "
            f"since {recorded['started_iso']}: {row}")
        assert row["duration_s"] < 600, row
    finally:
        srv.delete(f"/api/lanes/{lane}?force=true")


def test_a_finished_run_keeps_the_clock_it_recorded(repo, serve):
    """The other half of the pre-decision: the clock above is for a LIVE run and nothing else.

    A finished run's `duration_s` and `ended_iso` are what its record says, however long ago that
    was — a row recomputed to now for every run would make every old run look enormous.
    """
    plant_repo(repo.root, roles=ROLES_A)
    plant_run(repo.root, "run-1-done", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z", duration=60)

    row = rows_by_id(index(serve(repo.root)))["run-1-done"]
    assert row["ended_iso"] == "2026-08-30T09:01:00Z", row
    assert row["duration_s"] == 60, row
