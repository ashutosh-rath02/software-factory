"""A recorded cause blocks applicable work until evidence-backed resolution."""

import json
import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


TOOL = Path(__file__).resolve().parents[1] / "tools/ticket_readiness.py"


def run_tool(registry, output, mode, *args):
    return subprocess.run([sys.executable, str(TOOL), mode, "--registry", str(registry),
                           "--output", str(output), *args], capture_output=True, text=True,
                          timeout=15)


def row(tmp_path, *, cause_id="helper", probe_exit=0):
    script = tmp_path / "probe.py"
    script.write_text(f"print('complete probe output')\nraise SystemExit({probe_exit})\n")
    return dict(id=cause_id,
                scope=dict(workspaces=["factory-server"], profiles=["codex"]),
                fix_tickets=["SRV-FIX-560"],
                probes=[dict(argv=[sys.executable, str(script)], timeout_seconds=2)],
                evidence=["docs/reports/SRV-FEAT-558/tool-host.log"],
                incidents=["SRV-FEAT-558"], status="open")


def fixture(tmp_path):
    registry = tmp_path / "causes.json"
    registry.write_text(json.dumps(dict(version=1, causes=[])))
    return registry


def write_cause(tmp_path, value):
    path = tmp_path / "cause.json"
    path.write_text(json.dumps(value))
    return path


def test_record_deduplicates_incidents_and_detects_conflict(tmp_path):
    registry = fixture(tmp_path)
    cause = row(tmp_path)
    source = write_cause(tmp_path, cause)
    first = run_tool(registry, tmp_path / "first.json", "record", "--cause-file", str(source))
    assert first.returncode == 0, first.stderr
    cause["incidents"] = ["SRV-FEAT-558", "SRV-FEAT-562"]
    source.write_text(json.dumps(cause))
    preview = run_tool(registry, tmp_path / "preview.json", "dedup", "--cause-file", str(source))
    assert preview.returncode == 0, preview.stderr
    assert json.loads(registry.read_text())["causes"][0]["incidents"] == ["SRV-FEAT-558"]
    again = run_tool(registry, tmp_path / "again.json", "record", "--cause-file", str(source))
    assert again.returncode == 0, again.stderr
    saved = json.loads(registry.read_text())["causes"]
    assert len(saved) == 1 and saved[0]["incidents"] == ["SRV-FEAT-558", "SRV-FEAT-562"]
    cause["probes"][0]["timeout_seconds"] = 3
    source.write_text(json.dumps(cause))
    conflict = run_tool(registry, tmp_path / "conflict.json", "record", "--cause-file", str(source))
    assert conflict.returncode == 2
    assert json.loads(registry.read_text())["causes"] == saved


def test_same_cause_accepts_new_evidence_without_changing_its_definition(tmp_path):
    registry = fixture(tmp_path)
    cause = row(tmp_path)
    source = write_cause(tmp_path, cause)
    assert run_tool(registry, tmp_path / "first.json", "record",
                    "--cause-file", str(source)).returncode == 0
    cause["evidence"] = ["docs/reports/SRV-FEAT-558/tool-host-rerun.log"]
    source.write_text(json.dumps(cause))
    preview = run_tool(registry, tmp_path / "preview.json", "dedup",
                       "--cause-file", str(source))
    assert preview.returncode == 0, preview.stderr
    assert run_tool(registry, tmp_path / "again.json", "record",
                    "--cause-file", str(source)).returncode == 0
    saved = json.loads(registry.read_text())["causes"]
    assert len(saved) == 1
    assert saved[0]["evidence"] == ["docs/reports/SRV-FEAT-558/tool-host-rerun.log",
                                    "docs/reports/SRV-FEAT-558/tool-host.log"]


def test_open_cause_blocks_even_when_current_probe_passes(tmp_path):
    registry = fixture(tmp_path)
    registry.write_text(json.dumps(dict(version=1, causes=[row(tmp_path)])))
    captures = tmp_path / "captures"
    output = tmp_path / "checked.json"
    result = run_tool(registry, output, "check", "--workspace", "factory-server",
                      "--profile", "codex", "--ticket", "SRV-FEAT-562",
                      "--captures", str(captures))
    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text())
    assert report["status"] == "blocked"
    assert report["checked"] == 1
    assert report["causes"][0]["probe_results"][0]["exit_code"] == 0
    assert any(b"complete probe output" in p.read_bytes() for p in captures.iterdir())
    summary = json.loads(result.stdout)
    assert summary["blocking_causes"] == ["helper"]
    assert summary["fix_tickets"] == ["SRV-FIX-560"]
    assert "complete probe output" not in result.stdout


