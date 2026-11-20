#include "git.h"

#include <algorithm>
#include <chrono>
#include <cerrno>
#include <cstring>
#include <sstream>

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <app/manifest/json.h>
#include <glog/logging.h>

extern "C" char** environ;

static const std::vector<std::string> ALLOWED = {
    "branch", "cat-file", "commit", "diff", "for-each-ref", "log", "ls-tree", "merge",
    "merge-base", "push", "rev-list", "rev-parse", "status", "update-ref", "worktree",
};

static std::vector<std::string> childEnvironment_()
{
    static const char* OVERRIDE[] = {
        "GIT_OPTIONAL_LOCKS=0",
        "GIT_TERMINAL_PROMPT=0",
        "GIT_PAGER=cat",
        "GIT_ASKPASS=",
        "GCM_INTERACTIVE=never",
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

static std::string argvJson_(const std::vector<std::string>& argv)
{
    std::ostringstream o;
    o << "[";
    for (std::size_t i = 0; i < argv.size(); ++i)
        o << (i ? "," : "") << "\"" << manifest::jsonEscape(argv[i]) << "\"";
    o << "]";
    return o.str();
}

const std::vector<std::string>& land::git::subcommands() { return ALLOWED; }

std::vector<std::string> land::git::lines(const std::string& text)
{
    std::vector<std::string> out;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) out.push_back(line);
    }
    return out;
}

std::string land::git::firstLine(const std::string& text)
{
    const std::vector<std::string> all = lines(text);
    return all.empty() ? std::string() : all.front();
}

land::git::Output land::git::run(const std::string& cwd, const std::string& subcommand,
                                 const std::vector<std::string>& args,
                                 const std::vector<std::string>& config, int timeoutMs)
{
    Output out;
    if (std::find(ALLOWED.begin(), ALLOWED.end(), subcommand) == ALLOWED.end()) {
        out.failure = "refused: `git " + subcommand + "` is not one of the subcommands this "
                      "module is allowed to run";
        LOG(WARNING) << "land: " << out.failure;
        return out;
    }

    std::vector<std::string> argv {"git", "--no-optional-locks"};
    if (!cwd.empty()) { argv.push_back("-C"); argv.push_back(cwd); }
    for (const auto& c : config) { argv.push_back("-c"); argv.push_back(c); }
    argv.push_back(subcommand);
    argv.insert(argv.end(), args.begin(), args.end());

    LOG(INFO) << "land: ran " << argvJson_(argv);

    int fds[2] = {-1, -1};
    if (::pipe2(fds, O_CLOEXEC) != 0) {
        out.failure = std::string("pipe: ") + std::strerror(errno);
        return out;
    }

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addclose(&actions, fds[0]);
    posix_spawn_file_actions_adddup2(&actions, fds[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, fds[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&actions, fds[1]);

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);

    std::vector<std::string> env = childEnvironment_();
    std::vector<char*> envp;
    for (auto& e : env) envp.push_back(const_cast<char*>(e.c_str()));
    envp.push_back(nullptr);

    std::vector<char*> raw;
    for (auto& a : argv) raw.push_back(const_cast<char*>(a.c_str()));
    raw.push_back(nullptr);

    pid_t pid = -1;
    const int spawned = ::posix_spawnp(&pid, argv[0].c_str(), &actions, &attr,
                                       raw.data(), envp.data());
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
    while (!out.text.empty() && (out.text.back() == '\n' || out.text.back() == '\r'))
        out.text.pop_back();
    if (timedOut) {
        out.failure = "git " + subcommand + " did not answer within "
                    + std::to_string(timeoutMs) + "ms";
        return out;
    }
    out.exitCode = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    out.ok = out.exitCode == 0;
    if (!out.ok) out.failure = "git " + subcommand + " exited " + std::to_string(out.exitCode);
    return out;
}
