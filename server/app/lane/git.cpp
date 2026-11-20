#include "git.h"

#include <cctype>
#include <climits>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <filesystem>
#include <sstream>

#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <app/agent/proc.h>

namespace fs = std::filesystem;

namespace {

lane::git::Result exec_(const std::vector<std::string>& argv, const std::string& cwd,
                        bool keepStderr = true)
{
    lane::git::Result result;
    int fds[2];
    if (::pipe(fds) != 0) {
        result.out = std::string("pipe: ") + std::strerror(errno);
        return result;
    }

    std::vector<char*> args;
    args.reserve(argv.size() + 1);
    for (const auto& a : argv) args.push_back(const_cast<char*>(a.c_str()));
    args.push_back(nullptr);

    const pid_t pid = ::fork();
    if (pid < 0) {
        ::close(fds[0]);
        ::close(fds[1]);
        result.out = std::string("fork: ") + std::strerror(errno);
        return result;
    }

    if (pid == 0) {
        ::close(fds[0]);
        ::dup2(fds[1], STDOUT_FILENO);
        if (keepStderr) {
            ::dup2(fds[1], STDERR_FILENO);
        } else {
            const int null = ::open("/dev/null", O_WRONLY);
            if (null >= 0) {
                ::dup2(null, STDERR_FILENO);
                ::close(null);
            }
        }
        ::close(fds[1]);
        if (!cwd.empty() && ::chdir(cwd.c_str()) != 0) ::_exit(127);
        ::execvp(args[0], args.data());
        ::_exit(127);
    }

    ::close(fds[1]);
    char buf[4096];
    ssize_t n;
    while ((n = ::read(fds[0], buf, sizeof buf)) > 0) result.out.append(buf, static_cast<size_t>(n));
    ::close(fds[0]);

    int status = 0;
    while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    result.code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    result.ok = (result.code == 0);
    return result;
}

}

lane::git::Result lane::git::run(const std::string& repoRoot, const std::vector<std::string>& args)
{
    std::vector<std::string> argv {"git", "-C", repoRoot};
    argv.insert(argv.end(), args.begin(), args.end());
    return exec_(argv, {});
}

lane::git::Result lane::git::runQuiet(const std::string& cwd,
                                      const std::vector<std::string>& args)
{
    std::vector<std::string> argv {"git", "-C", cwd};
    argv.insert(argv.end(), args.begin(), args.end());
    return exec_(argv, {}, /*keepStderr=*/false);
}

bool lane::git::isRepository(const std::string& repoRoot)
{
    return run(repoRoot, {"rev-parse", "--git-dir"}).ok;
}

bool lane::git::branchExists(const std::string& repoRoot, const std::string& branch)
{
    return run(repoRoot, {"rev-parse", "--verify", "--quiet", "refs/heads/" + branch}).ok;
}

