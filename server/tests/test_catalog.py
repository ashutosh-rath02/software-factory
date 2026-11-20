"""SRV-FEAT-004 — the repository's documents are the state, read straight from disk.

Every case builds its own synthetic repository and points a server at it. The document
directories here are deliberately **not** named `docs/epics`, `docs/tickets`, `docs/reports`: a
catalog that only works when the directories are called what this repository calls them is a
catalog that has learned a repository, which is the one thing `SPEC.md` §10 forbids it.
"""

import collections
import json
import pytest
import time


REQ_DIR = "product/requirements"
TKT_DIR = "product/work-items"
RPT_DIR = "product/deliveries"


def manifest_for(workspaces=("alpha", "beta"), ticket_fields=None, epics=REQ_DIR, stages=None):
    m = {
        "factory": "0.1",
        "workspaces": {name: {"path": "."} for name in workspaces},
        "docs": {"epics": epics, "tickets": TKT_DIR, "reports": RPT_DIR},
    }
    if stages is not None:
        m["stages"] = stages
    if ticket_fields is not None:
        m["display"] = {"ticket_fields": ticket_fields}
    return m


def doc(front, body):
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


def ok(srv, path):
    r = srv.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"
    return r.json()


# ── F2 ────────────────────────────────────────────────────────────────────────────────────────

def test_requirements_filter(repo, serve):
    """F2 — filtering and searching happen on the server, so the count cannot disagree.

    The requirement that "needs a person" needs one for the only reason a document can give:
    a delivery report that claims the work is done and cites no run (`SPEC.md` §3.4, F28).
    """
    repo.write(f"{REQ_DIR}/REQ-001-cold-start.md",
               doc({"id": "REQ-001", "title": "Cold start is fast", "status": "draft"}, "One."))
    repo.write(f"{REQ_DIR}/REQ-002-rollback.md",
               doc({"id": "REQ-002", "title": "Rollback protects the fleet", "status": "draft"},
                   "Two."))
    repo.write(f"{REQ_DIR}/REQ-003-search.md",
               doc({"id": "REQ-003", "title": "Search finds a document", "status": "draft"},
                   "Three."))

    repo.write(f"{TKT_DIR}/TKT-1.md",
               doc({"id": "TKT-1", "parent": "REQ-002", "workspace": "alpha",
                    "title": "Write the rollback path", "status": "todo"}, "Body."))
    repo.write(f"{TKT_DIR}/TKT-2.md",
               doc({"id": "TKT-2", "parent": "REQ-001", "workspace": "alpha",
                    "title": "Measure the cold start", "status": "todo"}, "Body."))
    # claims done, cites no run → claimed_unproven → REQ-002 needs a person
    repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — done\n\nNo run is cited anywhere here.\n")

    srv = serve(repo.manifest(manifest_for()))

    everything = ok(srv, "/api/requirements")
    assert [r["id"] for r in everything["requirements"]] == ["REQ-001", "REQ-002", "REQ-003"], \
        everything
    assert everything["total"] == 3, everything

    needs = ok(srv, "/api/requirements?filter=needs")
    assert [r["id"] for r in needs["requirements"]] == ["REQ-002"], needs
    assert needs["total"] == 1, "the count line and the rows must be the same number"
    assert needs["requirements"][0]["needs_you"]["count"] == 1, needs

    for query in ("rollback", "ROLLBACK", "REQ-002", "req-002"):
        hit = ok(srv, f"/api/requirements?q={query}")
        assert [r["id"] for r in hit["requirements"]] == ["REQ-002"], (query, hit)
        assert hit["total"] == 1, (query, hit)

    both = ok(srv, "/api/requirements?q=fleet&filter=needs")
    assert [r["id"] for r in both["requirements"]] == ["REQ-002"], both

    none = ok(srv, "/api/requirements?q=nothing-matches-this")
    assert none["requirements"] == [] and none["total"] == 0, none


# ── F3 ────────────────────────────────────────────────────────────────────────────────────────

def test_requirement_groups(repo, serve):
    """F3 — one group per workspace, each with its own sequence; nothing disappears."""
    repo.write(f"{REQ_DIR}/REQ-100.md",
               doc({"id": "REQ-100", "title": "Two workspaces, two processes", "status": "draft"},
                   "The summary paragraph.\n\nA second paragraph."))

    repo.write(f"{TKT_DIR}/TKT-10.md",
               doc({"id": "TKT-10", "parent": "REQ-100", "workspace": "alpha",
                    "title": "Alpha work", "stages": "SPEC IMPL GATE"}, "a"))
    repo.write(f"{TKT_DIR}/TKT-11.md",
               doc({"id": "TKT-11", "parent": "REQ-100", "workspace": "beta",
                    "title": "Beta work", "stages": "DESIGN BUILD SHIP VERIFY"}, "b"))
    repo.write(f"{TKT_DIR}/TKT-12.md",
               doc({"id": "TKT-12", "parent": "REQ-100", "workspace": "gamma",
                    "title": "Work in a workspace nobody declared"}, "c"))

    srv = serve(repo.manifest(manifest_for(workspaces=("alpha", "beta"),
                                           stages=["ONE", "TWO"])))

    body = ok(srv, "/api/requirements/REQ-100")
    assert body["id"] == "REQ-100" and body["title"] == "Two workspaces, two processes", body
    assert body["summary"] == "The summary paragraph.", body["summary"]

    groups = {g["workspace"]: g for g in body["groups"]}
    assert [g["workspace"] for g in body["groups"]] == ["alpha", "beta", "unassigned"], body

    assert groups["alpha"]["stage_sequence"] == ["SPEC", "IMPL", "GATE"], groups["alpha"]
    assert groups["alpha"]["count"] == 1
    assert groups["alpha"]["tickets"][0]["stage"]["total"] == 3, groups["alpha"]

    assert groups["beta"]["stage_sequence"] == ["DESIGN", "BUILD", "SHIP", "VERIFY"], groups["beta"]
    assert groups["beta"]["tickets"][0]["stage"]["total"] == 4, groups["beta"]

    # declared nowhere, so it falls back to the manifest's sequence — and it is still on screen
    assert [t["id"] for t in groups["unassigned"]["tickets"]] == ["TKT-12"], groups["unassigned"]
    assert groups["unassigned"]["stage_sequence"] == ["ONE", "TWO"], groups["unassigned"]

    assert srv.get("/api/requirements/REQ-404").status_code == 404
    missing = srv.get("/api/requirements/REQ-404").json()
    assert missing["error"]["code"] == "not_found" and missing["error"]["detail"]["path"], missing


# ── F4 ────────────────────────────────────────────────────────────────────────────────────────

def test_ticket_extras(repo, serve):
    """F4 — the repository's own vocabulary reaches the screen; nothing else does."""
    repo.write(f"{TKT_DIR}/TKT-20.md",
               doc({"id": "TKT-20", "title": "Carries three extra fields", "workspace": "alpha",
                    "status": "todo", "alpha": "A-value", "beta": "B-value",
                    "gamma": "G-value"}, "# Heading\n\nThe body."))
    repo.write(f"{TKT_DIR}/TKT-21.md",
               doc({"id": "TKT-21", "title": "Carries only one of them", "workspace": "alpha",
                    "alpha": "A-only"}, "Body."))

    srv = serve(repo.manifest(manifest_for(ticket_fields=["alpha", "beta"])))

    body = ok(srv, "/api/tickets/TKT-20")
    assert body["extras"] == [{"label": "alpha", "value": "A-value"},
                              {"label": "beta", "value": "B-value"}], body["extras"]
    assert "gamma" not in json.dumps(body), "a field nobody asked for reached the response"
    assert "G-value" not in json.dumps(body), body
    assert body["body_markdown"].strip().startswith("# Heading"), body["body_markdown"]
    assert body["workspace"] == "alpha" and body["status"] == "todo", body

    # a declared field the document does not carry is absent, not an empty tag
    only = ok(srv, "/api/tickets/TKT-21")
    assert only["extras"] == [{"label": "alpha", "value": "A-only"}], only["extras"]

    gone = srv.get("/api/tickets/TKT-999")
    assert gone.status_code == 404, gone.text
    assert gone.json()["error"]["detail"]["path"].endswith(TKT_DIR), gone.text


# ── F5 ────────────────────────────────────────────────────────────────────────────────────────

def test_report_absent_then_present(repo, serve):
    """F5 — the absence is the normal case, and the arrival needs no poll."""
    repo.write(f"{TKT_DIR}/TKT-30.md",
               doc({"id": "TKT-30", "title": "Not delivered yet", "workspace": "alpha"}, "Body."))
    repo.mkdir(f"{RPT_DIR}")
    repo.mkdir(".factory/runs/run-2f9c1b")

    srv = serve(repo.manifest(manifest_for()))
    sock = srv.ws()
    # Deltas are routed by scope (SRV-FEAT-005). A connection that subscribes to nothing
    # correctly receives nothing, so this one says which scope it is watching.
    sock.send({"m": "subscribe", "scope": {"requirements": True}})
    sock.wait_for("snapshot")
    try:
        absent = srv.get("/api/tickets/TKT-30/report")
        assert absent.status_code == 404, absent.text
        err = absent.json()["error"]
        assert err["code"] == "not_found", err
        assert err["detail"]["path"].endswith("TKT-30-report.md"), err

        repo.write(f"{RPT_DIR}/TKT-30-report.md",
                   "# TKT-30 — delivered\n\nEvidence: run `run-2f9c1b` did the work.\n")

        frame = sock.wait_for("catalog.changed")
        assert frame["kind"] == "report" and frame["change"] == "created", frame
        assert frame["id"] == "TKT-30", frame
        assert frame["path"].endswith("TKT-30-report.md"), frame

        present = ok(srv, "/api/tickets/TKT-30/report")
        assert present["evidence"]["verdict"] == "claimed_proven", present
        assert present["evidence"]["run_id"] == "run-2f9c1b", present
        assert "did the work" in present["body_markdown"], present

        # and the ticket agrees with its own report
        ticket = ok(srv, "/api/tickets/TKT-30")
        assert ticket["report"]["exists"] is True, ticket
        assert ticket["state"]["evidence"] == "claimed_proven", ticket
    finally:
        sock.close()


# ── F29 ───────────────────────────────────────────────────────────────────────────────────────

def test_no_cache_disagrees_with_disk(repo, serve):
    """F29 — no copy of a document outlives the file it was read from."""
    path = repo.write(f"{TKT_DIR}/TKT-40.md",
                      doc({"id": "TKT-40", "title": "First title", "workspace": "alpha",
                           "status": "todo"}, "The first body."))

    srv = serve(repo.manifest(manifest_for()))
    sock = srv.ws()
    # Deltas are routed by scope (SRV-FEAT-005). A connection that subscribes to nothing
    # correctly receives nothing, so this one says which scope it is watching.
    sock.send({"m": "subscribe", "scope": {"requirements": True}})
    sock.wait_for("snapshot")
    try:
        first = ok(srv, "/api/tickets/TKT-40")
        assert first["title"] == "First title", first
        assert "The first body." in first["body_markdown"], first

        path.write_text(doc({"id": "TKT-40", "title": "Second title", "workspace": "beta",
                             "status": "in-progress"},
                            "The second body, which is a different length entirely."))

        frame = sock.wait_for("catalog.changed")
        assert frame["kind"] == "ticket" and frame["change"] == "modified", frame
        assert frame["id"] == "TKT-40", frame

        second = ok(srv, "/api/tickets/TKT-40")
        assert second["title"] == "Second title", second
        assert second["workspace"] == "beta" and second["status"] == "in-progress", second
        assert "The second body" in second["body_markdown"], second
        assert "First title" not in json.dumps(second), "a stale value survived the edit"
        assert "The first body." not in json.dumps(second), "a stale body survived the edit"

        path.unlink()
        gone = sock.wait_for("catalog.changed")
        assert gone["change"] == "deleted" and gone["id"] == "TKT-40", gone
        assert srv.get("/api/tickets/TKT-40").status_code == 404
    finally:
        sock.close()


# ── §3.4 ──────────────────────────────────────────────────────────────────────────────────────

def test_evidence_verdict_reads_the_run_id(repo, serve):
    """§3.4 — the tool matches a string it assigned, and forms no other opinion."""
    for tid in ("TKT-50", "TKT-51"):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "title": f"{tid} work", "workspace": "alpha"}, "Body."))
    repo.mkdir(".factory/runs/run-7f3a2e")

    repo.write(f"{RPT_DIR}/TKT-50-report.md",
               "# TKT-50 — PASS\n\nGate run: run-7f3a2e, quoted output below.\n")
    # every word of a delivery report, and not one run id
    repo.write(f"{RPT_DIR}/TKT-51-report.md",
               "# TKT-51 — PASS\n\nAll assertions pass. Ran the gate. Green.\n")

    srv = serve(repo.manifest(manifest_for()))

    proven = ok(srv, "/api/tickets/TKT-50/report")
    assert proven["evidence"] == {"verdict": "claimed_proven", "run_id": "run-7f3a2e"}, proven

    unproven = ok(srv, "/api/tickets/TKT-51/report")
    assert unproven["evidence"]["verdict"] == "claimed_unproven", unproven
    assert "run_id" not in unproven["evidence"], unproven

    # the alarm reaches the list without anybody opening the report
    tickets = {t["id"]: t for t in [ok(srv, "/api/tickets/TKT-50"), ok(srv, "/api/tickets/TKT-51")]}
    assert tickets["TKT-50"]["state"]["evidence"] == "claimed_proven", tickets["TKT-50"]
    assert tickets["TKT-51"]["state"]["evidence"] == "claimed_unproven", tickets["TKT-51"]


# ── E3's missing directory ────────────────────────────────────────────────────────────────────

def test_missing_docs_directory_is_not_an_error(repo, serve):
    """A manifest pointing at the wrong directory is a manifest mistake, and the screen says which."""
    repo.mkdir(TKT_DIR)
    repo.mkdir(RPT_DIR)
    srv = serve(repo.manifest(manifest_for(epics="product/requirements-that-are-not-there")))

    body = ok(srv, "/api/requirements")
    assert body["requirements"] == [] and body["total"] == 0, body
    codes = [(w["code"], w["path"]) for w in body["warnings"]]
    assert any(c == "path_missing" for c, _ in codes), body["warnings"]
    assert any(p.endswith("product/requirements-that-are-not-there") for _, p in codes), codes


# ── Negative, mandatory ───────────────────────────────────────────────────────────────────────

def test_one_malformed_document_does_not_blank_the_list(repo, serve):
    """One unreadable file may not take out a screen.

    This is the case that decides whether the parser is defensive or merely optimistic: the
    malformed document keeps the keys that did parse, keeps its body, and stays in every list
    it belongs to — and nothing anywhere returns 500.
    """
    repo.write(f"{REQ_DIR}/REQ-200.md",
               doc({"id": "REQ-200", "title": "Has a broken child", "status": "draft"}, "Summary."))

    repo.write(f"{TKT_DIR}/TKT-60.md",
               doc({"id": "TKT-60", "parent": "REQ-200", "workspace": "alpha",
                    "title": "Fine"}, "a"))
    repo.write(f"{TKT_DIR}/TKT-61.md",
               "---\n"
               "id: TKT-61\n"
               "parent: REQ-200\n"
               "workspace: alpha\n"
               "title: Malformed but still readable\n"
               "this line is not a key and not a list item\n"
               "---\n"
               "\n# Broken frontmatter\n\nThe body markdown must be intact.\n")
    repo.write(f"{TKT_DIR}/TKT-62.md",
               doc({"id": "TKT-62", "parent": "REQ-200", "workspace": "alpha",
                    "title": "Also fine"}, "c"))

    srv = serve(repo.manifest(manifest_for()))

    broken = srv.get("/api/tickets/TKT-61")
    assert broken.status_code == 200, broken.text
    body = broken.json()
    assert body.get("parse_error"), "a document that would not parse reported no parse_error"
    assert "The body markdown must be intact." in body["body_markdown"], body
    assert body["title"] == "Malformed but still readable", "keys that parsed were thrown away"

    group = ok(srv, "/api/requirements/REQ-200")
    ids = [t["id"] for g in group["groups"] for t in g["tickets"]]
    assert ids == ["TKT-60", "TKT-61", "TKT-62"], ids

    listing = ok(srv, "/api/requirements")
    row = [r for r in listing["requirements"] if r["id"] == "REQ-200"]
    assert row and row[0]["ticket_count"] == 3, listing

    for path in ("/api/requirements", "/api/requirements?filter=needs",
                 "/api/requirements/REQ-200", "/api/tickets/TKT-61",
                 "/api/tickets/TKT-61/report", "/api/manifest", "/api/health"):
        assert srv.get(path).status_code != 500, f"{path} returned 500"


# ── Attacking the green build: what §2 tells QA to hunt ───────────────────────────────────────

def test_a_document_outside_the_repository_is_not_served(repo, serve, tmp_path_factory):
    """A path that leaves the repository is refused, wherever the link was planted.

    `SRV-FEAT-003` proved no *declared* path escapes. A symlink dropped inside a declared
    directory is the same attack one level down, and it is the one this module could have.
    """
    elsewhere = tmp_path_factory.mktemp("outside") / "outside.md"
    elsewhere.write_text("---\nid: LEAKED\ntitle: Not this repository's\n---\n\nSECRET-MARKER\n")

    repo.write(f"{TKT_DIR}/TKT-70.md",
               doc({"id": "TKT-70", "title": "An honest ticket", "workspace": "alpha"}, "Body."))
    (repo.root / TKT_DIR / "leak.md").symlink_to(elsewhere)

    srv = serve(repo.manifest(manifest_for()))

    listed = ok(srv, "/api/requirements")
    assert "SECRET-MARKER" not in json.dumps(listed), listed
    assert srv.get("/api/tickets/LEAKED").status_code == 404
    assert srv.get("/api/tickets/leak").status_code == 404

    honest = ok(srv, "/api/tickets/TKT-70")
    assert honest["title"] == "An honest ticket", honest


def test_a_ticket_id_cannot_walk_out_of_the_reports_directory(repo, serve):
    """The one place an id becomes a path. It stays inside the directory it was given."""
    repo.write("secret-report.md", "# not a delivery report\n\nSECRET-MARKER\n")
    repo.write(f"{TKT_DIR}/TKT-71.md",
               doc({"id": "TKT-71", "title": "Fine", "workspace": "alpha"}, "Body."))
    repo.mkdir(RPT_DIR)

    srv = serve(repo.manifest(manifest_for()))

    for hostile in ("..%2F..%2Fsecret", "../../secret", "..", ".", "%2e%2e%2fsecret"):
        r = srv.get(f"/api/tickets/{hostile}/report")
        assert r.status_code == 404, (hostile, r.status_code, r.text)
        assert "SECRET-MARKER" not in r.text, (hostile, r.text)


def test_a_duplicate_key_does_not_become_two_values(repo, serve):
    repo.write(f"{TKT_DIR}/TKT-72.md",
               "---\nid: TKT-72\ntitle: The first one\ntitle: The second one\n"
               "workspace: alpha\nalpha: one\n---\n\nBody.\n")
    srv = serve(repo.manifest(manifest_for(ticket_fields=["alpha"])))

    body = ok(srv, "/api/tickets/TKT-72")
    assert body["title"] == "The first one", body
    assert body["extras"] == [{"label": "alpha", "value": "one"}], body["extras"]


def test_an_unknown_filter_is_refused_not_read_as_all(repo, serve):
    repo.write(f"{REQ_DIR}/REQ-300.md", doc({"id": "REQ-300", "title": "One"}, "Summary."))
    srv = serve(repo.manifest(manifest_for()))

    bad = srv.get("/api/requirements?filter=everything")
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["code"] == "bad_filter", bad.text
    assert srv.get("/api/requirements?filter=all").status_code == 200

    # SRV-FEAT-165 — `done` joins the set, and adding a value to an enum must not open the enum:
    # `finished` is the near-miss a person types, and it is still refused.
    assert srv.get("/api/requirements?filter=done").status_code == 200
    near = srv.get("/api/requirements?filter=finished")
    assert near.status_code == 400, near.text
    assert near.json()["error"]["code"] == "bad_filter", near.text


def test_a_document_full_of_json_metacharacters_is_still_json(repo, serve):
    """A title is a string from a file nobody validated. It goes on the wire escaped or not at all."""
    repo.write(f"{REQ_DIR}/REQ-301.md",
               '---\nid: REQ-301\ntitle: He said "no" about C:\\path\n---\n\n'
               'A body with\ta tab, a "quote" and a backslash \\ in it.\n')
    srv = serve(repo.manifest(manifest_for()))

    body = ok(srv, "/api/requirements")          # ok() parses the JSON: that is the assertion
    assert body["total"] == 1, body
    row = body["requirements"][0]
    assert row["title"] == 'He said "no" about C:\\path', row["title"]

    detail = ok(srv, "/api/requirements/REQ-301")
    assert '"quote"' in detail["body_markdown"] and "\t" in detail["body_markdown"], detail


# ── SRV-FIX-021 · a ticket names its workspace ────────────────────────────────────────────────

def test_a_ticket_names_the_workspace_it_belongs_to(repo, serve):
    """The agreement SRV-FIX-021 makes: `workspace:` in a ticket's frontmatter is the key, and
    it must name a workspace the manifest declares.

    The negative is the whole point. A repository that calls the concept something else — `repo`
    here, because that is what this one called it — does not get to teach the tool a second word:
    the ticket lands in `unassigned` and the other word reaches the screen only as an extra.
    """
    repo.write(f"{REQ_DIR}/REQ-200.md",
               doc({"id": "REQ-200", "title": "Three workspaces, three processes"}, "Summary."))
    repo.write(f"{REQ_DIR}/REQ-201.md",
               doc({"id": "REQ-201", "title": "One ticket using the other word"}, "Summary."))

    repo.write(f"{TKT_DIR}/TKT-200.md",
               doc({"id": "TKT-200", "parent": "REQ-200", "workspace": "alpha",
                    "title": "Alpha work", "stages": "SPEC TEST IMPL FLASH GATE"}, "a"))
    repo.write(f"{TKT_DIR}/TKT-201.md",
               doc({"id": "TKT-201", "parent": "REQ-200", "workspace": "beta",
                    "title": "Beta work", "stages": "SPEC TEST IMPL GATE"}, "b"))
    repo.write(f"{TKT_DIR}/TKT-202.md",
               doc({"id": "TKT-202", "parent": "REQ-200", "workspace": "gamma",
                    "title": "Gamma work", "stages": "SPEC IMPL GATE"}, "c"))
    # the old vocabulary, on its own — and `workspace` is nowhere in this document
    repo.write(f"{TKT_DIR}/TKT-203.md",
               doc({"id": "TKT-203", "parent": "REQ-201", "repo": "alpha",
                    "title": "Names its repository, not its workspace"}, "d"))

    srv = serve(repo.manifest(manifest_for(workspaces=("alpha", "beta", "gamma"),
                                           ticket_fields=["repo"],
                                           stages=["ONE", "TWO"])))

    body = ok(srv, "/api/requirements/REQ-200")
    names = [g["workspace"] for g in body["groups"]]
    assert names == ["alpha", "beta", "gamma"], body
    assert "unassigned" not in names, "a declared workspace must not fall into the leftovers group"

    groups = {g["workspace"]: g for g in body["groups"]}
    assert groups["alpha"]["stage_sequence"] == ["SPEC", "TEST", "IMPL", "FLASH", "GATE"], groups
    assert groups["beta"]["stage_sequence"] == ["SPEC", "TEST", "IMPL", "GATE"], groups
    assert groups["gamma"]["stage_sequence"] == ["SPEC", "IMPL", "GATE"], groups
    assert [g["count"] for g in body["groups"]] == [1, 1, 1], body
    assert groups["alpha"]["tickets"][0]["stage"]["total"] == 5, groups["alpha"]
    assert groups["gamma"]["tickets"][0]["stage"]["total"] == 3, groups["gamma"]

    # NEGATIVE — `repo` is not a second name for `workspace`, and the tool has not learned it
    other = ok(srv, "/api/requirements/REQ-201")
    assert [g["workspace"] for g in other["groups"]] == ["unassigned"], other
    assert [t["id"] for t in other["groups"][0]["tickets"]] == ["TKT-203"], other

    ticket = ok(srv, "/api/tickets/TKT-203")
    assert ticket["workspace"] == "", "a ticket with no `workspace` key has no workspace"
    assert ticket["extras"] == [{"label": "repo", "value": "alpha"}], ticket["extras"]


# ── WEB-FEAT-041 · a ticket whose parent is nobody's is still on a screen ─────────────────────

def standalone_repo(repo, extra=()):
    """A repository with two real requirements and three orphans of all three kinds."""
    repo.write(f"{REQ_DIR}/REQ-400.md",
               doc({"id": "REQ-400", "title": "A requirement with a child"}, "Summary."))
    repo.write(f"{REQ_DIR}/REQ-401.md",
               doc({"id": "REQ-401", "title": "A requirement with none"}, "Summary."))

    repo.write(f"{TKT_DIR}/TKT-400.md",
               doc({"id": "TKT-400", "parent": "REQ-400", "workspace": "alpha",
                    "title": "A child of a real requirement"}, "a"))
    # the three kinds of orphan §1 names, which the tool must not distinguish between
    repo.write(f"{TKT_DIR}/TKT-401.md",
               doc({"id": "TKT-401", "parent": "standalone", "workspace": "alpha",
                    "title": "Says standalone"}, "b"))
    repo.write(f"{TKT_DIR}/TKT-402.md",
               doc({"id": "TKT-402", "workspace": "beta", "title": "Names no parent at all"}, "c"))
    repo.write(f"{TKT_DIR}/TKT-403.md",
               doc({"id": "TKT-403", "parent": "REQ-999", "workspace": "gamma",
                    "title": "Names a requirement that is not there"}, "d"))
    for rel, text in extra:
        repo.write(rel, text)
    return repo


def test_standalone_tickets_appear_in_requirements_list(repo, serve):
    """E3 — orphan tickets are one synthetic row at the END of the list, and it counts.

    Invisible was the defect: before this, `renderRequirements` iterated the epics and a ticket
    whose parent was not one of them was emitted by nothing at all.
    """
    srv = serve(standalone_repo(repo).manifest(manifest_for(workspaces=("alpha", "beta"))))

    body = ok(srv, "/api/requirements?filter=all")
    ids = [r["id"] for r in body["requirements"]]
    assert ids == ["REQ-400", "REQ-401", "standalone"], body
    assert body["total"] == 3, "the count line must count the synthetic row it renders"

    row = body["requirements"][-1]
    assert row["title"] == "Standalone", row
    assert row["ticket_count"] == 3, row
    assert row["status"] == "", "a synthetic row has no document and so no declared status"
    # the same aggregation a real row gets: manifest order, and the undeclared workspace last
    assert row["workspaces"] == ["alpha", "beta", "unassigned"], row
    assert row["needs_you"] == {"count": 0, "label": ""}, row
    assert row["delivery"]["state"] and row["state"]["evidence"] == "nothing_claimed", row

    # searchable like any other row, and it disappears when nothing matches
    hit = ok(srv, "/api/requirements?q=standalone")
    assert [r["id"] for r in hit["requirements"]] == ["standalone"], hit
    assert hit["total"] == 1, hit
    none = ok(srv, "/api/requirements?q=nothing-matches-this")
    assert none["requirements"] == [] and none["total"] == 0, none


def test_requirement_standalone_returns_orphan_tickets(repo, serve):
    """E4 — `standalone` answers in the same shape as any requirement, grouped by workspace."""
    srv = serve(standalone_repo(repo).manifest(manifest_for(workspaces=("alpha", "beta"),
                                                           stages=["ONE", "TWO"])))

    body = ok(srv, "/api/requirements/standalone")
    assert body["id"] == "standalone" and body["title"] == "Standalone", body
    assert body["summary"], "the screen prints this line, so it may not be empty"
    assert body["needs_you"]["total"] == 3, body

    groups = {g["workspace"]: g for g in body["groups"]}
    assert [g["workspace"] for g in body["groups"]] == ["alpha", "beta", "unassigned"], body
    assert [t["id"] for t in groups["alpha"]["tickets"]] == ["TKT-401"], groups["alpha"]
    assert [t["id"] for t in groups["beta"]["tickets"]] == ["TKT-402"], groups["beta"]
    assert [t["id"] for t in groups["unassigned"]["tickets"]] == ["TKT-403"], groups["unassigned"]
    assert groups["alpha"]["stage_sequence"] == ["ONE", "TWO"], groups["alpha"]

    # and it took nothing from the requirement that has real children
    real = ok(srv, "/api/requirements/REQ-400")
    assert [t["id"] for g in real["groups"] for t in g["tickets"]] == ["TKT-400"], real


def test_a_requirement_id_nobody_wrote_is_still_404(repo, serve):
    """NEGATIVE — the synthetic id is one name, not a licence to answer for every name.

    Both halves matter: an unknown id stays `404` rather than becoming a silent empty group, and
    `standalone` itself is `404` in a repository that has no orphan for it — a group with nothing
    in it does not exist, which is the same rule that keeps the row off E3.
    """
    srv = serve(standalone_repo(repo).manifest(manifest_for(workspaces=("alpha", "beta"))))

    for unknown in ("nonexistent-epic-id", "REQ-999", "Standalone", "standalone-x"):
        r = srv.get(f"/api/requirements/{unknown}")
        assert r.status_code == 404, (unknown, r.status_code, r.text)
        assert r.json()["error"]["code"] == "not_found", (unknown, r.text)
        assert r.json()["error"]["detail"]["path"], (unknown, r.text)

    # a repository whose every ticket has a real parent: no row, and no group either
    repo.write(f"{TKT_DIR}/TKT-401.md",
               doc({"id": "TKT-401", "parent": "REQ-400", "workspace": "alpha",
                    "title": "Adopted"}, "b"))
    repo.write(f"{TKT_DIR}/TKT-402.md",
               doc({"id": "TKT-402", "parent": "REQ-400", "workspace": "beta",
                    "title": "Adopted"}, "c"))
    repo.write(f"{TKT_DIR}/TKT-403.md",
               doc({"id": "TKT-403", "parent": "REQ-401", "workspace": "alpha",
                    "title": "Adopted"}, "d"))

    body = ok(srv, "/api/requirements")
    assert [r["id"] for r in body["requirements"]] == ["REQ-400", "REQ-401"], body
    assert body["total"] == 2, body
    assert srv.get("/api/requirements/standalone").status_code == 404


def test_a_real_requirement_called_standalone_owns_the_name(repo, serve):
    """A repository that declares the id owns it: its document wins, and there is one row."""
    repo.write(f"{REQ_DIR}/standalone.md",
               doc({"id": "standalone", "title": "The repository's own standalone bucket",
                    "status": "draft"}, "Written by a person."))
    repo.write(f"{TKT_DIR}/TKT-410.md",
               doc({"id": "TKT-410", "parent": "standalone", "workspace": "alpha",
                    "title": "A child of the real document"}, "a"))

    srv = serve(repo.manifest(manifest_for(workspaces=("alpha", "beta"))))

    body = ok(srv, "/api/requirements")
    assert [r["id"] for r in body["requirements"]] == ["standalone"], body
    row = body["requirements"][0]
    assert row["title"] == "The repository's own standalone bucket", row
    assert row["status"] == "draft" and row["path"].endswith("standalone.md"), row
    assert row["ticket_count"] == 1, row

    detail = ok(srv, "/api/requirements/standalone")
    assert "Written by a person." in detail["body_markdown"], detail
    assert [t["id"] for g in detail["groups"] for t in g["tickets"]] == ["TKT-410"], detail


# ── SRV-FIX-181 §3 · a check derives its inputs from the declaration under test ───────────────
#
# The structural change this escape owes, and the one that catches its whole class. The four checks
# below used to build their ticket directories as a UNION — the one the manifest declared, plus a
# glob of every `*/docs/tickets` that existed — and named the gap in their own comment: *"the tool
# reads the one the manifest declares, and the others are repositories waiting to be split out."*
# So every factory ticket was checked, every assertion was green, and the question of whether the
# TOOL could open any of them was never asked. A check that widens its own input to cover what the
# configuration omits converts a configuration gap into a permanent silence.
#
# The glob stays, and it stops being an input: it computes what this repository HOLDS, the manifest
# computes what is DECLARED, and the first must be a subset of the second. A `docs/` tree added to
# this monorepo in future turns this red until the manifest declares the repository it belongs to,
# instead of being quietly absorbed.
#
# WIDENED BY SRV-FEAT-244, and the property is unchanged. This monorepo now holds TWO manifests:
# `.factory/factory.json` points the tool at Acme and declares Acme's documents, and
# `factory/.factory/factory.json` points a second instance at the factory and declares the
# factory's. So *declared* means declared by SOME manifest here, and the checks below span both —
# the documents did not move, only which manifest hands them to a tool.
#
# What is globbed for is a MANIFEST, which is the declaration these checks are about, and never a
# document directory. Dropping a `docs/tickets` tree into this monorepo is still red: the only way
# to make it green is to write the manifest that declares it, which is the act the check demands.

