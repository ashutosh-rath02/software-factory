#include "probe.h"

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <sstream>


#include <core/engine.h>
#include <core/worker.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <glog/logging.h>

#include "git.h"

namespace fs = std::filesystem;

using manifest::jsonEscape;

using manifest::jstr;
std::vector<std::string> delivery::Probe::lines(const std::string& text)
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

std::pair<std::string, std::string> delivery::Probe::split(const std::string& line, char sep)
{
    const std::size_t at = line.find(sep);
    if (at == std::string::npos) return {line, ""};
    return {line.substr(0, at), line.substr(at + 1)};
}

std::string delivery::Probe::strip(const std::string& value, const std::string& prefix)
{
    return value.compare(0, prefix.size(), prefix) == 0 ? value.substr(prefix.size()) : value;
}

const char* HEADS = "refs/heads/";
const char* REMOTES = "refs/remotes/";

int delivery::Probe::envInt(const char* name, int fallback)
{
    const char* raw = std::getenv(name);
    if (!raw || !*raw) return fallback;
    const int value = std::atoi(raw);
    return value > 0 ? value : fallback;
}

std::string delivery::Probe::plural(int n, const char* one, const char* many)
{
    return std::to_string(n) + " " + (n == 1 ? one : many);
}

void delivery::Probe::init()
{
    const auto& m = manifest::Store::instance().data();
    root_ = m.root;
    inFlightStatus_ = m.inFlightStatus;

    for (const auto& repo : m.repos) {
        if (root_.empty() || repo.docs.reportsAbs.empty()) continue;
        std::error_code ec;
        const bool own = !repo.absPath.empty() && repo.absPath != root_
            && fs::exists(fs::path(repo.absPath) / ".git", ec);
        if (own) {
            const fs::path rel = fs::relative(fs::path(repo.docs.reportsAbs), fs::path(repo.absPath), ec);
            SubRepo sub;
            sub.root = repo.absPath;
            if (!ec && !rel.empty()) sub.reportsRel.push_back(rel.string());
            subRepos_.push_back(std::move(sub));
            continue;
        }
        const fs::path rel = fs::relative(fs::path(repo.docs.reportsAbs), fs::path(root_), ec);
        if (!ec && !rel.empty()) reportsRel_.push_back(rel.string());
    }

    pollMs_ = envInt("FACTORY_DELIVERY_POLL_MS", 5000);
    watchRefs_();
}

void delivery::Probe::watchRefs_()
{
    if (root_.empty()) return;
    const std::string git = root_ + "/.git";
    std::error_code ec;
    if (!fs::is_directory(fs::path(git), ec)) return;
    RefWatch_.watch(git, queue_);
    RefWatch_.watch(git + "/refs/heads", queue_);
}

FILE_HANDLER(delivery::Probe, Ref)
{
    (void)change;
    if (stopping_ || passInFlight_) return;
    pollTask_.stop();
    startPass_();
}

void delivery::Probe::start()
{
    LOG(INFO) << "delivery probing " << (root_.empty() ? "(no repository)" : root_)
              << " every " << pollMs_ << "ms";
    queue_.post([this] { startPass_(); });
}

void delivery::Probe::stop()
{
    queue_.post([this] {
        stopping_ = true;
        pollTask_.stop();
        auto waiting = std::move(waiters_);
        waiters_.clear();
        for (auto& w : waiting) w(snapshot_);
    });
}

void delivery::Probe::snapshotThen(std::function<void(std::shared_ptr<const Snapshot>)> done)
{
    queue_.post( [this, done = std::move(done)]() mutable {
        if (snapshot_ || stopping_) { done(snapshot_); return; }
        waiters_.push_back(std::move(done));
        startPass_();
    });
}

void delivery::Probe::requestPass()
{
    queue_.post([this] {
        if (stopping_) return;
        if (passInFlight_) passRequested_ = true;
        else {
            pollTask_.stop();
            startPass_();
        }
    });
}

