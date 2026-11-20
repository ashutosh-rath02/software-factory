"""`tools/docgate.py` — what THIS branch adds to the document gate.

The gate is red on `main` for reasons older than any one ticket, so a verdict is a comparison:
the failing SET on this branch against the same command on its merge-base, where the set is the
case names AND the ids each failing case lists. `docs/technical/workflow/WORKFLOW.md` asks every session to do that
by hand, twice per round.

**The arbiter is the real program, not a mock.** Every canned gate output here is a capture of a
real `pytest tests/ -m repository` run, used byte-for-byte. Four were committed under
`docs/reports/` by the ticket that made them; the fifth is taken by this ticket, because no
earlier round ever recorded a gate that collapsed:

    docs/reports/SRV-FIX-453/SRV-FIX-453-document-gate-2026-09-08.log              18 red
    docs/reports/SRV-FIX-453/SRV-FIX-453-document-gate-merge-base-2026-09-08.log   18 red, same set
    docs/reports/WEB-FIX-370/WEB-FIX-370-document-gate-2026-09-08.log              17 red
    docs/reports/WEB-FIX-370/WEB-FIX-370-document-gate-merge-base-2026-09-08.log   21 red
    tests/fixtures/docgate/collection-error-2026-09-09.log        24 errors, 0 FAILED, interrupted

The `SRV-FIX-453` pair is a real branch that added nothing: two different runs, different
`rootdir`, different node-id prefixes, one failing set. The `WEB-FIX-370` pair is a real branch
that added something — its own `status:` line put `WEB-FIX-370` into two already-red cases that
enumerate the tickets they refuse. That pair is used with the two sides SWAPPED, which is the
only edit made to any capture and is stated in each case that does it: the branch under test is
the one whose gate names `WEB-FIX-370`, and the merge-base is the one whose gate does not.

To re-capture: `cd server && python3 -m pytest tests/ -m repository -v -p no:cacheprovider`, on
the branch and again on `git merge-base HEAD origin/main`.
"""

import datetime
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
CHECKER = SERVER / "tools" / "docgate.py"
REPORTS = REPO / "docs" / "reports"

CAPTURES = {
    "453-head": REPORTS / "SRV-FIX-453" / "SRV-FIX-453-document-gate-2026-09-08.log",
    "453-base": REPORTS / "SRV-FIX-453" / "SRV-FIX-453-document-gate-merge-base-2026-09-08.log",
    "370-head": REPORTS / "WEB-FIX-370" / "WEB-FIX-370-document-gate-2026-09-08.log",
    "370-base": REPORTS / "WEB-FIX-370" / "WEB-FIX-370-document-gate-merge-base-2026-09-08.log",
    # The one capture not taken by an earlier ticket. Re-capture it with
    #   printf 'raise ImportError("captured on purpose")\n' > <dir>/jsonschema.py
    #   cd server && PYTHONPATH=<dir> python3 -m pytest tests/ -m repository -v -p no:cacheprovider
    "collection-error": SERVER / "tests" / "fixtures" / "docgate"
                        / "collection-error-2026-09-09.log",
}


def capture(name):
    p = CAPTURES[name]
    assert p.exists(), f"the fixture this case rests on is not on disk: {p}"
    return p.read_text(errors="replace")


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"git {' '.join(args)} failed in {cwd}:\n{r.stdout}{r.stderr}"
    return r.stdout.strip()


def run(cwd, *args):
    return subprocess.run([sys.executable, str(CHECKER), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=900)


def make_checkout(tmp_path, base_out, head_out, name="checkout"):
    """A repository shaped like this one: `main`, then a ticket branch off it. Each commit
    carries the gate output that commit's tree produces, so `--gate-cmd 'cat gate-out.txt'`
    runs the same command on both sides and gets that side's answer — which is what the real
    default does with the real gate."""
    repo = tmp_path / name
    (repo / "server").mkdir(parents=True)
    git(repo.parent, "init", "-b", "main", str(repo))
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / "server" / "gate-out.txt").write_text(base_out)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "ticket/T")
    (repo / "server" / "gate-out.txt").write_text(head_out)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "head")
    return repo


GATE = ["--gate-cmd", "cat gate-out.txt"]

VERDICT = re.compile(r"^base: (\d+) red · head: (\d+) red · added: (\d+)$", re.M)
SUMMARY_LINE = re.compile(r"^=+ .*\b\d+ (?:failed|passed|error|errors)\b.*=+$", re.M)


def passed_in_final_pytest_summary(said):
    summaries = list(SUMMARY_LINE.finditer(said))
    assert summaries, f"the document gate produced no summary line, so it did not run:\n{said[-3000:]}"
    passed = re.search(r"\b(\d+) passed\b", summaries[-1].group(0))
    return int(passed.group(1)) if passed else 0


def test_owned_gate_counts_terminal_summary_after_embedded_nested_pytest_output():
    said = ("E   nested output: ============ 1 failed, 1 passed in 0.01s ============\n"
            "FAILED tests/test_catalog.py::historical_parent_case - existing debt\n"
            "================== 2 failed, 21 passed in 2.00s ==================\n")
    assert passed_in_final_pytest_summary(said) == 21


def test_a_branch_that_adds_no_failure_passes_while_the_gate_is_red_on_both_sides(tmp_path):
    """Criterion 1. `SRV-FIX-453`'s own pair: two real runs of the real gate, 18 red on each
    side, one failing set. The two captures are not the same bytes — different `rootdir`, and
    node ids written `tests/…` on one side and `server/tests/…` on the other — so a comparison
    that passes by comparing text rather than sets cannot pass this."""
    base, head = capture("453-base"), capture("453-head")
    assert base != head, "the two sides of this fixture are the same bytes; it proves nothing"

    repo = make_checkout(tmp_path, base, head)
    r = run(repo, *GATE)

    m = VERDICT.search(r.stdout)
    assert m, f"no verdict line in:\n{r.stdout}{r.stderr}"
    n_base, n_head, added = (int(x) for x in m.groups())
    assert (n_base, n_head, added) == (18, 18, 0), r.stdout
    assert n_base > 0 and n_head > 0, "the gate must be RED on both sides for this to mean anything"
    assert r.returncode == 0, f"a branch that adds nothing did not exit 0:\n{r.stdout}{r.stderr}"


def test_a_newly_red_case_blocks_by_name(tmp_path):
    """Criterion 2. `WEB-FIX-370`'s pair with the sides swapped: the branch under test is the
    capture carrying 21 red and the merge-base the one carrying 17, so four cases are red on the
    branch and red nowhere on the merge-base."""
    base, head = capture("370-head"), capture("370-base")
    repo = make_checkout(tmp_path, base, head)
    r = run(repo, *GATE)

    assert r.returncode == 1, f"a newly red case did not exit 1:\n{r.stdout}{r.stderr}"
    assert "tests/test_catalog.py::test_a_ticket_landed_on_main_has_a_report" in r.stdout, r.stdout
    assert "NEW CASE" in r.stdout, r.stdout

    m = VERDICT.search(r.stdout)
    assert m and (int(m.group(1)), int(m.group(2))) == (17, 21), r.stdout