DEFAULT_DOCS = {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"}


def declared_repos(manifest):
    """Every repository the manifest declares, `{name: {path, docs}}`.

    A manifest with no `repos` block is one implicit repository at `.` carrying the top-level
    `docs` — the same rule the tool itself applies, written once here so these checks cannot
    drift from it.
    """
    def docs_of(spec):
        given = spec.get("docs", {})
        return {k: given.get(k, default) for k, default in DEFAULT_DOCS.items()}

    if "repos" in manifest:
        return {name: {"path": spec.get("path", "."), "docs": docs_of(spec)}
                for name, spec in manifest["repos"].items()}
    return {"": {"path": ".", "docs": docs_of(manifest)}}


MANIFEST_REL = ".factory/factory.json"


def _repo_root():
    """The ONE door from this suite to the monorepo it lives in.

    Every case that reads this repository's own documents — rather than a repository it built in
    `tmp_path` — comes through here and carries `@pytest.mark.repository`, and
    `test_every_case_that_reads_this_repository_is_in_the_repository_gate` asserts both halves.
    Together they are what makes `pytest tests/ -m repository` a set somebody can run after a
    merge and know it is the whole of the document gate (SRV-FIX-242).

    It counted directories — the suite's parent's parent's parent — which is the monorepo root
    only while this repository sits exactly one level below it. A lane is a worktree at
    `.factory/worktrees/lane/NN`, so in the place every ticket is actually worked the count landed
    on `.factory/worktrees` and all 27 cases behind this door failed with `FileNotFoundError` on a
    manifest that was never there. The checkout is found instead: this suite is at
    `<checkout>/server/tests`, and the checkout is the monorepo root unless its PARENT keeps a
    manifest too, in which case that parent is the monorepo and this is one repository inside it.
    """
    from pathlib import Path
    checkout = Path(__file__).resolve().parents[2]
    return checkout.parent if (checkout.parent / MANIFEST_REL).is_file() else checkout


def _this_repository():
    """This repository's own checkout — the tree the suite is PART of, wherever it is run from.

    `_repo_root()` answers *which monorepo*; from the developer's checkout that is the repository
    this one is cloned into, and from a lane worktree there is no such repository above it at all.
    This answers *which of its repositories is mine*, and the answer may not be a directory name
    typed here: a lane is a linked worktree at `.factory/worktrees/lane/NN`, so `<root>/factory`
    is the developer's copy of `main` when it exists and nothing when it does not. Six cases
    spelled it that way and all six were red in every lane (`SRV-FIX-495`).

    It goes through `_repo_root` so the gate above still sees every case that reaches this
    repository's documents.
    """
    from pathlib import Path

    root = _repo_root()
    here = Path(__file__).resolve().parents[2]
    return here if (here / MANIFEST_REL).is_file() else root


def _enclosing_monorepo():
    """The repository THIS one is cloned into, or `None` where there is none.

    Derived from git, never from the directories above the checkout: `--git-common-dir` points
    inside the repository that owns this checkout's objects and refs, and it gives the SAME answer
    from the developer's checkout and from a lane worktree — which `parents[n]` does not.
    """
    import subprocess
    from pathlib import Path

    mine = _this_repository()
    out = subprocess.run(["git", "-C", str(mine), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return None
    common = Path(out.stdout.strip())
    if not common.is_absolute():
        common = mine / common
    own_common = common.resolve()
    at = own_common
    while at != at.parent:
        at = at.parent
        if at != mine and (at / MANIFEST_REL).is_file():
            candidate = subprocess.run(["git", "-C", str(at), "rev-parse", "--git-common-dir"],
                                       capture_output=True, text=True, timeout=60)
            if candidate.returncode == 0:
                candidate_common = Path(candidate.stdout.strip())
                if not candidate_common.is_absolute():
                    candidate_common = at / candidate_common
                if candidate_common.resolve() == own_common:
                    continue
            return at
    return None


@pytest.mark.repository
@pytest.mark.parametrize("parent_manifest", [False, True])
def test_linked_lane_does_not_treat_its_physical_source_as_enclosing_monorepo(
        tmp_path, monkeypatch, parent_manifest):
    import subprocess
    from pathlib import Path

    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(outer)], check=True)
    if parent_manifest:
        manifest = outer / MANIFEST_REL
        manifest.parent.mkdir()
        manifest.write_text("{}\n")
    source = outer / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
    own_manifest = source / MANIFEST_REL
    own_manifest.parent.mkdir()
    own_manifest.write_text("{}\n")
    subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.test", "commit", "-qm", "source"],
                   check=True)
    lane = tmp_path / "lanes" / "01"
    lane.parent.mkdir()
    subprocess.run(["git", "-C", str(source), "worktree", "add", "-q", "-b",
                    "ticket/T", str(lane)], check=True)
    common = subprocess.check_output(["git", "-C", str(lane), "rev-parse",
                                      "--path-format=absolute", "--git-common-dir"],
                                     text=True).strip()
    assert Path(common).resolve() == (source / ".git").resolve()
    monkeypatch.setattr("test_catalog._this_repository", lambda: lane)

    inherited = next((parent for parent in outer.parents
                      if (parent / MANIFEST_REL).is_file()), None)
    assert _enclosing_monorepo() == (outer if parent_manifest else inherited)


def the_enclosing_monorepo_or_why_not():
    """`(root, "")` when the repository THIS one is cloned into is readable, else `(None, why)`.

    A rule about TWO repositories can only be checked where both are readable, and inside a lane
    the second is not: `S4` binds a lane to the repository it is a lane OF and nothing above it,
    which is the boundary working rather than failing.

    So the third state is REPORTED, never skipped. `docs/operations/ESCAPES.md` carries the row for a guarded
    section reading `n/n passed` without running, so a case using this prints which of the two it
    could not reach and says so in its own assertion message — and a case that CAN reach both is
    given no excuse at all.
    """
    mine = _this_repository()
    enclosing = _enclosing_monorepo()
    if enclosing is None:
        return None, (f"this repository ({mine}) is not cloned into a repository that keeps a "
                      f"manifest of its own that this process can read — S4 binds a lane to the "
                      f"repository it is a lane of, so a rule about two repositories cannot be "
                      f"answered from inside one of them")
    if not (enclosing / MANIFEST_REL).is_file():
        return None, f"{enclosing} keeps no manifest of its own"
    return enclosing, ""


def manifests_of_this_monorepo(root):
    """`[(the root it points at, its parsed manifest)]` for every manifest in this monorepo.

    The root's own first, then one per subdirectory that keeps a manifest of its own — a
    repository this monorepo has handed to an instance of its own rather than to the tool serving
    the product (SRV-FEAT-244).
    """
    found = [(root, json.loads((root / MANIFEST_REL).read_text()))]
    for path in sorted(root.glob("*/" + MANIFEST_REL)):
        found.append((path.parents[1], json.loads(path.read_text())))
    return found


def declared_ticket_dirs(root):
    """`{absolute tickets dir: the manifest that declares it}`, and the assertion that there are
    no others.

    The return value is the DECLARED set. What the filesystem holds is never added to it; it is
    only ever compared against it.
    """
    declared = {}
    for base, manifest in manifests_of_this_monorepo(root):
        for spec in declared_repos(manifest).values():
            declared[(base / spec["path"] / spec["docs"]["tickets"]).resolve()] = manifest

    held = {p.resolve() for p in root.glob("*/docs/tickets") if p.is_dir()}
    if (root / "docs" / "tickets").is_dir():
        held.add((root / "docs" / "tickets").resolve())

    undeclared = sorted(str(p.relative_to(root)) for p in held - set(declared))
    assert not undeclared, (
        "this monorepo holds ticket directories no manifest in it declares, so no instance of the "
        "tool can open a single document in them:\n  " + "\n  ".join(undeclared)
        + "\ndeclare the repository they belong to under `repos` in .factory/factory.json, or "
          "give that repository a manifest of its own at <repo>/.factory/factory.json")

    dirs = {p: manifest for p, manifest in sorted(declared.items()) if p.is_dir()}
    assert dirs, "no ticket directory found"
    return dirs


def ticket_ids_under(root):
    """`{id: [path relative to root]}` over every ticket document every manifest under `root`
    declares.

    It takes a root because the case below reads THIS repository, and a case that can only read
    this repository cannot go red until somebody has already broken `main` — which is how the
    collision this function was extracted for sat on `main` for five days (SRV-FIX-242 §1). The
    same function answers about a repository a case builds in `tmp_path`, so the rule is
    falsifiable before the repository is.
    """
    import re

    seen = {}
    # every ticket directory this monorepo keeps, DECLARED rather than found: the glob is
    # an assertion now, not an input (SRV-FIX-181 §3).
    for d in declared_ticket_dirs(root):
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue
            text = f.read_text()
            head = text.split("\n---", 2)
            front = head[0][4:] if text.startswith("---\n") and len(head) > 1 else ""
            keys = dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, re.M))
            tid = keys.get("id", "").strip()
            if tid:
                seen.setdefault(tid, []).append(str(f.relative_to(root)))
    return seen


def ticket_titles_under(root):
    """`{title: [path]}` over the same documents, lowercased and unquoted.

    The id rule cannot see two documents that are the SAME ticket under two ids, which is what
    `WEB-FIX-197` and `SRV-FIX-207` were: one filing, renumbered by the commit that recorded it,
    with the original left on disk. Two ids, so no collision; one ticket, so a report, a ledger
    row and a fix could each land under a different one of them.
    """
    import re

    seen = {}
    for d in declared_ticket_dirs(root):
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue
            text = f.read_text()
            head = text.split("\n---", 2)
            front = head[0][4:] if text.startswith("---\n") and len(head) > 1 else ""
            keys = dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, re.M))
            title = keys.get("title", "").strip().strip("'\"").lower()
            if title:
                seen.setdefault(title, []).append(str(f.relative_to(root)))
    return seen


def ids_claimed_twice(root):
    """The offenders alone — `{id: [paths]}` for every id more than one document claims
    WITHIN ONE REPOSITORY.

    Across repositories an id may repeat, and must be allowed to. A monorepo can hold several
    manifests, each handed to its own instance (SRV-FEAT-244), and an instance only ever reads the
    ticket directories ITS manifest declares — `Acme`'s declares one repo at `.` with `docs/`,
    and the factory's declares the factory. Two independent repositories both numbering a ticket
    `SRV-FEAT-007` is the same fact as two GitHub repositories both having issue #1: no instance
    can be asked an ambiguous question, so there is nothing to refuse.

    Unioning them is what this function did, and it made a repository's own numbering hostage to
    whatever it happens to be cloned inside — which `factory/CLAUDE.md` forbids in as many words:
    *nothing here may depend on that parent existing*. The rule the ledger is about is still
    enforced, one repository at a time: `409 ambiguous_ticket_id` (SRV-FIX-188) is what an
    instance answers when ITS OWN documents claim one id twice.
    """
    import json as _json
    import re as _re

    per_repo = {}
    for d, manifest in declared_ticket_dirs(root).items():
        key = _json.dumps(manifest, sort_keys=True)
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue
            text = f.read_text()
            head = text.split("\n---", 2)
            front = head[0][4:] if text.startswith("---\n") and len(head) > 1 else ""
            keys = dict(_re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, _re.M))
            tid = keys.get("id", "").strip()
            if tid:
                per_repo.setdefault(key, {}).setdefault(tid, []).append(str(f.relative_to(root)))

    offenders = {}
    for _key, ids in per_repo.items():
        for tid, files in ids.items():
            if len(files) > 1:
                offenders[tid] = files
    return offenders


@pytest.mark.repository
def test_no_two_tickets_claim_the_same_id():
    """One id, one ticket — because one grep has to trace ticket ↔ commits ↔ report.

    Root `CLAUDE.md`: *"Ticket id = commit prefix (`FEAT-007: …`) — one grep traces ticket ↔
    commits ↔ report"*. And `catalog::Store::ticketById` returns the FIRST document whose id
    matches, so `GET /api/tickets/<id>` can only ever reach one of a colliding pair: the other is
    unreachable through the tool that exists to find it, and a report filed under that id belongs
    to both and to neither.

    Nothing asserted this, and two collisions had accumulated — one of them made by the session
    that wrote this test, 36 minutes after another session took the number. The counter is
    repo-wide and hand-allocated by `ls docs/tickets/`, which is a race between any two sessions
    filing at once; a check is the only thing that closes it.
    """
    root = _repo_root()
    seen = ticket_ids_under(root)

    assert len(seen) > 20, f"only {len(seen)} ids seen — the glob found the wrong thing"
    clashes = ids_claimed_twice(root)
    assert not clashes, (
        "these ids are claimed by more than one ticket; the tool can only reach the first of "
        "each and a report filed under one belongs to neither:\n  "
        + "\n  ".join(f"{tid}: {', '.join(files)}" for tid, files in sorted(clashes.items())))


# ── SRV-FIX-242 · the id rule, drivable against a repository a case BUILDS ═════════════════════
#
# `test_no_two_tickets_claim_the_same_id` reads this repository, and a rule that can only read
# this repository has exactly one way to go red: somebody breaks `main` first. It did — two
# documents claimed `WEB-FIX-197` from 2026-08-22, in `d94f5840`, a commit that names no ticket
# and ran no gate, and the case was red on `main` for five days while every ticket's own GATE ran
# it and read past it. The rule is not wrong; the only fixture it had was the repository it
# guards.

def a_repository_of_tickets(tmp_path, tickets, name="fx"):
    """A repository the document rules can be asked about: one manifest, one workspace, one docs
    triple, and `{filename: id}` written into its tickets directory."""
    root = tmp_path / name
    (root / ".factory").mkdir(parents=True)
    (root / ".factory" / "factory.json").write_text(json.dumps({
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
    }))
    (root / "docs" / "tickets").mkdir(parents=True)
    for filename, tid in tickets.items():
        (root / "docs" / "tickets" / filename).write_text(
            doc({"id": tid, "workspace": "alpha", "status": "todo"}, "body"))
    return root


def test_a_second_document_claiming_a_taken_id_is_named(tmp_path):
    """Adding the collision makes the rule report it, and not adding it makes the rule quiet.

    Both arms, because a rule that reports a collision on every input reports nothing: the clean
    arm is what says the answer is about the documents rather than about the function.
    """
    clean = a_repository_of_tickets(tmp_path, {
        "FAC-FIX-900-the-first.md": "FAC-FIX-900",
        "FAC-FIX-901-the-second.md": "FAC-FIX-901",
    }, name="clean")
    assert ids_claimed_twice(clean) == {}, (
        "two documents with two ids were reported as a collision: "
        f"{ids_claimed_twice(clean)}")

    collided = a_repository_of_tickets(tmp_path, {
        "FAC-FIX-900-the-first.md": "FAC-FIX-900",
        "FAC-FIX-900-a-second-session-took-the-same-number.md": "FAC-FIX-900",
        "FAC-FIX-901-the-second.md": "FAC-FIX-901",
    }, name="collided")
    clashes = ids_claimed_twice(collided)
    assert sorted(clashes) == ["FAC-FIX-900"], clashes
    assert sorted(clashes["FAC-FIX-900"]) == [
        "docs/tickets/FAC-FIX-900-a-second-session-took-the-same-number.md",
        "docs/tickets/FAC-FIX-900-the-first.md",
    ], clashes["FAC-FIX-900"]


@pytest.mark.repository
def test_the_id_rule_still_names_a_collision_with_this_repository_clean(tmp_path):
    """MANDATORY NEGATIVE — the rule went green because the duplicate went away, not because the
    rule stopped looking.

    `SRV-FIX-242` cleared this repository's own collision by renumbering one of the two documents
    that claimed `WEB-FIX-197`. Deleting a file is also how you would make a broken rule green, and
    from the summary line the two are the same event. So this runs the SAME function the live case
    runs — `ids_claimed_twice` — over a repository built here with a DIFFERENT collision in it, and
    requires it to be named. It fails the moment the scan stops scanning, whatever this
    repository's documents happen to hold.
    """
    assert ids_claimed_twice(_repo_root()) == {}, (
        "this repository has a collision again; the live case above owns that failure")

    fixture = a_repository_of_tickets(tmp_path, {
        "FAC-FIX-902-one.md": "FAC-FIX-902",
        "FAC-FIX-902-two.md": "FAC-FIX-902",
    }, name="negative")
    assert sorted(ids_claimed_twice(fixture)) == ["FAC-FIX-902"], (
        "the rule is green on this repository AND green on a repository that has a collision — it "
        "is measuring the absence of a file, not the presence of the rule")


def reaches_the_monorepo_root(node):
    """`…parents[3]` as a syntax tree rather than as a substring.

    Read off the AST because this case's own source has to be able to NAME the expression it
    forbids without becoming an instance of it — a grep for the text finds the rule as well as the
    violations, and a rule that fails on itself gets deleted rather than obeyed.
    """
    import ast

    return (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute) and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant) and node.slice.value == 3)


@pytest.mark.repository
def test_every_case_that_reads_this_repository_is_in_the_repository_gate():
    """The gate's own guard: one door to the monorepo, and everything through it is selectable.

    Five document rules were red on `main` for days at a time and no ticket's GATE stopped on
    them, because the only command that runs them is the full suite — 833 s — and the failures it
    reports are one summary line among 450 cases. `pytest tests/ -m repository` is the whole of
    this repository's document gate: 22 cases, 11-14 s of which almost all is importing the
    suite's other modules, against 842 s for the suite itself. That is what makes it a thing
    somebody actually runs after a merge (`docs/TICKET-WORKFLOW.md`, VERIFY).

    A named set is only a gate while it is COMPLETE, so this asserts the two facts that keep it so:
    the monorepo is reached through `_repo_root` and nowhere else, and every case that reaches it
    carries the marker. A new document rule cannot be written outside the gate without turning
    this red.
    """
    import ast
    import re
    from pathlib import Path

    tests_dir = Path(__file__).resolve().parent
    modules = sorted(tests_dir.glob("test_*.py"))
    assert len(modules) > 10, f"only {len(modules)} test modules seen — the glob found the wrong thing"

    doors, unmarked, reaching = [], [], []
    for path in modules:
        src = path.read_text()
        tree = ast.parse(src)
        segments = {n.name: (ast.get_source_segment(src, n) or "")
                    for n in tree.body if isinstance(n, ast.FunctionDef)}

        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name == "_repo_root":
                continue
            if any(reaches_the_monorepo_root(n) for n in ast.walk(node)):
                doors.append(f"{path.name}::{node.name}")

        # transitively: a case that calls a helper that calls the door is reading this repository
        reach = {"_repo_root"}
        changed = True
        while changed:
            changed = False
            for name, seg in segments.items():
                if name in reach:
                    continue
                if any(re.search(rf"\b{re.escape(r)}\s*\(", seg) for r in reach):
                    reach.add(name)
                    changed = True

        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if node.name not in reach:
                continue
            reaching.append(f"{path.name}::{node.name}")
            marks = {ast.unparse(d) for d in node.decorator_list}
            if "pytest.mark.repository" not in marks:
                unmarked.append(f"{path.name}::{node.name}")

    assert not doors, (
        "these reach the monorepo root without going through `_repo_root`, so the gate cannot see "
        "them:\n  " + "\n  ".join(doors)
        + "\ncall `_repo_root()` — one door is what makes the marker checkable")
    assert not unmarked, (
        "these cases read this repository's own documents and are not in the repository gate, so "
        "`pytest tests/ -m repository` would report green on a repository they fail:\n  "
        + "\n  ".join(unmarked) + "\nadd `@pytest.mark.repository`")
    assert len(reaching) > 15, (
        f"only {len(reaching)} cases reach this repository — the scan found the wrong thing, and "
        f"an empty scan is what makes the two assertions above vacuous")


@pytest.mark.repository
def test_no_two_tickets_are_the_same_ticket_under_two_ids():
    """THE ASSERTION THIS ESCAPE WAS OWED — one filing, one id, and the id rule cannot see it.

    `test_no_two_tickets_claim_the_same_id` above compares ids, so it is blind in exactly the
    direction this repository actually failed: `WEB-FIX-197-two-factories-…` and
    `SRV-FIX-207-two-factories-…` were byte-identical but for one path, filed once and renumbered
    by `d94f5840`, which wrote the new document and left the old one. Two ids means no collision to
    report, and the consequence is worse than a collision — `factory/docs/operations/ESCAPES.md` carries the
    row under `207`, the older document is what a `git grep` finds first, and the fix, the report
    and the ledger row could each have landed under a different id for one piece of work.

    The title is the key because it is the one field a renumber does not touch. A ticket that
    genuinely supersedes another says so in its own words and gets its own title; two documents
    with one title are one ticket wearing two.
    """
    root = _repo_root()
    titles = ticket_titles_under(root)

    assert len(titles) > 20, f"only {len(titles)} titles seen — the glob found the wrong thing"
    twinned = {t: files for t, files in titles.items() if len(files) > 1}
    assert not twinned, (
        "these titles belong to more than one document, so one ticket exists under two ids and "
        "its report, its ledger row and its fix can each land under a different one:\n  "
        + "\n  ".join(f"{t}:\n      " + "\n      ".join(files)
                      for t, files in sorted(twinned.items()))
        + "\ndelete the superseded document, or give the second ticket a title of its own")


@pytest.mark.repository
def test_every_ticket_in_this_repository_names_a_declared_workspace():
    """This repository holding up its end of that agreement — the check whose absence let every
    ticket sit in `unassigned` while every assertion about the grouping passed.

    No server and no fixture: it is a statement about documents, and the documents are the state.
    """
    import re

    root = _repo_root()
    # every ticket directory this monorepo keeps, DECLARED rather than found: the glob is
    # an assertion now, not an input (SRV-FIX-181 §3). Each directory is checked against the
    # workspaces of the manifest that DECLARES it — the manifest whose instance will draw it —
    # rather than against one manifest's list for documents belonging to two (SRV-FEAT-244).
    dirs = declared_ticket_dirs(root)

    offenders = []
    checked = 0
    for d, manifest in dirs.items():
        declared = set(manifest["workspaces"])
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue          # a template is not a ticket and has no workspace to name
            head = f.read_text().split("\n---", 2)
            front = head[0][4:] if head[0].startswith("---\n") and len(head) > 1 else ""
            keys = dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, re.M))
            checked += 1
            ws = keys.get("workspace", "").strip()
            if ws not in declared:
                offenders.append(f"{f.relative_to(root)}: workspace={ws!r} "
                                 f"(declared here: {sorted(declared)})")

    assert checked > 3, f"only {checked} tickets seen — the glob found the wrong thing"
    assert not offenders, (
        "these tickets name no workspace the manifest that declares their directory declares, so "
        "they group as `unassigned`:\n  " + "\n  ".join(offenders))


# ── SRV-FIX-105 QA round 1, finding 2 · an escape answers §3 and enters the ledger ────────────
#
# `CLAUDE.md` and `docs/TICKET-WORKFLOW.md` have said for weeks that a `FIX` carrying
# `escaped_from:` answers §3 and adds a row to its own `ESCAPES.md`, in the SAME commit as the
# fix. Nothing ever checked, and QA measured the result while failing `SRV-FIX-105` for exactly
# this: of the escape tickets in `factory/docs/tickets/`, twenty-four answer neither.
#
# The debt below is FROZEN. It is not a to-do list the next session may add a line to: every id
# in it was allocated before `ESCAPE_DEBT_CEILING`, the check refuses any entry above that number,
# and ticket numbers only go up — so a ticket written after this gate landed cannot join it. The
# list may only SHRINK: an entry that has since answered §3 and taken its ledger row fails the
# check until the line is deleted, which is what stops it becoming documentation with an `assert`
# in front of it. `SRV-FIX-107` is the ticket that works it off.

ESCAPE_DEBT_CEILING = 106

ESCAPE_DEBT = {
    "SRV-FIX-043", "SRV-FIX-066", "WEB-FIX-067", "SRV-FIX-068", "SRV-FIX-071", "SRV-FIX-074", "SRV-FIX-076", "SRV-FIX-089",
    "WEB-FIX-090", "SRV-FIX-092", "SRV-FIX-093", "WEB-FIX-094", "WEB-FIX-095",
    "WEB-FIX-097", "WEB-FIX-100", "WEB-FIX-103", "WEB-FIX-104",
}


# ── SRV-FIX-528 · the domain of both sweeps: the escape tickets somebody has WORKED ───────
#
# Everything an escape ticket owes below — a row in its own ledger, §3 answered, §3's fifth
# question carrying a query and its verbatim output — is paid by the commit that FIXES the escape
# (`_TEMPLATE.md` §3: *"Landed in this commit"*). Both sweeps filtered on `escaped_from:` and on
# nothing else, so a ticket that was filed and never opened was judged against obligations it
# could not yet have met, and FILING an escape — the one act the ledger exists to encourage —
# turned the document gate red. On 2026-09-11 all 20 tickets the first case named were
# `status: todo`, and every session since 2026-09-08 had read past them.
#
# What follows is a domain, not a softer rule. The obligations, their regexes and their words are
# untouched; the refusals below take the set they may refuse as an ARGUMENT, which is what lets
# `test_a_filed_escape_owes_its_heading_and_its_row` and
# `test_a_worked_escape_with_an_unanswered_section_3_is_refused` drive the same code over a
# repository written in `tmp_path` and require it RED.

FILED_NOT_WORKED = ("todo", "superseded")

EscapeRecord = collections.namedtuple("EscapeRecord", "rel keys text ledger_rel ledger_text")


def worked_statuses(root):
    """The statuses at which an escape ticket's obligations are OWED, read off `_TEMPLATE.md`.

    READ and not typed. The template's `status:` line carries the vocabulary in its own trailing
    comment, and a domain spelled in this file would keep its list of statuses long after the
    product stopped using them — a set you typed is bounded by what you already thought of.

    `superseded` is subtracted as well although the template does not declare it: a ticket the
    backlog moved past was never worked either, and 101 of this repository's escape tickets carry
    it. A status the template declares NEITHER — `open`, today, on three of them — is outside the
    domain too, and `escape_summary` prints it by name and by count so that is a thing a reader
    sees rather than a thing the sweep swallows.
    """
    import re

    text = (root / "docs" / "tickets" / "_TEMPLATE.md").read_text()
    head = text.split("\n---", 2)
    front = head[0][4:] if text.startswith("---\n") and len(head) > 1 else ""
    m = re.search(r"^status:[^#\n]*#(.*)$", front, re.M)
    assert m, (
        "`docs/tickets/_TEMPLATE.md`'s frontmatter has no `status:` line carrying its vocabulary "
        "in a trailing comment, so there is nothing here to read this sweep's domain out of and "
        "it would silently judge every escape ticket or none of them")
    declared = {s.strip() for s in m.group(1).split("|") if s.strip()}
    assert "todo" in declared and len(declared) >= 4, (
        "the template's `status:` comment does not read as an `a | b | c` vocabulary naming "
        f"`todo`: {sorted(declared)}")
    return declared - set(FILED_NOT_WORKED)


def _status_of(rec):
    """A ticket's `status:`, without any comment it trails.

    A status nobody can read is excluded from every sweep below, which is the quiet way a domain
    becomes empty; the split is on the WORD, so `pass  # QA round 2` is `pass`.
    """
    return rec.keys.get("status", "").split("#")[0].strip()


def escape_records_in(pairs, root):
    """`{id: EscapeRecord}` over `[(tickets dir, that repository's ESCAPES.md)]`.

    The ONE scanner. The repository cases reach it through `declared_ticket_dirs`; the two fixture
    cases reach it with a directory and a ledger they wrote in `tmp_path`, so a fixture is judged
    by the same frontmatter parse, the same `status:` read and the same ledger read as this
    repository is, rather than by a model of them.
    """
    import re

    found = {}
    for tickets, ledger in pairs:
        rows = ledger.read_text() if ledger.exists() else ""
        ledger_rel = str(ledger.relative_to(root))
        for f in sorted(tickets.glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue
            text = f.read_text()
            head = text.split("\n---", 2)
            front = head[0][4:] if text.startswith("---\n") and len(head) > 1 else ""
            keys = {k: v.strip().strip("`") for k, v in
                    re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, re.M)}
            tid = keys.get("id", "")
            if tid and keys.get("escaped_from", ""):
                found[tid] = EscapeRecord(str(f.relative_to(root)), keys, text, ledger_rel, rows)
    return found


def declared_ticket_ledgers(root):
    """Each declared ticket directory and the ledger its manifest assigns to that repository."""
    declared_ticket_dirs(root)
    pairs = {}
    for base, manifest in manifests_of_this_monorepo(root):
        raw_repos = manifest.get("repos", {"": manifest})
        for name, spec in declared_repos(manifest).items():
            repository = base / spec["path"]
            tickets = (repository / spec["docs"]["tickets"]).resolve()
            ledger = raw_repos[name].get("docs", {}).get("escapes")
            pairs[tickets] = ((repository / ledger).resolve() if ledger
                              else tickets.parent / "ESCAPES.md")
    return pairs


def escape_records(root):
    """Every escape ticket paired with its declaring repository's ledger."""
    return escape_records_in(declared_ticket_ledgers(root).items(), root)


def filing_misses(tid, rec):
    """What FILING an escape owes, whatever the ticket's status — the heading and the row.

    Status-blind on purpose. Filing an escape is not repairing it, so nothing about the repair can
    be owed yet; a `## 3.` heading and a row in the ticket's own ledger are the filing itself, and
    the narrowed domain below must not be read as deleting them.
    """
    import re

    missing = []
    if not re.search(r"^## 3", rec.text, re.M):
        missing.append("no §3 escape analysis")
    if tid not in rec.ledger_text:
        missing.append(f"no row in {rec.ledger_rel}")
    return missing


def excluded_lines(records, judged):
    """One line per record the domain left out, naming it and the status that excused it."""
    keep = set(judged)
    return [f"{tid} ({_status_of(rec) or 'no status'}) — filed, not yet worked: {rec.rel}"
            for tid, rec in sorted(records.items()) if tid not in keep]


def split_by_status(records, worked):
    """`(judged, excluded)` — the ids a sweep may refuse, and the ones it may not, with why."""
    judged = sorted(tid for tid, rec in records.items() if _status_of(rec) in worked)
    return judged, excluded_lines(records, judged)


def escape_summary(records, judged, offenders, debt, domain):
    """The line a sweep prints, green or red — its counts, its exclusions, its frozen entries.

    An exclusion nobody can see is indistinguishable from a repository with no escapes in it,
    which is why `test_escapes_are_evals.py` prints its own on every run and why this does too.
    A FROZEN offender is the same shape of invisible — `offenders: 37` on a green run means
    nothing until the line says how many of them a debt list is excusing and which.

    `domain` names the set that was judged, and it is a parameter because there are two: a line
    reading `judged (worked)` over a status-blind pass is two correct numbers forming a false
    sentence. The count and the list are the SAME `excluded_lines` call, for the same reason.
    """
    excluded = excluded_lines(records, judged)
    keep = set(judged)
    tally = collections.Counter(_status_of(rec) or "no status"
                                for tid, rec in records.items() if tid not in keep)
    frozen = sorted(tid for tid, _why in offenders if tid in debt)
    unfrozen = sorted(tid for tid, _why in offenders if tid not in debt)
    return (f"escape tickets: {len(records)} · judged ({domain}): {len(judged)} · "
            f"excluded: {len(excluded)} "
            f"[{' · '.join(f'{s}: {n}' for s, n in sorted(tally.items())) or 'none'}] · "
            f"owing: {len(offenders)} [frozen: {len(frozen)} · unfrozen: {len(unfrozen)}]\n  "
            + "\n  ".join(["excluded on this run (filed, not yet worked):"] + excluded)
            + "\n  " + "\n  ".join(["frozen on this run (a debt list is excusing these):"]
                                    + frozen))


WORKED_OWES = ("have been worked, and owe what an escape owes — §3's four questions, and a row "
               "in their own ESCAPES.md, in the same commit as the fix")

FILING_OWES = ("were FILED as escapes and owe what filing owes — a `## 3.` heading, and a row in "
               "their own ESCAPES.md naming them, so the ledger can see the cause before anybody "
               "has written the fix")