void delivery::Probe::onChange(ChangeHandler handler)
{
    queue_.post( [this, handler = std::move(handler)]() mutable {
        handlers_.push_back(std::move(handler));
    });
}

void delivery::Probe::onSnapshot(ChangeHandler handler)
{
    queue_.post([this, handler = std::move(handler)]() mutable {
        snapshotHandlers_.push_back(std::move(handler));
    });
}

void delivery::Probe::startPass_()
{
    if (stopping_ || passInFlight_) return;
    passInFlight_ = true;
    queue_.offload(
        [this] { return scan_(); },
        [this](Snapshot pass) {
            passInFlight_ = false;
            publish_(std::make_shared<Snapshot>(std::move(pass)));
            if (passRequested_) {
                passRequested_ = false;
                startPass_();
                return;
            }
            scheduleScan_();
        });
}

void delivery::Probe::scheduleScan_()
{
    if (stopping_) return;
    pollTask_.start(pollMs_, 1);
}

void delivery::Probe::pollTick_()
{
    if (stopping_) return;
    startPass_();
}

void delivery::Probe::publish_(std::shared_ptr<Snapshot> snapshot)
{
    const bool changed = !snapshot_ || snapshot_->fingerprint != snapshot->fingerprint;
    snapshot_ = snapshot;

    auto waiting = std::move(waiters_);
    waiters_.clear();
    for (auto& w : waiting) w(snapshot_);

    for (auto& h : snapshotHandlers_) h(snapshot_);

    if (!changed) return;
    for (auto& h : handlers_) h(snapshot_);
}