def test_a_new_id_inside_an_already_red_case_blocks_and_a_stable_list_does_not(tmp_path):
    """Criterion 3. One fixture, two cases red on BOTH sides, telling them apart:

    `test_every_escape_ticket_answers_section_3_and_has_a_ledger_row` is red on both sides and its
    list GREW by `WEB-FIX-370` — the real thing `SRV-FIX-384` describes, a ticket's own commit
    putting its own name into a case that was already red.

    `test_a_ticket_describes_no_workspace_but_its_own` is red on both sides with the same list on
    both, and must not be reported at all.
    """
    base, head = capture("370-head"), capture("370-base")
    grew = "tests/test_catalog.py::test_every_escape_ticket_answers_section_3_and_has_a_ledger_row"
    stable = "tests/test_catalog.py::test_a_ticket_describes_no_workspace_but_its_own"
    for side in (base, head):
        assert grew.split("::")[1] in side and stable.split("::")[1] in side, \
            "both cases must be red on both sides or this fixture is not about ids at all"

    repo = make_checkout(tmp_path, base, head)
    r = run(repo, *GATE)

    assert r.returncode == 1, f"a new id in an already-red case did not exit 1:\n{r.stdout}{r.stderr}"
    lines = r.stdout.splitlines()
    new_ids = [ln for ln in lines if ln.startswith("NEW ID")]
    assert any(grew in ln and "WEB-FIX-370" in ln for ln in new_ids), \
        f"the grown list was not reported by case AND id:\n{r.stdout}"
    assert not any(stable in ln for ln in lines if ln.startswith(("NEW ID", "NEW CASE"))), \
        f"a case red on both sides with the same ids was reported as an addition:\n{r.stdout}"


