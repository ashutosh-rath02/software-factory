"""Paired REVIEW measurement uses one immutable snapshot and complete usage events."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "server/tools/review_token_eval.py"
SPEC = importlib.util.spec_from_file_location("review_token_eval", TOOL)
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root).decode().strip()


@pytest.fixture
def review_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "review eval")
    git(root, "config", "user.email", "review-eval@test.local")
    script = root / ".claude/scripts/review.sh"
    script.parent.mkdir(parents=True)
    candidate = (ROOT / ".claude/scripts/review.sh").read_bytes()
    exclusion = b" -- . ':(top,exclude)docs/reports/**'"
    assert exclusion in candidate
    historical = candidate.replace(exclusion, b"")
    assert historical != candidate
    script.write_bytes(historical)
    docs = root / "docs/technical/workflow/REVIEW.md"
    docs.parent.mkdir(parents=True)
    docs.write_text("Historical review instructions.\n")
    source = root / "server/app/store.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("before\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    source.write_text("after\n")
    report = root / "docs/reports/TICKET-report.md"
    report.parent.mkdir()
    report.write_text("Long evidence text.\n" * 100)
    git(root, "add", ".")
    git(root, "commit", "-qm", "change")
    head = git(root, "rev-parse", "HEAD")
    script.write_bytes(candidate)
    return root, base, head


def invoke(repo, base, head, output, env, *extra):
    return subprocess.run([sys.executable, str(TOOL), "--repo", str(repo),
                           "--base", base, "--head", head, "--output", str(output), *extra],
                          env=env, capture_output=True, text=True, timeout=30)


def fake_codex(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "codex"
    shim.write_text(f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
with pathlib.Path(os.environ['EVAL_CALLS']).open('a') as log:
    log.write(json.dumps({{'argv': args, 'prompt': sys.stdin.read(), 'cwd': os.getcwd(),
        'snapshot_status': subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip(),
        'instruction_paths': [path for path in ('docs/REVIEW.md', 'docs/technical/workflow/REVIEW.md')
                              if pathlib.Path(path).is_file()]}}) + '\\n')
mode = os.environ.get('EVAL_MODE')
if mode == 'timeout':
    time.sleep(2)
if mode == 'nonzero':
    sys.exit(7)
pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(
    'This looks good.\\n' if mode == 'invalid' else '')
print(json.dumps({{'type': 'item.completed', 'item': {{'type': 'command_execution', 'command': 'git show HEAD:server/app/store.cpp'}}}}))
print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 30}}}}))
if os.environ.get('EVAL_BAD_USAGE') != '1':
    print(json.dumps({{'type': 'turn.completed', 'usage': {{'input_tokens': 10, 'cached_input_tokens': 5, 'output_tokens': 2}}}}))
else:
    print(json.dumps({{'type': 'turn.completed'}}))
""")
    shim.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               EVAL_CALLS=str(tmp_path / "calls.jsonl"))
    return env


def test_offline_capture_uses_historical_docs_and_identical_snapshot(review_repo, tmp_path):
    repo, base, head = review_repo
    output = tmp_path / "offline"
    env = fake_codex(tmp_path)
    before = git(repo, "status", "--porcelain")
    ran = invoke(repo, base, head, output, env)
    assert ran.returncode == 0, ran.stderr
    assert not Path(env["EVAL_CALLS"]).exists()
    assert git(repo, "status", "--porcelain") == before
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["base"] == base and manifest["head"] == head
    assert manifest["source_head"] == head
    assert manifest["model"] == "gpt-6-sol"
    assert manifest["changed_files"]["all"] == ["docs/reports/TICKET-report.md", "server/app/store.cpp"]
    assert manifest["changed_files"]["included"] == ["server/app/store.cpp"]
    old = (output / "baseline.prompt").read_bytes()
    new = (output / "candidate.prompt").read_bytes()
    assert b"Historical review instructions." in old and b"Historical review instructions." in new
    assert b"Long evidence text." in old
    assert b"Long evidence text." not in new
    assert b"server/app/store.cpp" in old and b"server/app/store.cpp" in new
    for label, prompt in (("baseline", old), ("candidate", new)):
        assert manifest["arms"][label]["prompt_bytes"] == len(prompt)
        assert manifest["arms"][label]["prompt_sha256"] == EVAL.digest(prompt)