def refuse_escape_obligations(records, judged, debt, domain="worked", owes=WORKED_OWES):
    """`## 3.` and a ledger row over `judged`, and the summary line it decided on.

    Pure: the domain arrives as an argument, which is the whole of `SRV-FIX-528`. `debt` is the
    frozen list the caller tolerates — empty for a fixture, `ESCAPE_DEBT` for the worked pass,
    `ESCAPE_DEBT | FILING_DEBT` for the filing one.

    `domain` is what the summary calls the set it judged and `owes` is what the refusal says they
    owe, both parameters for the same reason: one sentence over two domains told a `todo` ticket
    it *"has been worked"* and owes its row *"in the same commit as the fix"* — a refusal that is
    right about the document and wrong about why.
    """
    offenders = []
    for tid in judged:
        misses = filing_misses(tid, records[tid])
        if misses:
            offenders.append((tid, f"{records[tid].rel}: " + ", ".join(misses)))
    said = escape_summary(records, judged, offenders, debt, domain)

    assert judged, (
        f"{said}\nnot one escape ticket here has been WORKED, so this case examined nothing and "
        "passed. A check that sweeps nothing has not run: an escape backlog every member of "
        "which is `todo` is a backlog nobody has opened, not a clean one. Check `worked_statuses` "
        "against the template's vocabulary before believing the repository.")

    unfrozen = [why for tid, why in offenders if tid not in debt]
    assert not unfrozen, (
        f"{said}\nthese tickets carry `escaped_from:`, {owes}:\n  " + "\n  ".join(unfrozen))
    return said


def refuse_where_else(records, judged, heading):
    """§3's fifth question over `judged`, and the summary line it decided on."""
    offenders = []
    for tid in judged:
        missing = where_else_is_unanswered(records[tid].text, heading)
        if missing:
            offenders.append((tid, f"{records[tid].rel}:\n      - " + "\n      - ".join(missing)))
    said = escape_summary(records, judged, offenders, frozenset(), "worked, at or above the "
                          f"{CLASS_SWEEP_FROM} ceiling")

    assert judged, (
        f"{said}\nno WORKED escape ticket at or above {CLASS_SWEEP_FROM} was found in either "
        "declared ticket directory, so this case asserted nothing. An empty sweep is a red: it is "
        "the shape a rule takes when it has become documentation with an `assert` in front of it.")
    assert not offenders, (
        f"{said}\nthese escape tickets are at or above the {CLASS_SWEEP_FROM} ceiling, have been "
        "worked, and do not say where else their cause is:\n  "
        + "\n  ".join(why for _tid, why in offenders)
        + "\n  run a query over the tree, paste it and its VERBATIM output in a fenced block "
          "under §3's fifth question, and give every hit a disposition line")
    return said


# ── SRV-FIX-528 QA round 1 · the FILING tier, and the debt the day it landed ──────────────
#
# Round 1 narrowed the sweep to the WORKED statuses and left `filing_misses` status-blind with a
# comment saying the narrowing "must not be read as deleting" the filing obligations. Nothing on
# the repository path ever called it for a `todo` ticket, so the sentence was true of the helper
# and false of the gate: QA filed `SRV-FIX-999` — `status: todo`, no `## 3.`, no ledger row — and
# both sweeps went green over a document `main` refuses by name. A rule is the domain it is
# CALLED with.
#
# So there are two tiers and two debts:
#
#   FILING  a `## 3.` heading and a row in the ticket's own ledger. Owed the moment the ticket is
#           filed, whatever its status — 25 of this repository's 45 `todo` escape tickets already
#           carry both, which is what says this is the convention and not a new rule.
#   WORKED  the same two, judged again over the worked statuses with `ESCAPE_DEBT` alone, so the
#           older freeze keeps biting the moment one of its tickets is picked up. A ticket frozen
#           below is excused for having been FILED without a row; landing its fix pays it.
#
# `FILING_DEBT` is the 20 tickets that owed a row (7 of them a heading as well) on the day this
# tier started biting. It is frozen by NUMBER at this ticket's own, exactly like `ESCAPE_DEBT`
# and `CLASS_SWEEP_FROM`: ticket numbers only go up, so nothing filed after this can join it, and
# the list may only SHRINK — an entry that has since taken its row fails until the line is
# deleted. `SRV-FIX-531` is the ticket that works it off.

FILING_DEBT_CEILING = 528

FILING_DEBT = {
    "SRV-FIX-392", "SRV-FIX-393", "SRV-FIX-438", "SRV-FIX-449", "SRV-FIX-450", "SRV-FIX-465",
    "SRV-FIX-468", "SRV-FIX-471", "SRV-FIX-472", "SRV-FIX-473", "SRV-FIX-496", "SRV-FIX-524",
    "WEB-FIX-346", "WEB-FIX-358", "WEB-FIX-371", "WEB-FIX-375", "WEB-FIX-386", "WEB-FIX-388",
    "WEB-FIX-390", "WEB-FIX-394",
}


def filing_domain(records):
    """Every escape ticket, whatever its status — the set the FILING refusal is called with.

    Named, and called by the gate, because the domain a rule is called with IS the rule. A
    `todo` ticket cannot have answered §3's questions about a fix nobody has written; it can
    carry the heading and take its row, and that is the filing rather than the repair.
    """
    return sorted(records)


def judge_filed_and_worked(records, root, worked_debt, filing_debt=frozenset()):
    """Every `## 3.`-and-ledger-row refusal the document gate makes over `records`.

    The gate's judgement, as a FUNCTION of the documents. A case asking *would this gate refuse
    X* adds X to the records and gets the answer the gate gives, rather than calling a refusal
    with a domain the gate never builds — which is the whole of what QA round 1 found.

    Two passes, two domains, two debts; the summary of each is returned so both are printed.
    """
    filed = refuse_escape_obligations(records, filing_domain(records), filing_debt,
                                      "filed — every escape ticket, whatever its status",
                                      FILING_OWES)
    worked = split_by_status(records, worked_statuses(root))[0]
    return ("FILING — a `## 3.` heading and a row in the ticket's own ledger:\n" + filed
            + "\n\nWORKED — the same two, over the template's statuses minus `todo` and "
              "`superseded`:\n"
            + refuse_escape_obligations(records, worked, worked_debt))


@pytest.mark.repository
def test_every_escape_ticket_answers_section_3_and_has_a_ledger_row():
    """A ticket that says it is fixing an escape, checked against what an escape ticket owes.

    A statement about documents, like the case above it: the four questions in §3 are what turns
    a bug into a changed check, and the ledger row is what lets two escapes with one cause be
    seen as one cause. Both were required in prose and asserted nowhere.

    `SRV-FIX-528` gave it a domain. It judges the tickets somebody has WORKED — the template's
    own status vocabulary minus `todo` and `superseded` — and prints every ticket it excluded and
    the status that excused it, on every run.
    """
    import re

    root = _repo_root()
    records = escape_records(root)
    assert len(records) > 3, (
        f"only {len(records)} escape tickets seen — the glob found the wrong thing")
    print("\n" + judge_filed_and_worked(records, root, ESCAPE_DEBT,
                                        ESCAPE_DEBT | FILING_DEBT))

    # ...and the frozen list itself, kept honest from both ends. STATUS-BLIND: every id in it is
    # `superseded` today, so a ratchet narrowed to the judged set would sweep nothing and call
    # that a list with nothing left to work off.
    owing = {tid for tid, rec in records.items() if filing_misses(tid, rec)}
    stale = sorted(ESCAPE_DEBT - owing)
    assert not stale, (
        "these ids are in ESCAPE_DEBT but no longer owe anything (or no longer exist as escape "
        f"tickets) — delete the line, the list may only shrink: {stale}")
    late = sorted(t for t in ESCAPE_DEBT
                  if int(re.search(r"(\d+)$", t).group(1)) > ESCAPE_DEBT_CEILING)
    assert not late, (
        f"ESCAPE_DEBT was frozen at {ESCAPE_DEBT_CEILING}; these were allocated after it and may "
        f"never be excused: {late}")
    assert ESCAPE_DEBT <= set(records), (
        "ESCAPE_DEBT names tickets this repository does not have: "
        f"{sorted(ESCAPE_DEBT - set(records))}")

    # ...and `FILING_DEBT`, kept honest from the same three ends. It sweeps 20 entries, every one
    # of them a ticket that still owes; an empty sweep here would be the list having become
    # documentation with an `assert` in front of it.
    assert FILING_DEBT, "FILING_DEBT is empty, so these three ratchets examine nothing"
    stale_filing = sorted(FILING_DEBT - owing)
    assert not stale_filing, (
        "these ids are in FILING_DEBT but have since taken their heading and their row — delete "
        f"the line, the list may only shrink: {stale_filing}")
    late_filing = sorted(t for t in FILING_DEBT
                         if _number(t) is not None and _number(t) > FILING_DEBT_CEILING)
    assert not late_filing, (
        f"FILING_DEBT was frozen at {FILING_DEBT_CEILING} — `SRV-FIX-528`'s own number. These "
        f"were filed after the tier started biting and may never be excused: {late_filing}")
    assert FILING_DEBT <= set(records), (
        "FILING_DEBT names tickets this repository does not have (or that stopped being escape "
        f"tickets): {sorted(FILING_DEBT - set(records))}")


# ── SRV-FIX-347 · a class is fixed at the site the verdict named and ships everywhere else ────
#
# The case above requires §3 to EXIST and a ledger row to exist. Both are satisfied by every one
# of the 94 rows that measure this defect: over `factory/docs/ESCAPES.md` at `e52a4cae`, 94 of
# 172 rows (55%) are the second or later escape from a source already in the ledger, 95 escaped
# from a `FIX` — a repair that leaked again — and 46 sources account for 140 rows.
#
# The mechanism is in the ledger's own words three times: *"the class was fixed where it was
# lethal and left where it was merely wrong"* (`SRV-FIX-128`), *"a class was named in an analysis
# and fixed at one site"* (`SRV-FIX-105`), and `SRV-FIX-195`'s row — one load-sensitive vitest
# case fixed, *"twenty-three instances of the same cause left standing"*.
#
# §3's four questions ask what CHANGES so the class is caught. A change is a statement about
# future tickets, and no question asks where the cause is in the tree RIGHT NOW — so the fix lands
# at the site the verdict named and the other instances ship. `docs/TICKET-WORKFLOW.md` already
# says *"when two rows in the ledger share a cause, the cause is the next ticket"*; it is prose,
# it has no reader at the moment it comes due, and nothing asserted it. *An obligation that is
# prose and nothing asserts it* is itself a repeated row here (`SRV-FIX-281`, `SRV-FEAT-002`),
# which is why this ticket's change is these cases and not another paragraph.
#
# The ceiling is INCLUSIVE and frozen at this ticket's own number: `SRV-FIX-347` is judged by its
# own rule, and the 94 historical rows stay below it. Ticket numbers only go up, so nothing
# written after this can join the exemption and the exempt set can only shrink — the same shape
# as `ESCAPE_DEBT_CEILING` above, without the list, because there is nothing to work off.

CLASS_SWEEP_FROM = 347

# `- fixed here` / `- filed as SRV-FIX-350` / `- no other site` — one line per hit, and the third
# is what a query that found nothing looks like. The question is answered by a measurement, never
# by silence.
DISPOSITION = r"^- (fixed here|filed as [A-Z][A-Z-]*-\d+|no other site)\b"

# `SRV-FIX-347`, `FIX-041`, `FEAT-007`, `EPIC-003` — both repositories' spellings, and narrow
# enough that a ledger row saying `UTF-8` or `TCP-5680` is not read as a ticket.
TICKET_ID = r"\b(?:[A-Z][A-Z0-9]*-)?(?:FIX|FEAT|EPIC)-\d+\b"


def _number(ticket_id):
    """The trailing number, or `None` when there is not one.

    `None` is treated as IN scope everywhere below, never as exempt: an exemption reachable by
    writing an id this cannot read is not an exemption, it is a hole with a `#` in front of it.
    """
    import re

    m = re.search(r"(\d+)$", ticket_id)
    return int(m.group(1)) if m else None


def _below_the_ceiling(ticket_id):
    n = _number(ticket_id)
    return n is not None and n < CLASS_SWEEP_FROM


def _section_three(text):
    """§3 of a ticket or of the template — the heading to the next `## `, or `""`."""
    import re

    m = re.search(r"^## 3\b.*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def _bullet_headings(section):
    """The bold lead of every top-level `- **…**` bullet, in order."""
    import re

    return [m.group(1).strip() for m in re.finditer(r"^- \*\*(.+?)\*\*", section, re.M)]


def question_five_heading(root):
    """§3's fifth question, read OFF `_TEMPLATE.md` at test time — never spelled in a case.

    A case carrying the question's own words would stay green against a template that had dropped
    the bullet: it would be asserting a string the test holds rather than the document. So the
    sentence lives in exactly one place and the handle here is structural — §3's four original
    headings end in `:`, and the fifth is the only one phrased as a QUESTION.
    """
    section = _section_three((root / "docs" / "tickets" / "_TEMPLATE.md").read_text())
    assert section, "`docs/tickets/_TEMPLATE.md` has no §3 at all"
    questions = [h for h in _bullet_headings(section) if h.rstrip().endswith("?")]
    assert len(questions) == 1, (
        "`docs/tickets/_TEMPLATE.md` §3 must carry exactly one bullet phrased as a question — the "
        "fifth, asking where else the cause is in the tree right now — and it carries "
        f"{len(questions)}: {questions or 'none'}.\nThe other four headings end in `:` and this "
        "one ends in `?`; every case below reads the heading from here, so a template that drops "
        "it turns this red instead of leaving a green check behind.")
    heading = questions[0]
    assert len(heading.split()) >= 4, (
        f"§3's fifth heading is not a question anybody can answer: {heading!r}")
    return heading


def where_else_section(text, heading):
    """The answer under §3's fifth question in one document, dedented, or `""`.

    Dedented because the answer is the BODY of a markdown bullet and is indented under it, while
    the disposition line §1 specifies anchors at the start of a line.
    """
    import re

    section = _section_three(text)
    if not section:
        return ""
    m = re.search(r"^- \*\*" + re.escape(heading) + r"\*\*", section, re.M)
    if not m:
        return ""                       # the BULLET, never a mention of it in §3's prose
    body = section[m.start():]
    nxt = re.search(r"\n- \*\*", body)
    if nxt:
        body = body[:nxt.start()]
    return re.sub(r"^[ \t]+", "", body, flags=re.M)


def _without_fences(body):
    """The answer's prose with its fenced blocks removed.

    A disposition line is the author's DECISION about a hit; a line inside the fence is output the
    query printed. Counting the second as the first would let a ticket satisfy this by grepping
    for the word.
    """
    import re

    return re.sub(r"^```.*?^```", "", body, flags=re.M | re.S)


def where_else_is_unanswered(text, heading):
    """What a document's answer to §3's fifth question is missing; `[]` means it answered.

    ONE refusal, called by the case that judges this repository and by the negative that builds a
    ticket which must fail it. Without the negative, the positive passes identically against a
    helper that returns true the moment the heading appears — which is the whole of what the
    check above does with §3.
    """
    import re

    body = where_else_section(text, heading)
    if not body:
        return [f"§3 carries no `{heading}` bullet"]

    missing = []
    if not re.search(r"^```", body, re.M):
        missing.append("no fenced block — the answer is a re-runnable query and its VERBATIM "
                       "output, never prose about having looked")
    if not re.search(DISPOSITION, _without_fences(body), re.M):
        missing.append("no disposition line outside the fence — one per hit, each `- fixed here` "
                       "or `- filed as <TICKET-ID>`, or `- no other site` when the query found "
                       "nothing")
    return missing


def escape_tickets(root):
    """`{id: (path relative to root, frontmatter, full text)}` for every ticket carrying a
    non-empty `escaped_from:`, over both declared ticket directories.

    One scanner, `escape_records` (`SRV-FIX-528`): two of them drifting is a repository judged by
    one set of escape tickets and reported on by another.
    """
    return {tid: (rec.rel, rec.keys, rec.text) for tid, rec in escape_records(root).items()}


def _ledger_cells(line):
    """One ledger row split on `|`, ignoring pipes inside backticks."""
    out, cur, tick = [], "", False
    for ch in line.strip().strip("|"):
        if ch == "`":
            tick = not tick
            cur += ch
        elif ch == "|" and not tick:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [c.strip() for c in out]


def ledger_filers(root):
    """`{source id: {ids of the tickets that filed a row against it}}`, over both ledgers.

    A row names its source in the `Escaped from` column; which ticket FILED it is a join, not a
    column — the ticket whose id appears in the row AND whose own `escaped_from:` is that source.
    Reading the id alone would count every ticket a row's prose happens to mention.
    """
    import collections
    import re

    escapes = escape_tickets(root)
    filed = collections.defaultdict(set)
    for ledger in declared_ticket_ledgers(root).values():
        if not ledger.exists():
            continue
        lines = ledger.read_text().splitlines()
        headers = [_ledger_cells(l) for l in lines if l.startswith("| Date")]
        assert headers and "Escaped from" in headers[0], (
            f"{ledger} has no `| Date | … | Escaped from | …` header row, so there is no column "
            "to read a source out of and this join would quietly return nothing")
        source_col = headers[0].index("Escaped from")

        for line in lines:
            if not line.startswith("| 20"):
                continue
            cols = _ledger_cells(line)
            if len(cols) <= source_col:
                continue
            m = re.search(TICKET_ID, cols[source_col])
            if not m:
                continue
            source = m.group(0)
            for cand in set(re.findall(TICKET_ID, line)):
                if cand == source or cand not in escapes:
                    continue
                if escapes[cand][1].get("escaped_from", "") == source:
                    filed[source].add(cand)
    return filed


@pytest.mark.repository
def test_the_template_declares_the_fifth_question():
    """§3 asks where the cause is NOW, and says what an answer looks like.

    The four questions it had are all about the check: what it asserted, why the bug satisfied it,
    what would have caught it, what changes. Every one of them is answerable without opening the
    tree, which is how a ticket answers all four and still ships the class.
    """
    import re

    root = _repo_root()
    heading = question_five_heading(root)

    body = where_else_section((root / "docs" / "tickets" / "_TEMPLATE.md").read_text(), heading)
    assert body, f"§3 declares `{heading}` and then says nothing about how to answer it"
    lowered = re.sub(r"\s+", " ", body).lower()

    assert "verbatim" in lowered or "output" in lowered, (
        "the template asks the question without saying the answer is a QUERY AND ITS OUTPUT. "
        "`where else is this cause` answered in prose is the same sentence the ledger already "
        "carries 94 times.")
    for token in ("fixed here", "filed as", "no other site"):
        assert token in lowered, (
            f"the template never names `{token}`. `where_else_is_unanswered` refuses anything "
            "else, so a vocabulary the author cannot read here is a gate that fails people for "
            "words nobody told them.")


@pytest.mark.repository
def test_an_escape_ticket_above_the_ceiling_names_where_else_the_cause_is():
    """The gate `test_every_escape_ticket_answers_section_3_and_has_a_ledger_row` stops short of.

    That one asks whether §3 exists. This one asks whether the ticket MEASURED its own tree — a
    `FIX` that cannot show a query and its output over the code it is fixing does not pass.
    """
    root = _repo_root()
    heading = question_five_heading(root)

    # frozen by NUMBER — the exempt set only shrinks — and then by STATUS (`SRV-FIX-528`): a
    # ticket nobody has worked cannot have measured the tree its fix has not touched.
    above = {tid: rec for tid, rec in escape_records(root).items()
             if not _below_the_ceiling(tid)}
    judged, _excluded = split_by_status(above, worked_statuses(root))
    print("\n" + refuse_where_else(above, judged, heading))


@pytest.mark.repository
def test_a_repeat_escape_names_the_earlier_one():
    """`docs/TICKET-WORKFLOW.md`'s *"two rows share a cause, the cause is the next ticket"*, executable.

    55% of this ledger is a source leaking twice or more, and the second ticket is written as
    though it were the first: the earlier row is not read, so nothing about it reaches §1 and the
    fix is scoped to whatever the new verdict happened to name. `recurrence_of:` is the citation,
    and it is checked against the ledgers rather than trusted.
    """
    import re

    root = _repo_root()
    filers = ledger_filers(root)

    swept, offenders = [], []
    for tid, (rel, keys, _text) in sorted(escape_tickets(root).items()):
        if _below_the_ceiling(tid):
            continue
        source = keys.get("escaped_from", "")
        mine = _number(tid)
        earlier = sorted(t for t in filers.get(source, ())
                         if _number(t) is not None and mine is not None and _number(t) < mine)
        if not earlier:
            continue                                    # first escape from this source; nothing owed
        swept.append(tid)
        named = set(re.findall(TICKET_ID, keys.get("recurrence_of", "")))
        if not named & set(earlier):
            offenders.append(
                f"{rel}: escaped_from {source}, which {', '.join(earlier)} already filed against; "
                f"recurrence_of={keys.get('recurrence_of') or 'absent'}")

    assert swept, (
        "no escape ticket at or above the ceiling repeats a source already in a ledger, so this "
        "case asserted nothing. Check the join in `ledger_filers` before believing it: a source "
        "column this stopped parsing looks exactly like a repository that stopped repeating.")
    assert not offenders, (
        "these escapes are the second or later from their source and do not cite the first:\n  "
        + "\n  ".join(offenders)
        + "\n  read that row before writing §1 and add `recurrence_of: <ticket id>` to the "
          "frontmatter — a cause with two rows is a cause the new ticket is answering too")


@pytest.mark.repository
def test_a_prose_only_where_else_answer_is_refused(tmp_path):
    """NEGATIVE (mandatory). The heading is not the answer.

    Without this, the case above passes identically against a helper that returns true the moment
    the heading appears — and a §3 that carries the bullet and answers it with *"I looked and this
    is the only place"* is precisely the escape this ticket exists to stop. Both documents are
    built here and differ in one thing: whether the answer is a measurement.
    """
    root = _repo_root()
    heading = question_five_heading(root)

    def ticket(answer):
        return "\n".join([
            "---", "id: FAC-FIX-901", "escaped_from: FAC-FIX-900", "---", "",
            "## 3. Escape analysis", "",
            "- **What the gate asserted:** a criterion", "",
            f"- **{heading}**", "", answer, "",
            "- **Landed in this commit:** a file", ""])

    prose = tmp_path / "prose.md"
    prose.write_text(ticket(
        "  I read through the tree and this is the only place the cause appears. Nothing else\n"
        "  uses the pattern, so there is no other site to fix."))
    refused = where_else_is_unanswered(prose.read_text(), heading)
    assert len(refused) == 2, (
        "a prose answer under the right heading was accepted, or refused for only one of the two "
        f"things it is missing: {refused}")

    measured = tmp_path / "measured.md"
    measured.write_text(ticket(
        "  ```\n"
        "  $ grep -rn 'the cause' -- . | wc -l\n"
        "  0\n"
        "  ```\n"
        "\n"
        "  - no other site: the query is over the whole tree and returns nothing."))
    assert where_else_is_unanswered(measured.read_text(), heading) == [], (
        "the same helper refuses a query, its output and a disposition line — so the case above "
        "is red for everybody and the refusal is measuring the wrong thing")


@pytest.mark.repository
def test_the_ceiling_is_frozen():
    """NEGATIVE (mandatory). Raising the ceiling excuses whichever ticket is in front of you.

    That is how a frozen list becomes documentation with an `assert` in front of it, which is
    `ESCAPE_DEBT`'s own lesson one section up. The number is this ticket's own, and the exempt set
    below it can only shrink because ticket numbers only go up.
    """
    assert CLASS_SWEEP_FROM == 347, (
        f"CLASS_SWEEP_FROM was frozen at 347 — `SRV-FIX-347`'s own number — and is now "
        f"{CLASS_SWEEP_FROM}. Moving it does not close a class, it excuses the ticket that could "
        "not answer question 5; the historical rows below it are named in that ticket's §1 and "
        "stay there.")


# ── SRV-FIX-528 · the two sweeps above have a domain, and it is the tickets somebody WORKED ──
#
# Both cases filtered on `escaped_from:` and on nothing else. What they enforce — a ledger row,
# §3 answered, the fifth question carrying a query and its verbatim output — is owed by the commit
# that FIXES the escape (`_TEMPLATE.md` §3: *"Landed in this commit"*), so a ticket that was filed
# and never opened cannot have paid it and the act of FILING an escape turned the document gate
# red. 20 of the 20 tickets the first case named on 2026-09-11 were `status: todo`, every session
# since 2026-09-08 read past them, and that is how a red gate stops meaning anything.
#
# The fix is a domain, not a softer rule, and the two cases below are what says so: the same
# refusals, driven over a repository written in `tmp_path`, refuse a filed escape that carries
# neither its heading nor its row, and refuse a WORKED one whose §3 measured nothing.


def _filed_escape_repo(tmp_path, status, section_three="", row="", tid="FAC-FIX-901"):
    """A one-ticket repository on disk — a ticket directory, its `ESCAPES.md`, and one escape.

    Written as FILES and read back through `escape_records_in`, the same scanner the two
    repository cases go through. A record handed straight to a refusal would be a model of a
    ticket, and the frontmatter parse, the `status:` read and the ledger read are exactly what
    this ticket changes the domain of.

    `FAC-FIX-901` is above `CLASS_SWEEP_FROM` and above `ESCAPE_DEBT_CEILING`, so neither freeze
    can excuse it and what the cases below measure is the status and nothing else.
    """
    tickets = tmp_path / "docs" / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    ledger = tmp_path / "docs/operations/ESCAPES.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "| Date | Escape | Escaped from | Found by | Root cause of the MISS | Change landed |\n"
        "|---|---|---|---|---|---|\n" + row)
    (tickets / (tid + "-a-filed-escape.md")).write_text("\n".join([
        "---", "id: " + tid, "status: " + status, "escaped_from: FAC-FIX-900", "---", "",
        "# Factory Ticket — a filed escape", "", section_three, ""]))
    return escape_records_in([(tickets, ledger)], tmp_path)


def test_declared_escape_ledger_overrides_the_sibling_decoy(tmp_path):
    tickets = tmp_path / "docs/tickets"
    tickets.mkdir(parents=True)
    manifest = tmp_path / MANIFEST_REL
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({
        "workspaces": {"product": {"path": "."}},
        "docs": {"tickets": "docs/tickets", "escapes": "operations/evidence/ledger.md"},
    }))
    (tickets / "SRV-FIX-999.md").write_text(
        "---\nid: SRV-FIX-999\nstatus: awaiting-qa\nescaped_from: SRV-FIX-998\n---\n")
    header = "| Date | Escape | Escaped from |\n|---|---|---|\n"
    declared = tmp_path / "operations/evidence/ledger.md"
    declared.parent.mkdir(parents=True)
    declared.write_text(header + "| 2026-10-03 | SRV-FIX-999 | SRV-FIX-998 |\n")
    (tickets.parent / "ESCAPES.md").write_text(
        header + "| 2026-10-03 | obsolete sibling SRV-FIX-997 | SRV-FIX-998 |\n")

    records = escape_records(tmp_path)
    assert set(records) == {"SRV-FIX-999"}
    assert records["SRV-FIX-999"].ledger_rel == "operations/evidence/ledger.md"
    assert records["SRV-FIX-999"].ledger_text == declared.read_text()
    assert ledger_filers(tmp_path) == {"SRV-FIX-998": {"SRV-FIX-999"}}


@pytest.mark.repository
def test_a_filed_escape_owes_its_heading_and_its_row(tmp_path):
    """Criterion 2. The DOMAIN narrowed; what FILING owes did not.

    A `todo` escape ticket cannot have landed a fix, so it cannot have answered §3's questions
    about one. It can still carry the heading and take its row — that much is the filing, not the
    repair — and `filing_misses` is status-blind so that the rule stays sayable about a ticket
    the sweep no longer judges. Without this case the narrowing below is indistinguishable from
    deleting the rule for every ticket that has not been merged.
    """
    recs = _filed_escape_repo(tmp_path, "todo")            # no `## 3.`, no ledger row
    assert list(recs) == ["FAC-FIX-901"], (
        f"the fixture repository did not read back as one escape ticket: {list(recs)}")

    assert filing_misses("FAC-FIX-901", recs["FAC-FIX-901"]) == [
        "no §3 escape analysis", "no row in docs/operations/ESCAPES.md"], (
        "a filed escape carrying neither its §3 heading nor a ledger row was not refused both: "
        f"{filing_misses('FAC-FIX-901', recs['FAC-FIX-901'])}")

    with pytest.raises(AssertionError, match="FAC-FIX-901") as refused:
        refuse_escape_obligations(recs, filing_domain(recs), frozenset(),
                                  "filed — every escape ticket, whatever its status", FILING_OWES)
    assert "no §3 escape analysis" in str(refused.value), (
        f"the refusal named the ticket without saying what it owes: {refused.value}")
    assert "no row in docs/operations/ESCAPES.md" in str(refused.value), (
        f"the refusal did not say the ledger row is missing: {refused.value}")

    # ...and the only thing this ticket changed: the same records, over the WORKED statuses, are
    # excluded rather than forgiven — the id and the status that excused it are both printed.
    root = _repo_root()
    judged, excluded = split_by_status(recs, worked_statuses(root))
    assert judged == [], f"a `todo` escape ticket was judged as worked: {judged}"
    assert [l for l in excluded if "FAC-FIX-901" in l and "todo" in l], (
        "the exclusion is invisible — nothing in it names the ticket and the status that excused "
        f"it, so a reader cannot tell it apart from a repository with no escapes: {excluded}")

    # ── QA round 1 · the assertion that would have caught it ───────────────────────────
    #
    # Everything above proves the REFUSAL still refuses a `todo` ticket when it is told to judge
    # one. It cannot see that nothing tells it to. The QA session filed a real escape —
    # `SRV-FIX-999`, `status: todo`, `escaped_from:` set, no `## 3.` heading, no row in
    # `docs/ESCAPES.md` — into this repository's own `docs/tickets/` and ran both sweeps:
    #
    #     ======================= 2 passed, 78 deselected in 0.52s =======================
    #
    # while `main`'s code refuses the same document by name:
    #
    #     E  docs/tickets/SRV-FIX-999-…md: no §3 escape analysis, no row in docs/ESCAPES.md
    #     ============================== 1 failed in 1.14s ===============================
    #
    # So the assertion is about the GATE, not about the refusal: hand the gate this repository
    # plus that one document and require it to say no. The document is built in `tmp_path` and
    # read back through the same scanner rather than written into `docs/tickets/`, because a
    # `repository`-marked case reads documents and writes none (S2) — and the scanner is the only
    # thing between the disk and the judgement, so nothing the reproduction depends on is skipped.
    filed = _filed_escape_repo(tmp_path / "qa", "todo", tid="SRV-FIX-999")
    assert filing_misses("SRV-FIX-999", filed["SRV-FIX-999"]) == [
        "no §3 escape analysis", "no row in docs/operations/ESCAPES.md"], (
        "the reproduction document does not have the two misses QA's does, so what follows would "
        "be measuring something else")

    with pytest.raises(AssertionError, match="SRV-FIX-999") as gate:
        judge_filed_and_worked({**escape_records(root), **filed}, root, ESCAPE_DEBT,
                               ESCAPE_DEBT | FILING_DEBT)
    assert "no §3 escape analysis" in str(gate.value), (
        f"the gate refused the filed escape without saying it owes a §3 heading: {gate.value}")
    assert "no row in docs/operations/ESCAPES.md" in str(gate.value), (
        f"the gate refused the filed escape without saying it owes a ledger row: {gate.value}")

    # ...and at EVERY status the template declares, plus the two the domain excuses. "Whatever
    # its status" is the criterion's own word, and a single `todo` reproduction proves it for one
    # status: the filing tier would look identical if it had simply swapped which statuses it
    # judged. `superseded` is in here on purpose — it is the other excused status, and a filing
    # obligation the backlog can outrun is not an obligation.
    for status in sorted(worked_statuses(root) | set(FILED_NOT_WORKED)):
        one = _filed_escape_repo(tmp_path / ("qa-" + status), status, tid="SRV-FIX-999")
        with pytest.raises(AssertionError, match="SRV-FIX-999"):
            judge_filed_and_worked({**escape_records(root), **one}, root, ESCAPE_DEBT,
                                   ESCAPE_DEBT | FILING_DEBT)


