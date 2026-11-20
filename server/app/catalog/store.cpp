#include "store.h"

#include <sys/stat.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <set>
#include <sstream>

#include <glog/logging.h>

#include <app/delivery/git.h>
#include <app/manifest/store.h>

bool catalog::Store::readFile(const std::string& path, std::string& out)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::ostringstream ss;
    ss << in.rdbuf();
    out = ss.str();
    return true;
}

long long catalog::Store::modifiedAt(const std::string& path)
{
    struct stat st {};
    if (::stat(path.c_str(), &st) != 0) return 0;
    return static_cast<long long>(st.st_mtime);
}

bool catalog::Store::isDocument(const std::string& path)
{
    const std::string name = std::filesystem::path(path).filename().string();
    if (name.size() <= 3 || name.compare(name.size() - 3, 3, SUFFIX) != 0) return false;
    if (name[0] == '.' || name[0] == '_') return false;
    return name != "README.md";
}

std::string catalog::Store::ticketOfReport(const std::string& stem)
{
    const std::size_t n = std::string(REPORT_SUFFIX).size();
    if (stem.size() > n && stem.compare(stem.size() - n, n, REPORT_SUFFIX) == 0)
        return stem.substr(0, stem.size() - n);
    return stem;
}

const char* catalog::Store::kindName(Kind kind)
{
    switch (kind) {
        case Kind::Requirement: return "epic";
        case Kind::Ticket:      return "ticket";
        case Kind::Report:      return "report";
    }
    return "ticket";
}

void catalog::Store::init()
{
    const auto& m = manifest::Store::instance().data();
    root_ = m.root;
    for (const auto& repo : m.repos)
        roots_.push_back({repo.docs.epicsAbs, repo.docs.ticketsAbs, repo.docs.reportsAbs});
    ledgers_ = m.ledgers();

    if (roots_.empty() || roots_.front().epics.empty()) {
        LOG(WARNING) << "catalog has no directories to read — the manifest did not load";
        return;
    }
    for (const auto& r : roots_) {
        LOG(INFO) << "catalog reading " << r.epics << ", " << r.tickets << ", " << r.reports;
        watch_(r.epics, Kind::Requirement, RequirementWatch_);
        watch_(r.tickets, Kind::Ticket, TicketWatch_);
        watch_(r.reports, Kind::Report, ReportWatch_);
    }
}

void catalog::Store::stop()
{
    RequirementWatch_.stop();
    TicketWatch_.stop();
    ReportWatch_.stop();
}

void catalog::Store::onChange(ChangeHandler handler)
{
    queue_.post([this, handler = std::move(handler)]() mutable {
        handlers_.push_back(std::move(handler));
    });
}

bool catalog::Store::insideRepository_(const std::string& path) const
{
    if (root_.empty() || path.empty()) return false;
    std::error_code ec;
    const std::string abs = std::filesystem::weakly_canonical(std::filesystem::path(path), ec).string();
    if (ec) return false;
    return abs == root_ || abs.rfind(root_ + "/", 0) == 0;
}

bool catalog::Store::safeId_(const std::string& id)
{
    if (id.empty() || id == "." || id == "..") return false;
    return id.find('/') == std::string::npos && id.find('\\') == std::string::npos
        && id.find("..") == std::string::npos;
}

