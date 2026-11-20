"""The declared REVIEW command selects a transport without changing finding semantics."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[2]
CAPTURE = Path(__file__).parent / "fixtures/cli-review-codex-2026-09-18.json"
FINDINGS = "minor app/store.cpp:3 a minor finding.\nblocker app/store.cpp:7 a blocking finding.\n"


@pytest.fixture
def review_repo(tmp_path):
    root = tmp_path / "reviewed"
    (root / "docs/technical/workflow").mkdir(parents=True)
    shutil.copy(FACTORY / "docs/technical/workflow/REVIEW.md", root / "docs/technical/workflow/REVIEW.md")
    (root / "review.sh").write_bytes((FACTORY / ".claude/scripts/review.sh").read_bytes())
    (root / ".claude/scripts").mkdir(parents=True)
    shutil.copy(root / "review.sh", root / ".claude/scripts/review.sh")
    for args in (("init", "-b", "main"), ("config", "user.name", "review test"),
                 ("config", "user.email", "review@test.local")):
        subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
    target = root / "changed.txt"
    target.write_text("before\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-qb", "ticket/REVIEW"], cwd=root, check=True)
    target.write_text("after\n")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=root, check=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    recorder = f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
record = {{
    'program': pathlib.Path(sys.argv[0]).name, 'argv': args,
    'stdin': sys.stdin.read(), 'home': os.environ.get('CODEX_HOME'),
    'cwd': str(pathlib.Path.cwd()),
}}
if os.environ.get('REVIEW_TEST_READ_PATH'):
    diff = subprocess.check_output([
        'git', 'diff', 'main...HEAD', '--', os.environ['REVIEW_TEST_READ_PATH']])
    record['readback_bytes'] = len(diff)
    record['readback_has_marker'] = os.environ['REVIEW_TEST_READ_MARKER'].encode() in diff
pathlib.Path(os.environ['REVIEW_TEST_LOG']).write_text(json.dumps(record))
print('diagnostic from reviewer', file=sys.stderr)
if os.environ.get('REVIEW_TEST_EXIT'):
    sys.exit(int(os.environ['REVIEW_TEST_EXIT']))
answer = os.environ.get('REVIEW_TEST_ANSWER', {FINDINGS!r})
if pathlib.Path(sys.argv[0]).name == 'codex':
    print('blocker wrong-stream.cpp:1 this is not the final answer.')
    if os.environ.get('REVIEW_TEST_MISSING') != '1':
        pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(answer)
else:
    print(answer, end='')
"""
    for name in ("claude", "codex"):
        tool = bins / name
        tool.write_text(recorder)
        tool.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("FACTORY_REVIEW_")}
    env.update(PATH=f"{bins}:{os.environ['PATH']}", REVIEW_TEST_LOG=str(tmp_path / "called.json"))
    return root, env


def run_review(review_repo, *, driver="codex", base="main", cwd=None, **values):
    root, env = review_repo
    env = dict(env, FACTORY_REVIEW_DRIVER=driver, **values)
    return subprocess.run(["sh", str(root / "review.sh"), base], cwd=cwd or root, env=env,
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("nested", [False, True])
def test_review_sends_complete_actionable_diff_and_inventory_without_report_hunks(review_repo, nested):
    root, env = review_repo
    changes = {
        "server/app/store.cpp": "actionable production change\n",
        "server/tests/test_store.py": "actionable test change\n",
        "server/tests/fixtures/recorded.log": "actionable protocol capture\n",
        ".claude/skills/run-ticket/SKILL.md": "actionable process change\n",
        "client/web/src/types/api.ts": "actionable generated change\n",
        "docs/reports-policy.md": "actionable document outside reports directory\n",
        "docs/reports/TICKET-report.md": "excluded report narrative\n",
        "docs/reports/TICKET/tier1.log": "excluded raw evidence\n" * 1000,
    }
    for name, body in changes.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "source and evidence"], cwd=root, check=True)
    reports = {name: (root / name).read_bytes() for name in changes if name.startswith("docs/reports/")}
    before = subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
    ran = run_review(review_repo, cwd=root / "server" if nested else root)
    assert ran.returncode == 0, ran.stderr
    called = json.loads(Path(env["REVIEW_TEST_LOG"]).read_text())
    prompt = called["stdin"]
    inventory = subprocess.check_output(["git", "diff", "--name-status", "main...HEAD"],
                                        cwd=root, text=True)
    expected_diff = subprocess.check_output([
        "git", "diff", "main...HEAD", "--", ".", ":(top,exclude)docs/reports/**",
    ], cwd=root, text=True)
    assert inventory in prompt
    assert expected_diff in prompt
    assert called["cwd"] == str(root)
    for name, body in changes.items():
        assert name in prompt
        if name.startswith("docs/reports/"):
            assert body.splitlines()[0] not in prompt
            assert f"diff --git a/{name} b/{name}" not in prompt
        else:
            assert f"diff --git a/{name} b/{name}" in prompt
            assert body.rstrip() in prompt
    assert "Read evidence by its repository-relative path" in prompt
    assert "read-only git" in prompt
    assert "review: omitted evidence hunks from docs/reports/" in ran.stderr
    assert ran.stderr.count("review: outcome=ran") == 1
    assert {name: (root / name).read_bytes() for name in reports} == reports
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=root) == before


