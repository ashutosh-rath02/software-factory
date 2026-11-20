"""QA probes for SRV-FIX-290 — written by the QA session, not the implementer.

Not a fix and not a deliverable: evidence. Each probe asks a question §1 asks that the
committed tests do not, or a question §1 never wrote down at all.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_lane import build_repo, git          # noqa: E402


def nested_repo(tmp_path):
    top = Path(tmp_path) / "top"
    sub = top / "sub"
    sub.mkdir(parents=True)
    build_repo(sub)
    shutil.rmtree(sub / ".git")
    (top / "README.md").write_text("the enclosing repository\n")
    git(top, "init", "-b", "main")
    git(top, "config", "user.email", "factory@test.local")
    git(top, "config", "user.name", "factory tests")
    git(top, "config", "commit.gpgsign", "false")
    git(top, "add", "-A")
    git(top, "commit", "-m", "initial")
    return top, sub


def test_probe_the_agent_actually_executes_not_merely_accepted(serve, tmp_path):
    """§1 criterion 1 says: 'the agent **executes** — assert its transcript, not the 201'.

    The committed test asserts only the status code of POST .../runs. This probe asserts the
    child process really ran inside the lane worktree, by making it leave a file behind.
    """
    top, sub = nested_repo(tmp_path)
    agent = sub / "agent.sh"
    agent.write_text(
        "#!/bin/sh\n"
        "echo 'ready' >&2\n"
        "pwd > QA_PROOF_CWD\n"
        "echo '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",'"
        "'\"text\":\"QA-290-EXECUTED\"}]}}'\n"
        "sleep 30\n")
    agent.chmod(0o755)
    manifest = json.loads((sub / ".factory" / "factory.json").read_text())
    manifest["agent"] = {"command": "/bin/sh sub/agent.sh"}
    (sub / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    git(top, "add", "-A")
    git(top, "commit", "-m", "an agent the fixture owns")

    srv = serve(sub)
    lane = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert lane.status_code == 201, lane.text
    name = lane.json()["lane"]
    worktree = Path(lane.json()["worktree"])

    started = srv.post(f"/api/lanes/{name}/runs", json={})
    assert started.status_code in (200, 201, 202), started.text
    run_id = started.json().get("run_id")

    proof = worktree / "QA_PROOF_CWD"
    transcript = sub / ".factory" / "runs" / str(run_id) / "transcript.jsonl"
    deadline = time.time() + 30
    entries = []
    while time.time() < deadline:
        if transcript.exists():
            entries = [l for l in transcript.read_text().splitlines() if l.strip()]
        if proof.exists() and entries:
            break
        time.sleep(0.1)

    print("PROBE run_id =", run_id)
    print("PROBE proof file exists =", proof.exists())
    if proof.exists():
        print("PROBE agent cwd =", proof.read_text().strip())
    print("PROBE transcript path =", transcript, "exists =", transcript.exists())
    print("PROBE transcript entries =", len(entries))
    for e in entries[:8]:
        print("PROBE  |", e[:200])

    assert proof.exists(), (
        "the agent process never ran: no file was written in the lane worktree "
        f"{worktree}. The run POST answered {started.status_code}.")
    assert entries, f"no transcript entries at {transcript}"


def test_probe_a_manifest_root_inside_a_linked_git_worktree(serve, tmp_path):
    """Beyond §1. A linked worktree's `.git` is a FILE, not a directory.

    Real shape: this very QA checkout. `git worktree add /home/dev/qa290` gives
    `/home/dev/qa290/.git` as a file, so `factory/` inside it has a git repository above it
    in every sense git understands — and the discovery walks straight past it.
    """
    top, sub = nested_repo(tmp_path)
    wt = Path(tmp_path) / "linked"
    git(top, "worktree", "add", "-b", "wt", str(wt))
    assert (wt / ".git").is_file(), "fixture wrong: a linked worktree should carry a .git FILE"
    # the manifest lives inside the linked worktree, exactly as factory/ lives inside a checkout
    inner = wt / "sub"
    print("PROBE linked worktree manifest root =", inner)
    print("PROBE git -C says toplevel =", subprocess.run(
        ["git", "-C", str(inner), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True).stdout.strip())

    srv = serve(inner)
    health = srv.get("/api/health")
    print("PROBE health repo =", json.dumps(health.json().get("repo")))
    lane = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    print("PROBE claim status =", lane.status_code, lane.text[:400])


def test_probe_discovery_has_no_ceiling(serve, tmp_path):
    """Beyond §1. How far up will it walk for a `.git` that has nothing to do with this manifest?

    Prints the git root chosen for a manifest root buried deep under a repository that was never
    meant to contain it.
    """
    top = Path(tmp_path) / "unrelated"
    top.mkdir()
    (top / "README.md").write_text("a repository about something else\n")
    git(top, "init", "-b", "main")
    git(top, "config", "user.email", "factory@test.local")
    git(top, "config", "user.name", "factory tests")
    git(top, "config", "commit.gpgsign", "false")
    git(top, "add", "-A")
    git(top, "commit", "-m", "initial")

    deep = top / "a" / "b" / "c" / "d" / "sub"
    deep.mkdir(parents=True)
    build_repo(deep)
    shutil.rmtree(deep / ".git")

    srv = serve(deep)
    repo = srv.get("/api/health").json()["repo"]
    print("PROBE document root =", repo["root"])
    print("PROBE git root      =", repo.get("git_root"))
    lane = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    print("PROBE claim status  =", lane.status_code, lane.text[:300])