catalog::Store::Listing catalog::Store::read_(const std::string& dir) const
{
    Listing out;
    out.path = dir;
    std::error_code ec;
    if (dir.empty() || !std::filesystem::is_directory(std::filesystem::path(dir), ec)) return out;
    out.directoryExists = true;

    std::vector<std::filesystem::path> files;
    for (auto& entry : std::filesystem::directory_iterator(std::filesystem::path(dir), ec)) {
        if (ec) break;
        if (!entry.is_regular_file(ec)) continue;
        if (!isDocument(entry.path().string())) continue;
        if (!insideRepository_(entry.path().string())) {
            LOG(WARNING) << "ignoring " << entry.path().string()
                         << ": it resolves outside the repository this tool was pointed at";
            continue;
        }
        files.push_back(entry.path());
    }
    std::sort(files.begin(), files.end());

    for (auto& f : files) {
        std::string text;
        if (!readFile(f.string(), text)) {
            Document d;
            d.path = f.string();
            d.id = f.stem().string();
            d.title = d.id;
            d.parseError = "the file could not be read";
            out.documents.push_back(std::move(d));
            continue;
        }
        Document d = parseDocument(f.string(), f.stem().string(), text);
        d.mtime = modifiedAt(f.string());
        out.documents.push_back(std::move(d));
    }
    return out;
}

catalog::Store::Listing catalog::Store::readAll_(std::string Roots::*which) const
{
    Listing out;
    for (std::size_t i = 0; i < roots_.size(); ++i) {
        Listing one = read_(roots_[i].*which);
        if (out.path.empty()) out.path = one.path;
        out.directoryExists = out.directoryExists || one.directoryExists;
        for (auto& d : one.documents) {
            d.repo = static_cast<int>(i);
            out.documents.push_back(std::move(d));
        }
    }
    return out;
}

catalog::Store::Listing catalog::Store::requirements() const { return readAll_(&Roots::epics); }
catalog::Store::Listing catalog::Store::tickets() const { return readAll_(&Roots::tickets); }

bool catalog::Store::splitTicketId(const std::string& id, std::string& prefix, long long& number)
{
    const std::size_t dash = id.rfind('-');
    if (dash == std::string::npos || dash == 0 || dash + 1 >= id.size()) return false;

    const std::string tail = id.substr(dash + 1);
    if (tail.size() > 9) return false;
    for (unsigned char c : tail) if (c < '0' || c > '9') return false;

    const std::string head = id.substr(0, dash);
    std::size_t at = 0;
    while (at < head.size()) {
        const std::size_t next = head.find('-', at);
        const std::string segment = head.substr(at, next == std::string::npos
                                                    ? std::string::npos : next - at);
        if (segment.empty() || segment[0] < 'A' || segment[0] > 'Z') return false;
        for (unsigned char c : segment)
            if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) return false;
        if (next == std::string::npos) break;
        at = next + 1;
    }

    prefix = head;
    number = std::stoll(tail);
    return true;
}

std::string catalog::Store::seriesOf(const std::string& prefix)
{
    const std::size_t dash = prefix.rfind('-');
    return dash == std::string::npos ? std::string() : prefix.substr(0, dash);
}

catalog::Store::IdSurvey catalog::Store::surveyIds() const
{
    IdSurvey out;
    std::set<std::string> prefixes;

    const auto note = [&](const std::string& id) {
        std::string prefix;
        long long number = 0;
        if (!splitTicketId(id, prefix, number)) return;
        prefixes.insert(prefix);
        long long& highest = out.highest[seriesOf(prefix)];
        if (number > highest) highest = number;
    };

    for (const auto& d : tickets().documents) note(d.id);

    for (const auto& d : requirements().documents) note(d.id);

    for (const auto& id : idsOnEveryRef()) note(id);

    out.prefixes.assign(prefixes.begin(), prefixes.end());
    return out;
}

