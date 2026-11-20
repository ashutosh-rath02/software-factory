"""SRV-FEAT-565: index identity and stability of one production input scan."""

from pathlib import Path
import re
import subprocess

import pytest

from test_lane import build_repo, git
from test_ticket_router import assert_receipt, router, step


@pytest.mark.parametrize('change', ['blob', 'mode', 'stages'])
def test_index_only_changes_invalidate_cacheable_declared_command(router, change):
    env = router(build_body=(
        '/usr/bin/git ls-files --stage -- README.md\n'
        'if /usr/bin/git show :README.md; then :; '
        'else /usr/bin/git show :1:README.md; fi\n'))
    plan = [step('build', reuse=True)]
    env.submit('index-original', plan)
    original = env.finished('index-original')
    original_capture = assert_receipt(env, original['receipts'][0], 'build')
    env.submit('index-cached', plan)
    cached = env.finished('index-cached')
    assert cached['receipts'][0]['reused'] is True
    before_head = git(env.worktree, 'rev-parse', 'HEAD').stdout
    before_paths = git(env.worktree, 'ls-files', '-z').stdout
    file = env.worktree / 'README.md'
    before_bytes, before_mode = file.read_bytes(), file.stat().st_mode
    staged = git(env.worktree, 'ls-files', '--stage', '--', 'README.md').stdout
    mode, oid, stage = staged.split('\t')[0].split()
    assert stage == '0'
    if change == 'blob':
        written = subprocess.run(
            ['git', '-C', str(env.worktree), 'hash-object', '-w', '--stdin'],
            input='different staged bytes\n', capture_output=True, text=True, check=True)
        git(env.worktree, 'update-index', '--cacheinfo', mode, written.stdout.strip(), 'README.md')
    elif change == 'mode':
        git(env.worktree, 'update-index', '--chmod=+x', 'README.md')
    else:
        subprocess.run(
            ['git', '-C', str(env.worktree), 'update-index', '--index-info'],
            input=f'0 {oid}\tREADME.md\n{mode} {oid} 1\tREADME.md\n',
            capture_output=True, text=True, check=True)
    assert git(env.worktree, 'ls-files', '--stage', '--', 'README.md').stdout != staged
    assert git(env.worktree, 'rev-parse', 'HEAD').stdout == before_head
    assert git(env.worktree, 'ls-files', '-z').stdout == before_paths
    assert (file.read_bytes(), file.stat().st_mode) == (before_bytes, before_mode)
    env.submit('index-changed', plan)
    changed = env.finished('index-changed')
    assert changed['source'] != cached['source'], (
        f'index {change} changed while HEAD, paths and working bytes stayed equal; '
        f'the router reused the old input identity: {changed}')
    assert changed['state'] == 'completed'
    assert changed['receipts'][0]['reused'] is False
    changed_capture = assert_receipt(env, changed['receipts'][0], 'build')
    assert changed_capture != original_capture
    assert env.invocations() == ['build', 'build']


