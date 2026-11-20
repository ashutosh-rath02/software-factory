"""SRV-FIX-470 — one commit per ticket reaches `main`, and something other than a sentence says so.

`CLAUDE.md`, `.claude/skills/qa-ticket/SKILL.md` and `server/design/land.md` all carry the rule:
one commit per ticket reaches `main` — a squash, never a merge commit and never the branch's own
history. On 2026-09-08 `WEB-FIX-370`'s QA lane read the skill, wrote *"merged to `main`"*, and ran

    git merge origin/main --no-edit
    git merge-base --is-ancestor origin/main HEAD && git push origin HEAD:main

which fast-forwarded the remote to a branch head carrying nine commits and a merge. The
`--is-ancestor` check answers *can this push succeed*; nothing anywhere answered *is this the
shape `main` takes*. Three other tickets landed correctly the same day, which is what "the rule
holds when a session chooses to follow it" means.

The rule is DERIVED here, never typed. There is no list of tickets, no list of exempt commits and
no list of directories — one category, `docs/`, stated with its reason:

  * a DOCUMENT COMMIT touches only paths under `docs/` — a filing, a §1 edit, an epic roll-up, a
    QA verdict or a correction to one. A ticket may have as many as its round needed.
  * a LANDING touches at least one path outside `docs/`. A ticket has at most one.
  * and `main` carries no merge commit at all.

    cd server && pytest tests/test_main_history.py -v
"""

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.repository

# The ratchet. History AT OR BEFORE it is not judged: `origin/main` is shared and rewriting it is
# a decision the operator has not made, so the violation stays visible in the log and this gate
# starts after it. `d33d4286` is where the ratchet WOULD be if the rule had always been enforced;
# it is kept below as the mandatory negative's ratchet, because the whole of the evidence that
# these predicates work is that they refuse the history between the two.
# SRV-FIX-532 cluster B1 (folds SRV-FIX-527). The ratchet moves from `efefdc60` to `7cd9a3a2`,
# which is the landing immediately after `02273871` — an operator commit that changed
# `.factory/prompts/qa-ticket.md` with no ticket id in its subject, made on 2026-09-08, before
# this rule was enforced on `main`. It is not rewritten: rewriting shared history is the thing
# the rule exists to forbid, so the floor moves past it instead and the range this move stops
# examining is `efefdc60..7cd9a3a2` — two commits, `f9bc32b2` (a docs-only filing, which the rule
# never judged) and `02273871` itself.
RATCHET = "7cd9a3a2"        # SRV-FEAT-002's landing, the first after the operator commit above.
BEFORE_THE_VIOLATION = "d33d4286"

# A wider, older floor for the document-commit case. `RATCHET..main` holds too little to say
# anything about document commits with — a claim about a category asserted over a range holding
# no member of it is a claim about nothing.
AUDIT_FLOOR = "8beafaac"    # `SRV-FEAT-437: a PASS verdict lands its branch`, 2026-09-07

TICKET_ID = re.compile(r"^([A-Z]+-[A-Z]+-[0-9]+)")

DOCUMENT_ROOT = "docs/"