def test_logs_are_written_beside_the_report_only_when_a_ticket_is_named(tmp_path):
    """Criterion 4. The pair layout is derived from the pairs already committed under
    `docs/reports/`, never typed: every committed capture must be one of the two shapes the
    script writes."""
    base, head = capture("453-base"), capture("453-head")
    today = datetime.date.today().isoformat()

    # The pairs §1 names — and only those. `docs/reports/` as a whole carries 34 different
    # spellings of this filename, which is what a ritual nobody automated looks like on disk.
    pairs = [d for d in (REPORTS / "WEB-FIX-370", REPORTS / "SRV-FIX-453")]
    committed = sorted(f for d in pairs for f in d.glob("*-document-gate-*.log"))
    assert len(committed) == 4, \
        f"the two committed pairs this criterion is measured against are not on disk: {committed}"
    shapes = {re.sub(r"^[A-Z]+-[A-Z]+-\d+", "<ID>",
                     re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", f.name)) for f in committed}
    assert shapes == {"<ID>-document-gate-<DATE>.log", "<ID>-document-gate-merge-base-<DATE>.log"}, \
        f"the committed pairs are not the two shapes this criterion is about: {sorted(shapes)}"

    repo = make_checkout(tmp_path, base, head)
    r = run(repo, *GATE, "--ticket", "WEB-FIX-370")
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"

    d = repo / "docs" / "reports" / "WEB-FIX-370"
    head_log = d / f"WEB-FIX-370-document-gate-{today}.log"
    base_log = d / f"WEB-FIX-370-document-gate-merge-base-{today}.log"
    assert head_log.exists(), f"no head log at {head_log}; {sorted(p.name for p in d.glob('*')) if d.exists() else 'no directory'}"
    assert base_log.exists(), f"no merge-base log at {base_log}"
    assert head_log.read_text(errors="replace").strip() == head.strip(), \
        "the head log is not the head run's raw output"
    assert base_log.read_text(errors="replace").strip() == base.strip(), \
        "the merge-base log is not the merge-base run's raw output"

    other = make_checkout(tmp_path, base, head, name="checkout-no-ticket")
    r2 = run(other, *GATE)
    assert r2.returncode == 0, f"{r2.stdout}{r2.stderr}"
    assert not (other / "docs").exists(), \
        f"without --ticket the script wrote under docs/: {sorted(p.name for p in (other / 'docs').rglob('*'))}"


def test_the_scratch_worktree_is_removed_on_every_exit_and_the_checkout_is_untouched(tmp_path):
    """Criterion 5. Exit 0, exit 1 and exit 2 in one case, each asked the same three questions of
    the operator's checkout afterwards: no worktree the script created, the same `HEAD`, the same
    `git status`."""
    b453, h453 = capture("453-base"), capture("453-head")
    b370, h370 = capture("370-head"), capture("370-base")
    killed = h453[:h453.index("=================================== FAILURES")]

    for expected, base_out, head_out in ((0, b453, h453), (1, b370, h370), (2, b453, killed)):
        repo = make_checkout(tmp_path, base_out, head_out, name=f"checkout-{expected}")
        worktrees_before = git(repo, "worktree", "list")
        head_before = git(repo, "rev-parse", "HEAD")
        status_before = git(repo, "status", "--porcelain")

        r = run(repo, *GATE)
        assert r.returncode == expected, \
            f"expected exit {expected}, got {r.returncode}:\n{r.stdout}{r.stderr}"

        after = git(repo, "worktree", "list")
        assert after == worktrees_before, \
            f"exit {expected} left a worktree behind:\nbefore:\n{worktrees_before}\nafter:\n{after}"
        assert len(after.splitlines()) == 1, f"more than the checkout itself:\n{after}"
        assert git(repo, "rev-parse", "HEAD") == head_before, f"exit {expected} moved HEAD"
        assert git(repo, "status", "--porcelain") == status_before, \
            f"exit {expected} changed the checkout's working tree"


def test_the_baseline_keeps_the_same_git_and_enclosing_repository_context(tmp_path):
    """A branch must not acquire a NEW CASE solely because its baseline lost Git metadata.

    Exercise the existing prompt-comparison assertion unchanged in a synthetic gitlink-shaped
    checkout. Both revisions have identical prompts, while the enclosing repository has drifted
    since their historical capture. A linked baseline sees the same current drift; an archive
    instead accepts the historical capture and invents a newly failing case on the head.
    """
    import inspect
    import test_catalog

    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-b", "main")
    (outer / ".factory/prompts").mkdir(parents=True)
    (outer / ".factory/factory.json").write_text("{}\n")
    repo = outer / "tool"
    repo.mkdir()
    common = outer / ".git/modules/tool"
    common.parent.mkdir()
    git(outer, "init", "-b", "main", "--separate-git-dir", str(common), str(repo))
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / ".factory/prompts").mkdir(parents=True)
    (repo / ".factory/factory.json").write_text("{}\n")
    for name in ("run-ticket.md", "qa-ticket.md"):
        (repo / ".factory/prompts" / name).write_text("the recorded shared process\n")
        (outer / ".factory/prompts" / name).write_text("the recorded shared process\n")
    evidence = repo / "docs/reports/SRV-FIX-532"
    evidence.mkdir(parents=True)
    mine = test_catalog.digest_of(repo / ".factory/prompts/qa-ticket.md")
    theirs = test_catalog.digest_of(outer / ".factory/prompts/qa-ticket.md")
    assert mine == theirs
    (evidence / "synthetic-prompt-files-identical.log").write_text(
        f"this repository: qa-ticket.md sha256 {mine}\n"
        f"enclosing repository: qa-ticket.md sha256 {theirs}\ncmp: identical\n")
    tests = repo / "server/tests"
    tests.mkdir(parents=True)
    functions = (
        test_catalog._repo_root, test_catalog._this_repository,
        test_catalog._enclosing_monorepo, test_catalog.the_enclosing_monorepo_or_why_not,
        test_catalog.digest_of, test_catalog.the_newest_prompt_capture,
        test_catalog.test_the_two_manifests_prompt_files_are_the_same_text,
    )
    (tests / "test_catalog.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "MANIFEST_REL = '.factory/factory.json'\nTICKET_EVIDENCE = 'SRV-FIX-532'\n\n"
        + "\n\n".join(inspect.getsource(function) for function in functions))
    (repo / ".gitignore").write_text("__pycache__/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "baseline with measured shared prompts")
    git(repo, "checkout", "-b", "ticket/T")
    (repo / "unrelated.txt").write_text("the branch changes no prompt\n")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-m", "an unrelated head change")
    for name in ("run-ticket.md", "qa-ticket.md"):
        (outer / ".factory/prompts" / name).write_text("the enclosing repository's new process\n")
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"),
              git(repo, "worktree", "list", "--porcelain"), git(repo, "show-ref"))
    command = f"{sys.executable} -m pytest tests/test_catalog.py -v -p no:cacheprovider"
    result = run(repo, "--gate-cmd", command, "--ticket", "SRV-FEAT-558")
    print(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = VERDICT.search(result.stdout)
    assert verdict and tuple(map(int, verdict.groups())) == (1, 1, 0), result.stdout
    logs = sorted((repo / "docs/reports/SRV-FEAT-558").glob("*.log"))
    assert len(logs) == 2
    for log in logs:
        said = log.read_text()
        assert "test_the_two_manifests_prompt_files_are_the_same_text FAILED" in said, said
        assert "['run-ticket.md', 'qa-ticket.md']" in said, said
    # The command's explicitly requested raw logs are the only added files.
    assert git(repo, "rev-parse", "HEAD") == before[0]
    assert git(repo, "status", "--porcelain", "--", ":!docs/reports/SRV-FEAT-558") == before[1]
    assert git(repo, "worktree", "list", "--porcelain") == before[2]
    assert git(repo, "show-ref") == before[3]


@pytest.mark.repository
def test_linked_lane_baseline_does_not_inherit_physical_sources_manifest(tmp_path):
    """A linked lane's physical Git directory does not make its source checkout its parent."""
    import inspect
    import test_catalog

    source = tmp_path / "physical" / "source"
    (source / "server/tests").mkdir(parents=True)
    git(source.parent, "init", "-b", "main", str(source))
    git(source, "config", "user.email", "lane@factory.test")
    git(source, "config", "user.name", "lane")
    (source / ".factory").mkdir()
    (source / ".factory/factory.json").write_text(
        '{"docs":{"escapes":"docs/operations/ESCAPES.md"}}\n')
    (source / "docs/operations").mkdir(parents=True)
    (source / "docs/operations/ESCAPES.md").write_text("# Source ledger\n")
    (source / "server/tests/test_root_rule.py").write_text(
        "from pathlib import Path\n"
        "MANIFEST_REL = '.factory/factory.json'\n\n"
        + inspect.getsource(test_catalog._repo_root)
        + "\n\ndef test_baseline_root_uses_checkout():\n"
          "    checkout = Path(__file__).resolve().parents[2]\n"
          "    assert _repo_root() == checkout, f'wrong root: {_repo_root()} != {checkout}'\n"
          "    assert not (checkout.parent / MANIFEST_REL).is_file(), 'injected parent manifest'\n")
    git(source, "add", "-A")
    git(source, "commit", "-qm", "source with its own ledger")
    base = git(source, "rev-parse", "HEAD")
    lane = tmp_path / "lanes" / "01"
    lane.parent.mkdir()
    git(source, "worktree", "add", "-q", "-b", "ticket/T", str(lane))
    (lane / "unrelated.txt").write_text("changed only in the linked lane\n")
    git(lane, "add", "unrelated.txt")
    git(lane, "commit", "-qm", "unrelated change")
    common = Path(git(lane, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert source in common.parents and (source / ".factory/factory.json").is_file()
    assert not (lane.parent / ".factory/factory.json").exists()

    command = f"{sys.executable} -m pytest tests/test_root_rule.py -v -p no:cacheprovider"
    result = run(lane, "--base", base, "--gate-cmd", command, "--ticket", "SRV-FEAT-563")
    logs = lane / "docs/reports/SRV-FEAT-563"
    base_log = next(logs.glob("*-document-gate-merge-base-*.log"))
    head_log = next(logs.glob("*-document-gate-20*.log"))
    for name, log in (("base", base_log), ("head", head_log)):
        said = log.read_text()
        assert "test_baseline_root_uses_checkout PASSED" in said, (
            f"{name} gate used the wrong repository root:\n{said}")
    assert result.returncode == 0 and VERDICT.search(result.stdout).groups() == ("0", "0", "0"), (
        result.stdout + result.stderr)


@pytest.mark.repository
def test_nested_checkout_baseline_keeps_nearest_distinct_outer_manifest(tmp_path):
    import inspect
    import test_catalog

    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-b", "main")
    (outer / ".factory").mkdir()
    (outer / ".factory/factory.json").write_text("{}\n")
    repo = outer / "projects" / "nested" / "repo"
    (repo / "server/tests").mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / ".factory").mkdir()
    (repo / ".factory/factory.json").write_text("{}\n")
    functions = (test_catalog._repo_root, test_catalog._this_repository,
                 test_catalog._enclosing_monorepo)
    (repo / "server/tests/test_context.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "MANIFEST_REL = '.factory/factory.json'\n\n"
        + "\n\n".join(inspect.getsource(function) for function in functions)
        + "\n\ndef test_nested_context():\n"
          "    checkout = Path(__file__).resolve().parents[2]\n"
          "    assert _repo_root() == checkout\n"
          "    assert _enclosing_monorepo() == checkout.parents[2]\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "nested baseline")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "unrelated.txt").write_text("head\n")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-qm", "unrelated head")
    command = f"{sys.executable} -m pytest tests/test_context.py -v -p no:cacheprovider"
    result = run(repo, "--base", base, "--gate-cmd", command, "--ticket", "SRV-FEAT-563")
    logs = repo / "docs/reports/SRV-FEAT-563"
    base_log = next(logs.glob("*-document-gate-merge-base-*.log"))
    head_log = next(logs.glob("*-document-gate-20*.log"))
    for name, log in (("base", base_log), ("head", head_log)):
        said = log.read_text()
        assert "test_nested_context PASSED" in said, f"{name} used the wrong context:\n{said}"
    assert result.returncode == 0 and VERDICT.search(result.stdout).groups() == ("0", "0", "0"), (
        result.stdout + result.stderr)


@pytest.mark.repository
def test_baseline_keeps_declared_parent_documents_sibling_and_project_name(tmp_path):
    outer = tmp_path / "product"
    outer.mkdir()
    git(outer, "init", "-b", "main")
    (outer / ".factory").mkdir()
    (outer / ".factory/factory.json").write_text(
        '{"repos":{"product":{"path":".","docs":{"tickets":"docs/tickets",'
        '"escapes":"docs/operations/ESCAPES.md"}},'
        '"firmware":{"path":"boards","docs":{"tickets":"docs/tickets"}}}}\n')
    (outer / "docs/tickets").mkdir(parents=True)
    (outer / "docs/tickets/_TEMPLATE.md").write_text("parent template\n")
    (outer / "docs/operations").mkdir(parents=True)
    (outer / "docs/operations/ESCAPES.md").write_text("parent escape ledger\n")
    (outer / "docs/TICKET-WORKFLOW.md").write_text("parent process\n")
    sibling = outer / "boards"
    (sibling / "docs/tickets").mkdir(parents=True)
    git(sibling, "init", "-b", "main")
    git(sibling, "config", "user.email", "lane@factory.test")
    git(sibling, "config", "user.name", "lane")
    (sibling / ".factory").mkdir()
    (sibling / ".factory/factory.json").write_text('{"workspaces":{}}\n')
    (sibling / "docs/tickets/FW-FEAT-001.md").write_text("sibling ticket\n")
    git(sibling, "add", "-A")
    git(sibling, "commit", "-qm", "FW-FEAT-001: sibling history")
    repo = outer / "factory"
    (repo / "server/tests").mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / ".factory").mkdir()
    (repo / ".factory/factory.json").write_text("{}\n")
    (repo / "server/tests/test_context.py").write_text(
        "from pathlib import Path\nimport json\nimport subprocess\n\n"
        "def test_parent_context():\n"
        "    project = Path(__file__).resolve().parents[2]\n"
        "    assert project.name == 'factory'\n"
        "    parent = project.parent\n"
        "    manifest = json.loads((parent / '.factory/factory.json').read_text())\n"
        "    sibling = parent / manifest['repos']['firmware']['path']\n"
        "    assert (parent / 'docs/operations/ESCAPES.md').read_text() == 'parent escape ledger\\n'\n"
        "    assert (parent / 'docs/tickets/_TEMPLATE.md').read_text() == 'parent template\\n'\n"
        "    assert (parent / 'docs/TICKET-WORKFLOW.md').read_text() == 'parent process\\n'\n"
        "    assert (sibling / '.factory/factory.json').is_file()\n"
        "    assert (sibling / 'docs/tickets/FW-FEAT-001.md').read_text() == 'sibling ticket\\n'\n"
        "    history = subprocess.check_output(['git', '-C', str(sibling), 'log', 'main', '--format=%s'], text=True)\n"
        "    assert 'FW-FEAT-001: sibling history' in history\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "baseline")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "unrelated.txt").write_text("head\n")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-qm", "head")
    command = f"{sys.executable} -m pytest tests/test_context.py -v -p no:cacheprovider"
    result = run(repo, "--base", base, "--gate-cmd", command, "--ticket", "SRV-FEAT-563")
    logs = repo / "docs/reports/SRV-FEAT-563"
    base_log = next(logs.glob("*-document-gate-merge-base-*.log"))
    head_log = next(logs.glob("*-document-gate-20*.log"))
    for name, log in (("base", base_log), ("head", head_log)):
        said = log.read_text()
        assert "test_parent_context PASSED" in said, f"{name} lost declared context:\n{said}"
    assert result.returncode == 0 and VERDICT.search(result.stdout).groups() == ("0", "0", "0"), (
        result.stdout + result.stderr)


@pytest.mark.repository
def test_baseline_keeps_single_repo_parent_custom_document_paths(tmp_path):
    outer = tmp_path / "product"
    outer.mkdir()
    git(outer, "init", "-b", "main")
    (outer / ".factory").mkdir()
    (outer / ".factory/factory.json").write_text(
        '{"docs":{"tickets":"work-items","epics":"requirements",'
        '"reports":"deliveries","escapes":"policy/ESCAPES.md"}}\n')
    for relative, contents in (("work-items/T.md", "custom ticket\n"),
                               ("requirements/R.md", "custom requirement\n"),
                               ("deliveries/report.md", "custom report\n"),
                               ("policy/ESCAPES.md", "custom escape ledger\n")):
        path = outer / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    repo = outer / "factory"
    (repo / "server/tests").mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / ".factory").mkdir()
    (repo / ".factory/factory.json").write_text("{}\n")
    (repo / "server/tests/test_context.py").write_text(
        "from pathlib import Path\nimport json\n\n"
        "def test_single_repo_custom_docs():\n"
        "    parent = Path(__file__).resolve().parents[3]\n"
        "    docs = json.loads((parent / '.factory/factory.json').read_text())['docs']\n"
        "    assert (parent / docs['tickets'] / 'T.md').read_text() == 'custom ticket\\n'\n"
        "    assert (parent / docs['epics'] / 'R.md').read_text() == 'custom requirement\\n'\n"
        "    assert (parent / docs['reports'] / 'report.md').read_text() == 'custom report\\n'\n"
        "    assert (parent / docs['escapes']).read_text() == 'custom escape ledger\\n'\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "baseline")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "unrelated.txt").write_text("head\n")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-qm", "head")
    command = f"{sys.executable} -m pytest tests/test_context.py -v -p no:cacheprovider"
    result = run(repo, "--base", base, "--gate-cmd", command, "--ticket", "SRV-FEAT-563")
    logs = repo / "docs/reports/SRV-FEAT-563"
    base_log = next(logs.glob("*-document-gate-merge-base-*.log"))
    head_log = next(logs.glob("*-document-gate-20*.log"))
    for name, log in (("base", base_log), ("head", head_log)):
        said = log.read_text()
        assert "test_single_repo_custom_docs PASSED" in said, f"{name} lost custom docs:\n{said}"
    assert result.returncode == 0 and VERDICT.search(result.stdout).groups() == ("0", "0", "0"), (
        result.stdout + result.stderr)


def test_an_unwritable_source_worktree_registry_still_has_a_git_baseline(tmp_path):
    """The baseline uses real history without registering a worktree in the source repository."""
    base, head = capture("453-base"), capture("453-head")
    repo = make_checkout(tmp_path, base, head)
    registry = repo / ".git/worktrees"
    registry.mkdir()
    registry.chmod(0o500)
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "worktree", "list", "--porcelain"),
              git(repo, "show-ref"))
    try:
        result = run(repo, *GATE, "--ticket", "SRV-FEAT-558")
    finally:
        registry.chmod(0o700)
    assert result.returncode == 0, result.stdout + result.stderr
    assert VERDICT.search(result.stdout).groups() == ("18", "18", "0")
    assert (git(repo, "rev-parse", "HEAD"), git(repo, "worktree", "list", "--porcelain"),
            git(repo, "show-ref")) == before
    assert not list(registry.iterdir()), "the baseline registered a source worktree"
    assert len(list((repo / "docs/reports/SRV-FEAT-558").glob("*.log"))) == 2


