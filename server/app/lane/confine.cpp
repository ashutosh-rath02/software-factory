#include "confine.h"

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>

#include <pwd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>

#include "git.h"

namespace fs = std::filesystem;

namespace {

constexpr const char* HOME_INSIDE = "/home/lane";
constexpr const char* SCRATCH_INSIDE = "/tmp/factory";

constexpr const char* AGENT_CONFIG_INSIDE = "/factory/agent-config";
constexpr const char* AGENT_BIN_INSIDE = "/factory/bin";
constexpr const char* LANE_HOME_ENV = "FACTORY_LANE_HOME";

constexpr const char* SYSTEM_EXEC_DIRS[] = {"/usr/local/sbin", "/usr/local/bin", "/usr/sbin",
                                            "/usr/bin", "/sbin", "/bin"};

bool isUnder(const std::string& path, const std::string& dir)
{
    if (dir.empty() || path.empty()) return false;
    if (path == dir) return true;
    const std::string prefix = dir.back() == '/' ? dir : dir + "/";
    return path.compare(0, prefix.size(), prefix) == 0;
}

std::string resolved(const std::string& path)
{
    std::error_code ec;
    const fs::path real = fs::weakly_canonical(fs::path(path), ec);
    return ec ? path : real.string();
}

std::vector<std::string> homesOfALane()
{
    std::vector<std::string> out;
    out.push_back(resolved(HOME_INSIDE));
    for (const char* name : {"HOME", LANE_HOME_ENV})
        if (const char* value = ::getenv(name))
            if (*value) {
                const std::string here = resolved(value);
                if (here != HOME_INSIDE && ::getenv(LANE_HOME_ENV)
                    && here == resolved(::getenv(LANE_HOME_ENV)))
                    out.push_back(here);
            }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    return out;
}

std::string developerHomeImpl()
{
    const char* home = ::getenv("HOME");
    const char* lane = ::getenv(LANE_HOME_ENV);
    const bool insideALane =
        home && *home
        && (std::string(home) == HOME_INSIDE || (lane && *lane && std::string(home) == lane));
    if (!insideALane && home && *home) return resolved(home);
    if (const struct passwd* pw = ::getpwuid(::getuid()))
        if (pw->pw_dir && *pw->pw_dir) return resolved(pw->pw_dir);
    if (home && *home) return resolved(home);
    return {};
}

std::string binaryPath()
{
    if (const char* override_ = ::getenv("FACTORY_CONFINE_BIN")) return override_;
    std::error_code ec;
    for (const char* candidate : {"/usr/bin/bwrap", "/bin/bwrap", "/usr/local/bin/bwrap"})
        if (fs::exists(candidate, ec)) return candidate;
    return "/usr/bin/bwrap";
}

std::string gitDirOf(const std::string& worktree)
{
    std::ifstream in(worktree + "/.git");
    if (!in) return {};
    std::string line;
    if (!std::getline(in, line)) return {};
    const std::string tag = "gitdir:";
    if (line.compare(0, tag.size(), tag) != 0) return {};
    std::string path = line.substr(tag.size());
    while (!path.empty() && (path.front() == ' ' || path.front() == '\t')) path.erase(path.begin());
    while (!path.empty() && (path.back() == '\r' || path.back() == ' ')) path.pop_back();
    if (path.empty()) return {};
    if (path.front() != '/') path = worktree + "/" + path;
    return resolved(path);
}

std::string commonDirOf(const std::string& repoRoot)
{
    std::error_code ec;
    const std::string direct = repoRoot + "/.git";
    if (fs::is_directory(fs::path(direct), ec)) return resolved(direct);

    const std::string gitDir = gitDirOf(repoRoot);
    if (gitDir.empty() || !fs::is_directory(fs::path(gitDir), ec)) return {};

    std::ifstream in(gitDir + "/commondir");
    std::string line;
    if (!in || !std::getline(in, line)) return resolved(gitDir);
    while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
    if (line.empty()) return resolved(gitDir);
    if (line.front() != '/') line = gitDir + "/" + line;
    return resolved(line);
}

std::string invent(const std::string& base, const std::vector<std::string>& taken)
{
    for (int n = 1; n <= 64; ++n) {
        const std::string candidate = n == 1 ? base : base + std::to_string(n);
        const bool clash = std::any_of(taken.begin(), taken.end(), [&](const std::string& t) {
            return !t.empty() && (isUnder(t, candidate) || isUnder(candidate, t));
        });
        if (!clash) return candidate;
    }
    return {};
}

bool holdsAnExecutable(const fs::path& dir)
{
    std::error_code ec;
    if (!fs::is_directory(dir, ec)) return false;
    for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec)) {
        if (it->is_directory(ec)) continue;
        if (::access(it->path().c_str(), X_OK) == 0) return true;
    }
    return false;
}