RUNNER = r'''
#include <app/api/routing-inputs.h>

#include <cerrno>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

lane::git::Result gitRead(const std::string& root, const std::vector<std::string>& args)
{
    int output[2];
    if (::pipe(output) != 0) throw std::runtime_error("pipe failed");
    const pid_t child = ::fork();
    if (child == 0) {
        ::close(output[0]);
        ::dup2(output[1], STDOUT_FILENO);
        ::dup2(output[1], STDERR_FILENO);
        ::close(output[1]);
        std::vector<std::string> owned{"git", "-C", root};
        owned.insert(owned.end(), args.begin(), args.end());
        std::vector<char*> argv;
        for (auto& arg : owned) argv.push_back(arg.data());
        argv.push_back(nullptr);
        ::execvp(argv.front(), argv.data());
        ::_exit(127);
    }
    ::close(output[1]);
    if (child < 0) throw std::runtime_error("fork failed");
    lane::git::Result result;
    char buffer[4096];
    for (;;) {
        const ssize_t count = ::read(output[0], buffer, sizeof buffer);
        if (count == 0) break;
        if (count < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("read failed");
        }
        result.out.append(buffer, static_cast<std::size_t>(count));
    }
    ::close(output[0]);
    int status = 0;
    while (::waitpid(child, &status, 0) < 0)
        if (errno != EINTR) throw std::runtime_error("wait failed");
    result.code = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
    result.ok = result.code == 0;
    return result;
}

void mutate(const std::string& root, const std::string& action)
{
    const std::string path = root + "/000-input.txt";
    if (action == "index-mode") {
        if (!gitRead(root, {"update-index", "--chmod=+x", "000-input.txt"}).ok)
            throw std::runtime_error("index fixture mutation failed");
    } else if (action == "retarget") {
        if (::unlink(path.c_str()) != 0 || ::symlink("new-target", path.c_str()) != 0)
            throw std::runtime_error("symlink fixture mutation failed");
    } else {
        struct stat before{};
        if (action == "rewrite" && ::stat(path.c_str(), &before) != 0)
            throw std::runtime_error("fixture stat failed");
        {
            std::ofstream output(path);
            output << "new\n";
            if (!output) throw std::runtime_error("fixture write failed");
        }
        if (action == "rewrite") {
            const struct timespec times[]{before.st_atim, before.st_mtim};
            if (::utimensat(AT_FDCWD, path.c_str(), times, 0) != 0)
                throw std::runtime_error("fixture timestamp restore failed");
        }
    }
}

int main(int argc, char** argv)
{
    if (argc != 4) return 2;
    const std::string root = argv[1], manifest = argv[2], action = argv[3];
    lane::Store::Record record;
    record.name = "lane/01";
    record.ticketId = "TCK-001";
    record.workspace = "core";
    record.branch = "ticket/TCK-001";
    record.worktree = root;
    record.claimedAtMs = 42;
    unsigned int heads = 0;
    bool mutated = false;
    const auto read = [&](const std::vector<std::string>& args) {
        for (const auto& arg : args) {
            if (arg == "rev-parse" && ++heads == 2 && action != "stable") {
                mutate(root, action);
                mutated = true;
                break;
            }
        }
        return gitRead(root, args);
    };
    const auto result = api::routingInputs::fingerprint(record, "effective manifest", manifest, read);
    std::cout << "source=" << result.source << "\nerror=" << result.error
              << "\nmutated=" << mutated << "\nheads=" << heads << '\n';
}
'''


@pytest.fixture(scope='module')
def fingerprint_runner(tmp_path_factory):
    server = Path(__file__).resolve().parents[1]
    folder = tmp_path_factory.mktemp('routing-input-runner')
    source, binary = folder / 'runner.cpp', folder / 'runner'
    source.write_text(RUNNER)
    compiled = subprocess.run([
        'c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', f'-I{server}', str(source),
        str(server / 'app/api/routing-inputs.cpp'), str(server / 'app/routing/bytes.cpp'),
        '-lcrypto', '-o', str(binary)], capture_output=True, text=True, timeout=90)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return binary


def scan(binary, root, action):
    process = subprocess.run([str(binary), str(root), str(root / '.factory/factory.json'), action],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
    values = dict(line.split('=', 1) for line in process.stdout.splitlines())
    assert values['heads'] == '2', values
    return values


@pytest.mark.parametrize('change', ['rewrite', 'retarget', 'recreate', 'index-mode'])
def test_scan_refuses_inputs_changed_after_file_pass(fingerprint_runner, tmp_path, change):
    root = build_repo(tmp_path / 'repo')
    file = root / '000-input.txt'
    if change == 'retarget':
        file.symlink_to('old-target')
    else:
        file.write_text('old\n')
    git(root, 'add', '000-input.txt')
    git(root, 'commit', '-m', 'tracked input for production scan')
    if change == 'recreate':
        file.unlink()
    stable = scan(fingerprint_runner, root, 'stable')
    assert re.fullmatch('[0-9a-f]{64}', stable['source']) and stable['error'] == '', stable
    before = file.lstat() if file.exists() else None
    changed = scan(fingerprint_runner, root, change)
    assert changed['mutated'] == '1', changed
    if change == 'rewrite':
        after = file.stat()
        assert after.st_size == before.st_size and after.st_mtime_ns == before.st_mtime_ns
        assert file.read_bytes() == b'new\n'
    assert changed['source'] == '' and 'changed' in changed['error'], (
        f'{change} happened at the final Git phase after production read every source path; '
        f'the scan still advertised a usable source: {changed}')