@pytest.mark.repository
def test_a_worked_escape_with_an_unanswered_section_3_is_refused(tmp_path):
    """Criterion 3. NEGATIVE (mandatory). The domain narrowed, the rule did not.

    The same fixture, one word different — `status: awaiting-qa` — and both refusals must name it.
    Without this, a domain that excluded every status passes criterion 1 and criterion 2 exactly
    as a correct one does: both repository cases go green, the fixture case above still refuses a
    filed ticket, and nothing anywhere is left that fails when an escape ships unanswered. That is
    this ticket's own failure mode, so it is this ticket's negative.
    """
    root = _repo_root()
    heading = question_five_heading(root)

    section = "\n".join([
        "## 3. Escape analysis", "",
        "- **What the gate asserted:** a criterion", "",
        "- **" + heading + "**", "",
        "  I read through the tree and this is the only place the cause appears.", "",
        "- **Landed in this commit:** a file", ""])
    recs = _filed_escape_repo(tmp_path, "awaiting-qa", section)

    judged, excluded = split_by_status(recs, worked_statuses(root))
    assert judged == ["FAC-FIX-901"], (
        f"a worked escape ticket was excluded from the sweep: judged={judged} excluded={excluded}")
    assert excluded == [], f"nothing should have been excluded: {excluded}"

    with pytest.raises(AssertionError, match="FAC-FIX-901") as owed:
        refuse_escape_obligations(recs, judged, frozenset())
    assert "no row in docs/operations/ESCAPES.md" in str(owed.value), (
        f"a worked escape with no ledger row was accepted, or refused for something else: "
        f"{owed.value}")

    with pytest.raises(AssertionError, match="FAC-FIX-901") as unmeasured:
        refuse_where_else(recs, judged, heading)
    assert "no fenced block" in str(unmeasured.value), (
        "a worked escape whose §3 answers the fifth question in prose was accepted, or refused "
        f"for something else: {unmeasured.value}")


# ── SRV-FIX-081 · a ticket a lane is running says so on the catalog surfaces ──────────────────
#
# The operator's two curls, one minute apart from nothing: E9 said `running`, E4 said
# `not_started` about the same ticket in the same second, because the catalog renders derived
# `process` from FINISHED runs only. These cases drive the product's own surface — E12 claims,
# real runs through the manifest's agent — and never reach into the store. The GET is always a
# different connection from the POST that changed the world (template §2: a write's second
# observer), because `Instance.get` opens a fresh connection per call.

def lane_repo(tmp_path):
    """A git repository the catalog cases can claim lanes in: nonstandard doc dirs (this file's
    rule), two workspaces, one exclusive resource, and the stand-in agent as its `agent.command`."""
    from test_lane import git
    from test_transcript import AGENT_SH

    root = tmp_path / "lanerepo"
    (root / ".factory").mkdir(parents=True)
    (root / ".factory" / "factory.json").write_text(json.dumps({
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}, "beta": {"path": "."}},
        "resources": {"bench": {"exclusive": True, "needed_by": ["beta"],
                                "description": "one bench, one run at a time"}},
        "lanes": {"host": 4},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        "stages": ["ONE", "TWO"],
        "agent": {"command": "/bin/sh agent.sh"},
    }, indent=2))
    (root / ".gitignore").write_text(".factory/worktrees/\n.factory/runs/\n")
    (root / "agent.sh").write_text(AGENT_SH)

    docs = {
        f"{REQ_DIR}/REQ-500.md": doc({"id": "REQ-500", "title": "Watched while lanes run",
                                      "status": "draft"}, "Summary."),
        f"{REQ_DIR}/REQ-501.md": doc({"id": "REQ-501", "title": "The neighbour requirement",
                                      "status": "draft"}, "Summary."),
        f"{TKT_DIR}/TKT-500.md": doc({"id": "TKT-500", "parent": "REQ-500",
                                      "workspace": "alpha", "title": "The ticket a lane runs"},
                                     "a"),
        f"{TKT_DIR}/TKT-501.md": doc({"id": "TKT-501", "parent": "REQ-500",
                                      "workspace": "alpha", "title": "Sibling, same workspace, "
                                      "no lane"}, "b"),
        f"{TKT_DIR}/TKT-502.md": doc({"id": "TKT-502", "parent": "REQ-501",
                                      "workspace": "alpha", "title": "Another requirement "
                                      "entirely"}, "c"),
        f"{TKT_DIR}/TKT-510.md": doc({"id": "TKT-510", "parent": "REQ-501",
                                      "workspace": "beta", "title": "Holds the bench"}, "d"),
        f"{TKT_DIR}/TKT-511.md": doc({"id": "TKT-511", "parent": "REQ-501",
                                      "workspace": "beta", "title": "Waits for the bench"}, "e"),
    }
    for rel, text in docs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / RPT_DIR).mkdir(parents=True, exist_ok=True)

    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "factory@test.local")
    git(root, "config", "user.name", "factory tests")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


def claim_lane(srv, ticket, workspace):
    r = srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})
    assert r.status_code == 201, r.text
    return r.json()["lane"]


def start_run(srv, lane):
    r = srv.post(f"/api/lanes/{lane}/runs", json={})
    assert r.status_code == 201, r.text
    return r.json()


def lane_row(srv, lane):
    r = srv.get("/api/lanes")
    assert r.status_code == 200, r.text
    for row in r.json()["lanes"]:
        if row["lane"] == lane:
            return row
    return None


def wait_lane_process(srv, lane, want, timeout=30):
    """A PRECONDITION wait on E9 — the fact the lane itself reports — asserted once afterwards.
    The catalog assertion that follows is then made exactly once, never retried."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = lane_row(srv, lane)
        if row is not None and row["state"]["process"] == want:
            return row
        time.sleep(0.1)
    raise AssertionError(f"{lane} never reported process={want!r} on E9 within {timeout}s; "
                         f"last row: {lane_row(srv, lane)}")


def end_run(srv, lane):
    """Close the run's stdin and wait for the reap, so no case leaks a live agent."""
    r = srv.post(f"/api/lanes/{lane}/end", json={})
    assert r.status_code == 202, r.text
    wait_lane_process(srv, lane, "finished")


def ticket_rows(body):
    return {t["id"]: t for g in body["groups"] for t in g["tickets"]}


def test_a_running_lanes_ticket_reports_running_on_its_requirement(tmp_path, serve):
    """§1 case 1 — claim a lane for T, start its run, and E4 from another connection says
    `running` and names the lane, in the same second E9 does."""
    srv = serve(lane_repo(tmp_path))
    lane = claim_lane(srv, "TKT-500", "alpha")
    start_run(srv, lane)
    wait_lane_process(srv, lane, "running")
    try:
        body = ok(srv, "/api/requirements/REQ-500")
        row = ticket_rows(body)["TKT-500"]
        assert row["state"]["process"] == "running", row["state"]
        assert row["lane"] == lane, row
        assert row["state"]["motion"] == "producing", row["state"]
    finally:
        end_run(srv, lane)


def test_a_queued_lane_reports_queued_not_not_started(tmp_path, serve):
    """§1 case 2 — the `lane/03` case: two lanes contending one exclusive resource, and the
    waiting ticket's row says `queued`, not `not_started`. No process exists yet, so the answer
    is deterministic the moment the claim returns 201."""
    srv = serve(lane_repo(tmp_path))
    holding = claim_lane(srv, "TKT-510", "beta")
    waiting = claim_lane(srv, "TKT-511", "beta")
    row = lane_row(srv, waiting)
    assert row["state"]["process"] == "queued", row

    body = ok(srv, "/api/requirements/REQ-501")
    rows = ticket_rows(body)
    assert rows["TKT-511"]["state"]["process"] == "queued", rows["TKT-511"]["state"]
    assert rows["TKT-511"]["lane"] == waiting, rows["TKT-511"]
    # the holder has a lane and no process: its row names the lane and invents no process
    assert rows["TKT-510"]["state"]["process"] == "not_started", rows["TKT-510"]["state"]
    assert rows["TKT-510"]["lane"] == holding, rows["TKT-510"]


def test_the_requirements_list_row_carries_the_live_axis(tmp_path, serve):
    """§1 case 3 — E3: the parent requirement's own `state.process` reflects the live child, so
    the board's left column cannot say nothing is happening while a lane burns."""
    srv = serve(lane_repo(tmp_path))
    lane = claim_lane(srv, "TKT-500", "alpha")
    start_run(srv, lane)
    wait_lane_process(srv, lane, "running")
    try:
        body = ok(srv, "/api/requirements")
        rows = {r["id"]: r for r in body["requirements"]}
        assert rows["REQ-500"]["state"]["process"] == "running", rows["REQ-500"]["state"]
        # the neighbour requirement has no live child and keeps today's answer
        assert rows["REQ-501"]["state"]["process"] == "not_started", rows["REQ-501"]["state"]
    finally:
        end_run(srv, lane)


def test_a_finished_run_still_reports_its_outcome(tmp_path, serve):
    """§1 case 4 — run ends cleanly, lane released: the row says `finished` off the existing
    OutcomeLookup path. The new join must not shadow the old one once the lane is gone.
    (Green before the fix by design — it pins the path the fix must not break.)"""
    srv = serve(lane_repo(tmp_path))
    lane = claim_lane(srv, "TKT-500", "alpha")
    start_run(srv, lane)
    wait_lane_process(srv, lane, "running")
    end_run(srv, lane)
    released = srv.delete(f"/api/lanes/{lane}")
    assert released.status_code == 200, released.text
    assert lane_row(srv, lane) is None, "the lane must be out of the table for this case"

    body = ok(srv, "/api/requirements/REQ-500")
    row = ticket_rows(body)["TKT-500"]
    assert row["state"]["process"] == "finished", row["state"]
    assert row["lane"] is None, row


def test_a_live_lane_does_not_leak_onto_its_neighbours(tmp_path, serve):
    """§1 NEGATIVE (mandatory) — with T running, a sibling in the SAME requirement and SAME
    workspace with no lane stays `not_started`, and a ticket in ANOTHER requirement stays
    `not_started`. Break-it check: key the lookup by workspace instead of ticket id and this
    goes red — TKT-501 shares TKT-500's workspace precisely so that leak has a witness."""
    srv = serve(lane_repo(tmp_path))
    lane = claim_lane(srv, "TKT-500", "alpha")
    start_run(srv, lane)
    wait_lane_process(srv, lane, "running")
    try:
        same = ticket_rows(ok(srv, "/api/requirements/REQ-500"))
        assert same["TKT-501"]["state"]["process"] == "not_started", same["TKT-501"]["state"]
        assert same["TKT-501"]["lane"] is None, same["TKT-501"]

        other = ticket_rows(ok(srv, "/api/requirements/REQ-501"))
        assert other["TKT-502"]["state"]["process"] == "not_started", other["TKT-502"]["state"]
        assert other["TKT-502"]["lane"] is None, other["TKT-502"]

        listing = {r["id"]: r for r in ok(srv, "/api/requirements")["requirements"]}
        assert listing["REQ-501"]["state"]["process"] == "not_started", listing["REQ-501"]["state"]
    finally:
        end_run(srv, lane)


@pytest.mark.repository
def test_a_ticket_describes_no_workspace_but_its_own():
    """EPIC-003 and EPIC-004's rule, made checkable.

    `workspace:` is the field the tool groups by, so a ticket that declares one workspace and then
    describes a fix in two is lying to the grouping — the same defect as `SRV-FIX-021`, one field
    over. `WEB-FEAT-085` filed nine tickets and three of them did it: `SRV-FIX-093` declared
    `factory-server` and its Layers line said *"factory-server for E9's total; factory-client for
    the length match"*; `WEB-FIX-094` declared `factory-client` and named the spec, the server and
    the client. Both were split into epics with one child per workspace.

    A fix that genuinely needs two workspaces is not a ticket. It is an epic whose first deliverable
    is the frozen contract between them, and whose gate owns the journey neither child can assert.

    No server and no fixture: a statement about documents, and the documents are the state.
    """
    import re

    root = _repo_root()
    # every ticket directory this monorepo keeps, DECLARED rather than found: the glob is
    # an assertion now, not an input (SRV-FIX-181 §3). A ticket's workspaces are its own
    # manifest's (SRV-FEAT-244).
    dirs = declared_ticket_dirs(root)

    offenders, checked = [], 0
    for d, manifest in dirs.items():
        declared = set(manifest["workspaces"])
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue
            body = f.read_text()
            head = body.split("\n---", 2)
            front = head[0][4:] if head[0].startswith("---\n") and len(head) > 1 else ""
            keys = dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, re.M))
            mine = (keys.get("workspace") or "").strip()
            if mine not in declared:
                continue                      # the sibling test above owns that failure
            checked += 1

            # The Layers bullet is where a ticket says which code it will touch. Read only that
            # bullet: prose elsewhere legitimately names other workspaces to explain a seam, and
            # a ticket that says "this half is WEB-FIX-098's" is doing the right thing.
            m = re.search(r"^-\s+\*\*Layers:\*\*(.+?)(?=^-\s+\*\*|\Z)", body, re.M | re.S)
            if not m:
                continue                      # not every document carries one; not this test's call
            layers = m.group(1)
            # a sentence that hands the other half to a named ticket is the split working
            layers = re.sub(r"`(?:FAC|SRV|WEB)-[A-Z]+-\d+`[^.]*\.", "", layers)
            others = {w for w in declared if w != mine and re.search(rf"`{re.escape(w)}`", layers)}
            if others:
                offenders.append(f"{f.name}: declares `{mine}`, Layers also names {sorted(others)}")

    # The debt this rule found on the day it was written, enumerated rather than hidden. Twelve
    # factory tickets and two Acme ones already carry a `pass`, and a rule invented today may not
    # retroactively fail work that shipped under yesterday's rule. So they are named here, and the
    # list has exactly two properties: **it may only shrink**, and **a new ticket may never join
    # it**. A silent cap would read as "this rule is satisfied"; a named one reads as a queue.
    GRANDFATHERED = {
        "FIX-075-fw-ble-prov-app-compat.md",
        "FIX-078-fw-prov-stop-then-start-aborts.md",
        "SRV-FEAT-001-bootstrap.md",
        "SRV-FEAT-005-socket-contract.md",
        "SRV-FEAT-028-what-actually-happened.md",
        "WEB-FEAT-041-standalone-tickets-visible.md",
        "SRV-FEAT-065-qa-runs-where-it-can-be-seen.md",
        "WEB-FIX-035-gate-cannot-reach-terminal-run-states.md",
        "SRV-FIX-038-a-ticket-id-matches-any-process-on-the-machine.md",
        "SRV-FIX-054-a-second-run-in-a-lane-freezes-the-transcript.md",
        "SRV-FIX-059-a-lane-blocked-by-its-own-leftovers-has-no-way-out.md",
        "WEB-FIX-067-a-patched-entry-is-drawn-twice-from-a-snapshot.md",
        "SRV-FIX-076-a-typed-prompt-bypasses-the-guard-that-refuses-a-ticket-the-branch-does-not-have.md",
    }

    assert checked, "no ticket carried a declared workspace"

    fresh = [o for o in offenders if o.split(":")[0] not in GRANDFATHERED]
    assert not fresh, (
        "a ticket declares one workspace and describes another; split it into an epic with one "
        "child per workspace and freeze the contract between them first (EPIC-003 and EPIC-004 are "
        "the worked examples):\n  " + "\n  ".join(fresh))

    # The list may only shrink. A name that no longer offends must leave it, or the exemption
    # outlives the debt and quietly re-opens the hole for a file that already got fixed.
    still = {o.split(":")[0] for o in offenders}
    stale = sorted(GRANDFATHERED - still)
    assert not stale, (
        "these are no longer offenders and must be removed from GRANDFATHERED — an exemption that "
        "outlives its debt is a hole:\n  " + "\n  ".join(stale))


# ── SRV-FEAT-157 · a criterion may not quantify over a domain it does not enumerate ───────────
#
# Six QA FAILs and zero passes in one week, every round individually correct. The cause is a
# quantifier mismatch: §1 states a universal, a test discharges it existentially, and `qa-ticket`
# step 8 samples the domain — so any sample outside the covered region is a counterexample to the
# criterion rather than a finding beside it, and the domain is unbounded.
#
# WEB-FIX-101 is the measurement. Round 1: criterion 1 FAIL, from the exits above `trap cleanup
# EXIT`. Round 2 moved the trap; criterion 1 FAIL again, from a SIGTERM'd gate exiting 143. Two
# rounds, two true counterexamples, one criterion, no closure. SRV-FIX-059's round-5 verdict says
# the same thing about itself: "each round has closed the clause the previous verdict named while
# the sentence around it stayed unasserted."
#
# These cases are enumerated on purpose. There is deliberately NO case here asserting that every
# ticket's criteria are bounded — that is the unbounded shape this rule forbids, and asserting it
# would make this ticket unclosable by its own mechanism.

def _prose(path):
    """A document's words with its line breaks removed.

    These assertions are about what a document SAYS, and a sentence in a wrapped markdown file is
    split across lines at whatever column it happened to reach. Asserting on the raw text makes the
    case depend on the author's reflow rather than on the rule — which is a criterion that fails for
    a reason nobody meant, the mirror image of one that passes for a reason nobody meant.
    """
    import re
    return re.sub(r"\s+", " ", path.read_text()).lower()


@pytest.mark.repository
def test_the_template_forbids_a_criterion_that_quantifies_without_enumerating():
    """§1's rule, and the half of it that makes the rule survive contact with QA."""
    lowered = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "enumerate" in lowered, "_TEMPLATE.md §1 does not carry the quantifier rule at all"
    assert "new ticket" in lowered and "member" in lowered, (
        "the rule is stated without its second half. A class list that still fails when a new "
        "member turns up is the same unbounded loop with extra words: the rule only works if a "
        "member discovered later is a NEW TICKET rather than a failure of the one that named it.")


@pytest.mark.repository
def test_the_template_requires_a_negation_criterion_to_list_its_members():
    """SRV-FIX-292's §3 — the quantifier rule in the form where the omission is invisible.

    `SRV-FIX-290`'s criterion 2 read *"a manifest root with no `.git` above it anywhere"*. Its
    domain had three members — `.git` absent, `.git` a FILE naming its repository, `.git` a
    directory that is not a repository — and every fixture used `git init`, so only the first was
    ever built. The sentence quantifies over all three and enumerates none, and it does not look
    like it does: a negation reads as one case. The tool refused every lane inside a worktree the
    day after that ticket merged.

    So the template says it separately, and says what the mandatory negative owes.
    """
    lowered = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "negation" in lowered, (
        "_TEMPLATE.md §1 does not carry the negation rule. Without it the quantifier rule above is "
        "read as being about the word `every`, and a criterion whose domain is written as `no X "
        "anywhere` passes it while enumerating nothing.")
    assert "mandatory negative covers more than one member" in lowered, (
        "the rule is stated without the half that can be checked. A ticket obeys it by listing "
        "members in prose and then testing the one member it already had a fixture for, which is "
        "exactly what SRV-FIX-290 did — the negative has to reach more than one of them.")


@pytest.mark.repository
def test_the_qa_skill_sends_an_unbounded_break_finding_to_a_new_ticket():
    """Step 8's findings become escapes, not vetoes — which is what they already are elsewhere."""
    lowered = _prose(_repo_root() / ".claude" / "skills" / "qa-ticket" / "SKILL.md")
    assert "enumerate" in lowered, "the qa skill never mentions enumeration"
    assert "does not fail the criterion" in lowered or "not a failure of that criterion" in lowered, (
        "the skill does not say what to DO with a break finding that contradicts a criterion the "
        "ticket never enumerated. Without that sentence the finding still lands on the criterion "
        "and the loop is unchanged.")


@pytest.mark.repository
def test_the_workflow_names_the_quantifier_rule_beside_the_escape_rule():
    """It only works if a break finding becomes an escape, so it belongs where escapes are."""
    lowered = _prose(_repo_root() / "docs" / "TICKET-WORKFLOW.md")
    assert "escaped_from" in lowered, "the workflow no longer explains escapes at all"
    # NOT a bare "enumerate" check: line 187 already asks, as a post-mortem after two failures,
    # whether "§1 never enumerated its input space". The repository has had the diagnosis all
    # along and only downstream of the loop. What is new is the rule at AUTHORING time, so the
    # assertion is on that, and a substring satisfied by the existing sentence would be vacuous.
    assert "when a criterion is written" in lowered or "writing a criterion" in lowered, (
        "TICKET-WORKFLOW.md discusses enumeration only in the two-failure analysis. The rule has "
        "to reach the moment a criterion is WRITTEN, or it stays a post-mortem for a loop that "
        "has already cost its rounds.")
    assert "escape" in lowered and "member" in lowered, (
        "the workflow does not connect a later-found member to the escape machinery, which is the "
        "half that stops it being a veto.")


@pytest.mark.repository
def test_har_fix_101_criterion_1_names_its_four_exit_paths():
    """The worked example, applied to the case that produced the measurement."""
    import glob
    hits = glob.glob(str(_this_repository() / "docs" / "tickets" / "WEB-FIX-101-*.md"))
    assert hits, "WEB-FIX-101 is not in this tree"
    from pathlib import Path
    text = _prose(Path(hits[0]))
    for member in ("lock", "pre-trap", "build", "signal"):
        assert member in text, (
            f"WEB-FIX-101 §1 does not name '{member}' among its exit paths. Its criterion 1 is the "
            "universal this ticket exists to bound, and it stays unbounded until the four members "
            "QA found are written down.")


@pytest.mark.repository
def test_a_criterion_naming_one_scenario_is_not_flagged():
    """NEGATIVE (mandatory). The rule asks nothing of a criterion that never quantified.

    `SRV-FIX-105` passed on its first QA round with "removing a lane record kills the process it
    started" — one scenario, one observable, correct as written. A rule that demanded a class list
    from it would make every passing ticket in this repository non-compliant, which is how a
    process rule turns into paperwork. Asserted against the real ticket, in the tree.
    """
    import glob
    hits = glob.glob(str(_this_repository() / "docs" / "tickets" / "SRV-FIX-105-*.md"))
    assert hits, "SRV-FIX-105 is not in this tree"
    text = open(hits[0]).read()
    assert "test_removing_a_lane_record_kills_the_process_it_started" in text
    template = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "one scenario" in template or "names one scenario" in template, (
        "the template states the rule without its escape hatch. A criterion that names ONE "
        "scenario is complete as it stands; only a criterion that says *every* owes a class list.")


@pytest.mark.repository
def test_the_mandatory_negative_rule_still_stands():
    """NEGATIVE (mandatory). The cheap reading of this ticket is 'criteria should ask for less'.

    That is the opposite of its point. A bounded criterion asks for MORE per member, and the
    mandatory negative is what stops a class list becoming a list of easy members. Red if the
    requirement is weakened or removed.
    """
    lowered = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "negative test" in lowered and "mandatory" in lowered, (
        "_TEMPLATE.md no longer requires a mandatory negative per layer block")
    assert 'stays green when the thing it guards is deleted is not one' in lowered, (
        "_TEMPLATE.md's definition of a real mandatory negative is gone. That sentence is what "
        "stops a class list becoming a list of easy members, and this ticket makes class lists "
        "the normal shape of a criterion.")


# ── SRV-FIX-158 · a superseded rule comes back when the branch that holds it merges ───────────
#
# SRV-FEAT-157 replaced "a criterion quantified over a class of events is asserted at two points in
# that class, never one" with "a criterion may not quantify over a domain it does not enumerate",
# and said so only in its own §1 prose. The superseded rule is alive on ticket/WEB-FIX-101 as a
# HARD standing rule in _TEMPLATE.md §2, and is owed a second time by ESCAPES.md's row and by
# WEB-FIX-155 §1 — so it can arrive by two routes.
#
# Merging that branch puts two hard rules about one question into _TEMPLATE.md, disagreeing: assert
# a universal at two points, against never state a universal you have not enumerated. SRV-FEAT-157
# exists because two points do not bound a universal — WEB-FIX-101 asserted two and failed on the
# third. Nothing in the suite sees the contradiction, so the merge stays green.
#
# These fire on main's side, at integration, which is where the two texts actually meet.

@pytest.mark.repository
def test_the_two_points_rule_is_not_in_the_template():
    """Red the moment a merge brings the dead rule back, whichever branch carries it."""
    template = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "asserted at two points in that class" not in template, (
        "_TEMPLATE.md carries the rule SRV-FEAT-157 superseded. Two points do not bound a "
        "universal — WEB-FIX-101 asserted two and failed on the third, which is why the rule was "
        "replaced rather than kept alongside. Remove it; the replacement is the quantifier rule.")


def _escapes_row_owing_two_points():
    """THE ROW criterion 2 names, not the file that contains it.

    Round 2 asserted `"srv-feat-157" in <the whole of ESCAPES.md>`. That string sits in three
    unrelated rows, two of them written by SRV-FEAT-157's own DELIVER commit BEFORE this ticket
    existed — so the assertion was green on the pre-fix state and the named row could lose its
    annotation without turning anything red. A criterion about a row is asserted against that row.
    """
    for line in (_this_repository() / "docs/operations/ESCAPES.md").read_text().splitlines():
        if "quantified over every non-zero exit" in line:
            return line
    raise AssertionError("the 2026-08-13 row that owed the two-points change is gone from ESCAPES.md")


@pytest.mark.repository
def test_the_escapes_row_that_owed_the_two_points_rule_names_its_successor():
    """Criterion 2, member 1 of 2 — its own case, so it cannot ride a sibling's redness."""
    row = _escapes_row_owing_two_points().lower()
    assert "supersed" in row, (
        "the row that owed 'assert at two points' does not say it was superseded. A session "
        "settling that debt reads the row, not the file.")
    assert "srv-feat-157" in row, (
        "the row does not name what replaced the owed change. `SRV-FEAT-157` appearing elsewhere "
        "in ESCAPES.md is not this row saying it — that is the defect round 2 shipped.")


@pytest.mark.repository
def test_web_fix_155_names_its_successor_and_no_longer_asks_for_the_dead_rule():
    """Criterion 2, member 2 of 2 — and F4, which the file-level assertion could not see.

    Criterion 2's purpose clause is *"so the next session to read either one does not land it"*.
    Round 2 added a correct annotation and left the instruction beneath it intact: `not yet landed`
    and `not done until _TEMPLATE.md carries the new §2 rule` both survived four lines below.
    """
    import glob
    from pathlib import Path
    hits = glob.glob(str(_this_repository() / "docs" / "tickets" / "WEB-FIX-155-*.md"))
    assert hits, "WEB-FIX-155 is not in this tree"
    text = _prose(Path(hits[0]))
    assert "srv-feat-157" in text, "WEB-FIX-155 §1 does not name what replaced the change it owes"
    assert "not yet landed" not in text, (
        "WEB-FIX-155 still tells its next reader the superseded rule is owed and unlanded. The "
        "annotation says one thing and the instruction under it says the other; a session picking "
        "the ticket up follows the instruction.")


@pytest.mark.repository
def test_the_quantifier_rule_is_still_in_the_template():
    """NEGATIVE (mandatory). The cheap fix is to delete both sentences and call it resolved.

    Deleting the contradiction is not resolving it: SRV-FEAT-157's rule is the one that survives,
    and a template with neither has lost the thing a week of failed rounds bought.
    """
    template = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "may not quantify over a domain it does not enumerate" in template, (
        "_TEMPLATE.md no longer carries SRV-FEAT-157's rule. Removing the superseded sentence must "
        "not take its replacement with it.")


@pytest.mark.repository
def test_the_template_requires_a_supersession_to_be_asserted_absent():
    """SRV-FIX-158 §3's structural change, in the commit that owes it.

    QA round 1 failed this ticket for landing §3 in prose and not in a document — reproducing, one
    level up, the exact defect it was filed to fix. This is the line, and this is the case that
    stops the line itself being described rather than present.
    """
    template = _prose(_repo_root() / "docs" / "tickets" / "_TEMPLATE.md")
    assert "asserted absent" in template, (
        "_TEMPLATE.md does not require a superseded rule to be asserted ABSENT. Every other rule "
        "here reads a document for text it gained; a supersession is the opposite shape and is "
        "complete only when the old sentence is gone from every branch that reaches main.")
    # ANCHORED to the rule's own block. `same commit` occurs at three places in this file that
    # predate the ticket, so asserting it file-wide cannot fail (SRV-FIX-158 QA round 2, F2).
    lines = (_repo_root() / "docs" / "tickets" / "_TEMPLATE.md").read_text().splitlines()
    start = next((i for i, l in enumerate(lines)
                  if "SUPERSEDES IS ASSERTED ABSENT" in l), None)
    assert start is not None, "the supersession rule's heading is gone"
    block = " ".join(lines[start:start + 12]).lower()
    assert "same commit" in block, (
        "the rule does not say WHEN the assertion lands, in its own words. A supersession asserted "
        "later is the deferral this ticket exists to stop — and `same commit` elsewhere in this "
        "file is not this rule saying it.")


# ── SRV-FIX-181 · the tool reads every repository the manifest declares ═══════════════════════
#
# A monorepo is a set of repositories. Each has a path, a documents tree of its own and the
# workspaces that belong to it, and the tool is GIVEN that structure — it discovers nothing and
# defaults nothing. Before this ticket the manifest could name one documents root, so a repository
# keeping a second tree had every document in it invisible: served by no endpoint, watched by no
# scan, and claimable by no lane.