@pytest.mark.parametrize("migrated", [False, True], ids=["historical-head", "cross-layout"])
def test_instruction_path_migration_preserves_scripts_head_bytes_and_live_snapshot(tmp_path, migrated):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "review eval")
    git(repo, "config", "user.email", "review-eval@test.local")
    script = repo / ".claude/scripts/review.sh"
    script.parent.mkdir(parents=True)
    candidate = (ROOT / ".claude/scripts/review.sh").read_bytes()
    historical = candidate.replace(b"docs/technical/workflow/REVIEW.md", b"docs/REVIEW.md")
    historical = historical.replace(b" -- . ':(top,exclude)docs/reports/**'", b"")
    script.write_bytes(historical)
    legacy = repo / "docs/REVIEW.md"
    legacy.parent.mkdir()
    legacy.write_bytes(b"Baseline instruction bytes.\n")
    source = repo / "server/app/store.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("before\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "historical layout")
    base = git(repo, "rev-parse", "HEAD")
    head_docs = b"Head instruction bytes with CRLF.\r\n"
    if migrated:
        legacy.unlink()
        declared = repo / "docs/technical/workflow/REVIEW.md"
        declared.parent.mkdir(parents=True)
        script.write_bytes(candidate)
    else:
        declared = legacy
    declared.write_bytes(head_docs)
    source.write_text("after\n")
    report = repo / "docs/reports/TICKET-report.md"
    report.parent.mkdir()
    report.write_text("Long evidence text.\n" * 100)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewable head")
    head = git(repo, "rev-parse", "HEAD")
    script.write_bytes(candidate)
    before = git(repo, "status", "--porcelain")
    env = fake_codex(tmp_path)
    output = tmp_path / "migration"
    ran = invoke(repo, base, head, output, env, "--run")
    assert ran.returncode == 0, ran.stderr
    assert git(repo, "status", "--porcelain") == before
    assert (output / "baseline.review.sh").read_bytes() == historical
    assert (output / "candidate.review.sh").read_bytes() == candidate
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["review_instructions_sha256"] == EVAL.digest(head_docs)
    for label in ("baseline", "candidate"):
        prompt = (output / f"{label}.prompt").read_bytes()
        assert prompt.split(b"\n---\n", 1)[0] == head_docs
    calls = [json.loads(line) for line in Path(env["EVAL_CALLS"]).read_text().splitlines()]
    assert len(calls) == 2
    for call in calls:
        assert call["snapshot_status"] == ""
        assert call["instruction_paths"] == [str(declared.relative_to(repo))]
    report = json.loads((output / "report.json").read_text())
    assert all(arm["status"] == "complete" for arm in report["arms"].values())


def test_conflicting_tracked_instruction_paths_refuse_provider_calls(review_repo, tmp_path):
    repo, base, _ = review_repo
    legacy = repo / "docs/REVIEW.md"
    legacy.write_text("Conflicting instructions.\n")
    git(repo, "add", "docs/REVIEW.md")
    git(repo, "commit", "-qm", "ambiguous instruction documents")
    head = git(repo, "rev-parse", "HEAD")
    env = fake_codex(tmp_path)
    output = tmp_path / "conflicting-instructions"
    ran = invoke(repo, base, head, output, env, "--run")
    assert ran.returncode == 2
    assert "conflicting REVIEW instructions" in ran.stderr
    assert not Path(env["EVAL_CALLS"]).exists()


def test_failed_capture_removes_instruction_aliases_before_snapshot_cleanup(review_repo, tmp_path, monkeypatch):
    repo, base, head = review_repo
    retained = tmp_path / "retained-capture"
    retained.mkdir()

    class RetainedDirectory:
        def __enter__(self):
            return str(retained)

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(EVAL.tempfile, "TemporaryDirectory", lambda **_: RetainedDirectory())

    def fail_capture(_script, _label, _base, clone, _output):
        assert (clone / "docs/REVIEW.md").read_bytes() == (
            clone / "docs/technical/workflow/REVIEW.md").read_bytes()
        raise ValueError("interrupted prompt capture")

    monkeypatch.setattr(EVAL, "capture", fail_capture)
    args = EVAL.argparse.Namespace(repo=str(repo), base=base, head=head,
                                  output=str(tmp_path / "interrupted"), run=False)
    with pytest.raises(ValueError, match="interrupted prompt capture"):
        EVAL.evaluate(args)
    clone = retained / "snapshot"
    assert not (clone / "docs/REVIEW.md").exists()
    assert (clone / "docs/technical/workflow/REVIEW.md").read_bytes() == subprocess.check_output(
        ["git", "show", "HEAD:docs/technical/workflow/REVIEW.md"], cwd=clone)
    assert git(clone, "status", "--porcelain") == ""