@pytest.mark.repository
def test_baseline_keeps_the_sources_local_main_for_history_gate(tmp_path):
    repo = tmp_path / "history"
    (repo / "server/tests").mkdir(parents=True)
    git(repo.parent, "init", "-b", "main", str(repo))
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / "server/tests/test_main_context.py").write_text(
        "import subprocess\nfrom pathlib import Path\n\n"
        "def test_shared_main_violation():\n"
        "    root = Path(__file__).resolve().parents[2]\n"
        "    ref = subprocess.run(['git', 'rev-parse', '--verify', 'main'], cwd=root, "
        "capture_output=True, text=True)\n"
        "    assert ref.returncode == 0, 'there is no main in this checkout'\n"
        "    subject = subprocess.check_output(['git', 'log', '-1', '--format=%s', 'main'], "
        "cwd=root, text=True).strip()\n"
        "    assert subject != 'unlabelled shared main', 'shared main violation'\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "baseline")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "ticket/T")
    (repo / "unrelated.txt").write_text("ticket head\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ticket head")
    git(repo, "checkout", "main")
    (repo / "server/offense.txt").write_text("main-only change\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "unlabelled shared main")
    git(repo, "checkout", "ticket/T")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "main"),
              git(repo, "rev-parse", "origin/main"), git(repo, "show-ref"))
    assert len(set(before[:3])) == 3

    command = f"{sys.executable} -m pytest tests/test_main_context.py -v -p no:cacheprovider"
    result = run(repo, "--gate-cmd", command, "--ticket", "SRV-FEAT-573")
    logs = repo / "docs/reports/SRV-FEAT-573"
    assert logs.is_dir(), result.stdout + result.stderr
    base_log = next(logs.glob("*-document-gate-merge-base-*.log"))
    head_log = next(logs.glob("*-document-gate-20*.log"))
    for name, log in (("base", base_log), ("head", head_log)):
        said = log.read_text()
        assert "shared main violation" in said, f"{name} lost local main:\n{said}"
        assert "E       AssertionError: there is no main in this checkout" not in said
    assert result.returncode == 0 and VERDICT.search(result.stdout).groups() == ("1", "1", "0"), (
        result.stdout + result.stderr)
    assert (git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "main"),
            git(repo, "rev-parse", "origin/main"), git(repo, "show-ref")) == before