def git(*args):
    out = subprocess.run(["git", *args], cwd=FACTORY, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"`git {' '.join(args)}` failed:\n{out.stderr}"
    return out.stdout


def base():
    """`main` as this checkout holds it, asserted to exist before anything is read off it."""
    out = subprocess.run(["git", "rev-parse", "--verify", "main"], cwd=FACTORY,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, (
        "there is no `main` in this checkout, so nothing here judged the history it is about. "
        f"git said: {out.stderr.strip()}")
    return "main"


@dataclass
class Commit:
    sha: str
    subject: str
    ticket: str | None
    files: list

    @property
    def non_docs(self):
        return [f for f in self.files if not f.startswith(DOCUMENT_ROOT)]

    @property
    def is_landing(self):
        return bool(self.non_docs)

    def __str__(self):
        return f"{self.sha[:8]} {self.ticket or '(no ticket id)'} non-docs-files={len(self.non_docs)}"


def sweep(ratchet):
    """Every non-merge commit in `<ratchet>..main`, with the files it touched.

    One `git log`, not one `git show` per commit: the population and the paths come out of the
    same walk, so there is no second query that could disagree with the first.
    """
    raw = git("log", "--no-merges", "--format=%x1e%H%x1f%s", "--name-only",
              f"{ratchet}..{base()}")
    out = []
    for chunk in raw.split("\x1e"):
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        sha, _, subject = header.partition("\x1f")
        found = TICKET_ID.match(subject.strip())
        out.append(Commit(sha=sha.strip(), subject=subject.strip(),
                          ticket=found.group(1) if found else None,
                          files=[line for line in body.splitlines() if line.strip()]))
    return out


def merges(ratchet):
    return [sha for sha in git("rev-list", "--merges", f"{ratchet}..{base()}").split() if sha]


def landings(ratchet):
    """ticket id (or `None`) -> the commits under it that touch a path outside `docs/`."""
    grouped = defaultdict(list)
    for commit in sweep(ratchet):
        if commit.is_landing:
            grouped[commit.ticket].append(commit)
    return grouped


def one_landing_findings(ratchet):
    """PREDICATE 1. Every way `<ratchet>..main` can break *one commit per ticket*."""
    findings = []
    for ticket, commits in sorted(landings(ratchet).items(), key=lambda kv: kv[0] or ""):
        if ticket is None:
            # PRE-DECIDED (§1): a commit whose subject carries no ticket id is REPORTED, never
            # exempted. The operator's own commits follow the same rule, and an exemption list
            # for `integrate:` / `merge:` / `model:` is the typed set §2 forbids.
            for commit in commits:
                findings.append(
                    f"{commit.sha[:8]} touches {len(commit.non_docs)} path(s) outside "
                    f"`{DOCUMENT_ROOT}` and its subject carries no ticket id: "
                    f"{commit.subject[:70]!r}")
            continue
        if len(commits) > 1:
            findings.append(
                f"{ticket} landed {len(commits)} times: "
                + ", ".join(str(c) for c in commits))
    return findings


def merge_findings(ratchet):
    """PREDICATE 2. `main` carries no merge commit after the ratchet."""
    return [f"{sha[:8]} {git('log', '-1', '--format=%s', sha).strip()[:70]}"
            for sha in merges(ratchet)]


# ── criterion 1 ──────────────────────────────────────────────────────────────────────────────

def test_a_ticket_lands_on_main_once():
    """§1 criterion 1 — over `<ratchet>..main`, no ticket id has more than one commit touching a
    path outside `docs/`, and no such commit is unattributed."""
    examined = sweep(RATCHET)
    assert examined, (
        f"ratchet has no successors — move it or wait. `{RATCHET}..main` is empty, so this case "
        f"examined nothing and passed for the wrong reason.")

    findings = one_landing_findings(RATCHET)
    assert not findings, (
        f"`main` gained more than one commit for a ticket after {RATCHET}. One commit per ticket "
        f"reaches `main` — `git merge --squash`, never the branch's own history — because "
        f"`git log --oneline main` is read to answer *what changed and why*, one line per "
        f"ticket:\n  " + "\n  ".join(findings)
        + f"\n  (examined {len(examined)} commit(s) in {RATCHET}..main)")


# ── criterion 2 ──────────────────────────────────────────────────────────────────────────────

def test_main_carries_no_merge_commit_after_the_ratchet():
    """§1 criterion 2 — `git rev-list --merges <ratchet>..main` is empty."""
    assert sweep(RATCHET), (
        f"ratchet has no successors — move it or wait. `{RATCHET}..main` is empty.")

    findings = merge_findings(RATCHET)
    assert not findings, (
        f"`main` carries a merge commit after {RATCHET}. A branch is landed by a squash, so a "
        f"merge commit on `main` is a branch's own history arriving whole — and it is a shape "
        f"`test_authorship.py`'s `--no-merges` derivation was never written for:\n  "
        + "\n  ".join(findings))


# ── criterion 3 ──────────────────────────────────────────────────────────────────────────────

DOCUMENT_COMMITS = {
    "d33d4286": ("FAC-EPIC-373", "an epic roll-up"),
    "b86081fa": ("WEB-FEAT-365", "a correction to a QA verdict"),
}


def test_a_document_commit_is_not_a_landing():
    """§1 criterion 3 — a commit touching only `docs/` does not count toward criterion 1.

    Asserted over `AUDIT_FLOOR..main` rather than `RATCHET..main`, because the ratchet range
    holds no document commit and a rule about a category cannot be asserted where the category is
    empty. Both commits are asserted PRESENT first: a classification of a commit that is not in
    the range is a classification of nothing.
    """
    walked = sweep(AUDIT_FLOOR)
    assert walked, f"ratchet has no successors — move it or wait. `{AUDIT_FLOOR}..main` is empty."

    for short, (ticket, what) in DOCUMENT_COMMITS.items():
        # matched against the FULL sha rather than keyed by an abbreviation: two commits sharing
        # a prefix would silently drop one, and a case that quietly examines the wrong commit is
        # the loose match `server/design/land.md` records landing a FAIL twice.
        matched = [c for c in walked if c.sha.startswith(short)]
        assert len(matched) == 1, (
            f"{short} ({what}) matches {len(matched)} commit(s) in `{AUDIT_FLOOR}..main`, so this "
            f"case classified nothing (or the wrong thing). The range walked {len(walked)} "
            f"commit(s).")
        commit = matched[0]
        assert commit.ticket == ticket, f"{short} reads as {commit.ticket}, not {ticket}"
        assert commit.files, f"{short} touched no file, so it says nothing about the category"
        assert not commit.is_landing, (
            f"{short} ({what}) touches {commit.non_docs}, so it is not the document commit this "
            f"case is about")

    counted = landings(AUDIT_FLOOR)
    for short, (ticket, what) in DOCUMENT_COMMITS.items():
        got = counted.get(ticket, [])
        assert len(got) <= 1, (
            f"{ticket} counts {len(got)} landings over {AUDIT_FLOOR}..main, so {short} ({what}) "
            f"was counted as one: " + ", ".join(str(c) for c in got))


# ── criterion 4 · MANDATORY NEGATIVE ─────────────────────────────────────────────────────────

def test_the_check_refuses_the_landing_that_got_through():
    """§1 criterion 4 — the same two predicates, one range earlier, are RED on the history that
    is in this repository right now.

    Without this, criteria 1 and 2 pass on any history where nobody has misbehaved yet, which
    includes a repository with no commits at all. This is the defect transcribed: `WEB-FIX-370`
    landed three times outside `docs/` and brought a merge commit with it.
    """
    findings = one_landing_findings(BEFORE_THE_VIOLATION)
    assert findings, (
        f"predicate 1 found nothing wrong in `{BEFORE_THE_VIOLATION}..main`, which is the range "
        f"holding `WEB-FIX-370`'s three landings. The predicate cannot see the defect it exists "
        f"for, so criterion 1 proves nothing.")
    named = [f for f in findings if "WEB-FIX-370" in f]
    assert named, f"predicate 1's findings do not name WEB-FIX-370: {findings}"
    assert "landed 3 times" in named[0], (
        f"WEB-FIX-370 put three commits touching paths outside `{DOCUMENT_ROOT}` on `main` "
        f"(b0caabdd, 4ba27c83, 1feee984) and the predicate counted otherwise: {named[0]}")

    merged = merge_findings(BEFORE_THE_VIOLATION)
    assert any(f.startswith("efefdc60") for f in merged), (
        f"predicate 2 does not name `efefdc60`, the merge commit that reached `main` when the QA "
        f"lane pushed its branch head: {merged}")


# ── criterion 5 ──────────────────────────────────────────────────────────────────────────────

QA_SKILL = FACTORY / ".claude" / "skills" / "qa-ticket" / "SKILL.md"


def merge_section():
    """The skill's landing instructions — from the MERGE heading to the next `## `.

    The section is the unit a merging session reads, so the section is what is asserted. A grep
    over the whole file would be satisfied by the word appearing in an unrelated paragraph.
    """
    text = QA_SKILL.read_text()
    # `MERGE` uppercase, and EXACTLY one such heading. A first-match over a case-insensitive
    # pattern is how this repository has twice read the wrong document and believed it
    # (`server/design/land.md`, the verdict glob and the `find("PASS")`).
    starts = [m.start() for m in re.finditer(r"^## .*\bMERGE\b", text, re.M)]
    assert len(starts) == 1, (
        f"{QA_SKILL.name} has {len(starts)} `## …MERGE…` headings; this case reads a section and "
        f"cannot choose between two")
    start = starts[0]
    following = re.search(r"^## ", text[start + 3:], re.M)
    return text[start:start + 3 + following.start()] if following else text[start:]


def test_the_skills_merge_step_names_the_tools_landing_path():
    """§1 criterion 5 — the step a QA session follows points at the tool's landing path and names
    the hand push as the defect.

    The session that did this read the skill. What the skill did not say is that the command it
    reached for instead — `git push origin HEAD:main` behind a `merge-base --is-ancestor` check —
    is a fast-forward test and not a landing.
    """
    section = merge_section()
    where = QA_SKILL.relative_to(FACTORY).as_posix()

    names_the_path = "app/land" in section and "SRV-FEAT-437" in section
    assert names_the_path, (
        f"{where} §MERGE: "
        "the section does not name the tool's own landing path (`app/land`, "
        "`SRV-FEAT-437`), which squashes in a scratch worktree and cannot put a branch's history "
        "on `main`. A session with no such pointer reaches for git by hand.")

    names_the_command = bool(re.search(r"push[^\n]*HEAD:main", section))
    assert names_the_command, (
        f"{where} §MERGE: "
        "the section does not name `git push … HEAD:main` — the exact command that put "
        "nine commits and a merge on `main` under a verdict reading *merged to `main`* "
        "(SRV-FIX-470). A rule that does not name the thing it forbids is refused by whoever "
        "believes they are doing something else.")
    cites_the_round = "SRV-FIX-470" in section
    assert cites_the_round, (
        f"{where} §MERGE: "
        "the section does not cite `SRV-FIX-470`, so a reader cannot get from the rule to "
        "the round that measured what breaking it costs")
