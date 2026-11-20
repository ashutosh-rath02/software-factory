#include "store.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <istream>
#include <set>
#include <sstream>
#include <thread>

#include <dirent.h>
#include <climits>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>


#include <core/engine.h>
#include <app/agent/history.h>
#include <core/worker.h>
#include <app/error/remedy.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <glog/logging.h>

#include "git.h"
#include "state.h"

namespace fs = std::filesystem;

namespace {

int pidRunningIn(const std::string& path)
{
    if (path.empty()) return 0;
    std::error_code ec;
    const std::string want = fs::weakly_canonical(fs::path(path), ec).string();
    if (ec || want.empty()) return 0;

    DIR* proc = ::opendir("/proc");
    if (!proc) return 0;
    const pid_t mine = ::getpid();
    int found = 0;
    while (dirent* entry = ::readdir(proc)) {
        const std::string name = entry->d_name;
        if (name.empty() || !std::isdigit(static_cast<unsigned char>(name[0]))) continue;
        const int pid = std::atoi(name.c_str());
        if (pid <= 0 || pid == static_cast<int>(mine)) continue;

        char resolved[PATH_MAX + 1] = {0};
        const ssize_t n = ::readlink(("/proc/" + name + "/cwd").c_str(), resolved, PATH_MAX);
        if (n <= 0) continue;
        const std::string cwd(resolved, static_cast<std::size_t>(n));
        if (cwd != want && cwd.compare(0, want.size() + 1, want + "/") != 0) continue;
        if (found == 0 || pid < found) found = pid;
    }
    ::closedir(proc);
    return found;
}



constexpr int KILL_GRACE_MS = 4000;
constexpr int KILL_DEADLINE_MS = 2000;
constexpr int KILL_STEP_MS = 20;
static_assert(KILL_GRACE_MS % KILL_STEP_MS == 0);
constexpr int SETTLE_STEP_MS = 20;
constexpr int SETTLE_TRIES = 100;

constexpr int SWEEP_INTERVAL_MS = 5000;

constexpr uint32_t WORDS_COALESCE_MS = 2000;

constexpr int REAP_CONFIRMATIONS = 2;

constexpr const char* LOCK_NAME = ".owner.lock";

long long nowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

using manifest::jstr;
std::string sanitised(const std::string& s)
{
    std::string out;
    for (char c : s)
        out.push_back((std::isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '-'
                       || c == '_') ? c : '-');
    if (out.empty()) out = "repo";
    return out.substr(0, 48);
}

bool seedOnce(const fs::path& dir,
              const std::vector<std::pair<std::string, std::string>>& files,
              std::string& reason)
{
    std::error_code ec;
    for (const auto& file : files) {
        const fs::path path = dir / file.first;
        if (fs::exists(path, ec)) continue;
        std::ofstream out(path.string(), std::ios::binary | std::ios::trunc);
        if (!out) {
            reason = "the lane's agent configuration could not be written at " + path.string();
            return false;
        }
        out << file.second;
        out.close();
        fs::permissions(path, fs::perms::owner_read | fs::perms::owner_write,
                        fs::perm_options::replace, ec);
    }
    return true;
}

std::vector<std::string> carryBorrowed(
    const fs::path& dir, const std::vector<std::pair<std::string, std::string>>& files)
{
    std::vector<std::string> current;
    std::error_code ec;
    for (const auto& borrowed : files) {
        const fs::path src(borrowed.second);
        const fs::path dst = dir / borrowed.first;
        if (!fs::is_regular_file(src, ec)) continue;
        std::error_code sameEc;
        if (fs::exists(dst, ec) && fs::equivalent(src, dst, sameEc) && !sameEc) {
            current.push_back(borrowed.first);
            continue;
        }

        const fs::path carry = dir / (borrowed.first + ".carry");
        fs::remove(carry, ec);
        std::error_code linkEc;
        fs::create_hard_link(src, carry, linkEc);
        if (linkEc) {
            std::error_code copyEc;
            fs::copy_file(src, carry, fs::copy_options::overwrite_existing, copyEc);
            if (copyEc) { fs::remove(carry, ec); continue; }
            fs::permissions(carry, fs::perms::owner_read | fs::perms::owner_write,
                            fs::perm_options::replace, ec);
        }
        std::error_code renameEc;
        fs::rename(carry, dst, renameEc);
        if (renameEc) {
            fs::remove(carry, ec);
            continue;
        }
        current.push_back(borrowed.first);
    }
    return current;
}

std::vector<std::string> reCarry(const std::string& dir,
                                 const std::vector<std::pair<std::string, std::string>>& files)
{
    if (dir.empty()) return {};
    std::error_code ec;
    fs::create_directories(fs::path(dir), ec);
    if (!fs::is_directory(fs::path(dir), ec)) return {};
    fs::permissions(fs::path(dir), fs::perms::owner_all, fs::perm_options::replace, ec);
    return carryBorrowed(fs::path(dir), files);
}

bool furnish(const std::string& dir, const lane::Store::Launch& launch, std::string& reason)
{
    if (dir.empty()) return true;
    std::error_code ec;
    fs::create_directories(fs::path(dir), ec);
    if (!fs::is_directory(fs::path(dir), ec)) {
        reason = "the lane's agent configuration directory could not be made at " + dir;
        return false;
    }
    fs::permissions(fs::path(dir), fs::perms::owner_all, fs::perm_options::replace, ec);

    if (!seedOnce(fs::path(dir), launch.configSeeded, reason)) return false;
    carryBorrowed(fs::path(dir), launch.configBorrowed);
    return true;
}

std::string agentConfigRootFor(const std::string& repoRoot, const std::string& repoName)
{
    const char* base = std::getenv("FACTORY_AGENT_CONFIG_ROOT");
    std::string root = (base && *base) ? base : "/tmp";
    if (!(base && *base) && ::access(root.c_str(), W_OK) != 0)
        if (const char* tmpdir = std::getenv("TMPDIR"))
            if (*tmpdir && ::access(tmpdir, W_OK) == 0) root = tmpdir;

    std::uint64_t h = 1469598103934665603ull;
    for (unsigned char c : repoRoot) { h ^= c; h *= 1099511628211ull; }
    char hex[17];
    std::snprintf(hex, sizeof(hex), "%016llx", static_cast<unsigned long long>(h));

    return root + "/factory-agent-" + std::to_string(static_cast<int>(::getuid())) + "/"
         + sanitised(repoName) + "-" + std::string(hex).substr(0, 8);
}

bool safeName(const std::string& s)
{
    if (s.empty() || s == "." || s == "..") return false;
    if (s.find("..") != std::string::npos) return false;
    for (unsigned char c : s) {
        if (c <= 0x20 || c == 0x7f) return false;
        if (c == '/' || c == '\\' || c == '~' || c == '^' || c == ':' || c == '?' || c == '*'
            || c == '[' || c == '"')
            return false;
    }
    return true;
}

std::string trimmed(const std::string& s, std::size_t limit)
{
    return s.size() <= limit ? s : s.substr(0, limit) + "…";
}

}


namespace {

std::string laneFilePath(const std::string& dir, const std::string& lane)
{
    std::string flat = lane;
    for (char& c : flat) if (c == '/') c = '-';
    return dir + "/" + flat + ".json";
}

std::string jsonEscaped(const std::string& s)
{
    std::string out;
    for (const char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c == '\n') out += "\\n";
        else out += c;
    }
    return out;
}

std::string canonical(const std::string& path)
{
    std::error_code ec;
    const fs::path got = fs::weakly_canonical(fs::path(path), ec);
    return ec ? path : got.string();
}

long long madeAtMs(const fs::path& path)
{
    struct stat st {};
    if (::stat(path.c_str(), &st) != 0) return 0;
    return static_cast<long long>(st.st_mtim.tv_sec) * 1000
         + static_cast<long long>(st.st_mtim.tv_nsec) / 1000000;
}

std::string readMember(const std::string& body, const std::string& key)
{
    const std::string needle = "\"" + key + "\":\"";
    const std::size_t at = body.find(needle);
    if (at == std::string::npos) return {};
    const std::size_t from = at + needle.size();
    const std::size_t to = body.find('"', from);
    return to == std::string::npos ? std::string() : body.substr(from, to - from);
}

std::string laneNameFor(int n)
{
    std::ostringstream o;
    o << "lane/";
    if (n < 10) o << "0";
    o << n;
    return o.str();
}

int firstFreeNumber(const std::string& worktreeRoot, int from)
{
    std::error_code ec;
    for (int n = from;; ++n) {
        const fs::path at(worktreeRoot + "/" + laneNameFor(n));
        if (!fs::exists(at, ec)) return n;
    }
}

long long readNumber(const std::string& body, const std::string& key)
{
    const std::string needle = "\"" + key + "\":";
    const std::size_t at = body.find(needle);
    if (at == std::string::npos) return 0;
    return std::strtoll(body.c_str() + at + needle.size(), nullptr, 10);
}

}

void lane::Store::init()
{
    const auto& m = manifest::Store::instance().data();
    root_ = m.root;
    gitRoot_ = m.gitRoot;
    worktreeRoot_ = m.worktreesDir;
    lanesDir_ = m.lanesDir;
    agentConfigRoot_ = agentConfigRootFor(m.root, m.name);
    seats_ = m.lanesHost;

    recover_();
    adopt_();
}

void lane::Store::recover_()
{
    std::error_code ec;
    const fs::path dir = fs::path(lanesDir_);
    if (!fs::is_directory(dir, ec)) return;

    int recovered = 0, dropped = 0, live = 0;
    for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec)) {
        if (it->path().extension() != ".json") continue;
        std::ifstream in(it->path());
        const std::string body((std::istreambuf_iterator<char>(in)),
                               std::istreambuf_iterator<char>());
        Record r;
        r.name = readMember(body, "lane");
        r.ticketId = readMember(body, "ticket_id");
        r.workspace = readMember(body, "workspace");
        r.role = readMember(body, "role");
        r.branch = readMember(body, "branch");
        r.worktree = readMember(body, "worktree");
        r.claimedAtMs = readNumber(body, "claimed_at_ms");
        r.pid = static_cast<int>(readNumber(body, "pid"));

        if (r.name.empty() || r.worktree.empty()
            || git::presenceOf(r.worktree) != git::Presence::Present) {
            std::error_code rmEc;
            fs::remove(it->path(), rmEc);
            ++dropped;
            continue;
        }
        if (r.pid > 0 && !git::isRunning(r.pid)) r.pid = 0;
        if (r.pid > 0) ++live;
        r.worktreeReady = true;
        lanes_.push_back(std::move(r));
        ++recovered;
    }
    if (recovered || dropped)
        LOG(INFO) << "recovered " << recovered << " lane(s) from disk (" << live
                  << " with a live process), dropped " << dropped
                  << " whose worktree is gone";
}

std::string lane::Store::ticketFor_(const std::string& branch, std::string& roleOut)
{
    roleOut = "run";
    const std::string tail = branch.substr(std::string("ticket/").size());
    const std::size_t dash = tail.rfind('-');
    if (dash == std::string::npos || dash + 1 >= tail.size()) return tail;
    const std::string last = tail.substr(dash + 1);
    if (std::all_of(last.begin(), last.end(),
                    [](unsigned char c) { return std::isdigit(c) != 0; }))
        return tail;
    roleOut = last;
    return tail.substr(0, dash);
}

