"""SRV-FEAT-313 — `E30 GET /api/home`: where the work is, what needs you, what never left.

`SPEC.md` §4.18 draws three panels and §5 `E30` answers all three from ONE read. Three endpoints
would be three answers about one repository at three instants, which is `FAC-EPIC-004`'s defect
class generalised — the counts in the first panel and the rows in the second would be free to
disagree and each would be correct.

Every case builds its own synthetic repository. Never this one: an answer about *what became of
this work* that only holds against the repository it was written in has learned that repository.

Nothing below transcribes a vocabulary. §3.5's delivery values, §3.3's seven clauses and §9.2's
remedy verbs are read out of `SPEC.md` on every run, and the clause names are cross-checked
against what `GET /api/lanes` really says at the same moment — because a second vocabulary for the
same conditions is two places the states drift, which is the thing criterion 2 exists to refuse.

    cd factory/server && pytest tests/test_home.py -v
"""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import jsonschema
import pytest

from test_agent import assert_reaped
from test_spec import SPEC, verb_set, spec
# The stand-in agent and the recording it replays: a turn ends because a real `result` frame says
# so, not because this session synthesised one (SRV-FEAT-010).
from test_transcript import AGENT_SH

FACTORY = Path(__file__).resolve().parents[2]
HTTP_SCHEMA = json.loads((FACTORY / "docs/technical/contracts/http.schema.json").read_text())

REQ_DIR = "product/requirements"
TKT_DIR = "product/work-items"
RPT_DIR = "product/deliveries"

DAY = 86400


# ── the document, read rather than retyped ───────────────────────────────────────────────────

def spec_text():
    return SPEC.read_text()


def delivery_values():
    """§3.5's table of `delivery` values, IN ITS OWN ORDER.

    `E30` serves one entry per value in this order including the zeroes, so the row of counts
    cannot change shape under the reader. Parsed, so a value added to §3.5 fails here rather
    than quietly never being counted.
    """
    text = spec_text()
    start = text.index("### 3.5 Delivery")
    end = text.index("### 3.6 Remedy")
    out = []
    for line in text[start:end].splitlines():
        m = re.match(r"^\|\s*`([a-z_]+)`\s*\|", line)
        if m:
            out.append(m.group(1))
            continue
        # The FIRST table only. §3.5 carries a second one — the document's claim against the
        # states that agree with it — whose left column is `todo` and `pass`, and those are
        # `status:` values a repository writes, not values this endpoint may ever count.
        if out and not line.lstrip().startswith("|"):
            break
    assert len(out) >= 7, f"§3.5's value table did not parse: {out}"
    return out


def needs_you_conditions():
    """§3.3's clause lines, one per condition, verbatim.

    The fenced block reads

        needs_you(lane) =
             process == idle                     the agent asked and stopped
          ∨  process == blocked                  it is waiting on your answer
          ...

    so a condition is a line holding `==`. Seven of them, and the count is asserted by the
    caller: a clause that disappears from the document must not quietly disappear from the check.
    """
    text = spec_text()
    start = text.index('### 3.3 "Needs you"')
    end = text.index("### 3.4 Claimed, and proven")
    block = text[start:end]
    fence = block.index("```") + 3
    body = block[fence:block.index("```", fence)]
    return [line.strip() for line in body.splitlines() if "==" in line]


def axis_values():
    """§3.2's three axes and §3.5's delivery values — every word `E9` puts on a lane.

    A clause name outside this union is a second vocabulary for the same conditions, which is
    exactly what criterion 2 refuses.
    """
    text = spec_text()
    start = text.index("### 3.2 The three state axes")
    end = text.index('### 3.3 "Needs you"')
    words = set()
    for line in text[start:end].splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        for token in re.findall(r"`([a-z_]+)`", cells[1]):
            words.add(token)
    words |= set(delivery_values())
    assert {"idle", "blocked", "crashed", "silent"} <= words, sorted(words)
    return words


# ── the synthetic repository ─────────────────────────────────────────────────────────────────

def manifest_for(*, lanes_host=3, agent_command="/bin/true", stages=None):
    return {
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}},
        "lanes": {"host": lanes_host},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "stages": stages or ["SPEC", "IMPL", "GATE"],
        "agent": {"command": agent_command},
    }


def doc(front, body="Body."):
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class Git:
    """The repository a case is about, built commit by commit."""

    def __init__(self, root):
        self.root = root

    def __call__(self, *args, env=None, **kw):
        merged = dict(os.environ, **(env or {}))
        out = subprocess.run(["git", "-C", str(self.root), *args],
                             capture_output=True, text=True, timeout=30, env=merged, **kw)
        assert out.returncode == 0, f"git {' '.join(args)} → {out.returncode}\n{out.stderr}"
        return out.stdout.strip()

    def init(self):
        self("init", "-b", "main")
        self("config", "user.email", "t@example.invalid")
        self("config", "user.name", "Test")
        self("config", "commit.gpgsign", "false")
        return self

    def commit(self, message, ago_s=None):
        """One commit, optionally back-dated — `age_s` is a fact about WHEN, and a case that
        cannot move the clock cannot tell an age from a zero."""
        env = {}
        if ago_s is not None:
            when = int(time.time()) - ago_s
            env = {"GIT_AUTHOR_DATE": f"{when} +0000", "GIT_COMMITTER_DATE": f"{when} +0000"}
        self("add", "-A")
        self("commit", "-m", message, "--no-gpg-sign", env=env)
        return self("rev-parse", "HEAD")


@pytest.fixture
def gitrepo(repo):
    return Git(repo.root)


def serve_fast(serve, root, **env):
    """A server whose delivery probe re-reads the world often enough for a case to see it."""
    env.setdefault("FACTORY_DELIVERY_POLL_MS", "200")
    return serve(root, **env)


def ok(srv, path):
    r = srv.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"
    return r.json()


def home(srv):
    return ok(srv, "/api/home")


def delivery_of(srv, ticket_id):
    return ok(srv, f"/api/tickets/{ticket_id}")["delivery"]


def wait_for_state(srv, ticket_id, state, timeout=20):
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        seen = delivery_of(srv, ticket_id)
        if seen["state"] == state:
            return seen
        time.sleep(0.1)
    raise AssertionError(f"{ticket_id} never reached {state!r}; last was {seen}")


def counts_of(body):
    return {row["state"]: row["count"] for row in body["delivery"]}


# ── criterion 1 ──────────────────────────────────────────────────────────────────────────────

