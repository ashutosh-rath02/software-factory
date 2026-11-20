#include "git.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cerrno>
#include <cstring>
#include <sstream>

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

extern "C" char** environ;


namespace {

const std::vector<std::string> READ_ONLY = {
    "for-each-ref", "ls-tree", "rev-list", "rev-parse", "status", "worktree",
};

std::vector<std::string> childEnvironment()
{
    static const char* OVERRIDE[] = {
        "GIT_OPTIONAL_LOCKS=0",
        "GIT_TERMINAL_PROMPT=0",
        "GIT_PAGER=cat",
        "GIT_ASKPASS=",
        "GCM_INTERACTIVE=never",
        "GH_PROMPT_DISABLED=1",
        "GH_NO_UPDATE_NOTIFIER=1",
        "NO_COLOR=1",
    };
    std::vector<std::string> out;
    for (char** e = environ; e && *e; ++e) {
        const std::string entry(*e);
        const std::string key = entry.substr(0, entry.find('='));
        bool overridden = false;
        for (auto* o : OVERRIDE) {
            const std::string ov(o);
            if (ov.compare(0, key.size() + 1, key + "=") == 0) { overridden = true; break; }
        }
        if (!overridden) out.push_back(entry);
    }
    for (auto* o : OVERRIDE) out.push_back(o);
    return out;
}

}

const std::vector<std::string>& delivery::gitSubcommands() { return READ_ONLY; }

delivery::Output delivery::run(const std::string& cwd, const std::vector<std::string>& argv,
                               int timeoutMs)
{
    Output out;
    if (argv.empty()) { out.failure = "no program to run"; return out; }

    int fds[2] = {-1, -1};
    if (::pipe(fds) != 0) { out.failure = std::string("pipe: ") + std::strerror(errno); return out; }

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addclose(&actions, fds[0]);
    posix_spawn_file_actions_adddup2(&actions, fds[1], STDOUT_FILENO);
    posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0);
    posix_spawn_file_actions_addclose(&actions, fds[1]);
    if (!cwd.empty()) {
#ifdef __GLIBC__
        posix_spawn_file_actions_addchdir_np(&actions, cwd.c_str());
#endif
    }

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);

    std::vector<std::string> env = childEnvironment();
    std::vector<char*> envp;
    for (auto& e : env) envp.push_back(const_cast<char*>(e.c_str()));
    envp.push_back(nullptr);

    std::vector<char*> args;
    for (auto& a : argv) args.push_back(const_cast<char*>(a.c_str()));
    args.push_back(nullptr);

    pid_t pid = -1;
    const int spawned = ::posix_spawnp(&pid, argv[0].c_str(), &actions, &attr,
                                       args.data(), envp.data());
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    ::close(fds[1]);

    if (spawned != 0) {
        ::close(fds[0]);
        out.failure = argv[0] + ": " + std::strerror(spawned);
        return out;
    }

    std::string text;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
    bool timedOut = false;
    for (;;) {
        const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now()).count();
        if (left <= 0) { timedOut = true; break; }
        struct pollfd p { fds[0], POLLIN, 0 };
        const int ready = ::poll(&p, 1, static_cast<int>(left));
        if (ready < 0) { if (errno == EINTR) continue; break; }
        if (ready == 0) { timedOut = true; break; }
        char buf[4096];
        const ssize_t n = ::read(fds[0], buf, sizeof buf);
        if (n <= 0) break;
        text.append(buf, static_cast<std::size_t>(n));
    }
    ::close(fds[0]);

    if (timedOut) ::kill(-pid, SIGKILL);
    int status = 0;
    ::waitpid(pid, &status, 0);

    out.text = std::move(text);
    if (timedOut) {
        out.failure = argv[0] + " did not answer within " + std::to_string(timeoutMs) + "ms";
        return out;
    }
    out.exitCode = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    out.ok = out.exitCode == 0;
    if (!out.ok) out.failure = argv[0] + " exited " + std::to_string(out.exitCode);
    return out;
}

delivery::Output delivery::git(const std::string& cwd, const std::string& subcommand,
                               const std::vector<std::string>& args, int timeoutMs)
{
    Output out;
    if (std::find(READ_ONLY.begin(), READ_ONLY.end(), subcommand) == READ_ONLY.end()) {
        out.failure = "refused: `git " + subcommand + "` is not one of the read-only subcommands"
                      " this tool is allowed to run";
        return out;
    }
    std::vector<std::string> argv { "git", "--no-optional-locks", subcommand };
    argv.insert(argv.end(), args.begin(), args.end());
    return run(cwd, argv, timeoutMs);
}