def test_an_unfinished_gate_or_a_missing_merge_base_is_exit_2_not_a_pass(tmp_path):
    """MANDATORY NEGATIVE — criterion 6. Three ways the question cannot be answered, and none of
    them may be a pass or leave a log behind that could be quoted as one."""
    base, head = capture("453-base"), capture("453-head")
    cut = "=================================== FAILURES"

    # (a) the HEAD side was killed before its summary line — the GATE_EXIT= lie, 2026-08-27.
    repo = make_checkout(tmp_path, base, head[:head.index(cut)], name="killed-head")
    r = run(repo, *GATE, "--ticket", "SRV-FEAT-479")
    assert r.returncode == 2, f"a killed head gate did not exit 2:\n{r.stdout}{r.stderr}"
    said = (r.stdout + r.stderr).lower()
    assert "head" in said and "merge-base" not in said, \
        f"exit 2 did not name the HEAD side as the one that did not finish:\n{r.stdout}{r.stderr}"
    assert not VERDICT.search(r.stdout), f"exit 2 printed a verdict line:\n{r.stdout}"
    assert not (repo / "docs").exists(), "a gate that did not finish left a log that could be quoted"

    # (b) the BASE side was killed. The naming must say which side, not just that one failed.
    repo = make_checkout(tmp_path, base[:base.index(cut)], head, name="killed-base")
    r = run(repo, *GATE, "--ticket", "SRV-FEAT-479")
    assert r.returncode == 2, f"a killed merge-base gate did not exit 2:\n{r.stdout}{r.stderr}"
    assert "merge-base" in (r.stdout + r.stderr).lower(), \
        f"exit 2 did not name the MERGE-BASE side as the one that did not finish:\n{r.stdout}{r.stderr}"
    assert not (repo / "docs").exists(), "a gate that did not finish left a log that could be quoted"

    # (c) no merge-base at all: a branch with no `main` and no `origin/main`.
    lonely = tmp_path / "lonely"
    (lonely / "server").mkdir(parents=True)
    git(tmp_path, "init", "-b", "ticket/T", str(lonely))
    git(lonely, "config", "user.email", "lane@factory.test")
    git(lonely, "config", "user.name", "lane")
    (lonely / "server" / "gate-out.txt").write_text(head)
    git(lonely, "add", "-A")
    git(lonely, "commit", "-m", "only")
    r = run(lonely, *GATE, "--ticket", "SRV-FEAT-479")
    assert r.returncode == 2, f"a repository with no merge-base did not exit 2:\n{r.stdout}{r.stderr}"
    assert not (lonely / "docs").exists(), "a question that could not be asked left a log"

    # (d) an unrelated history: `main` exists and shares no commit with the branch.
    unrelated = make_checkout(tmp_path, base, head, name="unrelated")
    git(unrelated, "branch", "-D", "main")
    git(unrelated, "checkout", "--orphan", "main")
    git(unrelated, "commit", "-m", "unrelated root")
    git(unrelated, "checkout", "ticket/T")
    assert subprocess.run(["git", "merge-base", "HEAD", "main"], cwd=str(unrelated),
                          capture_output=True).returncode != 0, \
        "this fixture's two histories are related, so it is not the case it claims to be"
    r = run(unrelated, *GATE, "--ticket", "SRV-FEAT-479")
    assert r.returncode == 2, f"an unrelated history did not exit 2:\n{r.stdout}{r.stderr}"
    assert not (unrelated / "docs").exists(), "a question that could not be asked left a log"


def test_a_run_that_collapsed_at_collection_is_not_a_pass(tmp_path):
    """The same rule as criterion 6, at the shape that carries a summary line anyway.

    A real capture of the real gate with one import broken: 24 modules fail to collect, no test
    runs, and pytest still prints `185 deselected, 6 warnings, 24 errors in 5.14s` — a summary
    line, so the summary-line guard alone lets it through as an EMPTY failing set, `added: 0`,
    exit 0. It is `!!! Interrupted: 24 errors during collection !!!` that says the cases never
    ran, and a gate whose cases never ran has not passed.
    """
    base, head = capture("453-base"), capture("collection-error")
    assert SUMMARY_LINE.search(head), \
        "this fixture has no pytest summary line, so it is not the shape this case is about"
    assert "FAILED" not in head, "this fixture reports failures, so an empty set would be correct"

    repo = make_checkout(tmp_path, base, head, name="collapsed")
    r = run(repo, *GATE, "--ticket", "SRV-FEAT-479")

    assert r.returncode == 2, \
        f"a gate that collapsed at collection did not exit 2:\n{r.stdout}{r.stderr}"
    assert not VERDICT.search(r.stdout), f"exit 2 printed a verdict line:\n{r.stdout}"
    assert not (repo / "docs").exists(), "a run whose cases never ran left a log to be quoted"