def five_tickets_in_three_states(repo, gitrepo):
    """FIVE tickets across THREE delivery states, with one state holding TWO of them.

    A fixture with one ticket per state cannot separate a real count from an implementation of
    `first` or `any`: every number would be 1 and every wrong answer would be 1 as well. So
    `unshipped` and `not_started` each hold two, `done` holds one, and the totals are 1/2/2.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-spread.md",
               doc({"id": "REQ-001", "title": "The work is spread out", "status": "draft"}))
    for tid, status in (("TKT-1", "in-progress"), ("TKT-2", "in-progress"),
                        ("TKT-3", "in-progress"), ("TKT-4", "todo"), ("TKT-5", "todo")):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": status}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")

    # done · merged into main, its branch deleted the way a forge deletes it
    gitrepo("checkout", "-b", "ticket/TKT-1")
    repo.write(f"{RPT_DIR}/TKT-1-report.md", "# TKT-1 — PASS\n\nIt landed.\n")
    gitrepo.commit("TKT-1: deliver")
    gitrepo("checkout", "main")
    gitrepo("merge", "--no-ff", "-m", "merge TKT-1", "ticket/TKT-1")
    gitrepo("branch", "-D", "ticket/TKT-1")

    # unshipped · TWO of them, each with commits no remote has
    for tid in ("TKT-2", "TKT-3"):
        gitrepo("checkout", "-b", f"ticket/{tid}")
        repo.write(f"src/{tid}.txt", "work nobody outside this machine can see\n")
        gitrepo.commit(f"{tid}: the work")
        gitrepo("checkout", "main")

    # not_started · TWO of them, untouched


def test_the_delivery_counts_are_over_every_ticket_and_their_states_are_e10s(
        repo, gitrepo, serve):
    """§1 criterion 1, verbatim:

    **`test_the_delivery_counts_are_over_every_ticket_and_their_states_are_e10s`** — every value
    in `delivery[].state` is a member of `E10`'s closed set, and the counts sum to the
    repository's ticket total. **The fixture spreads five tickets across three different states**,
    so a count implementing `first` or `any` is separable.

    (The closed set of `delivery` values is §3.5's, which §4.18 names as the one axis this row
    draws; it is parsed out of `SPEC.md` here rather than typed, so a value added to the document
    and not to the endpoint fails.)
    """
    five_tickets_in_three_states(repo, gitrepo)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-1", "done")
    wait_for_state(srv, "TKT-2", "unshipped")
    wait_for_state(srv, "TKT-3", "unshipped")

    body = home(srv)
    values = delivery_values()

    # every value in `delivery[].state` is a member of the closed set...
    seen = [row["state"] for row in body["delivery"]]
    assert set(seen) <= set(values), (
        f"E30 invented a delivery state the document does not have: "
        f"{sorted(set(seen) - set(values))}")

    # ...and the closed set is served WHOLE, in §3.5's own order, zeroes included (E30's own
    # sentence: a row of counts that omits its empty members changes shape under the reader)
    assert seen == values, f"the row is not §3.5's values in §3.5's order: {seen} != {values}"

    # the counts sum to the repository's ticket total
    tickets = sorted(p.name for p in (repo.root / TKT_DIR).glob("*.md"))
    assert len(tickets) == 5, tickets
    assert sum(row["count"] for row in body["delivery"]) == len(tickets), (
        f"the counts do not sum to the {len(tickets)} tickets on disk: {counts_of(body)}")

    # three different states, and one of them holds TWO — `first`/`any` cannot produce this
    got = counts_of(body)
    assert got["done"] == 1, got
    assert got["unshipped"] == 2, (
        f"two tickets are unshipped and the row says {got['unshipped']} — a count implementing "
        f"`first` or `any` says 1 here: {got}")
    assert got["not_started"] == 2, got
    assert [state for state, n in got.items() if n], "no state was counted at all"
    assert {state for state, n in got.items() if n} == {"done", "unshipped", "not_started"}, got
    assert all(got[state] == 0 for state in values
               if state not in ("done", "unshipped", "not_started")), got


# ── criterion 2 ──────────────────────────────────────────────────────────────────────────────

def clause_enum():
    """The clause names `http.schema.json` lets a `needs_you` row carry.

    A closed set on the wire is a closed set in the schema — that is how the client's generated
    types get it (`gen:api-types`), and it is the only place the set can be asserted against the
    document without somebody typing it a second time.
    """
    node = HTTP_SCHEMA["$defs"]["needsYouRow"]["properties"]["clauses"]["items"]
    return node["enum"]


def test_needs_you_rows_are_e9s_clauses_and_not_a_second_definition(homeenv):
    """§1 criterion 2, verbatim:

    **`test_needs_you_rows_are_e9s_clauses_and_not_a_second_definition`** — each row's `clauses`
    are §3.3's, by the same names `E9` uses. A second vocabulary for the same seven conditions
    fails: two drawings of a lane are two places the states drift.
    """
    e = homeenv
    conditions = needs_you_conditions()
    assert len(conditions) == 7, (
        f"§3.3 has {len(conditions)} clause lines, not seven — the set this endpoint draws "
        f"changed and nothing here noticed:\n  " + "\n  ".join(conditions))

    names = clause_enum()
    assert len(names) == 7, f"the schema declares {len(names)} clause names, not seven: {names}"

    # ONE name per §3.3 line and one line per name — a bijection, so a clause cannot be dropped
    # and a name cannot be invented for a condition the document does not have.
    for name in names:
        matched = [c for c in conditions if re.search(rf"\b{re.escape(name)}\b", c)]
        assert len(matched) == 1, (
            f"clause name {name!r} names {len(matched)} of §3.3's conditions, not one: {matched}")
    for condition in conditions:
        matched = [n for n in names if re.search(rf"\b{re.escape(n)}\b", condition)]
        assert len(matched) == 1, (
            f"§3.3's condition {condition!r} is named by {matched}, not by exactly one clause")

    # and every name is a word E9 already puts on a lane, not a new one for the same fact
    vocabulary = axis_values()
    assert set(names) <= vocabulary, (
        f"a second vocabulary for §3.3's conditions: {sorted(set(names) - vocabulary)} appear in "
        f"no axis §3.2 declares and in no delivery value §3.5 declares")

    body = e.home()
    rows = {row["ticket_id"]: row for row in body["needs_you"]["rows"]}

    # the two clauses that need no lane, by the delivery states E5 reports for the same tickets
    assert delivery_of(e.srv, "TKT-STALL")["state"] == "stalled"
    assert rows["TKT-STALL"]["clauses"] == ["stalled"], rows["TKT-STALL"]
    assert delivery_of(e.srv, "TKT-SHIP")["state"] == "unshipped"
    assert rows["TKT-SHIP"]["clauses"] == ["unshipped"], rows["TKT-SHIP"]

    # the lane clause, cross-checked against what E9 SAYS AT THE SAME MOMENT — this is the whole
    # of "by the same names E9 uses", and it is asserted against the live answer, not a copy
    lane_row = [r for r in ok(e.srv, "/api/lanes")["lanes"] if r["lane"] == e.lane][0]
    assert lane_row["state"]["process"] == "running", lane_row["state"]
    assert lane_row["state"]["motion"]["value"] == "silent", lane_row["state"]
    live = rows[e.ticket]
    assert "silent" in live["clauses"], (
        f"E9 calls this lane `running` + `silent` and E30's row says {live['clauses']}")
    assert lane_row["state"]["motion"]["value"] in live["clauses"], (
        f"E30 named the condition with a word E9 does not use: {live['clauses']} against "
        f"{lane_row['state']}")

    # every clause any row carries is one of the seven, always
    for ticket_id, row in rows.items():
        assert row["clauses"], f"{ticket_id} is a needs-you row that names no clause: {row}"
        assert set(row["clauses"]) <= set(names), (ticket_id, row["clauses"])
        assert row["detail"], f"{ticket_id} says nothing about why: {row}"

    # `needs_you.total` counts TICKETS and not clauses (E30): TKT-DEAD satisfies two at once —
    # a lane that crashed, on a branch holding commits no remote has
    two = rows["TKT-DEAD"]
    assert set(two["clauses"]) == {"crashed", "unshipped"}, (
        f"the fixture meant TKT-DEAD to satisfy two clauses at once and it satisfies "
        f"{two['clauses']} — the tickets-not-clauses assertion below proves nothing without it")
    assert body["needs_you"]["total"] == len(body["needs_you"]["rows"]), (
        f"total {body['needs_you']['total']} against {len(body['needs_you']['rows'])} rows — "
        f"a ticket satisfying three clauses is one row and one unit of somebody's attention")
    assert body["needs_you"]["total"] == 4, (
        f"four tickets need a person here and the total says "
        f"{body['needs_you']['total']}: {[r['ticket_id'] for r in body['needs_you']['rows']]}")

    # and the fourth contributes no row and therefore no control (§4.18). A `needs you` that is
    # always on is a notification channel a person learns to ignore.
    assert "TKT-CALM" not in rows, (
        f"a ticket in none of §3.3's states produced a row: {rows.get('TKT-CALM')}")
    assert delivery_of(e.srv, "TKT-CALM")["state"] == "not_started"


# ── criterion 3 ──────────────────────────────────────────────────────────────────────────────

def one_ahead_one_pushed_one_dirty(repo, gitrepo):
    """Three branches that a naive check cannot tell apart, and only one of them is unpushed.

    `git status` answers *does this hold uncommitted files*. It cannot answer *has this left the
    laptop*, and conflating the two is how `S5` was broken on 2026-08-06: four lane worktrees read
    `dirt=0`, `ahead=0`, were called "verified empty" and were removed — one of them 410 s into a
    live run.

      TKT-AHEAD   two commits, no remote has either        → the ONE row
      TKT-PUSHED  one commit, a remote has its tip          → not unpushed
      TKT-DIRTY   one commit a remote has, worktree dirty    → `unshipped`, and NOT unpushed
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-laptop.md",
               doc({"id": "REQ-001", "title": "Nothing lives on one laptop", "status": "draft"}))
    for tid in ("TKT-AHEAD", "TKT-PUSHED", "TKT-DIRTY"):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": "in-progress"}))
    gitrepo.init()
    gitrepo.commit("the repository as it stands")

    # ahead and never pushed — the oldest of its two commits is a week old, so `age_s` is a
    # measurement and not a zero
    gitrepo("checkout", "-b", "ticket/TKT-AHEAD")
    repo.write("src/ahead-1.txt", "a week ago\n")
    gitrepo.commit("TKT-AHEAD: the first half", ago_s=7 * DAY)
    repo.write("src/ahead-2.txt", "an hour ago\n")
    gitrepo.commit("TKT-AHEAD: the second half", ago_s=3600)
    gitrepo("checkout", "main")

    # already pushed — a remote ref points at the branch tip, which is what `pushed` means
    gitrepo("checkout", "-b", "ticket/TKT-PUSHED")
    repo.write("src/pushed.txt", "everybody can see this\n")
    tip = gitrepo.commit("TKT-PUSHED: the work")
    gitrepo("checkout", "main")
    gitrepo("update-ref", "refs/remotes/origin/ticket/TKT-PUSHED", tip)

    # pushed AND dirty — a checkout holding uncommitted files, on a branch a remote already has
    gitrepo("checkout", "-b", "ticket/TKT-DIRTY")
    repo.write("src/dirty.txt", "committed\n")
    dirty_tip = gitrepo.commit("TKT-DIRTY: the work")
    gitrepo("checkout", "main")
    gitrepo("update-ref", "refs/remotes/origin/ticket/TKT-DIRTY", dirty_tip)
    gitrepo("worktree", "add", str(repo.root / "wt-dirty"), "ticket/TKT-DIRTY")
    (repo.root / "wt-dirty" / "scratch.txt").write_text("uncommitted, and going nowhere\n")