void delivery::Probe::scanRepo_(const std::string& root, const std::vector<std::string>& reportsRel,
                                Snapshot& s, std::set<std::string>& used)
{
    Output refs = git(root, "for-each-ref",
                      {"--format=%(refname)\t%(objectname)", "refs/heads", "refs/remotes"});
    used.insert("for-each-ref");
    std::set<std::string> remoteTips;
    for (auto& line : lines(refs.text)) {
        auto [name, tip] = split(line, '\t');
        if (name.compare(0, std::string(HEADS).size(), HEADS) == 0) {
            Branch b;
            b.name = strip(name, HEADS);
            b.tip = tip;
            s.branches[b.name] = b;
        } else if (name.compare(0, std::string(REMOTES).size(), REMOTES) == 0) {
            remoteTips.insert(tip);
        }
    }
    for (auto& [name, b] : s.branches) b.pushed = remoteTips.count(b.tip) > 0;

    if (s.branches.count("main")) s.base = "main";
    else if (s.branches.count("master")) s.base = "master";
    else {
        Output head = git(root, "rev-parse", {"--abbrev-ref", "HEAD"});
        s.base = head.ok ? lines(head.text).empty() ? "" : lines(head.text).front() : "";
    }

    {
        auto it = s.branches.find(s.base);
        if (it != s.branches.end()) s.baseTip = it->second.tip;
    }

    if (!s.base.empty()) {
        Output merged = git(root, "for-each-ref",
                            {"--merged", s.base, "--format=%(refname)", "refs/heads"});
        for (auto& line : lines(merged.text)) {
            auto it = s.branches.find(strip(line, HEADS));
            if (it != s.branches.end()) it->second.merged = true;
        }

        for (auto& [name, b] : s.branches) {
            if (b.merged || name == s.base) continue;
            Output ahead = git(root, "rev-list", {"--count", s.base + ".." + name});
            used.insert("rev-list");
            if (ahead.ok && !ahead.text.empty()) b.ahead = std::atoi(ahead.text.c_str());
            if (b.ahead > 0) {
                Output who = git(root, "rev-list",
                                 {"--no-merges", "--format=%an", "-n", "40",
                                  s.base + ".." + name});
                std::map<std::string, int> seen;
                for (auto& line : lines(who.text)) {
                    if (line.compare(0, 7, "commit ") == 0) continue;
                    if (++seen[line] == 1 && b.author.empty()) b.author = line;
                }
                for (auto& [who_, count] : seen)
                    if (count > seen[b.author]) b.author = who_;
            }

            if (b.ahead <= 0 || b.pushed) continue;
            Output when = git(root, "rev-list", {"--format=%ct", s.base + ".." + name});
            for (auto& line : lines(when.text)) {
                if (line.compare(0, 7, "commit ") == 0) continue;
                const long long at = std::atoll(line.c_str());
                if (at > 0 && (b.unpushedSinceS == 0 || at < b.unpushedSinceS))
                    b.unpushedSinceS = at;
            }
        }

        Output merges = git(root, "rev-list", {"--merges", "--parents", "-n", "500", s.base});
        used.insert("rev-list");
        for (auto& line : lines(merges.text)) {
            std::istringstream parts(line);
            std::string sha;
            for (int i = 0; parts >> sha; ++i)
                if (i >= 2) s.mergedTips.insert(sha);
        }

        if (!reportsRel.empty()) {
            std::vector<std::string> args {"-r", "--name-only", s.base, "--"};
            for (const auto& rel : reportsRel) args.push_back(rel);
            Output tree = git(root, "ls-tree", args);
            used.insert("ls-tree");
            for (auto& path : lines(tree.text)) {
                const std::size_t slash = path.find_last_of('/');
                s.artefacts.insert(slash == std::string::npos ? path : path.substr(slash + 1));
            }
        }
    }

    Output trees = git(root, "worktree", {"list", "--porcelain"});
    used.insert("worktree");
    std::string path;
    for (auto& line : lines(trees.text)) {
        if (line.compare(0, 9, "worktree ") == 0) { path = line.substr(9); continue; }
        if (line.compare(0, 7, "branch ") != 0) continue;
        auto it = s.branches.find(strip(line.substr(7), HEADS));
        if (it == s.branches.end() || path.empty()) continue;
        it->second.worktree = path;
        Output status = git(path, "status", {"--porcelain"});
        used.insert("status");
        std::error_code dirtEc;
        const fs::file_status st = fs::status(fs::path(path), dirtEc);
        const bool definitelyAbsent = st.type() == fs::file_type::not_found;
        it->second.dirty =
            status.ok ? (status.text.empty() ? Branch::Dirt::Clean : Branch::Dirt::Dirty)
          : definitelyAbsent ? Branch::Dirt::Clean
                             : Branch::Dirt::Unknown;
    }

}

