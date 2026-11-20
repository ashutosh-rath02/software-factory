"""SRV-FIX-292 — the git root is the repository GIT reports, not a directory named `.git`.

`SRV-FIX-290` discovered the git root by walking up from the document root and accepting a `.git`
**directory**. A linked worktree — anything made by `git worktree add` — carries a `.git` **FILE**
holding `gitdir: …`, so the walk stepped past it and kept climbing. Serving `factory/` from inside
such a checkout reported `git_root: ""` and refused every lane with `409 no_git_root`, while git
itself answered the opposite question twice:

    $ git -C /home/dev/qa290/factory rev-parse --show-toplevel
    /home/dev/qa290
    $ git -C /home/dev/qa290/factory rev-parse --git-common-dir
    /home/dev/software-factory/.git

    GET  /api/health   ->  {"repo":{…,"git_root":""}}
    POST /api/lanes    ->  409 no_git_root

This repository runs its tickets in worktrees by design, so that is every parallel session, every
QA session, and the factory's own lanes.

**The rule this ticket writes down** (§1's second arm, and `SPEC.md` §1 now says it): the walk has
the ceiling GIT has, which is none. The tool's git root is the repository git reports for the
document root — `git rev-parse --show-toplevel` — so a manifest buried under a repository that has
nothing to do with it is served, and `E1` reports both roots so the operator can see which is
which. The alternative rules were a depth limit and a `--git-root` override; both invent a second
answer to a question git already answers, and a person standing in that directory would get git's.
`test_the_git_root_walk_stops_where_the_ticket_says_it_stops` asserts the tool against git's own
answer rather than against a number written here.

What "is a repository" means is git's answer too, and it is what makes the refusal survive: a
`.git` DIRECTORY is one when it holds a `HEAD`, and a `.git` FILE is one when its `gitdir:` pointer
resolves to a directory that holds a `HEAD`. Everything else — absent, a stale pointer, an empty
directory wearing the name — is no repository, and is refused by name at claim time.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from test_lane import build_repo, git          # noqa: E402
from test_roots import claim, transcript_of    # noqa: E402


def git_says(where, *args):
    """What git itself answers in `where` — the arbiter every case here is measured against."""
    out = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True)
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def repo_at(root):
    """An ordinary repository with one commit. Not a manifest root — just a repository."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("the enclosing repository\n")
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "factory@test.local")
    git(root, "config", "user.name", "factory tests")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


def worktree_repo(tmp_path, *, agent=None):
    """A repository `R`, a LINKED worktree `W` cut from it, and a manifest at `W/sub`.

    The shape every parallel session in this repository actually uses, and the one no fixture had:
    `W/.git` is a FILE. `build_repo` runs `git init` at the tree it builds, so the tree is built at
    `W/sub` and that repository is then discarded — the enclosing one is `R`, reached through `W`.
    """
    top = repo_at(Path(tmp_path) / "R")
    wt = Path(tmp_path) / "W"
    git(top, "worktree", "add", "-b", "wt", str(wt))
    assert (wt / ".git").is_file(), (
        "the fixture is meant to force the file/directory difference and did not: "
        f"{wt / '.git'} is not a file")

    sub = wt / "sub"
    sub.mkdir(parents=True)
    build_repo(sub)
    shutil.rmtree(sub / ".git")

    if agent is not None:
        (sub / "agent.sh").write_text(agent)
        (sub / "agent.sh").chmod(0o755)
        manifest = json.loads((sub / ".factory" / "factory.json").read_text())
        # `sub/agent.sh`, not `agent.sh`: a lane worktree is a checkout of the repository, so the
        # child's working directory is the worktree root and the script is one level down.
        # SRV-FIX-293 is the ticket that owns that offset; this ticket lives with it.
        manifest["agent"] = {"command": "/bin/sh sub/agent.sh"}
        (sub / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))

    git(wt, "add", "-A")
    git(wt, "commit", "-m", "a manifest inside a linked worktree")
    return top, wt, sub


AGENT = ("#!/bin/sh\n"
         "echo 'ready' >&2\n"
         "pwd > EXECUTED_HERE\n"
         "echo '{\"type\":\"assistant\",\"message\":{\"content\":"
         "[{\"type\":\"text\",\"text\":\"WORKTREE-292-EXECUTED\"}]}}'\n"
         "sleep 30\n")


