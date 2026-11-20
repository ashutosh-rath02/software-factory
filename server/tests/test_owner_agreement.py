"""SRV-FIX-377 / SRV-FIX-380 — every emitter of the ticket document names the same owner.

`catalog::renderTickets` takes an `AuthorLookup`; `catalog::renderTicket` takes none, so the
single-ticket endpoint cannot serve an owner whatever the repository holds. A list and a detail
that share a row may not disagree about it.

Found by `WEB-FEAT-325`'s tier 3: the header chip was green against a fixture and drew nothing on
the real screen. Every case here asks the RUNNING SERVER both questions about one ticket, because
that disagreement is invisible to anything that reads a type or a schema.

`SRV-FIX-380` adds the THIRD emitter, which `SRV-FIX-377` never enumerated: `subscribe
{ticket:<id>}` on `/ws`, whose snapshot `docs/technical/contracts/socket.schema.json` declares to be "the shape of the
HTTP endpoint that answers the same question ... E5 for a ticket". It served that shape minus
`owner`, because `renderTicket`'s `authors` parameter carried a default and `socket.cpp` omitted
it. A criterion is discharged by the members it lists; this file now lists all three.

SRV-FIX-379 — every case here is run under a WORLD, and a guard proves each one can go red.

`FACTORY_OWNER_WORLD` picks the world. `honest` is the default and every case passes in it; each
other world is an input this suite builds for itself, and each case names in `FIRING_WORLD` the one
world it is shown red in. The guard runs each case ALONE in its own world, one subprocess apiece,
and names any case it could not redden — because `SRV-FIX-377`'s mandatory negative was green in
the very condition its name described and nothing compares a test's name to what the test does.

**A world reddens the case registered against it and says nothing about the rest.** It is an input,
not a scalpel: `e5-drops-the-lookup` puts the seam on every server this module starts, so it also
reddens cases whose guard clauses need an owner E5 no longer serves, and `nobody-committed` reddens
two. The file claimed *exactly one* until `SRV-FIX-379` round 3 and that was never measured
(`SRV-FIX-408`). Nothing depends on it: the sweep never runs this file whole under one world.

The guard cases are the one category the sweep excludes. Running the guard inside itself does not
terminate, and their own firability is `SRV-FIX-379` §1 criteria 3 and 4 — which point the guard at
a case that cannot go red, once as a plain function and once as a method on a class that only
pytest's collection can see, and require it to name that case and fail.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

TKT_DIR, REQ_DIR, RPT_DIR = "docs/tickets", "docs/requirements", "docs/reports"

SERVER_DIR = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()

WORLD_ENV = "FACTORY_OWNER_WORLD"
HONEST = "honest"
E5_DROPS_THE_LOOKUP = "e5-drops-the-lookup"
EVERY_TICKET_OWNED = "every-ticket-owned"
NOBODY_COMMITTED = "nobody-committed"

SEAM = {"FACTORY_TEST_HOOKS": "1", "FACTORY_TEST_TICKET_WITHOUT_AUTHORS": "1"}

GUARD = "guard: this case IS the firability guard — criterion 3 is its negative"

FIRING_WORLD = {
    "test_e5_and_e29_agree_about_the_owner": E5_DROPS_THE_LOOKUP,
    "test_no_branch_no_owner_from_either_endpoint": EVERY_TICKET_OWNED,
    "test_e5_without_the_lookup_makes_the_agreement_case_red": NOBODY_COMMITTED,
    "test_every_case_here_can_be_made_to_fail": GUARD,
    "test_the_firability_guard_names_a_case_that_cannot_fail": GUARD,
    "test_the_guard_names_a_class_based_case_that_cannot_go_red": GUARD,
    "test_the_socket_snapshot_agrees_with_e5_about_the_owner": E5_DROPS_THE_LOOKUP,
    "test_no_branch_no_owner_from_the_socket": EVERY_TICKET_OWNED,
    "test_the_socket_snapshot_is_e5_whole_apart_from_the_members_srv_fix_399_owns":
        E5_DROPS_THE_LOOKUP,
}


def world():
    return os.environ.get(WORLD_ENV, HONEST)


def manifest():
    return {
        "factory": "0.1",
        "workspaces": {"alpha": {"path": "."}},
        "docs": {"epics": REQ_DIR, "tickets": TKT_DIR, "reports": RPT_DIR},
        "stages": ["SPEC", "TEST", "IMPL", "GATE", "QA"],
        "agent": {"command": "claude"},
    }


def doc(front, body="Body."):
    return "\n".join(["---", *[f"{k}: {v}" for k, v in front.items()], "---", "", body, ""])


def git(root, *args, author=None):
    env = None
    if author:
        env = dict(os.environ, GIT_AUTHOR_NAME=author, GIT_COMMITTER_NAME="Maintainer",
                   GIT_AUTHOR_EMAIL="a@example.invalid",
                   GIT_COMMITTER_EMAIL="m@example.invalid")
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                         timeout=30, env=env)
    assert out.returncode == 0, f"git {' '.join(args)} -> {out.stderr}"
    return out.stdout.strip()


@pytest.fixture
def owned(repo):
    """A repository where TIX-001 has a branch authored by somebody, and TIX-002 has none.

    `every-ticket-owned` gives TIX-002 a branch too; `nobody-committed` gives neither one.
    """
    repo.mkdir(TKT_DIR)
    repo.write(f"{TKT_DIR}/TIX-001.md",
               doc({"id": "TIX-001", "title": "Has a branch", "workspace": "alpha",
                    "status": "in-progress", "parent": "standalone"}))
    repo.write(f"{TKT_DIR}/TIX-002.md",
               doc({"id": "TIX-002", "title": "Has no branch", "workspace": "alpha",
                    "status": "todo", "parent": "standalone"}))
    root = repo.manifest(manifest())
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "m@example.invalid")
    git(root, "config", "user.name", "Maintainer")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-m", "base", "--no-gpg-sign")

    branches = []
    if world() != NOBODY_COMMITTED:
        branches.append(("ticket/TIX-001", "TIX-001", "Member Dev"))
    if world() == EVERY_TICKET_OWNED:
        branches.append(("ticket/TIX-002", "TIX-002", "Other Dev"))
    for branch, ticket, author in branches:
        git(root, "checkout", "-b", branch)
        repo.write(f"worked-{ticket}.txt", f"{author}'s work\n")
        git(root, "add", "-A")
        git(root, "commit", "-m", f"{ticket}: the member's work", "--no-gpg-sign", author=author)
        git(root, "checkout", "main")
    return root


def start(serve, root, **env):
    """Start a server for this module, honouring the world.

    `e5-drops-the-lookup` puts the seam on EVERY server this module starts, which is the input the
    guard uses to make criterion 1's case go red without anybody editing a source file.
    """
    if world() == E5_DROPS_THE_LOOKUP:
        env = dict(env, **SEAM)
    return serve(root, **env)


def owners(srv, ticket_id="TIX-001"):
    """`(what E29 says, what E5 says)` about one ticket, asked of one server in one moment."""
    index = srv.get("/api/tickets").json()
    rows = {t["id"]: t for g in index["groups"] for t in g["tickets"]}
    one = srv.get(f"/api/tickets/{ticket_id}").json()
    return rows[ticket_id].get("owner"), one.get("owner")


def assert_endpoints_agree(from_index, from_detail):
    """The one comparison in this file. Both criterion 1 and its negative run THIS function."""
    assert from_index is not None, (
        "the fixture did not produce an owner from E29 either, so this comparison cannot see the "
        f"disagreement it exists for: {from_index!r}")
    assert from_detail == from_index, (
        f"E29 says the owner of TIX-001 is {from_index!r} and E5 says {from_detail!r}. "
        f"A list and a detail that share a row may not disagree about it — the ticket header "
        f"reads E5 and can never name who wrote it.")


def cases_in(path, timeout=120):
    """Every case in a test file, as the node-id suffix pytest addresses it by.

    The baseline for what a CASE is comes from pytest's own collection, because a case is whatever
    pytest will RUN. A set derived from the file's top-level functions is narrower than that — a
    method on a class is collected, is run, and is invisible to it — so the guard could never
    report the one thing it exists to report about such a case (`SRV-FIX-379` §1 criterion 4).

    A collection that produced nothing raises rather than returning an empty set: a sweep over no
    cases reports zero for the same reason a dead one does. `-o addopts=` is why it does not:
    pytest's `-q` is a DECREMENT, so what one of them prints depends on the `-v` in whichever
    `pytest.ini` the collected file happens to sit under, and above a threshold pytest prints a
    per-file count with no node id in it at all.
    """
    path = Path(path).resolve()
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", str(path), "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(path.parent), capture_output=True, text=True, timeout=timeout)
    collected = sorted(line.split("::", 1)[1].strip()
                       for line in out.stdout.splitlines() if "::" in line)
    assert out.returncode == 0 and collected, (
        f"pytest collected no case from {path}, so a sweep over it would examine nothing and "
        f"report zero. exit={out.returncode}\n{out.stdout}\n{out.stderr}")
    return collected


def cases_that_cannot_go_red(node_world, cwd=SERVER_DIR, timeout=600):
    """Run each node id in its world and return, in order, the ones that exited 0.

    A subprocess, because a case is only firable if the case itself — its fixtures, its server, its
    assertions — goes red, and nothing short of running it can say that.
    """
    green = []
    for node_id, in_world in node_world.items():
        env = dict(os.environ, **{WORLD_ENV: in_world})
        out = subprocess.run(
            [sys.executable, "-m", "pytest", str(node_id), "-q", "-p", "no:cacheprovider"],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)
        if out.returncode == 0:
            green.append(node_id)
    return green


def firability_verdict(node_world, cwd=SERVER_DIR):
    """Fail naming every case that could not be made to fail. Never reports a bare count.

    `raise`, not `assert`: pytest rewrites an `assert` and appends the repr of the expression it
    tested, so a guard that reported nothing but a COUNT would still have every name read back out
    of it by criterion 3. What is asserted has to be the words this function chose.
    """
    green = cases_that_cannot_go_red(node_world, cwd=cwd)
    if green:
        raise AssertionError(
            f"{len(green)} of {len(node_world)} case(s) stayed green in the world meant to redden "
            f"them, so they are checks that cannot fail:\n"
            + "\n".join(f"  {node} — world {node_world[node]!r}" for node in green))


def test_e5_and_e29_agree_about_the_owner(repo, serve, owned):
    """§1 criterion 1 of SRV-FIX-377."""
    srv = start(serve, owned)
    from_index, from_detail = owners(srv)
    assert_endpoints_agree(from_index, from_detail)


def test_no_branch_no_owner_from_either_endpoint(repo, serve, owned):
    """§1 criterion 2 of SRV-FIX-377 — `null` is the answer when nobody has committed. Inventing
    one from `main` would make the header claim an absent fact."""
    srv = start(serve, owned)
    index = srv.get("/api/tickets").json()
    rows = {t["id"]: t for g in index["groups"] for t in g["tickets"]}
    one = srv.get("/api/tickets/TIX-002").json()
    assert rows["TIX-002"].get("owner") is None, rows["TIX-002"].get("owner")
    assert one.get("owner") is None, one.get("owner")


def test_e5_without_the_lookup_makes_the_agreement_case_red(repo, serve, owned):
    """§1 criterion 1 of SRV-FIX-379 · MANDATORY NEGATIVE, and it drops the lookup itself.

    `SRV-FIX-377`'s negative was named for this experiment and never performed it: it read
    `_from_detail` and discarded it. This one serves E5 with no `AuthorLookup` — the seam, set here
    and nowhere a person reaches — and requires the comparison criterion 1 makes to go red naming
    both values.
    """
    srv = start(serve, owned, **SEAM)
    from_index, from_detail = owners(srv)

    assert from_index != from_detail, (
        f"E5 served {from_detail!r} with the lookup dropped and E29 served {from_index!r}: the two "
        f"agree, so there is no disagreement here for the comparison to be red about and this "
        f"negative has witnessed nothing.")

    with pytest.raises(AssertionError) as red:
        assert_endpoints_agree(from_index, from_detail)
    message = str(red.value)
    for value in (from_index, from_detail):
        assert repr(value) in message, (
            f"the comparison went red without naming {value!r}, so a person reading the failure "
            f"cannot tell which endpoint said what: {message}")
    assert "E29" in message and "E5" in message, message

    quiet = serve(owned, FACTORY_TEST_TICKET_WITHOUT_AUTHORS="1")
    assert_endpoints_agree(*owners(quiet))


def test_every_case_here_can_be_made_to_fail(repo):
    """§1 criterion 2 of SRV-FIX-379.

    The set of cases is derived from this module, never typed: a case added here with no world
    registered against it fails this guard by name.
    """
    here = set(cases_in(THIS_FILE))

    unregistered = sorted(here - set(FIRING_WORLD))
    assert not unregistered, (
        "these cases in this file have no world that reddens them, so nothing here shows they can "
        f"fail: {unregistered}")
    stale = sorted(set(FIRING_WORLD) - here)
    assert not stale, f"FIRING_WORLD names cases this file no longer has: {stale}"

    swept = {f"{THIS_FILE}::{name}": FIRING_WORLD[name]
             for name in sorted(here) if FIRING_WORLD[name] is not GUARD}
    excluded = sorted(name for name in here if FIRING_WORLD[name] is GUARD)
    assert excluded == ["test_every_case_here_can_be_made_to_fail",
                        "test_the_firability_guard_names_a_case_that_cannot_fail",
                        "test_the_guard_names_a_class_based_case_that_cannot_go_red"], (
        f"the only category excluded from this sweep is the guard itself: {excluded}")
    assert len(swept) >= 3, (
        f"this guard swept {len(swept)} case(s); a sweep that examines almost nothing reports zero "
        f"for the same reason a dead one does: {sorted(swept)}")

    firability_verdict(swept)


def test_the_firability_guard_names_a_case_that_cannot_fail(tmp_path):
    """§1 criterion 3 of SRV-FIX-379 · MANDATORY NEGATIVE — the guard can itself fail."""
    unfirable = tmp_path / "test_unfirable.py"
    unfirable.write_text("def test_this_case_cannot_go_red():\n    assert True\n")
    node = f"{unfirable}::test_this_case_cannot_go_red"

    with pytest.raises(AssertionError) as red:
        firability_verdict({node: HONEST}, cwd=tmp_path)
    message = str(red.value)
    assert "test_this_case_cannot_go_red" in message, (
        f"the guard failed without naming the case that cannot go red: {message}")
    assert "1 of 1" in message, message


def test_the_guard_names_a_class_based_case_that_cannot_go_red(tmp_path):
    """§1 criterion 4 of SRV-FIX-379 · MANDATORY NEGATIVE — transcribed from QA round 2's Finding 2.

    QA planted `class TestQaProbe: def test_a_class_based_case_that_can_never_go_red` in this file
    and the guard stayed green over six passing cases, one of which can never fail. The guard's
    baseline for what a CASE is came from the module's top-level functions, which is narrower than
    the set pytest runs, so the exact class this ticket exists to close was invisible to it.

    The probe holds one case of each kind: one that goes red in the world it is handed, and one
    that cannot go red at all. The verdict must name the second and leave the first alone.
    """
    probe = tmp_path / "test_collection_probe.py"
    probe.write_text(
        "import os\n"
        "\n"
        "\n"
        "def test_a_module_level_case_that_can_go_red():\n"
        "    assert os.environ.get('FACTORY_OWNER_WORLD') != 'probe-world'\n"
        "\n"
        "\n"
        "class TestQaProbe:\n"
        "    def test_a_class_based_case_that_can_never_go_red(self):\n"
        "        assert True\n")

    collected = cases_in(probe)
    assert "TestQaProbe::test_a_class_based_case_that_can_never_go_red" in collected, (
        "pytest will run this case and the guard cannot see it, so a case that can never go red "
        f"sits in the file and is never reported by name: {collected}")
    assert "test_a_module_level_case_that_can_go_red" in collected, collected

    with pytest.raises(AssertionError) as red:
        firability_verdict({f"{probe}::{case}": "probe-world" for case in collected}, cwd=tmp_path)
    message = str(red.value)
    assert "test_a_class_based_case_that_can_never_go_red" in message, (
        f"the guard swept the class-based case and did not name it: {message}")
    assert "test_a_module_level_case_that_can_go_red" not in message, (
        f"the guard named a case that DID go red in its world, so its verdict cannot be read as a "
        f"list of checks that cannot fail: {message}")


# ── SRV-FIX-380 · the third emitter ──────────────────────────────────────────────────────────

def snapshot_for(srv, ticket_id):
    """The `snapshot.data` a real `/ws` subscriber receives for `{ticket: <id>}`.

    Driven over the socket the product actually serves — greeting judged, one masked frame sent,
    the reply's scope checked — because the defect is on the wire and nothing that reads a type
    or a schema can see it.
    """
    c = srv.ws()
    try:
        hello = c.recv()
        assert hello["m"] == "hello", hello
        c.send({"m": "subscribe", "scope": {"ticket": ticket_id}})
        snap = c.wait_for("snapshot")
        assert snap["scope"] == {"ticket": ticket_id}, snap
        return snap["data"]
    finally:
        c.close()


def test_the_socket_snapshot_agrees_with_e5_about_the_owner(repo, serve, owned):
    """§1 criterion 1.

    One server, one moment, one ticket whose branch carries a commit by a known author. The
    socket's snapshot is documented as E5's shape; if it names a different owner — or none — a
    consumer of the published socket contract disagrees with the ticket page about who wrote it.
    """
    srv = start(serve, owned)
    from_detail = srv.get("/api/tickets/TIX-001").json().get("owner")
    from_socket = snapshot_for(srv, "TIX-001").get("owner")

    assert from_detail, (
        "E5 itself served no owner for TIX-001, so this case cannot see the disagreement it "
        f"exists for — it would pass on two absences. E5 said: {from_detail!r}")
    assert from_socket == from_detail, (
        f"E5 says the owner of TIX-001 is {from_detail!r} and the /ws snapshot says "
        f"{from_socket!r}. `docs/technical/contracts/socket.schema.json` declares snapshot.data to be E5's shape for "
        f"a ticket scope, so a consumer of the published contract is told nobody wrote it.")


def test_no_branch_no_owner_from_the_socket(repo, serve, owned):
    """§1 criterion 2 — absent, not `{}`, and not a name derived from `main`.

    `main` in this fixture has a commit by `Maintainer`. A snapshot that reported an owner for
    TIX-002 would be claiming an absent fact off the wrong branch, and it must agree with the two
    endpoints that already answer nothing.
    """
    srv = start(serve, owned)
    index = srv.get("/api/tickets").json()
    rows = {t["id"]: t for g in index["groups"] for t in g["tickets"]}
    from_socket = snapshot_for(srv, "TIX-002")

    assert rows["TIX-002"].get("owner") is None, rows["TIX-002"].get("owner")
    assert srv.get("/api/tickets/TIX-002").json().get("owner") is None
    assert "owner" not in from_socket, (
        f"TIX-002 has no branch, so the socket must omit the member entirely rather than send "
        f"an empty object or a name taken from `main`. It sent: {from_socket.get('owner')!r}")


# The members `socket.cpp` still cannot answer, excluded by CATEGORY with a reason each rather
# than by deleting rows until the case passes. All three are decided by a lookup the ticket
# branch of `Connection::subscribe_` passes as `{}`, which SRV-FIX-380 §3 deliberately left in
# place and SRV-FIX-399 owns. Measured, not assumed — a ticket carrying one run of 6354 s reads
# `runs {count: 0}` and `state.process not_started` from the socket while E5 says `count: 1` and
# `crashed`.
NOT_YET_ANSWERED_BY_THE_SOCKET = {
    "runs": "RunsLookup is passed {} — SRV-FIX-399",
    "state": "its `process` axis is derived from the same run history — SRV-FIX-399",
    "stage": "StageLookup is passed {} — SRV-FIX-399",
}


def test_the_socket_snapshot_is_e5_whole_apart_from_the_members_srv_fix_399_owns(repo, serve,
                                                                                 owned):
    """The claim underneath criteria 1 and 2, asserted against the WHOLE document rather than
    against `owner` alone.

    `docs/technical/contracts/socket.schema.json` says snapshot.data IS E5's shape. Comparing only `owner` would go
    green the day another member starts disagreeing, which is precisely the escape this ticket
    exists for — a criterion discharged by the members it happened to list.

    So this compares every member and excludes three by name, each with a reason and a ticket.
    The exclusion is asserted from BOTH sides: a member that leaves the exclusion list must start
    agreeing, and a member that disagrees without being on it fails here. On this fixture — no
    planted run, no stage history — the three excluded members happen to agree anyway, and the
    case says so rather than quietly counting that as coverage.
    """
    srv = start(serve, owned)
    from_detail = srv.get("/api/tickets/TIX-001").json()
    from_socket = snapshot_for(srv, "TIX-001")

    compared = sorted((set(from_detail) | set(from_socket)) - set(NOT_YET_ANSWERED_BY_THE_SOCKET))
    assert len(compared) >= 10, (
        f"only {compared} were compared, so this case swept almost nothing and cannot be read as "
        f"the snapshot agreeing with E5")

    differing = [k for k in compared if from_detail.get(k) != from_socket.get(k)]
    assert differing == [], (
        f"the /ws snapshot and E5 disagree about {differing} for one ticket in one moment, and "
        f"none of these is a member SRV-FIX-399 owns. "
        f"E5: { {k: from_detail.get(k) for k in differing} }; "
        f"socket: { {k: from_socket.get(k) for k in differing} }")

    still_excluded = sorted(k for k in NOT_YET_ANSWERED_BY_THE_SOCKET if k in from_detail)
    assert still_excluded == sorted(NOT_YET_ANSWERED_BY_THE_SOCKET), (
        f"the exclusion list names {sorted(NOT_YET_ANSWERED_BY_THE_SOCKET)} but E5 serves only "
        f"{still_excluded}. An excluded member E5 no longer emits is an exclusion nobody can "
        f"check — narrow the list or delete it, do not leave it standing")