SUB = "second"
DOCS_OF = {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"}


def two_repo_manifest(workspaces=None):
    return {
        "factory": "0.1",
        "repos": {
            "first": {"path": ".", "docs": dict(DOCS_OF)},
            "second": {"path": SUB, "docs": dict(DOCS_OF)},
        },
        "workspaces": workspaces or {
            "alpha": {"repo": "first", "path": "."},
            "beta": {"repo": "second", "path": "."},
        },
    }


def two_repo_documents(repo):
    """One epic and one ticket in each repository, and a report for the second repo's ticket."""
    repo.write("docs/epics/AAA-000.md",
               doc({"id": "AAA-000", "title": "The first repository's epic", "status": "draft"},
                   "One."))
    repo.write("docs/tickets/AAA-001.md",
               doc({"id": "AAA-001", "parent": "AAA-000", "workspace": "alpha",
                    "title": "A ticket of the first repository", "status": "todo"}, "Body."))
    repo.write(f"{SUB}/docs/epics/BBB-000.md",
               doc({"id": "BBB-000", "title": "The second repository's epic", "status": "active"},
                   "Two."))
    repo.write(f"{SUB}/docs/tickets/BBB-002.md",
               doc({"id": "BBB-002", "parent": "BBB-000", "workspace": "beta",
                    "title": "A ticket of the second repository", "status": "todo"}, "Body."))
    repo.mkdir("docs/reports")
    repo.mkdir(f"{SUB}/docs/reports")


def test_a_manifest_declaring_two_repos_lists_tickets_from_both(repo, serve):
    """The measurement in §1, as an assertion: a workspace the manifest declares has tickets the
    tool can open, whichever repository they live in."""
    two_repo_documents(repo)
    srv = serve(repo.manifest(two_repo_manifest()))

    first = ok(srv, "/api/tickets/AAA-001")
    second = ok(srv, "/api/tickets/BBB-002")
    assert first["id"] == "AAA-001" and first["workspace"] == "alpha", first
    assert second["id"] == "BBB-002" and second["workspace"] == "beta", second
    assert second["path"].endswith(f"{SUB}/docs/tickets/BBB-002.md"), second["path"]


def test_a_requirement_list_spans_every_declared_repo(repo, serve):
    """E3 reduces over every declared repository, not over the first one it was given.

    The two epics disagree on every field the listing reduces — status, ticket count, and the
    delivery state a report with no run produces — so a reduction that read only one repo is red
    rather than accidentally right (`SRV-FIX-166`).
    """
    two_repo_documents(repo)
    # a second ticket for the first repo's epic, and a report claiming work with no run cited:
    # the first row is `2 tickets, one of them needing a person`, the second is `1, needing none`.
    repo.write("docs/tickets/AAA-003.md",
               doc({"id": "AAA-003", "parent": "AAA-000", "workspace": "alpha",
                    "title": "A second ticket of the first repository", "status": "todo"}, "B."))
    repo.write("docs/reports/AAA-003-report.md", "# AAA-003 — done\n\nNo run is cited here.\n")
    srv = serve(repo.manifest(two_repo_manifest()))

    body = ok(srv, "/api/requirements?filter=all")
    rows = {r["id"]: r for r in body["requirements"]}
    assert body["total"] == 2, body
    assert sorted(rows) == ["AAA-000", "BBB-000"], body

    assert rows["AAA-000"]["status"] == "draft", rows["AAA-000"]
    assert rows["BBB-000"]["status"] == "active", rows["BBB-000"]
    assert rows["AAA-000"]["ticket_count"] == 2, rows["AAA-000"]
    assert rows["BBB-000"]["ticket_count"] == 1, rows["BBB-000"]
    assert rows["AAA-000"]["needs_you"]["count"] == 1, rows["AAA-000"]
    assert rows["BBB-000"]["needs_you"]["count"] == 0, rows["BBB-000"]
    assert rows["AAA-000"]["workspaces"] == ["alpha"], rows["AAA-000"]
    assert rows["BBB-000"]["workspaces"] == ["beta"], rows["BBB-000"]


def test_a_report_is_written_beside_the_ticket_it_reports_on(repo, serve):
    """A report belongs to the repository that holds the ticket, not to the first repo declared."""
    two_repo_documents(repo)
    repo.write(f"{SUB}/docs/reports/BBB-002-report.md",
               "# BBB-002 — delivered\n\nThe second repository's own report.\n")
    # the same name in the FIRST repo's reports directory, so a resolver that ignores the ticket's
    # repository answers 200 with the wrong document instead of failing to find one.
    repo.write("docs/reports/BBB-002-report.md",
               "# BBB-002 — the wrong repository's report\n\nThis one must never be served.\n")
    srv = serve(repo.manifest(two_repo_manifest()))

    body = ok(srv, "/api/tickets/BBB-002/report")
    assert body["path"] == str(repo.real / SUB / "docs" / "reports" / "BBB-002-report.md"), body
    assert "second repository's own report" in body["body_markdown"], body
    assert "wrong repository" not in body["body_markdown"], body

    ticket = ok(srv, "/api/tickets/BBB-002")
    assert ticket["report"]["path"] == body["path"], ticket["report"]


def test_a_document_changing_in_the_second_repo_pushes_catalog_changed(repo, serve):
    """Every declared repo is WATCHED, or none of it is contract.

    `catalog/store.cpp` registered exactly three directories, so a second repository would have
    been read once at start-up and never again — a screen that goes quietly stale, which is worse
    than one that never loaded.
    """
    two_repo_documents(repo)
    srv = serve(repo.manifest(two_repo_manifest()))
    assert ok(srv, "/api/tickets/BBB-002")["id"] == "BBB-002"

    c = srv.ws()
    hello = c.recv()
    assert hello["m"] == "hello", hello
    c.send({"m": "subscribe", "scope": {"ticket": "BBB-002"}})
    assert c.wait_for("snapshot")["scope"] == {"ticket": "BBB-002"}
    try:
        repo.write(f"{SUB}/docs/tickets/BBB-002.md",
                   doc({"id": "BBB-002", "parent": "BBB-000", "workspace": "beta",
                        "title": "A ticket of the second repository", "status": "in-progress"},
                       "Edited body."))
        frame = c.wait_for("catalog.changed", timeout=10)
        assert frame["kind"] == "ticket" and frame["id"] == "BBB-002", frame
        assert frame["change"] == "modified", frame
        assert frame["path"] == str(repo.real / SUB / "docs" / "tickets" / "BBB-002.md"), frame
    finally:
        c.close()


def test_one_id_in_two_repos_is_refused_and_names_both(repo, serve):
    """NEGATIVE (mandatory) — an ambiguous id is not a ticket.

    Watched RED against an implementation that returns the first match: first-wins is the cheap
    implementation of a collection, and it serves the wrong document forever without ever saying
    so. The refusal names both files, because the only useful thing to do about it is delete one.
    """
    two_repo_documents(repo)
    for where in ("docs/tickets", f"{SUB}/docs/tickets"):
        repo.write(f"{where}/FIX-009.md",
                   doc({"id": "FIX-009", "workspace": "alpha", "title": f"claimed by {where}",
                        "status": "todo"}, "Body."))
    srv = serve(repo.manifest(two_repo_manifest()))

    r = srv.get("/api/tickets/FIX-009")
    assert r.status_code == 409, (
        f"an id claimed by two repositories was served as a ticket: {r.status_code} {r.text[:400]}")
    err = r.json()["error"]
    assert err["code"] == "ambiguous_ticket_id", err
    assert sorted(err["detail"]["paths"]) == sorted([
        str(repo.real / "docs" / "tickets" / "FIX-009.md"),
        str(repo.real / SUB / "docs" / "tickets" / "FIX-009.md")]), err


# ── SRV-FEAT-244 · the screen shows the repository under management, not the tool ═════════════
#
# `.factory/factory.json` declared two repositories, so `GET /api/requirements` answered about the
# roti machine AND about the factory being used to look at it — six of eleven rows, and every
# filter, count and `needs you` total mixing the two.
#
# The cause is one declaration and so is the fix: this repository's manifest declares Acme and
# nothing else, and `factory/.factory/factory.json` — a manifest pointing at the factory — is what
# a server instance aimed at the factory reads instead. `SRV-FIX-181` exists because no `FAC-*`
# ticket could be claimed in a lane, so the second manifest is not a nicety: it is the half that
# keeps 181's property alive, and it is asserted here by starting a real server against it.
#
# This SUPERSEDES `test_this_repository_declares_the_repo_its_factory_tickets_live_in`, which
# asserted the opposite — that `factory/docs/tickets` is reachable through one of THIS manifest's
# declared repos. The rule it carried is not lost, it moved: the tool must be able to serve the
# ticket that fixed the blocker, and the case that says so now points at the factory's own manifest.

def test_the_screen_lists_only_the_repository_under_management(api):
    """§1 criterion 1 — E3 driven against this repository's real manifest.

    On the IDS the endpoint returns, never on a count. A count is green for the wrong reason the
    day somebody adds an epic, and it cannot say WHICH rows left.
    """
    r = api.get("/api/requirements?filter=all")
    assert r.status_code == 200, r.text[:400]
    ids = [row["id"] for row in r.json()["requirements"]]

    tool = [i for i in ids if i.startswith("FAC-")]
    assert not tool, (
        "the requirements screen of the repository under management is answering about the tool "
        f"being used to look at it: {tool}\nevery row: {ids}")

    for wanted in ("EPIC-001", "EPIC-002", "EPIC-003"):
        assert wanted in ids, (
            f"{wanted} is the work this screen exists for and it is not on it: {ids}")


def test_the_standalone_group_holds_only_this_repositorys_loose_tickets(api):
    """§1 consequence 3 — the synthetic row mixed both repositories' orphans.

    The count line and the rows are ONE assertion: two facts that agree by construction cannot
    see a screen that contradicts itself (`WEB-FIX-091`).
    """
    detail = ok(api, "/api/requirements/standalone")
    ids = [t["id"] for g in detail["groups"] for t in g["tickets"]]
    assert ids, "this repository has loose tickets; the group answered with none"

    tool = [i for i in ids if i.startswith("FAC-")]
    assert not tool, f"the standalone group still carries the tool's own loose tickets: {tool}"

    row = [r for r in ok(api, "/api/requirements?filter=all")["requirements"]
           if r["id"] == "standalone"]
    assert row, "the standalone row left the list while the group still answers"
    assert row[0]["ticket_count"] == len(ids), (
        f"the row counts {row[0]['ticket_count']} and the group draws {len(ids)}: {ids}")


@pytest.mark.repository
def test_a_ticket_of_the_undeclared_repo_is_not_reachable(serve):
    """§1 criterion 2 — and it is about the DECLARATION, not about a missing file.

    `SRV-FEAT-065`'s document is on disk, asserted here first: without that the 404 would be the
    ordinary answer for an id nobody wrote, and the case would pass in a repository that never had
    a factory in it.
    """
    held = sorted((_this_repository() / "docs" / "tickets").glob("SRV-FEAT-065-*.md"))
    assert held, ("SRV-FEAT-065's document is not on disk, so a 404 for it says nothing about "
                  "which repositories this manifest declares")

    enclosing, why = the_enclosing_monorepo_or_why_not()
    if enclosing is None:
        pytest.skip(
            "cannot be answered from here — " + why
            + f"; this rule is about the repository above {_this_repository()}, which is not "
              "readable from a lane: S4 binds a lane to the repository it is a lane of and "
              "nothing over it. Exact from the developer's checkout, where both are readable.")

    srv = serve(enclosing)
    r = srv.get("/api/tickets/SRV-FEAT-065")
    assert r.status_code == 404, f"{r.status_code} {r.text[:400]}"
    err = r.json()["error"]
    assert err["code"] == "not_found", err
    assert "SRV-FEAT-065" in err["message"], (
        f"the refusal does not name the ticket that was asked for: {err}")
    assert str(_this_repository()) not in r.text, (
        "the refusal names a path inside the repository this manifest no longer declares, so the "
        f"tool is still reaching into it: {r.text[:400]}")


def test_a_second_declared_repo_is_still_read(repo, serve):
    """§1 NEGATIVE (mandatory) — the tool's ability to read two repositories is untouched.

    What this ticket changes is THIS repository's declaration. The factory is pointed at monorepos
    other than this one, and a fix that narrowed the tool instead of the declaration would be
    invisible here and fatal there.

    Break the fixture to one repo — drop `second` from `two_repo_manifest()` — and every assertion
    below goes red: the second repository's epic leaves E3 and its ticket stops resolving.
    """
    two_repo_documents(repo)
    srv = serve(repo.manifest(two_repo_manifest()))

    listing = ok(srv, "/api/requirements?filter=all")
    ids = sorted(row["id"] for row in listing["requirements"])
    assert ids == ["AAA-000", "BBB-000"], (
        f"a manifest declaring two repositories listed {ids}; both repositories' requirements are "
        f"rows on the screen of a repository that declares both")
    assert listing["total"] == 2, listing

    first = ok(srv, "/api/tickets/AAA-001")
    second = ok(srv, "/api/tickets/BBB-002")
    assert first["id"] == "AAA-001" and first["workspace"] == "alpha", first
    assert second["id"] == "BBB-002" and second["workspace"] == "beta", second
    assert second["path"].endswith(f"{SUB}/docs/tickets/BBB-002.md"), second["path"]


def test_the_factory_has_a_manifest_of_its_own_that_serves_its_tickets(serve):
    """The other half of §1's pre-decision, asserted at the RECEIVER rather than on disk.

    Taking the factory off the product's screen must not take it away from the tool — that is the
    blocker `SRV-FIX-181` was filed for, and a manifest that merely EXISTS is the shape of evidence
    that has let three defects through here. So this starts a real server against
    `factory/.factory/factory.json` and asks it for the documents.

    The two instances are asserted apart in both directions: this one serves `FAC-*` and lists no
    `EPIC-*`, and `test_the_screen_lists_only_the_repository_under_management` is the mirror.
    """
    from conftest import REPO_ROOT

    srv = serve(REPO_ROOT / "factory")

    served = srv.get("/api/manifest")
    assert served.status_code == 200, (
        f"the factory's own manifest does not load ({served.status_code}): {served.text[:600]}")

    ticket = ok(srv, "/api/tickets/SRV-FEAT-065")
    assert ticket["id"] == "SRV-FEAT-065", ticket
    assert ticket["workspace"] == "factory-server", (
        "the factory's manifest declares workspaces under other names, so every FAC-* ticket "
        f"groups as `unassigned` on it: {ticket['workspace']!r}")

    ids = [row["id"] for row in ok(srv, "/api/requirements?filter=all")["requirements"]]
    assert "FAC-EPIC-001" in ids, f"the factory's own epics are not on its own screen: {ids}"
    product = [i for i in ids if i.startswith("EPIC-")]
    assert not product, (
        f"the instance pointed at the factory is answering about the product too: {product}")


TICKET_EVIDENCE = "SRV-FIX-532"


def digest_of(path):
    """sha256 of a file's bytes, hex — the only thing byte-identity can be argued from."""
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def the_newest_prompt_capture(evidence_dir):
    """`(path, {this, enclosing, identical})` for the newest capture, or `(None, why)`.

    A lane cannot read the repository this one is cloned into, so the byte comparison cannot be
    made here. What CAN be made here is a judgement about a capture somebody made where both were
    readable — and a capture is only worth anything while it still describes the bytes on disk, so
    every field it records is read back and checked against them rather than believed.

    The shape is four lines and it is the operator's, not this file's:

        branch tip: <sha>
        this repository: .factory/prompts/qa-ticket.md sha256 <hex>
        enclosing repository: ../.factory/prompts/qa-ticket.md sha256 <hex>
        cmp: identical
    """
    import re as _re

    captures = sorted(Path(evidence_dir).glob("*prompt-files-identical*.log"),
                      key=lambda f: (f.stat().st_mtime, f.name))
    if not captures:
        return None, (f"no capture under {evidence_dir} matches *prompt-files-identical*.log, so "
                      "nothing here has ever seen the second copy")
    newest = captures[-1]
    text = newest.read_text()
    mine = _re.search(r"^this repository:.*sha256\s+([0-9a-f]{64})", text, _re.M)
    theirs = _re.search(r"^enclosing repository:.*sha256\s+([0-9a-f]{64})", text, _re.M)
    if not mine or not theirs:
        return None, f"{newest} records no pair of sha256 lines, so it argues nothing"
    return newest, {"this": mine.group(1), "enclosing": theirs.group(1),
                    "identical": _re.search(r"^cmp:\s*identical\s*$", text, _re.M) is not None}


@pytest.mark.repository
def test_the_two_manifests_prompt_files_are_the_same_text():
    """Two manifests, one process — and the prompt a lane starts with may not fork.

    `.factory/prompts/` is where this monorepo says what starting a lane MEANS, and the second
    manifest cannot point at the first one's copy: `resolveUnder_` refuses a path that leaves the
    repository it is declared in. So the file is duplicated, and a duplicated rule is how this
    repository has lost rules before — each fix repairs the copy somebody noticed. Byte-equality
    is the only thing that makes the second copy answerable to the first.
    """
    mine_root = _this_repository()
    theirs_root, why = the_enclosing_monorepo_or_why_not()
    if theirs_root is None:
        mine = mine_root / ".factory" / "prompts" / "qa-ticket.md"
        assert mine.is_file(), f"the factory's own manifest declares qa-ticket.md and there is none at {mine}"
        here = digest_of(mine)

        evidence = mine_root / "docs" / "reports" / TICKET_EVIDENCE
        capture, found = the_newest_prompt_capture(evidence)
        assert capture is not None, (
            "this placement cannot read the second copy — " + why
            + f"; and {found}. A criterion about two repositories is answered either by reading "
              "both or by a capture taken where both were readable, and neither is available, so "
              "this run says nothing about it.")

        assert found["this"] == here, (
            f"{capture} was taken against different bytes than the ones on disk, so it argues "
            f"about a file this branch no longer has:\n"
            f"  the capture recorded  {found['this']}\n"
            f"  {mine} is now         {here}\n"
            "Re-take the capture from the developer's checkout against this tip.")

        assert found["this"] == found["enclosing"] and found["identical"], (
            f"{capture} is the evidence for this criterion and it records a DIFFERENCE:\n"
            f"  this repository      {found['this']}\n"
            f"  enclosing repository {found['enclosing']}\n"
            f"  cmp: identical       {found['identical']}\n"
            "The two manifests' QA prompt files are not byte-identical. SRV-FIX-527 owns levelling "
            "them, and a lane cannot: the second copy is not on this filesystem.")
        return
    drifted = []
    for name in ("run-ticket.md", "qa-ticket.md"):
        mine = mine_root / ".factory" / "prompts" / name
        theirs = theirs_root / ".factory" / "prompts" / name
        assert mine.is_file(), f"the factory's own manifest declares {name} and there is none at {mine}"
        if mine.read_bytes() != theirs.read_bytes():
            drifted.append(name)
    assert not drifted, (
        "these prompts differ between the two manifests, so a lane started by one instance is "
        f"given different instructions from a lane started by the other: {drifted}")


def _tickets_of(directory):
    """Every ticket document in one directory: a template and a README are not tickets."""
    import re

    for f in sorted(directory.glob("*.md")):
        if f.name.startswith("_") or f.name == "README.md":
            continue
        head = f.read_text().split("\n---", 2)
        front = head[0][4:] if head[0].startswith("---\n") and len(head) > 1 else ""
        yield f, dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", front, re.M))


def workspace_owners(root):
    """`{workspace name: the root of the manifest it belongs to}`.

    A name declared by more than one manifest belongs to the **deepest** one. `factory-server`,
    `factory-client` and `factory` are declared twice on purpose: by the factory's own manifest,
    which is what an instance aimed at `factory/` builds and gates; and by the product's, because
    `test_schema_consumers.py` forces `factory-client`'s declared build AND gate from there the
    moment a factory-server ticket edits a schema the client is generated from. The deeper manifest
    is the one whose DOCUMENTS are about that workspace, which is the question this file asks.

    Derived from the declarations, never from a list written here — a workspace this monorepo grows
    later is owned correctly without anybody remembering to come back.
    """
    owner = {}
    for base, manifest in manifests_of_this_monorepo(root):
        for name in manifest["workspaces"]:
            if name not in owner or len(str(base)) > len(str(owner[name])):
                owner[name] = base
    return owner


@pytest.mark.repository
def test_every_ticket_names_a_workspace_in_its_own_repo():
    """SRV-FIX-021's agreement, read from the other end and over every repository declared here.

    A ticket names a declared workspace — and that workspace belongs where the ticket does. A
    `FAC-*` ticket naming `server`, or an Acme ticket naming `factory-client`, is a ticket the tool
    would group into a workspace that cannot build it.

    Over EVERY manifest in this monorepo (SRV-FEAT-244), and the ticket is judged by the one that
    declares its directory: `factory/docs/tickets` belongs to `factory/.factory/factory.json` now,
    and reading its documents against the product manifest's `repos` would call every one of them
    an offender.

    TWO tests, because after SRV-FEAT-244 the `repo` key alone can no longer see the second one.
    Every workspace in the product's manifest names `repo: acme` — the factory ones included,
    since the directory they build really is inside that checkout — so "the workspace's repo is the
    ticket's repo" is satisfied by an Acme ticket declaring `factory-server`, which is exactly the
    mistake the case was written for. `workspace_owners` is what still refuses it.
    """
    root = _repo_root()
    owner = workspace_owners(root)

    offenders, checked = [], 0
    for base, manifest in manifests_of_this_monorepo(root):
        workspaces = manifest["workspaces"]
        for name, spec in declared_repos(manifest).items():
            directory = base / spec["path"] / spec["docs"]["tickets"]
            if not directory.is_dir():
                continue
            for f, keys in _tickets_of(directory):
                checked += 1
                ws = keys.get("workspace", "").strip()
                if ws not in workspaces:
                    offenders.append(f"{f.relative_to(root)}: workspace={ws!r} is not declared")
                elif workspaces[ws].get("repo", "") != name:
                    offenders.append(
                        f"{f.relative_to(root)}: workspace={ws!r} belongs to repo "
                        f"{workspaces[ws].get('repo', '')!r}, the ticket is in repo {name!r}")
                elif owner.get(ws) != base:
                    offenders.append(
                        f"{f.relative_to(root)}: workspace={ws!r} belongs to the manifest at "
                        f"{owner[ws].relative_to(root) if owner[ws] != root else '.'}, and this "
                        f"ticket is a document of the one at "
                        f"{base.relative_to(root) if base != root else '.'}")

    assert checked > 100, (
        f"only {checked} tickets seen — every repository every manifest here declares should be "
        f"read, and this monorepo holds well over a hundred tickets across them")
    assert not offenders, (
        "these tickets name a workspace that belongs somewhere else:\n  " + "\n  ".join(offenders))


# ── SRV-FIX-188 · E26 — an id is allocated by the tool, once ══════════════════════════════════
#
# `.claude/skills/new-ticket/SKILL.md` §2 told every agent to compute `highest taken + 1` from the
# files it could see, and a lane is a checkout cut at claim time that nothing merges into. So two
# lanes filing within one window each computed the highest id IN THEIR OWN WORKTREE, neither
# worktree held the other's file, and both answers were right: `SRV-FIX-185` named two documents,
# and `SRV-FIX-181`'s `409 ambiguous_ticket_id` then made both of them unreachable.
#
# Every case below is written against the property that failed, which is not "the arithmetic is
# right" but "**no two callers ever receive the same id**". A counter read from a filesystem each
# caller sees a different version of is not a counter.

import concurrent.futures
import threading
from pathlib import Path

from test_isolation import laneenv, run_in_lane                    # noqa: F401


def allocate(srv, prefix, **extra):
    body = {"prefix": prefix}
    body.update(extra)
    return srv.post("/api/tickets/ids", json=body)


def allocated(srv, prefix):
    r = allocate(srv, prefix)
    assert r.status_code == 201, f"E26 for {prefix!r} → {r.status_code} {r.text[:400]}"
    return r.json()


def id_repo(repo, tickets, where="docs/tickets"):
    """A repository whose `tickets` directory holds exactly these ids."""
    for t in tickets:
        repo.write(f"{where}/{t}.md",
                   doc({"id": t, "workspace": "alpha", "title": f"ticket {t}", "status": "todo"},
                       "Body."))


def test_two_concurrent_callers_get_different_ids(repo, serve):
    """THE assertion this ticket exists for — N=8 callers at once, 8 distinct ids.

    **The concurrency is the criterion.** A sequential loop is green against a read-then-write
    with no exclusion, which is exactly the implementation being replaced: every caller reads the
    same high-water mark and every caller returns the same next number. Eight threads released
    from one barrier is the smallest thing that can tell the two apart.

    Contiguous, because a race that hands out {42, 42, 43, ...} and a race that hands out
    {42, 99, 1000, ...} are the same defect, and only "eight distinct numbers starting at the
    high-water mark plus one" refuses both.
    """
    id_repo(repo, ["FIX-039", "FIX-041", "FEAT-040"])
    repo.mkdir(f"{SUB}/docs/tickets")
    srv = serve(repo.manifest(two_repo_manifest()))

    N = 8
    ready = threading.Barrier(N)

    def fire(_):
        ready.wait(timeout=20)
        return allocate(srv, "FIX")

    with concurrent.futures.ThreadPoolExecutor(max_workers=N) as pool:
        answers = list(pool.map(fire, range(N)))

    bad = [(r.status_code, r.text[:200]) for r in answers if r.status_code != 201]
    assert not bad, f"{len(bad)} of {N} concurrent callers were refused: {bad}"

    bodies = [r.json() for r in answers]
    numbers = sorted(b["number"] for b in bodies)
    assert len(set(numbers)) == N, (
        f"{N} callers asked at the same time and were handed {len(set(numbers))} distinct "
        f"ids — the same number was issued to more than one of them: {numbers}")
    assert numbers == list(range(42, 42 + N)), (
        f"the ids are not the {N} numbers after the high-water mark 41: {numbers}")
    assert sorted(b["id"] for b in bodies) == [f"FIX-{n:03d}" for n in numbers], bodies
    assert {b["prefix"] for b in bodies} == {"FIX"}, bodies


def test_the_high_water_mark_spans_every_declared_repo(repo, serve):
    """The mark is the whole manifest's, not the repository the caller happens to be in.

    The two repositories **disagree** — 100 against 150 — or the assertion cannot see which one
    was read: a mark computed from either would be "higher than the ids in this repo" and both
    readings would pass (`SRV-FIX-166`).

    It also fixes the SERIES: `FAC-FEAT-150` is in `FAC-FIX`'s sequence, because the counter
    belongs to the series and never to the type. A counter per type answers `FAC-FIX-101` here,
    and in this repository would hand out `FAC-FEAT-191` while `SRV-FIX-191` is a file on disk.
    """
    id_repo(repo, ["FAC-FIX-100"])
    id_repo(repo, ["FAC-FEAT-150"], where=f"{SUB}/docs/tickets")
    srv = serve(repo.manifest(two_repo_manifest()))

    body = allocated(srv, "FAC-FIX")
    assert body["number"] == 151, (
        f"E26 answered {body['id']} — the high-water mark was read from one repository "
        f"(first=100, second=150) instead of from every declared one: {body}")
    assert body["id"] == "FAC-FIX-151", body
    assert body["series"] == "FAC", body


def test_an_id_reserved_and_never_written_is_not_reissued(repo, serve):
    """An id handed out is spent, document or no document.

    A tool that reissues what a crashed agent never wrote reintroduces the collision on the
    recovery path — the second caller gets the first caller's id and neither file exists yet to
    say so. A gap in the sequence is free; a duplicate is the defect.
    """
    id_repo(repo, ["FAC-FIX-100"])
    repo.mkdir(f"{SUB}/docs/tickets")
    srv = serve(repo.manifest(two_repo_manifest()))

    first = allocated(srv, "FAC-FIX")
    assert first["number"] == 101, first
    second = allocated(srv, "FAC-FIX")
    assert second["number"] > first["number"], (
        f"{first['id']} was handed out and never written, and the next caller was handed "
        f"{second['id']} — an id nobody wrote is still spent")
    assert second["number"] == 102, second


def test_an_unknown_prefix_is_refused(repo, serve):
    """NEGATIVE (mandatory) — a prefix no declared repository uses allocates nothing.

    Inventing a counter for a mistyped prefix silently starts a second numbering series, which is
    this defect wearing a different hat: `FAC-BUG-001` looks allocated, collides with nothing, and
    means nothing. Watched RED against an implementation that creates the series on demand.
    """
    id_repo(repo, ["FAC-FIX-100"])
    id_repo(repo, ["FAC-FEAT-150"], where=f"{SUB}/docs/tickets")
    srv = serve(repo.manifest(two_repo_manifest()))

    r = allocate(srv, "FAC-BUG")
    assert r.status_code == 422, (
        f"a prefix no declared repository uses was allocated an id: {r.status_code} {r.text[:400]}")
    err = r.json()["error"]
    assert err["code"] == "unknown_prefix", err
    assert err["detail"]["prefix"] == "FAC-BUG", err
    assert sorted(err["detail"]["known"]) == ["FAC-FEAT", "FAC-FIX"], err
    # §11 check 12 exempts it, so the body says so rather than leaving the member out.
    assert "remedy" in err and err["remedy"] is None, err

    # ...and nothing was allocated. The known prefix still answers the number it would have.
    assert allocated(srv, "FAC-FIX")["number"] == 151, "the refusal spent an id anyway"
    assert allocate(srv, "FAC-BUG").status_code == 422, "the first refusal created the series"


def test_a_lane_can_reach_it(laneenv):
    """The endpoint answers from inside a lane's confinement, which is where every caller is.

    An allocator a lane cannot reach is the current situation with extra steps. Asserted from a
    real process in a real lane, with `curl` and nothing else — the same way `E22` is reached and
    the same way the skill will reach it.
    """
    # `/bin/sh` rather than the real agent CLI: the criterion is that the ENDPOINT answers from
    # inside the boundary, and a lane that cannot be claimed because no agent is installed would
    # make this case say nothing about E26 either way.
    env = laneenv.build(agent={"command": "/bin/sh agent.sh"})
    lane = env.claim()

    rc, out = run_in_lane(
        env, lane,
        "/usr/bin/curl -s -X POST -H 'Content-Type: application/json' "
        f"-d '{{\"prefix\":\"TCK\"}}' {env.srv.base}/api/tickets/ids")
    assert rc == 0, f"curl failed inside the lane: rc={rc} {out!r}"
    body = json.loads(out) if out.lstrip().startswith("{") else {}
    assert body.get("id") == "TCK-003", (
        f"a lane asked the tool for an id and got {out!r} — `build_repo` writes TCK-001 and "
        f"TCK-002, so the next one is TCK-003")
    env.unchanged()


import re  # noqa: E402  — this block's own dependency, appended after the module's imports

# ── SRV-FEAT-065 round 5 · a criterion that asks for a screen is asserted on the screen ───────
#
# This case is the FIRST commit of the round after `SRV-FEAT-065`'s fourth QA FAIL, and it is not
# a fix — it is the assertion that would have caught what QA found, transcribed from that verdict's
# own reproduction:
#
#     ls factory/docs/reports/SRV-FEAT-065/
#     git diff --stat origin/main...HEAD -- '*.png' '*.jpg'    # → (empty: the branch adds no image)
#
# Rounds 1, 3 and 4 all failed the same way: an obligation §1 stated in prose, with nothing that
# could go red. Round 4 fixed that for the SPEC sentence — `test_spec.py::test_f31_says_which…` —
# and left the identical instance sitting three bullets lower in the same §1. *"A green
# `test_roles.py` is not a screen anybody can read"* is a requirement about an artefact, so it is
# asserted on the artefact.
#
# IT RATCHETS, and the baseline is the finding rather than an excuse. Nine tickets are `pass`
# today whose §1 asked for a screenshot and whose evidence directory holds no image. They are named
# here, they are owed to `SRV-FIX-245`, and no tenth may join them.

#: `pass` with a screenshot criterion and no screenshot, measured 2026-08-24. Owed to SRV-FIX-245.
#: A name leaves this set when its image is committed; nothing may be added to it.
PASSED_WITHOUT_THE_SCREEN = {
    "WEB-FEAT-041", "SRV-FEAT-084", "WEB-FEAT-085", "WEB-FIX-051", "WEB-FIX-061",
    "WEB-FIX-072", "WEB-FIX-073", "WEB-FIX-169", "SRV-FIX-059",
}

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


#: A fenced block and a `*"…"*` quotation are EVIDENCE, not a request. Both are stripped before
#: §1 is read for the word, because the one document guaranteed to quote this rule verbatim is a
#: ticket ABOUT this rule: `SRV-FIX-384` pastes the refusal above and asks for no screen at all,
#: and a keyword sweep cannot tell *asks for a screenshot* from *quotes the case that says so*.
#: Measured when it was added: of 42 tickets in the domain, 4 leave — `SRV-FIX-384` and three
#: `superseded` — and no `pass`/`awaiting-qa` offender and no `PASSED_WITHOUT_THE_SCREEN` name
#: does. Asserted by `test_a_ticket_that_only_quotes_the_screenshot_rule_is_not_in_its_domain`.
QUOTED_EVIDENCE = (re.compile(r"^[ \t]*```.*?^[ \t]*```", re.M | re.S),
                   re.compile(r'\*"[^"]*"\*', re.S))

#: A §1 that declares it touches NO SURFACE is not asking for one to be photographed. The template
#: makes every ticket state its surfaces, and a ticket answering `none` cannot owe a screen of a
#: screen it does not change — the word then belongs to something else, usually a list of the KINDS
#: of evidence a criterion can have. `SRV-FEAT-480` is the instance: its §1 says `Surfaces: none`
#: and criterion 5 reads *"a transcript, a screenshot or a sentence"*, naming what its program must
#: classify. Same class as the `SRV-FIX-384` narrowing above — a keyword sweep cannot tell
#: *asks for a screenshot* from *uses the word*.
#: Measured when it was added: of 46 tickets in the domain, 2 leave — `SRV-FEAT-480` and
#: `SRV-FIX-391` (`todo`, which owes nothing yet) — and no `pass`/`awaiting-qa` offender and no
#: `PASSED_WITHOUT_THE_SCREEN` name does. `WEB-FIX-334` stays in the domain and stays red.
#: Asserted in both directions by
#: `test_a_ticket_that_only_quotes_the_screenshot_rule_is_not_in_its_domain`.
NO_SURFACE = re.compile(r"^-\s+\*\*Surfaces[^:]*:\*\*\s*(?:<[^>]*>\s*)?(?:none|n/a)\b",
                        re.M | re.I)


def _asks_for_a_screen(section_one):
    """Does this §1 ASK for a screenshot? One door, so the negative below drives what the sweep does."""
    if NO_SURFACE.search(section_one):
        return False
    asked = section_one
    for pattern in QUOTED_EVIDENCE:
        asked = pattern.sub(" ", asked)
    return bool(re.search(r"screenshot", asked, re.I))


def _tickets_asking_for_a_screen():
    """Every ticket whose §1 asks for a screenshot, with its status and its evidence directory.

    §1 only — the standing process is `_TEMPLATE.md`'s and mentions screens for other reasons, so
    reading past `## 2.` would put every ticket in this repository into the domain.
    """
    root = _repo_root()
    for docs in ("factory/docs", "docs"):
        tickets = root / docs / "tickets"
        if not tickets.is_dir():
            continue
        for path in sorted(tickets.glob("*.md")):
            if path.name.startswith("_"):
                continue
            text = path.read_text(errors="ignore")
            head = text[:4000]
            ident = re.search(r"^id:\s*(\S+)\s*$", head, re.M)
            status = re.search(r"^status:\s*(\S+)\s*$", head, re.M)
            if not ident:
                continue
            section_one = text.split("## 2.")[0]
            if not _asks_for_a_screen(section_one):
                continue
            yield (ident.group(1), status.group(1) if status else "", root / docs / "reports" / ident.group(1))


@pytest.mark.repository
def test_a_ticket_that_asks_for_a_screenshot_has_one():
    """A ticket claiming to be done, whose §1 asked for a screen, has committed the screen.

    The domain is enumerated rather than quantified over: `pass` and `awaiting-qa` only — a ticket
    that is `todo`, `fail` or `duplicate` owes nothing yet, and asserting against those would make
    this red for every unstarted ticket in the backlog.
    """
    missing = []
    for ident, status, reports in _tickets_asking_for_a_screen():
        if status not in ("pass", "awaiting-qa"):
            continue
        if ident in PASSED_WITHOUT_THE_SCREEN:
            continue
        images = sorted(p.name for p in reports.glob("*")
                        if p.suffix.lower() in IMAGE_SUFFIXES) if reports.is_dir() else []
        if not images:
            missing.append(f"{ident} ({status}): §1 asks for a screenshot; {reports} holds none")

    assert not missing, (
        "these tickets claim to be done and their §1 asked for a screen nobody can read without:\n  "
        + "\n  ".join(missing)
        + "\n\nA criterion that says a green test is not a screen cannot be discharged by a green "
          "test. Commit the capture into the ticket's own evidence directory.")


ASKS_IN_PROSE = """
- **Feature / behaviour:** the report carries a screenshot of the rail in both themes.
"""

ONLY_QUOTES_THE_RULE = """
- **The raw output.**

  ```
  FAILED tests/test_catalog.py::test_a_ticket_that_asks_for_a_screenshot_has_one
      WEB-FIX-328 (awaiting-qa): §1 asks for a screenshot;
  ```

  The comment above the case is explicit that it ratchets: *"Nine tickets are `pass` today whose
  §1 asked for a screenshot and whose evidence directory holds no image … no tenth may join
  them."* A tenth was one QA verdict away from joining them.
"""


NO_SURFACE_AND_NAMES_THE_WORD = """
- **Surfaces (`docs/product/SPEC.md` 5-6):** none.

5. A criterion whose evidence is a transcript, a screenshot or a sentence prints a row `manual`.
"""

A_SURFACE_AND_ASKS = """
- **Surfaces (`docs/product/SPEC.md` 5-6):** `E12`, `M8`.

5. The report carries a screenshot of the rail in both themes.
"""


def test_a_ticket_that_only_quotes_the_screenshot_rule_is_not_in_its_domain():
    """MANDATORY NEGATIVE for the narrowing above, which is the half that can rot.

    Stripping evidence from §1 is one edit away from stripping the request too, and then the
    ratchet governs nothing while reporting the same green. Both directions are driven here on
    synthetic §1s, so neither depends on what this repository's tickets happen to say today.
    """
    assert _asks_for_a_screen(ASKS_IN_PROSE), (
        "a §1 that asks for a screenshot in prose left the domain — the narrowing has eaten the "
        "rule it was supposed to keep")
    assert not _asks_for_a_screen(ONLY_QUOTES_THE_RULE), (
        "a §1 that only QUOTES this case — in a fenced block and in a `*\"…\"*` quotation — is "
        "still read as asking for a screen, so a ticket about the rule is an offender against it")
    assert not _asks_for_a_screen(NO_SURFACE_AND_NAMES_THE_WORD), (
        "a §1 that declares `Surfaces: none` and uses the word while naming the KINDS of evidence "
        "a criterion can have is read as asking for a screen — it changes no screen, so there is "
        "none to photograph")
    assert _asks_for_a_screen(A_SURFACE_AND_ASKS), (
        "a §1 that names a surface and asks for a screenshot left the domain — the surface "
        "narrowing has eaten the rule it was supposed to keep")


@pytest.mark.repository
def test_the_screenshot_baseline_only_shrinks():
    """MANDATORY NEGATIVE — the ratchet, which is the half that can rot.

    A baseline is the cheap way to make any gate green: add the name and move on. This case makes
    the set falsifiable in the one direction that matters — every name in it must still be a real
    ticket that still lacks its image, so a name kept after somebody commits the capture is a
    stale exemption, and a name added for a NEW ticket cannot hide there because the set is
    compared against what is actually missing.
    """
    known = {ident: reports for ident, status, reports in _tickets_asking_for_a_screen()}
    unknown = sorted(PASSED_WITHOUT_THE_SCREEN - set(known))
    assert not unknown, (
        f"the baseline names tickets that do not ask for a screenshot any more: {unknown}")

    settled = []
    for ident in sorted(PASSED_WITHOUT_THE_SCREEN):
        reports = known[ident]
        if reports.is_dir() and any(p.suffix.lower() in IMAGE_SUFFIXES for p in reports.glob("*")):
            settled.append(ident)
    assert not settled, (
        f"these are in the baseline and now have their screenshot; remove them from "
        f"PASSED_WITHOUT_THE_SCREEN so the ratchet keeps its teeth: {settled}")


# ── SRV-FIX-281 · the mark comes from the REPOSITORY, not from the directory we stand in ──────
#
# `SRV-FIX-188` moved the counter into the tool so two lanes could not both compute `highest + 1`
# from a worktree the other's file was not in. The tool then reads ONE working tree — `surveyIds()`
# iterates `tickets().documents`, which is the catalog's view of the filesystem — so every id filed
# on a branch that has not merged is invisible to it.
#
# Live, 2026-08-27: `E26` answered `WEB-FIX-278` while `WEB-FIX-278` and `WEB-FIX-279` sat on
# `ticket/WEB-FIX-275`, filed forty minutes earlier, with the root checkout on `main`. `ids.json`
# recorded 278 as spent — the tool asserting a false fact about its own series. `SPEC.md` says the
# mark "spans every declared repository"; it does not span every REF, and a ticket document's whole
# life before it merges is on a ref.

def committed_id_repo(repo, on_main, on_branch, branch="ticket/x"):
    """A git repository whose `main` holds `on_main` and whose `branch` holds `on_branch`.

    The checkout is left on `main`, so `on_branch`'s documents exist in the repository and in no
    directory anybody is standing in — which is where every filed ticket lives until it merges.
    """
    from test_lane import git

    repo.mkdir("docs/tickets")
    repo.mkdir("docs/epics")
    repo.mkdir("docs/reports")
    id_repo(repo, on_main)
    root = repo.manifest({
        "factory": "0.1",
        "repos": {"only": {"path": ".", "docs": dict(DOCS_OF)}},
        "workspaces": {"alpha": {"repo": "only", "path": "."}},
    })

    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "main")

    git(root, "checkout", "-q", "-b", branch)
    id_repo(repo, on_branch)
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "branch")

    git(root, "checkout", "-q", "main")           # the branch.s documents are now on no disk
    for t in on_branch:                            # ...and provably so
        assert not (root / "docs" / "tickets" / f"{t}.md").exists()
    return root


