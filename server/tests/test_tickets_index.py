"""SRV-FEAT-311 — E29: every ticket, grouped by parent, with provenance DERIVED and never claimed.

`SPEC.md` §4.17 and §5 `E29`. One endpoint, one page load: the three lenses and the table the
design draws are one answer, because per-lens endpoints are three ways for the counts to disagree.

Every case builds its own git repository and points a server at it. The answer this endpoint gives
is a fact about a repository's history, and a test that only passes against the repository it was
written in has learned that repository (`test_delivery.py`'s opening line, and it is this file's
too — `provenance` is the same derivation).

**The one line that matters most, and what this file is really watching.** `provenance` is
`delivery.state` and NEVER the document's `status:` field. The document is a claim written by a
session that may not have survived to write it truthfully — three of the first four QA runs on this
repository left one behind that was not true, and in the worst case it said `pass`. So the fixture
holds exactly that ticket, and `test_provenance_is_the_derived_state_and_the_disagreement_is_carried`
fails a row whose `provenance` equals the document's claim for it.

The second thing being watched is an aggregate over a collection whose members AGREE. A facet count
computed over the page instead of the repository is invisible if every page carries the same stages,
so the fixture's page 1 and page 2 deliberately **disagree** about which stages appear.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"
HTTP_SCHEMA = json.loads((DOCS / "technical/contracts/http.schema.json").read_text())
E29_SCHEMA = dict(HTTP_SCHEMA["responses"]["E29"], **{"$defs": HTTP_SCHEMA["$defs"]})

REQ_DIR = "product/requirements"
TKT_DIR = "product/work-items"
RPT_DIR = "product/deliveries"

# The sequence the manifest declares. `stage=` may name one of these and nothing else.
SEQUENCE = ["SPEC", "TEST", "IMPL", "GATE", "QA"]

# `delivery.state`'s own values, in SPEC.md §3.5's table order, with `all` in front. `provenance=`
# may name one of these and nothing else.
PROVENANCE = ["not_started", "running", "stalled", "unshipped", "waiting", "done", "unknown"]

# The order the repository's own files put the tickets in — filename order, which is what
# `catalog::Store::read_` sorts by. SPEC.md §4.17: sorting is the repository's order within a
# group, not alphabetical-by-title and not by any state the tool derived.
ORDER = ["TIX-001", "TIX-002", "TIX-003", "TIX-004", "TIX-005", "TIX-006"]


def manifest():
    return {
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}, "beta": {"path": "beta"}},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        "stages": SEQUENCE,
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "display": {"ticket_fields": ["owner", "contract"]},
        "agent": {"command": "claude"},
    }


def doc(front, body="Body."):
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class Git:
    def __init__(self, root):
        self.root = root

    def __call__(self, *args, author=None):
        env = None
        if author:
            env = {"GIT_AUTHOR_NAME": author, "GIT_COMMITTER_NAME": author,
                   "GIT_AUTHOR_EMAIL": "a@example.invalid",
                   "GIT_COMMITTER_EMAIL": "a@example.invalid"}
        out = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                             text=True, timeout=30,
                             env=None if env is None else dict(os.environ, **env))
        assert out.returncode == 0, f"git {' '.join(args)} → {out.returncode}\n{out.stderr}"
        return out.stdout.strip()

    def init(self):
        self("init", "-b", "main")
        self("config", "user.email", "t@example.invalid")
        self("config", "user.name", "Test")
        self("config", "commit.gpgsign", "false")
        return self

    def commit(self, message, author=None):
        self("add", "-A")
        self("commit", "-m", message, "--no-gpg-sign", author=author)
        return self("rev-parse", "HEAD")


def write_run(root, run_id, ticket_id, stage, cost=0.0):
    """One recorded run on disk, exactly as `agent::history` writes it.

    `lane::Store::start` seeds its stage history from `.factory/runs/` at startup, so a repository
    can carry the stage a ticket has REACHED without a live lane. That is the only way a fixture
    can make page 1 and page 2 disagree about stages, which is what criterion 3 needs.
    """
    d = root / ".factory" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({
        "run_id": run_id, "lane": "", "ticket_id": ticket_id, "session_id": "s-" + run_id,
        "n": 1, "started_iso": "2026-08-31T09:00:00Z", "ended_iso": "2026-08-31T09:10:00Z",
        "outcome": "ended cleanly", "duration_s": 600, "cost_usd": cost, "turns": 2,
        "exit_code": 0, "signal": None, "unmapped": {},
    }) + "\n")
    # `agent::history::stagesByTicket` pre-filters on the literal substring `"kind":"stage"`, so
    # the separators matter: `json.dumps`'s default `", "` and `": "` write a line the server
    # skips before it ever parses it.
    (d / "transcript.jsonl").write_text(
        json.dumps({"seq": 1, "kind": "stage", "stage": stage},
                   separators=(",", ":")) + "\n")


def build(repo, gitrepo):
    """The repository every case in this file is about.

    Two epics with three and one children plus two orphans; six tickets whose stages and whose
    provenance both disagree across the page boundary at `page_size=3`.

        group          ticket    stage         provenance    document says
        REQ-001        TIX-001   SPEC          unshipped     pass        ← the disagreement
        REQ-001        TIX-002   SPEC          not_started   todo
        REQ-001        TIX-003   IMPL          done          pass
        REQ-002        TIX-004   GATE          unshipped     todo
        standalone     TIX-005   QA            not_started   todo
        standalone     TIX-006   not_started   not_started   todo

    page 1 (`page_size=3`) holds SPEC and IMPL; page 2 holds GATE, QA and no stage at all. A facet
    computed over either page is visibly wrong about the other.
    """
    repo.manifest(manifest())
    repo.write("beta/.keep", "")
    repo.write(f"{REQ_DIR}/REQ-001-the-first.md",
               doc({"id": "REQ-001", "title": "The first requirement", "status": "draft"}))
    repo.write(f"{REQ_DIR}/REQ-002-the-second.md",
               doc({"id": "REQ-002", "title": "The second requirement", "status": "draft"}))

    repo.write(f"{TKT_DIR}/TIX-001-alpha.md", doc(
        {"id": "TIX-001", "parent": "REQ-001", "workspace": "alpha",
         "title": "The one whose document is not true", "status": "pass",
         "owner": "written-down-owner", "contract": "wire-v1"}))
    repo.write(f"{TKT_DIR}/TIX-002-bravo.md", doc(
        {"id": "TIX-002", "parent": "REQ-001", "workspace": "alpha",
         "title": "Nobody has started this", "status": "todo", "owner": "bob"}))
    repo.write(f"{TKT_DIR}/TIX-003-charlie.md", doc(
        {"id": "TIX-003", "parent": "REQ-001", "workspace": "beta",
         "title": "Merged and reported", "status": "pass"}))
    repo.write(f"{TKT_DIR}/TIX-004-delta.md", doc(
        {"id": "TIX-004", "parent": "REQ-002", "workspace": "alpha",
         "title": "At the gate, on no remote", "status": "todo"}))
    repo.write(f"{TKT_DIR}/TIX-005-echo.md", doc(
        {"id": "TIX-005", "parent": "REQ-404", "workspace": "alpha",
         "title": "Its parent names no requirement on disk", "status": "todo"}))
    # No `parent:` key AT ALL — the other half of criterion 1's "a ticket with no parent".
    repo.write(f"{TKT_DIR}/TIX-006-foxtrot.md", doc(
        {"id": "TIX-006", "workspace": "beta", "title": "No parent key at all", "status": "todo"}))

    write_run(repo.root, "run-0001", "TIX-001", "SPEC", cost=0.25)
    write_run(repo.root, "run-0002", "TIX-001", "SPEC", cost=0.75)
    write_run(repo.root, "run-0003", "TIX-002", "SPEC", cost=0.0)
    write_run(repo.root, "run-0004", "TIX-003", "IMPL", cost=1.5)
    write_run(repo.root, "run-0005", "TIX-004", "GATE", cost=0.0)
    write_run(repo.root, "run-0006", "TIX-005", "QA", cost=0.0)

    repo.write(f"{RPT_DIR}/TIX-003-report.md",
               "# TIX-003\n\nDelivered in run-0004.\n")

    gitrepo.init()
    gitrepo.commit("the repository as it stands")

    # TIX-003 — merged into the base branch, so `done`, and its author is on a branch git kept.
    gitrepo("checkout", "-b", "ticket/TIX-003")
    repo.write("src/three.txt", "three\n")
    gitrepo.commit("TIX-003: the work", author="Grace Hopper")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TIX-003", "ticket/TIX-003")

    # TIX-001 — two commits on a branch no remote has. The document says `pass`.
    gitrepo("checkout", "-b", "ticket/TIX-001")
    repo.write("src/one.txt", "one\n")
    gitrepo.commit("TIX-001: the first half", author="Ada Lovelace")
    repo.write("src/one-more.txt", "one more\n")
    gitrepo.commit("TIX-001: the second half", author="Ada Lovelace")

    # TIX-004 — one commit on a branch no remote has.
    gitrepo("checkout", "main")
    gitrepo("checkout", "-b", "ticket/TIX-004")
    repo.write("src/four.txt", "four\n")
    gitrepo.commit("TIX-004: the work", author="Katherine Johnson")

    gitrepo("checkout", "main")
    return repo


@pytest.fixture
def gitrepo(repo):
    return Git(repo.root)


@pytest.fixture
def api29(repo, gitrepo, serve):
    """A server over the fixture repository, with the delivery probe having actually looked.

    The probe reads the world on its own schedule; a case that asked before it had looked would be
    asserting on `unknown` and would pass or fail with the machine's load.

    It waits on `delivery.state` and DELIBERATELY NOT on `provenance`. Criterion 2's whole subject
    is whether `provenance` is that derived value, and a fixture that waited on `provenance` would
    fail in setup — reporting an ERROR before the case's own assertions ever ran. Measured: with
    `provenance` written from the document's `status:`, waiting on `provenance` gives
    `ERROR at setup`, and waiting on `delivery.state` gives the case's own
    `assert row["provenance"] != row["status"]`.
    """
    build(repo, gitrepo)
    srv = serve(repo.root, FACTORY_DELIVERY_POLL_MS="200")
    deadline = time.time() + 20
    seen = None
    while time.time() < deadline:
        r = srv.get("/api/tickets")
        if r.status_code == 200:
            seen = {t["id"]: t["delivery"]["state"]
                    for g in r.json()["groups"] for t in g["tickets"]}
            if seen.get("TIX-001") == "unshipped" and seen.get("TIX-003") == "done":
                return srv
        time.sleep(0.1)
    raise AssertionError(f"delivery never settled; last was {seen} "
                         f"(status {r.status_code}: {r.text[:400]})")


def get(srv, query=""):
    r = srv.get("/api/tickets" + query)
    assert r.status_code == 200, f"/api/tickets{query} → {r.status_code} {r.text[:400]}"
    return r.json()


def rows(body):
    return [t for g in body["groups"] for t in g["tickets"]]


def ids(body):
    return [t["id"] for t in rows(body)]


def facet(body, axis, value):
    for f in body["facets"][axis]:
        if f["value"] == value:
            return f["count"]
    return None


# ── criterion 1 ──────────────────────────────────────────────────────────────────────────────

def test_every_ticket_appears_exactly_once_under_the_parent_it_names(api29):
    """`test_every_ticket_appears_exactly_once_under_the_parent_it_names` — grouped by `parent`;
    a ticket with no parent lands in the standalone group. The fixture has **two epics with three
    and one children plus two orphans**, so a `count` implementing `first`/`any` is separable from
    one implementing a real total.
    """
    body = get(api29)

    # grouped by `parent`
    groups = {g["parent"]: g for g in body["groups"]}
    assert [g["parent"] for g in body["groups"]] == ["REQ-001", "REQ-002", "standalone"], \
        body["groups"]
    assert [t["id"] for t in groups["REQ-001"]["tickets"]] == ["TIX-001", "TIX-002", "TIX-003"]
    assert [t["id"] for t in groups["REQ-002"]["tickets"]] == ["TIX-004"]

    # a ticket with no parent lands in the standalone group — BOTH kinds of no parent: one whose
    # `parent:` names a requirement that is not on disk, and one with no `parent:` key at all.
    assert [t["id"] for t in groups["standalone"]["tickets"]] == ["TIX-005", "TIX-006"]

    # EXACTLY ONCE: no ticket is in two groups and none is missing
    assert ids(body) == ORDER, ids(body)
    assert len(ids(body)) == len(set(ids(body)))

    # two epics with three and one children plus two orphans — the `count` is a REAL TOTAL, and
    # `first`/`any` would put 1 on every one of these three.
    assert [groups["REQ-001"]["count"], groups["REQ-002"]["count"],
            groups["standalone"]["count"]] == [3, 1, 2]
    assert body["total"] == 6

    # the group header's numbers are members of the same object as the rows they describe
    # (WEB-FIX-091), so the bar cannot describe a set the rows below it are not
    assert groups["REQ-001"]["merged"] == 1, groups["REQ-001"]
    assert groups["REQ-002"]["merged"] == 0
    for g in body["groups"]:
        assert g["count"] == len(g["tickets"]), g
        assert g["merged"] + g["pushed"] <= g["count"], g

    # the group carries the requirement's title, not just its id
    assert groups["REQ-001"]["title"] == "The first requirement"
    assert groups["REQ-002"]["title"] == "The second requirement"

    # sorting is the repository's order within a group — filename order, not alphabetical by title
    titles = [t["title"] for t in groups["REQ-001"]["tickets"]]
    assert titles != sorted(titles), titles


# ── criterion 2 ──────────────────────────────────────────────────────────────────────────────

def test_provenance_is_the_derived_state_and_the_disagreement_is_carried(api29):
    """`test_provenance_is_the_derived_state_and_the_disagreement_is_carried` — the fixture holds
    a ticket whose document says `pass` and whose delivery is `unshipped`. The row's `provenance`
    is the derived value, `delivery.disagrees` is true, and `delivery.claimed` is `"pass"`.
    **A row whose provenance equals the document's `status:` for this ticket fails.**
    """
    body = get(api29)
    row = {t["id"]: t for t in rows(body)}["TIX-001"]

    # the document says `pass` and the delivery is `unshipped`
    assert row["status"] == "pass", row
    assert row["provenance"] == "unshipped", row

    # the row's `provenance` IS the derived value
    assert row["provenance"] == row["delivery"]["state"], row

    # A ROW WHOSE PROVENANCE EQUALS THE DOCUMENT'S `status:` FOR THIS TICKET FAILS.
    assert row["provenance"] != row["status"], (
        "provenance was read off the document's claim; §3.5 says it is git and never the field")

    # `delivery.disagrees` is true, and `delivery.claimed` is "pass"
    assert row["delivery"]["disagrees"] is True, row["delivery"]
    assert row["delivery"]["claimed"] == "pass", row["delivery"]

    # the row carries BOTH, because that disagreement is the most valuable thing on it
    assert set(["status", "provenance", "delivery"]).issubset(row), sorted(row)

    # and provenance is not merged into the stage: two axes, two members (§3.2)
    assert isinstance(row["stage"], dict) and "index" in row["stage"], row["stage"]
    assert row["stage"].get("index") != row["provenance"]

    # the derivation is not "always disagree": a ticket the document is right about does not
    assert {t["id"]: t for t in rows(body)}["TIX-002"]["delivery"]["disagrees"] is False
    assert {t["id"]: t for t in rows(body)}["TIX-003"]["delivery"]["disagrees"] is False


# ── criterion 3 ──────────────────────────────────────────────────────────────────────────────

def test_the_facet_counts_are_over_the_whole_repository_and_not_the_page(api29):
    """`test_the_facet_counts_are_over_the_whole_repository_and_not_the_page` — with a page size
    smaller than the ticket count, every facet total counts all matching tickets, not the window's.
    The fixture's page 1 and page 2 **disagree** on which stages appear, so a facet computed over
    the page is visibly wrong.
    """
    page1 = get(api29, "?page=1&page_size=3")
    page2 = get(api29, "?page=2&page_size=3")

    # the page size really is smaller than the ticket count, or nothing below means anything
    assert page1["total"] == 6 and page1["page_size"] == 3
    assert ids(page1) == ["TIX-001", "TIX-002", "TIX-003"]
    assert ids(page2) == ["TIX-004", "TIX-005", "TIX-006"]

    # THE MEMBERS OF THE COLLECTION DISAGREE: the two pages carry different stages, so a facet
    # computed over either one cannot be mistaken for a facet computed over the repository.
    stages1 = {t["stage"]["name"] for t in rows(page1)}
    stages2 = {t["stage"]["name"] for t in rows(page2)}
    assert stages1 == {"SPEC", "IMPL"}, stages1
    assert stages2 == {"GATE", "QA", ""}, stages2
    assert stages1.isdisjoint(stages2 - {""}), (stages1, stages2)

    # every facet total counts ALL matching tickets, not the window's
    for body in (page1, page2):
        # the shape does not move under the reader: every declared stage is a member, in the
        # sequence's order, including the ones nothing has reached (§4.18's rule for E30's
        # delivery row, applied here for the same reason — a row of counts that omits its empty
        # members is a row whose shape changes under the reader)
        assert [f["value"] for f in body["facets"]["stage"]] == SEQUENCE, body["facets"]
        assert facet(body, "stage", "SPEC") == 2, body["facets"]
        assert facet(body, "stage", "TEST") == 0, body["facets"]
        assert facet(body, "stage", "IMPL") == 1, body["facets"]
        assert facet(body, "stage", "GATE") == 1, body["facets"]
        assert facet(body, "stage", "QA") == 1, body["facets"]
        # five of the six tickets have reached a stage; TIX-006 has reached none and is in no
        # bucket, because `stage=` may only name a stage the repository declares
        assert sum(f["count"] for f in body["facets"]["stage"]) == 5, body["facets"]

        assert [f["value"] for f in body["facets"]["provenance"]] == PROVENANCE, body["facets"]
        assert facet(body, "provenance", "unshipped") == 2, body["facets"]
        assert facet(body, "provenance", "done") == 1, body["facets"]
        assert facet(body, "provenance", "not_started") == 3, body["facets"]
        assert facet(body, "provenance", "waiting") == 0, body["facets"]
        assert sum(f["count"] for f in body["facets"]["provenance"]) == 6, body["facets"]

    # and the two pages agree with each other about every facet, which they cannot do if either
    # was computed from its own rows
    assert page1["facets"] == page2["facets"]

    # the count line is the server's too, and it is the window and not the total
    assert (page1["from"], page1["to"], page1["page"], page1["pages"]) == (1, 3, 1, 2), page1
    assert (page2["from"], page2["to"], page2["page"], page2["pages"]) == (4, 6, 2, 2), page2


# ── criterion 4 ──────────────────────────────────────────────────────────────────────────────

def test_filters_compose_and_the_footer_states_what_was_applied(api29):
    """`test_filters_compose_and_the_footer_states_what_was_applied` — stage and provenance
    filters applied together return their intersection, and `total` is the filtered total.
    """
    by_stage = get(api29, "?stage=SPEC")
    by_prov = get(api29, "?provenance=unshipped")
    both = get(api29, "?stage=SPEC&provenance=unshipped")

    # each filter alone selects a set of two, so the intersection is strictly smaller than BOTH —
    # an implementation that ORed them, or that applied only the last parameter it read, differs
    assert ids(by_stage) == ["TIX-001", "TIX-002"], ids(by_stage)
    assert ids(by_prov) == ["TIX-001", "TIX-004"], ids(by_prov)

    # applied together they return their INTERSECTION
    assert ids(both) == ["TIX-001"], ids(both)
    assert set(ids(both)) == set(ids(by_stage)) & set(ids(by_prov))

    # and `total` is the FILTERED total, in every one of the three
    assert by_stage["total"] == 2, by_stage["total"]
    assert by_prov["total"] == 2, by_prov["total"]
    assert both["total"] == 1, both["total"]
    assert both["from"] == 1 and both["to"] == 1 and both["pages"] == 1

    # a group with nothing left in it is not drawn as an empty group
    assert [g["parent"] for g in both["groups"]] == ["REQ-001"], both["groups"]
    assert both["groups"][0]["count"] == 1

    # the footer STATES WHAT WAS APPLIED, and it is the server's sentence and not the URL's
    assert both["applied"] == {"q": "", "provenance": "unshipped", "stage": "SPEC"}, both["applied"]
    assert get(api29)["applied"] == {"q": "", "provenance": "all", "stage": ""}

    # the search box composes with them too — `q` matches id, title, owner and lane
    assert ids(get(api29, "?q=tix-001")) == ["TIX-001"]
    assert ids(get(api29, "?q=Ada")) == ["TIX-001"]
    assert get(api29, "?q=tix-001&provenance=done")["total"] == 0
    assert get(api29, "?q=tix-001")["applied"]["q"] == "tix-001"


# ── criterion 5 · MANDATORY NEGATIVE ─────────────────────────────────────────────────────────

def test_a_filter_value_the_repository_does_not_declare_is_refused(api29):
    """`test_a_filter_value_the_repository_does_not_declare_is_refused` — a stage not in the
    manifest's sequence, and a provenance outside `E10`'s closed set: `400 bad_filter` naming the
    parameter and listing the accepted values. Silently returning everything fails — `GET
    /api/requirements` already refuses this way and the two must not disagree.
    """
    # a stage NOT IN THE MANIFEST'S SEQUENCE
    bad_stage = api29.get("/api/tickets?stage=NOT-A-STAGE")
    assert bad_stage.status_code == 400, f"{bad_stage.status_code} {bad_stage.text[:400]}"
    err = bad_stage.json()["error"]
    assert err["code"] == "bad_filter", err
    # NAMING THE PARAMETER
    assert err["detail"]["parameter"] == "stage", err
    assert err["detail"]["value"] == "NOT-A-STAGE", err
    # AND LISTING THE ACCEPTED VALUES
    assert err["detail"]["accepted"] == SEQUENCE, err
    for name in SEQUENCE:
        assert name in err["message"], err["message"]

    # a provenance OUTSIDE THE CLOSED SET §3.5 gives `delivery.state`
    bad_prov = api29.get("/api/tickets?provenance=shipped-ish")
    assert bad_prov.status_code == 400, f"{bad_prov.status_code} {bad_prov.text[:400]}"
    err = bad_prov.json()["error"]
    assert err["code"] == "bad_filter", err
    assert err["detail"]["parameter"] == "provenance", err
    assert err["detail"]["value"] == "shipped-ish", err
    assert err["detail"]["accepted"] == ["all"] + PROVENANCE, err
    for name in PROVENANCE:
        assert name in err["message"], err["message"]

    # SILENTLY RETURNING EVERYTHING FAILS — neither refusal may carry a body of rows
    for r in (bad_stage, bad_prov):
        assert "groups" not in r.json(), r.text[:400]

    # `GET /api/requirements` ALREADY REFUSES THIS WAY AND THE TWO MUST NOT DISAGREE
    theirs = api29.get("/api/requirements?filter=NOT-A-FILTER")
    assert theirs.status_code == 400, theirs.text[:400]
    assert theirs.json()["error"]["code"] == bad_stage.json()["error"]["code"] == "bad_filter"
    assert set(theirs.json()["error"]) == set(bad_stage.json()["error"]), (
        theirs.json()["error"], bad_stage.json()["error"])

    # the accepted values really are the repository's and not a list the tool carries: a stage
    # this repository DOES declare is served, and one every other repository might is not
    assert api29.get("/api/tickets?stage=QA").status_code == 200
    assert api29.get("/api/tickets?stage=MERGE").status_code == 400
    assert api29.get("/api/tickets?stage=").status_code == 200
    assert api29.get("/api/tickets?provenance=all").status_code == 200


# ── criterion 6 · MANDATORY NEGATIVE ─────────────────────────────────────────────────────────

def test_a_page_beyond_the_last_is_an_empty_window_and_not_an_error(api29):
    """`test_a_page_beyond_the_last_is_an_empty_window_and_not_an_error` — `200` with empty
    `tickets` and the true `total`. The `docs.tickets` directories absent: `200`, empty, with
    `warnings:[{code:"path_missing",path}]`.
    """
    beyond = api29.get("/api/tickets?page=99&page_size=3")

    # `200` — never a 404: a stale page number in a URL is not a missing thing
    assert beyond.status_code == 200, f"{beyond.status_code} {beyond.text[:400]}"
    body = beyond.json()

    # with EMPTY `tickets` — in every group, and the groups themselves are still there
    assert [g["parent"] for g in body["groups"]] == ["REQ-001", "REQ-002", "standalone"]
    for g in body["groups"]:
        assert g["tickets"] == [], g
    assert rows(body) == []

    # AND THE TRUE `total` — the count that exists, not the window's zero
    assert body["total"] == 6, body["total"]
    assert body["page"] == 99 and body["pages"] == 2, body
    assert (body["from"], body["to"]) == (0, 0), body

    # the group counts stay true as well, or the header describes a set that is not there
    assert [g["count"] for g in body["groups"]] == [3, 1, 2], body["groups"]


def test_the_tickets_directory_absent_is_an_empty_answer_with_a_warning(repo, gitrepo, serve):
    """`test_a_page_beyond_the_last_is_an_empty_window_and_not_an_error`, second half — The
    `docs.tickets` directories absent: `200`, empty, with `warnings:[{code:"path_missing",path}]`.
    """
    repo.manifest(manifest())
    repo.write(f"{REQ_DIR}/REQ-001-the-first.md",
               doc({"id": "REQ-001", "title": "The first requirement", "status": "draft"}))
    repo.mkdir(RPT_DIR)
    repo.write("beta/.keep", "")
    gitrepo.init()
    gitrepo.commit("a repository with no tickets directory at all")
    assert not (repo.root / TKT_DIR).exists()

    srv = serve(repo.root, FACTORY_DELIVERY_POLL_MS="200")
    r = srv.get("/api/tickets")

    # `200`
    assert r.status_code == 200, f"{r.status_code} {r.text[:400]}"
    body = r.json()

    # EMPTY
    assert body["groups"] == [], body["groups"]
    assert body["total"] == 0 and body["pages"] == 1, body
    assert (body["from"], body["to"]) == (0, 0), body

    # WITH `warnings:[{code:"path_missing",path}]`, naming the path a person edits
    missing = [w for w in body.get("warnings", []) if w["code"] == "path_missing"]
    assert missing, body.get("warnings")
    assert any(w["path"].endswith(TKT_DIR) for w in missing), missing

    # an empty answer is not an error, and it is not a filter refusal either
    assert "error" not in body
    # the facet row keeps its shape and reads zero — a shape that collapses when the repository is
    # empty is a shape the reader cannot rely on
    assert [f["value"] for f in body["facets"]["stage"]] == SEQUENCE, body["facets"]
    assert [f["value"] for f in body["facets"]["provenance"]] == PROVENANCE, body["facets"]
    assert sum(f["count"] for f in body["facets"]["stage"]) == 0, body["facets"]
    assert sum(f["count"] for f in body["facets"]["provenance"]) == 0, body["facets"]


# ── criterion 7 ──────────────────────────────────────────────────────────────────────────────

def test_the_response_validates_against_http_schema_json(api29):
    """`test_the_response_validates_against_http_schema_json` — through
    `test_schema_consumers.py`.

    `test_schema_consumers.py` forces the CLIENT half — the workspace whose `src/types/api.ts` is
    generated from this file must build and pass its own gate. That is a different question from
    whether the SERVER's live body satisfies the node, and this file owns the second one: the
    browser gate validates it against the same schema, and a red gate is a better answer than a
    blank screen.
    """
    validator = jsonschema.Draft202012Validator(E29_SCHEMA)
    for query in ("", "?page=2&page_size=3", "?page=99&page_size=3",
                  "?stage=SPEC&provenance=unshipped", "?q=nothing-matches-this"):
        validator.validate(get(api29, query))

    body = get(api29)
    row = {t["id"]: t for t in rows(body)}

    # the members the schema declares required really are populated, not merely present
    assert row["TIX-001"]["owner"] == {"name": "Ada Lovelace", "source": "commits"}, \
        row["TIX-001"].get("owner")
    assert row["TIX-002"]["owner"] == {"name": "bob", "source": "document", "value": "bob"}, \
        row["TIX-002"].get("owner")
    # A TICKET WITH NEITHER HAS NO OWNER, and the member is ABSENT rather than an empty string
    assert "owner" not in row["TIX-006"], row["TIX-006"]

    # `cost_usd` is the sum over the ticket's runs, and a ticket with no run reports 0, not null
    assert row["TIX-001"]["cost_usd"] == 1.0, row["TIX-001"]["cost_usd"]
    assert row["TIX-006"]["cost_usd"] == 0, row["TIX-006"]["cost_usd"]
    assert row["TIX-006"]["cost_usd"] is not None

    # every row carries the repository's own extra fields, passed through untouched
    assert {"label": "contract", "value": "wire-v1"} in row["TIX-001"]["extras"]
    assert row["TIX-003"]["extras"] == []

    # evidence is beside the row, not merged into it (§3.4)
    assert row["TIX-003"]["evidence"] == {"verdict": "claimed_proven", "run_id": "run-0004"}
    assert row["TIX-006"]["evidence"] == {"verdict": "nothing_claimed"}

    # and the workspace is the document's own word
    assert [row[i]["workspace"] for i in ORDER] == \
        ["alpha", "alpha", "beta", "alpha", "alpha", "beta"]


def test_a_page_size_above_the_maximum_is_clamped_and_says_so(api29):
    """SPEC.md §5 E29 — `page_size` above the maximum is clamped and the response says so with
    `warnings:[{code:"clamped",field:"page_size"}]`; refusing would be a `400` a person cannot
    act on from a link somebody sent them.
    """
    body = get(api29, "?page_size=5000")
    assert body["page_size"] == 200, body["page_size"]
    assert {"code": "clamped", "field": "page_size"} in [
        {k: w[k] for k in ("code", "field") if k in w} for w in body.get("warnings", [])
    ], body.get("warnings")
    assert body["total"] == 6 and len(ids(body)) == 6

    # and the ordinary case carries no such warning
    assert not [w for w in get(api29).get("warnings", []) if w["code"] == "clamped"]