def test_committed_candidate_uses_diff_merge_base_script(review_repo, tmp_path):
    repo, base, _ = review_repo
    git(repo, "add", ".claude/scripts/review.sh")
    git(repo, "commit", "-qm", "commit candidate reviewer")
    head = git(repo, "rev-parse", "HEAD")
    env = fake_codex(tmp_path)
    output = tmp_path / "committed"
    ran = invoke(repo, base, head, output, env)
    assert ran.returncode == 0, ran.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["baseline_script_ref"] == base
    assert manifest["arms"]["baseline"]["script_sha256"] != manifest["arms"]["candidate"]["script_sha256"]
    assert (output / "baseline.prompt").read_bytes() != (output / "candidate.prompt").read_bytes()
    assert b"Long evidence text." in (output / "baseline.prompt").read_bytes()
    assert b"Long evidence text." not in (output / "candidate.prompt").read_bytes()
    assert not Path(env["EVAL_CALLS"]).exists()


def test_diverged_base_ref_uses_its_merge_base_script(review_repo, tmp_path):
    repo, base, head = review_repo
    candidate = (repo / ".claude/scripts/review.sh").read_bytes()
    git(repo, "checkout", "-fq", "-b", "comparison-side", base)
    (repo / ".claude/scripts/review.sh").write_bytes(candidate)
    git(repo, "add", ".claude/scripts/review.sh")
    git(repo, "commit", "-qm", "diverged comparison side")
    side = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-fq", head)
    (repo / ".claude/scripts/review.sh").write_bytes(candidate)
    env = fake_codex(tmp_path)
    output = tmp_path / "diverged"
    ran = invoke(repo, side, head, output, env)
    assert ran.returncode == 0, ran.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["base"] == side and manifest["baseline_script_ref"] == base
    assert manifest["comparison_status"] == "comparable"
    assert manifest["arms"]["baseline"]["script_sha256"] != manifest["arms"]["candidate"]["script_sha256"]
    assert not Path(env["EVAL_CALLS"]).exists()


def test_legacy_claude_capture_is_stubbed_before_any_provider_call(review_repo, tmp_path):
    repo, base, _ = review_repo
    script = repo / ".claude/scripts/review.sh"
    candidate = script.read_bytes()
    git(repo, "checkout", "-fq", "-b", "legacy-baseline", base)
    script.write_text("""#!/bin/sh
set -e
{ cat docs/technical/workflow/REVIEW.md; git diff "$1...HEAD"; } | claude --model claude-opus-5 -p >/dev/null
echo 'review: outcome=ran' >&2
""")
    git(repo, "add", ".claude/scripts/review.sh")
    git(repo, "commit", "-qm", "historical Claude-only reviewer")
    legacy_base = git(repo, "rev-parse", "HEAD")
    (repo / "server/app/store.cpp").write_text("changed again\n")
    git(repo, "add", "server/app/store.cpp")
    git(repo, "commit", "-qm", "reviewable change")
    head = git(repo, "rev-parse", "HEAD")
    script.write_bytes(candidate)
    external = tmp_path / "external"
    external.mkdir()
    marker = tmp_path / "external-provider-called"
    provider = external / "claude"
    provider.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('called')\nraise SystemExit(73)\n")
    provider.chmod(0o755)
    env = fake_codex(tmp_path)
    env["PATH"] = f"{external}:{env['PATH']}"
    output = tmp_path / "legacy-stubbed"
    ran = invoke(repo, legacy_base, head, output, env, "--run")
    assert ran.returncode == 2
    assert "did not use codex gpt-6-sol" in ran.stderr
    assert not marker.exists()
    assert not Path(env["EVAL_CALLS"]).exists()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["comparison_status"] == "incompatible_driver"
    assert manifest["arms"]["baseline"]["capture_invocation"] == {
        "driver": "claude", "model": "claude-opus-5"}
    assert manifest["arms"]["candidate"]["capture_invocation"] == {
        "driver": "codex", "model": "gpt-6-sol"}
    assert not (output / "report.json").exists()
    assert b"Historical review instructions." in (output / "baseline.prompt").read_bytes()
    assert b"server/app/store.cpp" in (output / "baseline.prompt").read_bytes()


def test_identical_scripts_refuse_paid_pair(review_repo, tmp_path):
    repo, _, _ = review_repo
    git(repo, "add", ".claude/scripts/review.sh")
    git(repo, "commit", "-qm", "commit candidate reviewer")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "server/app/store.cpp").write_text("third version\n")
    git(repo, "add", "server/app/store.cpp")
    git(repo, "commit", "-qm", "another source change")
    head = git(repo, "rev-parse", "HEAD")
    env = fake_codex(tmp_path)
    output = tmp_path / "identical-scripts"
    ran = invoke(repo, base, head, output, env, "--run")
    assert ran.returncode == 2
    assert "identical REVIEW scripts" in ran.stderr
    assert json.loads((output / "manifest.json").read_text())["comparison_status"] == "identical_scripts"
    assert not (output / "report.json").exists()
    assert not Path(env["EVAL_CALLS"]).exists()