def test_an_id_filed_on_an_unmerged_branch_is_never_reissued(repo, serve):
    """THE assertion. `TCK-002` exists in the repository and in no working tree.

    A caller that reads the directory answers `TCK-002` — the number a document already carries —
    which is the reissue `SPEC.md` E26 says must never happen: "an id nobody used is a gap in the
    sequence, and a gap is free; reissuing what an agent never wrote is how the collision comes
    back on the recovery path."
    """
    root = committed_id_repo(repo, on_main=["TCK-001"], on_branch=["TCK-002"])
    srv = serve(root)

    answer = allocated(srv, "TCK")
    assert answer["number"] == 3, (
        f"E26 answered {answer['id']}, and TCK-002 is a document on `ticket/x`. The mark was read "
        f"from the working tree, so every id filed on an unmerged branch is invisible to it.")


def test_the_mark_still_comes_from_documents_and_not_from_ids_json(repo, serve):
    """MANDATORY NEGATIVE — the cheap fix is to trust `ids.json`, and it loses the mark.

    `ids.json` is the tool's memory of what it RESERVED, not of what exists: `SPEC.md` calls it
    "the tool's own record, not the repository's", and says losing it costs a gap and never a
    duplicate. An implementation that answered from it alone would satisfy the case above and then
    reissue every id in the series the moment that file was cleaned.

    **The server is RESTARTED between the two questions, and that is the whole case.**
    `catalog::Ids::spent_` is an in-memory map. Round 1 deleted the file under a running server and
    asserted the answer had moved forward — an assertion satisfied by construction, which sat in
    the passed column against an `ids.json`-only mutant its own report claimed it had been watched
    red against. Transcribed from `SRV-FIX-281`'s QA, finding F1, and from the probe committed at
    `factory/docs/reports/SRV-FIX-281/qa281_probe_negative.py`.

    Restarted, the tool has to MEASURE the mark instead of remembering it, and §1's words hold
    literally: the answer is unchanged.
    """
    root = committed_id_repo(repo, on_main=["TCK-001"], on_branch=["TCK-002"])

    first_server = serve(root)
    first = allocated(first_server, "TCK")["id"]
    serve.stop(first_server)

    ids = root / ".factory" / "ids.json"
    assert ids.exists(), "E26 did not record the allocation it made"
    ids.unlink()

    again = allocated(serve(root), "TCK")["id"]

    # BOTH halves, because neither alone discriminates. "Unchanged" is satisfied by an
    # `ids.json`-only implementation too — it answers TCK-001 before the deletion and TCK-001
    # after, which is unchanged and wrong. Measured against that mutant: this assertion is RED on
    # the value and green on the equality.
    assert again == first, (
        f"the tool answered {first} with its memory intact and {again} after it was cleaned — the "
        f"mark moved when only the tool's own record was removed")
    assert again == "TCK-003", (
        f"after the deletion the tool answered {again}. TCK-001 is on `main` and TCK-002 is on "
        f"`ticket/x`, so the only measured answer is TCK-003: this is an implementation reading "
        f"its own memory rather than the repository, and a cleaned .factory reissues the series.")


def test_a_ref_with_no_ticket_directory_does_not_break_the_scan(repo, serve):
    """A branch that predates the docs tree is skipped, not fatal.

    An allocator that throws on one odd ref is an allocator that has stopped working, and the ref
    it throws on is whichever one somebody made before the convention existed.
    """
    from test_lane import git

    root = committed_id_repo(repo, on_main=["TCK-001"], on_branch=["TCK-002"])
    git(root, "checkout", "-q", "--orphan", "ancient")
    git(root, "rm", "-rqf", ".")
    repo.write("README.md", "before there were tickets\n")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ancient")
    git(root, "checkout", "-qf", "main")

    srv = serve(root)
    assert allocated(srv, "TCK")["number"] == 3


# ── A template is not a document ──────────────────────────────────────────────────────────────

def test_a_template_and_a_readme_are_not_served_as_documents(repo, serve):
    """`_TEMPLATE.md` and `README.md` are the form and the sign on the door, not the work.

    `SRV-FIX-043` reported this in 2026-07-30 and `SRV-FIX-263` reported it again on 2026-08-26;
    both were closed by decision on 2026-09-01 without the tool learning anything. The convention
    was never absent — `test_every_ticket_in_this_repository_names_a_declared_workspace` and four
    other cases in this file already skip `f.name.startswith("_") or f.name == "README.md"` — it
    just lived in the suite and not in `catalog::Store::isDocument`, so the tool counted a blank
    form as a requirement, gave it a status, an age, a workspace chip and a child (a real ticket
    copied from the template still says `parent: EPIC-NNN`), and aggregated it into `needs you`.

    That is also why this repository has no template of its own: adding one drew a phantom row.
    """
    repo.write(f"{REQ_DIR}/REQ-001-cold-start.md",
               doc({"id": "REQ-001", "title": "Cold start is fast", "status": "draft"}, "One."))
    repo.write(f"{REQ_DIR}/_TEMPLATE.md",
               doc({"id": "EPIC-NNN", "title": "<REQUIREMENT NAME>", "status": "draft"}, "Form."))
    repo.write(f"{TKT_DIR}/TKT-1.md",
               doc({"id": "TKT-1", "parent": "REQ-001", "workspace": "alpha",
                    "title": "Measure the cold start", "status": "todo"}, "Body."))
    repo.write(f"{TKT_DIR}/_TEMPLATE.md",
               doc({"id": "TYPE-NNN", "parent": "EPIC-NNN", "workspace": "alpha",
                    "title": "<FEATURE NAME>", "status": "todo"}, "Form."))
    repo.write(f"{TKT_DIR}/README.md", "# How to write a ticket\n\nNot a ticket.\n")

    srv = serve(repo.manifest(manifest_for()))

    body = ok(srv, "/api/requirements")
    ids = [r["id"] for r in body["requirements"]]
    assert ids == ["REQ-001"], (
        "the template and the readme are not work; the screen counts what a person can act on: "
        f"{ids}")
    assert body["total"] == len(body["requirements"]) == 1, (
        "the count line and the rows are one sentence and must be asserted together", body)

    gone = srv.get("/api/tickets/TYPE-NNN")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "not_found", \
        "a document the list refuses must not be reachable by id either"
    assert ok(srv, "/api/tickets/TKT-1")["id"] == "TKT-1", \
        "the real ticket beside the template is still served"


def test_a_document_whose_name_merely_contains_the_words_is_still_served(repo, serve):
    """MANDATORY NEGATIVE — the rule is `_` FIRST and `README.md` EXACTLY, not a substring search.

    A filter that went green by dropping more than it was asked to would pass the case above and
    silently unpublish real work. Both of these are documents somebody wrote on purpose.
    """
    repo.write(f"{REQ_DIR}/REQ-001-cold-start.md",
               doc({"id": "REQ-001", "title": "Cold start is fast", "status": "draft"}, "One."))
    repo.write(f"{TKT_DIR}/TKT-my_notes.md",
               doc({"id": "TKT-my_notes", "parent": "REQ-001", "workspace": "alpha",
                    "title": "An underscore inside a name is a name", "status": "todo"}, "Body."))
    repo.write(f"{TKT_DIR}/README-THE-PROTOCOL.md",
               doc({"id": "README-THE-PROTOCOL", "parent": "REQ-001", "workspace": "alpha",
                    "title": "A ticket whose name starts with the word", "status": "todo"}, "B."))

    srv = serve(repo.manifest(manifest_for()))

    body = ok(srv, "/api/requirements/REQ-001")
    served = sorted(t["id"] for g in body["groups"] for t in g["tickets"])
    assert served == ["README-THE-PROTOCOL", "TKT-my_notes"], (
        "the rule dropped a document it was not asked to drop", served)


# ── A parent names a requirement in its OWN repository ───────────────────────────────────────
#
# Written 2026-09-02 while planning a per-surface documents split — `docs/epics/` at the root
# carrying the cross-cutting requirement, `server/docs/tickets/` and `client/web/docs/tickets/`
# carrying `SRV-*` and `WEB-*` under it. The plan rested on a fact nobody had asserted, and the
# fact is the opposite: `render.cpp` keys the lookup by `{repo, parent}`, so a ticket in one
# repository can never be a child of a requirement in another. `test_a_manifest_declaring_two_repos_
# lists_tickets_from_both` does not see it — both of its epics sit beside their own tickets.
#
# It is the SRV-FIX-181 / SRV-FIX-199 invariant seen from the other side: repo-scoped ids are what
# stops one repository's requirements answering about another's. So the constraint is recorded
# here rather than worked around, and any layout that wants a shared epic is a MODEL change.

def test_a_parent_names_a_requirement_in_any_declared_repository(repo, serve):
    """Two children in sub-repos naming a root epic land in `standalone`, not under it."""
    manifest = {
        "factory": "0.1",
        "repos": {
            "root":   {"path": ".",          "role": "tool",    "docs": dict(DOCS_OF)},
            "server": {"path": "server",     "role": "product", "docs": dict(DOCS_OF)},
            "web":    {"path": "client/web", "role": "product", "docs": dict(DOCS_OF)},
        },
        "workspaces": {
            "srv": {"repo": "server", "path": "."},
            "web": {"repo": "web",    "path": "."},
        },
    }
    repo.write("docs/epics/FAC-EPIC-001.md",
               doc({"id": "FAC-EPIC-001", "title": "One requirement, two surfaces",
                    "status": "active"}, "The contract freeze lives here."))
    repo.write("server/docs/tickets/SRV-FEAT-012.md",
               doc({"id": "SRV-FEAT-012", "parent": "FAC-EPIC-001", "workspace": "srv",
                    "title": "The endpoint", "status": "todo"}, "Body."))
    repo.write("client/web/docs/tickets/WEB-FEAT-004.md",
               doc({"id": "WEB-FEAT-004", "parent": "FAC-EPIC-001", "workspace": "web",
                    "title": "The surface that draws it", "status": "todo"}, "Body."))
    for d in ("docs/tickets", "docs/reports", "server/docs/epics", "server/docs/reports",
              "client/web/docs/epics", "client/web/docs/reports"):
        repo.mkdir(d)

    srv = serve(repo.manifest(manifest))

    listed = {r["id"]: r for r in ok(srv, "/api/requirements")["requirements"]}
    assert sorted(listed) == ["FAC-EPIC-001"], (
        "SRV-FEAT-555: a ticket's `parent:` resolves by id in any declared repository, so the two "
        "sub-repository tickets hang off the root's requirement and nothing is standalone",
        sorted(listed))
    assert listed["FAC-EPIC-001"]["ticket_count"] == 2, listed["FAC-EPIC-001"]

    body = ok(srv, "/api/requirements/FAC-EPIC-001")
    assert sorted(t["id"] for g in body["groups"] for t in g["tickets"]) == [
        "SRV-FEAT-012", "WEB-FEAT-004"], body["groups"]


def test_a_parent_in_the_same_repository_still_resolves(repo, serve):
    """MANDATORY NEGATIVE — the case above must fail for the REASON it names.

    An assertion that children go to `standalone` passes just as well against a tool whose parent
    resolution is broken outright. This is the same shape with the epic moved into the sub-repo
    beside its children, and there it must group.
    """
    manifest = {
        "factory": "0.1",
        "repos": {
            "root":   {"path": ".",      "role": "tool",    "docs": dict(DOCS_OF)},
            "server": {"path": "server", "role": "product", "docs": dict(DOCS_OF)},
        },
        "workspaces": {"srv": {"repo": "server", "path": "."}},
    }
    repo.write("server/docs/epics/SRV-EPIC-001.md",
               doc({"id": "SRV-EPIC-001", "title": "Beside its children", "status": "active"}, "x"))
    repo.write("server/docs/tickets/SRV-FEAT-012.md",
               doc({"id": "SRV-FEAT-012", "parent": "SRV-EPIC-001", "workspace": "srv",
                    "title": "A child", "status": "todo"}, "Body."))
    for d in ("docs/epics", "docs/tickets", "docs/reports", "server/docs/reports"):
        repo.mkdir(d)

    srv = serve(repo.manifest(manifest))

    listed = {r["id"]: r for r in ok(srv, "/api/requirements")["requirements"]}
    assert sorted(listed) == ["SRV-EPIC-001"], (
        "same repository, so the child resolves and there is no orphan", sorted(listed))
    assert listed["SRV-EPIC-001"]["ticket_count"] == 1, listed["SRV-EPIC-001"]


# ── SRV-FIX-381 · a ticket whose work reached `main` has a report on `main` ────────────────────
#
# `SRV-FIX-377` merged on 2026-09-04 as `042c061` and left no `docs/reports/SRV-FIX-377-report.md`
# and no run log. Its QA session found it at step 8 — the excerpt audit, which had nothing to
# audit — and nothing in the workflow could have found it earlier. `_TEMPLATE.md` §2 requires a
# report at every stage in PROSE; `GET /api/tickets/{id}/report` correctly serves an absence for a
# ticket that has not run, so *has not run* and *ran and left nothing* read identically on every
# surface; and no case anywhere compared a ticket's commits to the existence of its report.
#
# The obligation stops being a sentence somebody follows and becomes a set derived from
# `git log main`. A typed list of tickets to check would be bounded by whoever last remembered to
# extend it, which is the shape `SRV-FIX-351` cost four rounds.

#: Landed, claiming done, and with no report — measured 2026-09-05, owed to `SRV-FIX-397`.
#: `SRV-FIX-381` wrote the two reports its §1 scoped; these three are that ticket's debt.
#: A name leaves this set when its report is committed; nothing may be added to it.
LANDED_WITHOUT_A_REPORT = {
    "SRV-FIX-020": "on `main` as FAC-FIX-020 since 2026-08-06; `SRV-FIX-020-qa.md` and an "
                   "evidence directory are committed and the implementer's report never was",
    "WEB-FEAT-085": "on `main` as FAC-FEAT-085; eleven artefacts under `docs/reports/` including "
                    "five QA rounds and a run log, and no `-report.md` among them",
    "WEB-FIX-328": "on `main` since 2026-09-04 with four commits and a QA verdict, and no "
                   "`-report.md` — the same cause as SRV-FIX-377, five days later",
}

#: A ticket owes a report once it says its work is finished. Every other status owes nothing YET.
DONE_STATUSES = ("pass", "awaiting-qa")


def id_and_old_scheme_alias(ident):
    """The ticket's id, plus the id the same ticket carried before the scheme was renamed.

    `SRV-FIX-368` landed *"FAC- is epics only"* on 2026-09-04 and every `FAC-FIX-*` and
    `FAC-FEAT-*` ticket became `SRV-*` or `WEB-*`. Commit subjects are history and were not
    rewritten, so a ticket whose id is `SRV-FIX-020` today is on `main` under `FAC-FIX-020`. The
    report is matched literally, by the id the ticket carries now.

    Matching on `(kind, number)` alone — ignoring the component — was the obvious shortcut and it
    is wrong: `SRV-FIX-338` and `WEB-FIX-338` are two live tickets in this backlog, and conflating
    them would let either one's report discharge the other's obligation.
    """
    import re

    m = re.match(r"^([A-Z][A-Z0-9]*)-(FIX|FEAT|EPIC)-(\d+)$", ident)
    if not m:
        return {ident}
    return {ident, f"FAC-{m.group(2)}-{m.group(3)}"}


def declared_document_dirs(root):
    """`{tickets dir: reports dir}` for every repository every manifest under `root` declares.

    The pair travels together because the obligation is a pair: a report is owed *beside* the
    ticket that owes it, in whatever the manifest calls those two directories. `test_catalog.py`
    names its own fixtures `product/work-items` and `product/deliveries` for exactly this reason —
    a rule that only works when the directories are called `docs/tickets` and `docs/reports` has
    learned this repository.
    """
    declared_ticket_dirs(root)                    # its assertion: nothing here is UNdeclared

    pairs = {}
    for base, manifest in manifests_of_this_monorepo(root):
        for spec in declared_repos(manifest).values():
            where = base / spec["path"]
            tickets = (where / spec["docs"]["tickets"]).resolve()
            if tickets.is_dir():
                pairs[tickets] = (where / spec["docs"]["reports"]).resolve()

    assert pairs, "no ticket directory found"
    return pairs


def landed_ticket_ids(where):
    """`{id: subject}` for every id that PREFIXES the subject of a commit reachable from `main`.

    Git is run inside `where`, so the history read is the one that repository keeps rather than a
    superproject's. A monorepo declaring three repositories is three questions, not one.
    """
    import re
    import subprocess

    seen = {}
    log = subprocess.run(["git", "-C", str(where), "log", "--format=%s", "main"],
                         capture_output=True, text=True, timeout=180)
    if log.returncode != 0:
        return seen
    for subject in log.stdout.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9]*-(?:FIX|FEAT|EPIC)-\d+)", subject)
        if m:
            seen.setdefault(m.group(1), subject)
    return seen


def tickets_claiming_done(tickets):
    """`[(id, status, path)]` for every document in `tickets` whose status says the work is done."""
    import re

    out = []
    for path in sorted(tickets.glob("*.md")):
        if path.name.startswith("_") or path.name == "README.md":
            continue
        text = path.read_text(errors="ignore")
        head = text.split("\n---", 2)[0]
        keys = dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", head, re.M))
        ident, status = keys.get("id", "").strip(), keys.get("status", "").strip()
        if ident and status in DONE_STATUSES:
            out.append((ident, status, path))
    return out


def landed_without_a_report(root):
    """`{id: what is on `main` and what is not}` — the offenders, and nothing else.

    A ticket is in the domain when it says its work is finished AND its id prefixes a commit
    reachable from `main`. The status half is the one excluded category and it is stated here
    rather than discovered: FILING a ticket puts its id on `main` — the commit that adds
    `docs/tickets/WEB-FIX-338-….md` is reachable from `main` and its subject begins with the id —
    so without it every ticket in the backlog goes red the day it is filed, and a check nobody can
    keep green is a check nobody keeps. `test_a_ticket_that_asks_for_a_screenshot_has_one`
    enumerates its domain the same way for the same reason.

    Its cost, stated: a ticket merged while its status still reads `todo` is outside this. §2
    permits a merge only on `status: pass`, and the sweep on 2026-09-05 found no such ticket.
    """
    offenders = {}
    for tickets, reports in declared_document_dirs(root).items():
        landed = landed_ticket_ids(tickets)
        for ident, status, _path in tickets_claiming_done(tickets):
            on_main = sorted(id_and_old_scheme_alias(ident) & set(landed))
            if not on_main:
                continue
            if (reports / f"{ident}-report.md").is_file():
                continue
            offenders[ident] = (f"{status}, on `main` as {on_main[0]} — {landed[on_main[0]]!r} — "
                                f"and {reports / (ident + '-report.md')} does not exist")
    return offenders


@pytest.mark.repository
def test_a_ticket_landed_on_main_has_a_report():
    """A person opening the report tab of a ticket whose work is on `main` finds a report there.

    THE ASSERTION `SRV-FIX-377` WAS OWED. It merged, its commit message quoted `54 passed, 1
    skipped` and `Document gate 52 passed`, and no artefact on disk could be checked against
    either number. QA's step 8 is the audit of the implementer's excerpt against the implementer's
    own raw capture — the line just after the quoted one, a failure earlier in the same run, a
    second run that quietly replaced a first. With no report and no log that step cannot run at
    all, and every gate the workflow names was green.
    """
    root = _repo_root()
    offenders = landed_without_a_report(root)

    new = {i: why for i, why in offenders.items() if i not in LANDED_WITHOUT_A_REPORT}
    assert not new, (
        "these tickets say their work is finished and their work is on `main`, and `main` carries "
        "no report for them — so nothing anybody can open says what was run or what it "
        "printed:\n  " + "\n  ".join(f"{i}: {why}" for i, why in sorted(new.items()))
        + "\n\nWrite `<reports>/<id>-report.md`. A commit message quoting a test count is not a "
          "report: QA audits the excerpt against the raw capture, and there is nothing to audit.")

    settled = sorted(set(LANDED_WITHOUT_A_REPORT) - set(offenders))
    assert not settled, (
        f"these are in LANDED_WITHOUT_A_REPORT and are not offenders any more; remove them so the "
        f"ratchet keeps its teeth: {settled}")


@pytest.mark.repository
def test_the_landed_ticket_sweep_is_non_empty():
    """A check that sweeps nothing has not run, and this one is two sweeps deep.

    The landed set comes out of `git log main` and the domain out of the declared ticket
    directories, and either can silently become empty: a repository with no `main` ref, a manifest
    whose `docs` block moved, a lane whose worktree the door resolves past. The case above would
    then be green having examined nothing, which is how a ratchet in this suite passed its first
    run against a file that was on an unmerged branch.
    """
    root = _repo_root()

    landed, done = {}, []
    for tickets, _reports in declared_document_dirs(root).items():
        landed.update(landed_ticket_ids(tickets))
        done.extend(tickets_claiming_done(tickets))

    assert len(landed) > 20, (
        f"`git log main` yielded {len(landed)} ticket ids — the derivation found the wrong thing, "
        f"and the check above is vacuous until it does not")
    assert len(done) > 20, (
        f"only {len(done)} tickets in this repository claim to be done — the ticket directories "
        f"the manifests declare are not being read")

    checked = [i for i, _s, _p in done if id_and_old_scheme_alias(i) & set(landed)]
    assert len(checked) > 20, (
        f"only {len(checked)} of {len(done)} finished tickets could be matched to a commit on "
        f"`main`; the id scheme and the commit subjects have stopped agreeing, so the rule is "
        f"asking about almost nobody")


def a_repository_with_a_landed_ticket(tmp_path):
    """A repository the rule can be asked about: one manifest, tickets, reports, and a `main`.

    A rule that can only read this repository has exactly one way to go red — somebody breaks
    `main` first — and `SRV-FIX-242` is what that cost: two documents claimed `WEB-FIX-197` for
    five days while every ticket's GATE ran the case and read past it.

    Four tickets, and they disagree with each other: one finished and landed, one finished and
    never landed, one landed while still `todo`, and one finished and landed WITH its report.
    """
    from test_lane import git

    root = tmp_path / "fx"
    (root / ".factory").mkdir(parents=True)
    (root / "docs" / "tickets").mkdir(parents=True)
    (root / "docs" / "reports").mkdir(parents=True)
    (root / ".factory" / "factory.json").write_text(json.dumps({
        "factory": "0.1",
        "docs": dict(DEFAULT_DOCS),
        "workspaces": {"alpha": {"path": "."}},
    }))

    for ident, status in (("TCK-FIX-001", "pass"), ("TCK-FIX-002", "pass"),
                          ("TCK-FIX-003", "todo"), ("TCK-FIX-004", "pass")):
        (root / "docs" / "tickets" / f"{ident}-x.md").write_text(
            doc({"id": ident, "title": ident, "workspace": "alpha", "status": status}, "Body."))
    for ident in ("TCK-FIX-001", "TCK-FIX-004"):
        (root / "docs" / "reports" / f"{ident}-report.md").write_text("# report\n")

    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
    for subject in ("TCK-FIX-001: the work", "TCK-FIX-003: the work", "TCK-FIX-004: the work"):
        (root / "work.txt").write_text(subject)
        git(root, "add", "-A")
        git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", subject)
    return root


def test_removing_a_report_makes_the_landed_check_red(tmp_path):
    """MANDATORY NEGATIVE — take one report away and the rule names that id, and only that id.

    The three tickets that must NOT be named are the whole of what makes this a negative rather
    than an existence check. `TCK-FIX-002` is finished and never landed; `TCK-FIX-003` landed and
    has not claimed to be done, which is the stated excluded category asserted rather than
    asserted about; `TCK-FIX-004` is finished, landed, and has its report. A rule that named any
    of them would be green on this corpus for the wrong reason.
    """
    root = a_repository_with_a_landed_ticket(tmp_path)
    assert landed_without_a_report(root) == {}, (
        "a corpus in which every landed ticket has its report must be clean", 
        landed_without_a_report(root))

    (root / "docs" / "reports" / "TCK-FIX-001-report.md").unlink()

    offenders = landed_without_a_report(root)
    assert sorted(offenders) == ["TCK-FIX-001"], (
        "one report was removed from a landed ticket and the rule did not name it, or named "
        "somebody else with it", offenders)
    assert "TCK-FIX-001-report.md" in offenders["TCK-FIX-001"], (
        "the finding must say which file is missing", offenders["TCK-FIX-001"])


# ── SRV-FIX-532 cluster D1 — two SERVERS on one repository ────────────────────────────────────
#
# `SRV-FIX-207`, and round 1's QA FAIL. `test_two_concurrent_callers_get_different_ids` above
# fires eight callers at ONE server and is green, because one process serialises them on its own
# queue. The defect is one process out: `catalog::Ids::init` reads `.factory/ids.json` ONCE into
# `spent_`, and every allocation after that is `max(highest id on disk, spent_[series]) + 1` from
# that in-memory map. A second server on the same repository read the same ledger at ITS start and
# has its own `spent_`, so the two walk the same range from the same place.
#
# QA's round-1 probe, verbatim — two servers built from this branch, both `--repo` this repository,
# six `POST /api/tickets/ids` each released from one FIFO barrier:
#
#     $ grep -o '"id":"[^"]*"' qa-d1-two-servers.log | sort | uniq -c
#           2 "id":"SRV-FIX-549"
#           2 "id":"SRV-FIX-548"
#           2 "id":"SRV-FIX-547"
#           2 "id":"SRV-FIX-546"
#           2 "id":"SRV-FIX-545"
#           2 "id":"SRV-FIX-544"
#
# Twelve allocations, six ids, every one handed out twice — not a race on the margin. And the
# ledger afterwards holds only the high-water mark, which is what makes the duplicates invisible
# to anything that reads it later: `{"series": {"SRV": 549}}`.


def two_servers_on(root, serve):
    """Two independent server processes pointed at ONE repository.

    The whole of the defect is that they are two PROCESSES: no in-process queue reaches across
    them, and `CLAUDE.md` forbids a mutex in event-driven code besides. `serve` gives each its own
    ephemeral ports, so nothing but the repository is shared — which is the shape a person gets
    when they leave one instance running and start another, and the shape two lanes get through
    two instances.
    """
    return serve(root), serve(root)


def ids_from(answers):
    """`(every id handed out, in order)`, with every non-201 raised as the failure it is."""
    refused = [(r.status_code, r.text[:200]) for r in answers if r.status_code != 201]
    assert not refused, f"{len(refused)} of {len(answers)} callers were refused: {refused}"
    return [r.json()["id"] for r in answers]