@pytest.mark.slow
def test_the_real_gate_end_to_end_answers_for_this_branch():
    """No `--gate-cmd`: the real default, the real `pytest tests/ -m repository`, run twice —
    once here and once in a scratch worktree at this branch's merge-base. Two minutes, and it is
    the only case in this file that exercises the default command, the default base resolution
    and the worktree together.

    **RED inside a lane, and owned by `SRV-FIX-481`.** A lane's mount namespace binds the
    repository's git common directory read-only with only `worktrees/<lane>` writable, so
    `git worktree add` — the mechanism `SRV-FEAT-479` §1 pre-decided — cannot register a worktree
    and the script correctly exits 2 saying so. It is left failing rather than skipped: this case
    is `SRV-FIX-481`'s own criterion 2, and a skip is a debt nobody can see.
    """
    def scratch():
        # Other lanes of this repository are claimed and released while this runs, so the
        # question is what THIS script left, not how many worktrees exist.
        return [ln for ln in git(REPO, "worktree", "list").splitlines() if "docgate-" in ln]

    assert scratch() == [], f"a scratch worktree was already there before the run: {scratch()}"
    r = run(REPO)
    assert r.returncode in (0, 1), \
        f"the real gate could not be answered on this branch:\n{r.stdout}{r.stderr}"
    assert VERDICT.search(r.stdout), f"no verdict line:\n{r.stdout}{r.stderr}"
    assert scratch() == [], f"the end-to-end run left a scratch worktree behind: {scratch()}"


# ── SRV-FEAT-498 · the process change the gate names ──────────────────────────────────────────
#
# A change to a skill, to `CLAUDE.md`, to the ticket template or to the manifest is a change to how
# every future round is run. `docgate.py` derives that set from the repository it is asked about —
# everything under `.claude/`, plus the documents `_TEMPLATE.md` §2's `FIRST read:` sentence names,
# plus the two files that derivation itself reads — and says which of them this branch touched.

FACTORY_ROOT = REPO
TEMPLATE = FACTORY_ROOT / "docs" / "tickets" / "_TEMPLATE.md"
MANIFEST = FACTORY_ROOT / ".factory" / "factory.json"
THE_EVAL = "test_every_case_the_ledger_names_is_collected"