delivery::Probe::Snapshot delivery::Probe::scan_()
{
    Snapshot s;
    std::set<std::string> used;

    if (root_.empty()) {
        s.inFlight = inFlightStatus_;
        s.reason = "no repository is loaded, so nothing can be read from git";
        s.fingerprint = "unavailable:" + s.reason;
        return s;
    }

    Output top = git(root_, "rev-parse", {"--show-toplevel"});
    used.insert("rev-parse");
    if (!top.ok || top.text.empty()) {
        s.reason = root_ + " is not a git repository, so the tool cannot tell what became of "
                           "any of this work";
        s.inFlight = inFlightStatus_;
        s.used.assign(used.begin(), used.end());
        s.fingerprint = "unavailable:" + s.reason;
        LOG_FIRST_N(WARNING, 1) << "delivery: " << s.reason;
        return s;
    }
    s.available = true;

    scanRepo_(root_, reportsRel_, s, used);

    for (const auto& sub : subRepos_) {
        Snapshot t;
        scanRepo_(sub.root, sub.reportsRel, t, used);
        for (auto& [name, b] : t.branches) {
            if (name == t.base) continue;
            s.branches.emplace(name, b);
        }
        s.artefacts.insert(t.artefacts.begin(), t.artefacts.end());
        s.mergedTips.insert(t.mergedTips.begin(), t.mergedTips.end());
    }

    bool anyPushed = false;
    for (auto& [name, b] : s.branches)
        if (b.pushed && !b.merged && name != s.base) anyPushed = true;
    if (anyPushed && forgeUsable_) {
        s.forge = "gh pr list";
        Output prs = run(root_, {"gh", "pr", "list", "--state", "open", "--limit", "100",
                                 "--json", "number,headRefName"});
        if (prs.ok) {
            manifest::Json::Error err;
            const manifest::Json parsed = manifest::Json::parse(prs.text, err);
            if (err.ok && parsed.isArray()) {
                s.forgeAnswered = true;
                for (auto& item : parsed.items()) {
                    const manifest::Json* head = item.find("headRefName");
                    const manifest::Json* number = item.find("number");
                    if (!head || !number) continue;
                    auto it = s.branches.find(head->str());
                    if (it != s.branches.end())
                        it->second.pullRequest = static_cast<int>(number->number());
                }
            }
        }
        if (!s.forgeAnswered) {
            forgeUsable_ = false;
            LOG(WARNING) << "delivery: the forge could not be asked (" << prs.failure
                         << ") — a pushed branch will read `waiting` and say so";
        }
    }

    s.inFlight = inFlightStatus_;

    s.used.assign(used.begin(), used.end());

    std::ostringstream print;
    print << s.base << '|';
    for (auto& [name, b] : s.branches)
        print << name << ':' << b.tip << ':' << b.ahead << b.merged << b.pushed << static_cast<int>(b.dirty)
              << ':' << b.pullRequest << ':' << b.unpushedSinceS << '|';
    for (auto& a : s.artefacts) print << a << ',';
    print << '|';
    for (auto& a : s.mergedTips) print << a << ',';
    s.fingerprint = print.str();

    std::ostringstream ran;
    ran << "[";
    for (std::size_t i = 0; i < s.used.size(); ++i) ran << (i ? "," : "") << jstr(s.used[i]);
    ran << "]";
    LOG(INFO) << "delivery: pass used git " << ran.str() << " over " << s.branches.size()
              << " branch(es)";
    return s;
}

const delivery::Probe::Branch* delivery::Probe::branchFor(const Snapshot& s, const std::string& ticketId)
{
    if (ticketId.empty()) return nullptr;
    const Branch* best = nullptr;
    for (auto& [name, b] : s.branches) {
        const bool exact = name == ticketId;
        const std::string tail = "/" + ticketId;
        const bool prefixed = name.size() > tail.size()
            && name.compare(name.size() - tail.size(), tail.size(), tail) == 0;
        if (!exact && !prefixed) continue;
        if (!best) {
            best = &b;
        } else if (b.ahead != best->ahead) {
            if (b.ahead > best->ahead) best = &b;
        } else if (b.dirty == Branch::Dirt::Dirty && best->dirty != Branch::Dirt::Dirty) {
            best = &b;
        }
    }
    return best;
}