std::vector<std::string> splitPath(const char* value)
{
    std::vector<std::string> out;
    const std::string all = value ? value : "";
    for (std::size_t at = 0; at <= all.size();) {
        const std::size_t colon = all.find(':', at);
        const std::string dir =
            all.substr(at, colon == std::string::npos ? std::string::npos : colon - at);
        if (!dir.empty() && dir.front() == '/'
            && std::find(out.begin(), out.end(), dir) == out.end())
            out.push_back(dir);
        if (colon == std::string::npos) break;
        at = colon + 1;
    }
    return out;
}

std::vector<std::string> pathsIn(const std::string& line)
{
    std::vector<std::string> out;
    std::string token;
    auto flush = [&] {
        while (!token.empty() && (token.back() == ':' || token.back() == ',' || token.back() == '.'
                                  || token.back() == ')' || token.back() == ';'))
            token.pop_back();
        if (token.size() > 1 && token.front() == '/') out.push_back(token);
        token.clear();
    };
    for (char c : line) {
        if (c == ' ' || c == '\t' || c == '\'' || c == '"' || c == '`' || c == '\n') flush();
        else token.push_back(c);
    }
    flush();
    return out;
}

bool originalBinaryIsReachable(const lane::confine::Policy& policy,
                              const lane::confine::Policy::AgentBinary& binary)
{
    if (binary.host == binary.inside) return false;
    for (const std::string& shadow : std::vector<std::string>{
             "/tmp", "/proc", "/dev", AGENT_BIN_INSIDE, policy.home, policy.scratch,
             policy.agentConfigInside, policy.worktree, policy.gitCommonDir})
        if (isUnder(binary.host, shadow)) return false;
    for (const auto& toolchain : policy.toolchain)
        if (toolchain.host != toolchain.inside && isUnder(binary.host, toolchain.inside))
            return false;
    std::error_code ec;
    return std::any_of(policy.hostReadOnly.begin(), policy.hostReadOnly.end(),
        [&](const std::string& directory) {
            return fs::is_directory(fs::path(directory), ec) && isUnder(binary.host, directory);
        });
}

}

std::string lane::confine::developerHome()
{
    return developerHomeImpl();
}