std::vector<std::string> catalog::Store::idsOnEveryRef() const
{
    std::vector<std::string> found;
    if (root_.empty() || roots_.empty()) return found;

    const delivery::Output refs =
        delivery::git(root_, "for-each-ref", {"--format=%(refname)"});
    if (!refs.ok) return found;                    // no git, no refs: the working tree still counts

    std::vector<std::string> relatives;
    for (const auto& r : roots_) {
        if (r.tickets.size() > root_.size() && r.tickets.compare(0, root_.size(), root_) == 0) {
            std::string rel = r.tickets.substr(root_.size());
            while (!rel.empty() && rel.front() == '/') rel.erase(rel.begin());
            if (!rel.empty()) relatives.push_back(rel);
        }
    }
    if (relatives.empty()) return found;

    std::istringstream lines(refs.text);
    std::string ref;
    while (std::getline(lines, ref)) {
        if (ref.empty()) continue;
        std::vector<std::string> args{"-r", "--name-only", ref, "--"};
        args.insert(args.end(), relatives.begin(), relatives.end());
        const delivery::Output listing = delivery::git(root_, "ls-tree", args);
        if (!listing.ok) continue;

        std::istringstream paths(listing.text);
        std::string path;
        while (std::getline(paths, path)) {
            const std::size_t slash = path.rfind('/');
            std::string name = slash == std::string::npos ? path : path.substr(slash + 1);
            if (name.size() < 4 || name.compare(name.size() - 3, 3, ".md") != 0) continue;
            name.erase(name.size() - 3);
            std::string id;
            int fields = 0;
            std::size_t at = 0;
            while (at < name.size()) {
                const std::size_t dash = name.find('-', at);
                const std::string field = name.substr(at, dash == std::string::npos
                                                          ? std::string::npos : dash - at);
                if (field.empty()) break;
                const bool numeric = field.find_first_not_of("0123456789") == std::string::npos;
                id = id.empty() ? field : id + "-" + field;
                ++fields;
                if (numeric && fields > 1) break;       // the number ends the id
                if (dash == std::string::npos) break;
                at = dash + 1;
            }
            if (!id.empty()) found.push_back(id);
        }
    }
    return found;
}
const std::string& catalog::Store::requirementsPath() const
{
    static const std::string none;
    return roots_.empty() ? none : roots_.front().epics;
}

const std::string& catalog::Store::ticketsPath() const
{
    static const std::string none;
    return roots_.empty() ? none : roots_.front().tickets;
}

const std::string& catalog::Store::reportsPath() const
{
    static const std::string none;
    return roots_.empty() ? none : roots_.front().reports;
}

std::vector<std::string> catalog::Store::documentPaths() const
{
    std::vector<std::string> all;
    for (const auto& r : roots_)
        for (const std::string* d : {&r.epics, &r.tickets, &r.reports})
            if (!d->empty()) all.push_back(*d);
    return all;
}

catalog::Store::Lookup catalog::Store::findTicket(const std::string& id, Document& out,
                                           std::vector<std::string>& paths) const
{
    paths.clear();
    const Document* first = nullptr;
    Listing all = tickets();
    for (auto& d : all.documents) {
        if (d.id != id) continue;
        if (!first) first = &d;
        paths.push_back(d.path);
    }
    if (paths.empty()) return Lookup::Missing;
    if (paths.size() > 1) return Lookup::Ambiguous;
    out = *first;
    paths.clear();
    return Lookup::Found;
}

bool catalog::Store::ticketById(const std::string& id, Document& out) const
{
    std::vector<std::string> paths;
    return findTicket(id, out, paths) == Lookup::Found;
}

bool catalog::Store::requirementById(const std::string& id, Document& out) const
{
    for (auto& d : requirements().documents)
        if (d.id == id) { out = d; return true; }
    return false;
}

std::string catalog::Store::reportPathFor(const Document& ticket) const
{
    std::string reports = reportsPath();
    if (!ticket.path.empty()) {
        const std::string dir = std::filesystem::path(ticket.path).parent_path().string();
        for (const auto& r : roots_)
            if (!r.tickets.empty() && r.tickets == dir) { reports = r.reports; break; }
    }
    if (reports.empty()) return "";
    if (!safeId_(ticket.id)) return reports;
    return (std::filesystem::path(reports) / (ticket.id + REPORT_SUFFIX + SUFFIX)).string();
}

