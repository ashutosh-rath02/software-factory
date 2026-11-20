"""SRV-FEAT-558: a declared executable keeps its existing, confined bundle context."""

import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT, SERVER_DIR


POLICY_DRIVER = r'''
#include <app/lane/confine.h>
#include <iostream>
int main(int argc, char** argv) {
    if (argc < 7 || (argc - 5) % 2) return 64;
    std::vector<lane::confine::AgentBinary> agents;
    for (int i = 5; i < argc; i += 2) agents.push_back({argv[i], argv[i + 1]});
    auto policy = lane::confine::build(argv[1], argv[2], "ticket/context", {argv[3]},
                                       {}, "", {}, agents);
    if (!policy.ok) { std::cerr << policy.reason; return 2; }
    for (const auto& word : lane::confine::wrap(policy, argv[4]))
        std::cout << word << '\0';
}
'''

BUNDLED_PROGRAM = r'''
#include <cstdio>
#include <string>
#include <unistd.h>
int main(int argc, char**) {
    if (argc > 1) { std::puts("PROGRAM-RAN"); return 0; }
    char path[8192];
    const auto n = ::readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (n < 0) return 110;
    path[n] = '\0';
    std::string helper(path);
    helper = helper.substr(0, helper.rfind('/')) + "/bundle-helper";
    ::execl(helper.c_str(), helper.c_str(), static_cast<char*>(nullptr));
    std::fprintf(stderr, "adjacent helper could not execute: %s\n", helper.c_str());
    return 111;
}
'''


def checked(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("agent-binary-compile")
    driver = scratch / "policy-driver.cpp"
    driver.write_text(POLICY_DRIVER)
    checked("g++", "-std=c++17", "-I", str(SERVER_DIR), str(driver),
            str(SERVER_DIR / "app/lane/confine.cpp"), "-o", str(scratch / "policy-driver"))
    program = scratch / "bundled-program.cpp"
    program.write_text(BUNDLED_PROGRAM)
    checked("g++", "-std=c++17", str(program), "-o", str(scratch / "bundled-program"))
    return scratch


@pytest.fixture
def boundary(tmp_path, compiled):
    root = tmp_path / "repository"
    root.mkdir()
    checked("git", "init", "-b", "main", str(root))
    checked("git", "config", "user.email", "test@factory.invalid", cwd=root)
    checked("git", "config", "user.name", "test", cwd=root)
    (root / "tracked").write_text("unchanged\n")
    checked("git", "add", "tracked", cwd=root)
    checked("git", "commit", "-m", "fixture", cwd=root)
    work = tmp_path / "worktree"
    checked("git", "worktree", "add", "-b", "ticket/context", str(work), cwd=root)
    before = checked("git", "status", "--porcelain", cwd=root)
    main = checked("git", "rev-parse", "main", cwd=root)

    # Outside /tmp, which the production namespace intentionally replaces after the RO bind.
    with tempfile.TemporaryDirectory(prefix=".agent-binary-context-", dir=REPO_ROOT) as made:
        bundles = Path(made)
        selected = bundles / "selected"
        unselected = bundles / "unselected"
        for directory in (selected, unselected):
            directory.mkdir()
            shutil.copy2(compiled / "bundled-program", directory / "program")
            helper = directory / "bundle-helper"
            helper.write_text("#!/bin/sh\nprintf 'ADJACENT-HELPER-RAN\\n'\n")
            helper.chmod(0o755)
        secret = unselected / "not-declared"
        secret.write_text("not exposed\n")
        (selected / "outside-link").symlink_to(secret)

        class Boundary:
            def argv(self, command, visible=selected, agents=None):
                agents = agents or {"selected-agent": selected / "program",
                                    "unselected-agent": unselected / "program"}
                result = subprocess.run(
                    [str(compiled / "policy-driver"), str(root), str(work), str(visible), command,
                     *(word for name, path in agents.items() for word in (name, str(path)))],
                    capture_output=True, timeout=30)
                assert result.returncode == 0, result.stderr.decode()
                return result.stdout.decode().rstrip("\0").split("\0")

            def run(self, argv):
                result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
                print(result.stdout + result.stderr)
                return result

        yield Boundary(), selected, unselected
        assert secret.read_text() == "not exposed\n"
    assert checked("git", "status", "--porcelain", cwd=root) == before
    assert checked("git", "rev-parse", "main", cwd=root) == main


def ro_binds(argv):
    return {(argv[i + 1], argv[i + 2]) for i, word in enumerate(argv)
            if word in ("--ro-bind", "--ro-bind-try")}


def test_an_already_bound_bundle_keeps_its_adjacent_helper_in_a_real_lane(boundary):
    lane, selected, unselected = boundary
    argv = lane.argv("selected-agent")
    result = lane.run(argv)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ADJACENT-HELPER-RAN"
    assert ["--symlink", str(selected / "program"), "/factory/bin/selected-agent"] in [
        argv[i:i + 3] for i in range(len(argv) - 2)]
    assert (str(selected / "program"), "/factory/bin/selected-agent") not in ro_binds(argv)
    own_binds = {(src, dst) for src, dst in ro_binds(argv) if src.startswith(str(selected.parent))}
    assert own_binds == {(str(selected), str(selected)),
                         (str(unselected / "program"), "/factory/bin/unselected-agent")}


def test_an_unselected_file_projection_does_not_reveal_its_siblings(boundary):
    lane, selected, unselected = boundary
    command = (
        "unselected-agent --identity && "
        "test ! -L /factory/bin/unselected-agent && "
        f"test ! -e {shlex.quote(str(unselected / 'not-declared'))} && "
        f"test ! -e {shlex.quote(str(selected / 'outside-link'))} && "
        f"test ! -e {shlex.quote(str(unselected / 'bundle-helper'))}")
    argv = lane.argv(command)
    result = lane.run(argv)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PROGRAM-RAN"
    assert (str(unselected / "program"), "/factory/bin/unselected-agent") in ro_binds(argv)
    assert (str(unselected), str(unselected)) not in ro_binds(argv)


def test_a_bundle_shadowed_by_tmp_retains_its_working_file_projection(boundary, tmp_path, compiled):
    lane, _, _ = boundary
    selected = tmp_path / "shadowed-bundle"
    selected.mkdir()
    shutil.copy2(compiled / "bundled-program", selected / "program")
    command = ("shadowed-agent --identity && test ! -L /factory/bin/shadowed-agent && "
               f"test ! -e {shlex.quote(str(selected / 'program'))}")
    argv = lane.argv(command, selected, {"shadowed-agent": selected / "program"})
    result = lane.run(argv)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PROGRAM-RAN"
    assert (str(selected / "program"), "/factory/bin/shadowed-agent") in ro_binds(argv)


def test_a_declared_symlink_does_not_make_its_unmounted_target_directory_visible(boundary):
    lane, selected, unselected = boundary
    link = selected / "linked-program"
    link.symlink_to(unselected / "program")
    command = ("linked-agent --identity && test ! -L /factory/bin/linked-agent && "
               f"test ! -e {shlex.quote(str(unselected / 'bundle-helper'))}")
    argv = lane.argv(command, selected, {"linked-agent": link})
    result = lane.run(argv)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PROGRAM-RAN"
    assert (str(unselected / "program"), "/factory/bin/linked-agent") in ro_binds(argv)