def test_unpushed_counts_branches_that_exist_and_not_worktrees_that_are_dirty(
        repo, gitrepo, serve):
    """§1 criterion 3, verbatim:

    **`test_unpushed_counts_branches_that_exist_and_not_worktrees_that_are_dirty`** — the fixture
    has a branch ahead of `main` and never pushed, a branch already pushed, and a dirty worktree
    on a pushed branch. **Exactly the first appears.** `git status` answers *does this hold
    uncommitted files*; it cannot answer *has this left the laptop*, and conflating them is how
    `S5` was broken on 2026-08-06.
    """
    one_ahead_one_pushed_one_dirty(repo, gitrepo)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-AHEAD", "unshipped")

    # the dirty worktree really is `unshipped` — so a panel that simply filtered on the delivery
    # state would carry it, and the case below is not passing by accident
    dirty = wait_for_state(srv, "TKT-DIRTY", "unshipped")
    assert "uncommitted" in dirty["detail"], dirty

    # the pushed branch has left the laptop, whatever the forge could or could not be asked
    pushed = delivery_of(srv, "TKT-PUSHED")
    assert pushed["state"] in ("waiting", "unshipped"), pushed
    assert "on no remote" not in pushed["detail"], pushed

    body = home(srv)
    rows = body["unpushed"]["rows"]

    assert [row["ticket_id"] for row in rows] == ["TKT-AHEAD"], (
        f"exactly the branch that is ahead and never pushed appears, and the panel carried "
        f"{[r['ticket_id'] for r in rows]}")
    assert body["unpushed"]["total"] == 1, body["unpushed"]

    row = rows[0]
    assert row["branch"] == "ticket/TKT-AHEAD", row
    assert row["commits"] == 2, f"two commits no remote has, and the row says {row['commits']}"
    assert isinstance(row["age_s"], int), row
    assert row["age_s"] >= 6 * DAY, (
        f"the oldest commit nobody else can see is seven days old and `age_s` is {row['age_s']} "
        f"— the panel exists so that nobody discovers a week of work living on one laptop")
    assert row["age_s"] < 60 * DAY, row
    assert row["title"], row


# ── criterion 4 ──────────────────────────────────────────────────────────────────────────────

def test_lane_occupancy_is_e9s_and_agrees_with_it_in_one_call(repo, gitrepo, serve):
    """§1 criterion 4, verbatim:

    **`test_lane_occupancy_is_e9s_and_agrees_with_it_in_one_call`** — `lanes.occupied` and
    `lanes.total` equal what `GET /api/lanes` reports at the same moment. Asserted with **one**
    call to each, not by polling either.
    """
    repo.manifest(manifest_for(lanes_host=3, agent_command="/bin/sh agent.sh"))
    repo.write("agent.sh", AGENT_SH)
    repo.write(f"{REQ_DIR}/REQ-001-seats.md",
               doc({"id": "REQ-001", "title": "Three seats", "status": "draft"}))
    for tid in ("TKT-A", "TKT-B"):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": "todo"}))
    repo.write(".gitignore", ".factory/worktrees/\n.factory/runs/\n.factory/ids.json\n")
    gitrepo.init()
    gitrepo.commit("the repository as it stands")

    srv = serve_fast(serve, repo.root)
    for tid in ("TKT-A", "TKT-B"):
        r = srv.post("/api/lanes", json={"ticket_id": tid, "workspace": "alpha"})
        assert r.status_code == 201, r.text

    # exactly one call to each, counted — "agrees with it" is a claim about one instant, and a
    # test that retried until the two matched would prove the opposite of what it says
    calls = []
    original = srv.get

    def counting(path, **kw):
        calls.append(path)
        return original(path, **kw)

    srv.get = counting
    try:
        body = home(srv)
        lanes = ok(srv, "/api/lanes")
    finally:
        srv.get = original

    assert calls == ["/api/home", "/api/lanes"], f"more than one call to each: {calls}"

    seats = lanes["seats"]["host"]
    assert body["lanes"]["occupied"] == seats["used"], (body["lanes"], seats)
    assert body["lanes"]["total"] == seats["total"], (body["lanes"], seats)
    assert body["lanes"]["occupied"] == len(lanes["lanes"]) == 2, (body["lanes"], lanes["lanes"])
    assert body["lanes"]["total"] == 3, body["lanes"]

    # A LANE HELD WITH NO RUN NEEDS NOBODY, and it is one of the break attempts this case keeps.
    # Both lanes exist, hold a ticket, have a branch and a checkout, and no process has ever
    # started in either. §3.3's clauses are about a process and about delivery; a seat being
    # taken is neither, and an endpoint that counted "a lane exists" as attention would make the
    # first screen permanently red on a healthy repository.
    assert body["needs_you"]["rows"] == [], (
        f"two claimed lanes with no run produced needs-you rows: {body['needs_you']}")
    assert body["needs_you"]["total"] == 0, body["needs_you"]
    for row in lanes["lanes"]:
        assert row["state"]["process"] == "not_started", row["state"]
        assert row["needs_you"] is False, row


# ── criterion 5 · mandatory negative ─────────────────────────────────────────────────────────

def test_an_empty_repository_answers_zeroes_and_not_an_error(repo, serve):
    """§1 criterion 5, verbatim:

    **(negative) `test_an_empty_repository_answers_zeroes_and_not_an_error`** — nothing in it:
    `200`, every count zero, both row lists empty. The `docs` directories absent: the same, plus
    `warnings:[{code:"path_missing",path}]`. A `404` for either fails.
    """
    # nothing in it: the docs directories exist and hold no document, and there is no git history
    repo.manifest(manifest_for())
    for rel in (REQ_DIR, TKT_DIR, RPT_DIR):
        repo.mkdir(rel)
    assert not (repo.root / ".git").exists()

    srv = serve_fast(serve, repo.root)
    r = srv.get("/api/home")
    assert r.status_code != 404, (
        f"an empty repository is not a missing thing, and E30 answered 404: {r.text}")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    body = r.json()

    assert [row["state"] for row in body["delivery"]] == delivery_values(), body["delivery"]
    assert all(row["count"] == 0 for row in body["delivery"]), body["delivery"]
    assert body["needs_you"]["rows"] == [], body["needs_you"]
    assert body["needs_you"]["total"] == 0, body["needs_you"]
    assert body["unpushed"]["rows"] == [], body["unpushed"]
    assert body["unpushed"]["total"] == 0, body["unpushed"]
    assert body.get("warnings", []) == [], (
        f"the directories are all there; nothing is missing: {body.get('warnings')}")


def test_an_empty_repository_with_the_docs_directories_absent_warns_and_still_answers(repo, serve):
    """The second half of §1 criterion 5, and it is a separate case because it fails differently.

    "The `docs` directories absent: the same, plus `warnings:[{code:"path_missing",path}]`. A
    `404` for either fails."
    """
    repo.manifest(manifest_for())
    for rel in (REQ_DIR, TKT_DIR, RPT_DIR):
        assert not (repo.root / rel).exists()

    srv = serve_fast(serve, repo.root)
    r = srv.get("/api/home")
    assert r.status_code != 404, (
        f"a manifest pointing at a directory nobody made is a warning, not a 404: {r.text}")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    body = r.json()

    assert all(row["count"] == 0 for row in body["delivery"]), body["delivery"]
    assert body["needs_you"]["rows"] == [] and body["needs_you"]["total"] == 0, body["needs_you"]
    assert body["unpushed"]["rows"] == [] and body["unpushed"]["total"] == 0, body["unpushed"]

    warnings = body.get("warnings")
    assert warnings, "three declared directories are absent and the answer says nothing"
    missing = {w["path"] for w in warnings if w["code"] == "path_missing"}
    for rel in (REQ_DIR, TKT_DIR, RPT_DIR):
        assert any(m.endswith(rel) for m in missing), (
            f"{rel} is absent and no warning names it: {sorted(missing)}")
    for w in warnings:
        assert w["code"] == "path_missing", w
        assert w["path"], w


def test_an_invalid_manifest_is_answered_and_named_rather_than_blanked(repo, serve):
    """`E30` · Manifest invalid: `200`, zeros, empty lists and `E3`'s `invalid_manifest` warning.

    Found by breaking the fixture rather than by reading the document: a first draft of this file
    declared an agent that is not on PATH, the manifest was refused, and the two cases above went
    red on a warning nobody had asked about. That is the exact state SPEC.md names — *a first
    screen that was blank for two different reasons and said which for neither is SRV-FIX-071 on
    the surface a person opens first* — so it is a case rather than a fixture detail.
    """
    repo.manifest(manifest_for(agent_command="factory-home-no-such-agent"))
    for rel in (REQ_DIR, TKT_DIR, RPT_DIR):
        repo.mkdir(rel)

    srv = serve_fast(serve, repo.root)
    r = srv.get("/api/home")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    body = r.json()

    assert all(row["count"] == 0 for row in body["delivery"]), body["delivery"]
    assert body["needs_you"]["rows"] == [] and body["unpushed"]["rows"] == [], body

    codes = [w["code"] for w in body.get("warnings", [])]
    assert "invalid_manifest" in codes, (
        f"an empty first screen that does not say the manifest was refused: {body.get('warnings')}")

    # verbatim `E3`'s, so the two surfaces cannot explain the same blankness differently
    mine = [w for w in body["warnings"] if w["code"] == "invalid_manifest"]
    theirs = [w for w in ok(srv, "/api/requirements")["warnings"]
              if w["code"] == "invalid_manifest"]
    assert mine == theirs, (mine, theirs)


# ── criterion 6 · mandatory negative ─────────────────────────────────────────────────────────