def test_two_servers_on_one_repository_never_hand_out_one_id(repo, serve):
    """§1 D1 — two servers, one repository, fired at both at once: no id is handed out twice.

    Transcribed from QA's round-1 reproduction. N = 6 per server from a single barrier — §1 asks
    for N ≥ 4 each — and the assertion is the one that failed: **twelve allocations, twelve
    distinct ids.**

    Written with the callers released TOGETHER and never as a loop, because a sequential loop is
    green against a read-then-write with no exclusion, which is the shape the race lives in: one
    caller cannot tell a counter from a race (`SRV-FIX-188`).

    Contiguity is asserted too. A race that hands out {544, 544, 545, …} and one that hands out
    {544, 9999, …} are the same defect, and only *the twelve numbers after the high-water mark*
    refuses both.
    """
    id_repo(repo, ["FIX-039", "FIX-041", "FEAT-040"])
    repo.mkdir(f"{SUB}/docs/tickets")
    root = repo.manifest(two_repo_manifest())
    first, second = two_servers_on(root, serve)

    N = 6
    ready = threading.Barrier(2 * N)

    def fire(srv):
        ready.wait(timeout=30)
        return allocate(srv, "FIX")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2 * N) as pool:
        answers = list(pool.map(fire, [first] * N + [second] * N))

    handed = ids_from(answers)
    twice = sorted(i for i in set(handed) if handed.count(i) > 1)
    assert not twice, (
        f"{len(handed)} allocations from two servers on one repository were handed "
        f"{len(set(handed))} distinct ids — these were issued more than once: {twice}\n"
        f"all of them, in order: {handed}\n"
        f"Two sessions filing at once get the same id, the second document collides with the "
        f"first, and `409 ambiguous_ticket_id` makes both unreachable — SRV-FIX-188's escape.")

    numbers = sorted(r.json()["number"] for r in answers)
    assert numbers == list(range(42, 42 + 2 * N)), (
        f"the ids are not the {2 * N} numbers after the high-water mark 41: {numbers}")


def test_an_id_another_factory_took_is_not_reissued_after_it_is_written(repo, serve):
    """§1 D1 — the ledger is consulted at EVERY allocation, not once at start.

    The other half of the same cause, and the one a barrier cannot show: server A allocates and
    its ledger write lands on disk; server B — already running, its own `spent_` read long before —
    is then asked, and must not hand back what A took. No concurrency at all here, so a fix that
    only serialises two simultaneous callers passes the case above and fails this one.

    Both servers are started BEFORE either allocates, deliberately: a server started afterwards
    would read the new ledger at init and be right for the wrong reason.
    """
    id_repo(repo, ["FIX-039", "FIX-041", "FEAT-040"])
    repo.mkdir(f"{SUB}/docs/tickets")
    root = repo.manifest(two_repo_manifest())
    first, second = two_servers_on(root, serve)

    mine = [allocated(first, "FIX") for _ in range(3)]
    took = [b["id"] for b in mine]
    assert len(set(took)) == 3, f"one server handed out a duplicate on its own: {took}"

    # The series the ledger keys by is the PRODUCT's answer, not a string typed here: `E26`
    # reports it on every allocation, and a name this case invented would make the read below
    # pass or fail for a reason that has nothing to do with the ledger.
    series = mine[-1]["series"]
    ledger = json.loads((Path(root) / ".factory" / "ids.json").read_text())
    assert ledger["series"].get(series) == mine[-1]["number"], (
        f"the first server's allocations are not on disk, so the second has nothing to read and "
        f"this case is about nothing: series={series!r} ledger={ledger}")

    after = [allocated(second, "FIX")["id"] for _ in range(3)]
    clash = sorted(set(took) & set(after))
    assert not clash, (
        f"the second server reissued ids the first had already taken and WRITTEN: {clash}\n"
        f"first server: {took}\nsecond server: {after}\n"
        f"The ledger on disk said {ledger['series'][series]} and the second server answered from "
        f"the high-water mark it read at its own start.")


def test_a_ledger_that_cannot_be_taken_refuses_rather_than_guessing(repo, serve):
    """§1 D-NEG — when the exclusion cannot be taken, `E26` answers an error and allocates nothing.

    MANDATORY NEGATIVE. The obvious wrong fix for the two cases above is *read the ledger again and
    hope*, and it passes both of them on a quiet machine. What tells the two apart is what happens
    when the ledger cannot be held: an allocator that guesses answers `201` with a number it cannot
    defend, and one that refuses says so and writes nothing.

    The ledger is made unwritable rather than deleted — a missing ledger is a first run, which is
    legal and must keep working; an unwritable one is the state a stale lock or a read-only
    checkout leaves, and it is the one that must refuse.
    """
    id_repo(repo, ["FIX-039", "FIX-041", "FEAT-040"])
    repo.mkdir(f"{SUB}/docs/tickets")
    root = repo.manifest(two_repo_manifest())
    srv = serve(root)

    mine = allocated(srv, "FIX")
    assert mine["number"] == 42, mine

    ledger = Path(root) / ".factory" / "ids.json"
    assert ledger.is_file(), f"{ledger} was never written, so this case has nothing to hold shut"
    before = ledger.read_text()
    holder = Path(root) / ".factory"
    holder.chmod(0o500)
    try:
        r = allocate(srv, "FIX")
        assert r.status_code != 201, (
            f"E26 handed out `{r.json().get('id')}` with a ledger it could not write. An id the "
            f"tool cannot record is an id it will hand out again.")
        assert 400 <= r.status_code < 600, r.status_code
        said = json.dumps(r.json())
        assert "ledger" in said or "ids.json" in said, (
            f"the refusal does not name what could not be taken: {said}")
    finally:
        holder.chmod(0o700)

    assert ledger.read_text() == before, (
        "the ledger moved while it could not be written, so a later reader will skip an id "
        "nobody was given")


# ── SRV-FIX-532 cluster D2 — two ids for one defect ───────────────────────────────────────────
#
# `SRV-FIX-521`, and the inverse of D1. A lane is a checkout cut at claim time, so `docs/tickets/`
# in one lane cannot hold a ticket another lane wrote after the cut, and each files against what it
# can see. On `SRV-FIX-379` in one day that produced FOUR ids for TWO defects — every id allocated
# correctly, so `409 ambiguous_ticket_id` could never fire and nothing in the tool, the skills or
# the gate ever compares a new ticket against one filed in a sibling lane.
#
# The gate's job is to put the candidates in front of a person. Deciding that two tickets are one
# cause is a person's call in every case, and the check never closes one.


CAUSE = "the guard reads any non-zero pytest exit as the world reddened it"

# Words that carry no cause. Excluded by a stated CATEGORY — the vocabulary every ticket title in
# this repository is written in — rather than by deleting rows until the sweep passes.
EMPTY_WORDS = frozenset("""
a an and are as at be because before but by can cannot could does for from has have in into is it
its no not of on one only or so than that the their them then there these they this to two up was
what when where which while who why will with without
""".split())

OPEN_STATUSES = frozenset({"todo", "in-progress", "in progress", "awaiting-qa", "open"})


def _pair_records(tickets):
    """`{id: (path, front, cause words, who found it)}` over every ticket document in a directory.

    `_*.md` and `README.md` are not documents — `catalog::Store::isDocument` skips them, and
    `SRV-FIX-043` and `SRV-FIX-263` are what it cost to learn that the convention lived only in
    the suite.
    """
    import re

    out = {}
    for path in sorted(Path(tickets).glob("*.md")):
        if path.name.startswith("_") or path.name == "README.md":
            continue
        text = path.read_text(errors="replace")
        head = text.split("\n---", 2)
        front = dict(re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$",
                                head[0][4:] if head[0].startswith("---\n") and len(head) > 1
                                else "", re.M))
        tid = front.get("id", "").strip()
        if not tid:
            continue
        words = {w for w in re.findall(r"[a-z0-9]+", front.get("title", "").lower())
                 if w not in EMPTY_WORDS and len(w) > 2}
        found = re.search(r"^-\s+\*\*Found by:\*\*\s*(.+)$", text, re.M)
        out[tid] = (path, front, words, (found.group(1).strip() if found else ""))
    return out


def pairs_compared(tickets):
    """How many ticket documents the pair rule actually read. A number, not a promise."""
    return len(_pair_records(tickets))


def _number(tid):
    import re

    m = re.search(r"(\d+)$", tid)
    return int(m.group(1)) if m else 0


def _found_key(said):
    """The ticket and the day a `Found by:` line names, which is what makes two filings siblings."""
    import re

    ticket = re.search(r"\b([A-Z]+-[A-Z]+-\d+)\b", said)
    day = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", said)
    return (ticket.group(1) if ticket else "", day.group(1) if day else "")


def refuse_open_pairs(tickets):
    """`""` when no OPEN pair looks like one cause filed twice, else the report naming both ids.

    Two tickets are candidates when they are both open, were found by the SAME ticket on the SAME
    day — which is what a lane cut at claim time produces — and their titles name the same cause.
    *The same cause* is asked of the words, not of the string: the two real pairs were reworded
    between lanes, so equality would have found neither.

    The survivor named first is the earlier `created:`, with the lower number breaking a tie. That
    is the rule a person applied to both real pairs by hand and the check may not invent another.
    Nothing is ever closed here: the gate puts the candidates in front of somebody.
    """
    records = _pair_records(tickets)
    assert records, (
        f"the pair rule compared no ticket in {tickets} — a check that sweeps nothing has not run")

    closed, dangling = {}, []
    for tid, (_path, front, _words, _found) in records.items():
        of = front.get("duplicate_of", "").strip()
        if not of:
            continue
        closed.setdefault(tid, set()).add(of)
        closed.setdefault(of, set()).add(tid)
        if of not in records:
            dangling.append(f"{tid}: duplicate_of names {of}, which is not in this repository")

    open_ids = [tid for tid, (_p, front, _w, _f) in records.items()
                if front.get("status", "").strip() in OPEN_STATUSES]

    lines = list(dangling)
    for i, one in enumerate(sorted(open_ids)):
        for other in sorted(open_ids)[i + 1:]:
            if other in closed.get(one, set()):
                continue
            _p1, f1, w1, said1 = records[one]
            _p2, f2, w2, said2 = records[other]
            if not said1 or _found_key(said1) != _found_key(said2):
                continue
            if not w1 or w1 != w2:
                continue
            first, second = sorted(
                (one, other),
                key=lambda t: (records[t][1].get("created", ""), _number(t)))
            lines.append(
                f"{first} and {second} were both filed for one cause — "
                f"found by {_found_key(said1)[0]} on {_found_key(said1)[1]}, "
                f"{first} from {said1 if first == one else said2}, "
                f"{second} from {said2 if first == one else said1}. "
                f"{first} is the earlier `created:` and the lower number, so it survives.")
    return "\n".join(lines)




def pair_repo(root, *, first="SRV-FIX-407", second="SRV-FIX-412", closed=False,
              duplicate_of=None, extra=()):
    """A repository holding two `todo` tickets filed for one cause from two lanes.

    Modelled on the real pair: same `Found by` ticket and day, same cause in the title, filed
    hours apart from a QA role lane and from the ticket lane.
    """
    tickets = root / "docs" / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)

    def write(tid, when, lane, status, dup=None):
        front = [f"id: {tid}", "parent: standalone", "workspace: factory-server",
                 f"title: {CAUSE}", f"status: {status}", f"created: {when}"]
        if dup:
            front.append(f"duplicate_of: {dup}")
        (tickets / f"{tid}-{tid.lower()}.md").write_text(
            "---\n" + "\n".join(front) + "\n---\n\n"
            f"# Factory Ticket — {CAUSE}\n\n## 1. Request\n\n"
            f"- **Found by:** `SRV-FIX-379` round 3, 2026-09-11, from {lane}.\n")

    write(first, "2026-09-11", "the QA role lane", "todo")
    write(second, "2026-09-11", "the ticket lane",
          "duplicate" if closed else "todo", duplicate_of)
    for tid, title in extra:
        (tickets / f"{tid}-x.md").write_text(
            "---\n" + "\n".join([f"id: {tid}", "parent: standalone",
                                 "workspace: factory-server", f"title: {title}",
                                 "status: todo", "created: 2026-09-11"])
            + "\n---\n\n# Factory Ticket\n\n## 1. Request\n\n- **Found by:** somebody else.\n")
    return tickets


def test_two_open_tickets_for_one_cause_are_named_together(tmp_path):
    """§1 D2 — a surviving pair is reported, by BOTH ids, with which lane each came from.

    `SRV-FIX-521` criterion 1. The pair is two `todo` tickets whose `Found by` names the same
    ticket and the same day and whose titles name the same cause — the real shape, not an
    invented one.

    The report also names which id survives, and the rule is the one a person applied to both real
    pairs by hand: **the earlier `created:`, and the lower number breaks a tie.** The gate does not
    close either — it puts the candidates in front of somebody.
    """
    tickets = pair_repo(tmp_path)
    said = refuse_open_pairs(tickets)
    assert said, "a repository holding the real pair was passed"
    assert "SRV-FIX-407" in said and "SRV-FIX-412" in said, (
        f"the report names one id and not the other, which is half a pair: {said}")
    assert "QA role lane" in said and "ticket lane" in said, (
        f"the report does not say which lane each was filed from: {said}")
    assert said.index("SRV-FIX-407") < said.index("SRV-FIX-412"), (
        f"the earlier `created:` and lower number must be named first as the survivor: {said}")


def test_a_pair_closed_as_a_duplicate_is_not_reported(tmp_path):
    """§1 D2 — a pair somebody has already decided is a decision, not a finding.

    `SRV-FIX-521` criterion 2. A gate that keeps reporting a resolved pair teaches people to
    ignore it. The same repository, with the later ticket carrying `status: duplicate` and
    `duplicate_of:` naming the earlier, passes — and the case asserts the pair is STILL there, so
    it cannot pass by the tickets having vanished.
    """
    tickets = pair_repo(tmp_path, closed=True, duplicate_of="SRV-FIX-407")
    assert len(list(tickets.glob("*.md"))) == 2, sorted(p.name for p in tickets.glob("*.md"))
    said = refuse_open_pairs(tickets)
    assert not said, (
        f"a pair already closed as a duplicate is still reported, so the gate is noise: {said}")


def test_the_pair_rule_refuses_an_empty_sweep_and_a_dangling_duplicate_of(tmp_path):
    """§1 D-NEG — the check cannot pass by examining nothing, and cannot invent a pair.

    `SRV-FIX-521` criterion 3, mandatory negative, three members:

      1. a repository of DISTINCT causes passes, and says how many tickets it compared — and that
         number is the real one, so *"it found nothing"* and *"it looked at nothing"* are told
         apart;
      2. a sweep over zero tickets FAILS rather than reporting zero — a check that sweeps nothing
         has not run;
      3. a `duplicate_of:` naming an id that is not in the repository fails, naming that id — the
         escape hatch may not be a way to silence the rule with a typo.
    """
    distinct = tmp_path / "distinct" / "docs" / "tickets"
    distinct.mkdir(parents=True)
    pair_repo(tmp_path / "distinct", first="SRV-FIX-601", second="SRV-FIX-602")
    for name in sorted(distinct.glob("*.md")):
        text = name.read_text()
        if "SRV-FIX-602" in text:
            name.write_text(text.replace(f"title: {CAUSE}", "title: something else entirely")
                                .replace("- **Found by:** `SRV-FIX-379` round 3, 2026-09-11, from "
                                         "the ticket lane.",
                                         "- **Found by:** `SRV-FIX-500` round 1, 2026-09-02."))
    said = refuse_open_pairs(distinct)
    assert not said, f"two distinct causes were reported as a pair: {said}"
    counted = pairs_compared(distinct)
    assert counted == 2, f"the sweep says it compared {counted} tickets and there are 2"

    empty = tmp_path / "empty" / "docs" / "tickets"
    empty.mkdir(parents=True)
    with pytest.raises(AssertionError, match="compared no ticket"):
        refuse_open_pairs(empty)

    dangling = pair_repo(tmp_path / "dangling", closed=True, duplicate_of="SRV-FIX-99999")
    said = refuse_open_pairs(dangling)
    assert "SRV-FIX-99999" in said, (
        f"a `duplicate_of:` naming a ticket that does not exist silenced the rule: {said}")


@pytest.mark.repository
def test_this_repository_has_no_unclosed_pair_filed_for_one_cause():
    """The rule run over this repository's own backlog, which is what makes it a gate.

    `SRV-FIX-521`'s pairs are real and were closed by hand; this is what stops the next two being
    filed and forgotten. A pair a person has decided about carries `duplicate_of:` and is silent.
    """
    tickets = _this_repository() / "docs" / "tickets"
    counted = pairs_compared(tickets)
    assert counted > 100, (
        f"the sweep compared {counted} tickets in this repository, which is not the backlog")
    said = refuse_open_pairs(tickets)
    assert not said, (
        "these open tickets look like one cause filed twice from two lanes. A lane is a checkout "
        "cut at claim time, so neither could see the other.\n" + said
        + "\nDecide which survives — the earlier `created:`, lower number breaking a tie — and "
          "give the other `status: duplicate` and `duplicate_of:`.")


# ── SRV-FIX-532 round 2 — a rule about TWO repositories, checked from inside one ───────────────
#
# `S4` binds a lane to the repository it is a lane OF and nothing above it, so a rule that compares
# this repository against the one it is cloned into cannot be answered from inside a lane. That is
# the boundary working. What it must never become is a silent pass: `docs/operations/ESCAPES.md` carries the
# row for a guarded section reading `n/n passed` without running.


def prompt_copies_that_differ(root, other):
    """Which of the declared prompt files differ between two repositories."""
    drifted = []
    for name in ("run-ticket.md", "qa-ticket.md"):
        mine = Path(root) / ".factory" / "prompts" / name
        theirs = Path(other) / ".factory" / "prompts" / name
        if not mine.is_file() or not theirs.is_file():
            drifted.append(f"{name}: missing on one side")
        elif mine.read_bytes() != theirs.read_bytes():
            drifted.append(name)
    return drifted


def test_two_prompt_copies_that_differ_are_still_refused(tmp_path):
    """The third state may not be reached by a check that has stopped comparing.

    MANDATORY NEGATIVE for `the_enclosing_monorepo_or_why_not`. Two repositories the case CAN see,
    whose prompts differ by one byte, are still named — so *not answerable here* is a statement
    about reachability and never about the comparison having been deleted.

    Driven both ways in one case: identical copies are silent, and a one-byte difference is named.
    """
    same, other = tmp_path / "same", tmp_path / "other"
    for root in (same, other):
        (root / ".factory" / "prompts").mkdir(parents=True)
        for name in ("run-ticket.md", "qa-ticket.md"):
            (root / ".factory" / "prompts" / name).write_text("the same instructions\n")
    assert not prompt_copies_that_differ(same, other), (
        "two identical copies were reported as drifted, so the rule reports noise")

    (other / ".factory" / "prompts" / "qa-ticket.md").write_text("the same instructions!\n")
    said = prompt_copies_that_differ(same, other)
    assert said == ["qa-ticket.md"], (
        f"a one-byte difference between the two copies was not named: {said}")


UNASKED_HELPER = "the_enclosing_monorepo_or_why_not"


def cases_that_pass_when_the_rule_was_not_asked(source):
    """Every `test_*` in `source` whose *could not reach the second repository* branch passes.

    The helper above answers `(None, why)` where the repository a rule is ABOUT cannot be read,
    and a case handling that answer has exactly two honest endings: fail, or assert something
    about `why` — which is what a case whose SUBJECT is the third state does. A branch that
    reports the miss and returns is the third: it ends the test with no assertion, and pytest
    scores no assertion as a pass.

    Read off the syntax rather than off a list, so a case added later is covered by having been
    written and not by somebody remembering this rule. Returns `[(case, line)]`.
    """
    import ast

    def reaches_a_verdict(statements):
        for node in statements:
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Assert, ast.Raise)):
                    return True
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and sub.func.attr in ("fail", "skip", "xfail", "exit"):
                    return True
        return False

    def asked_by(node):
        """The name the helper's first answer was bound to, or None."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call) \
                    and getattr(sub.value.func, "id", None) == UNASKED_HELPER \
                    and isinstance(sub.targets[0], ast.Tuple):
                first = sub.targets[0].elts[0]
                return getattr(first, "id", None)
        return None

    def is_the_miss_branch(test, name):
        return (isinstance(test, ast.Compare)
                and getattr(test.left, "id", None) == name
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None)

    guilty = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        name = asked_by(node)
        if name is None:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.If) and is_the_miss_branch(sub.test, name) \
                    and not reaches_a_verdict(sub.body):
                guilty.append((node.name, sub.lineno))
    return guilty


@pytest.mark.repository
def test_a_rule_that_could_not_be_asked_never_reports_a_pass():
    """§1 B1, round 3 — the class, not the one site.

    `qa-ticket.md` differed between the two manifests from before this ticket was written until
    the operator ran the gate from the developer's checkout on 2026-09-12, and §1 names that
    difference as one of the three reds the ticket exists to close. The rule that was supposed to
    see it reported a PASS instead, in every placement the tool itself can produce: a lane cannot
    read the repository above it, the case said so, and then it returned.

    `docs/operations/ESCAPES.md`'s most repeated row is *a class is fixed at the site the verdict named* —
    55% of that ledger — and the cause here had two sites, one screen apart. So the check is the
    class: no case in this file may end its *could not ask* branch without a verdict.
    """
    guilty = cases_that_pass_when_the_rule_was_not_asked(Path(__file__).read_text())
    assert not guilty, (
        "these cases report that they could not reach the repository they are about and then "
        "pass, so a green run of this suite says nothing about them:\n  "
        + "\n  ".join(f"{name} (line {line})" for name, line in guilty))


@pytest.mark.repository
def test_the_unasked_rule_sweep_reads_the_branch_and_not_the_wording():
    """§1 B-NEG, round 3 · MANDATORY NEGATIVE — three members.

    Without this the sweep above could return `[]` for any reason at all and read as green:

      1. the shape that shipped — report the miss, return — is REPORTED, and by the case's name;
      2. the two honest endings are NOT reported: `pytest.fail`, and asserting about `why`, which
         is what a case whose subject IS the third state does;
      3. a file holding no case that calls the helper sweeps to nothing, so *it found nothing* and
         *it looked at nothing* are told apart by the reader being handed something to find.
    """
    guilty_source = (
        "def test_a_rule():\n"
        "    enclosing, why = the_enclosing_monorepo_or_why_not()\n"
        "    if enclosing is None:\n"
        "        print('NOT ANSWERABLE HERE — ' + why)\n"
        "        return\n"
        "    assert enclosing.is_dir()\n")
    found = cases_that_pass_when_the_rule_was_not_asked(guilty_source)
    assert [name for name, _ in found] == ["test_a_rule"], (
        f"the shape that shipped is not reported: {found}")

    for honest in (
        "def test_b():\n"
        "    enclosing, why = the_enclosing_monorepo_or_why_not()\n"
        "    if enclosing is None:\n"
        "        pytest.fail('NOT ASKED, AND THEREFORE RED — ' + why)\n"
        "    assert enclosing.is_dir()\n",
        "def test_c():\n"
        "    enclosing, why = the_enclosing_monorepo_or_why_not()\n"
        "    if enclosing is None:\n"
        "        assert 'S4' in why\n"
        "    else:\n"
        "        assert not why\n",
    ):
        assert cases_that_pass_when_the_rule_was_not_asked(honest) == [], (
            f"an honest ending is reported as a silent pass:\n{honest}")

    assert cases_that_pass_when_the_rule_was_not_asked(
        "def test_d():\n    assert 1 == 1\n") == [], "a file that never asks was judged anyway"


@pytest.mark.repository
def test_a_rule_about_two_repositories_says_when_it_cannot_see_both():
    """The third state is REPORTED, and it is the boundary that produces it — not a convenience.

    `SRV-FIX-541`. Where the enclosing monorepo is reachable, the answer is a root and no excuse;
    where it is not, the reason names `S4` and the repository that could not be reached, so a
    reader of a green run can tell *the rule held* from *the rule was not asked*.

    Both branches are asserted, so this case cannot pass by the helper always answering one way.
    """
    enclosing, why = the_enclosing_monorepo_or_why_not()
    if enclosing is None:
        assert "S4" in why and str(_this_repository()) in why, (
            f"the refusal does not say which boundary produced it or which repository it is "
            f"about: {why!r}")
        print(f"\nNOT ANSWERABLE HERE — {why}")
    else:
        assert not why, f"a reachable monorepo came back with an excuse attached: {why!r}"
        assert (enclosing / MANIFEST_REL).is_file(), enclosing
        assert enclosing != _this_repository(), (
            f"the enclosing monorepo and this repository are one directory ({enclosing}), so a "
            f"rule comparing the two compares a file with itself")


# ── SRV-FEAT-547 · a requirement's `parent:` is followed across declared repositories ═════════
#
# `docs/product/SPEC.md` §2's `parent` row moves from *ticket* to *requirement · ticket*. The link is by
# id across every repository the manifest declares (`S1`: no path is named), derived per request
# from the listing `Store::requirements()` already returns (`S2`: no index).
#
# E3's `requirements` array stays FLAT and the tree is the two link fields. That is the sentence
# `FAC-EPIC-377` §3 and `WEB-FEAT-411` §1 are both written on — the client resolves a `children`
# id AGAINST THE BODY (its second negative is "an E3 row whose `children` names an id absent from
# the body"), and criterion 1 below reads `T1`'s own `children`, `own_ticket_count` and
# `ticket_count` off E3 while `T1` is not a top-level row. Neither is answerable from an array
# holding roots only.

import os                                                                      # noqa: E402
import shutil                                                                  # noqa: E402
import subprocess                                                              # noqa: E402

import jsonschema                                                              # noqa: E402

from test_delivery import (AGENT_STAYS_UP, gitrepo, serve_fast,                # noqa: E402,F401
                           wait_for_process, wait_for_state)

FACTORY_ROOT = Path(__file__).resolve().parents[2]
HTTP_SCHEMA = json.loads((FACTORY_ROOT / "docs/technical/contracts/http.schema.json").read_text())
API_TYPES = FACTORY_ROOT / "client" / "web" / "src" / "types" / "api.ts"
GEN_API_TYPES = FACTORY_ROOT / "client" / "web" / "scripts" / "gen-api-types.mjs"

# The three keys `FAC-EPIC-377` §3 freezes onto an E3 row, and the ONE whose meaning changes.
NEW_E3_KEYS = ("parent", "children", "own_ticket_count")

# `docs/reports/SRV-FEAT-547/e3-flat-2026-09-16.json` — E3's body for `flat_target()` as the
# server answered it BEFORE this ticket, captured from the real binary. Re-capture with:
#
#     cd server && ../.cache/server/build/factory-server   # the build under test
#     python3 tests/capture_e3_flat.py docs/reports/SRV-FEAT-547/e3-flat-<date>.json
#
# A baseline typed out here instead would be this session's opinion of what the server used to
# answer, and criterion 4 is precisely the question of whether that opinion is right.
E3_FLAT_BASELINE = (FACTORY_ROOT / "docs" / "reports" / "SRV-FEAT-547"
                    / "e3-flat-2026-09-16.json")

SUB_TEAM = "team"
TREE_DOCS = {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"}


def tree_manifest(extra=None):
    """Two declared repositories, one workspace in each — `SRV-FIX-181`'s block."""
    m = {
        "factory": "0.1",
        "repos": {
            "root": {"path": ".", "docs": dict(TREE_DOCS)},
            "team": {"path": SUB_TEAM, "docs": dict(TREE_DOCS)},
        },
        "workspaces": {"alpha": {"repo": "root", "path": "."},
                       "beta": {"repo": "team", "path": "."}},
    }
    if extra:
        m.update(extra)
    return m


def tree_target(repo, extra=None):
    """§1's fixture: the roll-up has THREE kinds of member — own, child and grandchild.

        root/  R1  (no parent)          t0
               R2  (no parent)
        team/  T1  (parent: R1)         t1 t2
               T2  (parent: R1)         t3
               T3  (parent: T1)         t4

    R1 therefore rolls up to 5 (own 1 + T1's 2 + T3's 1 + T2's 1) and T1 to 3. A one-level
    fixture would make the roll-up indistinguishable from "add the direct children".
    """
    repo.write("docs/epics/R1.md",
               doc({"id": "R1", "title": "The product requirement", "status": "draft"}, "One."))
    repo.write("docs/epics/R2.md",
               doc({"id": "R2", "title": "A requirement with nothing under it",
                    "status": "draft"}, "Two."))
    repo.write("docs/tickets/t0.md",
               doc({"id": "t0", "parent": "R1", "workspace": "alpha",
                    "title": "The product requirement's own ticket", "status": "todo"}, "a"))

    repo.write(f"{SUB_TEAM}/docs/epics/T1.md",
               doc({"id": "T1", "parent": "R1", "title": "A team requirement",
                    "status": "active"}, "Three."))
    repo.write(f"{SUB_TEAM}/docs/epics/T2.md",
               doc({"id": "T2", "parent": "R1", "title": "A second team requirement",
                    "status": "draft"}, "Four."))
    repo.write(f"{SUB_TEAM}/docs/epics/T3.md",
               doc({"id": "T3", "parent": "T1", "title": "A requirement under a team requirement",
                    "status": "draft"}, "Five."))
    for tid, parent in (("t1", "T1"), ("t2", "T1"), ("t3", "T2"), ("t4", "T3")):
        repo.write(f"{SUB_TEAM}/docs/tickets/{tid}.md",
                   doc({"id": tid, "parent": parent, "workspace": "beta",
                        "title": f"The work of {tid}", "status": "todo"}, "b"))
    repo.mkdir("docs/reports")
    repo.mkdir(f"{SUB_TEAM}/docs/reports")
    return repo.manifest(tree_manifest(extra))


def rows_of(body):
    return {r["id"]: r for r in body["requirements"]}


def top_level(body):
    """The rows a surface draws at the top — §4.2's, and `FAC-EPIC-377` §3's own word."""
    return [r["id"] for r in body["requirements"] if r["parent"] == ""]


def warnings_coded(body, code):
    return [w for w in body.get("warnings", []) if w.get("code") == code]


def test_requirement_parent_is_followed_across_repos(repo, serve):
    """§1 criterion 1 — the link crosses the repository boundary and the counts roll up.

    The two numbers are asserted on the SAME row as the collection they are about: a row's
    `children` beside the count that spans them, and `own_ticket_count` beside the roll-up, so a
    number that agreed with the wrong set could not read as green.
    """
    srv = serve(tree_target(repo))
    body = ok(srv, "/api/requirements")
    rows = rows_of(body)

    assert set(rows) == {"R1", "R2", "T1", "T2", "T3"}, (
        f"E3 must carry every requirement — the tree is the links, and `WEB-FEAT-411` resolves a "
        f"`children` id against this body: {sorted(rows)}")
    assert top_level(body) == ["R1", "R2"], (
        f"only the requirements that name no parent are top-level rows: {top_level(body)}")

    assert rows["R1"]["parent"] == "", rows["R1"]
    assert rows["R1"]["children"] == ["T1", "T2"], (
        f"direct children, in `Store::requirements()` order: {rows['R1']}")
    assert rows["R1"]["own_ticket_count"] == 1, rows["R1"]
    assert rows["R1"]["ticket_count"] == 5, (
        f"the roll-up is own 1 + T1's 2 + T3's 1 + T2's 1 = 5, and a grandchild is a member of "
        f"it: {rows['R1']}")

    assert rows["T1"]["parent"] == "R1", rows["T1"]
    assert rows["T1"]["children"] == ["T3"], rows["T1"]
    assert rows["T1"]["own_ticket_count"] == 2, rows["T1"]
    assert rows["T1"]["ticket_count"] == 3, rows["T1"]

    for nested in ("T1", "T2", "T3"):
        assert rows[nested]["parent"] != "", (
            f"{nested} names a parent and is still drawn at top level: {rows[nested]}")

    assert rows["R2"]["children"] == [] and rows["R2"]["ticket_count"] == 0, rows["R2"]
    assert rows["R2"]["own_ticket_count"] == 0, rows["R2"]
    assert body["total"] == len(body["requirements"]) == 5, body["total"]
    assert warnings_coded(body, "unknown_parent") == [], body["warnings"]
    assert warnings_coded(body, "parent_cycle") == [], body["warnings"]