def test_check_stdout_deduplicates_fix_tickets_and_stays_bounded(tmp_path):
    registry = fixture(tmp_path)
    causes = [row(tmp_path, cause_id=f"cause-{index:02d}") for index in range(40)]
    registry.write_text(json.dumps(dict(version=1, causes=causes)))
    result = run_tool(registry, tmp_path / "checked.json", "check",
                      "--workspace", "factory-server", "--profile", "codex",
                      "--ticket", "SRV-FEAT-562", "--captures", str(tmp_path / "captures"))
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    assert summary["fix_tickets"] == ["SRV-FIX-560"]
    assert summary["blocking_causes"] == [f"cause-{index:02d}" for index in range(8)]
    assert summary["omitted_blocking_causes"] == 32
    assert len(result.stdout) < 4096
    assert len(json.loads((tmp_path / "checked.json").read_text())["causes"]) == 40


@pytest.mark.parametrize("workspace,profile,ticket", [
    ("factory-client", "codex", "WEB-FEAT-1"),
    ("factory-server", "claude", "SRV-FEAT-1"),
    ("factory-server", "codex", "SRV-FIX-560"),
])
def test_unrelated_or_owner_repair_has_explicit_empty_sweep(tmp_path, workspace, profile, ticket):
    registry = fixture(tmp_path)
    registry.write_text(json.dumps(dict(version=1, causes=[row(tmp_path)])))
    output = tmp_path / "checked.json"
    result = run_tool(registry, output, "check", "--workspace", workspace,
                      "--profile", profile, "--ticket", ticket,
                      "--captures", str(tmp_path / "captures"))
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["status"] == "no_applicable_checks"
    assert report["checked"] == 0 and report["causes"] == []


def test_missing_or_malformed_registry_is_not_an_empty_pass(tmp_path):
    output = tmp_path / "checked.json"
    args = ("--workspace", "factory-server", "--profile", "codex",
            "--ticket", "SRV-FEAT-562", "--captures", str(tmp_path / "captures"))
    missing = run_tool(tmp_path / "missing.json", output, "check", *args)
    assert missing.returncode == 2
    registry = tmp_path / "causes.json"
    registry.write_text('{"version": 1, "causes": [{"id": "x"}]}')
    malformed = run_tool(registry, output, "check", *args)
    assert malformed.returncode == 2
    assert json.loads(output.read_text())["status"] == "error"