def walk_remedies(node, where="$"):
    """Every `remedy` member anywhere in the body, with the path it was found at.

    Recursive on purpose: criterion 6 says EVERY row, and a check that reads
    `body["needs_you"]["rows"]` cannot see a remedy that appears somewhere else later.
    """
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "remedy" and value is not None:
                found.append((f"{where}.{key}", value))
            found += walk_remedies(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += walk_remedies(value, f"{where}[{i}]")
    return found


def test_no_row_offers_an_action_that_writes_a_document(homeenv):
    """§1 criterion 6, verbatim:

    **(negative) `test_no_row_offers_an_action_that_writes_a_document`** — the response carries no
    remedy whose verb writes into the repository. §4.18 declines the design's `Push N branches`
    for exactly this: one write verb there ends §3.5's read-only property for the whole product.
    Assert over **every** row's `remedy.verb` against the closed set in §9, so a new verb added
    later cannot slip through.
    """
    e = homeenv
    body = e.home()

    verbs = verb_set(spec())
    assert verbs, "SPEC.md §9.2 declares no verb set"

    # the schema's enum and §9.2's table are ONE set, so "the closed set in §9" is the set the
    # server is actually held to (`test_spec.py::test_the_verb_set_is_closed_and_generated`
    # keeps this true; it is restated here because this criterion quantifies over it)
    declared = HTTP_SCHEMA["$defs"]["remedy"]["properties"]["action"]["enum"]
    assert sorted(declared) == sorted(verbs), (sorted(declared), sorted(verbs))

    # the verbs whose §9.2 row says they destroy something, read out of the document
    destructive = {action for action, cells in verbs.items()
                   if json.loads(cells[4].strip().strip("`")) != []}
    assert destructive, "§9.2's `destroys` column did not parse"

    rows = body["needs_you"]["rows"]
    assert rows, "a case with no rows cannot say anything about the remedies on them"

    offered = walk_remedies(body)
    for where, remedy in offered:
        assert isinstance(remedy, dict), (where, remedy)
        assert remedy["action"] in verbs, (
            f"{where} offers `{remedy['action']}`, which §9.2's closed set does not contain — a "
            f"verb added later may not slip through this endpoint")
        assert remedy["action"] not in destructive, (
            f"{where} offers `{remedy['action']}`, which §9.2 says destroys "
            f"{verbs[remedy['action']][4]}; no panel on the first screen loses somebody's work")
        assert remedy["destroys"] == [], (
            f"{where} offers a remedy that destroys {remedy['destroys']}")

    # every row is accounted for: a `remedy` is the object or an explicit null, never absent
    for row in rows:
        assert "remedy" in row, f"{row['ticket_id']} carries no `remedy` member at all: {row}"
        assert row["remedy"] is None or isinstance(row["remedy"], dict), row

    # and the design's `Push N branches` is declined, structurally: the panel that names the
    # branches carries no remedy member at all, in the schema or on the wire. §3.5's read-only
    # property is six git subcommands served at E1, and this endpoint adds no seventh.
    assert "remedy" not in HTTP_SCHEMA["$defs"]["unpushedRow"]["properties"], (
        "the unpushed row has somewhere to put a `Push N branches` button")
    for row in body["unpushed"]["rows"]:
        assert "remedy" not in row, row
    for where, remedy in offered:
        blob = json.dumps(remedy).lower()
        for forbidden in ("push", "git add", "commit", "merge"):
            assert forbidden not in blob, f"{where} offers {forbidden!r}: {remedy}"

    # the six subcommands are still the whole of what delivery may run — asserted against E1 at
    # the same moment, because a seventh verb here would be a seventh subcommand there
    git_used = ok(e.srv, "/api/health")["delivery"]["git"]
    assert sorted(git_used) == sorted(
        ["for-each-ref", "ls-tree", "rev-list", "rev-parse", "status", "worktree"]), git_used


# ── criterion 7 ──────────────────────────────────────────────────────────────────────────────

def e30_validator():
    """`E30`'s node, validated with the whole file as its resolution root so `$ref` works."""
    return jsonschema.Draft202012Validator(HTTP_SCHEMA).evolve(
        schema=HTTP_SCHEMA["responses"]["E30"])


def test_the_response_validates_against_http_schema_json(homeenv):
    """§1 criterion 7, verbatim:

    **`test_the_response_validates_against_http_schema_json`** — through
    `test_schema_consumers.py`.

    The node is real and addressable (`E30`, `GET /api/home`, `200`), the live body validates
    against it, and `test_schema_consumers.py`'s derived source map names the schema — so an edit
    to it runs the client's own build AND gate rather than only the C++.
    """
    node = HTTP_SCHEMA["responses"]["E30"]
    assert node["path"] == "/api/home", node
    assert node["method"] == "GET", node
    assert node["status"] == 200, node

    e30_validator().validate(homeenv.home())

    # an endpoint whose body the client generates types from, whose source is under the gate that
    # forces the consuming workspace — the mechanism criterion 7 names by file
    from test_schema_consumers import generated_from
    watched = generated_from()
    assert "docs/technical/contracts/http.schema.json" in watched, watched


def test_the_populated_answer_validates_too(repo, gitrepo, serve):
    """Every panel non-empty, against the same node. A schema an empty body satisfies is a
    schema that has said nothing about the shape a person actually reads."""
    five_tickets_in_three_states(repo, gitrepo)
    srv = serve_fast(serve, repo.root)
    wait_for_state(srv, "TKT-2", "unshipped")
    body = home(srv)
    assert body["needs_you"]["rows"], body
    assert body["unpushed"]["rows"], body
    e30_validator().validate(body)


# ── the environment criteria 2, 6 and 7 share ────────────────────────────────────────────────

ONE_TURN = Path(__file__).resolve().parent / "fixtures" / "cli-stream-2026-07-27-simple.jsonl"


@pytest.fixture
def homeenv(repo, gitrepo, serve):
    """A repository holding one ticket per §3.3 clause the case can reach, and a live lane.

      TKT-STALL  `in-progress`, a branch with nothing on it        → `stalled`
      TKT-SHIP   commits on a branch no remote has                  → `unshipped`
      TKT-LIVE   a claimed lane, a running agent, the clock moved   → `silent`
      TKT-DEAD   `pass` with a QA verdict beside it, commits on a branch no remote has, and a
                 lane whose agent was killed                        → `crashed` AND `unshipped`:
                 TWO clauses on ONE ticket, which is what makes "a ticket satisfying three
                 clauses is ONE row and one unit of somebody's attention" assertable — and two
                 axes that genuinely disagree, `E9` saying `crashed` while `E5` says `unshipped`.
                 §3.2b places it in `awaiting merge`, so the exhaustiveness case still holds.
      TKT-CALM   `todo`, nothing anywhere                            → no clause, and no row

    SRV-FIX-453 · TKT-LIVE used to carry `silent` AND `stalled`, and the second of those was the
    defect: delivery said `stalled` about a ticket whose agent was alive, because the probe
    matched program names in `/proc` and a lane's agent runs under its interpreter's. A live lane
    is `running` now, and `running` is not a state that needs a person, so the two-clause ticket
    and the disagreeing pair moved to TKT-DEAD — where they are true rather than symptomatic.
    A lane's process and its ticket's delivery can still disagree; what they can no longer do is
    disagree about whether an agent is alive.
    """
    repo.manifest(manifest_for(agent_command="/bin/sh agent.sh"))
    repo.write(f"{REQ_DIR}/REQ-001-attention.md",
               doc({"id": "REQ-001", "title": "What needs a person", "status": "draft"}))
    for tid, status in (("TKT-STALL", "in-progress"), ("TKT-SHIP", "in-progress"),
                        ("TKT-LIVE", "in-progress"), ("TKT-DEAD", "pass"),
                        ("TKT-CALM", "todo")):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": status}))
    repo.write("agent.sh", AGENT_SH)
    repo.write("one-turn.jsonl", ONE_TURN.read_text())
    repo.write(".gitignore", ".factory/worktrees/\n.factory/runs/\n.factory/ids.json\n")
    gitrepo.init()
    gitrepo.commit("the repository, the agent and one recording")

    # stalled · a branch was claimed and holds nothing beyond main, and main moved on
    gitrepo("branch", "ticket/TKT-STALL")
    repo.write("src/elsewhere.txt", "somebody else's work\n")
    gitrepo.commit("something else entirely")

    # unshipped · commits on a branch no remote has
    gitrepo("checkout", "-b", "ticket/TKT-SHIP")
    repo.write("src/ship.txt", "work nobody outside this machine can see\n")
    gitrepo.commit("TKT-SHIP: the work")
    gitrepo("checkout", "main")

    # the same, for the ticket whose lane is going to die on it — and a QA verdict beside it, so
    # §3.2b places the row in `awaiting merge` rather than in no cell at all
    gitrepo("checkout", "-b", "ticket/TKT-DEAD")
    repo.write("src/dead.txt", "work a dead lane is holding\n")
    gitrepo.commit("TKT-DEAD: the work")
    gitrepo("checkout", "main")
    repo.write(f"{RPT_DIR}/TKT-DEAD-qa.md",
               "# TKT-DEAD — QA verdict\n\nSomebody else checked it and wrote this down.\n")

    srv = serve_fast(serve, repo.root, FACTORY_TEST_HOOKS="1")

    class Env:
        pass

    e = Env()
    e.srv = srv
    e.root = repo.root
    e.ticket = "TKT-LIVE"
    e.deadTicket = "TKT-DEAD"
    e.home = lambda: home(srv)

    r = srv.post("/api/lanes", json={"ticket_id": "TKT-LIVE", "workspace": "alpha"})
    assert r.status_code == 201, r.text
    e.lane = r.json()["lane"]

    r = srv.post(f"/api/lanes/{e.lane}/runs", json={"prompt": "do the work"})
    assert r.status_code == 201, r.text
    run = r.json()

    deadline = time.time() + 20
    while time.time() < deadline:
        row = [x for x in ok(srv, "/api/lanes")["lanes"] if x["lane"] == e.lane][0]
        if row["state"]["process"] == "running" and row["state"]["motion"]["since"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"the lane never started producing: {row}")

    assert srv.post("/api/_test/clock", json={"advance_s": 200}).status_code == 202
    deadline = time.time() + 10
    while time.time() < deadline:
        row = [x for x in ok(srv, "/api/lanes")["lanes"] if x["lane"] == e.lane][0]
        if row["state"]["motion"]["value"] == "silent":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"the lane never went silent: {row}")

    r = srv.post("/api/lanes", json={"ticket_id": "TKT-DEAD", "workspace": "alpha"})
    assert r.status_code == 201, r.text
    e.deadLane = r.json()["lane"]
    r = srv.post(f"/api/lanes/{e.deadLane}/runs", json={"prompt": "do the work"})
    assert r.status_code == 201, r.text
    dead = r.json()

    deadline = time.time() + 20
    while time.time() < deadline:
        row = [x for x in ok(srv, "/api/lanes")["lanes"] if x["lane"] == e.deadLane][0]
        if row["state"]["process"] == "running":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"the second lane never started: {row}")

    # the group, not the leader: `lane/git.cpp` puts a lane's process in a session of its own and
    # that is what dies when an agent dies (SRV-FIX-459 is the leader-only case).
    os.killpg(dead["pid"], signal.SIGKILL)
    deadline = time.time() + 25
    while time.time() < deadline:
        row = [x for x in ok(srv, "/api/lanes")["lanes"] if x["lane"] == e.deadLane][0]
        if row["state"]["process"] == "crashed":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"the second lane never reported crashed: {row}")

    wait_for_state(srv, "TKT-STALL", "stalled")
    wait_for_state(srv, "TKT-SHIP", "unshipped")
    wait_for_state(srv, "TKT-DEAD", "unshipped")

    yield e

    srv.post(f"/api/lanes/{e.lane}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])


# ── WEB-FIX-002 · criterion 2 ────────────────────────────────────────────────────────────────

CLIENT_VOCABULARY = FACTORY / "client" / "web" / "src" / "vocabulary.ts"
CLIENT_DASHBOARD = FACTORY / "client" / "web" / "src" / "pages" / "DashboardPage.tsx"


def wait_for(probe, what, timeout=20):
    """Poll `probe` until it answers something truthy, and return that answer.

    `wait_for_state` above has this shape already. It is a DEADLINE and not an interval: nothing
    here is waited out, the value returned is the server's own, and the failure message carries the
    last answer rather than the time that passed.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"{what}; the last answer was {last!r}")


def client_home_block():
    """§4.18's empty block as the CLIENT composes it — its title AND its body, out of both files.

    Reading `HOME_COLLECTION` alone is what this case did in `WEB-FIX-002`'s first round, and it is
    a claim about a constant rather than about the sentence somebody reads. Measured: leave the noun
    correct and inline `"No requirements in this repository."` back into `DashboardPage.tsx`, and
    this case PASSES over a screen saying the false thing —
    `docs/reports/WEB-FIX-002/WEB-FIX-002-break-C-red-2026-09-10.log`. §1 criterion 6 requires
    criteria 1 and 2 to go red TOGETHER on that break, and only criterion 1 did.

    So the block is reassembled here the way the browser assembles it: the title must BE
    `homeEmptyTitle()` rather than a literal, and the body is taken from the element itself. A
    screen that stops deriving its sentence from the collection is a screen this case can no longer
    read, and that is the same thing as failing.
    """
    vocab = CLIENT_VOCABULARY.read_text()

    noun = re.search(r"HOME_COLLECTION\s*=\s*['\"]([^'\"]+)['\"]", vocab)
    assert noun, (
        f"{CLIENT_VOCABULARY} declares no HOME_COLLECTION. This case reads the noun the empty "
        f"block names off the client; with nothing to read it asserts nothing, which is exactly "
        f"the state WEB-FEAT-001 shipped in")
    noun = noun.group(1)

    template = re.search(r"function\s+homeEmptyTitle\s*\([^)]*\)[^{]*\{\s*return\s+`([^`]*)`",
                         vocab)
    assert template, f"{CLIENT_VOCABULARY} declares no homeEmptyTitle template"
    title = template.group(1).replace("${HOME_COLLECTION}", noun)

    known = re.search(r"COLLECTIONS\s*=\s*\[([^\]]*)\]", vocab)
    assert known, f"{CLIENT_VOCABULARY} declares no COLLECTIONS"
    collections = re.findall(r"['\"]([^'\"]+)['\"]", known.group(1))

    page = CLIENT_DASHBOARD.read_text()
    block = re.search(r'<EmptyBlock([^>]*testId="home-empty"[^>]*)>(.*?)</EmptyBlock>', page, re.S)
    assert block, (
        f'{CLIENT_DASHBOARD} draws no EmptyBlock with testId="home-empty". The sentence this case '
        f'is about is not on the screen it is about')
    head, body = block.group(1), block.group(2)

    drawn = re.search(r"title=\{([^}]*)\}", head)
    assert drawn and drawn.group(1).strip() == "homeEmptyTitle()", (
        f"§4.18's empty block takes its title from {head.strip()!r} instead of homeEmptyTitle(). A "
        f"literal there is a sentence that has stopped moving with the collection, and this case "
        f"would go on reading a constant the screen no longer uses — WEB-FIX-002 break C")

    text = re.sub(r"<[^>]*>", " ", body)
    text = re.sub(r"\{'\s*'\}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return noun, collections, f"{title} {text}"


def test_a_repository_with_epics_and_no_tickets_is_not_told_it_has_no_requirements(
        repo, gitrepo, serve):
    """§1 criterion 2 of `WEB-FIX-002`, transcribed from `WEB-FEAT-001-qa.md`'s reproduction.

    A repository declaring `docs/epics` and `docs/tickets`, with **one epic document and zero
    ticket documents** — the state of every repository between `/split-requirement` and the first
    `/new-ticket`. `E30` answers all zeros and `E3` answers one row, and BOTH are asked in this one
    case, because the defect is that they disagree and no case that asked one of them could see it.

    Then the noun itself, derived rather than asserted: a document is added under each declared
    `docs` key in turn, and the key that MOVES `E30` is the collection `E30` counts. The client's
    empty block may name that collection and no other.
    """
    repo.manifest(manifest_for())
    for rel in (REQ_DIR, TKT_DIR, RPT_DIR):
        repo.mkdir(rel)
    repo.write(f"{REQ_DIR}/EPIC-001-the-first-requirement.md",
               doc({"id": "EPIC-001", "title": "Split, but not yet written up",
                    "status": "draft"}))
    assert not list((repo.root / TKT_DIR).glob("*.md")), "the fixture is epics WITHOUT tickets"
    gitrepo.init()
    gitrepo.commit("one epic, no tickets")

    srv = serve_fast(serve, repo.root)

    # ── the two answers, in one case ──────────────────────────────────────────────────────────
    body = home(srv)
    reqs = ok(srv, "/api/requirements")

    assert all(row["count"] == 0 for row in body["delivery"]), body["delivery"]
    assert body["needs_you"]["rows"] == [] and body["needs_you"]["total"] == 0, body["needs_you"]
    assert body["unpushed"]["rows"] == [] and body["unpushed"]["total"] == 0, body["unpushed"]

    assert reqs["total"] == 1, reqs
    assert len(reqs["requirements"]) == 1, reqs
    assert reqs["requirements"][0]["id"] == "EPIC-001", reqs

    # THE defect, stated as the relation between them: E30 is empty of everything it carries and
    # E3 is not empty, at the same instant, about the same repository. A screen drawn from E30
    # that says the word "requirements" is saying something E3 contradicts in the same breath.
    assert sum(row["count"] for row in body["delivery"]) == 0 and reqs["total"] > 0, (
        f"the fixture no longer holds the two apart: E30 {counts_of(body)} · E3 {reqs['total']}")

    # ── which collection actually moves E30 ───────────────────────────────────────────────────
    #
    # Every wait below is on a fact the SERVER produces. Polling until the answer CHANGES is
    # deterministic only for the key that moves it; for the key that must NOT, a deadline that
    # expires is a sleep wearing a loop. So the epic is written first and its arrival is confirmed
    # at E3 before the ticket file exists at all. Documents are only added, so the probe read that
    # first sees the ticket necessarily saw both epics, and the SIZE of that one answer says which
    # collections were in it: 1 is the ticket alone, 2 is the two epics alone, 3 is both.
    def delivery_total():
        return sum(row["count"] for row in home(srv)["delivery"])

    assert delivery_total() == 0, counts_of(home(srv))

    repo.write(f"{REQ_DIR}/EPIC-002.md",
               doc({"id": "EPIC-002", "title": "A second epic", "status": "draft"}))
    wait_for(lambda: ok(srv, "/api/requirements")["total"] == 2,
             "E3 never reported the second epic, so nothing measured after this could be said "
             "about which collection moves E30")

    repo.write(f"{TKT_DIR}/TKT-1.md",
               doc({"id": "TKT-1", "parent": "EPIC-001", "workspace": "alpha",
                    "title": "The first ticket", "status": "todo"}))
    total = wait_for(delivery_total,
                     "E30's counts never left zero after a ticket was added to a repository whose "
                     "docs/tickets it declares")

    # What is on disk when that answer is produced, and therefore what each answer would MEAN.
    # Written as the fixture's own counts rather than as literals: the reading below is arithmetic
    # over them, and a later edit that adds a document must not silently re-point it.
    on_disk = {"epics": 2, "tickets": 1}
    reading = {sum(n for k, n in on_disk.items() if k in combination): set(combination)
               for combination in ((), ("epics",), ("tickets",), ("epics", "tickets"))}
    assert len(reading) == 4, (
        f"two of the four possible answers are the same number for {on_disk}, so this fixture can "
        f"no longer tell the collections apart; give one of them a different count")

    assert total in reading, (
        f"E30 counts {total} over {on_disk}, which is not a total any combination of them can "
        f"produce — it is counting something this case does not know about")
    moved = reading[total]

    assert moved == {"tickets"}, (
        f"E30's counts total {total} over {on_disk}, so they move for "
        f"{sorted(moved) or 'nothing'}; the empty block's noun is only honest if it names the "
        f"collection that moves them")

    # ── and the sentence the client draws for this answer names that collection ────────────────
    named, collections, block = client_home_block()
    said = sorted({c for c in collections if re.search(rf"\b{c}\b", block, re.I)})

    assert named in moved, (
        f"the first screen's empty block names {named!r} out of an answer that counts "
        f"{sorted(moved)}. GET /api/requirements answered {reqs['total']} row(s) for this same "
        f"repository at the same instant, so that sentence is false the first time anybody reads "
        f"it — WEB-FEAT-001's F4, exactly")
    assert said == [named], (
        f"§4.18's empty block reads {block!r}, which names {said}. A claim about a collection is "
        f"about ONE collection, and every other noun in it is a second claim nobody asserted")
    assert named == "tickets", named


# ── SRV-FEAT-001 · the axes per ticket, in this same one read ────────────────────────────────
#
# §3.2b's nine cells read `lane · process · role · verdict · delivery · ledger` PER TICKET. `E30`
# serves those axes and NEVER the cell they flatten to: the flattening is the client's one derived
# presentation axis and §3.2b constraint 3 gives it one definition, so a server that answered it
# first would be the second one.


def stage_cells():
    """§3.2b's cell names, read out of `SPEC.md`'s own table rather than transcribed.

    A cell renamed in §3.2b and not here would leave the negative asserting a vocabulary the
    specification no longer has, which is a check that cannot fail on the thing it names.
    """
    text = spec_text()
    start = text.index("### 3.2b")
    end = text.index('### 3.3 "Needs you"')
    cells = [m.group(1) for m in
             (re.match(r"^\|\s*`([A-Za-z ]+)`\s*\|", line) for line in text[start:end].splitlines())
             if m]
    assert len(cells) == 9, cells
    return cells


def home_row_members():
    """The members `homeTicketRow` declares, read out of the contract."""
    return set(HTTP_SCHEMA["$defs"]["homeTicketRow"]["required"])


def rows_by_id(body):
    return {r["ticket_id"]: r for r in body["tickets"]}


# ── criterion 1 ──────────────────────────────────────────────────────────────────────────────

def test_every_ticket_appears_exactly_once_in_the_ticket_array(homeenv):
    """§1 criterion 1, verbatim:

    **`test_every_ticket_appears_exactly_once_in_the_ticket_array`** — a fixture repository with
    `N` tickets answers `len(body["tickets"]) == N`, ids unique, and every id one the catalogue
    lists. The array is the whole ticket set and not the subset some panel needed.

    `homeenv` holds four tickets in four different conditions — one stalled, one unshipped, one
    with a live lane, one untouched — so an array built from the tickets some panel happened to
    need is short, and short by a different number for each panel.
    """
    body = homeenv.home()
    rows = body["tickets"]
    ids = [r["ticket_id"] for r in rows]

    catalogue = sorted(t["id"] for g in ok(homeenv.srv, "/api/tickets")["groups"]
                       for t in g["tickets"])
    assert sorted(ids) == catalogue, (sorted(ids), catalogue)
    assert len(ids) == len(set(ids)) == 5, ids

    # every panel's subject is IN the array, so no panel is drawn from a set the array does not have
    for row in body["needs_you"]["rows"] + body["unpushed"]["rows"]:
        assert row["ticket_id"] in set(ids), (row, ids)


# ── criterion 2 · MANDATORY NEGATIVE ─────────────────────────────────────────────────────────

def test_the_axes_are_served_separately_and_no_flattened_stage_field_exists(homeenv):
    """§1 criterion 2, verbatim:

    **`test_the_axes_are_served_separately_and_no_flattened_stage_field_exists`** — MANDATORY
    NEGATIVE. Every row carries `state.process`, `state.motion`, `state.evidence` as three
    members, and `"stage"` does not appear as a member of any row with §3.2b's ten values in it.
    Asserted over the response body, so a later commit that "helpfully" serves the cell turns
    this red.

    Both halves are needed. The member set is read from the contract, so a row that GAINS a
    member fails here rather than in a client that already drew it; and the cell names are read
    from §3.2b, so a value carrying one of them fails wherever it is hidden.
    """
    body = homeenv.home()
    declared = home_row_members()
    forbidden = set(stage_cells()) | {"All"}

    assert body["tickets"], body
    for row in body["tickets"]:
        assert set(row) == declared, (sorted(row), sorted(declared))
        assert set(row["state"]) == {"process", "motion", "evidence"}, row["state"]

        # every string this row carries, at every depth, except the three members that pass the
        # repository's own words through verbatim — a ticket may be titled anything, and `status`
        # and `delivery.claimed` are the document's claim (§3.5)
        verbatim = {"title", "status", "claimed"}

        def strings(node, where="$"):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in verbatim:
                        continue
                    yield from strings(v, where + "." + k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from strings(v, f"{where}[{i}]")
            elif isinstance(node, str):
                yield where, node

        for where, value in strings(row):
            assert value not in forbidden, (where, value, sorted(forbidden))

    # the three axes are separately readable and disagree with each other. On the live lane the
    # process and motion axes disagree — alive, and going nowhere — which is the pair the
    # prototype flattened, and delivery agrees with E9 because SRV-FIX-453 made it read E9.
    live = rows_by_id(body)[homeenv.ticket]
    assert live["lane"], live
    assert live["state"]["process"] == "running", live
    assert live["state"]["motion"] == "silent", live
    assert live["delivery"]["state"] == "running", live

    # and on the dead lane the LANE axis and the DELIVERY axis disagree: `E9` says the process
    # crashed, `E5` says the work is unshipped, and a row that flattened them would have had to
    # choose which of those a person is told.
    dead = rows_by_id(body)[homeenv.deadTicket]
    assert dead["lane"], dead
    assert dead["state"]["process"] == "crashed", dead
    assert dead["delivery"]["state"] == "unshipped", dead


# ── criterion 3 ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def verdicts(repo, gitrepo, serve):
    """Four tickets that force §3.5b's two possible sources APART.

      TKT-PASS   claims `pass`         · a verdict document exists   → `"pass"`
      TKT-FAIL   claims `fail`         · a verdict document exists   → `"fail"`
      TKT-CLAIM  claims `pass`         · NO verdict document         → `null`
      TKT-QUIET  claims `awaiting-qa`  · a verdict document exists   → `null`

    `TKT-CLAIM` and `TKT-QUIET` are the two directions of disagreement: a fixture holding only
    tickets where the claim and the document agree passes identically whichever of the two the
    implementation actually read.
    """
    repo.manifest(manifest_for())
    repo.write(f"{REQ_DIR}/REQ-001-verdicts.md",
               doc({"id": "REQ-001", "title": "What QA said", "status": "draft"}))
    for tid, status in (("TKT-PASS", "pass"), ("TKT-FAIL", "fail"),
                        ("TKT-CLAIM", "pass"), ("TKT-QUIET", "awaiting-qa")):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": status}))
    for tid in ("TKT-PASS", "TKT-FAIL", "TKT-QUIET"):
        repo.write(f"{RPT_DIR}/{tid}-qa.md",
                   f"# {tid} — QA verdict\n\nSomebody else checked it and wrote this down.\n")
    gitrepo.init()
    gitrepo.commit("the repository, its verdicts, and the two disagreements")
    return serve_fast(serve, repo.root)


def test_the_verdict_is_read_from_the_source_the_spec_names(verdicts, repo):
    """§1 criterion 3, verbatim:

    **`test_the_verdict_is_read_from_the_source_the_spec_names`** — a ticket whose QA verdict is
    `fail` answers `verdict: "fail"`, one that never had QA answers `null`, and the source read
    is the one SPEC names — asserted by moving that file and seeing the answer change.

    §3.5b names the source and it is a PAIR: a verdict document beside the ticket's report AND a
    `pass`/`fail` claim. So the file is moved and the claim is not touched — the ticket document
    is byte-identical afterwards — and the answer moves anyway.
    """
    rows = rows_by_id(home(verdicts))
    assert rows["TKT-PASS"]["verdict"] == "pass", rows["TKT-PASS"]
    assert rows["TKT-FAIL"]["verdict"] == "fail", rows["TKT-FAIL"]
    assert rows["TKT-CLAIM"]["verdict"] is None, rows["TKT-CLAIM"]
    assert rows["TKT-QUIET"]["verdict"] is None, rows["TKT-QUIET"]

    # the claim is carried beside the verdict and is never the verdict (§3.5, §4.17's row)
    assert rows["TKT-CLAIM"]["status"] == "pass", rows["TKT-CLAIM"]

    document = repo.root / TKT_DIR / "TKT-FAIL.md"
    before = document.read_bytes()
    (repo.root / RPT_DIR / "TKT-FAIL-qa.md").rename(repo.root / RPT_DIR / "moved-away.md")

    after = rows_by_id(home(verdicts))
    assert after["TKT-FAIL"]["verdict"] is None, after["TKT-FAIL"]
    assert after["TKT-FAIL"]["status"] == "fail", after["TKT-FAIL"]
    assert document.read_bytes() == before, "the claim was not touched; only its document moved"


# ── criterion 4 ──────────────────────────────────────────────────────────────────────────────

LEDGER = """# The ledger this repository keeps

| when | what broke | escaped from | who caught it |
|------|------------|--------------|---------------|
| 2026-01-02 | a gate said yes where it should have said no | TKT-BURNT | a person |
| 2026-01-03 | a second cause, naming a ticket nobody has | TKT-404 | a person |

Prose between the tables, and then a table that is not the ledger:

| a | b |
|---|---|
| TKT-CLEAN | not an escape, and not the first table |
"""


def ledger_repo(repo, gitrepo, *, declare):
    """Three tickets and a ledger naming exactly one of them.

    The second table names `TKT-CLEAN`, so an implementation reading every table in the file
    marks a ticket the ledger's own ledger does not — §3.5b says the FIRST markdown table and
    a fixture with one table cannot tell the two apart.
    """
    docs = {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}
    if declare:
        docs["escapes"] = "product/ESCAPES.md"
    m = manifest_for()
    m["docs"] = docs
    repo.manifest(m)
    repo.write(f"{REQ_DIR}/REQ-001-ledger.md",
               doc({"id": "REQ-001", "title": "What got past a gate", "status": "draft"}))
    for tid in ("TKT-BURNT", "TKT-CLEAN", "TKT-QUIET"):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": "pass"}))
    repo.write("product/ESCAPES.md", LEDGER)
    gitrepo.init()
    gitrepo.commit("the repository and the ledger it keeps")


def test_a_ticket_an_escape_names_is_marked_escaped(repo, gitrepo, serve):
    """§1 criterion 4, verbatim:

    **`test_a_ticket_an_escape_names_is_marked_escaped`** — a repository whose `ESCAPES.md`
    names a passed ticket answers `escaped: true` for it and `false` for its neighbours; a
    repository with no ledger answers `false` everywhere and a `200`.

    All three tickets claim `pass`, so `escaped` cannot be riding on the claim; the ledger's
    second row names an id the catalogue does not have, so a reader that trusted its cells
    rather than matching an id it holds would have to answer for a ticket that is not there.
    """
    ledger_repo(repo, gitrepo, declare=True)
    srv = serve_fast(serve, repo.root)
    rows = rows_by_id(home(srv))

    assert rows["TKT-BURNT"]["escaped"] is True, rows["TKT-BURNT"]
    assert rows["TKT-CLEAN"]["escaped"] is False, rows["TKT-CLEAN"]
    assert rows["TKT-QUIET"]["escaped"] is False, rows["TKT-QUIET"]
    assert sorted(rows) == ["TKT-BURNT", "TKT-CLEAN", "TKT-QUIET"], sorted(rows)


def test_a_repository_that_declares_no_ledger_has_no_escapes_and_not_an_error(
        repo, gitrepo, serve):
    """The second half of criterion 4, on its own repository.

    The ledger FILE is still on disk under the name this repository happens to use; only the
    manifest's declaration is gone. A tool that found it anyway would be a tool that had taught
    itself a filename (`S1`, §9.1).
    """
    ledger_repo(repo, gitrepo, declare=False)
    srv = serve_fast(serve, repo.root)

    r = srv.get("/api/home")
    assert r.status_code == 200, r.text
    rows = rows_by_id(r.json())
    assert [row["escaped"] for row in rows.values()] == [False, False, False], rows


# ── criterion 5 ──────────────────────────────────────────────────────────────────────────────

def test_the_ticket_array_costs_one_read(homeenv):
    """§1 criterion 5, verbatim:

    **`test_the_ticket_array_costs_one_read`** — the endpoint still issues no second scan of the
    catalogue: the array is built from the `views` `renderHome` already computes.

    Asserted twice, because neither half is the whole claim. The counts and the array are read
    in ONE assertion against each other (`FAC-FIX-091`: a count and its rows are one sentence) —
    two scans at two instants are free to disagree and each be correct. And the function itself
    is read: one scan of the ticket store, in the source, so an array built from a second scan
    that happens to agree today fails here.
    """
    body = homeenv.home()

    histogram = {}
    for row in body["tickets"]:
        histogram[row["delivery"]["state"]] = histogram.get(row["delivery"]["state"], 0) + 1
    counted = {state: n for state, n in counts_of(body).items() if n}
    assert counted == histogram, (counted, histogram)
    assert sum(counts_of(body).values()) == len(body["tickets"]), body["delivery"]

    source = (FACTORY / "server" / "app" / "catalog" / "render.cpp").read_text()
    start = source.index("catalog::Result catalog::renderHome")
    body_of_fn = source[start:source.index("\n}\n", start)]
    assert body_of_fn.count("Store::instance().tickets()") == 1, body_of_fn
    assert body_of_fn.count("viewsOf(") == 1, body_of_fn


# ── criterion 6 ──────────────────────────────────────────────────────────────────────────────

def test_it_stays_read_only(homeenv):
    """§1 criterion 6, verbatim:

    **`test_it_stays_read_only`** — `E1`'s git subcommand set is unchanged, and the new fields
    add no seventh (§3.5).

    And a refusal about a boundary is asserted at the boundary: the repository is read from the
    outside, before and after the call, so a seventh verb that wrote something would be visible
    as a change to the repository rather than only as a name missing from a list.
    """
    git = Git(homeenv.root)
    before = (git("status", "--porcelain"), git("rev-parse", "HEAD"), git("for-each-ref"))

    body = homeenv.home()
    assert body["tickets"], body

    assert sorted(ok(homeenv.srv, "/api/health")["delivery"]["git"]) == sorted(
        ["for-each-ref", "ls-tree", "rev-list", "rev-parse", "status", "worktree"])
    assert (git("status", "--porcelain"), git("rev-parse", "HEAD"), git("for-each-ref")) == before


# ── SRV-FEAT-363 round 2 — the two assertions QA's FAIL was missing ───────────────────────────
#
# Both are transcribed from QA's own probes (`docs/reports/SRV-FEAT-363/`), committed RED before
# any fix, because a QA FAIL is a gate that said yes where it should have said no. Criterion 2
# asserted the row's MEMBER SET, which a constant satisfies exactly as well as a served fact —
# `"role":null` was written literally and passed. Criterion 4's fixture wrote `status: pass` on
# every ticket, so an `escaped` that ignores "after it passed" could not be seen.


def cell_of(row):
    """§3.2b's table, transcribed verbatim in its declared order (QA probe 2).

    The cells are the POINT of this endpoint: `E30.tickets` exists so the strip can compute them
    from one read. A row that falls in no cell is a row three of the nine cells cannot be reached
    from, whatever its member set looks like.
    """
    lane, role = row["lane"], row["role"]
    process = row["state"]["process"]
    verdict, delivery, escaped = row["verdict"], row["delivery"]["state"], row["escaped"]
    if not lane:                                                      return "unclaimed"
    if lane and process in ("not_started", "queued"):                 return "claimed"
    # `run`, not `agent`: §3.2b named a role no code path emits — `_claim-lane.cpp:65` defaults an
    # unnamed claim to `run` and every other role is one the manifest declares. The SPEC was
    # corrected in this round rather than the word being bent here to match the code.
    if role == "run" and process in ("running", "blocked", "idle"):   return "agent running"
    if role == "run" and process == "finished":                       return "awaiting QA"
    if role == "qa" and process in ("running", "blocked", "idle"):    return "QA running"
    if verdict == "fail":                                             return "QA failed"
    if verdict == "pass" and delivery in ("unshipped", "waiting"):    return "awaiting merge"
    if delivery == "done" and not escaped:                            return "merged"
    if escaped:                                                       return "escaped"
    return None


def test_every_row_falls_in_exactly_one_of_section_3_2b_cells(homeenv):
    """BLOCKER 1 from QA — `role` was a literal `null`, so three cells were unreachable.

    `render.cpp` wrote `,"role":null` unconditionally while `E9` answered `role` for the same lane,
    the same ticket, the same instant. Criterion 2 passed anyway because it asserts the member set.
    This asserts the CONSEQUENCE instead: every row lands in one of §3.2b's nine cells.
    """
    body = homeenv.home()
    homeless = [(r["ticket_id"], r["lane"], r["role"], r["state"]["process"])
                for r in body["tickets"] if cell_of(r) is None]
    assert not homeless, (
        f"§3.2b constraint 2: these rows fall in NO cell: {homeless}. Three of the nine cells "
        f"read `role`, so a row whose role is a constant can never reach them.")

    live = rows_by_id(body)[homeenv.ticket]
    assert live["role"], (
        f"a lane is held and the row names no role: {live}. E9 answers one for this lane.")


LEDGER_COLUMNS = """# The ledger this repository keeps

| Date | Escape | Escaped from | Found by | Root cause of the MISS | Change landed |
|------|--------|--------------|----------|------------------------|---------------|
| 2026-01-02 | a gate said yes | TKT-BURNT | a person | the check was typed | TKT-FIXER |
| 2026-01-03 | a second cause | TKT-GONE | a person | a proxy was tested | TKT-BURNT |
"""


@pytest.fixture
def ledger_columns(repo, gitrepo, serve):
    """A ledger with the column that means *this escaped*, and the one that does not.

    `Escaped from` names the ticket a defect got past. `Change landed` names the ticket that FIXED
    somebody else's escape — the opposite claim, in the same row. On this repository's own 201-row
    ledger, reading any cell marked 29 tickets escaped that only ever appear in `Change landed`
    (`SRV-FIX-378`, `SRV-FIX-166`, `WEB-FEAT-079`…) and missed 13 that are named in `Escaped from`.
    Row 211 shows both at once: `SRV-FIX-376` escaped, `SRV-FIX-378` fixed it.

    `TKT-GONE` also carries `status: todo`: the LEDGER ROW is the evidence a gate said yes, so a
    ticket's status today says nothing about whether it escaped one.
    """
    docs = {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR, "escapes": "product/ESCAPES.md"}
    m = manifest_for(); m["docs"] = docs
    repo.manifest(m)
    repo.write(f"{REQ_DIR}/REQ-001-ledger.md",
               doc({"id": "REQ-001", "title": "What got past a gate", "status": "draft"}))
    for tid, status in (("TKT-BURNT", "pass"), ("TKT-FIXER", "pass"), ("TKT-GONE", "todo")):
        repo.write(f"{TKT_DIR}/{tid}.md",
                   doc({"id": tid, "parent": "REQ-001", "workspace": "alpha",
                        "title": f"The work of {tid}", "status": status}))
    repo.write("product/ESCAPES.md", LEDGER_COLUMNS)
    gitrepo.init()
    gitrepo.commit("a ledger with both columns")
    return serve_fast(serve, repo.root)


def test_escaped_reads_the_column_that_means_it_escaped(ledger_columns):
    """BLOCKER from QA round 2 — the column, not the row.

    `escaped` matched an id in ANY cell. `Change landed` names the ticket that FIXED an escape,
    which is the opposite claim, and it sits in the same row as the one that escaped.
    """
    rows = rows_by_id(home(ledger_columns))

    assert rows["TKT-BURNT"]["escaped"] is True, (
        f"named in `Escaped from` and not marked escaped: {rows['TKT-BURNT']}")
    assert rows["TKT-FIXER"]["escaped"] is False, (
        f"`TKT-FIXER` appears only in `Change landed` — it FIXED somebody else's escape — and is "
        f"marked as having escaped one: {rows['TKT-FIXER']}")
    assert rows["TKT-GONE"]["escaped"] is True, (
        f"named in `Escaped from` but reported false because its status is "
        f"{rows['TKT-GONE']['status']!r}. The LEDGER ROW is the evidence a gate said yes; a "
        f"ticket's status today says nothing about whether it escaped one: {rows['TKT-GONE']}")


@pytest.fixture
def ledger_escapes(repo, gitrepo, serve):
    """The ledger's own fixture, plus the ticket QA's finding needed: one the ledger NAMES that
    never passed. `TKT-OPEN` is `todo` and appears in the ledger exactly as `WEB-FIX-329` does in
    this repository's — as the ticket that will FIX something, not as work that escaped a gate."""
    ledger_repo(repo, gitrepo, declare=True)
    repo.write(f"{TKT_DIR}/TKT-OPEN.md",
               doc({"id": "TKT-OPEN", "parent": "REQ-001", "workspace": "alpha",
                    "title": "The work of TKT-OPEN", "status": "todo"}))
    repo.write("product/ESCAPES.md", LEDGER.replace(
        "| 2026-01-03 | a second cause, naming a ticket nobody has | TKT-404 | a person |",
        "| 2026-01-03 | a second cause, naming a ticket nobody has | TKT-404 | a person |\n"
        "| 2026-01-04 | a third cause, to be fixed by TKT-OPEN | TKT-BURNT | a person |"))
    gitrepo.commit("a ticket the ledger names that never passed")
    return serve_fast(serve, repo.root)


def test_escaped_means_a_defect_found_after_the_ticket_passed(ledger_escapes):
    """BLOCKER 2 from QA — `escaped` matched any id anywhere in the ledger's first table.

    On the real repository that marked 188 of 378 tickets escaped, 75 of which never claimed pass:
    66 `superseded` and 5 `todo`. `WEB-FIX-329` is `todo` and appears in `ESCAPES.md` once, inside
    a cell's prose, as the ticket that will FIX a baseline — and was called an escape.

    §1's word is "a passed ticket". A ticket that never passed cannot have escaped a gate, because
    no gate ever said yes about it.
    """
    rows = rows_by_id(home(ledger_escapes))
    assert rows["TKT-BURNT"]["escaped"] is True, rows["TKT-BURNT"]
    assert rows["TKT-OPEN"]["escaped"] is False, (
        f"a ticket the ledger names is marked escaped while its own status is "
        f"{rows['TKT-OPEN']['status']!r}: nothing ever passed, so nothing escaped a gate. "
        f"{rows['TKT-OPEN']}")


# ── SRV-FEAT-363 round 4 — QA's third FAIL, both blockers ─────────────────────────────────────
#
# Both are the same shape: a field of the row derived by a LOSSY search over a document instead of
# by reading the structure the document has. Transcribed from
# `docs/reports/SRV-FEAT-363/SRV-FEAT-363-qa-r3-probe.py`, RED before either fix.


def test_a_ledger_row_naming_two_tickets_marks_both(ledger_columns, repo, gitrepo, serve):
    """BLOCKER 2 — `idNamedIn` returns the FIRST hit over a sorted set.

    A row naming two tickets marks whichever id sorts first. `ESCAPES.md:120` is a well-formed
    six-cell row reading `` `WEB-FEAT-176` / `WEB-FEAT-179` `` and only `176` was marked. Third
    round on this one field, which is what makes it a blocker by itself.
    """
    repo.write(f"{TKT_DIR}/TKT-ZEBRA.md",
               doc({"id": "TKT-ZEBRA", "parent": "REQ-001", "workspace": "alpha",
                    "title": "The work of TKT-ZEBRA", "status": "pass"}))
    repo.write("product/ESCAPES.md", LEDGER_COLUMNS.replace(
        "| 2026-01-03 | a second cause | TKT-GONE | a person | a proxy was tested | TKT-BURNT |",
        "| 2026-01-03 | a second cause | `TKT-BURNT` / `TKT-ZEBRA` | a person | a proxy | TKT-FIXER |"))
    gitrepo.commit("one row, two tickets")
    rows = rows_by_id(home(serve_fast(serve, repo.root)))

    assert rows["TKT-BURNT"]["escaped"] is True, rows["TKT-BURNT"]
    assert rows["TKT-ZEBRA"]["escaped"] is True, (
        f"one row names two tickets and only the first-sorting one is marked: {rows['TKT-ZEBRA']}")


def test_the_verdict_reads_the_reports_directory_of_the_ticket_s_own_repository(
        repo, gitrepo, serve, tmp_path):
    """BLOCKER 1 — rows come from EVERY declared repository, `reportsAbs` from `repos.front()`.

    `escapedIds()` twelve lines above loops the repos correctly; the asymmetry is inside one
    function. On a two-repo manifest a ticket that never had QA answers `pass` from another
    repository's file, and one whose verdict document exists answers `null`.
    """
    # the manifest shape `test_catalog.py` already uses for two repositories
    m = {
        "factory": "0.1",
        "repos": {
            "first": {"path": ".", "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}},
            "second": {"path": "second",
                       "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR}},
        },
        "workspaces": {"alpha": {"repo": "first", "path": "."},
                       "beta": {"repo": "second", "path": "."}},
        "status": {"in_flight": ["in-progress", "awaiting-qa"]},
        "agent": {"command": "claude"},
    }
    repo.manifest(m)
    repo.write(f"{REQ_DIR}/REQ-001-two.md", doc({"id": "REQ-001", "title": "Two", "status": "draft"}))
    repo.write(f"{TKT_DIR}/TKT-ONE.md",
               doc({"id": "TKT-ONE", "parent": "REQ-001", "workspace": "alpha",
                    "title": "In the first repo", "status": "pass"}))
    repo.write(f"second/{REQ_DIR}/REQ-002-two.md",
               doc({"id": "REQ-002", "title": "Two, second", "status": "draft"}))
    repo.write(f"second/{TKT_DIR}/TKT-TWO.md",
               doc({"id": "TKT-TWO", "parent": "REQ-002", "workspace": "beta",
                    "title": "In the second repo", "status": "pass"}))
    # the verdict document sits beside its OWN ticket, in the second repository
    repo.write(f"second/{RPT_DIR}/TKT-TWO-qa.md", "# TKT-TWO — QA verdict\n\nChecked.\n")
    gitrepo.init()
    gitrepo.commit("two repositories, one verdict document")

    rows = rows_by_id(home(serve_fast(serve, repo.root)))
    assert rows["TKT-TWO"]["verdict"] == "pass", (
        f"the verdict document sits beside its own ticket in the second repository and was not "
        f"found — the reports directory came from repos.front(): {rows['TKT-TWO']}")
    assert rows["TKT-ONE"]["verdict"] is None, (
        f"no verdict document exists for TKT-ONE and it answered "
        f"{rows['TKT-ONE']['verdict']!r}: a claim was taken for a verdict")