void lane::Store::adopt_()
{
    std::error_code ec;
    const fs::path laneRoot = fs::path(worktreeRoot_) / "lane";
    if (!fs::is_directory(laneRoot, ec)) return;

    std::map<std::string, std::string> branchAt;
    for (const git::Registered& w : git::registeredWorktrees(root_))
        branchAt[canonical(w.path)] = w.branch;

    std::set<std::string> covered;
    for (const Record& r : lanes_) covered.insert(canonical(r.worktree));

    std::vector<fs::path> entries;
    for (fs::directory_iterator it(laneRoot, ec), end; !ec && it != end; it.increment(ec))
        entries.push_back(it->path());
    std::sort(entries.begin(), entries.end());

    int adopted = 0;
    for (const fs::path& entry : entries) {
        const std::string path = canonical(entry.string());
        if (!covered.insert(path).second) continue;

        const auto at = branchAt.find(path);
        const std::string branch = at == branchAt.end() ? std::string() : at->second;
        if (at == branchAt.end() || branch.rfind("ticket/", 0) != 0) {
            Warning w;
            w.code = "unaccountable_worktree";
            w.path = path;
            w.message = at == branchAt.end()
                ? "this is under the lane root and git does not list it as a worktree of this "
                  "repository, so the tool cannot say whose it is; nothing was touched"
                : "this is a worktree of this repository on " + (branch.empty()
                    ? std::string("no branch") : branch)
                  + ", which is not a ticket branch, so it is not a lane; nothing was touched";
            warnings_.push_back(std::move(w));
            continue;
        }

        Record r;
        r.name = "lane/" + entry.filename().string();
        r.branch = branch;
        r.ticketId = ticketFor_(branch, r.role);
        r.worktree = path;
        r.claimedAtMs = madeAtMs(entry);
        r.worktreeReady = true;
        r.pid = pidRunningIn(path);
        lanes_.push_back(r);
        persist_(r);
        ++adopted;
    }

    if (adopted || !warnings_.empty())
        LOG(INFO) << "adopted " << adopted << " lane worktree(s) no record described, and could "
                  << "not account for " << warnings_.size() << " entry(ies) under " << laneRoot;
}

void lane::Store::persist_(const Record& record)
{
    std::error_code ec;
    fs::create_directories(fs::path(lanesDir_), ec);
    std::ofstream out(laneFilePath(lanesDir_, record.name), std::ios::trunc);
    if (!out) return;
    out << "{\"lane\":\"" << jsonEscaped(record.name)
        << "\",\"ticket_id\":\"" << jsonEscaped(record.ticketId)
        << "\",\"workspace\":\"" << jsonEscaped(record.workspace)
        << "\",\"role\":\"" << jsonEscaped(record.role)
        << "\",\"branch\":\"" << jsonEscaped(record.branch)
        << "\",\"worktree\":\"" << jsonEscaped(record.worktree)
        << "\",\"claimed_at_ms\":" << record.claimedAtMs
        << ",\"pid\":" << record.pid << "}\n";
}

void lane::Store::forget_(const std::string& name)
{
    words_.erase(name);
    std::error_code ec;
    fs::remove(laneFilePath(lanesDir_, name), ec);
}