namespace {

// SRV-FIX-189 — bring `base` into the branch checked out at `path`, or put everything back.
//
// A MERGE, deliberately, and not a reset or a re-cut: the branch's own commits are the reason
// `E13` keeps a branch at all, and a role's verdict is work. `base` is never moved — merging INTO
// the role's branch is the whole difference between following the work and rewriting it.
//
// The merge is the TOOL's commit and is attributed to the tool. `-c user.*` rather than the
// host's identity so that a claim does not fail on a machine where nobody has configured one,
// and so that a person reading the role branch's log can see which commit the factory made.
// `--no-verify`: the repository's commit hooks belong to the repository's own work, and a
// bookkeeping merge that runs them fails a claim for a reason nobody asked about.
//
// On failure NOTHING is left half-done. `merge --abort` unwinds what git started, `reset --hard`
// answers for the cases where there was no merge to abort, and the checkout is removed — so the
// next claim meets the same state this one did rather than adopting a tree that is neither round.
lane::git::Result takeWork_(const std::string& repoRoot, const std::string& path,
                            const std::string& base)
{
    // `base` is resolved HERE, against the repository ROOT, and merged as a commit — because the
    // same string does not name the same commit in two checkouts. `HEAD` at the root is the main
    // checkout's; `HEAD` under `git -C <worktree>` is the lane's own, and merging that is a
    // no-op nobody can see. A sha means one thing everywhere, and a base that resolves to nothing
    // is said out loud rather than merged as an empty string.
    std::string commit = lane::git::run(repoRoot, {"rev-parse", "--verify", "--quiet",
                                                   base + "^{commit}"}).out;
    while (!commit.empty() && (commit.back() == '\n' || commit.back() == '\r')) commit.pop_back();
    if (commit.empty()) {
        lane::git::Result unresolved;
        unresolved.out = "the base does not name a commit in this repository: " + base;
        lane::git::removeWorktree(repoRoot, path);
        return unresolved;
    }

    // Already in. A re-claim with nothing new on `base` leaves the branch on exactly the commit
    // it was on — no merge, no empty commit, no new sha for anybody to explain.
    if (lane::git::run(path, {"merge-base", "--is-ancestor", commit, "HEAD"}).ok) {
        lane::git::Result nothingToDo;
        nothingToDo.ok = true;
        nothingToDo.code = 0;
        return nothingToDo;
    }

    std::string was = lane::git::run(path, {"rev-parse", "HEAD"}).out;
    while (!was.empty() && (was.back() == '\n' || was.back() == '\r')) was.pop_back();

    const lane::git::Result merged =
        lane::git::run(path, {"-c", "user.name=factory", "-c", "user.email=factory@localhost",
                              "merge", "--no-edit", "--no-verify", commit});
    if (merged.ok) return merged;

    lane::git::run(path, {"merge", "--abort"});
    if (!was.empty()) lane::git::run(path, {"reset", "--hard", was});
    lane::git::removeWorktree(repoRoot, path);
    return merged;                       // ok=false, and `out` carries git's own conflict text
}

}

lane::git::Result lane::git::addWorktree(const std::string& repoRoot, const std::string& branch,
                                         const std::string& path, const std::string& base,
                                         OnExisting onExisting, bool* workNotTakenOut)
{
    if (workNotTakenOut) *workNotTakenOut = false;

    std::error_code ec;
    fs::create_directories(fs::path(path).parent_path(), ec);

    if (branchExists(repoRoot, branch)) {
        const Result added = run(repoRoot, {"worktree", "add", path, branch});
        if (!added.ok || onExisting == OnExisting::Checkout) return added;
        // SRV-FIX-184 resolved the base and this path threw it away, so the SECOND claim of a
        // role judged the round before it (SRV-FIX-189).
        const Result taken = takeWork_(repoRoot, path, base);
        if (!taken.ok && workNotTakenOut) *workNotTakenOut = true;
        return taken;
    }
    // `-b <branch> … <base>`: a NEW branch AT the base, never a second checkout OF it. The
    // difference is invisible until somebody commits — and then the second form puts a role's
    // verdict on the branch it was started to judge (SRV-FIX-184's first mandatory negative).
    return run(repoRoot, {"worktree", "add", "-b", branch, path, base});
}

bool lane::git::isRegisteredWorktree(const std::string& repoRoot, const std::string& path)
{
    const Result r = run(repoRoot, {"worktree", "list", "--porcelain"});
    if (!r.ok) return false;
    std::error_code ec;
    const fs::path want = fs::weakly_canonical(fs::path(path), ec);
    const std::string wanted = ec ? path : want.string();
    std::istringstream in(r.out);
    std::string line;
    while (std::getline(in, line)) {
        if (line.rfind("worktree ", 0) != 0) continue;
        const std::string listed = line.substr(9);
        if (listed == path || listed == wanted) return true;
        const fs::path got = fs::weakly_canonical(fs::path(listed), ec);
        if (!ec && got.string() == wanted) return true;
    }
    return false;
}

