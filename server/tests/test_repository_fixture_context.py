"""Repository-reading checks must use this checkout and controlled loader prerequisites."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT
import test_agent_trust
import test_manifest


def _fixture_binary_probe(env, code, *args):
    env = dict(env, PYTHONPATH=str(REPO_ROOT / "server/tests"))
    return subprocess.run([sys.executable, "-c", code, *map(str, args)],
                          cwd=REPO_ROOT / "server", capture_output=True, text=True,
                          timeout=10, env=env)


def test_server_binary_default_remains_in_this_checkout(monkeypatch):
    env = dict(os.environ)
    env.pop("FACTORY_TEST_SERVER_BINARY", None)
    ran = _fixture_binary_probe(env, "import conftest; print(conftest.BINARY)")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == str(REPO_ROOT / ".cache/server/build/factory-server")


def test_declared_server_binary_override_is_selected(tmp_path):
    binary = tmp_path / "supervisor-factory-server"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    env = dict(os.environ, FACTORY_TEST_SERVER_BINARY=str(binary))
    ran = _fixture_binary_probe(env, "import conftest; print(conftest.BINARY)")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == str(binary)


def test_missing_declared_server_binary_fails_before_spawn(tmp_path):
    missing = tmp_path / "missing-factory-server"
    env = dict(os.environ, FACTORY_TEST_SERVER_BINARY=str(missing))
    ran = _fixture_binary_probe(
        env,
        "import conftest, pathlib, sys; "
        "conftest._start_instance(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))",
        REPO_ROOT, tmp_path / "server.log")
    assert ran.returncode != 0
    assert "FACTORY_TEST_SERVER_BINARY" in ran.stderr
    assert str(missing) in ran.stderr


def test_trust_enumeration_reads_the_server_checkout_not_its_parent():
    assert test_agent_trust.REPO_ROOT == REPO_ROOT
    assert (test_agent_trust.REPO_ROOT / "server/app").samefile(test_agent_trust.APP)


def test_qa_role_fixture_loads_without_ambient_profile_directories(serve, tmp_path, monkeypatch):
    isolated = tmp_path / "empty-ambient-home"
    isolated.mkdir()
    monkeypatch.setenv("HOME", str(isolated))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "FACTORY_LANE_HOME"):
        monkeypatch.delenv(name, raising=False)
    test_manifest.test_this_repositorys_manifest_declares_the_qa_role(serve, tmp_path)
    assert not list(isolated.iterdir()), "the fixture must not furnish the ambient home"


LIVE_CLAUDE_CASES = {
    "test_confine.py": ("test_an_agent_starts_and_answers",),
    "test_session_permissions.py": (
        "test_a_headless_session_in_this_repository_can_run_its_own_gates",
        "test_a_headless_session_can_stage_and_commit_its_own_verdict"),
}


def test_authenticated_claude_cases_have_explicit_env_zero_guard():
    tests = REPO_ROOT / "server/tests"
    for filename, names in LIVE_CLAUDE_CASES.items():
        tree = ast.parse((tests / filename).read_text())
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for name in names:
            decorators = [ast.unparse(item) for item in functions[name].decorator_list]
            assert any("pytest.mark.skipif" in item and "FACTORY_REAL_CLI" in item
                       and "== '0'" in item for item in decorators), (filename, name, decorators)


@pytest.mark.repository
def test_env_zero_skips_authenticated_claude_before_provider_or_fixtures(tmp_path):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    marker = tmp_path / "claude-invoked"
    stub = stub_dir / "claude"
    stub.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 97\n")
    stub.chmod(0o755)
    nodeids = [f"{REPO_ROOT / 'server/tests' / filename}::{name}"
               for filename, names in LIVE_CLAUDE_CASES.items() for name in names]
    ran = subprocess.run([sys.executable, "-m", "pytest", "-v", "-r", "s", *nodeids],
                         cwd=REPO_ROOT / "server", capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, FACTORY_REAL_CLI="0", PYTEST_ADDOPTS="",
                                  PATH=str(stub_dir) + os.pathsep + os.environ["PATH"]))
    output = ran.stdout + ran.stderr
    assert ran.returncode == 0, output
    assert "3 skipped" in output, output
    for names in LIVE_CLAUDE_CASES.values():
        for name in names:
            assert name in output, output
    assert not marker.exists(), "the provider stub was invoked despite FACTORY_REAL_CLI=0"
