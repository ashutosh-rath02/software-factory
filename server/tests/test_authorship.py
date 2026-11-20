"""SRV-FIX-376 — a squash merge must not rewrite the author.

Git carries two names: AUTHOR, who wrote it, and COMMITTER, who applied it. `git merge --squash`
stages changes and leaves `git commit` to write BOTH as whoever runs it, so this repository's merge
rule silently reassigns a member's work to the maintainer.

The defect is INVISIBLE on a single-author repository — every row of `git log --format='%an|%cn'`
reads the same name either way — so every case here builds a two-author repository in `tmp_path`
and reads what git actually recorded. Nothing is asserted against this repository's own history.
"""
import re
import subprocess
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[2]
SKILLS = FACTORY / ".claude" / "skills"

MEMBER = "Member Dev <member@example.invalid>"
SECOND = "Second Dev <second@example.invalid>"
# SRV-FIX-532 cluster A4. The grader's name must sort ABOVE the member's under `sort -rn`'s
# reversed last-resort whole-line comparison, or the tie is won by the right person for the wrong
# reason and the case grades the alphabet instead of the rule.
GRADER_NAME = "Zed Reviewer"
GRADER = f"{GRADER_NAME} <zed@example.invalid>"


def git(repo, *args, **kw):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=kw.pop("check", True), timeout=60, **kw)