def test_review_reports_only_diff_does_not_launch_a_reviewer_or_change_evidence(review_repo):
    root, env = review_repo
    report = root / "docs/reports/TICKET/raw.log"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"retained report-only evidence\n")
    subprocess.run(["git", "add", "docs/reports"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "evidence only"], cwd=root, check=True)
    evidence = report.read_bytes()
    before = subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
    ran = run_review(review_repo, base="HEAD~1", cwd=root / "docs")
    assert ran.returncode == 0 and not ran.stdout
    assert ran.stderr.count("review: outcome=nothing_to_review") == 1
    assert "review: omitted evidence hunks from docs/reports/" in ran.stderr
    assert "no actionable diff" in ran.stderr
    assert not Path(env["REVIEW_TEST_LOG"]).exists()
    assert report.read_bytes() == evidence
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=root) == before


def test_codex_review_uses_declared_model_home_and_final_file(review_repo, tmp_path):
    selected = tmp_path / "selected config"
    selected.mkdir()
    ran = run_review(review_repo, FACTORY_REVIEW_MODEL="declared-model", CODEX_HOME=str(selected))
    assert ran.returncode == 0, ran.stderr
    called = json.loads(Path(review_repo[1]["REVIEW_TEST_LOG"]).read_text())
    assert called["program"] == "codex", called
    assert called["home"] == str(selected)
    args = called["argv"]
    assert args[0] == "exec" and args[-1] == "-", args
    assert args[args.index("--model") + 1] == "declared-model", args
    assert args[args.index("--sandbox") + 1] == "read-only", args
    assert "--ephemeral" in args and "--json" in args and "--output-last-message" in args
    assert "+after" in called["stdin"] and "docs/technical/MODEL.md" in called["stdin"]
    assert ran.stdout == "\n".join(reversed(FINDINGS.splitlines())) + "\n"
    assert ran.stderr.count("review: outcome=ran") == 1
    assert "diagnostic from reviewer" in ran.stderr


@pytest.mark.parametrize("code", [3, 37])
def test_codex_review_preserves_reviewer_failure(review_repo, code):
    ran = run_review(review_repo, REVIEW_TEST_EXIT=str(code))
    assert ran.returncode == code and "review: outcome=reviewer_failed" in ran.stderr
    assert "diagnostic from reviewer" in ran.stderr and not ran.stdout


def test_codex_review_refuses_unreadable_final_answer(review_repo):
    ran = run_review(review_repo, REVIEW_TEST_ANSWER="The diff looks good to me.\n")
    assert ran.returncode == 3 and "review: outcome=unreadable_answer" in ran.stderr
    assert not ran.stdout


def test_codex_review_requires_the_final_answer_artifact(review_repo):
    ran = run_review(review_repo, REVIEW_TEST_MISSING="1")
    assert ran.returncode == 3 and "review: outcome=unreadable_answer" in ran.stderr
    assert not ran.stdout


def test_codex_review_allows_an_explicitly_empty_final_answer(review_repo):
    ran = run_review(review_repo, REVIEW_TEST_ANSWER="")
    assert ran.returncode == 0 and "review: outcome=ran" in ran.stderr
    assert not ran.stdout


@pytest.mark.parametrize("base,outcome,code", [
    ("HEAD", "nothing_to_review", 0), ("missing-base", "no_base", 2),
])
def test_review_does_not_call_a_provider_without_a_diff(review_repo, base, outcome, code):
    ran = run_review(review_repo, base=base)
    assert ran.returncode == code and f"review: outcome={outcome}" in ran.stderr
    assert not Path(review_repo[1]["REVIEW_TEST_LOG"]).exists()