def test_a_lane_runs_when_the_document_root_is_inside_a_linked_worktree(serve, tmp_path):
    """§1 criterion 1 — the whole ticket, on the checkout shape this repository actually uses.

    The assertion is the agent's TRANSCRIPT, never the `201`: `SRV-FIX-290` round 1 asserted a
    status code on this same journey and was green while every agent it spawned died at exec. So
    two consequences the endpoint cannot fake — a file the agent's own process wrote inside the
    lane worktree, and the text it printed arriving on the transcript.

    RED before the fix: `409 no_git_root` at claim, three steps earlier than any of this.
    """
    top, wt, sub = worktree_repo(tmp_path, agent=AGENT)
    srv = serve(sub)

    lane = claim(srv)
    assert lane.status_code == 201, (
        f"a lane was refused inside a linked worktree, which git calls a repository: {lane.text}")
    name = lane.json()["lane"]

    started = srv.post(f"/api/lanes/{name}/runs", json={})
    assert started.status_code in (200, 201, 202), (
        f"the run was refused inside a linked worktree: {started.text}")
    run_id = started.json()["run_id"]

    worktree = Path(lane.json()["worktree"])
    proof = worktree / "EXECUTED_HERE"
    entries = []
    deadline = time.time() + 25
    while time.time() < deadline:
        entries = transcript_of(sub, run_id)
        if proof.exists() and any("WORKTREE-292-EXECUTED" in json.dumps(e) for e in entries):
            break
        time.sleep(0.25)

    assert proof.exists(), (
        f"the agent process never ran: it wrote no file in the lane worktree {worktree}. "
        f"The run POST answered {started.status_code}. transcript={entries}")
    assert any("WORKTREE-292-EXECUTED" in json.dumps(e) for e in entries), (
        f"the agent ran but nothing it said reached the transcript: {entries}")


def test_health_reports_the_worktrees_own_root_not_the_repository_it_was_cut_from(serve, tmp_path):
    """§1 criterion 2 — WHICH root, where two candidates both have a claim.

    A linked worktree has two repositories a walk could name: the checkout it stands in (`W`) and
    the repository holding its objects and refs (`R`, which `--git-common-dir` points inside). They
    are different directories here, deliberately, because a fixture where they coincide passes
    whichever one the code reads.

    It is `W`, and the reason is asserted rather than described: `W` is the checkout `git worktree
    add` must run in and the tree confinement must bind, while `R` is a repository this document
    root is not checked out of — its working tree holds a different commit and none of these
    documents. `E1` reports both `root` and `git_root` so an operator can see the answer.
    """
    top, wt, sub = worktree_repo(tmp_path)

    rc, toplevel, err = git_says(sub, "rev-parse", "--show-toplevel")
    assert rc == 0, err
    rc, common, err = git_says(sub, "rev-parse", "--git-common-dir")
    assert rc == 0, err
    common = Path(common if os.path.isabs(common) else os.path.join(sub, common)).resolve()

    srv = serve(sub)
    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    repo = health.json()["repo"]

    assert repo["root"] == str(sub), repo
    assert repo["git_root"] == str(wt), (
        f"the git root of a document root inside a linked worktree is the worktree: {repo}")
    assert repo["git_root"] == toplevel, (
        f"the tool disagrees with git about which repository encloses {sub}: "
        f"tool={repo['git_root']!r} git rev-parse --show-toplevel={toplevel!r}")
    assert repo["git_root"] != str(common.parent), (
        "the git root is the checkout the documents are in, not the repository the worktree was "
        f"cut from: `git worktree add` and confinement act on {wt}, while {common} only holds the "
        f"objects and refs. tool={repo['git_root']!r} --git-common-dir={str(common)!r}")