def test_two_recorders_preserve_both_incidents(tmp_path):
    registry = fixture(tmp_path)
    cause = row(tmp_path)
    writers = []
    for index in range(2):
        source = tmp_path / f"cause-{index}.json"
        source.write_text(json.dumps(dict(cause, incidents=[f"SRV-FEAT-{index}"])))
        writers.append(subprocess.Popen([sys.executable, str(TOOL), "record",
                                         "--registry", str(registry),
                                         "--cause-file", str(source),
                                         "--output", str(tmp_path / f"result-{index}.json")],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    for writer in writers:
        out, err = writer.communicate(timeout=10)
        assert writer.returncode == 0, (out, err)
    saved = json.loads(registry.read_text())["causes"]
    assert len(saved) == 1
    assert saved[0]["incidents"] == ["SRV-FEAT-0", "SRV-FEAT-1"]


def receipts(tmp_path, cause_id="helper", *, bad=None):
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate version 1")
    fingerprint = hashlib.sha256(artifact.read_bytes()).hexdigest()
    paths = {}
    for phase, outcome, code in (("red", "fail", 1), ("green", "pass", 0),
                                 ("negative", "fail", 1), ("qa", "pass", None)):
        evidence = tmp_path / f"{phase}.log"
        evidence.write_text(f"{phase} complete capture\n")
        receipt = dict(version=1, phase=phase, cause_id=cause_id,
                       artifact_sha256=fingerprint, outcome=outcome,
                       evidence=evidence.name)
        if code is not None:
            receipt["exit_code"] = code
        if phase == "qa":
            receipt.update(session_id="independent-qa-session", independent=True)
        if bad and bad[0] == phase:
            receipt[bad[1]] = bad[2]
        path = tmp_path / f"{phase}.json"
        path.write_text(json.dumps(receipt))
        paths[phase] = path
    return artifact, paths


def resolve_args(artifact, paths):
    return ("--id", "helper", "--artifact", str(artifact),
            "--red", str(paths["red"]), "--green", str(paths["green"]),
            "--negative", str(paths["negative"]), "--qa", str(paths["qa"]))


def test_resolve_checks_typed_receipts_then_keeps_regression_live(tmp_path):
    registry = fixture(tmp_path)
    open_cause = row(tmp_path)
    registry.write_text(json.dumps(dict(version=1, causes=[open_cause])))
    artifact, paths = receipts(tmp_path)
    result = run_tool(registry, tmp_path / "resolved.json", "resolve",
                      *resolve_args(artifact, paths))
    assert result.returncode == 0, result.stderr
    saved = json.loads(registry.read_text())["causes"][0]
    assert saved["status"] == "resolved"
    assert saved["resolution"]["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert set(saved["resolution"]["receipts"]) == {"red", "green", "negative", "qa"}
    checked = tmp_path / "checked.json"
    ready = run_tool(registry, checked, "check", "--workspace", "factory-server",
                     "--profile", "codex", "--ticket", "SRV-FEAT-562",
                     "--captures", str(tmp_path / "captures"))
    assert ready.returncode == 0, ready.stderr
    report = json.loads(checked.read_text())
    assert report["status"] == "ready" and report["checked"] == 1
    open_cause["probes"][0]["argv"][-1] = str(tmp_path / "failing-probe.py")
    (tmp_path / "failing-probe.py").write_text("raise SystemExit(1)\n")
    saved["probes"] = open_cause["probes"]
    registry.write_text(json.dumps(dict(version=1, causes=[saved])))
    failed = run_tool(registry, tmp_path / "failed.json", "check",
                      "--workspace", "factory-server", "--profile", "codex",
                      "--ticket", "SRV-FEAT-562", "--captures", str(tmp_path / "captures"))
    assert failed.returncode == 1
    assert json.loads((tmp_path / "failed.json").read_text())["status"] == "blocked"


@pytest.mark.parametrize("bad", [
    ("red", "outcome", "pass"),
    ("negative", "exit_code", 0),
    ("green", "artifact_sha256", "0" * 64),
    ("qa", "independent", False),
    ("qa", "session_id", ""),
    ("qa", "cause_id", "other"),
])
def test_resolve_refuses_wrong_receipt_without_changing_registry(tmp_path, bad):
    registry = fixture(tmp_path)
    registry.write_text(json.dumps(dict(version=1, causes=[row(tmp_path)])))
    before = registry.read_bytes()
    artifact, paths = receipts(tmp_path, bad=bad)
    output = tmp_path / "resolved.json"
    result = run_tool(registry, output, "resolve",
                      *resolve_args(artifact, paths))
    assert result.returncode == 2
    refusal = json.loads(output.read_text())
    assert refusal["status"] == "error"
    assert "receipt" in refusal["message"].lower(), refusal
    assert registry.read_bytes() == before


def test_owner_exemption_does_not_hide_another_open_cause(tmp_path):
    registry = fixture(tmp_path)
    other = row(tmp_path, cause_id="second")
    other["fix_tickets"] = ["SRV-FIX-561"]
    registry.write_text(json.dumps(dict(version=1, causes=[row(tmp_path), other])))
    output = tmp_path / "checked.json"
    result = run_tool(registry, output, "check", "--workspace", "factory-server",
                      "--profile", "codex", "--ticket", "SRV-FIX-560",
                      "--captures", str(tmp_path / "captures"))
    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text())
    assert report["status"] == "blocked" and report["checked"] == 1
    assert report["exempt"] == ["helper"]
    assert [cause["id"] for cause in report["causes"]] == ["second"]


def test_probe_timeout_is_unknown_and_keeps_partial_capture(tmp_path):
    registry = fixture(tmp_path)
    cause = row(tmp_path)
    script = tmp_path / "slow.py"
    script.write_text("import time\nprint('before timeout', flush=True)\ntime.sleep(3)\n")
    cause["probes"] = [dict(argv=[sys.executable, str(script)], timeout_seconds=1)]
    registry.write_text(json.dumps(dict(version=1, causes=[cause])))
    output = tmp_path / "checked.json"
    result = run_tool(registry, output, "check", "--workspace", "factory-server",
                      "--profile", "codex", "--ticket", "SRV-FEAT-562",
                      "--captures", str(tmp_path / "captures"))
    assert result.returncode == 2, result.stderr
    report = json.loads(output.read_text())
    probe = report["causes"][0]["probe_results"][0]
    assert report["status"] == "error" and report["message"]
    assert probe["status"] == "timeout" and probe["exit_code"] is None
    assert Path(probe["stdout_path"]).read_text() == "before timeout\n"
    assert "before timeout" not in result.stdout and "before timeout" not in output.read_text()


def test_timeout_kills_probe_child_and_preserves_large_file_capture(tmp_path):
    registry = fixture(tmp_path)
    cause = row(tmp_path)
    pidfile = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text("import os,sys,time\nopen(sys.argv[1],'w').write(str(os.getpid()))\ntime.sleep(30)\n")
    parent = tmp_path / "parent.py"
    parent.write_text("import subprocess,sys,time\n"
                      "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
                      "sys.stdout.write('x' * 1200000)\nsys.stdout.flush()\ntime.sleep(30)\n")
    cause["probes"] = [dict(argv=[sys.executable, str(parent), str(child), str(pidfile)],
                            timeout_seconds=1)]
    registry.write_text(json.dumps(dict(version=1, causes=[cause])))
    output = tmp_path / "checked.json"
    try:
        result = run_tool(registry, output, "check", "--workspace", "factory-server",
                          "--profile", "codex", "--ticket", "SRV-FEAT-562",
                          "--captures", str(tmp_path / "captures"))
        assert result.returncode == 2, result.stderr
        probe = json.loads(output.read_text())["causes"][0]["probe_results"][0]
        assert probe["status"] == "timeout"
        assert Path(probe["stdout_path"]).stat().st_size == 1200000
        assert len(result.stdout) < 1000 and len(output.read_text()) < 2000
        child_pid = int(pidfile.read_text())
        for _ in range(20):
            proc = Path(f"/proc/{child_pid}/stat")
            if not proc.exists() or proc.read_text().split()[2] == "Z":
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"probe child {child_pid} survived timeout")
    finally:
        if pidfile.exists():
            child_pid = int(pidfile.read_text())
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_new_incident_reopens_resolved_cause_without_losing_prior_proof(tmp_path):
    registry = fixture(tmp_path)
    cause = row(tmp_path)
    registry.write_text(json.dumps(dict(version=1, causes=[cause])))
    artifact, paths = receipts(tmp_path)
    resolved = run_tool(registry, tmp_path / "resolved.json", "resolve",
                        *resolve_args(artifact, paths))
    assert resolved.returncode == 0, resolved.stderr
    cause["incidents"] = ["SRV-FEAT-563"]
    source = write_cause(tmp_path, cause)
    recorded = run_tool(registry, tmp_path / "recorded.json", "record",
                        "--cause-file", str(source))
    assert recorded.returncode == 0, recorded.stderr
    saved = json.loads(registry.read_text())["causes"][0]
    assert saved["status"] == "open"
    assert saved["incidents"] == ["SRV-FEAT-558", "SRV-FEAT-563"]
    assert len(saved["previous_resolutions"]) == 1
    assert saved["previous_resolutions"][0]["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_receipt_evidence_must_exist_and_be_nonempty(tmp_path):
    registry = fixture(tmp_path)
    registry.write_text(json.dumps(dict(version=1, causes=[row(tmp_path)])))
    before = registry.read_bytes()
    artifact, paths = receipts(tmp_path)
    (tmp_path / "qa.log").write_text("")
    refused = run_tool(registry, tmp_path / "refused.json", "resolve",
                       *resolve_args(artifact, paths))
    assert refused.returncode == 2
    assert registry.read_bytes() == before