void lane::Store::sweepAgentConfigRoots_()
{
    std::error_code ec;
    const fs::path mine(agentConfigRoot_);
    const fs::path base = mine.parent_path();
    if (base.empty() || mine.empty()) return;

    const std::string home = confine::developerHome();
    const std::string prefix = home.empty() ? std::string() : home + "/";
    if (home.empty() || agentConfigRoot_ == home
        || agentConfigRoot_.compare(0, prefix.size(), prefix) == 0) {
        LOG(WARNING) << "not creating or sweeping " << mine
                     << ": it is inside the developer's home, or this process cannot tell where "
                        "that is. Every lane will be refused until it is moved.";
        return;
    }

    fs::create_directories(mine, ec);
    fs::permissions(mine, fs::perms::owner_all, fs::perm_options::replace, ec);
    configLockFd_ = ::open((mine / LOCK_NAME).c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (configLockFd_ < 0 || ::flock(configLockFd_, LOCK_EX | LOCK_NB) != 0) {
        if (configLockFd_ >= 0) { ::close(configLockFd_); configLockFd_ = -1; }
        LOG(WARNING) << "another factory holds " << mine
                     << "; leaving every agent configuration directory alone";
        return;
    }

    std::vector<fs::path> siblings;
    for (fs::directory_iterator it(base, ec), end; !ec && it != end; it.increment(ec)) {
        if (it->path() != mine && fs::is_directory(it->path(), ec))
            siblings.push_back(it->path());
    }

    std::uintmax_t swept = 0;
    int kept = 0;
    for (const fs::path& sibling : siblings) {
        const int fd = ::open((sibling / LOCK_NAME).c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
        if (fd < 0) continue;
        if (::flock(fd, LOCK_EX | LOCK_NB) != 0) {
            ::close(fd);
            ++kept;
            continue;
        }
        std::error_code rmEc;
        swept += fs::remove_all(sibling, rmEc);
        ::close(fd);
    }

    std::vector<fs::path> ours;
    for (fs::directory_iterator it(mine, ec), end; !ec && it != end; it.increment(ec))
        if (it->path().filename() != LOCK_NAME) ours.push_back(it->path());
    for (const fs::path& stale : ours) {
        std::error_code rmEc;
        swept += fs::remove_all(stale, rmEc);
    }

    if (swept > 0 || kept > 0)
        LOG(INFO) << "swept " << swept << " leftover agent configuration path(s) under " << base
                  << ", left " << kept << " owned by a live factory";
}

void lane::Store::start()
{
    sweepAgentConfigRoots_();
    LOG(INFO) << "lanes: " << seats_ << " host seat(s), worktrees under " << worktreeRoot_;

    const std::vector<std::string> runsDir = manifest::Store::instance().data().ledgers();
    if (runsDir.empty()) return;
    exec_.offload(
        [runsDir] { return agent::history::stagesByTicket(runsDir); },
        [this](std::map<std::string, std::vector<agent::history::Reported>> seed) {
            int tickets = 0, reports = 0;
            for (const auto& [ticketId, history] : seed) {
                if (history.empty() || stages_.count(ticketId)) continue;
                std::vector<progress::Report>& into = stages_[ticketId];
                for (const agent::history::Reported& r : history) {
                    progress::Report report;
                    report.name = r.name;
                    report.round = r.round;
                    report.hasRound = r.hasRound;
                    report.of = r.of;
                    report.hasOf = r.hasOf;
                    report.atMs = r.atMs;
                    report.endMs = r.endMs;
                    report.runId = r.runId;
                    into.push_back(std::move(report));
                }
                ++tickets;
                reports += static_cast<int>(history.size());
            }
            if (tickets)
                LOG(INFO) << "stages: " << reports << " report(s) read back for "
                          << tickets << " ticket(s)";
        });
}

void lane::Store::stop()
{
    exec_.post([this] {
        stopping_ = true;
        for (auto& [name, pending] : spawning_) {
            (void)name;
            const int pid = pending->stopPid->exchange(-1);
            if (pid > 0) git::signalGroup(pid, SIGKILL);
        }
        sweepTask_.stop();
        for (auto& entry : watches_) entry.second->out.close();
        watches_.clear();
    });
}

void lane::Store::onMembership(MembershipHandler handler)
{
    exec_.post([this, handler = std::move(handler)]() mutable {
        membershipHandlers_.push_back(std::move(handler));
    });
}

void lane::Store::onResource(ResourceHandler handler)
{
    exec_.post([this, handler = std::move(handler)]() mutable {
        handlers_.push_back(std::move(handler));
    });
}

void lane::Store::onIsolation(IsolationHandler handler)
{
    exec_.post([this, handler = std::move(handler)]() mutable {
        isolationHandlers_.push_back(std::move(handler));
    });
}

bool lane::Store::testHooksEnabled() const
{
    const char* on = std::getenv("FACTORY_TEST_HOOKS");
    return on && *on;
}

lane::Store::Reservation lane::Store::scanFor_(const std::string& worktreeRoot,
                                              const std::string& registered, int from)
{
    Reservation out;
    out.from = from;

    const std::string mine = worktreeRoot + "/lane/";
    out.adoptable = !registered.empty()
                 && registered.rfind(mine, 0) == 0
                 && registered.find("/", mine.size()) == std::string::npos
                 && git::presenceOf(registered) == git::Presence::Present;
    if (out.adoptable) out.worktree = registered;

    out.number = firstFreeNumber(worktreeRoot, from);
    return out;
}

namespace {
std::string joinedNames(const std::vector<std::string>& names)
{
    std::string out;
    for (const std::string& n : names) out += (out.empty() ? "" : ", ") + n;
    return out;
}
}

std::vector<std::pair<std::string, std::string>> lane::Store::toolchainFor_(
    const std::string& workspace, const std::vector<std::string>& also)
{
    std::vector<std::string> wanted {workspace};
    for (const std::string& extra : also)
        if (!extra.empty() && extra != workspace
            && std::find(wanted.begin(), wanted.end(), extra) == wanted.end())
            wanted.push_back(extra);

    std::vector<std::pair<std::string, std::string>> out;
    for (const std::string& name : wanted)
        for (const manifest::Store::Workspace& w : manifest::Store::instance().data().workspaces)
            if (w.name == name)
                for (const manifest::Store::ToolchainPath& t : w.toolchain) {
                    const std::pair<std::string, std::string> entry {t.declared, t.hostPath};
                    if (std::find(out.begin(), out.end(), entry) == out.end())
                        out.push_back(entry);
                }
    return out;
}

lane::Store::Record* lane::Store::find_(const std::string& name)
{
    for (auto& r : lanes_)
        if (r.name == name) return &r;
    return nullptr;
}

std::string lane::Store::branchFor_(const std::string& ticketId, const std::string& role) const
{
    return role == "run" ? "ticket/" + ticketId : "ticket/" + ticketId + "-" + role;
}

std::vector<std::string> lane::Store::resourcesFor_(const std::string& workspace) const
{
    std::vector<std::string> out;
    for (const auto& r : manifest::Store::instance().data().resources) {
        if (!r.exclusive) continue;
        if (std::find(r.neededBy.begin(), r.neededBy.end(), workspace) != r.neededBy.end())
            out.push_back(r.name);
    }
    return out;
}

lane::Store::ResourceView lane::Store::viewOf_(const std::string& name) const
{
    ResourceView v;
    v.name = name;
    for (const auto& r : manifest::Store::instance().data().resources) {
        if (r.name != name) continue;
        v.exclusive = r.exclusive;
        v.description = r.description;
        v.hasDescription = r.hasDescription;
    }
    auto held = holder_.find(name);
    if (held != holder_.end()) v.heldBy = held->second;
    for (const auto& r : lanes_)
        if (!v.heldBy.empty() && r.name == v.heldBy) v.holderPid = r.pid;
    auto q = queue_.find(name);
    if (q != queue_.end()) v.queue = q->second;
    return v;
}

std::vector<lane::Store::ResourceView> lane::Store::resourceViews_() const
{
    std::vector<ResourceView> out;
    for (const auto& r : manifest::Store::instance().data().resources)
        out.push_back(viewOf_(r.name));
    return out;
}

lane::Store::Snapshot lane::Store::snapshot_() const
{
    Snapshot s;
    s.lanes = lanes_;
    s.resources = resourceViews_();
    s.warnings = warnings_;
    s.seatsTotal = seats_;
    s.seatsUsed = static_cast<int>(lanes_.size());
    s.stages = stages_;
    return s;
}

static void refreshWaiting(std::vector<lane::Store::Record>& lanes,
                           const std::map<std::string, std::string>& holder,
                           const std::map<std::string, std::vector<std::string>>& queue,
                           const std::function<std::vector<std::string>(const std::string&)>& needs)
{
    for (auto& r : lanes) {
        if (!r.queued) { r.waiting = {}; continue; }
        r.waiting = {};
        for (const auto& n : needs(r.workspace)) {
            auto q = queue.find(n);
            const bool head = q != queue.end() && !q->second.empty() && q->second.front() == r.name;
            auto h = holder.find(n);
            const bool free = h == holder.end() || h->second.empty();
            if (free && head) continue;
            r.waiting.resource = n;
            r.waiting.heldBy = (h == holder.end()) ? std::string() : h->second;
            r.waiting.position = 0;
            r.waiting.holderPid = 0;
            for (const auto& other : lanes)
                if (!r.waiting.heldBy.empty() && other.name == r.waiting.heldBy)
                    r.waiting.holderPid = other.pid;
            if (q != queue.end()) {
                for (std::size_t i = 0; i < q->second.size(); ++i)
                    if (q->second[i] == r.name) r.waiting.position = static_cast<int>(i) + 1;
            }
            break;
        }
    }
}

bool lane::Store::grant_(Record& record)
{
    const auto needed = resourcesFor_(record.workspace);
    if (needed.empty()) {
        record.queued = false;
        return true;
    }

    const bool allFree = std::all_of(needed.begin(), needed.end(), [&](const std::string& n) {
        return holder_[n].empty() && queue_[n].empty();
    });

    if (allFree) {
        for (const auto& n : needed) holder_[n] = record.name;
        record.resourcesHeld = needed;
        record.queued = false;
        return true;
    }

    for (const auto& n : needed) queue_[n].push_back(record.name);
    record.queued = true;
    return false;
}

void lane::Store::reclaimFromStalledTriggers_(std::vector<std::string>& changed)
{
    for (const auto& waiter : lanes_) {
        if (!waiter.queued) continue;
        if (waiter.role.empty() || waiter.role == "run") continue;
        for (auto& holder : lanes_) {
            if (holder.name == waiter.name) continue;
            if (holder.ticketId != waiter.ticketId) continue;
            if (!(holder.role.empty() || holder.role == "run")) continue;
            if (holder.resourcesHeld.empty()) continue;
            if (triggerStillWorking_(holder)) continue;
            for (auto& [res, who] : holder_)
                if (who == holder.name) { who.clear(); changed.push_back(res); }
            holder.resourcesHeld.clear();
            holder.queued = false;
        }
    }
}

void lane::Store::reconcile_()
{
    std::vector<std::string> changed;
    advance_(changed);
    publish_(changed);
}

void lane::Store::advance_(std::vector<std::string>& changed)
{
    reclaimFromStalledTriggers_(changed);

    std::vector<std::array<std::string, 3>> granted;
    bool progress = true;
    while (progress) {
        progress = false;
        for (auto& r : lanes_) {
            if (!r.queued) continue;
            const auto needed = resourcesFor_(r.workspace);
            const bool ready = std::all_of(needed.begin(), needed.end(),
                [&](const std::string& n) {
                    return holder_[n].empty() && !queue_[n].empty() && queue_[n].front() == r.name;
                });
            if (!ready) continue;
            for (const auto& n : needed) {
                holder_[n] = r.name;
                queue_[n].erase(queue_[n].begin());
                changed.push_back(n);
            }
            r.resourcesHeld = needed;
            r.queued = false;
            granted.push_back({r.name, r.ticketId, r.role});
            progress = true;
        }
    }
    refreshWaiting(lanes_, holder_, queue_,
                   [this](const std::string& w) { return resourcesFor_(w); });

    for (const auto& g : granted)
        for (const auto& handler : grantHandlers_) handler(g[0], g[1], g[2]);
}

void lane::Store::publish_(const std::vector<std::string>& changed)
{
    std::vector<std::string> once;
    for (const auto& n : changed)
        if (std::find(once.begin(), once.end(), n) == once.end()) once.push_back(n);
    for (const auto& n : once) {
        const ResourceView v = viewOf_(n);
        for (auto& h : handlers_) h(v);
    }
}

void lane::Store::list(Done done)
{
    exec_.post([this, done = std::move(done)] {
        Outcome out;
        out.status = 200;
        out.snapshot = snapshot_();
        done(std::move(out));
    });
}

void lane::Store::armSweep_()
{
    if (stopping_ || sweepTask_.running() || lanes_.empty()) return;
    sweepTask_.start(SWEEP_INTERVAL_MS, 1);
}

void lane::Store::sweepTick_()
{
    if (!stopping_) sweep_();
}

void lane::Store::sweep_()
{
    std::vector<std::pair<std::string, std::string>> candidates;
    for (const auto& record : lanes_) {
        if (record.worktreeReady) candidates.emplace_back(record.name, record.worktree);
    }
    if (candidates.empty()) {
        armSweep_();
        return;
    }
    const std::size_t total = candidates.size();
    const std::string root = worktreeRoot_;
    exec_.offload(
        [candidates, root] {
            std::vector<std::pair<std::string, git::Presence>> verdicts;
            const git::Presence rootIs = git::presenceOf(root);
            verdicts.reserve(candidates.size());
            for (const auto& [name, worktree] : candidates) {
                const git::Presence is = rootIs == git::Presence::Present
                                       ? git::presenceOf(worktree)
                                       : rootIs;
                verdicts.emplace_back(name, is);
            }
            return verdicts;
        },
        [this, total](std::vector<std::pair<std::string, git::Presence>> verdicts) {
            if (stopping_) return;
            int absent = 0, present = 0, unknown = 0;
            std::vector<std::string> confirmed;
            for (const auto& [name, is] : verdicts) {
                Record* live = find_(name);
                if (is != git::Presence::Absent) {
                    (is == git::Presence::Present ? present : unknown) += 1;
                    if (live) live->absentPasses = 0;
                    continue;
                }
                ++absent;
                if (!live) continue;
                if (++live->absentPasses >= REAP_CONFIRMATIONS) {
                    confirmed.push_back(name);
                } else {
                    LOG(INFO) << "lane " << name << " has no worktree at " << live->worktree
                              << " (" << live->absentPasses << "/" << REAP_CONFIRMATIONS
                              << ") — reporting it and looking again";
                }
            }

            if (absent || unknown)
                LOG(INFO) << "sweep: candidates=" << total << " absent=" << absent
                          << " present=" << present << " unknown=" << unknown;

            for (const auto& name : confirmed) {
                Record* live = find_(name);
                if (!live) continue;
                const std::string worktree = live->worktree;
                const int pid = live->pid;
                LOG(WARNING) << "lane " << name << " lost its worktree"
                             << (pid > 0 ? " under a live process" : "")
                             << " — reaping it and freeing what it held";
                release(name, true, false, [name, worktree, pid](Outcome out) {
                    LOG(WARNING) << "reaped lane " << name << " at " << worktree
                                 << ": status=" << out.status
                                 << " reason=" << (out.reason.empty() ? "-" : out.reason)
                                 << " killed=" << (out.killed ? "yes" : "no")
                                 << " pid=" << (out.killed ? out.killedPid : pid)
                                 << (out.errorCode.empty() ? "" : " error=" + out.errorCode);
                });
            }
            armSweep_();
        });
}

namespace {

struct Holder {
    int pid = 0;
    std::string comm;
};

Holder holderOf(const std::string& device)
{
    std::error_code ec;
    const std::string real = std::filesystem::weakly_canonical(std::filesystem::path(device), ec);
    const std::string want = ec ? device : real;
    const int self = ::getpid();

    for (const auto& entry : std::filesystem::directory_iterator("/proc", ec)) {
        const std::string name = entry.path().filename().string();
        if (name.empty() || !std::isdigit(static_cast<unsigned char>(name[0]))) continue;
        const int pid = std::atoi(name.c_str());
        if (pid <= 0 || pid == self) continue;

        std::error_code fdEc;
        auto fds = std::filesystem::directory_iterator(entry.path() / "fd", fdEc);
        if (fdEc) continue;
        for (const auto& fd : fds) {
            std::error_code linkEc;
            const std::filesystem::path target = std::filesystem::read_symlink(fd.path(), linkEc);
            if (linkEc || target.string() != want) continue;
            Holder h;
            h.pid = pid;
            std::ifstream comm(entry.path() / "comm");
            std::getline(comm, h.comm);
            return h;
        }
    }
    return {};
}

}

lane::Store::Preflight lane::Store::preflight(const std::string& workspace,
                                const std::vector<std::pair<std::string, std::string>>& toolchain,
                                const std::vector<std::pair<std::string, std::string>>& devices)
{
    Preflight v;

    std::error_code ec;
    for (const auto& t : toolchain) {
        if (t.second.empty() || !std::filesystem::exists(std::filesystem::path(t.second), ec)) {
            v.ok = false;
            v.errorCode = "workspace_unrunnable";
            v.errorRemedy = remedy::reloadManifest();
            v.errorMessage = "this workspace declares a toolchain that is not on this machine any "
                             "more, so a lane for it could not run its own build";
            v.errorDetail = "\"reason\":\"toolchain_missing\""
                            ",\"workspace\":" + jstr(workspace)
                          + ",\"declared\":" + jstr(t.first)
                          + ",\"path\":" + jstr(t.second.empty() ? t.first : t.second);
            return v;
        }
    }

    for (const auto& d : devices) {
        const Holder h = holderOf(d.second);
        if (h.pid > 0) {
            v.ok = false;
            v.errorCode = "device_busy";
            v.errorRemedy = remedy::retry();
            v.errorMessage = "something on this machine already has that device open; a lane that "
                             "took this exclusive resource now would be using it at the same time";
            v.errorDetail = "\"reason\":\"device_in_use\""
                            ",\"workspace\":" + jstr(workspace)
                          + ",\"resource\":" + jstr(d.first)
                          + ",\"device\":" + jstr(d.second)
                          + ",\"pid\":" + std::to_string(h.pid)
                          + ",\"comm\":" + jstr(h.comm);
            return v;
        }
    }
    return v;
}

void lane::Store::claim(const ClaimRequest& request, Done done)
{
    exec_.post([this, request, done = std::move(done)] {
        Outcome out;
        const std::string role = request.role.empty() ? std::string("run") : request.role;

        if (!safeName(request.ticketId) || !safeName(role)) {
            out.status = 400;
            out.errorCode = "bad_request";
            out.errorRemedy = remedy::none();
            out.errorMessage = "a ticket id and a role become a branch name and may not contain "
                               "a path or a ref character";
            out.errorDetail = "\"ticket_id\":" + jstr(request.ticketId) + ",\"role\":" + jstr(role);
            return done(std::move(out));
        }

        const auto& m = manifest::Store::instance().data();
        const bool known = std::any_of(m.workspaces.begin(), m.workspaces.end(),
            [&](const manifest::Store::Workspace& w) { return w.name == request.workspace; });
        if (!known) {
            std::ostringstream declared;
            declared << "[";
            for (std::size_t i = 0; i < m.workspaces.size(); ++i) {
                if (i) declared << ",";
                declared << jstr(m.workspaces[i].name);
            }
            declared << "]";
            out.status = 404;
            out.errorCode = "not_found";
            out.errorRemedy = remedy::reloadManifest();
            out.errorMessage = "no workspace by that name is declared in the manifest";
            out.errorDetail = "\"workspace\":" + jstr(request.workspace)
                            + ",\"declared\":" + declared.str()
                            + ",\"path\":" + jstr(m.manifestPath);
            return done(std::move(out));
        }

        if (!request.requestId.empty()) {
            auto seen = byRequestId_.find(request.requestId);
            if (seen != byRequestId_.end()) {
                if (Record* existing = find_(seen->second)) {
                    out.status = 201;
                    out.lane = *existing;
                    out.snapshot = snapshot_();
                    return done(std::move(out));
                }
                byRequestId_.erase(seen);
            }
        }

        const std::vector<std::pair<std::string, std::string>> toolchain =
            toolchainFor_(request.workspace, request.alsoWorkspaces);

        std::vector<std::pair<std::string, std::string>> devices;
        for (const std::string& res : resourcesFor_(request.workspace))
            for (const manifest::Store::Resource& r : m.resources)
                if (r.name == res)
                    for (const std::string& d : r.devices) devices.emplace_back(res, d);

        const std::string branch = branchFor_(request.ticketId, role);
        exec_.offload(
            [workspace = request.workspace, toolchain, devices,
             root = root_, worktreeRoot = worktreeRoot_, branch, from = nextNumber_] {
                ClaimScan out;
                out.verdict = preflight(workspace, toolchain, devices);
                out.scan = scanFor_(worktreeRoot, git::worktreeForBranch(root, branch), from);
                return out;
            },
            [this, request, role, done](ClaimScan out) {
                if (!out.verdict.ok) {
                    Outcome refused;
                    refused.status = 409;
                    refused.errorCode = out.verdict.errorCode;
                    refused.errorMessage = out.verdict.errorMessage;
                    refused.errorDetail = out.verdict.errorDetail;
                    refused.errorRemedy = out.verdict.errorRemedy;
                    return done(std::move(refused));
                }
                reserve_(request, role, out.scan, done);
            });
    });
}

void lane::Store::scan_(const std::string& branch, int from,
                        std::function<void(Reservation)> done)
{
    exec_.offload(
        [root = root_, worktreeRoot = worktreeRoot_, branch, from] {
            return scanFor_(worktreeRoot, git::worktreeForBranch(root, branch), from);
        },
        [done](Reservation out) { done(std::move(out)); });
}

void lane::Store::reserve_(const ClaimRequest& request, const std::string& role,
                           const Reservation& scan, Done done)
{
    {
        Outcome out;

        if (!request.requestId.empty()) {
            auto seen = byRequestId_.find(request.requestId);
            if (seen != byRequestId_.end()) {
                if (Record* existing = find_(seen->second)) {
                    out.status = 201;
                    out.lane = *existing;
                    out.snapshot = snapshot_();
                    return done(std::move(out));
                }
                byRequestId_.erase(seen);
            }
        }

        for (const auto& r : lanes_) {
            if (r.ticketId != request.ticketId || r.role != role) continue;
            out.status = 409;
            out.errorCode = "lane_exists";
            out.errorRemedy = remedy::openLane(r.name);
            out.errorMessage = "this ticket already has a lane in that role";
            out.errorDetail = "\"lane\":" + jstr(r.name) + ",\"ticket_id\":" + jstr(r.ticketId)
                            + ",\"role\":" + jstr(r.role) + ",\"branch\":" + jstr(r.branch);
            return done(std::move(out));
        }

        if (gitRoot_.empty()) {
            out.status = 409;
            out.errorCode = "no_git_root";
            out.errorRemedy = remedy::reloadManifest();
            out.errorMessage = "no git repository encloses " + root_
                             + ", so a lane here could never be confined or run";
            out.errorDetail = "\"root\":" + jstr(root_);
            return done(std::move(out));
        }

        if (static_cast<int>(lanes_.size()) >= seats_) {
            out.status = 409;
            out.errorCode = "no_seat";
            out.errorRemedy = remedy::reloadManifest();
            out.errorMessage = "every host seat is taken; release a lane or raise lanes.host";
            out.errorDetail = "\"total\":" + std::to_string(seats_)
                            + ",\"used\":" + std::to_string(lanes_.size());
            return done(std::move(out));
        }

        bool adopted = false;
        Record record;
        record.ticketId = request.ticketId;
        record.workspace = request.workspace;
        record.role = role;
        record.branch = branchFor_(request.ticketId, role);

        const std::string mine = worktreeRoot_ + "/lane/";
        if (scan.adoptable) {
            record.worktree = scan.worktree;
            record.name = "lane/" + scan.worktree.substr(mine.size());
            adopted = true;
        } else {
            if (scan.number < nextNumber_ || find_(laneNameFor(scan.number))
                || spawning_.count(laneNameFor(scan.number))) {
                const std::string branch = record.branch;
                return scan_(branch, nextNumber_,
                             [this, request, role, done](Reservation again) {
                                 reserve_(request, role, again, done);
                             });
            }
            record.name = laneNameFor(scan.number);
            record.worktree = worktreeRoot_ + "/" + record.name;
            nextNumber_ = scan.number + 1;
        }
        record.alsoWorkspaces = request.alsoWorkspaces;
        record.claimedAtMs = nowMs();
        {
            std::ostringstream held;
            for (const auto& t : toolchainFor_(request.workspace, request.alsoWorkspaces))
                held << (held.tellp() > 0 ? ", " : "") << t.first;
            LOG(INFO) << "lane " << record.name << " holds the toolchain of "
                      << request.workspace
                      << (request.alsoWorkspaces.empty()
                              ? std::string(" and no other workspace")
                              : " and " + joinedNames(request.alsoWorkspaces))
                      << ": " << (held.tellp() > 0 ? held.str() : std::string("nothing"));
        }

        std::vector<std::string> changed;
        if (grant_(record)) changed = record.resourcesHeld;
        lanes_.push_back(record);
        persist_(record);
        if (!request.requestId.empty()) byRequestId_[request.requestId] = record.name;
        advance_(changed);
        publish_(changed);

        const std::string name = record.name;
        const std::string root = root_;
        const std::string runBranch =
            role == "run" ? std::string() : branchFor_(request.ticketId, "run");
        exec_.offload(
            [adopted, root, runBranch, branch = record.branch, path = record.worktree] {
                Worktree made;
                if (!git::isRepository(root)) {
                    made.result.out = "not a git repository";
                    return made;
                }
                if (adopted) {
                    made.result.ok = true;
                    made.onDisk = git::presenceOf(path) == git::Presence::Present;
                    return made;
                }
                const git::Presence presence = git::presenceOf(path);
                if (presence == git::Presence::Unknown) {
                    made.unreadable = true;
                    made.result.out = "the lane root is unreadable";
                    return made;
                }
                if (presence == git::Presence::Present) {
                    made.blocked = true;
                    made.result.out = "a worktree is already in the way";
                    made.ours = git::isRegisteredWorktree(root, path);
                    if (made.ours) {
                        made.held = git::whatItHolds(path);
                        made.changes = made.held.uncommitted;
                    }
                    return made;
                }
                const bool follows = !runBranch.empty() && git::branchExists(root, runBranch);
                const std::string base = follows ? runBranch : std::string("HEAD");
                made.result = git::addWorktree(root, branch, path, base,
                                               follows ? git::OnExisting::TakeWork
                                                       : git::OnExisting::Checkout,
                                               &made.conflicted);
                if (made.result.ok) made.onDisk = git::presenceOf(path) == git::Presence::Present;
                return made;
            },
            [this, name, runBranch, done](Worktree made) {
                Outcome out;
                Record* live = find_(name);
                if (!live) {
                    out.status = 409;
                    out.errorCode = "wrong_state";
                    out.errorRemedy = remedy::retry();
                    out.errorMessage = "the lane was released while it was being created";
                    out.errorDetail = "\"lane\":" + jstr(name) + ",\"state\":\"released\"";
                    return done(std::move(out));
                }

                if (made.result.ok) {
                    live->worktreeReady = made.onDisk;
                    live->absentPasses = 0;
                    armSweep_();
                    out.status = 201;
                    out.lane = *live;
                    publish_(live->resourcesHeld);
                    for (const auto& h : membershipHandlers_) h(*live, true);
                    out.snapshot = snapshot_();
                    return done(std::move(out));
                }

                const std::string worktree = live->worktree;
                std::vector<std::string> changed;
                for (auto& [res, who] : holder_)
                    if (who == name) { who.clear(); changed.push_back(res); }
                for (auto& [res, q] : queue_) {
                    auto it = std::find(q.begin(), q.end(), name);
                    if (it != q.end()) { q.erase(it); changed.push_back(res); }
                }
                forget_(name);
                lanes_.erase(std::remove_if(lanes_.begin(), lanes_.end(),
                    [&](const Record& r) { return r.name == name; }), lanes_.end());
                for (auto it = byRequestId_.begin(); it != byRequestId_.end();)
                    it = (it->second == name) ? byRequestId_.erase(it) : std::next(it);
                advance_(changed);
                publish_(changed);

                out.status = 409;
                out.errorCode = made.unreadable ? "worktree_unreadable"
                              : made.blocked    ? "worktree_in_the_way"
                              : made.conflicted ? "work_conflicts"
                                                : "worktree_failed";
                out.errorMessage = made.unreadable
                    ? "the lane root could not be read, so whether the path is free is not known"
                    : made.blocked
                    ? "something is already at the path this lane would use"
                    : made.conflicted
                    ? "the work under review will not merge into this role's own commits"
                    : "git could not create the worktree for this lane";
                out.errorDetail = "\"path\":" + jstr(worktree)
                                + ",\"git\":" + jstr(trimmed(made.result.out, 400));
                if (made.conflicted) {
                    out.errorDetail += ",\"lane\":" + jstr(name)
                                     + ",\"base\":" + jstr(runBranch);
                }
                if (made.conflicted) out.errorRemedy = remedy::none();
                else if (!made.blocked) out.errorRemedy = remedy::retry();
                if (made.blocked) {
                    std::string list;
                    for (std::size_t i = 0; i < made.held.ignored.size(); ++i)
                        list += (i ? "," : "") + jstr(made.held.ignored[i]);
                    out.errorDetail += std::string(",\"ours\":") + (made.ours ? "true" : "false")
                                     + ",\"lane\":" + jstr(name)
                                     + ",\"uncommitted_changes\":" + std::to_string(made.changes)
                                     + ",\"unmerged_commits\":" + std::to_string(made.held.unmerged)
                                     + ",\"ignored\":[" + list + "]";
                    const bool clearable = made.ours && made.held.asked
                                        && made.held.uncommitted == 0
                                        && made.held.unmerged == 0;
                    if (clearable) {
                        out.errorDetail += ",\"clear_with\":\"DELETE " + std::string("/api/lanes/")
                                         + name
                                         + (made.held.ignored.empty()
                                                ? "?force=true" : "?discard_ignored=true")
                                         + "\"";
                    }
                    if (clearable && made.held.ignored.empty()) {
                        out.errorRemedy = remedy::releaseLane(
                            name, true, remedy::Cost{false, false, true, false});
                    } else if (clearable) {
                        out.errorRemedy = remedy::discardIgnored(
                            name, made.held.ignored,
                            remedy::Cost{false, false, true, true});
                    } else {
                        out.errorRemedy = remedy::openLane(name);
                    }
                }
                done(std::move(out));
            });
    }
}

void lane::Store::forgetLane(const std::string& name, std::function<void(bool)> done)
{
    exec_.post([this, name, done = std::move(done)] {
        if (startHolds_.count(name) || spawning_.count(name)) {
            if (done) done(false);
            return;
        }
        const bool had = find_(name) != nullptr;
        disarm_(name);
        forget_(name);
        lanes_.erase(std::remove_if(lanes_.begin(), lanes_.end(),
            [&](const Record& r) { return r.name == name; }), lanes_.end());
        for (auto it = byRequestId_.begin(); it != byRequestId_.end();)
            it = (it->second == name) ? byRequestId_.erase(it) : std::next(it);

        forgetRolesAt_(name);
        std::vector<std::string> changed;
        for (auto& [res, who] : holder_)
            if (who == name) { who.clear(); changed.push_back(res); }
        advance_(changed);
        publish_(changed);
        nextNumber_ = 1;
        if (done) done(had);
    });
}

void lane::Store::release(const std::string& name, bool force, bool discardIgnored, Done done)
{
    release(name, force, discardIgnored, std::move(done), {});
}

void lane::Store::completePending_(const std::string& name, std::shared_ptr<PendingSpawn> pending)
{
    const auto current = spawning_.find(name);
    if (current == spawning_.end() || current->second != pending || !pending->complete) return;
    if (!pending->releaseRequested) {
        spawning_.erase(current);
        return;
    }
    if (startHolds_.count(name) || !pending->releaseDone) return;
    release(name, true, pending->discardIgnored, std::move(pending->releaseDone), pending);
}

void lane::Store::release(const std::string& name, bool force, bool discardIgnored, Done done,
                          std::shared_ptr<PendingSpawn> settled)
{
    exec_.post([this, name, force, discardIgnored, done = std::move(done), settled] {
        Outcome out;
        Record* live = find_(name);
        const auto start = spawning_.find(name);
        if (start != spawning_.end()) {
            const auto pending = start->second;
            if (settled == pending && pending->complete && pending->releaseRequested
                && !startHolds_.count(name) && live && sameIncarnation(*live, pending->record)) {
                spawning_.erase(start);
            } else {
                if (settled) {
                    if (settled == pending && pending->complete) spawning_.erase(start);
                    out.status = 409;
                    out.errorCode = "routing_unresolved";
                    out.errorRemedy = remedy::none();
                    out.errorMessage = "the lane incarnation changed during pending release";
                    out.snapshot = snapshot_();
                    return done(std::move(out));
                }
                if (!force || pending->releaseRequested) {
                    out.status = 409;
                    out.errorCode = "wrong_state";
                    out.errorRemedy = remedy::retry();
                    out.errorMessage = "a process start is pending in this lane";
                    out.errorDetail = "\"state\":\"starting\",\"lane\":" + jstr(name);
                    return done(std::move(out));
                }
                pending->releaseRequested = true;
                pending->discardIgnored = discardIgnored;
                pending->releaseDone = std::move(done);
                const int pid = pending->stopPid->exchange(-1);
                if (pid > 0) git::signalGroup(pid, SIGKILL);
                completePending_(name, pending);
                return;
            }
        } else if (settled) {
            out.status = 409;
            out.errorCode = "routing_unresolved";
            out.errorRemedy = remedy::none();
            out.errorMessage = "the pending process admission is no longer current";
            return done(std::move(out));
        }
        if (live && startHolds_.count(name)) {
            out.status = 409;
            out.errorCode = "wrong_state";
            out.errorRemedy = remedy::retry();
            out.errorMessage = "a start is pending in this lane";
            out.errorDetail = "\"state\":\"starting\",\"lane\":" + jstr(name);
            return done(std::move(out));
        }
        if (!live) {
            const std::string path = worktreeRoot_ + "/" + name;
            const std::string root = root_;

            {
                std::error_code baseEc, pathEc;
                const std::string base =
                    fs::weakly_canonical(fs::path(worktreeRoot_), baseEc).string();
                const std::string got = fs::weakly_canonical(fs::path(path), pathEc).string();
                if (baseEc || pathEc || base.empty() || got.empty()) {
                    Outcome out;
                    out.status = 409;
                    out.errorCode = "worktree_unreadable";
                    out.errorRemedy = remedy::retry();
                    out.errorMessage = "this lane's path could not be resolved, so whether it is "
                                       "inside the lane root is not known; nothing was touched";
                    out.errorDetail = "\"lane\":" + jstr(name)
                                    + ",\"lane_root\":" + jstr(worktreeRoot_);
                    out.snapshot = snapshot_();
                    return done(std::move(out));
                }
                const bool inside = got.size() > base.size() + 1
                                 && got.compare(0, base.size(), base) == 0
                                 && got[base.size()] == '/';
                if (!inside) {
                    Outcome out;
                    out.status = 400;
                    out.errorCode = "bad_lane";
                    out.errorRemedy = remedy::none();
                    out.errorMessage = "a lane name may not leave the lane root; nothing was "
                                       "looked at and nothing was touched";
                    out.errorDetail = "\"lane\":" + jstr(name)
                                    + ",\"resolved\":" + jstr(got)
                                    + ",\"lane_root\":" + jstr(base);
                    out.snapshot = snapshot_();
                    return done(std::move(out));
                }
            }

            Outcome seed;
            seed.lane.name = name;
            seed.lane.worktree = path;
            seed.snapshot = snapshot_();
            exec_.offload(
                [result = std::move(seed), root, path, name, discardIgnored]() mutable {
                    const git::Presence leftover = git::presenceOf(path);
                    if (leftover == git::Presence::Unknown) {
                        result.status = 409;
                        result.errorCode = "worktree_unreadable";
                        result.errorRemedy = remedy::retry();
                        result.errorMessage = "this lane's worktree could not be read, so "
                                              "whether it is there is not known";
                        result.errorDetail = "\"lane\":" + jstr(name)
                                           + ",\"path\":" + jstr(path);
                        return result;
                    }
                    if (leftover == git::Presence::Absent) {
                        result.status = 404;
                        result.errorCode = "not_found";
                        result.errorRemedy = remedy::reloadManifest();
                        result.errorMessage = "no lane by that name is claimed";
                        result.errorDetail = "\"lane\":" + jstr(name)
                                           + ",\"path\":" + jstr(path);
                        return result;
                    }
                    if (!git::isRegisteredWorktree(root, path)) {
                        result.status = 409;
                        result.errorCode = "not_ours";
                        result.errorRemedy = remedy::none();
                        result.errorMessage = "something is at this lane's path that this tool "
                                              "did not create; it will not be touched";
                        result.errorDetail = "\"lane\":" + jstr(name)
                                           + ",\"path\":" + jstr(path) + ",\"ours\":false";
                        return result;
                    }
                    int changes = 0;
                    if (git::isDirty(path, changes)) {
                        result.status = 409;
                        result.errorCode = changes < 0 ? "unreadable_worktree"
                                                       : "uncommitted_changes";
                        result.errorRemedy = changes < 0 ? remedy::retry()
                                                         : remedy::openLane(name);
                        result.errorMessage = "this lane's leftover checkout holds work that is "
                                              "not committed; it will not be removed";
                        result.errorDetail = "\"lane\":" + jstr(name)
                                           + ",\"path\":" + jstr(path) + ",\"ours\":true"
                                           + ",\"uncommitted_changes\":"
                                           + std::to_string(changes < 0 ? 0 : changes);
                        return result;
                    }
                    {
                        bool asked = false;
                        const std::vector<std::string> ignored =
                            git::ignoredContent(path, asked);
                        if (!asked || (!ignored.empty() && !discardIgnored)) {
                            std::string list;
                            for (std::size_t i = 0; i < ignored.size(); ++i)
                                list += (i ? "," : "") + jstr(ignored[i]);
                            result.status = 409;
                            result.errorCode = asked ? "ignored_work" : "unreadable_worktree";
                            result.errorRemedy = asked
                               ? remedy::discardIgnored(name, ignored,
                                                        remedy::Cost{false, false, true, true})
                               : remedy::retry();
                            result.errorMessage = asked
                               ? "this lane's leftover checkout holds files git was told to "
                                 "ignore, which is where work that cannot be got back lives; it "
                                 "will not be removed"
                               : "git could not say what this lane's leftover checkout holds, so "
                                 "it will not be removed";
                            result.errorDetail = "\"lane\":" + jstr(name)
                                               + ",\"path\":" + jstr(path) + ",\"ours\":true"
                                               + ",\"ignored\":[" + list + "]";
                            return result;
                        }
                    }

                    std::string branch;
                    const int unmerged = git::unmergedCommits(path, branch);
                    if (unmerged != 0) {
                        result.status = 409;
                        result.errorCode = unmerged < 0 ? "unreadable_worktree"
                                                        : "unmerged_commits";
                        result.errorRemedy = unmerged < 0 ? remedy::retry()
                                                          : remedy::openLane(name);
                        result.errorMessage = "this lane's leftover checkout is on a branch "
                                              "carrying work no other branch has; it will not "
                                              "be removed";
                        result.errorDetail = "\"lane\":" + jstr(name)
                                           + ",\"path\":" + jstr(path) + ",\"ours\":true"
                                           + ",\"branch\":" + jstr(branch)
                                           + ",\"commits\":"
                                           + std::to_string(unmerged < 0 ? 0 : unmerged);
                        return result;
                    }
                    const git::Result removed = git::removeWorktree(root, path);
                    result.status = removed.ok ? 200 : 409;
                    if (!removed.ok) {
                        result.errorCode = "remove_failed";
                        result.errorRemedy = remedy::retry();
                        result.errorMessage = "git could not remove this lane's leftover checkout";
                        result.errorDetail = "\"lane\":" + jstr(name)
                                           + ",\"path\":" + jstr(path);
                        return result;
                    }
                    result.released = false;
                    result.worktreeRemoved = true;
                    result.reason = "leftover";
                    return result;
                },
                [done](Outcome out) { done(std::move(out)); });
            return;
        }

        const bool alive = live->pid > 0 && git::isRunning(live->pid);
        if (alive && !force) {
            out.status = 409;
            out.errorCode = "wrong_state";
            out.errorRemedy = remedy::endSession(name);
            out.errorMessage = "a process is running in this lane; release it with force=true "
                               "to kill it first";
            out.errorDetail = "\"state\":\"running\",\"lane\":" + jstr(name)
                            + ",\"pid\":" + std::to_string(live->pid);
            return done(std::move(out));
        }

        const Record taken = *live;
        disarm_(name);
        forget_(name);
        lanes_.erase(std::remove_if(lanes_.begin(), lanes_.end(),
            [&](const Record& r) { return r.name == name; }), lanes_.end());
        for (auto it = byRequestId_.begin(); it != byRequestId_.end();)
            it = (it->second == name) ? byRequestId_.erase(it) : std::next(it);
        forgetRolesAt_(name);

        std::vector<std::string> changed;
        for (auto& [res, who] : holder_)
            if (who == name) { who.clear(); changed.push_back(res); }
        for (auto& [res, q] : queue_) {
            auto it = std::find(q.begin(), q.end(), name);
            if (it != q.end()) { q.erase(it); changed.push_back(res); }
        }
        advance_(changed);
        publish_(changed);

        for (const auto& h : membershipHandlers_) h(taken, false);

        drainRoles_();

        const Snapshot after = snapshot_();
        const std::string root = root_;
        const std::string agentConfig = agentConfigRoot_ + "/" + taken.name;
        Outcome released;
        released.status = 200;
        released.released = true;
        released.lane = taken;
        released.snapshot = after;

        kill_(alive ? taken.pid : 0, 0,
              [this, released, root, taken, agentConfig, discardIgnored, done](bool killed) {
            Outcome seed = released;
            if (killed) {
                seed.killed = true;
                seed.killedPid = taken.pid;
            }
            exec_.offload(
                [result = std::move(seed), root, taken, agentConfig, discardIgnored]() mutable {
                    std::error_code ec;
                    fs::remove_all(fs::path(agentConfig), ec);
                    if (git::presenceOf(taken.worktree) == git::Presence::Absent) {
                        result.reason = "worktree_missing";
                        const git::Result pruned = git::pruneWorktrees(root);
                        if (!pruned.ok)
                            LOG(WARNING) << "lane " << taken.name
                                         << ": worktree gone and `git worktree prune` failed — the"
                                         << " next claim of this ticket may adopt a path that is"
                                         << " not there: " << trimmed(pruned.out, 200);
                        return result;
                    }
                    const git::Holding held = git::whatItHolds(taken.worktree);
                    if (!held.asked) {
                        result.reason = "unreadable_worktree";
                        return result;
                    }
                    if (held.uncommitted > 0) {
                        result.reason = "uncommitted_changes";
                        result.changes = held.uncommitted;
                        return result;
                    }
                    if (!held.ignored.empty() && !discardIgnored) {
                        result.reason = "ignored_work";
                        result.ignored = held.ignored;
                        return result;
                    }
                    const git::Result removed = git::removeWorktree(root, taken.worktree);
                    result.worktreeRemoved = removed.ok;
                    result.reason = removed.ok ? "clean" : "remove_failed";
                    return result;
                },
                [done](Outcome out) { done(std::move(out)); });
        });
    });
}

void lane::Store::releaseResource(const std::string& resource, const std::string& name, Done done)
{
    exec_.post([this, resource, name, done = std::move(done)] {
        Outcome out;
        out.resource = resource;
        out.lane.name = name;
        out.snapshot = snapshot_();

        if (name.empty()) {
            out.status = 400;
            out.errorCode = "bad_request";
            out.errorRemedy = remedy::none();
            out.errorMessage = "freeing a resource names the lane believed to be holding it; "
                               "without one this call cannot tell a stranded holder from "
                               "whoever has it now";
            out.errorDetail = "\"resource\":" + jstr(resource) + ",\"field\":\"lane\"";
            return done(std::move(out));
        }

        bool declared = false;
        for (const auto& r : manifest::Store::instance().data().resources)
            if (r.name == resource) declared = true;
        if (!declared) {
            out.status = 404;
            out.errorCode = "not_found";
            out.errorRemedy = remedy::reloadManifest();
            out.errorMessage = "this repository declares no resource by that name";
            out.errorDetail = "\"resource\":" + jstr(resource)
                            + ",\"path\":" + jstr("resources." + resource);
            return done(std::move(out));
        }

        auto held = holder_.find(resource);
        const std::string holder = held == holder_.end() ? std::string() : held->second;
        Record* live = find_(name);
        if (holder == name && live && spawning_.count(name)) {
            out.status = 409;
            out.errorCode = "wrong_state";
            out.errorRemedy = remedy::retry();
            out.errorMessage = "a process start is pending in this lane";
            out.errorDetail = "\"state\":\"starting\",\"lane\":" + jstr(name);
            return done(std::move(out));
        }
        if (holder != name || !live) {
            out.status = 200;
            out.freed = false;
            out.holder = holder;
            out.reason = holder.empty() ? "not_held" : "held_by_another";
            return done(std::move(out));
        }

        const int pid = live->pid;
        const bool alive = git::isRunning(pid);
        {
            Outcome out;
            out.resource = resource;
            out.lane.name = name;
            out.holderPid = pid;

            auto held = holder_.find(resource);
            const std::string holder =
                held == holder_.end() ? std::string() : held->second;
            Record* live = find_(name);
            if (holder != name || !live) {
                out.status = 200;
                out.freed = false;
                out.holder = holder;
                out.reason = holder.empty() ? "not_held" : "held_by_another";
                out.snapshot = snapshot_();
                return done(std::move(out));
            }
            if (alive && live->pid == pid) {
                out.status = 409;
                out.errorCode = "wrong_state";
                out.errorRemedy = remedy::endSession(name);
                out.errorMessage = "a process is running in the lane that holds this "
                                   "resource; a resource is freed this way only when its "
                                   "holder is verifiably gone";
                out.errorDetail = "\"lane\":" + jstr(name)
                                + ",\"resource\":" + jstr(resource)
                                + ",\"state\":\"running\""
                                + ",\"pid\":" + std::to_string(pid);
                out.holder = name;
                out.snapshot = snapshot_();
                return done(std::move(out));
            }

            std::vector<std::string> changed;
            for (auto& [res, who] : holder_)
                if (who == name) {
                    who.clear();
                    changed.push_back(res);
                    out.freedResources.push_back(res);
                }
            live->resourcesHeld.clear();

            for (const auto& n : resourcesFor_(live->workspace)) {
                if (std::find(queue_[n].begin(), queue_[n].end(), name) == queue_[n].end())
                    queue_[n].push_back(name);
                changed.push_back(n);
            }
            live->queued = !resourcesFor_(live->workspace).empty();

            advance_(changed);
            publish_(changed);

            auto now = holder_.find(resource);
            out.status = 200;
            out.freed = true;
            out.reason = "freed";
            out.holder = now == holder_.end() ? std::string() : now->second;
            out.snapshot = snapshot_();
            done(std::move(out));
        }
    });
}

void lane::Store::recover(const std::string& name,
                          const std::vector<std::pair<std::string, std::string>>& borrowed,
                          Done done)
{
    exec_.post([this, name, borrowed, done = std::move(done)] {
        Record* live = find_(name);
        if (startHolds_.count(name) || spawning_.count(name)) {
            Outcome out;
            out.status = 409;
            out.errorCode = "wrong_state";
            out.errorRemedy = remedy::retry();
            out.errorMessage = "a process start is pending in this lane";
            out.errorDetail = "\"state\":\"starting\",\"lane\":" + jstr(name);
            return done(std::move(out));
        }
        if (!live) {
            Outcome out;
            out.status = 404;
            out.errorCode = "not_found";
            out.errorMessage = "no lane by that name is claimed";
            out.errorDetail = "\"lane\":" + jstr(name);
            out.snapshot = snapshot_();
            return done(std::move(out));
        }

        const Record taken = *live;
        const std::string agentConfig = agentConfigRoot_ + "/" + taken.name;
        const int pid = (taken.pid > 0 && git::isRunning(taken.pid)) ? taken.pid : 0;

        auto result = std::make_shared<Outcome>();
        result->status = 200;
        result->recovered = true;
        result->lane = taken;

        kill_(pid, 0, [this, name, pid, result, agentConfig, borrowed, done](bool killed) {
            if (pid > 0 && !killed) {
                result->status = 409;
                result->errorCode = "wrong_state";
                result->errorMessage =
                    "a process in this lane survived being killed, so the lane was not "
                    "recovered; nothing was destroyed and the session is untouched";
                result->errorDetail = "\"state\":\"running\",\"lane\":"
                                    + jstr(result->lane.name)
                                    + ",\"pid\":" + std::to_string(pid);
                result->snapshot = snapshot_();
                return done(*result);
            }
            if (pid > 0) {
                result->killed = true;
                result->killedPid = pid;
            }
            exec_.offload(
                [agentConfig, borrowed] { return reCarry(agentConfig, borrowed); },
                [this, name, pid, result, done](std::vector<std::string> reCarried) {
                    result->reCarried = std::move(reCarried);
                    if (pid <= 0) {
                        if (Record* still = find_(name)) result->lane = *still;
                        result->snapshot = snapshot_();
                        return done(*result);
                    }
                    settle_(name, pid, result, done, 0);
                });
        });
    });
}

void lane::Store::kill_(int pid, int waited, std::function<void(bool)> done)
{
    if (pid <= 0) return done(false);
    if (stopping_) {
        killing_.erase(pid);
        return done(false);
    }
    if (waited == 0) git::signalGroup(pid, SIGTERM);
    if (waited == KILL_GRACE_MS) git::signalGroup(pid, SIGKILL);

    if (git::groupIsEmpty(pid)) {
        killing_.erase(pid);
        return done(true);
    }
    if (waited >= KILL_GRACE_MS + KILL_DEADLINE_MS) {
        killing_.erase(pid);
        return done(false);
    }

    killing_.insert_or_assign(pid, core::Task {exec_,
        [this, pid, waited, done] { kill_(pid, waited + KILL_STEP_MS, done); }});
    killing_.at(pid).start(KILL_STEP_MS, 1);
}

void lane::Store::settle_(const std::string& name, int pid, std::shared_ptr<Outcome> result,
                          Done done, int attempt)
{
    Record* still = find_(name);
    if (!still || still->pid != pid || attempt >= SETTLE_TRIES) {
        if (still && still->pid == pid) { still->pid = 0; reconcile_(); }
        if (still) result->lane = *still;
        result->snapshot = snapshot_();
        return done(*result);
    }

    settling_.insert_or_assign(name, core::Task {exec_,
        [this, name, pid, result, done, attempt] {
            settling_.erase(name);
            settle_(name, pid, result, done, attempt + 1);
        }});
    settling_.at(name).start(SETTLE_STEP_MS, 1);
}

void lane::Store::holdStart(const std::string& name,
                            std::function<void(bool, bool)> done)
{
    exec_.post([this, name, done = std::move(done)] {
        const bool exists = find_(name) != nullptr;
        if (!exists) return done(false, false);
        const bool held = !spawning_.count(name) && startHolds_.insert(name).second;
        done(true, held);
    });
}

void lane::Store::releaseStartHold(const std::string& name, std::function<void()> done)
{
    exec_.post([this, name, done = std::move(done)] {
        startHolds_.erase(name);
        const auto pending = spawning_.find(name);
        if (pending != spawning_.end()) completePending_(name, pending->second);
        if (done) done();
    });
}

void lane::Store::startGate(const std::string& name, Done done)
{
    exec_.post([this, name, done = std::move(done)] {
        Outcome out;
        Record* live = find_(name);
        if (!live) {
            out.status = 404;
            out.errorCode = "not_found";
            out.errorRemedy = remedy::reloadManifest();
            out.errorMessage = "no lane by that name is claimed";
            out.errorDetail = "\"lane\":" + jstr(name);
            return done(std::move(out));
        }

        if (spawning_.count(name)) {
            out.status = 409;
            out.errorCode = "wrong_state";
            out.errorRemedy = remedy::retry();
            out.errorMessage = "a process start is pending in this lane";
            out.errorDetail = "\"state\":\"starting\",\"lane\":" + jstr(name);
            return done(std::move(out));
        }

        if (live->queued) {
            const Waiting waiting = live->waiting;
            const int holderPid = waiting.holderPid;
            const bool alive = git::isRunning(holderPid);

            out.status = 409;
            out.errorCode = "resource_busy";
            out.errorRemedy = alive
                ? remedy::releaseLane(waiting.heldBy, true,
                                      remedy::Cost{false, true, true, false})
                : remedy::releaseResource(waiting.resource, waiting.heldBy);
            out.errorMessage = alive
                ? "an exclusive resource this lane needs is held by another lane"
                : "an exclusive resource this lane needs is held by a lane whose "
                  "process is not running";
            out.errorDetail = "\"lane\":" + jstr(name)
                            + ",\"resource\":" + jstr(waiting.resource)
                            + ",\"held_by\":" + jstr(waiting.heldBy)
                            + ",\"holder_alive\":" + (alive ? "true" : "false")
                            + ",\"holder_pid\":" + std::to_string(holderPid)
                            + ",\"position\":" + std::to_string(waiting.position);
            out.snapshot = snapshot_();
            return done(std::move(out));
        }

        out.status = 200;
        out.lane = *live;
        done(std::move(out));
    });
}

void lane::Store::onProcessExit(ExitHandler handler)
{
    exitHandlers_.push_back(std::move(handler));
}

void lane::Store::setRun(const std::string& name, const std::string& runId,
                         const std::string& sessionId)
{
    exec_.post([this, name, runId, sessionId] {
        if (Record* live = find_(name)) {
            live->runId = runId;
            live->sessionId = sessionId;
            live->goalStatus.clear();
            live->lastWords = Words {};
            live->hasWords = false;
            live->wordsPending = false;
            words_.erase(name);
        }
    });
}

void lane::Store::setCost(const std::string& name, double costUsd)
{
    exec_.post([this, name, costUsd] {
        if (Record* live = find_(name)) live->costUsd = costUsd;
    });
}

void lane::Store::onWords(WordsHandler handler)
{
    wordsHandlers_.push_back(std::move(handler));
}

void lane::Store::setWords(const std::string& name, const std::string& kind,
                           const std::string& text)
{
    exec_.post([this, name, kind, text] {
        Record* live = find_(name);
        if (!live) return;
        live->lastWords = Words {kind, text, state::isoOf(state::nowMs())};
        live->hasWords = true;
        if (words_.find(name) != words_.end()) {
            live->wordsPending = true;
            return;
        }
        publishWords_(name);
        armWords_(name);
    });
}

void lane::Store::publishWords_(const std::string& name)
{
    Record* live = find_(name);
    if (!live || !live->hasWords) return;
    live->wordsPending = false;
    const std::string ticketId = live->ticketId;
    const Words words = live->lastWords;
    for (const auto& handler : wordsHandlers_) handler(name, ticketId, words);
}

void lane::Store::armWords_(const std::string& name)
{
    core::Task task {exec_, [this, name] {
            words_.erase(name);
            if (stopping_) return;
            Record* live = find_(name);
            if (!live || !live->wordsPending) return;
            publishWords_(name);
            armWords_(name);
        }};
    words_.insert_or_assign(name, std::move(task));
    words_.at(name).start(WORDS_COALESCE_MS, 1);
}

void lane::Store::reportStage(const StageRequest& request,
                              std::function<void(progress::Stage)> done)
{
    exec_.post([this, request, done = std::move(done)] {
        std::vector<progress::Report>& history = stages_[request.ticketId];
        history.push_back(request.report);
        history.back().atMs = state::nowMs();
        const progress::Stage stage = progress::of(request.sequence, request.source, history);

        if (done) done(stage);
    });
}

void lane::Store::stageHistory(std::function<void(StageHistory)> done)
{
    exec_.post([this, done = std::move(done)] {
        if (done) done(stages_);
    });
}

namespace {

std::string roleKey(const lane::Store::RoleStart& start)
{
    return start.ticketId + "\x1f" + start.stage + "\x1f" + start.role;
}

}

bool lane::Store::seatFree_() const
{
    return static_cast<int>(lanes_.size()) + startingRoles_ < seats_;
}

void lane::Store::onResourceGranted(GrantHandler handler)
{
    grantHandlers_.push_back(std::move(handler));
}

void lane::Store::onRoleStart(RoleHandler handler)
{
    roleHandlers_.push_back(std::move(handler));
}


bool lane::Store::triggerStillWorking_(const Record& record) const
{
    const auto pending = spawning_.find(record.name);
    if (pending != spawning_.end() && sameIncarnation(record, pending->second->record)) return true;
    const bool live = record.pid != 0;
    if (!live) return false;
    const auto watch = watches_.find(record.name);
    if (watch != watches_.end() && watch->second->pid == record.pid
        && watch->second->holdResourcesUntilExit) return true;
    return record.turnActive || !record.turnEnded;
}


void lane::Store::requestRole(const RoleStart& request, std::function<void(RoleAdmission)> done)
{
    exec_.post([this, request, done = std::move(done)] {
        RoleAdmission out;
        out.start = request;

        Record* from = find_(request.triggeredByLane);
        if (!from) {
            out.refused = true;
            out.reason = "no_such_lane";
            if (done) done(std::move(out));
            return;
        }

        if (from->role == request.role) {
            out.refused = true;
            out.reason = "self_trigger";
            if (done) done(std::move(out));
            return;
        }

        out.start.workspace = from->workspace;

        const std::string key = roleKey(request);
        if (roles_.count(key)) {
            out.refused = true;
            out.reason = "already_started";
            if (done) done(std::move(out));
            return;
        }

        RoleRecord record;
        record.start = out.start;
        if (seatFree_()) {
            record.state = "started";
            ++startingRoles_;
            out.admitted = true;
            reconcile_();
        } else {
            record.state = "pending";
            pendingRoles_.push_back(key);
            out.deferred = true;
        }
        roles_[key] = std::move(record);

        if (done) done(std::move(out));
    });
}

void lane::Store::noteRoleStarted(const RoleStart& start, const std::string& name)
{
    exec_.post([this, start, name] {
        auto it = roles_.find(roleKey(start));
        if (it == roles_.end()) return;
        it->second.state = "started";
        it->second.lane = name;
        if (const Record* record = find_(name)) it->second.runId = record->runId;
        if (startingRoles_ > 0) --startingRoles_;
        drainRoles_();
    });
}

void lane::Store::forgetRolesAt_(const std::string& lane)
{
    for (auto it = roles_.begin(); it != roles_.end();) {
        if (it->second.lane != lane) { ++it; continue; }
        const auto queued = std::find(pendingRoles_.begin(), pendingRoles_.end(), it->first);
        if (queued != pendingRoles_.end()) pendingRoles_.erase(queued);
        it = roles_.erase(it);
    }
}

void lane::Store::forgetRole(const RoleStart& start)
{
    exec_.post([this, start] {
        const std::string key = roleKey(start);
        const auto queued = std::find(pendingRoles_.begin(), pendingRoles_.end(), key);
        const bool wasQueued = queued != pendingRoles_.end();
        if (wasQueued) pendingRoles_.erase(queued);
        if (roles_.erase(key) && !wasQueued && startingRoles_ > 0) --startingRoles_;
        drainRoles_();
    });
}

void lane::Store::drainRoles_()
{
    while (!pendingRoles_.empty() && seatFree_()) {
        const std::string key = pendingRoles_.front();
        pendingRoles_.erase(pendingRoles_.begin());
        auto it = roles_.find(key);
        if (it == roles_.end()) continue;
        it->second.state = "started";
        ++startingRoles_;
        const RoleStart start = it->second.start;
        for (auto& handler : roleHandlers_) handler(start);
    }
}

void lane::Store::roleStates(std::function<void(RoleStates)> done)
{
    exec_.post([this, done = std::move(done)] {
        RoleStates out;
        for (const auto& [key, record] : roles_) {
            RoleState state;
            state.role = record.start.role;
            state.stage = record.start.stage;
            state.state = record.state;
            state.lane = record.lane;
            state.runId = record.runId;
            out[record.start.ticketId].push_back(std::move(state));
        }
        if (done) done(std::move(out));
    });
}

void lane::Store::onSilence(SilenceHandler handler)
{
    silenceHandlers_.push_back(std::move(handler));
}

void lane::Store::noteActivity(const std::string& name)
{
    exec_.post([this, name] { stamp_(name); });
}

void lane::Store::setTurn(const std::string& name, bool active, bool ended)
{
    exec_.post([this, name, active, ended] {
        Record* live = find_(name);
        if (!live) return;
        live->turnActive = active;
        if (ended) live->turnEnded = true;
        if (ended) reconcile_();
    });
}

void lane::Store::noteOutcome(const std::string& name, const std::string& outcome)
{
    exec_.post([this, name, outcome] {
        Record* live = find_(name);
        if (!live) return;
        if (outcome == "running") {
            live->outcome.clear();
            live->turnEnded = false;
            stamp_(name);
            return;
        }
        live->outcome = outcome;
        live->turnActive = false;
        live->silentAnnounced = false;
        disarm_(name);
    });
}

void lane::Store::setGoalStatus(const std::string& name, const std::string& status)
{
    exec_.post([this, name, status] {
        if (Record* live = find_(name)) live->goalStatus = status;
    });
}

void lane::Store::retimeSilence()
{
    exec_.post([this] {
        for (const auto& record : lanes_) arm_(record.name);
    });
}

void lane::Store::stamp_(const std::string& name)
{
    Record* live = find_(name);
    if (!live) return;
    endSilence_(*live);
    live->lastOutputMs = state::nowMs();
    if (silence_.find(name) == silence_.end()) arm_(name);
}

void lane::Store::arm_(const std::string& name)
{
    Record* live = find_(name);
    if (!live || stopping_) return disarm_(name);

    state::Inputs in;
    in.live = live->pid > 0;
    in.lastOutputMs = live->lastOutputMs;
    in.now = state::nowMs();
    const long long deadline = state::silenceDeadlineMs(in);
    if (deadline == 0) return disarm_(name);

    const long long wait = deadline > in.now ? deadline - in.now : 0;

    core::Task task {exec_, [this, name] {
            if (stopping_) return;
            silence_.erase(name);

            Record* record = find_(name);
            if (record) {
                state::Inputs now;
                now.live = record->pid > 0;
                now.lastOutputMs = record->lastOutputMs;
                now.now = state::nowMs();
                if (state::silenceDeadlineMs(now) > now.now) return arm_(name);
            }

            if (!record || record->silentAnnounced) return;
            record->silentAnnounced = true;
            const long long forS = (state::nowMs() - record->lastOutputMs) / 1000;
            for (const auto& handler : silenceHandlers_) handler(name, true, forS);
        }};
    silence_.insert_or_assign(name, std::move(task));
    silence_.at(name).start(static_cast<uint32_t>(wait), 1);
}

void lane::Store::endSilence_(Record& record)
{
    if (!record.silentAnnounced) return;
    record.silentAnnounced = false;
    const long long forS = (state::nowMs() - record.lastOutputMs) / 1000;
    for (const auto& handler : silenceHandlers_) handler(record.name, false, forS);
}

void lane::Store::disarm_(const std::string& name)
{
    silence_.erase(name);
}

void lane::Store::exists(const std::string& name, std::function<void(bool)> done)
{
    exec_.post([this, name, done = std::move(done)] { done(find_(name) != nullptr); });
}

void lane::Store::failNextSpawns(int times, std::function<void()> done)
{
    exec_.post([this, times, done = std::move(done)] {
        failSpawns_ = times;
        done();
    });
}

void lane::Store::attachProcess(const std::string& name, const std::string& command,
                                std::function<void(Spawn)> done)
{
    attachProcess(name, command, {}, Sink{}, std::move(done));
}

void lane::Store::onPrepareConfig(ConfigPreparer prepare)
{
    exec_.post([this, prepare = std::move(prepare)]() mutable {
        prepareConfig_ = std::move(prepare);
    });
}

void lane::Store::attachProcess(const std::string& name, const std::string& command,
                                const std::string& label, Sink sink,
                                std::function<void(Spawn)> done, std::optional<Record> expected)
{
    Launch launch;
    launch.shell = command;
    launch.label = label;
    launch.sink = std::move(sink);
    launch.holdResourcesUntilExit = true;
    launch.expected = std::move(expected);
    startProcess(name, launch, std::move(done));
}

bool lane::Store::sameIncarnation(const Record& left, const Record& right)
{
    return left.name == right.name && left.ticketId == right.ticketId
        && left.workspace == right.workspace && left.role == right.role
        && left.branch == right.branch && left.worktree == right.worktree
        && left.claimedAtMs == right.claimedAtMs && left.alsoWorkspaces == right.alsoWorkspaces;
}

void lane::Store::startProcess(const std::string& name, const Launch& launch,
                               std::function<void(Spawn)> done)
{
    exec_.post([this, name, launch, done = std::move(done)] {
        Record* live = find_(name);
        if (!live || live->pid > 0 || spawning_.count(name) || live->queued || !live->worktreeReady
            || (launch.expected && !sameIncarnation(*live, *launch.expected))) {
            if (launch.stdinFd >= 0) ::close(launch.stdinFd);
            Spawn refused;
            refused.laneExists = live != nullptr;
            refused.errorCode = live ? "wrong_state" : "not_found";
            if (live && launch.expected && !sameIncarnation(*live, *launch.expected)) {
                refused.error = "the lane incarnation changed before process admission";
                refused.errorCode = "routing_unresolved";
            } else if (live && live->queued) {
                refused.error = "the lane is waiting for an exclusive resource";
                refused.errorCode = "resource_busy";
            } else if (live && spawning_.count(name)) {
                refused.error = "a process start is pending in this lane";
            } else if (live && !live->worktreeReady) {
                refused.error = "the lane worktree is not ready for a process";
            }
            if (live) {
                auto held = watches_.find(name);
                if (held != watches_.end()) refused.busy = held->second->label;
            }
            return done(std::move(refused));
        }

        if (failSpawns_ > 0) {
            --failSpawns_;
            if (launch.stdinFd >= 0) ::close(launch.stdinFd);
            Spawn failed;
            failed.laneExists = true;
            failed.error = "the confined process could not be started";
            failed.errorCode = "spawn_failed";
            return done(std::move(failed));
        }

        auto pending = std::make_shared<PendingSpawn>();
        pending->record = *live;
        pending->stopPid = std::make_shared<StopPid::element_type>(0);
        spawning_[name] = pending;

        const std::string cwd = live->worktree;
        const std::string branch = live->branch;
        const std::string ticketId = live->ticketId;
        const std::string root = root_;
        const std::string gitRoot = gitRoot_.empty() ? root_ : gitRoot_;

        std::vector<std::string> hostReadOnly;
        const auto& manifestData = manifest::Store::instance().data();
        const manifest::Store::Agent* selectedAgent = &manifestData.agent;
        for (const auto& profile : manifestData.agents)
            if (profile.name == launch.agentProfile) selectedAgent = &profile;
        const manifest::Store::Agent& agent = *selectedAgent;
        if (!agent.binaryDir.empty()) hostReadOnly.push_back(agent.binaryDir);

        std::vector<std::string> devices;
        for (const std::string& held : live->resourcesHeld)
            for (const manifest::Store::Resource& r : manifest::Store::instance().data().resources)
                if (r.name == held)
                    devices.insert(devices.end(), r.devices.begin(), r.devices.end());

        std::vector<confine::AgentBinary> agentBinaries;
        for (const auto& declared : manifestData.agents) {
            const std::size_t space = declared.command.find_first_of(" \t");
            const std::string name = fs::path(declared.command.substr(0, space)).filename().string();
            agentBinaries.push_back({name, declared.binaryAbs});
        }

        const std::vector<std::pair<std::string, std::string>> toolchain =
            toolchainFor_(live->workspace, live->alsoWorkspaces);

        const std::string agentConfig = agentConfigRoot_ + "/" + name
                                     + (agent.models.empty() ? "" : "/profiles/" + agent.name);

        Launch prepared = launch;
        if (prepareConfig_)
            prepareConfig_(agent.name, cwd, prepared.configSeeded, prepared.configBorrowed,
                           prepared.runtimeDirectories);

        Spawn started;
        started.ticketId = ticketId;
        started.role = live->role;
        started.worktree = cwd;

        exec_.offload(
            [result = std::move(started), gitRoot, cwd, branch, hostReadOnly, devices,
             agentConfig, toolchain, agentBinaries, pending,
             launch = std::move(prepared)]() mutable {
                Started out;
                if (pending->stopPid->load() < 0 || (launch.cancelled && launch.cancelled->load())) {
                    out.spawn = std::move(result);
                    out.spawn.error = "the start was cancelled during shutdown";
                    out.spawn.errorCode = "spawn_failed";
                    return out;
                }
                out.policy = confine::build(gitRoot, cwd, branch, hostReadOnly, devices,
                                            agentConfig, toolchain, agentBinaries,
                                            launch.runtimeDirectories);
                out.spawn = std::move(result);
                out.spawn.program = out.policy.binary;
                if (!out.policy.ok) {
                    if (launch.stdinFd >= 0) ::close(launch.stdinFd);
                    out.spawn.error = out.policy.reason;
                    out.spawn.errorCode = "confinement_unavailable";
                    return out;
                }
                if (!furnish(out.policy.agentConfig, launch, out.spawn.error)) {
                    if (launch.stdinFd >= 0) ::close(launch.stdinFd);
                    out.spawn.errorCode = "confinement_unavailable";
                    return out;
                }
                confine::Command command;
                command.argv = launch.argv;
                command.env = launch.env;
                command.unsetEnv = launch.unsetEnv;
                if (!launch.configDirVar.empty() && !out.policy.agentConfigInside.empty())
                    command.env.emplace_back(launch.configDirVar, out.policy.agentConfigInside);
                if (command.argv.empty()) command.shell = launch.shell;
                if (pending->stopPid->load() < 0 || (launch.cancelled && launch.cancelled->load())) {
                    out.spawn.error = "the start was cancelled during shutdown";
                    out.spawn.errorCode = "spawn_failed";
                    if (launch.stdinFd >= 0) ::close(launch.stdinFd);
                    return out;
                }
                std::string execError;
                out.spawn.pid = git::spawnConfined(confine::wrap(out.policy, command), cwd,
                                                   out.readFd, launch.stdinFd, &execError);
                if (out.spawn.pid > 0 && launch.stopPid) launch.stopPid->store(out.spawn.pid);
                if (out.spawn.pid > 0) {
                    int expected = 0;
                    if (!pending->stopPid->compare_exchange_strong(expected, out.spawn.pid)
                        || pending->stopPid->load() < 0
                        || (launch.cancelled && launch.cancelled->load()))
                        git::signalGroup(out.spawn.pid, SIGKILL);
                }
                if (out.spawn.pid <= 0) {
                    out.spawn.error = execError.empty()
                        ? "the confined process could not be started"
                        : "the confined process could not be started — " + execError;
                    out.spawn.errorCode = "spawn_failed";
                }
                return out;
            },
            [this, name, launch, pending, done](Started out) {
                const Record* current = find_(name);
                const auto held = spawning_.find(name);
                out.spawn.laneExists = current != nullptr;
                const bool stale = !current || !sameIncarnation(*current, pending->record)
                    || held == spawning_.end() || held->second != pending;
                const bool cancelled = pending->stopPid->load() < 0
                    || (launch.cancelled && launch.cancelled->load());
                if (stale || cancelled || stopping_) {
                    const int ownedPid = out.spawn.pid;
                    out.spawn.error = stale ? "the lane incarnation changed during process admission"
                                            : "the pending process start was cancelled";
                    out.spawn.errorCode = stale ? "routing_unresolved" : "wrong_state";
                    auto complete = [this, name, pending, done,
                                           result = std::move(out.spawn)]() mutable {
                        result.pid = 0;
                        pending->complete = true;
                        done(std::move(result));
                        completePending_(name, pending);
                    };
                    if (ownedPid > 0) {
                        git::signalGroup(ownedPid, SIGKILL);
                        auto watch = std::make_shared<Watch>(exec_, out.readFd);
                        watch->lane = name;
                        watch->pid = ownedPid;
                        watch->stopPid = launch.stopPid ? launch.stopPid : pending->stopPid;
                        watch->afterReap = complete;
                        return finish_(watch);
                    }
                    if (out.readFd >= 0) ::close(out.readFd);
                    complete();
                    return;
                }
                if (out.spawn.pid <= 0) {
                    if (out.readFd >= 0) ::close(out.readFd);
                    pending->complete = true;
                    done(std::move(out.spawn));
                    completePending_(name, pending);
                    return;
                }
                if (Record* live = find_(name)) {
                    live->pid = out.spawn.pid;
                    live->outcome.clear();
                    stamp_(name);
                }

                auto watch = std::make_shared<Watch>(exec_, out.readFd);
                watch->lane = name;
                watch->pid = out.spawn.pid;
                watch->policy = std::move(out.policy);
                watch->label = launch.label;
                watch->sink = launch.sink;
                watch->holdResourcesUntilExit = launch.holdResourcesUntilExit;
                watch->stopPid = launch.stopPid;
                watches_[name] = watch;
                watch_(watch);
                pending->complete = true;
                completePending_(name, pending);
                done(std::move(out.spawn));
            });
    });
}

void lane::Store::killProcessIfCurrent(const std::string& name, int pid)
{
    exec_.post([this, name, pid] {
        const auto held = watches_.find(name);
        if (held != watches_.end() && held->second->pid == pid)
            git::signalGroup(pid, SIGKILL);
    });
}

void lane::Store::watch_(std::shared_ptr<Watch> watch)
{
    watch->out.read(
        watch->buf.data(), watch->buf.size(),
        [this, watch](bool ok, std::size_t n) {
            if (n > 0) {
                const std::string text(watch->buf.data(), n);
                if (watch->sink.chunk) watch->sink.chunk(text);
                stamp_(watch->lane);

                watch->line += text;
                for (std::size_t nl; (nl = watch->line.find('\n')) != std::string::npos;) {
                    report_(watch, watch->line.substr(0, nl));
                    watch->line.erase(0, nl + 1);
                }
                if (watch->line.size() > 8192) {
                    report_(watch, watch->line);
                    watch->line.clear();
                }
            }
            if (!ok) return finish_(watch);
            watch_(watch);
        });
}

void lane::Store::report_(const std::shared_ptr<Watch>& watch, const std::string& line)
{
    const confine::Refusal refusal = confine::inspect(watch->policy, line);
    if (refusal.refused)
        for (const auto& handler : isolationHandlers_) handler(watch->lane, refusal);
}

void lane::Store::finish_(std::shared_ptr<Watch> watch)
{
    watch->out.close();
    if (stopping_ && !watch->afterReap) return;

    if (!watch->line.empty()) {
        report_(watch, watch->line);
        watch->line.clear();
    }

    const int pid = watch->pid;
    const auto stopPid = watch->stopPid;
    const std::string name = watch->lane;
    const Sink sink = watch->sink;
    const auto afterReap = watch->afterReap;
    auto held = watches_.find(name);
    if (held != watches_.end() && held->second == watch && !watch->holdResourcesUntilExit)
        watches_.erase(held);
    if (!afterReap) disarm_(name);

    exec_.offload(
        [pid, stopPid] {
            Exit ended;
            const auto retireStopPid = [pid, &stopPid] {
                if (!stopPid) return;
                int expected = pid;
                while (!stopPid->compare_exchange_weak(expected, 0)) {
                    if (expected == 0) return;
                    if (expected == pid) continue;
                    if (expected != -pid) return;
                    std::this_thread::yield();
                    expected = pid;
                }
            };
            if (stopPid) {
                siginfo_t observed {};
                int seen;
                do {
                    seen = ::waitid(P_PID, static_cast<id_t>(pid), &observed,
                                    WEXITED | WNOWAIT);
                } while (seen < 0 && errno == EINTR);
                if (seen == 0 && observed.si_pid == pid) {
                    git::signalGroup(pid, SIGKILL);
                    retireStopPid();
                } else if (seen < 0 && errno == ECHILD) retireStopPid();
            }
            while (::waitpid(static_cast<pid_t>(pid), &ended.status, 0) < 0 && errno == EINTR) {}
            retireStopPid();
            ended.code = WIFEXITED(ended.status) ? WEXITSTATUS(ended.status)
                       : WIFSIGNALED(ended.status) ? 128 + WTERMSIG(ended.status) : -1;
            return ended;
        },
        [this, name, pid, sink, afterReap, watch](Exit ended) {
            if (afterReap) return afterReap();
            const auto held = watches_.find(name);
            if (held != watches_.end() && held->second == watch) watches_.erase(held);
            Record* live = find_(name);
            if (live && live->pid == pid) { live->pid = 0; reconcile_(); }
            if (sink.exit) sink.exit(ended.code);
            for (const auto& handler : exitHandlers_) handler(name, pid, ended.status);
        });
}