@pytest.fixture
def repo(tmp_path):
    """A repository whose branch was written by somebody other than the person merging."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Maintainer")
    git(r, "config", "user.email", "maintainer@example.invalid")
    (r / "f").write_text("base\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    git(r, "checkout", "-q", "-b", "ticket/SRV-FEAT-001")
    (r / "f").write_text("member's work\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "SRV-FEAT-001: the member's first commit", f"--author={MEMBER}")
    (r / "g").write_text("more\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "SRV-FEAT-001: and their second", f"--author={MEMBER}")
    git(r, "checkout", "-q", "main")
    return r


QA_SKILL = SKILLS / "qa-ticket" / "SKILL.md"


def skill_merge_block():
    """The fenced block a merging session copies out of `qa-ticket/SKILL.md`, verbatim."""
    text = QA_SKILL.read_text()
    start = text.index("git merge --squash")
    block = text[text.rindex("```", 0, start) + 3:text.index("```", start)].splitlines()
    if block and block[0].strip() in ("bash", "sh", ""):
        block = block[1:]
    return "\n".join(block)


def shell_fragment(block, var):
    """The complete shell assignment of `var`, taken out of the skill's own text.

    A fragment continues while the line ends in a backslash, while a `$(` or `<(` is unclosed, or
    while a `do` has no `done`: the skill writes `AUTHOR=` across two lines and `TRAILERS=` as a
    `while … done < <(…)` loop.
    """
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith(var + "="):
            continue
        frag = [line]
        while True:
            text = "\n".join(frag)
            unclosed_loop = (len(re.findall(r"(?<![\w])do(?![\w])", text))
                             > len(re.findall(r"(?<![\w])done(?![\w])", text)))
            if (text.rstrip().endswith("\\") or unclosed_loop
                    or text.count("(") > text.count(")")):
                i += 1
                frag.append(lines[i])
                continue
            return text
    raise AssertionError(f"`{var}=` is assigned nowhere in the skill's merge block:\n{block}")


def skill_derivation(repo, branch, *, no_merges=True):
    """RUN the skill's own `AUTHOR=` and `TRAILERS=` lines against `repo`. Never a model of them.

    This helper used to be two Python functions that re-implemented the pipeline. Under
    `SRV-FIX-378` the pipeline IS the subject, so a Python copy carrying the fix would grade the
    copy: criteria 1 and 2 would go green while the sentence a person actually reads at merge time
    still let the merger's bookkeeping outvote the branch's author. That is `_TEMPLATE.md` §2's
    second way a check lies, and it is how `SRV-FIX-376` shipped this defect.

    `no_merges=False` reverts the fix IN THE SKILL'S TEXT, which is the break the mandatory
    negative is required to make.
    """
    block = skill_merge_block()
    script = "\n".join([
        shell_fragment(block, "AUTHOR"),
        shell_fragment(block, "TRAILERS"),
        'printf "%s\\n" "$AUTHOR"',
        'printf -- "--\\n"',
        'for t in "${TRAILERS[@]}"; do printf "%s\\n" "$t"; done',
    ]).replace("ticket/<id>", branch)
    if not no_merges:
        script = script.replace(" --no-merges", "")
    out = subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, f"the skill's own derivation failed:\n{script}\n{out.stderr}"
    who, _, rest = out.stdout.partition("\n--\n")
    return who.strip() or None, [line for line in rest.splitlines() if line]


def skill_merge_commands():
    """The merge THE SKILL PRESCRIBES, read out of it.

    This is the whole point of the case. A helper here that ran the right commands would test the
    helper: criteria 1 and 2 would pass while the instruction a person actually follows still lost
    the author. The commands are extracted from `qa-ticket/SKILL.md`, so the thing under test is
    the sentence somebody reads at merge time.
    """
    text = QA_SKILL.read_text()
    start = text.index("git merge --squash")
    block = text[text.rindex("```", 0, start) + 3:text.index("```", start)]
    out = []
    for raw in block.splitlines():
        # the skill writes `git checkout main && git merge --squash …` on one line
        for line in raw.split("#", 1)[0].split("&&"):
            line = line.strip()
            if not line.startswith("git"):
                continue
            if "origin" in line or "push" in line:  # a fixture has no remote, and what crosses
                continue                            # one is not this ticket's subject
            out.append(line)
    assert out, "no git commands found in the skill's merge block"
    return out


def merged(repo, branch, *, use_skill=True, no_merges=True):
    """Run the merge the skill prescribes. `use_skill=False` is the plain merge, for the negative;
    `no_merges=False` runs the skill's own derivation with this ticket's fix reverted."""
    if not use_skill:
        git(repo, "merge", "--squash", branch)
        git(repo, "commit", "-q", "-m", "SRV-FEAT-001: the member's feature")
        return git(repo, "log", "-1", "--format=%an|%cn|%b").stdout.strip()

    who, trailers = skill_derivation(repo, branch, no_merges=no_merges)
    for command in skill_merge_commands():
        # the skill derives the author into a shell variable; the fixture substitutes it, so
        # what runs here is the skill's line with its own value filled in
        command = command.replace("ticket/<id>", branch)
        for form in ('"$AUTHOR"', "$AUTHOR", "$(AUTHOR)", "<AUTHOR>"):
            command = command.replace(form, who or "")
        if command.startswith("git commit"):
            args = ["commit", "-q", "-m", "SRV-FEAT-001: the member's feature"]
            # everything the skill's own commit line carries beyond the bare verb
            for flag in re.findall(r"--author=\S.*?$|--author=[^\s]+", command):
                args.append(f"--author={who}" if who else flag)
            # the trailers are the ones the skill's own loop built, passed through unread
            if "TRAILERS" in command or "--trailer" in command:
                args += trailers
            git(repo, *args)
        else:
            git(repo, *command.split()[1:])
    return git(repo, "log", "-1", "--format=%an|%cn|%b").stdout.strip()


# ── criterion 1 ──────────────────────────────────────────────────────────────────────────────

def test_a_squash_merge_keeps_the_branchs_author(repo):
    """§1 criterion 1 — the member wrote it, the maintainer applied it, and git records both."""
    author, committer, _body = merged(repo, "ticket/SRV-FEAT-001").split("|", 2)
    assert author == "Member Dev", (
        f"the squash merge recorded `{author}` as the author of work `Member Dev` wrote. "
        f"A member's name is destroyed by the merge and history cannot be rewritten afterwards.")
    assert committer == "Maintainer", (
        f"the committer should be whoever merged, and is `{committer}`")


# ── criterion 3 · MANDATORY NEGATIVE, and it comes before criterion 2 because it is what makes
#    criterion 1 mean anything on a repository where every commit already shares one author ──

def test_without_the_flag_the_author_is_lost(repo):
    """§1 criterion 3. Criterion 1 passes trivially if the fixture's authors are already the same,
    which is exactly the state of the repository this ticket was written in."""
    author, committer, _body = merged(repo, "ticket/SRV-FEAT-001", use_skill=False).split("|", 2)
    assert author == "Maintainer" and committer == "Maintainer", (
        f"the plain squash merge did NOT lose the author ({author}/{committer}), so criterion 1 "
        f"proves nothing — it would pass whether or not the flag were passed.")


# ── criterion 2 ──────────────────────────────────────────────────────────────────────────────

def test_a_second_author_is_named_in_a_trailer(repo):
    """§1 criterion 2 — `--author` holds exactly one name, and dropping the rest silently is the
    same defect one size smaller."""
    (repo / "h").write_text("third hand\n")
    git(repo, "checkout", "-q", "ticket/SRV-FEAT-001")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "SRV-FEAT-001: a second pair of hands", f"--author={SECOND}")
    git(repo, "checkout", "-q", "main")

    line = merged(repo, "ticket/SRV-FEAT-001")
    author, _committer, body = line.split("|", 2)
    assert author == "Member Dev", author
    assert "Co-Authored-By: Second Dev" in body, (
        f"the branch had two authors and only one survives. The commit body is:\n{body}")


# ── criterion 4 ──────────────────────────────────────────────────────────────────────────────

def merge_prescriptions():
    """Every RUNNABLE merge in this repository's documents, DERIVED BY CONTENT.

    The corpus is not a directory. Round 1 of this ticket fixed a block and missed the paragraph
    44 lines below it; round 2 fixed one file and missed another, because criterion 4 swept
    `.claude/skills/*/SKILL.md` — where the grep returns exactly one line, the copy just edited.
    **A sweep over one element agrees with itself.**

    Worse, the scope came from a sentence I had written in the report — *"the only place a merge
    happens"* — so the gate could only ever confirm what I already believed. A premise stated in
    prose and then used as a corpus boundary is self-confirming.

    Derived, the answer was three unfixed documents rather than one. A merge is RUNNABLE when a
    `git merge --squash` appears inside a fenced block with a `git commit` near it: that is the
    text somebody copies, and prose about the rule cannot lose an author by itself.
    """
    found = []
    for doc in sorted(FACTORY.rglob("*.md")):
        rel = doc.relative_to(FACTORY).as_posix()
        if rel.startswith(("docs/reports/", "node_modules")) or "node_modules" in rel:
            continue
        lines = doc.read_text().splitlines()
        fenced = False
        for n, line in enumerate(lines, 1):
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced or "merge --squash" not in line:
                continue
            window = "\n".join(lines[n - 1:n + 6])
            if "git commit" in window:
                found.append((rel, n, window))
    assert found, "no runnable merge found in any document — the sweep reaches nothing"
    return found


def test_every_documented_squash_merge_passes_an_author():
    """§1 criterion 4 — every runnable merge keeps the branch's author.

    A ticket's `stages:` line names MERGE and `_TEMPLATE.md` §2 is the standing process it follows
    verbatim, so a merge block there is followed exactly as often as the skill's.
    """
    offenders = [f"{rel}:{n}" for rel, n, window in merge_prescriptions()
                 if "--author" not in window]
    assert not offenders, (
        "these runnable merges do not pass an author, so the merge they describe reassigns the "
        "branch's work to whoever runs it:\n  " + "\n  ".join(offenders))


def test_the_sweep_reaches_more_than_the_skill_it_was_written_in():
    """MANDATORY NEGATIVE for the case above, and the reason round 2 failed.

    Scoped to `.claude/skills/`, criterion 4 swept ONE line — already fixed — and passed while
    `docs/tickets/_TEMPLATE.md` still lost the author. A corpus of one cannot disagree with itself.
    """
    where = {rel for rel, _n, _w in merge_prescriptions()}
    assert len(where) >= 2, (
        f"the sweep reaches only {where}. A corpus of one agrees with itself; this case exists "
        f"because that is exactly how criterion 4 passed in round 2.")
    assert any(r.startswith("docs/") for r in where), (
        f"the sweep reaches no document under docs/, which is where §2's standing process lives: "
        f"{where}")


# ── the round after a FAIL commits the missing assertion first ────────────────────────────────

def test_no_skill_both_mandates_and_forbids_a_coauthor_trailer():
    """SRV-FIX-376 QA, the blocker: `qa-ticket/SKILL.md` gained a mandatory
    `--trailer "Co-Authored-By: …"` at :270 while :314 still read *"Sign it with nothing (hard).
    No `Co-Authored-By` … not even when you merge on behalf of a lane"* — 44 lines apart, in one
    file, and the prohibition's own worked example IS the new block's scenario.

    A session reading in good faith drops the trailers, which §1 calls the same defect one size
    smaller, and it is permanent. The IMPL commit that introduced it named the class in its own
    message: *a rule in a paragraph next to a block is a rule the next person copying the block
    does not run.* Here the paragraph countermands the block.

    The prohibition is about the TOOL never signing — no vendor, no model, no generated-with
    footer. A human who authored commits on the branch IS the work, which is the same principle
    and not an exception to it. This case requires the distinction to be written where the
    prohibition is, so the two cannot be read as contradicting.
    """
    offenders = []
    for skill in sorted(SKILLS.glob("*/SKILL.md")):
        text = skill.read_text()
        mandates = "Co-Authored-By: $who" in text or '--trailer "Co-Authored-By' in text
        if not mandates:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if "No `Co-Authored-By`" not in line:
                continue
            # the prohibition must carve the human author out where it is written, not elsewhere
            window = "\n".join(text.splitlines()[n - 1:n + 6])
            if not re.search(r"author(ed|ship)?\b.*\bbranch\b|\bbranch\b.*\bauthor", window, re.I):
                offenders.append(
                    f"{skill.relative_to(FACTORY).as_posix()}:{n}: forbids `Co-Authored-By` "
                    f"without carving out the branch's human author, while the same file "
                    f"mandates that trailer")
    assert not offenders, "\n  ".join(["a skill contradicts itself about trailers:"] + offenders)


def test_a_report_claiming_no_client_files_is_not_contradicted_by_its_own_commits():
    """SRV-FIX-376 QA, the excerpt audit: the report said *"no file under `client/` is touched"*
    while `965dbd0` — the ticket's own IMPL commit — adds 83 lines to
    `client/web/design/factory.pen`.

    The conclusion (`visual: N/A`) was still right; the REASON was false, and a false reason is why
    nothing looked at the sweep-in. A report is read as evidence, so a sentence in it that git
    contradicts is the same class as a gate that says yes wrongly — one document earlier.
    """
    import subprocess as sp
    reports = FACTORY / "docs" / "reports"
    offenders = []
    for report in sorted(reports.glob("*-report.md")):
        text = report.read_text()
        if "no file under `client/` is touched" not in text:
            continue
        tid = report.name[: -len("-report.md")]
        out = sp.run(["git", "log", "--all", "--format=%H", f"--grep=^{tid}"],
                     cwd=FACTORY, capture_output=True, text=True, timeout=60).stdout.split()
        touched = set()
        for sha in out:
            files = sp.run(["git", "show", "--name-only", "--format=", sha],
                           cwd=FACTORY, capture_output=True, text=True, timeout=60).stdout.split()
            touched |= {f for f in files if f.startswith("client/")}
        if touched:
            offenders.append(f"{report.name} claims no `client/` file is touched; its commits touch "
                             + ", ".join(sorted(touched)[:3]))
    assert not offenders, "\n  ".join(["a report is contradicted by its own commits:"] + offenders)


# ── SRV-FIX-378 · the merger's own merge commits outvote the author ───────────────────────────
#
# Every fixture above is ONE KIND — two commits, one author, no merges — and a reduction cannot be
# wrong on a population of one kind: `max`, `min`, most-frequent and "the only name present" all
# return the same value. Meanwhile the block under test mandates `git fetch` + `git merge
# origin/main` INTO the branch ABOVE the query that counts it, so the very instruction being
# verified manufactures the commits the fixture omits. The fixture below carries both kinds and
# names them.

MAINTAINER = "Maintainer <maintainer@example.invalid>"


@pytest.fixture
def repo_with_merge_ins(repo):
    """The ordinary shape of a ticket that took more than one QA round while `main` moved.

    TWO KINDS in one commit range, named:
      - AUTHORSHIP  — one commit by `Member Dev`, the work the ticket is;
      - BOOKKEEPING — two `git merge main` commits by `Maintainer`, which `qa-ticket/SKILL.md`
        mandates in the same fenced block, above the query that counts them.

    2:1 against the member. A 1:1 tie is already enough to lose the author, because the reduction
    breaks ties towards whatever it saw first.
    """
    branch = "ticket/SRV-FEAT-001"
    git(repo, "checkout", "-q", branch)
    git(repo, "reset", "-q", "--hard", "HEAD~1")          # one authorship commit, not two
    for n in (1, 2):
        git(repo, "checkout", "-q", "main")
        (repo / f"main-{n}").write_text(f"another session's ticket {n}\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"SRV-FEAT-10{n}: somebody else's work")
        git(repo, "checkout", "-q", branch)
        git(repo, "merge", "-q", "--no-ff", "-m", f"Merge main into {branch}", "main")
    git(repo, "checkout", "-q", "main")

    population = [line for line in
                  git(repo, "log", "--format=%an", f"main..{branch}").stdout.splitlines() if line]
    assert sorted(population) == ["Maintainer", "Maintainer", "Member Dev"], (
        f"the fixture does not carry two kinds, so every reduction over it is the identity "
        f"and neither case below can fail: {population}")
    return repo


def test_merge_commits_do_not_outvote_the_author(repo_with_merge_ins):
    """§1 criterion 1 — a branch the maintainer merged `main` into twice still names the member.

    The author is derived by RUNNING `qa-ticket/SKILL.md`'s own `AUTHOR=` line, not a copy of it,
    so this case is red while the sentence a person reads at merge time is unfixed.
    """
    author, committer, _body = merged(repo_with_merge_ins, "ticket/SRV-FEAT-001").split("|", 2)
    assert author == "Member Dev", (
        f"the merge recorded `{author}` as the author of a branch whose only real commit is "
        f"`Member Dev`'s. The other two commits in `main..ticket/SRV-FEAT-001` are the merger's "
        f"own `git merge main` bookkeeping, which the same instruction block mandates above the "
        f"query that counts them. A merge commit is not authorship.")
    assert committer == "Maintainer", (
        f"the committer should be whoever merged, and is `{committer}`")


def test_without_no_merges_the_bookkeeping_wins(repo_with_merge_ins):
    """§1 criterion 2 · MANDATORY NEGATIVE — the fix reverted IN THE SKILL'S TEXT loses the author.

    Without this, criterion 1 passes on any fixture whose branch has no merge commits, which is
    every fixture `SRV-FIX-376` wrote — and it would also pass against a Python helper that
    modelled the query correctly while the skill did not.
    """
    author, committer, body = merged(
        repo_with_merge_ins, "ticket/SRV-FEAT-001", no_merges=False).split("|", 2)
    assert author == "Maintainer" and committer == "Maintainer", (
        f"with `--no-merges` stripped out of the skill's own derivation the merge did NOT lose "
        f"the author ({author}/{committer}), so criterion 1 proves nothing — it would pass "
        f"whether or not the flag were there.")
    assert "Co-Authored-By: Member Dev" in body, (
        f"the member should survive only as a trailer here — that is `SRV-FIX-376` §1's *the same "
        f"defect one size smaller*, arriving through the winning path. Body:\n{body}")


def merge_blocks():
    """Every RUNNABLE merge in this repository's documents, as WHOLE FENCED BLOCKS.

    Same corpus rule `SRV-FIX-376` established and QA accepted — a fenced block holding both
    `git merge --squash` and a `git commit` is the text somebody copies at merge time — widened
    from that ticket's 7-line window to the whole fence.

    The widening IS the fix for the miss. The author derivation sits FOUR lines above the merge in
    `qa-ticket/SKILL.md` and FIVE above it in `_TEMPLATE.md`, so a window anchored on the merge
    line cannot see the query that feeds it: criterion 4 of `SRV-FIX-376` was green over both
    unfixed derivations.

    Three fenced blocks in the tree read `%an` over a range and are deliberately NOT in this
    corpus, by the stated category *a document quoting a query as evidence or as a request*:
    `SRV-FIX-376`'s §1 (the query it asks for), and `SRV-FIX-378`'s §1 and §3 (the defect quoted
    verbatim, and the grep output recording the tree before this fix). None of the three carries a
    merge or a commit, and rewriting them would delete the evidence they exist to hold.
    """
    found = []
    for doc in sorted(FACTORY.rglob("*.md")):
        rel = doc.relative_to(FACTORY).as_posix()
        if rel.startswith("docs/reports/") or "node_modules" in rel:
            continue
        fenced, block, opened = False, [], 0
        for n, line in enumerate(doc.read_text().splitlines(), 1):
            if line.strip().startswith("```"):
                if fenced:
                    text = "\n".join(block)
                    if "merge --squash" in text and "git commit" in text:
                        found.append((rel, opened, text))
                    block = []
                else:
                    opened = n
                fenced = not fenced
                continue
            if fenced:
                block.append(line)
    assert found, "no runnable merge block found in any document — the sweep reaches nothing"
    return found


def test_every_documented_derivation_excludes_merge_commits():
    """§1 criterion 3 — every runnable merge in the repository's documents excludes merge commits.

    A merging session copies the block, not the paragraph, so a derivation that counts merge
    commits reassigns the branch's work no matter what the prose around it says.
    """
    offenders, examined, documents = [], [], set()
    for rel, opened, text in merge_blocks():
        documents.add(rel)
        seen_here = 0
        for n, line in enumerate(text.splitlines(), opened + 1):
            if "%an" not in line or ".." not in line:
                continue
            seen_here += 1
            examined.append(f"{rel}:{n}")
            if "--no-merges" not in line:
                offenders.append(f"{rel}:{n}: {line.strip()}")
        assert seen_here, (
            f"{rel}:{opened} prescribes a squash merge and derives no author over a commit range, "
            f"so this case swept it and asserted nothing about it")

    assert len(documents) >= 2, (
        f"the sweep reaches only {documents}. A corpus of one agrees with itself, which is how "
        f"round 2 of `SRV-FIX-376` passed while `_TEMPLATE.md` was still wrong.")
    assert not offenders, (
        "these runnable derivations count the merger's own `git merge main` commits as "
        "authorship, so the merge they prescribe hands the branch to whoever resolved the last "
        "conflict:\n  " + "\n  ".join(offenders)
        + f"\n  (examined: {', '.join(examined)})")


# ── SRV-FIX-532 cluster A4 — the verdict's commits are not the work ────────────────────────────
#
# `SRV-FIX-425`. `--no-merges` removed the merger's bookkeeping shaped like a MERGE and left the
# bookkeeping shaped like a DOCUMENT: the verdict, the `status:` edit and the audit, which
# `qa-ticket/SKILL.md` requires the QA session to commit onto the branch before it merges. Three
# commits of work against three of verdict is a tie `sort -rn | head -1` decides by accident, and
# one more QA commit — a second round, a re-run quoted, a filed minor — loses the author outright.


@pytest.fixture
def repo_with_a_verdict(repo):
    """The shape a finished ticket really has, and the shape `SRV-FIX-425` §1 measured.

    TWO KINDS in one commit range, named:
      - THE WORK      — three commits by `Member Dev` touching source;
      - THE VERDICT   — three by the QA session: the verdict document, the ticket's `status:`
                        edit, and the audit appended to the verdict.

    3:3. The fixture asserts the tie before anything is derived, because on a fixture where the
    two counts differ every rule agrees with every other. The grader is named so that the tie is
    lost — `sort -rn | head -1` breaks a tie on the reversed whole-line comparison, so the author
    today is decided by how two people's names happen to sort against each other, which is
    `SRV-FIX-425` §1's sentence exactly.
    """
    branch = "ticket/SRV-FEAT-001"
    git(repo, "checkout", "-q", branch)
    (repo / "h").write_text("a third piece of work\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "SRV-FEAT-001: and their third", f"--author={MEMBER}")

    tickets = repo / "docs" / "tickets"
    reports = repo / "docs" / "reports"
    tickets.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    ticket = tickets / "SRV-FEAT-001-a-thing-to-land.md"
    verdict = reports / "SRV-FEAT-001-qa.md"

    ticket.write_text("---\nid: SRV-FEAT-001\nstatus: awaiting-qa\n---\n")
    verdict.write_text("# SRV-FEAT-001 — QA verdict — PASS WITH MINORS\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "SRV-FEAT-001: QA verdict — PASS WITH MINORS",
        f"--author={GRADER}")
    ticket.write_text("---\nid: SRV-FEAT-001\nstatus: pass\n---\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "SRV-FEAT-001: status pass — every criterion verified",
        f"--author={GRADER}")
    verdict.write_text(verdict.read_text() + "\n## Excerpt audit\n\nNo misquote found.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "SRV-FEAT-001: QA — the merged tree, read against main",
        f"--author={GRADER}")
    git(repo, "checkout", "-q", "main")

    population = [line for line in
                  git(repo, "log", "--no-merges", "--format=%an",
                      f"main..{branch}").stdout.splitlines() if line]
    assert population.count("Member Dev") == population.count(GRADER_NAME) == 3, (
        f"the fixture must be the 3:3 tie `SRV-FIX-425` §1 measured, or a tie-break and a rule "
        f"that reads the work are indistinguishable on it: {population}")
    return repo


def test_the_verdicts_commits_do_not_outvote_the_work(repo_with_a_verdict):
    """§1 A4 — a branch whose QA session committed as often as its implementer names the implementer.

    `SRV-FIX-425` criterion 1. Derived by RUNNING `qa-ticket/SKILL.md`'s own `AUTHOR=` line, never
    a copy of it: under this ticket the pipeline IS the subject, so a Python model of it carrying
    the fix would grade the model while the sentence a person reads at merge time still hands the
    branch to whoever graded it.

    RED before the fix: `sort -rn | head -1` returns whichever of the two `sort` happens to put
    first, and the tie is the shape every finished ticket in this repository has.
    """
    branch = "ticket/SRV-FEAT-001"
    who, trailers = skill_derivation(repo_with_a_verdict, branch)
    assert who == MEMBER, (
        f"the skill's own derivation names {who!r} as the author of a branch whose work is "
        f"`Member Dev`'s. The other three commits are the verdict document, the `status:` edit "
        f"and the audit — which the same skill REQUIRES the QA session to commit before it "
        f"merges. A verdict is not authorship.")
    assert any(GRADER_NAME in t for t in trailers), (
        f"the grader survives as a trailer, not as the author: {trailers}")

    author, committer, _body = merged(repo_with_a_verdict, branch).split("|", 2)
    assert author == "Member Dev", f"the merge recorded {author!r}"
    assert committer == "Maintainer", f"the committer is whoever merged, and is {committer!r}"