lane::confine::Policy lane::confine::build(const std::string& repoRoot,
                                           const std::string& worktree,
                                           const std::string& branch,
                                           const std::vector<std::string>& hostReadOnly,
                                           const std::vector<std::string>& devices,
                                           const std::string& agentConfig,
                                           const std::vector<std::pair<std::string,
                                                                       std::string>>& toolchain,
                                           const std::vector<AgentBinary>& agentBinaries,
                                           const std::vector<std::string>& runtimeDirectories)
{
    Policy p;
    p.binary = binaryPath();
    p.repoRoot = resolved(repoRoot);
    p.worktree = resolved(worktree);

    std::error_code ec;
    if (!fs::is_regular_file(fs::path(p.binary), ec) || ::access(p.binary.c_str(), X_OK) != 0) {
        p.reason = "no confinement helper at " + p.binary;
        return p;
    }
    if (!fs::is_directory(fs::path(p.worktree), ec)) {
        p.reason = "the lane has no worktree at " + p.worktree;
        return p;
    }

    const std::size_t slash = branch.rfind('/');
    if (branch.empty() || slash == std::string::npos || slash == 0) {
        p.reason = "the lane branch " + branch + " is not namespaced, so `main` could not be "
                   "kept read-only";
        return p;
    }
    const std::string ns = branch.substr(0, slash);

    p.gitCommonDir = commonDirOf(p.repoRoot);
    if (p.gitCommonDir.empty() || !fs::is_directory(fs::path(p.gitCommonDir), ec)) {
        p.reason = "no git repository at " + p.repoRoot + "/.git";
        return p;
    }
    p.gitDir = gitDirOf(p.worktree);
    if (p.gitDir.empty() || !fs::is_directory(fs::path(p.gitDir), ec)) {
        p.reason = "the lane's worktree has no git directory";
        return p;
    }

    p.objectsDir = p.gitCommonDir + "/objects";
    p.refsDir = p.gitCommonDir + "/refs/heads/" + ns;
    p.logsRefsDir = p.gitCommonDir + "/logs/refs/heads/" + ns;

    fs::create_directories(fs::path(p.refsDir), ec);
    fs::create_directories(fs::path(p.logsRefsDir), ec);

    const std::string ours = branch.substr(slash + 1);
    for (const std::string& dir : {p.refsDir, p.logsRefsDir}) {
        std::error_code scanEc;
        for (fs::directory_iterator it(fs::path(dir), scanEc), end;
             !scanEc && it != end; it.increment(scanEc)) {
            std::error_code kindEc;
            if (!it->is_regular_file(kindEc) || kindEc) continue;
            if (it->path().filename().string() == ours) continue;
            p.foreignRefs.push_back(it->path().string());
        }
    }
    std::sort(p.foreignRefs.begin(), p.foreignRefs.end());
    if (!fs::is_directory(fs::path(p.objectsDir), ec)
        || !fs::is_directory(fs::path(p.refsDir), ec)
        || !fs::is_directory(fs::path(p.logsRefsDir), ec)) {
        p.reason = "git's object or ref directories are not where they are expected";
        return p;
    }

    for (const std::string& given : hostReadOnly) {
        if (given.empty()) continue;
        const std::string real = resolved(given);
        if (!fs::exists(fs::path(real), ec)) continue;
        if (std::find(p.hostReadOnly.begin(), p.hostReadOnly.end(), real) != p.hostReadOnly.end())
            continue;
        p.hostReadOnly.push_back(real);
    }

    for (const std::string& given : devices) {
        if (given.empty()) continue;
        const std::string real = resolved(given);
        struct ::stat st {};
        if (::stat(real.c_str(), &st) != 0) continue;
        if (!S_ISCHR(st.st_mode) && !S_ISBLK(st.st_mode)) {
            continue;
        }
        const bool already = std::any_of(p.devices.begin(), p.devices.end(),
            [&](const Policy::Device& d) { return d.declared == given; });
        if (already) continue;
        p.devices.push_back({real, given});
    }

    const std::string realHome = lane::confine::developerHome();

    if (!agentConfig.empty()) {
        p.agentConfig = resolved(agentConfig);
        p.agentConfigInside = AGENT_CONFIG_INSIDE;
        if (realHome.empty()) {
            p.reason = "this process cannot tell where the developer's home is — no HOME in its "
                       "environment and no passwd entry for uid "
                     + std::to_string(static_cast<int>(::getuid()))
                     + " — so it cannot prove that the lane's own agent configuration directory "
                     + p.agentConfig + " is outside it";
            return p;
        }
        if (isUnder(p.agentConfig, realHome)) {
            p.reason = "the lane's own agent configuration directory " + p.agentConfig
                     + " is inside the developer's home " + realHome
                     + ", and a directory a lane may write must not be";
            return p;
        }
        for (const std::string& laneHome : homesOfALane()) {
            if (!isUnder(p.agentConfig, laneHome)) continue;
            p.reason = "the lane's own agent configuration directory " + p.agentConfig
                     + " is inside a lane's home " + laneHome
                     + ", and a directory a lane may write must not be";
            return p;
        }
    }

    std::vector<std::pair<std::string, std::string>> declaredToolchain;
    for (const auto& given : toolchain) {
        const std::string& declared = given.first;
        if (declared.empty() || given.second.empty()) continue;
        if (declared.compare(0, 2, "~/") != 0 && declared.front() != '/') continue;
        if (!fs::exists(fs::path(given.second), ec)) continue;
        declaredToolchain.emplace_back(declared, given.second);
    }

    for (const AgentBinary& binary : agentBinaries) {
        if (binary.name.empty() || binary.path.empty() || binary.name.find('/') != std::string::npos
            || !fs::is_regular_file(fs::path(binary.path), ec)) continue;
        const std::string inside = std::string(AGENT_BIN_INSIDE) + "/" + binary.name;
        const bool already = std::any_of(p.agentBinaries.begin(), p.agentBinaries.end(),
            [&](const Policy::AgentBinary& held) { return held.inside == inside; });
        if (!already) p.agentBinaries.push_back({resolved(binary.path), inside});
        for (const auto& given : declaredToolchain) {
            const fs::path companion = resolved(given.second);
            if (companion.parent_path() != fs::path(resolved(binary.path)).parent_path()
                || !fs::is_regular_file(companion, ec)
                || ::access(companion.c_str(), X_OK) != 0) continue;
            const std::string target = std::string(AGENT_BIN_INSIDE) + "/"
                                     + companion.filename().string();
            const bool held = std::any_of(p.agentBinaries.begin(), p.agentBinaries.end(),
                [&](const Policy::AgentBinary& entry) { return entry.inside == target; });
            if (!held) p.agentBinaries.push_back({companion.string(), target});
        }
    }

    std::vector<std::string> taken {p.repoRoot, p.worktree, p.gitCommonDir, p.gitDir,
                                    p.objectsDir, p.refsDir, p.logsRefsDir, p.agentConfig};
    for (const auto& binary : p.agentBinaries) taken.push_back(binary.host);
    taken.insert(taken.end(), p.hostReadOnly.begin(), p.hostReadOnly.end());
    for (const auto& t : declaredToolchain) taken.push_back(t.second);
    for (const auto& d : p.devices) taken.push_back(d.node);
    taken.push_back(realHome);
    if (const char* here = ::getenv("HOME")) if (*here) taken.push_back(resolved(here));
    if (const char* here = ::getenv(LANE_HOME_ENV)) if (*here) taken.push_back(resolved(here));
    if (const char* here = ::getenv("TMPDIR")) if (*here) taken.push_back(resolved(here));

    p.scratch = invent(SCRATCH_INSIDE, taken);
    taken.push_back(p.scratch);
    p.home = invent(HOME_INSIDE, taken);
    if (p.home.empty() || p.scratch.empty()) {
        p.reason = "the confinement could not invent a home and a scratch directory for this "
                   "lane that no path it has to carry in already occupies";
        return p;
    }

    for (const std::string& given : runtimeDirectories) {
        const fs::path path(given);
        if (path.filename().empty() || path.parent_path() != fs::path("/tmp")
            || path.lexically_normal().string() != given) {
            p.reason = "an agent runtime directory must be a direct, normalized child of /tmp";
            return p;
        }
        std::vector<std::string> occupied {p.repoRoot, p.worktree, p.gitCommonDir, p.gitDir,
                                           p.objectsDir, p.refsDir, p.logsRefsDir,
                                           p.agentConfig, p.scratch, p.home};
        occupied.insert(occupied.end(), p.hostReadOnly.begin(), p.hostReadOnly.end());
        for (const auto& binary : p.agentBinaries) occupied.push_back(binary.inside);
        for (const auto& t : declaredToolchain) {
            occupied.push_back(t.second);
            occupied.push_back(t.first.compare(0, 2, "~/") == 0
                                   ? p.home + t.first.substr(1) : t.first);
        }
        for (const auto& path_ : occupied)
            if (isUnder(given, path_) || isUnder(path_, given)) {
                p.reason = "an agent runtime directory overlaps a carried path: " + given;
                return p;
            }
        if (std::find(p.runtimeDirectories.begin(), p.runtimeDirectories.end(), given)
            == p.runtimeDirectories.end()) p.runtimeDirectories.push_back(given);
    }

    for (const auto& given : declaredToolchain) {
        const std::string& declared = given.first;
        const std::string inside =
            declared.compare(0, 2, "~/") == 0 ? p.home + declared.substr(1) : declared;
        if (inside == "/" || inside == p.home) continue;
        const bool already = std::any_of(p.toolchain.begin(), p.toolchain.end(),
            [&](const Policy::Toolchain& t) { return t.inside == inside; });
        if (already) continue;
        p.toolchain.push_back({given.second, inside});
    }

    p.writable = {p.worktree, p.gitDir, p.objectsDir, p.refsDir, p.logsRefsDir};
    if (!p.agentConfig.empty()) p.writable.push_back(p.agentConfig);

    p.writableInside = {p.home, p.scratch};
    p.writableInside.insert(p.writableInside.end(), p.runtimeDirectories.begin(),
                            p.runtimeDirectories.end());
    if (!p.agentConfigInside.empty()) p.writableInside.push_back(p.agentConfigInside);

    {
        const std::string real = resolved("/etc/resolv.conf");
        if (real != "/etc/resolv.conf" && !isUnder(real, "/etc")
            && fs::is_regular_file(fs::path(real), ec))
            p.resolvConf = real;
    }

    if (!realHome.empty()) {
        for (const std::string& ro : p.hostReadOnly) {
            if (isUnder(ro, realHome)) {
                p.hiddenHome = realHome;
                break;
            }
        }
        for (const auto& t : p.toolchain)
            if (isUnder(t.host, realHome)) { p.hiddenHome = realHome; break; }
    }

    for (const std::string& w : p.writable) {
        if (isUnder(p.repoRoot, w) && p.repoRoot != w) {
            p.reason = "writable path " + w + " is above the repository root";
            return p;
        }
    }

    {
        std::vector<std::string> mounted {"/usr", "/etc", p.worktree};
        mounted.insert(mounted.end(), p.hostReadOnly.begin(), p.hostReadOnly.end());
        for (const auto& t : p.toolchain) mounted.push_back(t.inside);

        std::vector<std::string> dirs;
        auto keep = [&dirs](const std::string& dir) {
            if (!dir.empty() && std::find(dirs.begin(), dirs.end(), dir) == dirs.end())
                dirs.push_back(dir);
        };
        if (!p.agentBinaries.empty()) keep(AGENT_BIN_INSIDE);
        for (const auto& t : p.toolchain) {
            if (holdsAnExecutable(fs::path(t.host))) keep(t.inside);
            if (holdsAnExecutable(fs::path(t.host) / "bin")) keep(t.inside + "/bin");
        }
        for (const std::string& entry : splitPath(::getenv("PATH"))) {
            std::string candidate = entry;
            if (!realHome.empty() && entry != realHome && isUnder(entry, realHome))
                candidate = p.home + entry.substr(realHome.size());
            for (const std::string& m : mounted)
                if (isUnder(candidate, m)) { keep(candidate); break; }
        }
        for (const char* dir : SYSTEM_EXEC_DIRS) keep(dir);

        for (std::size_t i = 0; i < dirs.size(); ++i) {
            if (i) p.path += ':';
            p.path += dirs[i];
        }
    }

    p.ok = true;
    return p;
}