def test_a_root_with_no_repository_above_it_is_still_refused_by_name(serve, tmp_path):
    """§1 criterion 3 — **mandatory negative**. `SRV-FIX-290`'s refusal does not weaken.

    "No repository above it" is a negation, and §3 of this ticket is about a negation that reads
    as exhaustive while enumerating nothing. So its members are listed and every one is driven:

      1. `.git` absent entirely — nothing above the root at all;
      2. `.git` a FILE whose `gitdir:` names nothing that exists — a stale worktree pointer, the
         member this ticket's own fix makes reachable for the first time;
      3. `.git` a DIRECTORY that is not a repository — empty, no `HEAD`, the name and nothing else.

    Each must answer `409 no_git_root` at CLAIM time and name the root it searched from. Before the
    fix, member 3 answered `409 worktree_failed` carrying git's own "not a git repository" from
    three steps later, which is the symptom and not the cause (`SRV-FIX-109`).
    """
    members = {}

    absent = Path(tmp_path) / "absent" / "sub"
    absent.mkdir(parents=True)
    build_repo(absent)
    shutil.rmtree(absent / ".git")
    members["`.git` absent"] = absent

    stale = Path(tmp_path) / "stale" / "sub"
    stale.mkdir(parents=True)
    build_repo(stale)
    shutil.rmtree(stale / ".git")
    (stale.parent / ".git").write_text(
        f"gitdir: {tmp_path}/nowhere/.git/worktrees/gone\n")
    members["`.git` a file pointing nowhere"] = stale

    hollow = Path(tmp_path) / "hollow" / "sub"
    hollow.mkdir(parents=True)
    build_repo(hollow)
    shutil.rmtree(hollow / ".git")
    (hollow.parent / ".git").mkdir()
    members["`.git` a directory that is not a repository"] = hollow

    for what, root in members.items():
        rc, _, _ = git_says(root, "rev-parse", "--show-toplevel")
        assert rc != 0, f"the fixture for {what} is a repository after all, so it proves nothing"

        srv = serve(root)
        lane = claim(srv)
        assert lane.status_code == 409, (
            f"a lane was handed out where {what}: {lane.status_code} {lane.text}")
        body = lane.json()
        assert body["error"]["code"] == "no_git_root", (
            f"where {what}, the refusal is not named for its cause: {body}")
        assert str(root) in json.dumps(body), (
            f"where {what}, the refusal does not name the root it searched from: {body}")
        serve.stop(srv)


def test_the_git_root_walk_stops_where_the_ticket_says_it_stops(serve, tmp_path):
    """§1 criterion 4 — the second arm, and the rule is written down rather than left silent.

    **The walk has git's ceiling, which is none.** A manifest root buried five levels under a
    repository that has nothing to do with it IS served, and a lane IS handed out there — that is
    intended, and the reason is that git answers the same: a person standing in that directory gets
    that repository from `git status`, from `git worktree add`, and from every commit the agent
    makes. A depth limit or a `--git-root` override would be a second answer to a question that
    already has one, and the operator chose the directory. `E1` reports both roots (criterion 2) so
    the choice is visible.

    So this asserts the tool AGAINST GIT rather than against a number: the same answer at five
    levels deep, and the same answer — none — where git has none. A depth-limited walk fails the
    first; a walk that invents a root where git refuses fails the second.
    """
    top = repo_at(Path(tmp_path) / "unrelated")
    deep = top / "a" / "b" / "c" / "d" / "sub"
    deep.mkdir(parents=True)
    build_repo(deep)
    shutil.rmtree(deep / ".git")
    git(top, "add", "-A")
    git(top, "commit", "-m", "a manifest that arrived from somewhere else")

    rc, toplevel, err = git_says(deep, "rev-parse", "--show-toplevel")
    assert rc == 0, err

    srv = serve(deep)
    repo = srv.get("/api/health").json()["repo"]
    assert repo["git_root"] == toplevel == str(top), (
        f"the tool stopped somewhere git does not: tool={repo['git_root']!r} git={toplevel!r}")

    lane = claim(srv)
    assert lane.status_code == 201, (
        "the decided rule is git's own unbounded walk, so this claim is intended: "
        f"{lane.status_code} {lane.text}")
    serve.stop(srv)

    orphan = Path(tmp_path) / "orphan" / "sub"
    orphan.mkdir(parents=True)
    build_repo(orphan)
    shutil.rmtree(orphan / ".git")
    rc, _, _ = git_says(orphan, "rev-parse", "--show-toplevel")
    assert rc != 0, "the fixture is inside a repository, so it cannot show where the walk ends"

    srv = serve(orphan)
    repo = srv.get("/api/health").json()["repo"]
    assert repo["git_root"] == "", (
        f"the tool found a repository where git finds none: {repo}")