std::string lane::git::worktreeForBranch(const std::string& repoRoot, const std::string& branch)
{
    const Result r = run(repoRoot, {"worktree", "list", "--porcelain"});
    if (!r.ok) return {};
    const std::string want = "branch refs/heads/" + branch;
    std::istringstream in(r.out);
    std::string line, at;
    while (std::getline(in, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
        if (line.rfind("worktree ", 0) == 0) at = line.substr(9);
        else if (line == want) return at;
    }
    return {};
}

std::vector<lane::git::Registered> lane::git::registeredWorktrees(const std::string& repoRoot)
{
    std::vector<Registered> out;
    const Result r = run(repoRoot, {"worktree", "list", "--porcelain"});
    if (!r.ok) return out;
    std::istringstream in(r.out);
    std::string line;
    while (std::getline(in, line)) {
        while (!line.empty() && (line.back() == '\r' || line.back() == '\n')) line.pop_back();
        if (line.rfind("worktree ", 0) == 0) out.push_back({line.substr(9), {}});
        else if (line.rfind("branch refs/heads/", 0) == 0 && !out.empty())
            out.back().branch = line.substr(18);
    }
    return out;
}

bool lane::git::isDirty(const std::string& worktreePath, int& changesOut)
{
    changesOut = 0;
    const Result r = run(worktreePath, {"status", "--porcelain=v1", "--untracked-files=all"});
    if (!r.ok) {
        changesOut = -1;
        return true;
    }
    for (char c : r.out)
        if (c == '\n') ++changesOut;
    return changesOut != 0;
}

int lane::git::unmergedCommits(const std::string& worktreePath, std::string& branchOut)
{
    branchOut.clear();

    const Result head = run(worktreePath, {"rev-parse", "--abbrev-ref", "HEAD"});
    if (!head.ok) return -1;
    std::string branch = head.out;
    while (!branch.empty() && (branch.back() == '\n' || branch.back() == '\r')) branch.pop_back();
    if (branch.empty() || branch == "HEAD") return -1;
    branchOut = branch;

    const Result r = run(worktreePath, {"rev-list", "--count", "HEAD", "--not",
                                        "--exclude=" + branch,
                                        "--branches", "--tags", "--remotes"});
    if (!r.ok) return -1;

    std::string digits;
    for (char c : r.out)
        if (c >= '0' && c <= '9') digits.push_back(c);
    if (digits.empty()) return -1;
    try {
        return std::stoi(digits);
    } catch (const std::exception&) {
        return -1;
    }
}

std::vector<std::string> lane::git::ignoredContent(const std::string& worktreePath, bool& okOut,
                                                   int limit)
{
    std::vector<std::string> out;
    const Result r = run(worktreePath, {"status", "--porcelain=v1", "--ignored=matching"});
    okOut = r.ok;
    if (!r.ok) return out;
    std::istringstream in(r.out);
    std::string line;
    while (std::getline(in, line)) {
        if (line.rfind("!! ", 0) != 0) continue;
        out.push_back(line.substr(3));
        if (static_cast<int>(out.size()) >= limit) break;
    }
    return out;
}

lane::git::Holding lane::git::whatItHolds(const std::string& worktreePath)
{
    Holding held;

    int changes = 0;
    const bool dirty = isDirty(worktreePath, changes);
    if (changes < 0) return held;
    held.uncommitted = dirty ? changes : 0;

    const int unmerged = unmergedCommits(worktreePath, held.branch);
    if (unmerged < 0) return held;
    held.unmerged = unmerged;

    bool ok = false;
    held.ignored = ignoredContent(worktreePath, ok);
    if (!ok) { held.ignored.clear(); return held; }

    held.asked = true;
    return held;
}

lane::git::Result lane::git::removeWorktree(const std::string& repoRoot, const std::string& path)
{
    return run(repoRoot, {"worktree", "remove", path});
}

lane::git::Presence lane::git::presenceOf(const std::string& path)
{
    if (path.empty()) return Presence::Unknown;
    std::error_code ec;
    const fs::file_status st = fs::symlink_status(fs::path(path), ec);
    if (!ec) return fs::exists(st) ? Presence::Present : Presence::Absent;
    if (ec == std::errc::no_such_file_or_directory) return Presence::Absent;
    return Presence::Unknown;
}

lane::git::Result lane::git::pruneWorktrees(const std::string& repoRoot)
{
    return run(repoRoot, {"worktree", "prune"});
}

int lane::git::spawnConfined(const std::vector<std::string>& argv, const std::string& cwd,
                             int& readFdOut, int stdinFd, std::string* execErrorOut)
{
    readFdOut = -1;
    if (execErrorOut) execErrorOut->clear();
    if (argv.empty()) {
        if (stdinFd >= 0) ::close(stdinFd);
        if (execErrorOut) *execErrorOut = "no command was given";
        return 0;
    }

    int fds[2];
    if (::pipe(fds) != 0) {
        if (stdinFd >= 0) ::close(stdinFd);
        return 0;
    }

    std::vector<char*> args;
    args.reserve(argv.size() + 1);
    for (const auto& a : argv) args.push_back(const_cast<char*>(a.c_str()));
    args.push_back(nullptr);

    int ready[2];
    if (::pipe2(ready, O_CLOEXEC) != 0) {
        ::close(fds[0]);
        ::close(fds[1]);
        if (stdinFd >= 0) ::close(stdinFd);
        if (execErrorOut) *execErrorOut = "no pipe could be made to wait for the exec";
        return 0;
    }

    const pid_t pid = ::fork();
    if (pid < 0) {
        ::close(fds[0]);
        ::close(fds[1]);
        ::close(ready[0]);
        ::close(ready[1]);
        if (stdinFd >= 0) ::close(stdinFd);
        if (execErrorOut) *execErrorOut = "the process could not be forked";
        return 0;
    }

    if (pid == 0) {
        ::setsid();
        ::close(fds[0]);
        ::close(ready[0]);
        ::dup2(fds[1], STDOUT_FILENO);
        ::dup2(fds[1], STDERR_FILENO);
        ::close(fds[1]);
        if (stdinFd >= 0) {
            ::dup2(stdinFd, STDIN_FILENO);
            ::close(stdinFd);
        }
        int failed = 0;
        if (!cwd.empty() && ::chdir(cwd.c_str()) != 0) failed = errno;
        if (!failed) {
            ::execvp(args[0], args.data());
            failed = errno;
        }
        const ssize_t wrote = ::write(ready[1], &failed, sizeof failed);
        (void)wrote;
        ::_exit(127);
    }

    ::close(fds[1]);
    ::close(ready[1]);
    if (stdinFd >= 0) ::close(stdinFd);

    int failed = 0;
    ssize_t got = 0;
    while ((got = ::read(ready[0], &failed, sizeof failed)) < 0 && errno == EINTR) {}
    ::close(ready[0]);
    if (got == static_cast<ssize_t>(sizeof failed)) {
        ::close(fds[0]);
        int status = 0;
        while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
        if (execErrorOut)
            *execErrorOut = argv[0] + std::string(": ") + std::strerror(failed);
        return 0;
    }

    readFdOut = fds[0];
    return static_cast<int>(pid);
}

bool lane::git::isRunning(int pid)
{
    if (pid <= 0) return false;
    return ::kill(static_cast<pid_t>(pid), 0) == 0 || errno == EPERM;
}

namespace {

void reapLeader_(int pid)
{
    int status = 0;
    while (::waitpid(static_cast<pid_t>(pid), &status, WNOHANG) < 0 && errno == EINTR) {}
}

}

void lane::git::signalGroup(int pid, int sig)
{
    if (pid <= 0) return;
    ::kill(-static_cast<pid_t>(pid), sig);
}

bool lane::git::groupIsEmpty(int pid)
{
    if (pid <= 0) return true;
    reapLeader_(pid);
    if (agent::proc::state(pid) != agent::proc::State::Gone) return false;
    return agent::proc::signalGroupMembers(pid, 0) == 0;
}