def test_review_refuses_an_unknown_driver_without_falling_back(review_repo):
    ran = run_review(review_repo, driver="unknown-driver")
    assert ran.returncode == 2 and "review: outcome=reviewer_failed" in ran.stderr
    assert "unknown-driver" in ran.stderr
    assert not Path(review_repo[1]["REVIEW_TEST_LOG"]).exists()


def test_review_retains_the_recorded_claude_default(review_repo):
    root, env = review_repo
    ran = subprocess.run(["sh", "review.sh"], cwd=root, env=env,
                         capture_output=True, text=True, timeout=20)
    called = json.loads(Path(env["REVIEW_TEST_LOG"]).read_text())
    recorded = json.loads((CAPTURE.parent / "cli-review-2026-09-11-review-sh-prompt-to-findings.json").read_text())
    assert called["program"] == "claude" and called["argv"] == recorded["argv_tail"]
    assert ran.returncode == recorded["rc"] == 0


def test_recorded_codex_review_confirms_the_adapter_protocol(review_repo):
    assert CAPTURE.exists(), "capture the real reviewer over a deliberately defective diff"
    recorded = json.loads(CAPTURE.read_text())
    assert recorded["rc"] == 0 and "review: outcome=ran" in recorded["stderr"]
    assert recorded["stderr"].count("review: outcome=") == 1
    assert recorded["finding_lines"] and recorded["workspace_unchanged"]
    ran = run_review(review_repo, FACTORY_REVIEW_MODEL=recorded["model"],
                     REVIEW_TEST_ANSWER="\n".join(recorded["finding_lines"]) + "\n")
    called = json.loads(Path(review_repo[1]["REVIEW_TEST_LOG"]).read_text())
    argv = called["argv"]
    argv[argv.index("--output-last-message") + 1] = "<final-message>"
    assert argv == recorded["argv_tail"]
    assert ran.returncode == recorded["rc"]
    assert set(ran.stdout.splitlines()) == set(recorded["finding_lines"])


def test_manifest_review_commands_select_the_declared_profile(review_repo):
    manifest = json.loads((FACTORY / ".factory/factory.json").read_text())
    profile = manifest["agents"][manifest["default_agent"]]
    commands = [workspace["review"] for workspace in manifest["workspaces"].values()
                if "review" in workspace]
    assert commands
    root, env = review_repo
    for command in commands:
        ran = subprocess.run(["sh", "-c", command], cwd=root, env=env,
                             capture_output=True, text=True, timeout=20)
        assert ran.returncode == 0, ran.stderr
        called = json.loads(Path(env["REVIEW_TEST_LOG"]).read_text())
        assert called["program"] == profile["driver"], called
        assert called["argv"][called["argv"].index("--model") + 1] == profile["default_model"]
        if profile["driver"] == "codex":
            assert called["home"] == "/factory/agent-config"


