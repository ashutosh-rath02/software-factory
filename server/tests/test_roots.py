"""SRV-FIX-290 — the DOCUMENT root and the GIT root are two different things.

The factory is *given* a repository (SPEC.md §1) and reads its manifest from
`<root>/.factory/factory.json`. Confinement then binds `<root>/.git`, because until this ticket the
two roots were assumed to be one directory. They are not, and this repository is the proof: its own
manifest lives at `factory/.factory/factory.json` while its git directory is one level up, so the
instance that is supposed to run `FAC-*` tickets claims lanes happily and refuses every run:

    POST /api/lanes            -> 201  lane/01  ticket/SRV-FIX-280
    POST /api/lanes/lane/01/runs -> 409 confinement_unavailable
                                       "no git directory at .../factory/.git"

Two of the three verbs in `CLAUDE.md`'s *"read, claimed and run"* worked, which is why every `FAC-*`
ticket this repository has ever run was run OUTSIDE the tool, as a detached process the rail cannot
show, stop or release.

The decision this ticket takes, of the three §1 wrote down: **the git root is DISCOVERED, not
assumed.** The document root stays exactly what the operator passed; the git root is found from it.
Every existing invocation keeps working, because for a repository whose manifest is at its own git
root the two are the same directory.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from test_lane import build_repo, git          # noqa: E402


def nested_repo(tmp_path, *, git_root_has_manifest=False):
    """A git repository at `R`, whose manifest and docs live at `R/sub`.

    This is this repository's own shape — `factory/` inside the monorepo — and it is the shape no
    fixture had, which is exactly why nothing caught it. `build_repo` runs `git init` at the root it
    builds, so the tree is built at `R/sub` and git is initialised one level up.
    """
    top = Path(tmp_path) / "top"
    sub = top / "sub"
    sub.mkdir(parents=True)
    build_repo(sub)                      # manifest, docs, tickets, and a git repo AT sub
    for junk in (".git",):               # …whose git dir we now discard: git belongs at `top`
        p = sub / junk
        if p.is_dir():
            import shutil
            shutil.rmtree(p)
    (top / "README.md").write_text("the enclosing repository\n")
    git(top, "init", "-b", "main")
    git(top, "config", "user.email", "factory@test.local")
    git(top, "config", "user.name", "factory tests")
    git(top, "config", "commit.gpgsign", "false")
    git(top, "add", "-A")
    git(top, "commit", "-m", "initial")
    return top, sub


def orphan_root(tmp_path):
    """A manifest root with no `.git` anywhere above it. Not a repository at all."""
    sub = Path(tmp_path) / "orphan" / "sub"
    sub.mkdir(parents=True)
    build_repo(sub)
    import shutil
    shutil.rmtree(sub / ".git")
    return sub


def claim(srv, ticket="TCK-001", workspace="core"):
    return srv.post("/api/lanes", json={"ticket_id": ticket, "workspace": workspace})


def transcript_of(root, run_id):
    """Every entry the run wrote, read off disk — not from the endpoint the claim went through."""
    p = Path(root) / ".factory" / "runs" / run_id / "transcript.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_a_run_starts_when_the_manifest_root_is_below_the_git_root(serve, tmp_path):
    """§1 criterion 1 — the whole ticket.

    ROUND 2. Round 1's version of this case asserted the STATUS CODE of `POST …/runs` and nothing
    else, and it was green while the agent never ran: the fixture wrote `agent.sh` into `R/sub` and
    declared `agent.command = "/bin/sh agent.sh"`, but a lane worktree is a checkout of the
    repository at `R`, so the child's cwd is the worktree root and the script is at `sub/agent.sh`
    under it. QA's probe on that exact fixture:

        PROBE proof file exists = False
        PROBE transcript entries = 1
        PROBE  | {"seq":1,…,"kind":"error","raw":true,
                  "text":"/usr/bin/dash: 0: cannot open agent.sh: No such file"}

    §1 says it in as many words — *"the agent **executes** — assert its transcript, not the
    `201`"* — and round 1's own docstring cited `SRV-FIX-137` for the reason, which is that every
    agent that gate ever spawned died at exec and was recorded as a crashed run, so a gate that
    never once ran an agent read green. Round 1 wrote the sentence and asserted the `201` anyway.

    So this asserts TWO consequences the endpoint cannot fake: a file the agent's own process wrote
    into the lane worktree, and the text it emitted arriving on the transcript.
    """
    top, sub = nested_repo(tmp_path)
    agent = sub / "agent.sh"
    agent.write_text(
        "#!/bin/sh\n"
        "echo 'ready' >&2\n"
        "pwd > EXECUTED_HERE\n"
        "echo '{\"type\":\"assistant\",\"message\":{\"content\":"
        "[{\"type\":\"text\",\"text\":\"ROOT-290-EXECUTED\"}]}}'\n"
        "sleep 30\n")
    agent.chmod(0o755)
    manifest = json.loads((sub / ".factory" / "factory.json").read_text())
    # `sub/agent.sh`, not `agent.sh`: the lane worktree is a checkout of the repository at `top`,
    # so the child's working directory is the worktree root and the script is one level down.
    manifest["agent"] = {"command": "/bin/sh sub/agent.sh"}
    (sub / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    git(top, "add", "-A")
    git(top, "commit", "-m", "an agent the fixture owns")

    srv = serve(sub)

    lane = claim(srv)
    assert lane.status_code == 201, lane.text
    name = lane.json()["lane"]

    started = srv.post(f"/api/lanes/{name}/runs", json={})
    assert started.status_code in (200, 201, 202), (
        f"the run was refused on a repository whose git root is one level up: {started.text}")
    run_id = started.json()["run_id"]

    worktree = Path(sub) / ".factory" / "worktrees" / name
    proof = worktree / "EXECUTED_HERE"
    for _ in range(80):
        if proof.exists():
            break
        time.sleep(0.25)

    assert proof.exists(), (
        f"the agent process never ran: it wrote no file in the lane worktree {worktree}. "
        f"The run POST answered {started.status_code}. "
        f"transcript={transcript_of(sub, run_id)}")

    entries = []
    for _ in range(80):
        entries = transcript_of(sub, run_id)
        if any("ROOT-290-EXECUTED" in json.dumps(e) for e in entries):
            break
        time.sleep(0.25)

    assert any("ROOT-290-EXECUTED" in json.dumps(e) for e in entries), (
        f"the agent ran but nothing it said reached the transcript: {entries}")


def test_a_lane_is_refused_when_no_git_root_encloses_the_manifest_root(serve, tmp_path):
    """§1 criterion 2 — **mandatory negative**.

    **What the current build already gets right, said plainly so this arm is not credited with more
    than it does:** the claim is ALREADY refused, with `409`. What it is not is *named* — the code
    is `worktree_failed` carrying git's own `"not a git repository"`, which is the symptom reported
    from the place that tripped over it, three steps after the fact. `SRV-FIX-109` is in this
    repository for exactly that distinction, *a cause per name*: an unreadable lane root used to
    reach a person as `worktree_failed` too.

    So this arm asserts the CAUSE is named and that the message carries the root it searched from —
    red today with `worktree_failed`. It also guards the fix from the direction that would undo it:
    a discovery that walks up for ever eventually finds SOME `.git`, belonging to a repository that
    has nothing to do with this manifest. Refusing outright, by name, is the honest answer.
    """
    sub = orphan_root(tmp_path)
    srv = serve(sub)

    lane = claim(srv)
    assert lane.status_code == 409, (
        f"a lane was handed out on a root with no git repository above it: {lane.text}")

    body = lane.json()
    assert body["error"]["code"] == "no_git_root", body
    assert str(sub) in json.dumps(body), (
        f"the refusal does not name the root it searched from: {body}")


def test_the_document_root_and_the_git_root_are_reported_separately(serve, tmp_path):
    """§1 criterion 3 — one `root` field is what made this ambiguous.

    `E1` said `repo.root` and an operator reading it could not tell whether the tool had a git
    repository at all. Both are named now, and on the nested shape they DIFFER — which is the whole
    point, and is why this asserts inequality rather than merely that a key exists.
    """
    top, sub = nested_repo(tmp_path)
    srv = serve(sub)

    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    repo = health.json()["repo"]

    assert repo["root"] == str(sub), repo
    assert repo["git_root"] == str(top), repo
    assert repo["git_root"] != repo["root"], (
        "the fixture is meant to force the two roots apart and did not")
