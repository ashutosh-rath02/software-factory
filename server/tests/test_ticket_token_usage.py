"""Native Codex session usage is counted once across Factory run records."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "server/tools/ticket_token_usage.py"
SPEC = importlib.util.spec_from_file_location("ticket_token_usage", TOOL)
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)


def write_run(runs, run_id, session_id, model="gpt-6-sol", driver="codex"):
    path = runs / run_id / "meta.json"
    path.parent.mkdir()
    path.write_text(json.dumps(dict(run_id=run_id, session_id=session_id, model=model,
                                    driver=driver, agent="codex", ticket_id="SRV-FEAT-1",
                                    lane="lane/1", role="run", outcome="completed")))
    return path


def write_rollout(sessions, session_id, usages, name=None):
    path = sessions / (name or f"rollout-{session_id}.jsonl")
    events = [dict(type="session_meta", payload=dict(id=session_id))]
    for usage in usages:
        events.append(dict(type="event_msg", payload=dict(type="token_count", info=dict(
            total_token_usage=usage,
            last_token_usage={"input_tokens": 9999, "output_tokens": 9999}))))
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


@pytest.fixture
def roots(tmp_path):
    runs = tmp_path / "runs"
    sessions = tmp_path / "sessions"
    runs.mkdir()
    sessions.mkdir()
    return runs, sessions


def call(runs, sessions, output):
    return subprocess.run([sys.executable, str(TOOL), "--runs-dir", str(runs),
                           "--session-root", str(sessions), "--output", str(output)],
                          capture_output=True, text=True, timeout=10)


def test_cumulative_usage_is_counted_once_for_resumed_runs(roots, tmp_path):
    runs, sessions = roots
    write_run(runs, "run-1", "thread-1")
    write_run(runs, "run-2", "thread-1")
    write_run(runs, "claude-run", "other-thread", driver="claude")
    write_rollout(sessions, "thread-1", [
        {"input_tokens": 50, "cached_input_tokens": 20, "output_tokens": 10},
        {"input_tokens": 120, "cached_input_tokens": 70, "output_tokens": 30},
    ])
    output = tmp_path / "report.json"
    ran = call(runs, sessions, output)
    assert ran.returncode == 0, ran.stderr
    report = json.loads(output.read_text())
    assert report["summary"]["codex_runs"] == 2
    assert report["summary"]["distinct_sessions"] == 1
    assert report["summary"]["shared_sessions"] == 1
    assert report["summary"]["complete"] is True
    assert report["summary"]["known_usage_only"] == {
        "input_tokens": 120, "cached_input_tokens": 70,
        "output_tokens": 30, "total_tokens": 150,
    }
    assert report["sessions"][0]["run_ids"] == ["run-1", "run-2"]
    assert report["sessions"][0]["shared_session"] is True
    assert "last_token_usage" not in output.read_text()


def test_duplicate_rollout_uses_largest_cumulative_snapshot(roots, tmp_path):
    runs, sessions = roots
    write_run(runs, "run-1", "thread-1")
    write_rollout(sessions, "thread-1", [
        {"input_tokens": 200, "cached_input_tokens": 25, "output_tokens": 20}], "rollout-old.jsonl")
    write_rollout(sessions, "thread-1", [
        {"input_tokens": 250, "cached_input_tokens": 30, "output_tokens": 25}], "rollout-new.jsonl")
    report = COLLECTOR.collect(runs, [sessions])
    assert len(report["sessions"][0]["rollouts"]) == 2
    assert report["summary"]["known_usage_only"]["total_tokens"] == 275


def test_missing_usage_is_unknown_and_model_mismatch_is_exposed(roots, tmp_path):
    runs, sessions = roots
    write_run(runs, "run-1", "thread-1")
    write_run(runs, "run-2", "thread-2", model="gpt-5.6-sol")
    write_rollout(sessions, "thread-1", [])
    write_rollout(sessions, "thread-2", [
        {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 5}])
    report = COLLECTOR.collect(runs, [sessions])
    by_id = {session["session_id"]: session for session in report["sessions"]}
    assert by_id["thread-1"]["status"] == "unknown_usage"
    assert by_id["thread-1"]["usage"] is None
    assert by_id["thread-2"]["status"] == "model_mismatch"
    assert by_id["thread-2"]["usage"]["total_tokens"] == 55
    assert report["summary"]["unknown_sessions"] == 1
    assert report["summary"]["model_mismatch_sessions"] == 1
    assert report["summary"]["known_usage_only"]["total_tokens"] == 0
    assert report["summary"]["complete"] is False


@pytest.mark.parametrize("missing_model", [None, ""])
def test_resumed_session_with_missing_model_is_not_verified(roots, tmp_path, missing_model):
    runs, sessions = roots
    write_run(runs, "run-1", "thread-1")
    write_run(runs, "run-2", "thread-1", model=missing_model)
    write_rollout(sessions, "thread-1", [
        {"input_tokens": 120, "cached_input_tokens": 70, "output_tokens": 30}])
    output = tmp_path / "report.json"
    ran = call(runs, sessions, output)
    assert ran.returncode == 0, ran.stderr
    report = json.loads(output.read_text())
    session = report["sessions"][0]
    assert session["usage"]["total_tokens"] == 150
    assert session["model_match"] is False
    assert session["status"] == "model_mismatch"
    assert report["summary"]["matching_sessions"] == 0
    assert report["summary"]["known_sessions"] == 0
    assert report["summary"]["model_mismatch_sessions"] == 1
    assert report["summary"]["known_usage_only"]["total_tokens"] == 0
    assert report["summary"]["complete"] is False


def test_output_inside_input_is_refused_without_modifying_inputs(roots):
    runs, sessions = roots
    meta = write_run(runs, "run-1", "thread-1")
    before = meta.read_bytes()
    ran = call(runs, sessions, runs / "report.json")
    assert ran.returncode == 2
    assert "outside the input roots" in ran.stderr
    assert meta.read_bytes() == before
    assert not (runs / "report.json").exists()


def test_invalid_cached_count_is_not_reported_as_zero(roots):
    runs, sessions = roots
    write_run(runs, "run-1", "thread-1")
    write_rollout(sessions, "thread-1", [
        {"input_tokens": 2, "cached_input_tokens": 3, "output_tokens": 1}])
    report = COLLECTOR.collect(runs, [sessions])
    assert report["sessions"][0]["usage"] is None
    assert report["summary"]["unknown_sessions"] == 1


@pytest.mark.parametrize("only_claude", [False, True])
def test_zero_native_codex_runs_is_incomplete_without_a_zero_total(roots, tmp_path, only_claude):
    runs, sessions = roots
    if only_claude:
        write_run(runs, "claude-run", "claude-thread", driver="claude")
    output = tmp_path / "report.json"
    ran = call(runs, sessions, output)
    assert ran.returncode == 0, ran.stderr
    report = json.loads(output.read_text())
    assert report["runs"] == [] and report["sessions"] == []
    assert report["summary"]["codex_runs"] == 0
    assert report["summary"]["status"] == "no_codex_runs"
    assert report["summary"]["complete"] is False
    assert report["summary"]["known_usage_only"] is None


@pytest.mark.parametrize("unreadable", [False, True])
def test_unreadable_metadata_is_incomplete_without_an_empty_run_claim(roots, tmp_path, unreadable):
    runs, sessions = roots
    metadata = runs / "broken-run" / "meta.json"
    metadata.parent.mkdir()
    if unreadable:
        metadata.mkdir()
    else:
        metadata.write_text("{invalid JSON")
    output = tmp_path / "report.json"
    ran = call(runs, sessions, output)
    assert ran.returncode == 0, ran.stderr
    report = json.loads(output.read_text())
    assert report["problems"] and report["problems"][0]["path"] == str(metadata)
    assert report["summary"]["status"] == "incomplete"
    assert report["summary"]["complete"] is False
    assert report["summary"]["known_usage_only"] is None