def test_requirement_page_carries_child_requirements(repo, serve):
    """§1 criterion 2 — E4 carries each DIRECT child with that child's own page's groups.

    The groups are compared against `GET /api/requirements/T1` itself rather than restated here:
    a shape asserted against a sibling response cannot drift from it, and a list of field names
    typed into this file could only ever agree with whoever typed it.
    """
    srv = serve(tree_target(repo))
    page = ok(srv, "/api/requirements/R1")

    assert [c["id"] for c in page["children"]] == ["T1", "T2"], (
        f"direct children only, in E3's order — T3 hangs off T1 and belongs on T1's page: "
        f"{[c['id'] for c in page['children']]}")

    for child in page["children"]:
        assert {"id", "title", "repo", "status", "own_ticket_count", "ticket_count",
                "delivery", "tickets"} <= set(child), child

    t1 = next(c for c in page["children"] if c["id"] == "T1")
    own = ok(srv, "/api/requirements/T1")
    assert t1["tickets"] == own["groups"], (
        f"E4's child entry must reuse E4's own `groups` shape, byte for byte:\n"
        f"  under R1: {t1['tickets']}\n  T1's page: {own['groups']}")
    assert t1["repo"] == "team", f"the manifest's own name for the repository: {t1}"
    assert t1["title"] == "A team requirement" and t1["status"] == "active", t1
    assert t1["own_ticket_count"] == 2 and t1["ticket_count"] == 3, t1
    assert [t["id"] for g in t1["tickets"] for t in g["tickets"]] == ["t1", "t2"], t1

    t2 = next(c for c in page["children"] if c["id"] == "T2")
    assert t2["repo"] == "team" and t2["own_ticket_count"] == 1, t2
    assert [t["id"] for g in t2["tickets"] for t in g["tickets"]] == ["t3"], t2

    assert [t["id"] for g in page["groups"] for t in g["tickets"]] == ["t0"], (
        f"R1's own groups hold its own ticket and nothing a child owns: {page['groups']}")

    flat = ok(srv, "/api/requirements/R2")
    assert flat["children"] == [], f"a requirement nobody names carries no children: {flat}"


def tree_lane_target(repo, gitrepo):
    """`tree_target` inside a git repository with lane seats and an agent a lane can really run.

    Delivery is a fact about git and the lane set (`SPEC.md` §3.5), so a roll-up over delivery
    states can only be measured on a target that HAS a history and can hold a live lane.
    """
    root = tree_target(repo, extra={
        "lanes": {"host": 2},
        "stages": ["SPEC", "IMPL", "GATE"],
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": "claude"},
    })
    repo.write(".gitignore",
               ".factory/worktrees/\n.factory/runs/\n.factory/lanes/\n.factory/state/\n"
               ".factory/ids.json\n")
    repo.write("bin/claude", AGENT_STAYS_UP).chmod(0o755)
    # `t0` is DONE by §3.5 rule 2: its delivery report is tracked on the base branch.
    repo.write("docs/reports/t0-report.md", "# t0 — delivered\n\nNo run is cited here.\n")
    gitrepo.init()
    gitrepo.commit("the repository as it stands, t0's report included")
    return root


def live_lane_for(srv, ticket_id, workspace):
    """A lane the tool owns, holding a process that is really alive — `S5`'s own authority.

    The live process comes from the repository's `_test/lane-process` hook rather than from a
    real agent, and that is a finding rather than a convenience: `lane/confine.cpp:445` read-binds
    the agent binary's directory BEFORE `--tmpfs /tmp` at `:461`, so for a repository under `/tmp`
    — which is every fixture in this suite — the agent dies at `execvp` and `running` lasts
    milliseconds. Filed as `SRV-FIX-548`; measured crashed on 8 runs out of 8.

    What this case reduces is DELIVERY STATES, and `SPEC.md` §3.5 rule 1 reads them off `E9`. The
    hook puts a real, killable child in the lane, so `E9` answers about a process that exists —
    nothing here stands in for the value being reduced.
    """
    claimed = srv.post("/api/lanes", json={"ticket_id": ticket_id, "workspace": workspace})
    assert claimed.status_code == 201, f"E12 → {claimed.status_code} {claimed.text}"
    lane = claimed.json()["lane"]
    attached = srv.post("/api/_test/lane-process",
                        json={"lane": lane, "command": "while : ; do sleep 0.2 ; done"})
    assert attached.status_code == 202, (
        f"the lane-process hook → {attached.status_code} {attached.text}")
    wait_for_process(srv, lane, "running")
    return lane


def still_live(srv, lane):
    """E9's word for this lane, asserted BESIDE every measurement taken while it is live.

    A premise that stopped holding half way through a case is the coin flip `SRV-FIX-034` is
    about, and a green run must not be able to hide it.
    """
    for row in ok(srv, "/api/lanes")["lanes"]:
        if row["lane"] == lane:
            return row["state"]["process"]
    return "gone"


def test_parent_delivery_aggregates_descendants(repo, gitrepo, serve):
    """§1 criterion 3 — the row's delivery is the aggregate over own AND descendant tickets.

    The population DISAGREES WITH ITSELF, which is the only fixture a reduction is visible in:
    `t0` is `done`, `t4` is `running` two levels down, and `t1 t2 t3` are `not_started`. On a
    fixture where every member agreed, `min`, `max`, `first` and `any` would be the same function
    (`SRV-FEAT-165` shipped `?filter=done` over exactly that mistake).

    Two kinds, named: `R1` — whose only live ticket is its GRANDCHILD's — and `T2`, all todo.
    """
    root = tree_lane_target(repo, gitrepo)
    srv = serve_fast(serve, root, PATH=f"{repo.root}/bin:{os.environ['PATH']}",
                     FACTORY_TEST_HOOKS="1")

    wait_for_state(srv, "t0", "done")
    lane = live_lane_for(srv, "t4", "beta")
    wait_for_state(srv, "t4", "running")

    rows = rows_of(ok(srv, "/api/requirements"))
    assert still_live(srv, lane) == "running", (
        "the lane stopped being live while the row was being read, so every word below is "
        "about a population that no longer exists")
    assert rows["R1"]["delivery"]["state"] == "running", (
        f"R1's only live work is its grandchild T3's ticket t4, and the row does not carry it: "
        f"{rows['R1']['delivery']}")
    assert rows["T1"]["delivery"]["state"] == "running", rows["T1"]["delivery"]
    assert rows["T2"]["delivery"]["state"] == "not_started", (
        f"T2 holds one todo ticket and nothing else; it must not inherit its parent's or its "
        f"sibling's state: {rows['T2']['delivery']}")
    assert rows["R1"]["delivery"]["state"] != rows["T2"]["delivery"]["state"], (
        "two kinds were asserted and they came back the same word")
    assert rows["R1"]["ticket_count"] == 5, rows["R1"]


def test_the_requirement_page_header_agrees_with_its_row(repo, gitrepo, serve):
    """NO-RIDER's own case — §4.2's row and §4.3's header are one number, one source (F27).

    §1 does not name E4's own `delivery`, and leaving it over R1's single ticket would have the
    requirements screen say `running` and the requirement's own page say `done` about the same
    document in the same instant. It is rolled up, and this is the assertion that says so.

    `needs_you` is NOT rolled up and that is asserted here too: it is `{count,total}` over the
    groups this page draws, and a total over tickets the page does not carry is the false
    sentence `WEB-FIX-091` and `WEB-FIX-088` are about.
    """
    root = tree_lane_target(repo, gitrepo)
    srv = serve_fast(serve, root, PATH=f"{repo.root}/bin:{os.environ['PATH']}",
                     FACTORY_TEST_HOOKS="1")

    wait_for_state(srv, "t0", "done")
    lane = live_lane_for(srv, "t4", "beta")
    wait_for_state(srv, "t4", "running")

    row = rows_of(ok(srv, "/api/requirements"))["R1"]
    page = ok(srv, "/api/requirements/R1")
    assert still_live(srv, lane) == "running", (
        "the lane stopped being live between the two reads, so the disagreement below would be "
        "about two different worlds rather than about two surfaces")
    assert page["delivery"]["state"] == row["delivery"]["state"] == "running", (
        f"the row says {row['delivery']['state']!r} and the page says "
        f"{page['delivery']['state']!r} about R1 in the same instant")
    assert page["state"]["process"] == row["state"]["process"], (page["state"], row["state"])
    assert page["needs_you"]["total"] == len(
        [t for g in page["groups"] for t in g["tickets"]]), (
        f"`needs_you.total` is a claim about the groups this page draws: {page['needs_you']} "
        f"over {[t['id'] for g in page['groups'] for t in g['tickets']]}")


def flat_target(repo):
    """A target on which no requirement carries `parent:` — every instance before this ticket."""
    repo.write(f"{REQ_DIR}/REQ-900.md",
               doc({"id": "REQ-900", "title": "The first", "status": "draft"}, "One."))
    repo.write(f"{REQ_DIR}/REQ-901.md",
               doc({"id": "REQ-901", "title": "The second", "status": "active"}, "Two."))
    repo.write(f"{TKT_DIR}/TKT-900.md",
               doc({"id": "TKT-900", "parent": "REQ-900", "workspace": "alpha",
                    "title": "Alpha work", "status": "todo"}, "a"))
    repo.write(f"{TKT_DIR}/TKT-901.md",
               doc({"id": "TKT-901", "parent": "REQ-900", "workspace": "beta",
                    "title": "Beta work", "status": "todo"}, "b"))
    repo.write(f"{TKT_DIR}/TKT-902.md",
               doc({"id": "TKT-902", "parent": "REQ-901", "workspace": "alpha",
                    "title": "More alpha work", "status": "todo"}, "c"))
    repo.write(f"{TKT_DIR}/TKT-903.md",
               doc({"id": "TKT-903", "parent": "NOBODY", "workspace": "alpha",
                    "title": "An orphan, so the synthetic row is here too", "status": "todo"}, "d"))
    repo.write(f"{RPT_DIR}/TKT-900-report.md", "# TKT-900 — done\n\nNo run is cited here.\n")
    return repo.manifest(manifest_for())


def normalised(body, repo):
    """The body with everything that is a fact about THIS tmp directory taken out of it."""
    text = json.dumps(body, sort_keys=True)
    for root in sorted({str(repo.real), str(repo.root)}, key=len, reverse=True):
        text = text.replace(root, "<root>")
    out = json.loads(text)
    for row in out["requirements"]:
        row["updated_iso"] = "<iso>"
    return out


def test_flat_target_gains_only_the_new_keys(repo, serve):
    """§1 criterion 4 — a target with no `parent:` anywhere answers what it always answered.

    The baseline is a CAPTURE of the real binary from before this ticket, committed at
    `docs/reports/SRV-FEAT-547/e3-flat-2026-09-16.json`, not a shape typed into this file: a
    hand-written expectation is this session's opinion of what the server used to answer, and
    that opinion is the very thing under test.
    """
    srv = serve(flat_target(repo))
    live = normalised(ok(srv, "/api/requirements"), repo)

    assert len(live["requirements"]) >= 3, (
        f"the sweep is empty and a comparison over nothing is green for free: {live}")

    for row in live["requirements"]:
        assert row["parent"] == "", row
        assert row["children"] == [], row
        assert row["own_ticket_count"] == row["ticket_count"], (
            f"with no parent anywhere the roll-up is the row's own count: {row}")
        for key in NEW_E3_KEYS:
            row.pop(key)

    before = json.loads(E3_FLAT_BASELINE.read_text())
    assert live == before, (
        "E3 changed for a target that has no parent link in it. Diff against the capture at "
        f"{E3_FLAT_BASELINE}:\n  now:    {json.dumps(live, sort_keys=True)}\n"
        f"  before: {json.dumps(before, sort_keys=True)}")


def generated_interface_name(key, node):
    """The name `scripts/gen-api-types.mjs` gives this node — its rule, not a guess.

    `generate()` emits `iface(node.title ?? refName(key), node)` for a `$defs` node and
    `iface(node.title, node)` for a response, and `refName` upper-cases the first letter of the
    pointer's leaf. Reading the rule off the generator is the difference between a check that
    tracks the product and a check that tracks whoever wrote it.
    """
    title = node.get("title")
    if title:
        return title
    leaf = key.split("/")[-1]
    return leaf[:1].upper() + leaf[1:]


def api_types_interfaces(text):
    """`{name: [its top-level property lines]}`, parsed out of the checked-in file."""
    out, name, body = {}, None, []
    for line in text.splitlines():
        if line.startswith("export interface "):
            name = line[len("export interface "):].split()[0]
            body = []
            continue
        if name is None:
            continue
        if line == "}":
            out[name] = body
            name = None
            continue
        if line.startswith("  ") and not line.startswith("   "):
            member = line[2:]
            # `inlineObject` closes a NESTED object at the same two-space indent a top-level
            # property sits at, so `  }` and `  } | null` are the end of somebody else's shape
            # and not a member called `}`. Round 2's first run reported seven of those.
            if member.startswith("}"):
                continue
            body.append(member)
    return out


def drift_against_the_schema():
    """Every way `client/web/src/types/api.ts` disagrees with `docs/technical/contracts/http.schema.json`.

    This is QA round 1's Finding 1, executable and with no JavaScript in it. Its reproduction was
    a `grep` for three names against a schema that requires them; a check written to that shape
    would only ever have found those three, so the sweep is derived from the schema instead —
    EVERY `$defs` node, EVERY response, every top-level property of each and whether the schema
    calls it required. The three names QA grepped for are members of that sweep, not the whole of
    it.

    Returns a list of sentences; empty means no drift.
    """
    text = API_TYPES.read_text()
    faces = api_types_interfaces(text)
    problems, swept = [], 0

    nodes = [(k, v) for k, v in HTTP_SCHEMA["$defs"].items()]
    nodes += [(k, v) for k, v in HTTP_SCHEMA["responses"].items()]
    for key, node in nodes:
        if "properties" not in node:
            continue
        name = generated_interface_name(key, node)
        if name not in faces:
            problems.append(f"`export interface {name}` is missing (schema node `{key}`)")
            continue
        required = set(node.get("required", []))
        declared = {line.split(":")[0].rstrip("?"): line for line in faces[name]}
        for prop in node["properties"]:
            swept += 1
            if prop not in declared:
                problems.append(f"`{name}.{prop}` is in the schema and not in the type")
                continue
            optional = declared[prop].split(":")[0].endswith("?")
            if prop in required and optional:
                problems.append(f"`{name}.{prop}` is required by the schema and optional here")
            if prop not in required and not optional:
                problems.append(f"`{name}.{prop}` is optional in the schema and required here")
        for prop in declared:
            if prop not in node["properties"]:
                problems.append(f"`{name}.{prop}` is in the type and not in the schema")

    for eid, node in HTTP_SCHEMA["responses"].items():
        swept += 2
        if f"  {eid}: '{node['path']}'," not in text:
            problems.append(f"`PATHS.{eid}` does not carry `{node['path']}`")
        if f"  {eid}: '{node['method']}'," not in text:
            problems.append(f"`METHODS.{eid}` does not carry `{node['method']}`")

    assert swept > 100, (
        f"the sweep examined {swept} names, which is not a schema — a check that sweeps nothing "
        f"has not run")
    return problems


def test_the_checked_in_types_are_what_the_generator_produces(request):
    """The stronger half of criterion 5's fourth clause: byte-identity with the real generator.

    It SKIPS where no JavaScript runtime can run the product, and skipping rather than returning
    is the whole point — QA round 1 found this clause reported PASS in a lane that had not
    executed it, and a skip is counted where a pass is not. `drift_against_the_schema()` above
    runs unconditionally, so the clause is never unchecked; this case is what proves the file is
    byte-for-byte the generator's output rather than merely carrying the right names.
    """
    node, why = generator_runtime_or_why_not()
    if node is None:
        assert "SRV-FIX-549" in why, (
            f"the refusal does not name the ticket that is blocking it: {why!r}")
        pytest.skip(f"NOT ANSWERABLE HERE — {why}")

    checked_in = API_TYPES.read_text()
    run = subprocess.run([node, str(GEN_API_TYPES)], capture_output=True, text=True,
                         timeout=60, cwd=str(GEN_API_TYPES.parent))
    assert run.returncode == 0, f"the generator failed: {run.stdout}{run.stderr}"
    produced = API_TYPES.read_text()
    API_TYPES.write_text(checked_in)
    assert produced == checked_in, (
        "`client/web/src/types/api.ts` is not what `npm run gen:api-types` produces from "
        "`docs/technical/contracts/http.schema.json` — the schema moved and the generated file did not "
        "(`SRV-FIX-453`). Run it and commit the result.")


def generator_runtime_or_why_not():
    """A JavaScript runtime that can execute `scripts/gen-api-types.mjs`, or why there is none.

    The generator is the product: it is what `npm run gen:api-types` runs, and a type file this
    suite produced some other way would be a second generator agreeing with itself. So the
    question is asked of the machine — is there a `node` that can parse the file — and the answer
    is never guessed.
    """
    node = shutil.which("node")
    if node is None:
        return None, ("no `node` on PATH at all in this lane, and `npm` is not on it either: a "
                      "lane claimed for `factory-server` binds that workspace's toolchain and "
                      "`~/.nvm` belongs to `factory-client` (SRV-FIX-533, SRV-FIX-549)")
    probe = subprocess.run([node, "-e", "process.exit((null ?? 1) === 1 ? 0 : 1)"],
                           capture_output=True, text=True, timeout=30)
    if probe.returncode != 0:
        version = subprocess.run([node, "--version"], capture_output=True, text=True,
                                 timeout=30).stdout.strip()
        return None, (f"the only `node` in this lane is {version}, which cannot parse the "
                      f"generator's `??`; `~/.nvm` is `factory-client`'s toolchain and a lane "
                      f"claimed for `factory-server` is not given it (SRV-FIX-533, SRV-FIX-549)")
    return node, ""


def validator_for(endpoint):
    return jsonschema.Draft202012Validator(
        dict(HTTP_SCHEMA["responses"][endpoint],
             **{"$schema": HTTP_SCHEMA["$schema"], "$defs": HTTP_SCHEMA["$defs"]}))


def test_http_schema_covers_requirement_children(repo, serve):
    """§1 criterion 5 — the contract documents carry the shape, and the client's types derive.

    Every name below is read OFF the document rather than typed twice: the schema node is the
    source and the fixture's live bodies are validated against it, so a schema that drifts from
    the C++ turns this red instead of blanking a screen.
    """
    srv = serve(tree_target(repo))
    e3 = ok(srv, "/api/requirements")
    e4 = ok(srv, "/api/requirements/R1")

    validator_for("E3").validate(e3)
    validator_for("E4").validate(e4)

    row = HTTP_SCHEMA["$defs"]["requirementRow"]
    for key in NEW_E3_KEYS:
        assert key in row["properties"], f"`{key}` is not in `$defs.requirementRow`"
        assert key in row["required"], f"`{key}` is optional in `$defs.requirementRow`"

    child = HTTP_SCHEMA["$defs"]["requirementChild"]
    assert set(child["required"]) == {"id", "title", "repo", "status", "own_ticket_count",
                                      "ticket_count", "delivery", "tickets"}, child["required"]
    assert child["properties"]["tickets"]["items"] == {"$ref": "#/$defs/group"}, (
        f"E4's child entry must REUSE E4's own group shape, not declare a second one: {child}")
    assert HTTP_SCHEMA["responses"]["E4"]["properties"]["children"]["items"] == {
        "$ref": "#/$defs/requirementChild"}, HTTP_SCHEMA["responses"]["E4"]["properties"]
    assert "warnings" in HTTP_SCHEMA["responses"]["E4"]["properties"], (
        "E4 answers the two new warnings and the schema does not say so")

    warning = HTTP_SCHEMA["$defs"]["warning"]["properties"]
    for key in ("id", "value", "ids"):
        assert key in warning, f"`{key}` is not declared on `$defs.warning`"

    spec = (FACTORY_ROOT / "docs/product/SPEC.md").read_text()
    parent_row = next(line for line in spec.splitlines()
                      if line.startswith("| `parent` |"))
    assert "requirement · ticket" in parent_row, (
        f"§2's `parent` row still says it is a ticket's key alone: {parent_row}")

    # The fourth clause, and it is executed HERE rather than behind a runtime that may not exist.
    # QA round 1 failed this ticket on exactly that: the clause was never run and the case said
    # PASS. `drift_against_the_schema()` needs no JavaScript at all.
    drift = drift_against_the_schema()
    assert not drift, (
        "`client/web/src/types/api.ts` does not carry what `docs/technical/contracts/http.schema.json` declares, so "
        "`npm run gen:api-types` was not run after the schema moved (`SRV-FIX-453`, and QA round "
        "1 of this ticket):\n  " + "\n  ".join(drift))


# ── the mandatory negatives ───────────────────────────────────────────────────────────────────

def test_unknown_requirement_parent_is_top_level_with_warning(repo, serve):
    """NEGATIVE — a `parent:` naming nothing on disk is a fact about the repository, not an error.

    Three spellings of *unresolvable*, because one of them is the reserved synthetic id and the
    other two are an ordinary typo and a value the tool itself uses as a group name.
    """
    repo.write("docs/epics/R1.md", doc({"id": "R1", "title": "A real one"}, "One."))
    repo.write("docs/epics/BAD.md", doc({"id": "BAD", "parent": "NOPE", "title": "Names a typo"},
                                        "Two."))
    repo.write("docs/epics/LONE.md",
               doc({"id": "LONE", "parent": "standalone", "title": "Names the synthetic group"},
                   "Three."))
    repo.write("docs/epics/KID.md", doc({"id": "KID", "parent": "BAD", "title": "Under the typo"},
                                        "Four."))
    repo.mkdir("docs/reports")
    repo.mkdir("docs/tickets")
    srv = serve(repo.manifest({"factory": "0.1", "workspaces": {"alpha": {"path": "."}},
                               "docs": dict(TREE_DOCS)}))

    r = srv.get("/api/requirements")
    assert r.status_code == 200, f"E3 → {r.status_code} {r.text}"
    body = r.json()
    rows = rows_of(body)

    assert sorted(top_level(body)) == ["BAD", "LONE", "R1"], (
        f"a requirement whose parent cannot be resolved stays top-level: {top_level(body)}")
    assert rows["KID"]["parent"] == "BAD", (
        f"a child of an unresolvable parent still nests under it: {rows['KID']}")
    assert rows["BAD"]["children"] == ["KID"], rows["BAD"]

    found = {(w["id"], w["value"]) for w in warnings_coded(body, "unknown_parent")}
    assert found == {("BAD", "NOPE"), ("LONE", "standalone")}, (
        f"one warning per requirement whose parent names nothing, carrying the id and the value "
        f"it named: {body['warnings']}")

    page = srv.get("/api/requirements/BAD")
    assert page.status_code == 200, f"E4 → {page.status_code} {page.text}"
    assert {(w["id"], w["value"]) for w in warnings_coded(page.json(), "unknown_parent")} == found, (
        f"E4 must carry the same warnings E3 does: {page.json().get('warnings')}")


def test_parent_cycle_is_reported_not_followed(repo, serve):
    """NEGATIVE — A→B→A and C→C. Every member top-level, one warning per cycle, 200, and bounded.

    The chain of fifty exists for the bound: `S2` reads the listing on every request, so a walk
    that followed links without a visited set would not return at all on the two-cycle above and
    would be quadratic on the chain. A second is three orders of magnitude of headroom.
    """
    repo.write("docs/epics/A.md", doc({"id": "A", "parent": "B", "title": "A"}, "a"))
    repo.write("docs/epics/B.md", doc({"id": "B", "parent": "A", "title": "B"}, "b"))
    repo.write("docs/epics/C.md", doc({"id": "C", "parent": "C", "title": "C"}, "c"))
    repo.write("docs/epics/D.md", doc({"id": "D", "parent": "A", "title": "Under a cycle"}, "d"))
    for n in range(50):
        parent = f"CH-{n - 1:02d}" if n else ""
        front = {"id": f"CH-{n:02d}", "title": f"Chained {n}"}
        if parent:
            front["parent"] = parent
        repo.write(f"docs/epics/CH-{n:02d}.md", doc(front, "chain"))
    repo.mkdir("docs/reports")
    repo.mkdir("docs/tickets")
    srv = serve(repo.manifest({"factory": "0.1", "workspaces": {"alpha": {"path": "."}},
                               "docs": dict(TREE_DOCS)}))

    started = time.time()
    r = srv.get("/api/requirements")
    elapsed = time.time() - started
    assert r.status_code == 200, f"E3 → {r.status_code} {r.text}"
    assert elapsed < 1.0, f"E3 took {elapsed:.3f}s over 50 chained requirements and two cycles"

    body = r.json()
    rows = rows_of(body)
    for member in ("A", "B", "C"):
        assert rows[member]["parent"] == "", (
            f"{member} is in a cycle and must be drawn at top level: {rows[member]}")
    assert rows["D"]["parent"] == "A", (
        f"a requirement hanging off a cycle member still nests under it: {rows['D']}")

    cycles = [tuple(w["ids"]) for w in warnings_coded(body, "parent_cycle")]
    assert sorted(cycles) == [("A", "B"), ("C",)], (
        f"one warning per cycle, its members in the order the walk found them: {body['warnings']}")

    assert rows["CH-00"]["parent"] == "" and rows["CH-49"]["parent"] == "CH-48", rows["CH-49"]
    assert rows["CH-00"]["children"] == ["CH-01"], rows["CH-00"]
    assert warnings_coded(body, "parent_cycle") == [
        w for w in body["warnings"] if w.get("code") == "parent_cycle"], body


def test_a_tickets_parent_resolves_across_declared_repositories(repo, serve):
    """SRV-FEAT-555 — a ticket in `team` naming `R1`, a requirement of `root`, hangs off `R1`.

    `SRV-FEAT-547` let a REQUIREMENT name a parent in another repository and kept a ticket's
    parent same-repository; a product epic then reached every team through a team epic that
    existed only as a hop. The hop is gone: a ticket's `parent:` resolves in its own repository
    first and then, by id, in any other declared repository that holds exactly one requirement
    of that id.
    """
    repo.write("docs/epics/R1.md", doc({"id": "R1", "title": "In the root repo"}, "One."))
    repo.write(f"{SUB_TEAM}/docs/tickets/crossing.md",
               doc({"id": "crossing", "parent": "R1", "workspace": "beta",
                    "title": "A ticket naming another repository's requirement"}, "b"))
    repo.mkdir("docs/tickets")
    repo.mkdir("docs/reports")
    repo.mkdir(f"{SUB_TEAM}/docs/epics")
    repo.mkdir(f"{SUB_TEAM}/docs/reports")
    srv = serve(repo.manifest(tree_manifest()))

    rows = rows_of(ok(srv, "/api/requirements"))
    assert "standalone" not in rows, (
        f"the crossing ticket resolved to R1, so no synthetic group is drawn: {sorted(rows)}")
    assert rows["R1"]["own_ticket_count"] == 1 and rows["R1"]["ticket_count"] == 1, rows["R1"]

    body = ok(srv, "/api/requirements/R1")
    assert [t["id"] for g in body["groups"] for t in g["tickets"]] == ["crossing"], body["groups"]
    assert body["groups"][0]["workspace"] == "beta", body["groups"]

    index = ok(srv, "/api/tickets")
    parents = {t["id"]: g["parent"] for g in index["groups"] for t in g["tickets"]}
    assert parents.get("crossing") == "R1", index["groups"]


def test_a_tickets_parent_that_no_repository_carries_is_still_standalone(repo, serve):
    """NEGATIVE for SRV-FEAT-555 — resolving by id across repositories widens nothing else.

    A ticket naming an id that no declared repository holds as a requirement is exactly as
    orphaned as before, and the synthetic group still says so.
    """
    repo.write("docs/epics/R1.md", doc({"id": "R1", "title": "In the root repo"}, "One."))
    repo.write(f"{SUB_TEAM}/docs/tickets/lost.md",
               doc({"id": "lost", "parent": "NOPE", "workspace": "beta",
                    "title": "A ticket naming nothing"}, "b"))
    repo.mkdir("docs/tickets")
    repo.mkdir("docs/reports")
    repo.mkdir(f"{SUB_TEAM}/docs/epics")
    repo.mkdir(f"{SUB_TEAM}/docs/reports")
    srv = serve(repo.manifest(tree_manifest()))

    rows = rows_of(ok(srv, "/api/requirements"))
    assert "standalone" in rows and rows["standalone"]["ticket_count"] == 1, sorted(rows)
    assert rows["R1"]["ticket_count"] == 0, rows["R1"]
    orphans = ok(srv, "/api/requirements/standalone")
    assert [t["id"] for g in orphans["groups"] for t in g["tickets"]] == ["lost"], orphans["groups"]


def test_the_monitor_reads_every_declared_repositorys_ledger(repo, serve):
    """SRV-FEAT-556 — a root instance declaring `repos` reads each repository's `.factory/runs`.

    The robotics root monitor showed `claimed · no proof`, `0/10` and no cost for every ticket that a
    team instance had run, because evidence, stage and cost came from the root's own ledger alone.
    A run recorded in `team/.factory/runs` now counts for the ticket it names, on the root.
    """
    repo.write("docs/epics/R1.md", doc({"id": "R1", "title": "In the root repo"}, "One."))
    repo.write(f"{SUB_TEAM}/docs/tickets/t1.md",
               doc({"id": "t1", "parent": "R1", "workspace": "beta", "status": "pass",
                    "title": "Run by the team instance"}, "b"))
    repo.write(f"{SUB_TEAM}/docs/reports/t1-report.md",
               "# t1 — report — PASS\nRuns: run-1-feedbeef (run lane/01)\n")
    repo.write(f"{SUB_TEAM}/.factory/runs/run-1-feedbeef/meta.json", json.dumps({
        "run_id": "run-1-feedbeef", "lane": "lane/01", "ticket_id": "t1", "session_id": "s",
        "n": 1, "started_iso": "2026-09-17T01:00:00Z", "ended_iso": "2026-09-17T01:10:00Z",
        "outcome": "ended cleanly", "duration_s": 600, "cost_usd": 1.5, "turns": 1,
        "exit_code": 0, "signal": None, "role": "run", "branch": "ticket/t1", "branch_tip": ""}))
    repo.write(f"{SUB_TEAM}/.factory/runs/run-1-feedbeef/transcript.jsonl", "")
    repo.mkdir("docs/tickets")
    repo.mkdir("docs/reports")
    repo.mkdir(f"{SUB_TEAM}/docs/epics")
    srv = serve(repo.manifest(tree_manifest()))

    body = ok(srv, "/api/tickets/t1")
    assert body["state"]["evidence"] == "claimed_proven", body["state"]
    assert body["runs"]["count"] == 1 and abs(body["runs"]["cost_usd"] - 1.5) < 1e-9, body["runs"]

    rows = rows_of(ok(srv, "/api/requirements"))
    assert rows["R1"]["ticket_count"] == 1, rows["R1"]

    runs = ok(srv, "/api/tickets/t1/runs")["runs"]
    assert [r["run_id"] for r in runs] == ["run-1-feedbeef"], runs


def test_a_team_ledger_run_counts_only_for_the_ticket_it_names(repo, serve):
    """NEGATIVE for SRV-FEAT-556 — reading more ledgers attaches nothing to the wrong ticket."""
    repo.write("docs/epics/R1.md", doc({"id": "R1", "title": "In the root repo"}, "One."))
    repo.write(f"{SUB_TEAM}/docs/tickets/t1.md",
               doc({"id": "t1", "parent": "R1", "workspace": "beta", "status": "pass",
                    "title": "Claims done"}, "b"))
    repo.write(f"{SUB_TEAM}/docs/reports/t1-report.md", "# t1 — report — PASS\nRuns: run-1-feedbeef\n")
    repo.write(f"{SUB_TEAM}/.factory/runs/run-1-feedbeef/meta.json", json.dumps({
        "run_id": "run-1-feedbeef", "lane": "lane/01", "ticket_id": "t9", "session_id": "s",
        "n": 1, "started_iso": "2026-09-17T01:00:00Z", "ended_iso": "2026-09-17T01:10:00Z",
        "outcome": "ended cleanly", "duration_s": 600, "cost_usd": 1.5, "turns": 1,
        "exit_code": 0, "signal": None, "role": "run", "branch": "ticket/t9", "branch_tip": ""}))
    repo.mkdir("docs/tickets")
    repo.mkdir("docs/reports")
    repo.mkdir(f"{SUB_TEAM}/docs/epics")
    srv = serve(repo.manifest(tree_manifest()))

    body = ok(srv, "/api/tickets/t1")
    assert body["runs"]["count"] == 0, body["runs"]
    assert body["state"]["evidence"] == "claimed_proven", (
        "the report cites a run id the ledgers hold, which is the evidence rule as it stands",
        body["state"])
    assert ok(srv, "/api/tickets/t1/runs")["runs"] == []