# ── SRV-FIX-532 cluster E2 — the suite's two spellings of the root become one ─────────────────
#
# `SRV-FIX-495`. `conftest.py:REPO_ROOT` is `SERVER_DIR.parents[1]` — the directory ABOVE the
# checkout — while the comment one line above it says *"the repository this checkout is"*. From a
# developer's checkout that is the monorepo, which has a manifest, so every case works and every
# case is about the repository this one is CLONED INTO. From a lane it is `.factory/worktrees/lane`,
# which is not a repository and holds no documents, and 41 cases fail for a reason that is about
# where they ran.
#
# The door is asked of the REAL modules, imported from the tree under test, rather than of a copy
# of the derivation: a copy carrying the fix would grade the copy.

DOORS = """
import sys
sys.path.insert(0, "tests")
import conftest, test_catalog
print("conftest.REPO_ROOT", conftest.REPO_ROOT)
print("test_catalog._repo_root", test_catalog._repo_root())
print("test_catalog._this_repository", test_catalog._this_repository())
"""


def doors_answered_in(checkout):
    """`{door: the tree it names}`, from the suite's own modules as they sit in `checkout`."""
    out = subprocess.run([sys.executable, "-c", DOORS], cwd=str(Path(checkout) / "server"),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, (
        f"the suite's own doors could not be imported in {checkout}:\n{out.stdout}\n{out.stderr}")
    answers = {}
    for line in out.stdout.splitlines():
        name, _, where = line.partition(" ")
        if name:
            answers[name] = where.strip()
    assert len(answers) == 3, f"three doors were asked and {len(answers)} answered: {out.stdout}"
    return answers


def test_the_suite_reads_the_same_tree_from_a_lane_and_from_a_checkout(tmp_path):
    """§1 E2 — every door the suite uses names ITS OWN checkout, in both places.

    `SRV-FIX-495` criterion 1. A clone of this repository and a LINKED WORKTREE of that clone —
    which is what a lane is, `git worktree add`, a `.git` FILE and not a directory. The same three
    doors are asked in both, by importing the real modules out of each tree.

    The two checkouts are DIFFERENT DIRECTORIES on purpose: a fixture where they coincide passes
    whichever one the code reads. And the answers are compared as paths RELATIVE to their own
    checkout, because the absolute answers must differ — that is the whole point — while the tree
    each names must be the one it is part of.

    RED before the fix: `conftest.REPO_ROOT` answers the directory ABOVE each checkout, so the
    worktree's answer is the clone's parent and the two doors in one file disagree with each other.
    """
    primary = Path(tmp_path) / "primary"
    here = Path(__file__).resolve().parents[2]
    made = subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", "--shared",
                           str(here), str(primary)],
                          capture_output=True, text=True, timeout=900)
    assert made.returncode == 0, made.stderr[-2000:]

    linked = Path(tmp_path) / "linked"
    cut = subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(linked)],
                         cwd=str(primary), capture_output=True, text=True, timeout=300)
    assert cut.returncode == 0, cut.stderr[-2000:]
    assert (linked / ".git").is_file(), (
        f"{linked / '.git'} is not a FILE, so this fixture does not carry the shape a lane has")
    assert primary != linked

    from_checkout = doors_answered_in(primary)
    from_lane = doors_answered_in(linked)

    for door in sorted(from_checkout):
        mine = Path(from_checkout[door])
        theirs = Path(from_lane[door])
        assert mine == primary, (
            f"{door} in the primary checkout names {mine}, which is not the checkout it is part "
            f"of ({primary}). Every case behind it is about a tree this suite is not in.")
        assert theirs == linked, (
            f"{door} in a linked worktree names {theirs}, which is not the checkout it is part "
            f"of ({linked}). That is the lane case: 41 cases fail for a reason that is about "
            f"where they ran.\ncheckout said {mine}, worktree said {theirs}")

    assert len(set(from_checkout.values())) == 1 and len(set(from_lane.values())) == 1, (
        f"the doors in one file disagree with each other:\ncheckout {from_checkout}\n"
        f"lane {from_lane}")