std::vector<std::string> lane::confine::wrap(const Policy& p, const std::string& shellCommand)
{
    Command command;
    command.shell = shellCommand;
    return wrap(p, command);
}

std::vector<std::string> lane::confine::wrap(const Policy& p, const Command& command)
{
    if (!p.ok) return {};

    std::vector<std::string> a {p.binary};
    auto add = [&a](std::initializer_list<const char*> parts) {
        for (const char* s : parts) a.emplace_back(s);
    };
    auto bind = [&a](const char* mode, const std::string& src, const std::string& dst) {
        a.emplace_back(mode);
        a.push_back(src);
        a.push_back(dst);
    };

    add({"--unshare-user", "--unshare-pid", "--unshare-uts", "--unshare-ipc",
         "--unshare-cgroup-try", "--die-with-parent"});

    bind("--ro-bind", "/usr", "/usr");
    bind("--ro-bind-try", "/etc", "/etc");

    for (const std::string& ro : p.hostReadOnly) bind("--ro-bind", ro, ro);

    if (!p.resolvConf.empty()) bind("--ro-bind", p.resolvConf, p.resolvConf);
    add({"--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
         "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin"});
    add({"--proc", "/proc", "--dev", "/dev"});

    for (const auto& dev : p.devices) {
        const std::size_t slash = dev.declared.find_last_of('/');
        if (slash != std::string::npos && slash > 0)
            a.insert(a.end(), {"--dir", dev.declared.substr(0, slash)});
        bind("--dev-bind", dev.node, dev.declared);
    }

    add({"--tmpfs", "/tmp"});
    a.emplace_back("--dir");
    a.push_back(p.scratch);
    for (const std::string& dir : p.runtimeDirectories) {
        a.emplace_back("--dir");
        a.push_back(dir);
    }
    a.emplace_back("--tmpfs");
    a.push_back(p.home);

    if (!p.agentConfig.empty()) bind("--bind", p.agentConfig, p.agentConfigInside);

    if (!p.agentBinaries.empty()) {
        a.emplace_back("--dir");
        a.emplace_back(AGENT_BIN_INSIDE);
        for (const auto& binary : p.agentBinaries) {
            bind(originalBinaryIsReachable(p, binary) ? "--symlink" : "--ro-bind",
                 binary.host, binary.inside);
        }
    }

    for (const auto& t : p.toolchain) bind("--ro-bind", t.host, t.inside);

    bind("--ro-bind", p.gitCommonDir, p.gitCommonDir);
    bind("--bind", p.gitDir, p.gitDir);
    bind("--bind", p.objectsDir, p.objectsDir);
    bind("--bind", p.refsDir, p.refsDir);
    bind("--bind", p.logsRefsDir, p.logsRefsDir);
    for (const std::string& ref : p.foreignRefs) bind("--ro-bind", ref, ref);

    bind("--bind", p.worktree, p.worktree);

    add({"--remount-ro", "/", "--remount-ro", "/tmp"});

    a.emplace_back("--tmpfs");
    a.push_back(p.scratch);
    for (const std::string& dir : p.runtimeDirectories) {
        a.emplace_back("--tmpfs");
        a.push_back(dir);
        a.emplace_back("--chmod");
        a.emplace_back("0700");
        a.push_back(dir);
    }

    a.emplace_back("--chdir");
    a.push_back(p.worktree);
    a.emplace_back("--setenv");
    a.emplace_back("HOME");
    a.push_back(p.home);
    a.emplace_back("--setenv");
    a.emplace_back(LANE_HOME_ENV);
    a.push_back(p.home);
    a.emplace_back("--setenv");
    a.emplace_back("TMPDIR");
    a.push_back(p.scratch);
    a.emplace_back("--setenv");
    a.emplace_back("PWD");
    a.push_back(p.worktree);
    if (!p.path.empty()) {
        a.emplace_back("--setenv");
        a.emplace_back("PATH");
        a.push_back(p.path);
    }
    add({"--setenv", "LC_ALL", "C", "--setenv", "LANG", "C"});

    for (const auto& pair : command.env) {
        a.emplace_back("--setenv");
        a.push_back(pair.first);
        a.push_back(pair.second);
    }
    for (const auto& name : command.unsetEnv) {
        a.emplace_back("--unsetenv");
        a.push_back(name);
    }

    a.emplace_back("--");
    if (command.argv.empty()) {
        a.emplace_back("/bin/sh");
        a.emplace_back("-c");
        a.push_back(command.shell);
    } else {
        std::string program = command.argv.front();
        for (const auto& binary : p.agentBinaries)
            if (binary.host == program && !originalBinaryIsReachable(p, binary)) {
                program = binary.inside;
                break;
            }
        a.push_back(program);
        a.insert(a.end(), command.argv.begin() + 1, command.argv.end());
    }
    return a;
}