std::string catalog::Store::reportPathFor(const std::string& ticketId) const
{
    if (!safeId_(ticketId)) return reportsPath();
    Document ticket;
    std::vector<std::string> paths;
    if (findTicket(ticketId, ticket, paths) != Lookup::Found) {
        if (reportsPath().empty()) return "";
        return (std::filesystem::path(reportsPath()) / (ticketId + REPORT_SUFFIX + SUFFIX)).string();
    }
    return reportPathFor(ticket);
}

bool catalog::Store::readReport(const std::string& ticketId, Document& out) const
{
    if (!safeId_(ticketId)) return false;
    const std::string path = reportPathFor(ticketId);
    if (path.empty() || !insideRepository_(path)) return false;
    std::error_code ec;
    if (!std::filesystem::is_regular_file(std::filesystem::path(path), ec)) return false;
    std::string text;
    if (!readFile(path, text)) return false;
    out = parseDocument(path, ticketId, text);
    out.mtime = modifiedAt(path);
    return true;
}

std::vector<std::string> catalog::Store::runIds() const
{
    std::vector<std::string> ids;
    for (const auto& dir : ledgers_) {
        std::error_code ec;
        if (dir.empty() || !std::filesystem::is_directory(std::filesystem::path(dir), ec)) continue;
        for (auto& entry : std::filesystem::directory_iterator(std::filesystem::path(dir), ec)) {
            if (ec) break;
            if (entry.is_directory(ec)) ids.push_back(entry.path().filename().string());
        }
    }
    std::sort(ids.begin(), ids.end());
    ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    return ids;
}

std::string catalog::Store::evidenceAt_(const std::string& reportPath,
                                        const std::vector<std::string>& runs,
                                        std::string& runIdOut) const
{
    runIdOut.clear();
    std::error_code ec;
    if (reportPath.empty() || !insideRepository_(reportPath)
        || !std::filesystem::is_regular_file(std::filesystem::path(reportPath), ec)) return "nothing_claimed";
    std::string text;
    if (!readFile(reportPath, text)) return "nothing_claimed";
    for (auto& id : runs) {
        if (!id.empty() && text.find(id) != std::string::npos) {
            runIdOut = id;
            return "claimed_proven";
        }
    }
    return "claimed_unproven";
}

std::string catalog::Store::evidenceFor(const std::string& ticketId,
                                        const std::vector<std::string>& runs,
                                        std::string& runIdOut) const
{
    if (!safeId_(ticketId)) { runIdOut.clear(); return "nothing_claimed"; }
    return evidenceAt_(reportPathFor(ticketId), runs, runIdOut);
}

std::string catalog::Store::evidenceFor(const Document& ticket,
                                        const std::vector<std::string>& runs,
                                        std::string& runIdOut) const
{
    if (!safeId_(ticket.id)) { runIdOut.clear(); return "nothing_claimed"; }
    return evidenceAt_(reportPathFor(ticket), runs, runIdOut);
}

void catalog::Store::watch_(const std::string& dir, Kind kind, file::Watcher& watcher)
{
    if (dir.empty()) return;
    watcher.watch(
        dir, queue_,
        [this](const std::string& path) {
            return isDocument(path) && insideRepository_(path);
        },
        [kind](const std::string& path) {
            const std::filesystem::path p(path);
            if (kind == Kind::Report) return ticketOfReport(p.stem().string());
            std::string text;
            if (!readFile(path, text)) return p.stem().string();
            return parseDocument(path, p.stem().string(), text).id;
        });
}

void catalog::Store::report_(Kind kind, const file::Change& change)
{
    Change out;
    out.kind = kind;
    out.id = change.tag;
    out.path = change.path;
    out.change = file::name(change.kind);
    for (auto& h : handlers_) h(out);
}

FILE_HANDLER(catalog::Store, Requirement) { report_(Kind::Requirement, change); }
FILE_HANDLER(catalog::Store, Ticket) { report_(Kind::Ticket, change); }
FILE_HANDLER(catalog::Store, Report) { report_(Kind::Report, change); }