def make_process_checkout(tmp_path, base_out, head_out, touches, name="process"):
    """A repository carrying THIS repository's real template, manifest and named documents.

    The derivation under test reads `_TEMPLATE.md` §2 and the manifest, so a fixture that invents
    them would be judging the script against a template this repository does not have. Both are
    copied byte-for-byte; `touches` is the list of paths the ticket branch then changes.
    """
    repo = tmp_path / name
    (repo / "server").mkdir(parents=True)
    (repo / "docs" / "tickets").mkdir(parents=True)
    (repo / ".factory").mkdir(parents=True)
    (repo / ".claude" / "skills" / "run-ticket").mkdir(parents=True)
    (repo / ".codex").mkdir(parents=True)

    shutil.copy(TEMPLATE, repo / "docs" / "tickets" / "_TEMPLATE.md")
    shutil.copy(MANIFEST, repo / ".factory" / "factory.json")
    for rel in first_read_names():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FACTORY_ROOT / rel, repo / rel)
    (repo / ".claude" / "skills" / "run-ticket" / "SKILL.md").write_text("the standing round\n")
    (repo / "docs/technical/workflow").mkdir(parents=True, exist_ok=True)
    (repo / "docs/technical/workflow/WORKFLOW.md").write_text("# Shared workflow\n")
    (repo / "AGENTS.md").write_text("Follow [the shared workflow](docs/technical/workflow/WORKFLOW.md).\n")
    (repo / ".codex" / "README.md").write_text("Codex discovers ../AGENTS.md.\n")

    git(repo.parent, "init", "-b", "main", str(repo))
    git(repo, "config", "user.email", "lane@factory.test")
    git(repo, "config", "user.name", "lane")
    (repo / "server" / "gate-out.txt").write_text(base_out)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")

    git(repo, "checkout", "-b", "ticket/T")
    (repo / "server" / "gate-out.txt").write_text(head_out)
    for rel in touches:
        p = repo / rel
        p.write_text(p.read_text() + "\nchanged by the ticket\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "head")
    return repo


def first_read_names():
    """The paths `_TEMPLATE.md` §2's `FIRST read:` sentence names, read off the template.

    The same derivation the script performs, run here against the real template so the fixture
    carries whatever that sentence names today rather than what it named when this was written.
    """
    body = re.search(r"^## 2\b.*$", TEMPLATE.read_text(), re.M)
    assert body, f"{TEMPLATE} has no §2, so there is no standing process to read a set out of"
    section = TEMPLATE.read_text()[body.end():]
    nxt = re.search(r"^## ", section, re.M)
    section = section[:nxt.start()] if nxt else section
    sentence = re.search(r"FIRST read:(.*?`\.)", section, re.S)
    assert sentence, (
        f"{TEMPLATE} §2 has no `FIRST read:` sentence. The set of documents every round begins "
        "with is derived from that sentence, and without it the derivation is silently empty — "
        "which is a green tick over a gate that stopped covering `CLAUDE.md`.")
    named = [s for s in re.findall(r"`([^`]+)`", sentence.group(1))
             if (FACTORY_ROOT / s).is_file()]
    assert named, (
        f"{TEMPLATE} §2's `FIRST read:` sentence names no file that exists: "
        f"{re.findall(r'`([^`]+)`', sentence.group(1))}")
    return named


def test_a_process_change_is_named_by_the_gate(tmp_path):
    """Criterion 2 of `SRV-FEAT-498`. The gate says which process files a branch moved.

    A person who edits a skill and `CLAUDE.md` runs `docgate.py` and is told, before any verdict,
    that this branch changes how every future round is run and which files it changed. A branch
    that touches neither reads `process change: none` — and the ledger eval runs on both, because
    it is a `repository` case and `pytest tests/ -m repository` is not conditional on anything.
    """
    base, head = capture("453-base"), capture("453-head")
    named = first_read_names()
    skill = ".claude/skills/run-ticket/SKILL.md"

    repo = make_process_checkout(tmp_path, base, head, [skill, named[0]])
    r = run(repo, *GATE)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    assert re.search(r"^process change: 2 files$", r.stdout, re.M), \
        f"the gate did not count the process files this branch changed:\n{r.stdout}"
    for rel in (skill, named[0]):
        assert rel in r.stdout, f"the gate did not name {rel}:\n{r.stdout}"

    quiet = make_process_checkout(tmp_path, base, head, [], name="quiet")
    q = run(quiet, *GATE)
    assert q.returncode == 0, f"{q.stdout}{q.stderr}"
    assert re.search(r"^process change: none$", q.stdout, re.M), \
        f"a branch that touched no process file was not told so:\n{q.stdout}"

    selected = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-m", "repository", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(SERVER), capture_output=True, text=True, timeout=600)
    assert THE_EVAL in selected.stdout, (
        f"`pytest tests/ -m repository` does not collect {THE_EVAL}, so the gate the two runs "
        f"above compare never runs the ledger eval:\n{selected.stdout[-2000:]}{selected.stderr[-2000:]}")


@pytest.mark.parametrize("name", ["codex-process", ".factory/worktrees/lane/01"])
def test_codex_adapters_and_shared_workflow_are_process_files(tmp_path, name):
    """Codex entry points and their provider-neutral target are part of the audited process."""
    base, head = capture("453-base"), capture("453-head")
    touched = ["AGENTS.md", ".codex/README.md", "docs/technical/workflow/WORKFLOW.md"]
    repo = make_process_checkout(tmp_path, base, head, touched, name=name)

    r = run(repo, *GATE)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    assert re.search(r"^process change: 3 files$", r.stdout, re.M), r.stdout
    for rel in touched:
        assert rel in r.stdout, f"the gate did not name {rel}:\n{r.stdout}"


# ── SRV-FIX-520 · the eval has no root rule of its own ────────────────────────────────────────

DEVELOPERS_CASES = ("test_every_case_the_ledger_names_is_collected",
                    "test_a_process_change_that_loses_a_named_case_is_refused")


def developers_checkout(tmp_path, rel):
    """This checkout one level below a directory keeping a manifest that declares no ledger.

    The developer's placement, and the one no gate of `SRV-FEAT-498` ever ran in: the parent-wins
    root rule fires only here, because in a lane the checkout's parent is `lane/` and never keeps
    a manifest. It is a git checkout because the eval's own placements derive their copy from the
    index (`SRV-FIX-525`).
    """
    import test_escapes_are_evals as evals

    inner = evals.placement(tmp_path, rel, parent_manifest=evals.MONOREPO_WITHOUT_A_LEDGER)
    assert (inner.parent / evals.MANIFEST_REL).is_file(), \
        "the fixture's parent keeps no manifest, so the parent-wins rule never fires in it"
    git(inner.parent, "init", "-b", "main", str(inner))
    git(inner, "config", "user.email", "lane@factory.test")
    git(inner, "config", "user.name", "lane")
    git(inner, "add", "-A")
    git(inner, "commit", "--quiet", "-m", "the developer's checkout")
    return inner


def test_the_ledger_eval_is_green_from_the_developers_checkout(tmp_path):
    """Criterion 4 of `SRV-FIX-520`. One door, and the placement that had the hole.

    Two halves, and both are needed. The eval keeps no root rule of its own any more — asserted by
    IDENTITY against `test_catalog`'s own objects, because a local function of the same name would
    satisfy a check written against the name. And the two cases `SRV-FIX-520` found red are run,
    by the real pytest, from a checkout whose PARENT keeps a manifest declaring no ledger: the
    developer's checkout, and the one placement no gate of `SRV-FEAT-498` ever ran in.
    """
    import test_catalog
    import test_escapes_are_evals as evals

    src = (SERVER / "tests" / "test_escapes_are_evals.py").read_text()
    assert not re.search(r"^def repo_root\b", src, re.M), (
        "the eval still declares a root rule of its own. The copy IS the defect: it carried the "
        "parent-wins rule and not the enumeration behind it, and deleting it is what this ticket is")
    assert evals._repo_root is test_catalog._repo_root, \
        "the eval does not resolve its root through the catalog's own door"
    assert evals.manifests_of_this_monorepo is test_catalog.manifests_of_this_monorepo, \
        "the eval enumerates the monorepo with something other than the catalog's own enumeration"

    inner = developers_checkout(tmp_path, "dev/inner")

    r = evals.run_eval(inner, *(f"tests/test_escapes_are_evals.py::{c}" for c in DEVELOPERS_CASES),
                       scratch=False)
    for case in DEVELOPERS_CASES:
        assert re.search(rf"^PASSED .*::{case}$", r.stdout, re.M), (
            f"`{case}` is not green from the developer's checkout — the two cases this ticket "
            f"found red are its criterion:\n{r.stdout[-8000:]}{r.stderr[-4000:]}")
    assert r.returncode == 0, f"{r.stdout[-8000:]}{r.stderr[-4000:]}"


# ── SRV-FIX-525 · the placement cases, run where the tool has actually been ────────────────────

PLACEMENT_CASES = ("test_the_eval_reads_this_repositorys_ledger_from_inside_a_monorepo",
                   "test_three_placements_read_one_ledger",
                   "test_every_declared_ledger_is_swept_and_no_manifest_is_red")


def test_the_placement_cases_are_green_from_the_developers_checkout(tmp_path):
    """Criterion 3 of `SRV-FIX-525`. The one placement where the escape was visible.

    `SRV-FIX-520`'s three cases build their placements by copying the checkout they run in, and
    every gate of that round ran in a lane — where `.factory/runs` does not exist. Here the
    checkout they are run from is a developer's: one level below a directory holding a manifest
    that declares no ledger, with a run's `stdin` FIFO, a live lane worktree and the tool's id
    file beside it. That is the state `docs/operations/ESCAPES.md`'s 2026-09-11 row was measured in, and the
    three cases are run by the real pytest from inside it.
    """
    import test_escapes_are_evals as evals

    inner = developers_checkout(tmp_path, "dev/inner")

    run_dir = inner / ".factory" / "runs" / "run-26-ea321563"
    run_dir.mkdir(parents=True)
    os.mkfifo(run_dir / "stdin")
    (inner / ".factory" / "worktrees" / "lane" / "01" / "server").mkdir(parents=True)
    (inner / ".factory" / "ids.json").write_text('{"SRV-FIX": 525}')
    assert (run_dir / "stdin").is_fifo(), \
        "the fixture left no FIFO under `.factory/runs`, so it is not a checkout the tool has run in"
    assert git(inner, "status", "--short", "--", ".factory") == "", \
        "git can see the tool's state here, so this fixture does not carry it the way a checkout does"

    r = evals.run_eval(inner, *(f"tests/test_escapes_are_evals.py::{c}" for c in PLACEMENT_CASES),
                       scratch=False)
    for case in PLACEMENT_CASES:
        assert re.search(rf"^PASSED .*::{case}$", r.stdout, re.M), (
            f"`{case}` is not green from a checkout the tool has run in — the three cases "
            f"`SRV-FIX-525` found red are its criterion:\n{r.stdout[-8000:]}{r.stderr[-4000:]}")
    assert r.returncode == 0, f"{r.stdout[-8000:]}{r.stderr[-4000:]}"


# ── SRV-FIX-532 criterion G — the gate is green for what this round owns ───────────────────────
#
# §1 G: *"`cd server && pytest tests/ -m repository -q` has NO failure this ticket's clusters own,
# quoted; `server/tools/docgate.py` reports added: 0."*
#
# Both halves are asserted, and the OWNED SET is derived from §1 itself — every case named after an
# `Evidence:` line in the ticket — rather than from a list written here. A list would be bounded by
# what its author remembered, which is the first way a check lies.


def this_tickets_own_cases():
    """Every case §1 of `SRV-FIX-532` names after `Evidence:`, read out of the ticket document."""
    import re
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    found = sorted(here.glob("docs/tickets/SRV-FIX-532-*.md"))
    assert len(found) == 1, f"SRV-FIX-532's document is not one file: {found}"
    named = set(re.findall(r"server/(tests/test_[a-z_]+\.py)::([a-z0-9_]+)",
                           found[0].read_text()))
    assert len(named) > 50, (
        f"§1 names {len(named)} cases, which is not this ticket — the sweep is reading the wrong "
        f"document")
    return {f"{f}::{n}" for f, n in named}


def the_newest_docgate_capture(evidence_dir):
    """`(path, head_sha, added)` for the newest readable docgate run, or `(None, why, None)`.

    `docgate.py` cannot answer from a lane: it registers a worktree at the merge-base and a lane
    binds the git common directory read-only (`SRV-FIX-481`). So the answer comes from a run the
    operator made where it can, and the two lines that make such a log an argument rather than a
    story are its own:

        docgate: base <sha8> (…) · head <sha8>
        base: N red · head: M red · added: K

    The directory holds pytest logs under the same name from earlier rounds; a file that carries
    no `docgate:` head line is not a docgate run and is passed over rather than mis-read.
    """
    import re as _re

    seen = sorted(Path(evidence_dir).glob("SRV-FIX-532-document-gate-*.log"),
                  key=lambda f: (f.stat().st_mtime, f.name), reverse=True)
    if not seen:
        return None, f"no SRV-FIX-532-document-gate-*.log under {evidence_dir}", None
    for log in seen:
        text = log.read_text(errors="replace")
        head = _re.search(r"^docgate: base \S+.*· head ([0-9a-f]{7,40})\s*$", text, _re.M)
        added = _re.search(r"^base: \d+ red · head: \d+ red · added: (\d+)\s*$", text, _re.M)
        if head and added:
            return log, head.group(1), int(added.group(1))
    return None, (f"{len(seen)} file(s) under {evidence_dir} match the docgate name and none "
                  "carries a `docgate: base … · head …` line with a verdict beside it, so none of "
                  "them is a docgate run this case can read"), None


def answered_by_the_operators_docgate(repo, why):
    """§1 G, from a placement that cannot run the gate — decided on a capture, never skipped.

    A skip reads as a pass and this repository has paid for that twice on this very criterion, so
    the unanswerable placement asks a narrower question it CAN answer: *did a docgate run on a tip
    this branch has since only added documents to say `added: 0`?* Every clause of that is checked
    against git rather than taken from the log's own sentence — the tip it names must be an
    ancestor of `HEAD`, and every path changed since must be under `docs/`, because a code change
    after the run makes the run about a different branch.
    """
    import subprocess

    evidence = Path(repo) / "docs" / "reports" / "SRV-FIX-532"
    log, head, added = the_newest_docgate_capture(evidence)
    assert log is not None, (
        "the document gate cannot run here — " + why
        + f"; and {head}. §1 G is then unanswered, and an unanswered criterion is not a met one."
        + " The operator re-runs `tools/docgate.py` from the developer's checkout after this"
          " branch's last code commit and commits the log beside this report.")

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=120)

    known = git("cat-file", "-e", head + "^{commit}")
    assert known.returncode == 0, (
        f"{log} records head {head}, which is not a commit in this repository, so the run it "
        "reports was made somewhere this branch cannot see.")

    ancestor = git("merge-base", "--is-ancestor", head, "HEAD")
    assert ancestor.returncode == 0, (
        f"{log} records head {head}, which is NOT an ancestor of HEAD ({git('rev-parse', 'HEAD').stdout.strip()}). "
        "The gate was answered about a branch this one is not a continuation of.")

    changed = [line for line in git("diff", "--name-only", head, "HEAD").stdout.splitlines() if line]
    code = [f for f in changed if not f.startswith("docs/")]
    assert not code, (
        f"{log} answered the gate at {head}, and this branch has changed code since:\n  "
        + "\n  ".join(code)
        + "\nThe document gate is about what the tree holds, so a run made before these is not an "
          "answer about this tree. Re-run docgate from the developer's checkout on this tip.")

    assert added == 0, (
        f"{log} is the newest docgate run on this branch and it reports `added: {added}` — this "
        "branch adds that many failures to the document gate, and §1 G asks for none it owns. The "
        "log names each one under its `NEW CASE` / `NEW ID` lines.")