lane::confine::Refusal lane::confine::inspect(const Policy& p, const std::string& line)
{
    Refusal r;
    if (!p.ok || line.empty()) return r;

    const bool erofs = line.find("Read-only file system") != std::string::npos;
    const bool eacces = line.find("Permission denied") != std::string::npos
                        || line.find("Operation not permitted") != std::string::npos;
    const bool enoent = line.find("No such file or directory") != std::string::npos;
    if (!erofs && !eacces && !enoent) return r;

    std::string target;
    for (const std::string& candidate : pathsIn(line))
        if (candidate.size() > target.size()) target = candidate;
    if (target.empty()) return r;

    for (const std::string& w : p.writable)
        if (isUnder(target, w)) return r;
    for (const std::string& w : p.writableInside)
        if (isUnder(target, w)) return r;

    if (erofs) {
        r.operation = "write";
    } else if (eacces) {
        r.operation = "access";
    } else {
        bool hidden = isUnder(target, p.repoRoot);
        if (!hidden && !p.hiddenHome.empty() && isUnder(target, p.hiddenHome)) {
            hidden = true;
            for (const std::string& ro : p.hostReadOnly)
                if (isUnder(target, ro)) { hidden = false; break; }
            if (hidden)
                for (const auto& t : p.toolchain)
                    if (isUnder(target, t.inside)) { hidden = false; break; }
        }
        if (!hidden) return r;
        r.operation = "read";
    }

    r.refused = true;
    r.path = target;
    return r;
}
