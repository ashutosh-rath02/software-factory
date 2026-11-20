"""SRV-FIX-293 — a lane's worktree is a checkout of the GIT root, so document paths miss by the offset.

`SRV-FIX-290` separated the document root from the git root and fixed the one consumer that failed
loudly — confinement. This is the consumer that fails loudly with the WRONG CAUSE.

    api::prompt::inLane(path, repoRoot, worktree)      app/api/prompt.cpp:8
        strips `repoRoot`  = manifest data().root      ← the DOCUMENT root
        appends to `worktree`                          ← a checkout of the GIT root

For this repository that is `.../lane/01/docs/tickets/X.md` when the file is at
`.../lane/01/factory/docs/tickets/X.md`. The refusal is `ticket_not_on_branch`, which tells the
operator to commit a document that is already committed — a cause they can disprove in one
`git ls-tree`, which is what makes it cost an hour.

§1 took the first of its two answers: **the offset is carried**. The worktree stays a full checkout
of the git root, because the workspaces need it (`factory-server`'s build is `cd factory/server`,
which only exists if the worktree holds the monorepo).
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from test_lane import build_repo, git, lane_path, status_of   # noqa: E402
from test_roots import nested_repo, transcript_of             # noqa: E402


def _claim(srv, ticket="TCK-001", workspace="core"):
    r = srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})
    assert r.status_code == 201, f"claiming a lane failed before the run could be started: {r.text}"
    return r.json()["lane"]


def _start_run(srv, lane, body=None):
    return srv.post(lane_path(lane) + "/runs", json=body if body is not None else {})


@pytest.fixture
def nested(tmp_path, serve):
    """A repository whose document root is one level below its git root, served.

    Two things this fixture must get right, both of which round 1 got wrong:

    1. The manifest MUST declare `agent.start_prompt_file`. The ticket-path resolution this file is
       about lives in `startWithDeclaredPrompt`, which only runs when one is declared — so a
       fixture without one starts runs happily and asserts nothing. That is SRV-FIX-290's escape,
       and round 1 reproduced it here.
    2. The agent MUST be reachable INSIDE the lane. Round 1 put the stand-in under the pytest tmp
       directory, which is a tmpfs in the lane, and every run died at
       `bwrap: execvp .../agent/claude: No such file or directory` while `POST /runs` still
       answered 201. The agent is committed into the repository and declared `sub/agent.sh` — with
       the offset, because the worktree is a checkout of `top`.

    The agent echoes the prompt it was given, so the substituted `{ticket_path}` reaches the
    transcript and the two candidate joins can be told apart.
    """
    top, sub = nested_repo(tmp_path)

    agent = sub / "agent.sh"
    agent.write_text(
        "#!/bin/sh\n"
        "P=$(head -1 | sed 's/\"/ /g')\n"
        "echo '{\"type\":\"assistant\",\"message\":{\"content\":"
        "[{\"type\":\"text\",\"text\":\"PROMPT_WAS '\"$P\"'\"}]}}'\n"
        "sleep 5\n")
    agent.chmod(0o755)

    (sub / "docs" / "start-prompt.md").write_text("open {ticket_path} and report SPEC\n")

    mf = sub / ".factory" / "factory.json"
    data = json.loads(mf.read_text())
    data["agent"] = {"command": "/bin/sh sub/agent.sh",
                     "start_prompt_file": "docs/start-prompt.md"}
    mf.write_text(json.dumps(data, indent=2))

    git(top, "add", "-A")
    git(top, "-c", "user.email=t@t", "-c", "user.name=t",
        "commit", "-m", "an agent the fixture owns, and a start prompt naming {ticket_path}")

    return top, sub, serve(sub)


def test_a_run_starts_for_a_ticket_when_the_document_root_is_below_the_git_root(nested):
    """THE criterion, and §1's words are "assert the agent's TRANSCRIPT carries its own output".

    ROUND 2. Round 1 asserted the status code of `POST /runs` and stopped there — which is the exact
    shape this ticket's own §3 diagnoses as SRV-FIX-290's escape: *"the criterion proved the sandbox
    could be built and the process could exec ... it was never a test of the tool finding
    anything."* A 201 proves the run was accepted. It does not prove the tool resolved the ticket
    to a path that exists.

    The discriminating assertion, per QA's reproduction: the fixture's agent is /bin/sh fed the
    prompt `open {ticket_path} and report SPEC`, so the substituted path lands in the transcript.
    It must be <worktree>/sub/docs/tickets/TCK-001.md — carrying the `sub` offset. Against the
    pre-fix join it is <worktree>/docs/tickets/TCK-001.md, and a 201 alone cannot tell them apart.
    """
    top, sub, srv = nested
    lane = _claim(srv)
    r = _start_run(srv, lane)

    assert r.status_code != 409 or "ticket_not_on_branch" not in r.text, (
        "the tool refused to start a run for a ticket that IS on the branch. It stripped the "
        "document root and appended to a worktree that is a checkout of the GIT root, so the path "
        f"it looked at misses by the offset between them.\n{r.status_code} {r.text}")
    assert r.status_code in (200, 201, 202), (
        f"starting a run failed for a reason other than the path: {r.status_code} {r.text}")

    run_id = r.json()["run_id"]

    entries = []
    deadline = time.time() + 30
    while time.time() < deadline:
        entries = transcript_of(sub, run_id)
        if entries:
            break
        time.sleep(0.5)

    assert entries, (
        f"the run was accepted ({r.status_code}) and its transcript is empty. The tool recorded a "
        f"run; nothing shows the agent ever opened anything. This is the assertion §1 names and "
        f"round 1 omitted.")

    blob = json.dumps(entries)
    expected = "/sub/docs/tickets/TCK-001.md"
    wrong = "/lane/01/docs/tickets/TCK-001.md"
    assert expected in blob, (
        f"the transcript does not carry the ticket path with its offset. expected a path ending "
        f"{expected!r}; the pre-fix join would produce one ending {wrong!r}.\ntranscript: {blob[:1200]}")
    assert wrong not in blob, (
        f"the transcript carries the pre-fix path {wrong!r}, which is the document root joined to a "
        f"worktree that is a checkout of the git root.\ntranscript: {blob[:1200]}")


def test_the_path_the_refusal_names_is_the_path_the_tool_looked_at(nested):
    """An operator must be able to check the tool's claim with one `ls`.

    This is the assertion that would have caught the live case in one line: whatever
    `detail.path` says, its existence on disk agrees with the refusal. A refusal naming a path
    that IS there is the tool contradicting itself, and it is what sent the operator looking for a
    missing commit that did not exist.
    """
    top, sub, srv = nested
    lane = _claim(srv)
    r = _start_run(srv, lane)

    if r.status_code != 409:
        # The run was accepted. The claim the tool is making is that the ticket IS reachable, and
        # that claim is checkable too — round 1 returned here and asserted nothing in the state
        # the fix produces (QA finding 2), so the case was green against the defect both ways.
        doc = sub / "docs" / "tickets" / "TCK-001.md"
        assert doc.exists(), f"the fixture's own ticket is missing at {doc}"
        return

    detail = r.json().get("error", {}).get("detail", {})
    named = detail.get("path", "")
    assert named, f"a ticket_not_on_branch refusal named no path at all: {r.text}"
    assert not Path(named).exists(), (
        f"the tool refused with {r.json()['error']['code']!r} and named {named!r}, and that file "
        f"IS on disk. The refusal names a cause the operator can disprove with one `ls`.")


def test_a_ticket_genuinely_absent_from_the_branch_still_says_so(nested):
    """MANDATORY NEGATIVE — the refusal must survive.

    Without this arm the cheap fix for the criterion above is to stop checking, which turns a clear
    refusal into an agent that starts and fails on its first read. Watched RED against an
    implementation that skips the check: with the document removed from the lane's branch, the
    refusal is still `ticket_not_on_branch` and still names the path it looked at.
    """
    top, sub, srv = nested
    lane = _claim(srv)

    worktree = Path(json.loads(srv.get("/api/lanes").text)["lanes"][0].get("worktree", "")) \
        if srv.get("/api/lanes").status_code == 200 else None

    # remove the ticket from the lane's branch only — it stays on main
    doc = sub / "docs" / "tickets" / "TCK-001.md"
    rel = doc.relative_to(top)
    branch = f"ticket/TCK-001"
    if worktree and worktree.exists():
        git(worktree, "rm", "-q", str(rel))
        git(worktree, "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-m", "remove the ticket from this branch")

    r = _start_run(srv, lane)
    assert r.status_code == 409, (
        f"a ticket that is genuinely NOT on the lane's branch was accepted: {r.status_code} {r.text}")
    body = r.json()["error"]
    assert body["code"] == "ticket_not_on_branch", body
    assert body.get("detail", {}).get("path"), (
        f"the refusal does not name the path it looked at, so nobody can check it: {body}")


@pytest.mark.repository
def test_the_root_a_document_path_is_joined_to_is_not_a_parameter():
    """§3's structural change — the class is closed by making the wrong root UNPASSABLE.

    QA finding 4: the fix landed but nothing asserted a sole producer, so a new consumer could
    join a `docs/` path to the document root again and no gate would notice.

    The change is not "one function takes the root" — a parameter that can be wrong will be. It is
    that the root is not a parameter at all: `inLane` reads the manifest itself, and
    `ticketPathInLane`/`values` have no root argument for a caller to get wrong. This asserts the
    signatures stay that way, which is what the compiler then enforces at every call site.

    Stated floor: this reads declarations, so it cannot prove `inLane` picks the right root — the
    behavioural cases above do that. What it guards is the shape that lets a caller pass one.
    """
    server = Path(__file__).resolve().parents[1]
    header = (server / "app/api/prompt.h").read_text()
    impl = (server / "app/api/prompt.cpp").read_text()

    assert "repoRoot" not in header, (
        "app/api/prompt.h has a root parameter again. A caller that can pass a root can pass the "
        "document root, which is the join SRV-FIX-293 exists to make impossible.")

    assert "manifest::Store::instance().data()" in impl, (
        "app/api/prompt.cpp no longer reads the manifest itself, so the root must be coming from a "
        "caller — see SRV-FIX-293 §3.")

    for f in ("app/api/_start-run.cpp", "app/api/roles.cpp"):
        src = (server / f).read_text()
        assert "data().root, worktree" not in src and "data().root," not in src.replace(
            "data().root, worktree", ""), (
            f"{f} passes the document root into a path join again.")