def test_distinct_scripts_with_identical_prompts_refuse_paid_pair(review_repo, tmp_path):
    repo, _, _ = review_repo
    git(repo, "add", ".claude/scripts/review.sh")
    git(repo, "commit", "-qm", "commit candidate reviewer")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "server/app/store.cpp").write_text("third version\n")
    git(repo, "add", "server/app/store.cpp")
    git(repo, "commit", "-qm", "another source change")
    head = git(repo, "rev-parse", "HEAD")
    script = repo / ".claude/scripts/review.sh"
    script.write_bytes(script.read_bytes() + b"\n# No-op comparison change.\n")
    env = fake_codex(tmp_path)
    output = tmp_path / "identical-prompts"
    ran = invoke(repo, base, head, output, env, "--run")
    assert ran.returncode == 2
    assert "identical REVIEW prompts" in ran.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["comparison_status"] == "identical_prompts"
    assert manifest["arms"]["baseline"]["script_sha256"] != manifest["arms"]["candidate"]["script_sha256"]
    assert not (output / "report.json").exists()
    assert not Path(env["EVAL_CALLS"]).exists()


def test_run_pins_only_sol_and_counts_completed_turns_once(review_repo, tmp_path):
    repo, base, head = review_repo
    env = fake_codex(tmp_path)
    output = tmp_path / "run"
    ran = invoke(repo, base, head, output, env, "--run", "--reverse-order")
    assert ran.returncode == 0, ran.stderr
    calls = [json.loads(line) for line in Path(env["EVAL_CALLS"]).read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0]["prompt"] == (output / "candidate.prompt").read_text()
    assert calls[1]["prompt"] == (output / "baseline.prompt").read_text()
    assert calls[0]["cwd"] == calls[1]["cwd"]
    for call in calls:
        args = call["argv"]
        assert args[args.index("--model") + 1] == "gpt-6-sol"
        assert args[args.index("--sandbox") + 1] == "read-only"
        assert args[args.index("-c") + 1] == 'model_reasoning_effort="medium"'
        assert args[args.index("--disable") + 1] == "multi_agent"
        assert "--ignore-user-config" in args and "--ephemeral" in args and "--json" in args
    report = json.loads((output / "report.json").read_text())
    assert report["order"] == ["candidate", "baseline"]
    for arm in report["arms"].values():
        assert arm["status"] == "complete"
        assert arm["usage"] == {"input_tokens": 110, "cached_input_tokens": 25,
                                "output_tokens": 32, "total_tokens": 142}
        assert arm["completed_turns"] == 2 and arm["tool_calls"] == arm["readbacks"] == 1
    assert report["paired_delta_tokens"] == report["paired_savings_tokens"] == 0


def test_missing_usage_fails_both_arms_without_claiming_savings(review_repo, tmp_path):
    repo, base, head = review_repo
    env = fake_codex(tmp_path)
    env["EVAL_BAD_USAGE"] = "1"
    output = tmp_path / "failed"
    ran = invoke(repo, base, head, output, env, "--run")
    assert ran.returncode == 1
    assert len(Path(env["EVAL_CALLS"]).read_text().splitlines()) == 2
    report = json.loads((output / "report.json").read_text())
    assert all(arm["status"] == "failed" for arm in report["arms"].values())
    assert all("usage" in arm["error"] for arm in report["arms"].values())
    assert "paired_savings_tokens" not in report


@pytest.mark.parametrize("mode,error", [
    ("nonzero", "nonzero"), ("invalid", "invalid final answer"), ("timeout", "timeout"),
])
def test_failed_runs_never_report_savings(review_repo, tmp_path, mode, error):
    repo, base, head = review_repo
    env = fake_codex(tmp_path)
    env["EVAL_MODE"] = mode
    output = tmp_path / "failed"
    ran = invoke(repo, base, head, output, env, "--run", "--timeout", "1")
    assert ran.returncode == 1, ran.stderr
    report = json.loads((output / "report.json").read_text())
    assert len(Path(env["EVAL_CALLS"]).read_text().splitlines()) == 2
    assert all(arm["status"] == "failed" and error in arm["error"]
               for arm in report["arms"].values())
    assert "paired_delta_tokens" not in report


def test_usage_rejects_failed_turn_and_cached_double_count():
    with pytest.raises(ValueError, match="turn.failed"):
        EVAL.parse_events(b'{"type":"turn.failed"}\n')
    with pytest.raises(ValueError, match="cached input exceeds input"):
        EVAL.parse_events(b'{"type":"turn.completed","usage":'
                          b'{"input_tokens":1,"cached_input_tokens":2,"output_tokens":3}}\n')