@pytest.mark.parametrize("driver", ["claude", "codex"])
@pytest.mark.parametrize("directory", [".", "server"])
def test_review_excludes_only_report_artifacts_and_preserves_other_hunks(
        review_repo, driver, directory):
    root, env = review_repo
    kept = {
        "server/app/changed.cpp": "implementation marker\n",
        "server/tests/test_changed.py": "test marker\n",
        "docs/technical/contracts/http.schema.json": "schema marker\n",
        "client/web/src/types/api.ts": "generated marker\n",
        "AGENTS.md": "instruction marker\n",
        "docs/tickets/TKT-1.md": "criterion marker\n",
        "docs/operations/ESCAPES.md": "new escape marker\n",
        "docs/reports-guide.md": "sibling marker\n",
        "server/docs/reports/kept.log": "nested root marker\n",
    }
    excluded = {
        "docs/reports/TKT-1-report.md": "excluded report marker\n",
        "docs/reports/TKT-1/deep/output.log": "excluded log marker\n" + "x" * 1048577 + "\n",
    }
    for relative, contents in {**kept, **excluded}.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "mixed report and implementation changes"],
                   cwd=root, check=True)
    full_diff = subprocess.run(["git", "diff", "main...HEAD"], cwd=root,
                               capture_output=True, text=True, check=True).stdout
    assert len(full_diff) > 1048576
    ran = subprocess.run(["sh", str(root / "review.sh"), "main"], cwd=root / directory,
                         env=dict(env, FACTORY_REVIEW_DRIVER=driver),
                         capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    called = json.loads(Path(env["REVIEW_TEST_LOG"]).read_text())
    prompt = called["stdin"]
    assert not any("+" + text.splitlines()[0] in prompt for text in excluded.values()), (
        "excluded report artifacts were transmitted to the reviewer")
    changed = subprocess.run(["git", "diff", "--name-only", "-z", "main...HEAD"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.split("\0")
    retained = [path for path in changed if path and not path.startswith("docs/reports/")]
    assert set(kept).issubset(retained) and len(retained) == len(kept) + 1
    for path in retained:
        expected = subprocess.run(["git", "diff", "main...HEAD", "--", path], cwd=root,
                                  capture_output=True, text=True, check=True).stdout
        assert expected and expected in prompt, f"the complete diff for {path} was not transmitted"
    assert len(prompt) < 1048576
    assert called["program"] == driver
    assert ran.stderr.count("review: outcome=ran") == 1


@pytest.mark.parametrize("driver", ["claude", "codex"])
def test_report_only_diff_does_not_call_a_reviewer(review_repo, driver):
    root, env = review_repo
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()
    target = root / "docs/reports/TKT-1/raw.log"
    target.parent.mkdir(parents=True)
    target.write_text("only excluded report artifacts changed\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "report artifacts only"], cwd=root, check=True)
    ran = run_review(review_repo, driver=driver, base=base)
    assert ran.returncode == 0, ran.stderr
    assert ran.stderr.count("review: outcome=nothing_to_review") == 1
    assert not Path(env["REVIEW_TEST_LOG"]).exists()


@pytest.mark.parametrize("driver", ["codex", "claude"])
def test_oversized_actionable_diff_uses_bounded_prompt_and_readable_full_hunks(review_repo, driver):
    root, env = review_repo
    marker = "needed_hunk_at_the_end_411"
    big = root / "server/app/big.cpp"
    big.parent.mkdir(parents=True)
    big.write_text("start\n" + "x" * 1_150_000 + "\n" + marker + "\n")
    other = root / "client/web/src/other.ts"
    other.parent.mkdir(parents=True)
    other.write_text("another actionable change\n")
    evidence = root / "docs/reports/TKT/raw.log"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("excluded private evidence\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "large source diff"], cwd=root, check=True)
    full_diff = subprocess.check_output([
        "git", "diff", "main...HEAD", "--", ".", ":(top,exclude)docs/reports/**",
    ], cwd=root)
    assert len(full_diff) > 1_048_576

    ran = run_review(review_repo, driver=driver, REVIEW_TEST_READ_PATH="server/app/big.cpp",
                     REVIEW_TEST_READ_MARKER=marker)
    assert ran.returncode == 0, ran.stderr
    called = json.loads(Path(env["REVIEW_TEST_LOG"]).read_text())
    prompt = called["stdin"]
    inventory = subprocess.check_output(["git", "diff", "--name-status", "main...HEAD"],
                                        cwd=root, text=True)
    assert inventory in prompt
    assert all(path in prompt for path in ("server/app/big.cpp", "client/web/src/other.ts",
                                           "docs/reports/TKT/raw.log"))
    assert "Pass 1" in prompt and "Pass 2" in prompt and "Pass 3" in prompt and "Pass 4" in prompt
    assert "git diff" in prompt and "main...HEAD" in prompt
    assert marker not in prompt and "excluded private evidence" not in prompt
    assert len(prompt.encode()) <= 900_000
    assert called["readback_bytes"] > 1_048_576 and called["readback_has_marker"] is True
    assert "review: bounded prompt mode" in ran.stderr
    assert ran.stderr.count("review: outcome=ran") == 1


def test_review_fails_locally_when_instructions_and_inventory_exceed_prompt_bound(review_repo):
    root, env = review_repo
    (root / "docs/technical/workflow/REVIEW.md").write_text("Review instructions\n" + "x" * 950_000)
    ran = run_review(review_repo)
    assert ran.returncode != 0
    assert "review: outcome=prompt_too_large" in ran.stderr
    assert "900000 bytes" in ran.stderr
    assert not Path(env["REVIEW_TEST_LOG"]).exists()