@pytest.mark.repository
def test_the_document_gate_is_green_after_the_p0_round():
    """§1 G — no case this ticket's clusters own is red in the document gate.

    The gate is red on `main` for reasons older than any one ticket, which is why §1 asks for
    *no failure this ticket's clusters own* rather than *no failure*: a ticket cannot be asked to
    close debts nobody assigned it, and a ticket that quietly tolerated its OWN red would be the
    escape this whole round exists to answer.

    So the owned set is derived from §1's `Evidence:` lines and the gate's failures are read off
    the run — and the case asserts the gate RAN, because a sweep that collected nothing would
    report no owned failure and mean nothing.
    """
    import subprocess
    import sys
    from pathlib import Path

    from test_catalog import the_enclosing_monorepo_or_why_not, _this_repository

    # Some of the rules this ticket owns are about the repository THIS one is cloned into, and a
    # lane binds the repository it is a lane of and nothing over it (`S4`). Where that second
    # repository is unreachable those rules skip rather than answer, so a green gate here would
    # be green about a smaller set than §1 names — which is the shape SRV-FIX-532 round 2 shipped
    # and round 4 is fixing. The criterion is answered from the developer's checkout, and this
    # placement says which path it could not read instead of pretending.
    enclosing, why = the_enclosing_monorepo_or_why_not()
    if enclosing is None:
        answered_by_the_operators_docgate(_this_repository(), why)
        return

    server = Path(__file__).resolve().parents[1]
    # This case IS in the repository gate, and the gate is what it runs — so the inner run has to
    # be told to leave it out or it re-enters itself and never terminates. `test_docgate.py`'s
    # other nested-gate case carries a guard of its own for the same reason.
    mine = "tests/test_docgate.py::test_the_document_gate_is_green_after_the_p0_round"
    out = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-m", "repository",
                          "-q", "-p", "no:cacheprovider", "--deselect", mine],
                         cwd=str(server), capture_output=True, text=True, timeout=3600)
    said = out.stdout + out.stderr

    passed = passed_in_final_pytest_summary(said)
    assert passed > 20, (
        f"the document gate passed only {passed} case(s), so it collected almost nothing and a "
        f"green answer from it is about nothing:\n{said[-2000:]}")

    failed = {line.split()[1].split(" ")[0] for line in said.splitlines()
              if line.startswith("FAILED ")}
    mine = this_tickets_own_cases()
    owned_and_red = sorted(f for f in failed if f.split(" ")[0] in mine)

    assert not owned_and_red, (
        "these cases are named after an `Evidence:` line in §1 of SRV-FIX-532 and are RED in the "
        "document gate:\n  " + "\n  ".join(owned_and_red)
        + f"\n\nThe gate reported {len(failed)} failure(s) in all; the rest are not this "
          f"ticket's to close, and §1 G asks only about the ones that are.")
