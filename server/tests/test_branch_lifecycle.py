"""SRV-FIX-427 — the rule that keeps a branch, and the counterpart that ends it.

A release keeps the branch, always, and that is right: at release time nobody can know whether the
work landed. What the workflow never had is the other half — nothing says when a kept branch stops
needing keeping. Measured on this repository on 2026-09-06: 37 `ticket/*` branches, 27 worktrees,
zero lanes, and 24 of the branches provably landed.

**A squash is why this needs a rule rather than an eye.** After `git merge --squash` the branch's
own commits are not ancestors of the base, so `git branch -d` refuses them and
`rev-list base..branch` is never empty: both of git's own safety answers say *unmerged* about work
that landed. The tool has to answer from the TICKET and the squashed subject instead, which is the
traceability rule this repository already has — `<ID>: …` is the commit subject.
"""

import json
import subprocess
from pathlib import Path

import pytest

SWEEP = Path(__file__).resolve().parents[2] / ".claude" / "scripts" / "landed.sh"


def git(root, *args):
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"git {' '.join(args)} → {out.stderr}"
    return out.stdout.strip()


def doc(front):
    return "\n".join(["---"] + [f"{k}: {v}" for k, v in front.items()] + ["---", "", "Body.", ""])


@pytest.fixture
def repo_with_branches(tmp_path):
    """One repository, three branches, three different truths about them."""
    root = tmp_path / "repo"
    (root / "docs" / "tickets").mkdir(parents=True)
    git(root.parent, "init", "-q", "-b", "main", str(root))
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")

    def ticket(tid, status):
        (root / "docs" / "tickets" / f"{tid}-x.md").write_text(
            doc({"id": tid, "workspace": "alpha", "title": f"Ticket {tid}", "status": status}))

    ticket("TKT-1", "pass")      # landed: squashed onto main
    ticket("TKT-2", "pass")      # passed but NEVER merged — the live trap
    ticket("TKT-3", "todo")      # still open
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "the repository")

    for tid in ("TKT-1", "TKT-2", "TKT-3"):
        git(root, "checkout", "-q", "-b", f"ticket/{tid}")
        (root / f"{tid}.txt").write_text(f"work for {tid}\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", f"{tid}: the work")
        git(root, "checkout", "-q", "main")

    # TKT-1 lands the way this repository lands things: SQUASHED, so its commits are not ancestors
    git(root, "merge", "-q", "--squash", "ticket/TKT-1")
    git(root, "commit", "-q", "-m", "TKT-1: Ticket TKT-1")
    return root


def sweep(root):
    out = subprocess.run(["bash", str(SWEEP), "--json", "--base", "main"],
                         cwd=str(root), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"{SWEEP} → {out.returncode}\n{out.stderr}"
    return json.loads(out.stdout)


def test_a_squashed_branch_counts_as_landed(repo_with_branches):
    """Criterion 3 — the decision comes from the ticket, not from git's mergedness.

    This is the case git gets wrong in both directions, so it is asserted before the others: if the
    classifier believed `git branch -d`, it would keep every branch this repository ever merged.
    """
    root = repo_with_branches
    refused = subprocess.run(["git", "-C", str(root), "branch", "-d", "ticket/TKT-1"],
                             capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0, (
        "this case is meaningless unless git itself refuses the branch — it did not, so the merge "
        "was not a squash and the trap being tested is absent")
    assert git(root, "rev-list", "--count", "main..ticket/TKT-1") != "0"

    landed = {b["branch"] for b in sweep(root)["landed"]}
    assert "ticket/TKT-1" in landed, (
        "a squashed, landed branch was not recognised: git says unmerged about work that is on main")


def test_a_landed_ticket_leaves_nothing_behind(repo_with_branches):
    """Criterion 1 — what has landed is offered for removal, and removal actually removes it."""
    root = repo_with_branches
    assert "ticket/TKT-1" in {b["branch"] for b in sweep(root)["landed"]}

    out = subprocess.run(["bash", str(SWEEP), "--remove", "--base", "main"],
                         cwd=str(root), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    remaining = git(root, "branch", "--list", "ticket/*")
    assert "ticket/TKT-1" not in remaining, f"the landed branch survived a removal: {remaining}"


def test_an_unlanded_branch_is_never_removed(repo_with_branches):
    """MANDATORY NEGATIVE — criterion 2, and the trap this repository is holding right now.

    `TKT-2` says `pass` and was never merged, which is exactly `ticket/SRV-FEAT-001`'s state. A
    sweep that trusted the ticket alone would delete work no other ref carries.
    """
    root = repo_with_branches
    answer = sweep(root)
    landed = {b["branch"] for b in answer["landed"]}
    kept = {b["branch"]: b["reason"] for b in answer["kept"]}

    assert "ticket/TKT-2" not in landed, "a passed ticket that never merged was called landed"
    assert "ticket/TKT-3" not in landed, "an open ticket's branch was called landed"
    assert "ticket/TKT-2" in kept and "ticket/TKT-3" in kept, kept

    subprocess.run(["bash", str(SWEEP), "--remove", "--base", "main"],
                   cwd=str(root), capture_output=True, text=True, timeout=60)
    remaining = git(root, "branch", "--list", "ticket/*")
    for survivor in ("ticket/TKT-2", "ticket/TKT-3"):
        assert survivor in remaining, f"{survivor} was removed with work no other ref carries"


def test_the_sweep_is_not_limited_to_one_branch_prefix(repo_with_branches):
    """The set of branches is DERIVED, not a prefix somebody typed.

    The first version of this swept `ticket/*` only, and the operator's repository still held
    `squash/SRV-FIX-306`, `squash/SRV-FIX-344` and `qa-merge-SRV-FIX-378` afterwards — branches
    agents make during a round, carrying the same landed work under a different prefix. A sweep is
    only as good as the set it walks, and a prefix is a guess about what the tool will be called
    upon to clean.
    """
    root = repo_with_branches
    git(root, "branch", "squash/TKT-1")        # landed work, different prefix
    git(root, "branch", "qa-merge-TKT-2")      # NOT landed, different prefix
    git(root, "branch", "backup/whatever")     # no ticket id at all

    answer = sweep(root)
    landed = {b["branch"] for b in answer["landed"]}
    kept = {b["branch"]: b["reason"] for b in answer["kept"]}

    assert "squash/TKT-1" in landed, (
        f"a landed branch was invisible because of its prefix: {sorted(landed)}")
    assert "qa-merge-TKT-2" not in landed, "an unlanded branch was swept in on its id alone"
    assert "backup/whatever" in kept, "a branch naming no ticket must be kept, not guessed about"
    assert "no ticket id" in kept["backup/whatever"], kept["backup/whatever"]
    assert "main" not in landed and "main" not in kept, "the base branch is not a candidate"
