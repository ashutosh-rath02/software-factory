#include "lander.h"

#include <filesystem>
#include <unistd.h>
#include <map>
#include <set>
#include <sstream>
#include <utility>

#include <app/manifest/store.h>
#include <glog/logging.h>

#include "git.h"

namespace fs = std::filesystem;

using land::git::Output;

static std::string joined_(const std::vector<std::string>& values, const char* sep)
{
    std::string out;
    for (const auto& v : values) {
        if (!out.empty()) out += sep;
        out += v;
    }
    return out;
}

static std::string baseNameOf_(const std::string& path)
{
    const std::size_t slash = path.find_last_of('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

static bool endsWith_(const std::string& value, const std::string& tail)
{
    return value.size() >= tail.size()
        && value.compare(value.size() - tail.size(), tail.size(), tail) == 0;
}

std::string land::Lander::trim_(const std::string& value)
{
    std::size_t from = 0, to = value.size();
    while (from < to && std::isspace(static_cast<unsigned char>(value[from]))) ++from;
    while (to > from && std::isspace(static_cast<unsigned char>(value[to - 1]))) --to;
    return value.substr(from, to - from);
}

std::string land::Lander::resolve_(const std::string& cwd, const std::string& what)
{
    const Output out = git::run(cwd, "rev-parse", {"--verify", "--quiet", what});
    return out.ok ? trim_(out.text) : std::string();
}

std::string land::Lander::statusOf_(const std::string& document)
{
    bool inFront = false;
    for (const auto& line : git::lines(document)) {
        if (line == "---") {
            if (!inFront) { inFront = true; continue; }
            break;
        }
        if (!inFront) break;
        if (line.compare(0, 7, "status:") == 0) return trim_(line.substr(7));
    }
    return {};
}

std::string land::Lander::headingOf_(const std::string& document)
{
    for (const auto& line : git::lines(document))
        if (!line.empty() && line.front() == '#') return line;
    return {};
}

land::Lander::Verdict land::Lander::verdictOf_(const std::string& heading)
{
    const bool pass = heading.find("PASS") != std::string::npos;
    const bool fail = heading.find("FAIL") != std::string::npos;
    if (pass && fail) return Verdict::Ambiguous;
    if (pass) return Verdict::Pass;
    if (fail) return Verdict::Fail;
    return Verdict::None;
}

void land::Lander::init()
{
    root_ = manifest::Store::instance().data().root;
    published_ = std::make_shared<const Refusals>();
}

void land::Lander::stop()
{
    queue_.post([this] { stopping_ = true; });
}

void land::Lander::consider(const std::vector<Request>& requests)
{
    queue_.post([this, requests] {
        if (stopping_) return;
        pending_ = requests;
        if (!busy_) runNext_();
    });
}

void land::Lander::refusalsThen(std::function<void(std::shared_ptr<const Refusals>)> done)
{
    queue_.post([this, done = std::move(done)]() mutable { done(published_); });
}

void land::Lander::publish_()
{
    published_ = std::make_shared<const Refusals>(refusals_);
}

void land::Lander::runNext_()
{
    if (stopping_ || pending_.empty()) {
        busy_ = false;
        publish_();
        return;
    }
    busy_ = true;
    const Request request = pending_.front();
    pending_.erase(pending_.begin());

    auto seen = fingerprints_.find(request.ticketId);
    const std::string previous = seen == fingerprints_.end() ? std::string() : seen->second;
    auto done = landedTips_.find(request.ticketId);
    const std::string landed = done == landedTips_.end() ? std::string() : done->second;

    queue_.offload(
        [this, request, previous, landed] { return attempt_(request, previous, landed); },
        [this](Attempt outcome) {
            if (outcome.examined) {
                fingerprints_[outcome.ticketId] = outcome.fingerprint;
                if (outcome.refusal.empty()) refusals_.erase(outcome.ticketId);
                else refusals_[outcome.ticketId] = outcome.refusal;
            }
            if (!outcome.landedTip.empty())
                landedTips_[outcome.ticketId] = outcome.landedTip;
            runNext_();
        });
}

std::string land::Lander::remoteRefFor_(const std::string& base)
{
    const Output refs = git::run(root_, "for-each-ref", {"--format=%(refname)", "refs/remotes"});
    const std::string tail = "/" + base;
    std::string first;
    for (const auto& ref : git::lines(refs.text)) {
        if (!endsWith_(ref, tail)) continue;
        if (ref == "refs/remotes/origin" + tail) return ref;
        if (first.empty()) first = ref;
    }
    return first;
}

static bool roundOf_(const std::string& name, const std::string& ticketId, std::string& number)
{
    if (name == ticketId + "-qa.md") {
        number = "1";
        return true;
    }
    const std::string head = ticketId + "-qa-round";
    if (name.size() <= head.size() + 3 || name.compare(0, head.size(), head) != 0
        || !endsWith_(name, ".md"))
        return false;
    const std::string digits = name.substr(head.size(), name.size() - head.size() - 3);
    for (char c : digits)
        if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    const std::size_t first = digits.find_first_not_of('0');
    number = first == std::string::npos ? std::string("0") : digits.substr(first);
    return true;
}

static bool laterRound_(const std::string& a, const std::string& b)
{
    return a.size() != b.size() ? a.size() > b.size() : a > b;
}

std::string land::Lander::verdictPath_(const Request& request, const std::string& ref) const
{
    if (request.reportsDir.empty() || request.ticketId.empty() || ref.empty()) return {};
    const Output tree = git::run(root_, "ls-tree",
                                 {"--name-only", ref, "--", request.reportsDir + "/"});
    if (!tree.ok) return {};
    const std::string plain = request.ticketId + "-qa.md";
    std::string best, latest;
    for (const auto& path : git::lines(tree.text)) {
        if (path != request.reportsDir + "/" + baseNameOf_(path)) continue;
        std::string number;
        if (!roundOf_(baseNameOf_(path), request.ticketId, number)) continue;
        if (best.empty() || laterRound_(number, latest)
            || (number == latest && baseNameOf_(path) == plain)) {
            best = path;
            latest = number;
        }
    }
    return best;
}

bool land::Lander::refHasVerdict_(const Request& request, const std::string& ref)
{
    return !verdictPath_(request, ref).empty();
}

std::vector<std::string> land::Lander::otherVerdictDocuments_(const Request& request,
                                                              const std::string& ref)
{
    std::vector<std::string> out;
    if (request.reportsDir.empty()) return out;
    const Output tree = git::run(root_, "ls-tree",
                                 {"-r", "--name-only", ref, "--", request.reportsDir});
    if (!tree.ok) return out;
    const std::string want = request.ticketId + "-qa";
    for (const auto& path : git::lines(tree.text)) {
        const std::string name = baseNameOf_(path);
        std::string number;
        if (path == request.reportsDir + "/" + name && roundOf_(name, request.ticketId, number))
            continue;
        if (name.compare(0, want.size(), want) != 0) continue;
        if (!endsWith_(name, ".md")) continue;
        out.push_back(path);
    }
    return out;
}

std::vector<land::Lander::Candidate> land::Lander::candidatesFor_(const Request& request,
                                                                  const std::string& branchTip)
{
    std::vector<Candidate> out {{request.branch, branchTip}};
    for (const auto& role : request.roles) {
        if (role.empty() || role == "run") continue;
        const std::string branch = "ticket/" + request.ticketId + "-" + role;
        const std::string tip = resolve_(root_, "refs/heads/" + branch);
        if (tip.empty()) continue;
        out.push_back({branch, tip});
    }
    return out;
}

land::Lander::Decision land::Lander::decideAmong_(const std::vector<Candidate>& deciding)
{
    const std::size_t n = deciding.size();
    std::vector<bool> contains(n * n, true);
    for (std::size_t i = 0; i < n; ++i)
        for (std::size_t j = 0; j < n; ++j)
            if (i != j)
                contains[i * n + j] =
                    git::run(root_, "merge-base",
                             {"--is-ancestor", deciding[j].tip, deciding[i].tip}).ok;

    for (std::size_t i = 0; i < n; ++i) {
        bool everyOther = true;
        for (std::size_t j = 0; j < n && everyOther; ++j) everyOther = contains[i * n + j];
        if (everyOther) return {static_cast<int>(i), {}};
    }
    for (std::size_t i = 0; i < n; ++i)
        for (std::size_t j = i + 1; j < n; ++j)
            if (!contains[i * n + j] && !contains[j * n + i])
                return {-1, deciding[i].branch + " (" + deciding[i].tip + ") and "
                            + deciding[j].branch + " (" + deciding[j].tip
                            + ") have diverged; merge one into the other"};
    return {-1, {}};
}

std::string land::Lander::sessionThatWroteTheVerdict_(const Request& request,
                                                      const std::string& readBranch,
                                                      const std::string& path)
{
    if (path.empty() || readBranch.empty()) return {};
    for (const auto& session : request.sessions) {
        if (session.role.empty() || session.role == "run") continue;
        if (session.tip.empty()) continue;
        if (!git::run(root_, "merge-base", {"--is-ancestor", session.tip, readBranch}).ok)
            continue;
        const Output theirs = git::run(root_, "cat-file", {"blob", session.tip + ":" + path});
        if (!theirs.ok) continue;
        if (verdictOf_(headingOf_(theirs.text)) != Verdict::Pass) continue;
        return session.runId.empty() ? session.role : session.runId;
    }
    return {};
}

bool land::Lander::ownScratchAt_(const std::string& path)
{
    const Output list = git::run(root_, "worktree", {"list", "--porcelain"});
    if (!list.ok) return false;
    std::error_code ec;
    const std::string want = fs::weakly_canonical(fs::path(path), ec).string();
    bool here = false;
    for (const auto& line : git::lines(list.text)) {
        if (line.compare(0, 9, "worktree ") == 0) {
            const std::string at = line.substr(9);
            here = at == path
                || (!want.empty() && fs::weakly_canonical(fs::path(at), ec).string() == want);
            continue;
        }
        if (here && line == "detached") return true;
        if (here && line.compare(0, 7, "branch ") == 0) return false;
    }
    return false;
}

land::Lander::Scratch land::Lander::openScratch_(const std::string& purpose,
                                                 const std::string& at)
{
    Scratch scratch;
    if (at.empty()) return scratch;
    std::string name = purpose;
    for (char& c : name) if (c == '/' || c == ' ') c = '_';
    const std::string path =
        (fs::temp_directory_path()
         / ("factory-land-" + name + "-" + std::to_string(::getpid()) + "-" + at.substr(0, 12)))
            .string();
    std::error_code ec;
    if (fs::exists(fs::path(path), ec)) {
        if (!ownScratchAt_(path)) {
            scratch.refusal = "a directory that is not one of this tool's scratch worktrees "
                              "stands at " + path + "; the tool did not make it and does not "
                              "remove it";
            return scratch;
        }
        const Output removed = git::run(root_, "worktree", {"remove", "--force", path});
        git::run(root_, "worktree", {"prune"});
        if (!removed.ok || fs::exists(fs::path(path), ec)) {
            scratch.refusal = "the scratch worktree left at " + path
                            + " would not be removed: " + git::firstLine(removed.text);
            return scratch;
        }
    }
    const Output added = git::run(root_, "worktree", {"add", "--detach", path, at});
    if (!added.ok) return scratch;
    scratch.path = path;
    scratch.head = at;
    return scratch;
}

void land::Lander::closeScratch_(const Scratch& scratch)
{
    if (scratch.path.empty()) return;
    const Output removed = git::run(root_, "worktree", {"remove", "--force", scratch.path});
    git::run(root_, "worktree", {"prune"});
    if (removed.ok) return;
    LOG(WARNING) << "land: the scratch checkout at " << scratch.path << " would not be removed: "
                 << git::firstLine(removed.text);
}

std::string land::Lander::bringBaseIn_(const Request& request, const std::string& branchTip,
                                       const std::string& commit, std::string& refusalOut)
{
    if (commit.empty()) return branchTip;
    if (git::run(root_, "merge-base", {"--is-ancestor", commit, branchTip}).ok) return branchTip;

    const Scratch scratch = openScratch_(request.ticketId + "-merge", branchTip);
    if (scratch.path.empty()) {
        refusalOut = scratch.refusal.empty()
            ? "no scratch checkout could be made to merge " + request.base + " into "
              + request.branch
            : scratch.refusal;
        LOG(WARNING) << "land: " << request.ticketId << " — " << refusalOut;
        return {};
    }

    const Output merged = git::run(scratch.path, "merge",
                                   {"--no-edit", "--no-verify", commit},
                                   {"user.name=factory", "user.email=factory@localhost"});
    if (merged.ok) {
        const std::string mergedTip = resolve_(scratch.path, "HEAD");
        closeScratch_(scratch);
        return mergedTip;
    }

    const Output unmerged = git::run(scratch.path, "diff", {"--name-only", "--diff-filter=U"});
    const std::string names = joined_(git::lines(unmerged.text), ", ");
    closeScratch_(scratch);

    refusalOut = request.base + " will not merge into " + request.branch;
    if (!names.empty()) refusalOut += ": " + names;
    LOG(INFO) << "land: " << request.ticketId << " — " << refusalOut;
    return {};
}

bool land::Lander::isVerdictPath_(const Request& request, const std::string& path)
{
    if (!request.ticketPath.empty() && path == request.ticketPath) return true;
    if (request.ticketId.empty()) return false;
    const std::string name = baseNameOf_(path);
    const std::string want = request.ticketId + "-qa";
    return name.compare(0, want.size(), want) == 0;
}

std::vector<land::Lander::Written> land::Lander::workOn_(const Request& request,
                                                         const std::string& mergedTip)
{
    const Output log = git::run(root_, "log",
                                {"--no-merges", "--reverse", "--format=%x01%an <%ae>",
                                 "--name-only", request.base + ".." + mergedTip});
    std::vector<Written> all;
    for (const auto& line : git::lines(log.text)) {
        if (!line.empty() && line[0] == '\x01') {
            all.push_back({line.substr(1), {}});
            continue;
        }
        if (line.empty() || all.empty()) continue;
        all.back().paths.push_back(line);
    }

    std::vector<Written> work;
    for (const auto& one : all) {
        bool verdict = !one.paths.empty();
        for (const auto& path : one.paths)
            if (!isVerdictPath_(request, path)) { verdict = false; break; }
        if (!verdict) work.push_back(one);
    }
    return work.empty() ? all : work;
}

std::string land::Lander::authorOf_(const Request& request, const std::string& mergedTip,
                                    const std::vector<std::string>& everyone)
{
    const std::vector<Written> work = workOn_(request, mergedTip);
    std::map<std::string, int> counted;
    std::map<std::string, std::size_t> first;
    for (std::size_t i = 0; i < work.size(); ++i) {
        const std::string& name = work[i].author;
        if (counted[name]++ == 0) first[name] = i;
    }
    std::string author;
    for (const auto& [name, count] : counted) {
        if (author.empty()) { author = name; continue; }
        if (count > counted[author]
            || (count == counted[author] && first[name] < first[author]))
            author = name;
    }
    if (author.empty() && !everyone.empty()) author = everyone.front();
    return author;
}

std::string land::Lander::squash_(const Request& request, const std::string& baseTip,
                                  const std::string& mergedTip, std::string& refusalOut)
{
    const Scratch scratch = openScratch_(request.ticketId + "-squash", baseTip);
    if (scratch.path.empty()) {
        refusalOut = scratch.refusal.empty()
            ? "no scratch checkout of " + request.base + " could be made to squash "
              + request.branch + " onto"
            : scratch.refusal;
        LOG(WARNING) << "land: " << request.ticketId << " — " << refusalOut;
        return {};
    }

    const Output squashed = git::run(scratch.path, "merge", {"--squash", mergedTip});
    if (!squashed.ok) {
        const Output unmerged = git::run(scratch.path, "diff", {"--name-only", "--diff-filter=U"});
        const std::string names = joined_(git::lines(unmerged.text), ", ");
        closeScratch_(scratch);
        refusalOut = request.branch + " will not squash onto " + request.base;
        if (!names.empty()) refusalOut += ": " + names;
        LOG(INFO) << "land: " << request.ticketId << " — " << refusalOut;
        return {};
    }
    if (git::run(scratch.path, "status", {"--porcelain"}).text.empty()) {
        closeScratch_(scratch);
        return {};
    }

    const Output who = git::run(root_, "log",
                                {"--no-merges", "--format=%an <%ae>",
                                 request.base + ".." + mergedTip});
    std::vector<std::string> order;
    std::map<std::string, int> counted;
    for (const auto& line : git::lines(who.text))
        if (counted[line]++ == 0) order.push_back(line);
    const std::string author = authorOf_(request, mergedTip, order);

    const Output subjects = git::run(root_, "log",
                                     {"--no-merges", "--reverse", "--format=%s",
                                      request.base + ".." + mergedTip});
    std::ostringstream message;
    message << request.ticketId << ": "
            << (request.title.empty() ? std::string("the work on ") + request.branch
                                      : request.title)
            << "\n";
    const std::vector<std::string> rounds = git::lines(subjects.text);
    if (!rounds.empty()) message << "\n" << joined_(rounds, "\n") << "\n";
    std::string trailers;
    for (const auto& name : order)
        if (name != author) trailers += "Co-Authored-By: " + name + "\n";
    if (!trailers.empty()) message << "\n" << trailers;

    std::vector<std::string> args {"--no-verify", "--no-gpg-sign"};
    if (!author.empty()) args.push_back("--author=" + author);
    args.push_back("-m");
    args.push_back(message.str());
    const Output made = git::run(scratch.path, "commit", args,
                                 {"user.name=factory", "user.email=factory@localhost"});
    if (!made.ok) {
        closeScratch_(scratch);
        refusalOut = "the squash of " + request.branch + " onto " + request.base
                   + " would not commit: " + git::firstLine(made.text);
        LOG(WARNING) << "land: " << request.ticketId << " — " << refusalOut;
        return {};
    }
    const std::string landed = resolve_(scratch.path, "HEAD");
    closeScratch_(scratch);
    return landed;
}

land::Lander::Holder land::Lander::holderOf_(const std::string& branch)
{
    Holder holder;
    const Output list = git::run(root_, "worktree", {"list", "--porcelain"});
    if (!list.ok) return holder;
    holder.known = true;
    const std::string want = "branch refs/heads/" + branch;
    std::string at;
    for (const auto& line : git::lines(list.text)) {
        if (line.compare(0, 9, "worktree ") == 0) { at = line.substr(9); continue; }
        if (line == want && !at.empty()) { holder.path = at; return holder; }
    }
    return holder;
}

std::vector<std::string> land::Lander::pathsInTheWay_(const std::string& at,
                                                      const std::string& from,
                                                      const std::string& to)
{
    std::vector<std::string> blocking;
    const Output changing = git::run(at, "diff", {"--name-only", from, to},
                                     {"core.quotePath=false"});
    if (!changing.ok) return {"?"};
    std::set<std::string> touched;
    for (const auto& path : git::lines(changing.text)) touched.insert(path);
    if (touched.empty()) return blocking;

    const Output local = git::run(at, "status",
                                  {"--porcelain", "--untracked-files=all", "--ignored=matching"},
                                  {"core.quotePath=false"});
    if (!local.ok) return {"?"};
    for (const auto& line : git::lines(local.text)) {
        if (line.size() < 4) continue;
        const std::string path = line.substr(3);
        if (touched.count(path)) blocking.push_back(path);
    }
    return blocking;
}

std::string land::Lander::moveBase_(const Request& request, const std::string& baseTip,
                                    const std::string& landed, const Holder& holder)
{
    if (!holder.known)
        return "the worktrees of " + root_ + " could not be listed, so whether anything has "
             + request.base + " checked out is not known";

    if (holder.path.empty()) {
        const Output moved = git::run(root_, "update-ref",
                                      {"refs/heads/" + request.base, landed, baseTip});
        if (moved.ok) return {};
        return request.base + " moved while " + request.branch + " was being landed, so nothing "
               "was written to it";
    }

    const Output forward = git::run(holder.path, "merge", {"--ff-only", landed});
    if (forward.ok) return {};

    std::vector<std::string> named;
    for (const auto& line : git::lines(forward.text))
        if (!line.empty() && line.front() == '\t') named.push_back(trim_(line));
    std::string why = "the checkout at " + holder.path + " holds " + request.base
                    + " and git will not fast-forward it";
    if (!named.empty()) why += ": " + joined_(named, ", ");
    else why += ": " + git::firstLine(forward.text);
    return why;
}

std::string land::Lander::adoptedFromRemote_(const Request& request, const std::string& baseTip,
                                             const std::string& remoteTip)
{
    if (remoteTip.empty() || remoteTip == baseTip) return {};
    if (!git::run(root_, "merge-base", {"--is-ancestor", baseTip, remoteTip}).ok) return {};
    LOG(INFO) << "land: " << request.ticketId << " found " << request.base
              << " already carried forward on the remote as " << remoteTip
              << "; catching up rather than landing a second copy";
    return remoteTip;
}

void land::Lander::reap_(const Request& request, const std::string& mergedTip)
{
    const std::string landedTree = resolve_(root_, mergedTip + "^{tree}");
    const std::string baseTree = resolve_(root_, request.base + "^{tree}");
    if (landedTree.empty() || landedTree != baseTree
        || !git::run(root_, "merge-base",
                     {"--is-ancestor", request.branch, mergedTip}).ok) {
        LOG(INFO) << "land: " << request.ticketId << " kept " << request.branch
                  << " — its tree is not the tree on " << request.base;
        return;
    }

    const std::string worktree = request.worktree;
    if (!worktree.empty() && !request.worktreeIsALane) {
        LOG(INFO) << "land: " << request.ticketId << " kept the checkout at " << worktree
                  << " — no lane record names it, so the tool did not make it";
        LOG(INFO) << "land: " << request.ticketId << " kept " << request.branch
                  << " — its checkout is still there";
        return;
    }
    if (!worktree.empty()) {
        if (!request.checkoutIsIdle) {
            LOG(INFO) << "land: " << request.ticketId << " kept the checkout at " << worktree
                      << " — a lane process is live in it";
            LOG(INFO) << "land: " << request.ticketId << " kept " << request.branch
                      << " — its checkout is still there";
            return;
        }
        const Output dirt = git::run(worktree, "status",
                                     {"--porcelain", "--untracked-files=all"});
        const std::vector<std::string> changes = git::lines(dirt.text);
        if (!dirt.ok || !changes.empty()) {
            LOG(INFO) << "land: " << request.ticketId << " kept the checkout at " << worktree
                      << " — it holds " << changes.size() << " uncommitted change(s)";
            LOG(INFO) << "land: " << request.ticketId << " kept " << request.branch
                      << " — its checkout is still there";
            return;
        }
        const Output ignored = git::run(worktree, "status",
                                        {"--porcelain", "--ignored=matching"});
        std::vector<std::string> held;
        for (const auto& line : git::lines(ignored.text))
            if (line.compare(0, 3, "!! ") == 0) held.push_back(line.substr(3));
        if (!ignored.ok || !held.empty()) {
            LOG(INFO) << "land: " << request.ticketId << " kept the checkout at " << worktree
                      << " — it holds ignored content: " << joined_(held, ", ");
            LOG(INFO) << "land: " << request.ticketId << " kept " << request.branch
                      << " — its checkout is still there";
            return;
        }
        const Output removed = git::run(root_, "worktree", {"remove", worktree});
        if (!removed.ok) {
            LOG(INFO) << "land: " << request.ticketId << " kept the checkout at " << worktree
                      << " — git would not remove it: " << git::firstLine(removed.text);
            return;
        }
    }

    const Output deleted = git::run(root_, "branch", {"-D", request.branch});
    if (deleted.ok)
        LOG(INFO) << "land: " << request.ticketId << " reaped " << request.branch;
    else
        LOG(INFO) << "land: " << request.ticketId << " kept " << request.branch << " — "
                  << git::firstLine(deleted.text);
}

land::Lander::Attempt land::Lander::attempt_(const Request& request, const std::string& previous,
                                             const std::string& landedAt)
{
    Attempt attempt;
    attempt.ticketId = request.ticketId;
    if (root_.empty() || request.branch.empty() || request.base.empty()) return attempt;
    if (request.branch == request.base) return attempt;

    const std::string branchTip = resolve_(root_, request.branch);
    const std::string baseTip = resolve_(root_, request.base);
    if (branchTip.empty() || baseTip.empty()) return attempt;

    const std::vector<Candidate> candidates = candidatesFor_(request, branchTip);
    const std::string remoteRef = remoteRefFor_(request.base);
    const std::string remoteTip = remoteRef.empty() ? std::string()
                                                    : resolve_(root_, remoteRef);
    for (const auto& candidate : candidates)
        attempt.fingerprint += candidate.branch + "@" + candidate.tip + "|";
    attempt.fingerprint += baseTip + "|" + remoteTip;
    std::set<std::string> receipts;
    for (const auto& session : request.sessions) {
        std::string receipt;
        for (const auto* field : {&session.runId, &session.role, &session.branch, &session.tip})
            receipt += std::to_string(field->size()) + ":" + *field;
        receipts.insert(std::move(receipt));
    }
    for (const auto& receipt : receipts)
        attempt.fingerprint += "|" + receipt;
    for (const auto& role : std::set<std::string>(request.roles.begin(), request.roles.end()))
        attempt.fingerprint += "|role:" + std::to_string(role.size()) + ":" + role;
    attempt.fingerprint += request.checkoutIsIdle ? "|idle" : "|active";
    if (attempt.fingerprint == previous) return attempt;

    std::vector<Candidate> deciding {candidates.front()};
    for (std::size_t i = 1; i < candidates.size(); ++i)
        if (refHasVerdict_(request, candidates[i].branch)) deciding.push_back(candidates[i]);
    const Decision decision = decideAmong_(deciding);
    const bool decided = decision.chosen >= 0;
    const std::string readBranch = decided ? deciding[decision.chosen].branch : std::string();
    const std::string readTip = decided ? deciding[decision.chosen].tip : std::string();

    if (decided && !landedAt.empty() && readTip == landedAt) return attempt;
    attempt.examined = true;

    if (!decided) {
        LOG(INFO) << "land: " << request.ticketId << " — " << decision.divergence;
        return attempt;
    }

    const std::string verdictPath = verdictPath_(request, readBranch);
    if (verdictPath.empty()) {
        const std::vector<std::string> others = otherVerdictDocuments_(request, readBranch);
        if (others.empty())
            LOG(INFO) << "land: " << request.ticketId << " — no QA verdict on " << readBranch;
        else
            LOG(INFO) << "land: " << request.ticketId << " — no QA verdict on " << readBranch
                      << " (" << request.ticketId << "-qa.md or " << request.ticketId
                      << "-qa-round<N>.md), and these do not stand in for one: "
                      << joined_(others, ", ");
        return attempt;
    }
    const Output verdict = git::run(root_, "cat-file",
                                    {"blob", readBranch + ":" + verdictPath});
    const std::string heading = headingOf_(verdict.text);
    const Verdict says = verdictOf_(heading);
    if (says == Verdict::Ambiguous) {
        LOG(INFO) << "land: " << request.ticketId << " — the verdict at " << verdictPath << " on "
                  << readBranch << " names both PASS and FAIL in its heading, so it decides "
                  << "nothing: " << heading;
        return attempt;
    }
    if (says != Verdict::Pass) {
        LOG(INFO) << "land: " << request.ticketId << " — the verdict at " << verdictPath
                  << " on " << readBranch << " does not say PASS";
        return attempt;
    }
    const Output ticket = git::run(root_, "cat-file",
                                   {"blob", readBranch + ":" + request.ticketPath});
    if (statusOf_(ticket.text) != "pass") {
        LOG(INFO) << "land: " << request.ticketId << " — " << request.ticketPath << " on "
                  << readBranch << " does not say status: pass";
        return attempt;
    }

    if (request.roles.empty()) {
        attempt.refusal = "no role session recorded for " + request.ticketId
                        + "; a QA done outside the tool is landed by a person";
        LOG(INFO) << "land: " << request.ticketId << " — " << attempt.refusal;
        return attempt;
    }
    if (sessionThatWroteTheVerdict_(request, readBranch, verdictPath).empty()) {
        attempt.refusal = "no session recorded for " + request.ticketId + " ("
                        + joined_(request.roles, ", ") + ") left the verdict this landing would "
                          "rest on; a ref that carries a document is not a session";
        LOG(INFO) << "land: " << request.ticketId << " — " << attempt.refusal;
        return attempt;
    }

    const Holder holder = holderOf_(request.base);
    if (!holder.known) {
        attempt.refusal = "the worktrees of " + root_ + " could not be listed, so whether anything "
                          "has " + request.base + " checked out is not known";
        LOG(WARNING) << "land: " << request.ticketId << " — " << attempt.refusal;
        return attempt;
    }

    const std::string caughtUp = adoptedFromRemote_(request, baseTip, remoteTip);
    const std::string from = caughtUp.empty() ? baseTip : caughtUp;

    std::string mergedTip;
    std::string landed;
    if (caughtUp.empty()) {
        mergedTip = bringBaseIn_(request, readTip,
                                 remoteRef.empty() ? baseTip : remoteTip, attempt.refusal);
        if (mergedTip.empty()) return attempt;
        landed = squash_(request, from, mergedTip, attempt.refusal);
        if (!attempt.refusal.empty()) return attempt;
    } else {
        mergedTip = readTip;
        landed = caughtUp;
    }

    if (!landed.empty() && !holder.path.empty()) {
        const std::vector<std::string> blocking = pathsInTheWay_(holder.path, from, landed);
        if (!blocking.empty()) {
            attempt.refusal = "the checkout at " + holder.path + " holds " + request.base
                            + " and is mid-edit on " + joined_(blocking, ", ")
                            + ", which this landing changes — commit or set that aside and it "
                              "lands on its own";
            LOG(INFO) << "land: " << request.ticketId << " — " << attempt.refusal;
            return attempt;
        }
    }

    if (!remoteRef.empty() && !landed.empty() && caughtUp.empty()) {
        std::string remote = remoteRef.substr(std::string("refs/remotes/").size());
        remote = remote.substr(0, remote.size() - request.base.size() - 1);
        const Output pushed = git::run(root_, "push",
                                       {remote, landed + ":refs/heads/" + request.base});
        if (!pushed.ok) {
            attempt.refusal = "the push of " + request.branch + " to " + remote + " failed, so "
                              "nothing was moved here either: " + git::firstLine(pushed.text);
            LOG(WARNING) << "land: " << request.ticketId << " — " << attempt.refusal;
            return attempt;
        }
    }

    if (!landed.empty()) {
        attempt.refusal = moveBase_(request, baseTip, landed, holder);
        if (!attempt.refusal.empty()) {
            LOG(INFO) << "land: " << request.ticketId << " — " << attempt.refusal;
            return attempt;
        }
    }

    LOG(INFO) << "land: " << request.ticketId << " landed " << request.branch << " on "
              << request.base << " as "
              << (landed.empty() ? std::string("(its work was already there)") : landed);

    attempt.landedTip = readTip;
    reap_(request, mergedTip);
    return attempt;
}