delivery::Probe::State delivery::Probe::decide(const Snapshot& s, const Subject& subject,
                                               const LiveLanes& live)
{
    State st;
    st.claimed = subject.claimedStatus;

    if (!s.available) {
        st.state = "unknown";
        st.reason = s.reason;
        return st;
    }

    const Branch* branch = branchFor(s, subject.ticketId);

    auto settle = [&st](const char* state, const std::string& detail) {
        st.state = state;
        st.detail = detail;
    };

    const auto held = subject.ticketId.empty() ? live.end() : live.find(subject.ticketId);
    const bool finishedPass = held != live.end() && held->second.finished
                              && subject.claimedStatus == "pass";
    if (held != live.end() && !finishedPass)
        settle("running", "lane " + held->second.lane + " holds it and its agent is alive");

    if (st.state == "unknown") {
        if (!subject.reportFileName.empty() && s.artefacts.count(subject.reportFileName))
            settle("done", "its report is on " + s.base);
        else if (branch && branch->merged && branch->name != s.base
                 && !branch->tip.empty() && branch->tip != s.baseTip
                 && s.mergedTips.count(branch->tip))
            settle("done", branch->name + " is merged into " + s.base);
    }

    if (st.state == "unknown" && finishedPass)
        settle("running", "lane " + held->second.lane + " holds it and its agent is alive");

    if (st.state == "unknown" && branch && branch->dirty == Branch::Dirt::Dirty)
        settle("unshipped", "uncommitted changes in " + branch->worktree);
    if (st.state == "unknown" && branch && !branch->worktree.empty()
        && branch->dirty == Branch::Dirt::Unknown)
        settle("unshipped", "the worktree at " + branch->worktree + " could not be read, so "
                            "whether it holds uncommitted work is not known");
    if (st.state == "unknown" && branch && branch->ahead > 0 && !branch->pushed)
        settle("unshipped", plural(branch->ahead, "commit", "commits") + " on " + branch->name
                            + ", on no remote");

    if (st.state == "unknown" && branch && branch->ahead > 0 && branch->pushed) {
        if (branch->pullRequest > 0)
            settle("waiting", "pull request #" + std::to_string(branch->pullRequest)
                              + " is open on " + branch->name);
        else if (s.forgeAnswered)
            settle("unshipped", branch->name + " is pushed, and no pull request is open");
        else
            settle("waiting", branch->name + " is pushed; the forge could not be asked");
    }

    if (st.state == "unknown" && !subject.claimedStatus.empty()) {
        for (auto& value : s.inFlight) {
            if (value != subject.claimedStatus) continue;
            settle("stalled", "the document says " + subject.claimedStatus
                              + ", and no process is running and nothing new is committed");
            break;
        }
    }

    if (st.state == "unknown") settle("not_started", "");

    st.needsYou = st.state == "stalled" || st.state == "unshipped";

    static const std::map<std::string, std::set<std::string>> AGREES = {
        {"todo",        {"not_started", "running", "unknown"}},
        {"in-progress", {"running", "unshipped", "unknown"}},
        {"in progress", {"running", "unshipped", "unknown"}},
        {"awaiting-qa", {"running", "unshipped", "waiting", "unknown"}},
        {"pass",        {"done", "waiting", "unknown"}},
    };
    auto agrees = AGREES.find(subject.claimedStatus);
    st.disagrees = agrees != AGREES.end() && agrees->second.count(st.state) == 0;
    return st;
}

std::string delivery::Probe::toJson(const State& state)
{
    std::ostringstream o;
    o << "{\"state\":" << jstr(state.state)
      << ",\"detail\":" << jstr(state.detail)
      << ",\"claimed\":" << jstr(state.claimed)
      << ",\"disagrees\":" << (state.disagrees ? "true" : "false")
      << ",\"needs_you\":" << (state.needsYou ? "true" : "false");
    if (!state.reason.empty()) o << ",\"reason\":" << jstr(state.reason);
    o << "}";
    return o.str();
}

std::string delivery::Probe::healthFragment(const Snapshot* snapshot)
{
    std::ostringstream o;
    o << "\"delivery\":{\"available\":" << (snapshot && snapshot->available ? "true" : "false");
    if (!snapshot)
        o << ",\"reason\":" << jstr("the first pass has not landed yet");
    else if (!snapshot->available)
        o << ",\"reason\":" << jstr(snapshot->reason);
    else
        o << ",\"base\":" << jstr(snapshot->base);
    if (snapshot && !snapshot->forge.empty()) o << ",\"forge\":" << jstr(snapshot->forge);
    o << ",\"git\":[";
    const auto& allowed = gitSubcommands();
    for (std::size_t i = 0; i < allowed.size(); ++i) o << (i ? "," : "") << jstr(allowed[i]);
    o << "]}";
    return o.str();
}
