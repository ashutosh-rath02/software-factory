#include "render.h"
#include <regex>

#include <algorithm>
#include <cctype>
#include <ctime>
#include <iomanip>
#include <filesystem>
#include <map>
#include <set>
#include <fstream>
#include <sstream>
#include <vector>

#include <app/error/remedy.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>

#include "store.h"

using manifest::jsonEscape;

namespace {

const char* UNASSIGNED = "unassigned";

const char* STANDALONE = "standalone";
const char* STANDALONE_TITLE = "Standalone";
const char* STANDALONE_SUMMARY =
    "Tickets whose parent names no requirement in this repository, grouped by workspace.";

using manifest::jstr;
std::string money(double v)
{
    if (v == static_cast<long long>(v)) return std::to_string(static_cast<long long>(v));
    std::ostringstream o;
    o << std::setprecision(12) << v;
    return o.str();
}

std::string isoTime(long long epochSeconds)
{
    if (epochSeconds <= 0) return "";
    std::time_t t = static_cast<std::time_t>(epochSeconds);
    std::tm tm {};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

std::string lowered(const std::string& s)
{
    std::string out = s;
    for (auto& c : out) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return out;
}

bool contains(const std::string& haystack, const std::string& needle)
{
    return lowered(haystack).find(lowered(needle)) != std::string::npos;
}

std::string errorJson(const std::string& code, const std::string& message,
                      const std::string& detailKey, const std::string& detailValue,
                      const std::string& remedyJson = std::string())
{
    std::ostringstream o;
    o << "{\"error\":{\"code\":" << jstr(code)
      << ",\"message\":" << jstr(message)
      << ",\"detail\":{" << jstr(detailKey) << ":" << jstr(detailValue) << "}"
      << remedy::member(remedyJson) << "}}";
    return o.str();
}

void writeStrings(std::ostringstream& o, const std::vector<std::string>& v)
{
    o << "[";
    for (std::size_t i = 0; i < v.size(); ++i) {
        if (i) o << ",";
        o << jstr(v[i]);
    }
    o << "]";
}

std::string processOf(const std::string& lastOutcome)
{
    if (lastOutcome == "ended cleanly" || lastOutcome == "completed") return "finished";
    if (lastOutcome == "killed by you") return "killed";
    if (lastOutcome == "crashed") return "crashed";
    if (lastOutcome == "orphaned") return "crashed";
    if (!lastOutcome.empty()) return "crashed";
    return "not_started";
}

void writeState(std::ostringstream& o, const std::string& evidence,
                const std::string& lastOutcome = std::string(),
                const catalog::LaneState& live = {})
{
    const bool liveWins = !live.process.empty() && live.process != "not_started";
    o << "{\"process\":" << jstr(liveWins ? live.process : processOf(lastOutcome))
      << ",\"motion\":" << (liveWins && !live.motion.empty() ? jstr(live.motion) : "null")
      << ",\"evidence\":" << jstr(evidence) << "}";
}

int liveliness(const std::string& process)
{
    if (process == "running") return 0;
    if (process == "blocked") return 1;
    if (process == "idle") return 2;
    if (process == "queued") return 3;
    return 4;
}

std::string needsLabel(int count)
{
    if (count == 0) return "";
    if (count == 1) return "unproven claim";
    return std::to_string(count) + " need you";
}

struct TicketView {
    const catalog::Document* doc = nullptr;
    std::string evidence;
    std::string runId;
    std::vector<std::string> sequence;
    std::string sequenceSource;
    catalog::Delivery delivery;
    bool needsYou = false;
    catalog::Stage reported;
    std::string lastOutcome;
    catalog::LaneState liveLane;
};

void writeDelivery(std::ostringstream& o, const catalog::Delivery& d)
{
    o << "{\"state\":" << jstr(d.state)
      << ",\"detail\":" << jstr(d.detail)
      << ",\"claimed\":" << jstr(d.claimed)
      << ",\"disagrees\":" << (d.disagrees ? "true" : "false")
      << ",\"needs_you\":" << (d.needsYou ? "true" : "false");
    if (!d.reason.empty()) o << ",\"reason\":" << jstr(d.reason);
    o << "}";
}

int urgency(const std::string& state)
{
    if (state == "stalled") return 0;
    if (state == "unshipped") return 1;
    if (state == "waiting") return 2;
    if (state == "running") return 3;
    if (state == "done") return 4;
    if (state == "not_started") return 5;
    return 6;
}

const std::vector<std::string>& manifestStages()
{
    return manifest::Store::instance().data().stages;
}

void resolveSequence(TicketView& v)
{
    if (v.doc->hasStages) {
        v.sequence = v.doc->stages;
        v.sequenceSource = "ticket";
    } else {
        v.sequence = manifestStages();
        v.sequenceSource = "manifest";
    }
}

std::vector<TicketView> viewsOf(const std::vector<catalog::Document>& docs,
                                const catalog::DeliveryLookup& delivery,
                                const catalog::StageLookup& stage = {},
                                const catalog::OutcomeLookup& outcome = {},
                                const catalog::LaneLookup& lanes = {})
{
    auto& store = catalog::Store::instance();
    const std::vector<std::string> runs = store.runIds();
    std::vector<TicketView> views;
    views.reserve(docs.size());
    for (auto& d : docs) {
        TicketView v;
        v.doc = &d;
        v.evidence = store.evidenceFor(d, runs, v.runId);
        if (delivery) v.delivery = delivery(d.id, d.status, catalog::reportFileNameFor(d.id));
        v.needsYou = (v.evidence == "claimed_unproven") || v.delivery.needsYou;
        resolveSequence(v);
        if (stage) v.reported = stage(d.id, v.sequence);
        if (outcome) v.lastOutcome = outcome(d.id);
        if (lanes) v.liveLane = lanes(d.id);
        views.push_back(std::move(v));
    }
    return views;
}

catalog::Delivery aggregateDelivery(const std::vector<const TicketView*>& children,
                                    const catalog::Document& doc,
                                    const catalog::DeliveryLookup& delivery)
{
    const bool allDone = !children.empty()
        && std::all_of(children.begin(), children.end(),
                       [](const TicketView* c) { return c->delivery.state == "done"; });

    const catalog::Delivery* best = nullptr;
    for (auto* c : children) {
        if (!allDone && c->delivery.state == "done") continue;
        if (!best || urgency(c->delivery.state) < urgency(best->state)) best = &c->delivery;
    }
    if (best) return *best;
    return delivery ? delivery(doc.id, doc.status, "") : catalog::Delivery();
}

std::string stageNameOf(const TicketView& v)
{
    const int at = v.reported.index;
    if (at <= 0 || static_cast<std::size_t>(at) > v.sequence.size()) return {};
    return v.sequence[static_cast<std::size_t>(at) - 1];
}

void writeStage(std::ostringstream& o, const TicketView& v)
{
    o << "{\"index\":" << v.reported.index
      << ",\"name\":" << jstr(stageNameOf(v))
      << ",\"total\":" << v.sequence.size()
      << ",\"round\":" << v.reported.round
      << ",\"of\":" << v.reported.of
      << ",\"source\":" << jstr(v.sequenceSource) << "}";
}

void writeTicketRow(std::ostringstream& o, const TicketView& v)
{
    o << "{\"id\":" << jstr(v.doc->id)
      << ",\"title\":" << jstr(v.doc->title)
      << ",\"status\":" << jstr(v.doc->status)
      << ",\"state\":";
    writeState(o, v.evidence, v.lastOutcome, v.liveLane);
    o << ",\"stage\":";
    writeStage(o, v);
    o << ",\"delivery\":";
    writeDelivery(o, v.delivery);
    o << ",\"needs_you\":" << (v.needsYou ? "true" : "false")
      << ",\"lane\":" << (v.liveLane.lane.empty() ? "null" : jstr(v.liveLane.lane))
      << ",\"cost_usd\":0";
    if (!v.doc->parseError.empty()) o << ",\"parse_error\":" << jstr(v.doc->parseError);
    o << "}";
}

catalog::LaneState mostLive(const std::vector<const TicketView*>& children)
{
    const catalog::LaneState* best = nullptr;
    for (auto* c : children) {
        if (liveliness(c->liveLane.process) == 4) continue;
        if (!best || liveliness(c->liveLane.process) < liveliness(best->process))
            best = &c->liveLane;
    }
    return best ? *best : catalog::LaneState();
}

std::string aggregateEvidence(const std::vector<const TicketView*>& children)
{
    bool anyProven = false;
    for (auto* c : children) {
        if (c->evidence == "claimed_unproven") return "claimed_unproven";
        if (c->evidence == "claimed_proven") anyProven = true;
    }
    return anyProven ? "claimed_proven" : "nothing_claimed";
}

std::vector<std::string> workspaceOrder()
{
    std::vector<std::string> names;
    for (auto& w : manifest::Store::instance().data().workspaces) names.push_back(w.name);
    return names;
}

std::string groupOf(const catalog::Document& ticket, const std::vector<std::string>& declared)
{
    if (std::find(declared.begin(), declared.end(), ticket.workspace) != declared.end())
        return ticket.workspace;
    return UNASSIGNED;
}

int needsCount(const std::vector<const TicketView*>& children)
{
    int n = 0;
    for (auto* c : children)
        if (c->needsYou) ++n;
    return n;
}

std::vector<std::string> workspacesOf(const std::vector<const TicketView*>& children,
                                      const std::vector<std::string>& declared)
{
    std::vector<std::string> spaces;
    for (auto& name : declared)
        for (auto* c : children)
            if (groupOf(*c->doc, declared) == name) {
                spaces.push_back(name);
                break;
            }
    for (auto* c : children)
        if (groupOf(*c->doc, declared) == UNASSIGNED) {
            spaces.push_back(UNASSIGNED);
            break;
        }
    return spaces;
}

struct RowFacts {
    std::string id, title, status, updatedIso, path, parseError;
};

struct RowTree {
    std::string parent;
    std::vector<std::string> children;
    int ownCount = 0;
};

void writeRequirementRow(std::ostringstream& o, const RowFacts& f,
                         const std::vector<const TicketView*>& children,
                         const std::vector<std::string>& declared,
                         const catalog::Delivery& del,
                         const RowTree& tree)
{
    const int needs = needsCount(children);
    o << "{\"id\":" << jstr(f.id)
      << ",\"title\":" << jstr(f.title)
      << ",\"status\":" << jstr(f.status)
      << ",\"parent\":" << jstr(tree.parent)
      << ",\"children\":";
    writeStrings(o, tree.children);
    o << ",\"updated_iso\":" << jstr(f.updatedIso)
      << ",\"path\":" << jstr(f.path)
      << ",\"workspaces\":";
    writeStrings(o, workspacesOf(children, declared));
    o << ",\"state\":";
    writeState(o, aggregateEvidence(children), std::string(), mostLive(children));
    o << ",\"delivery\":";
    writeDelivery(o, del);
    o << ",\"needs_you\":{\"count\":" << needs
      << ",\"label\":" << jstr(needsLabel(needs)) << "}"
      << ",\"ticket_count\":" << children.size()
      << ",\"own_ticket_count\":" << tree.ownCount;
    if (!f.parseError.empty()) o << ",\"parse_error\":" << jstr(f.parseError);
    o << "}";
}

std::string repoNameOf(const catalog::Document& doc)
{
    const std::vector<manifest::Store::Repo>& repos = manifest::Store::instance().data().repos;
    const std::size_t at = static_cast<std::size_t>(doc.repo);
    return at < repos.size() ? repos[at].name : std::string();
}

struct ReqNode {
    const catalog::Document* doc = nullptr;
    std::string parent;
    std::vector<std::size_t> kids;
    std::vector<const TicketView*> own;
    std::vector<const TicketView*> rolled;
};

struct ReqTree {
    std::vector<ReqNode> nodes;
    std::map<std::string, std::size_t> byId;
    std::string warnings;

    const ReqNode* find(const std::string& id) const
    {
        auto at = byId.find(id);
        return at == byId.end() ? nullptr : &nodes[at->second];
    }
};

using TicketsByParent = std::map<std::pair<int, std::string>, std::vector<const TicketView*>>;

using ParentIndex = std::map<std::string, std::vector<int>>;

ParentIndex parentIndexOf(const std::vector<catalog::Document>& reqs)
{
    ParentIndex out;
    for (const auto& doc : reqs) out[doc.id].push_back(doc.repo);
    return out;
}

std::pair<int, std::string> parentKeyOf(int repo, const std::string& parent, const ParentIndex& index)
{
    auto at = index.find(parent);
    if (parent.empty() || at == index.end()) return {repo, parent};
    for (int r : at->second)
        if (r == repo) return {repo, parent};
    if (at->second.size() == 1) return {at->second.front(), parent};
    return {repo, parent};
}

TicketsByParent ticketsByParent(const std::vector<TicketView>& views, const ParentIndex& index)
{
    TicketsByParent out;
    for (auto& v : views) out[parentKeyOf(v.doc->repo, v.doc->parent, index)].push_back(&v);
    return out;
}

void appendWarning(std::string& warnings, const std::string& one)
{
    if (!warnings.empty()) warnings += ",";
    warnings += one;
}

ReqTree buildReqTree(const std::vector<catalog::Document>& reqs, const TicketsByParent& tickets)
{
    ReqTree tree;
    tree.nodes.resize(reqs.size());
    for (std::size_t i = 0; i < reqs.size(); ++i) {
        tree.nodes[i].doc = &reqs[i];
        tree.byId.emplace(reqs[i].id, i);
    }

    for (std::size_t i = 0; i < tree.nodes.size(); ++i) {
        ReqNode& n = tree.nodes[i];
        auto at = tickets.find({n.doc->repo, n.doc->id});
        if (at != tickets.end()) n.own = at->second;
        if (n.doc->parent.empty()) continue;
        if (tree.byId.count(n.doc->parent)) {
            n.parent = n.doc->parent;
            continue;
        }
        appendWarning(tree.warnings,
                      "{\"code\":\"unknown_parent\",\"id\":" + jstr(n.doc->id)
                      + ",\"value\":" + jstr(n.doc->parent)
                      + ",\"message\":" + jstr("\"" + n.doc->id + "\" names a parent \""
                                               + n.doc->parent + "\" that no requirement in any "
                                               "declared repository carries, so it is listed at "
                                               "the top level")
                      + "}");
    }

    enum Colour { White, Gray, Black };
    std::vector<Colour> colour(tree.nodes.size(), White);
    std::vector<std::size_t> path;
    for (std::size_t i = 0; i < tree.nodes.size(); ++i) {
        if (colour[i] != White) continue;
        path.clear();
        std::size_t at = i;
        for (;;) {
            if (colour[at] == Gray) {
                auto from = std::find(path.begin(), path.end(), at);
                std::vector<std::string> members;
                for (auto it = from; it != path.end(); ++it) {
                    members.push_back(tree.nodes[*it].doc->id);
                    tree.nodes[*it].parent.clear();
                }
                std::string chain;
                for (auto& id : members) chain += id + " -> ";
                std::ostringstream one;
                one << "{\"code\":\"parent_cycle\",\"ids\":";
                writeStrings(one, members);
                one << ",\"message\":"
                    << jstr("the parent: links of " + chain + members.front()
                            + " form a cycle, so every one of them is listed at the top level")
                    << "}";
                appendWarning(tree.warnings, one.str());
                break;
            }
            if (colour[at] != White) break;
            colour[at] = Gray;
            path.push_back(at);
            if (tree.nodes[at].parent.empty()) break;
            at = tree.byId.at(tree.nodes[at].parent);
        }
        for (auto idx : path) colour[idx] = Black;
    }

    std::vector<std::size_t> roots;
    for (std::size_t i = 0; i < tree.nodes.size(); ++i) {
        if (tree.nodes[i].parent.empty()) { roots.push_back(i); continue; }
        tree.nodes[tree.byId.at(tree.nodes[i].parent)].kids.push_back(i);
    }

    std::vector<std::size_t> order;
    order.reserve(tree.nodes.size());
    std::vector<std::size_t> stack(roots.rbegin(), roots.rend());
    while (!stack.empty()) {
        const std::size_t at = stack.back();
        stack.pop_back();
        order.push_back(at);
        for (auto k = tree.nodes[at].kids.rbegin(); k != tree.nodes[at].kids.rend(); ++k)
            stack.push_back(*k);
    }
    for (auto it = order.rbegin(); it != order.rend(); ++it) {
        ReqNode& n = tree.nodes[*it];
        n.rolled = n.own;
        for (auto k : n.kids)
            n.rolled.insert(n.rolled.end(), tree.nodes[k].rolled.begin(),
                            tree.nodes[k].rolled.end());
    }
    return tree;
}

RowTree rowTreeOf(const ReqTree& tree, const ReqNode& n)
{
    RowTree out;
    out.parent = n.parent;
    out.ownCount = static_cast<int>(n.own.size());
    for (auto k : n.kids) out.children.push_back(tree.nodes[k].doc->id);
    return out;
}

std::map<std::string, std::vector<const TicketView*>> groupTickets(
    const std::vector<const TicketView*>& own, const std::vector<std::string>& declared)
{
    std::map<std::string, std::vector<const TicketView*>> groups;
    for (auto* v : own) groups[groupOf(*v->doc, declared)].push_back(v);
    return groups;
}

void writeGroups(std::ostringstream& o,
                 const std::map<std::string, std::vector<const TicketView*>>& groups,
                 const std::vector<std::string>& order)
{
    o << "[";
    bool first = true;
    for (auto& name : order) {
        auto it = groups.find(name);
        if (it == groups.end()) continue;
        if (!first) o << ",";
        first = false;

        const std::vector<std::string>* sequence = nullptr;
        std::string source = "manifest";
        for (auto* m : it->second)
            if (m->doc->hasStages) { sequence = &m->sequence; source = "ticket"; break; }
        const std::vector<std::string>& seq = sequence ? *sequence : manifestStages();

        o << "{\"workspace\":" << jstr(name) << ",\"stage_sequence\":";
        writeStrings(o, seq);
        o << ",\"stage_sequence_source\":" << jstr(source)
          << ",\"count\":" << it->second.size() << ",\"tickets\":[";
        for (std::size_t i = 0; i < it->second.size(); ++i) {
            if (i) o << ",";
            writeTicketRow(o, *it->second[i]);
        }
        o << "]}";
    }
    o << "]";
}

void writeWarnings(std::ostringstream& o, const std::string& extra)
{
    auto& store = catalog::Store::instance();
    const std::vector<std::string> entries = store.documentPaths();
    o << "[";
    bool first = true;

    const auto& m = manifest::Store::instance().data();
    if (m.state != manifest::Store::State::Ok) {
        o << "{\"code\":\"invalid_manifest\",\"message\":" << jstr(m.errorMessage);
        if (m.hasErrorPointer) o << ",\"pointer\":" << jstr(m.errorPointer);
        if (m.hasErrorPath)    o << ",\"path\":" << jstr(m.errorPath);
        if (m.hasErrorField)   o << ",\"field\":" << jstr(m.errorField);
        o << "}";
        first = false;
    }
    for (auto& path : entries) {
        if (path.empty()) continue;
        std::error_code ec;
        if (std::filesystem::is_directory(std::filesystem::path(path), ec)) continue;
        if (!first) o << ",";
        first = false;
        o << "{\"code\":\"path_missing\",\"path\":" << jstr(path)
          << ",\"message\":" << jstr("\"" + path + "\" is not a directory") << "}";
    }
    if (!extra.empty()) {
        if (!first) o << ",";
        first = false;
        o << extra;
    }
    o << "]";
}

std::string percentDecode(const std::string& s)
{
    std::string out;
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '+') {
            out += ' ';
        } else if (s[i] == '%' && i + 2 < s.size() &&
                   std::isxdigit(static_cast<unsigned char>(s[i + 1])) &&
                   std::isxdigit(static_cast<unsigned char>(s[i + 2]))) {
            out += static_cast<char>(std::stoi(s.substr(i + 1, 2), nullptr, 16));
            i += 2;
        } else {
            out += s[i];
        }
    }
    return out;
}

const std::vector<std::string>& provenanceValues()
{
    static const std::vector<std::string> values {
        "not_started", "running", "stalled", "unshipped", "waiting", "done", "unknown"};
    return values;
}

std::string sentenceOf(const std::vector<std::string>& values)
{
    std::string out;
    for (std::size_t i = 0; i < values.size(); ++i) {
        if (i) out += (i + 1 == values.size()) ? " or " : ", ";
        out += values[i];
    }
    return out;
}

std::string badFilterJson(const std::string& parameter, const std::string& value,
                          const std::vector<std::string>& accepted)
{
    std::ostringstream o;
    o << "{\"error\":{\"code\":\"bad_filter\""
      << ",\"message\":" << jstr(parameter + " must be one of " + sentenceOf(accepted)
                                 + "; this repository does not declare \"" + value + "\"")
      << ",\"detail\":{\"parameter\":" << jstr(parameter)
      << ",\"value\":" << jstr(value)
      << ",\"accepted\":";
    writeStrings(o, accepted);
    o << "}" << remedy::member("null") << "}}";
    return o.str();
}

struct Owner {
    std::string name;
    std::string source;
    std::string value;
};

Owner ownerOf(const TicketView& v, const catalog::AuthorLookup& authors)
{
    Owner out;
    const std::string fromCommits = authors ? authors(v.doc->id) : std::string();
    if (!fromCommits.empty()) {
        out.name = fromCommits;
        out.source = "commits";
        return out;
    }
    for (const auto& label : manifest::Store::instance().data().ticketFields) {
        if (lowered(label) != "owner") continue;
        const std::string* written = v.doc->field(label);
        if (!written || written->empty()) continue;
        out.name = *written;
        out.source = "document";
        out.value = *written;
        return out;
    }
    return out;
}

std::string ownerMember(const Owner& owner)
{
    if (owner.source.empty()) return {};
    std::ostringstream o;
    o << ",\"owner\":{\"name\":" << jstr(owner.name) << ",\"source\":" << jstr(owner.source);
    if (!owner.value.empty()) o << ",\"value\":" << jstr(owner.value);
    o << "}";
    return o.str();
}

struct IndexEntry {
    const TicketView* view = nullptr;
    Owner owner;
    std::string stage;
    int repo = 0;
    std::string parent;
};

void writeIndexRow(std::ostringstream& o, const IndexEntry& e,
                   const catalog::RunsLookup& runs, const catalog::RolesLookup& roles)
{
    const TicketView& v = *e.view;
    o << "{\"id\":" << jstr(v.doc->id)
      << ",\"title\":" << jstr(v.doc->title)
      << ",\"workspace\":" << jstr(v.doc->workspace)
      << ",\"status\":" << jstr(v.doc->status)
      << ",\"provenance\":" << jstr(v.delivery.state)
      << ",\"delivery\":";
    writeDelivery(o, v.delivery);
    o << ",\"stage\":";
    writeStage(o, v);
    o << ",\"lane\":" << (v.liveLane.lane.empty() ? "null" : jstr(v.liveLane.lane))
      << ",\"evidence\":{\"verdict\":" << jstr(v.evidence);
    if (!v.runId.empty()) o << ",\"run_id\":" << jstr(v.runId);
    o << "}";

    const std::vector<catalog::Role> started =
        roles ? roles(v.doc->id) : std::vector<catalog::Role>{};
    o << ",\"roles\":[";
    for (std::size_t i = 0; i < started.size(); ++i) {
        const catalog::Role& r = started[i];
        if (i) o << ",";
        o << "{\"role\":" << jstr(r.role) << ",\"stage\":" << jstr(r.stage)
          << ",\"state\":" << jstr(r.state);
        if (!r.lane.empty()) o << ",\"lane\":" << jstr(r.lane);
        if (!r.runId.empty()) o << ",\"run_id\":" << jstr(r.runId);
        o << "}";
    }
    o << "]";

    o << ownerMember(e.owner);

    o << ",\"extras\":[";
    bool first = true;
    for (const auto& label : manifest::Store::instance().data().ticketFields) {
        const std::string* written = v.doc->field(label);
        if (!written || written->empty()) continue;
        if (!first) o << ",";
        first = false;
        o << "{\"label\":" << jstr(label) << ",\"value\":" << jstr(*written) << "}";
    }
    o << "]";

    const catalog::Runs counted = runs ? runs(v.doc->id) : catalog::Runs{};
    o << ",\"cost_usd\":" << money(counted.costUsd)
      << ",\"needs_you\":" << (v.needsYou ? "true" : "false");
    if (!v.doc->parseError.empty()) o << ",\"parse_error\":" << jstr(v.doc->parseError);
    o << "}";
}

long long boundedNumber(const std::string& text, long long fallback, long long ceiling)
{
    if (text.empty()) return fallback;
    for (unsigned char c : text)
        if (c < '0' || c > '9') return fallback;
    const long long parsed = std::strtoll(text.c_str(), nullptr, 10);
    if (parsed <= 0) return fallback;
    return parsed > ceiling ? ceiling : parsed;
}


std::string trimCell(std::string s)
{
    const auto notSpace = [](unsigned char c) { return !std::isspace(c); };
    s.erase(s.begin(), std::find_if(s.begin(), s.end(), notSpace));
    s.erase(std::find_if(s.rbegin(), s.rend(), notSpace).base(), s.end());
    return s;
}

bool isTableRule(const std::string& line)
{
    bool sawDash = false;
    for (char c : line) {
        if (c == '-') sawDash = true;
        else if (c != '|' && c != ':' && !std::isspace(static_cast<unsigned char>(c))) return false;
    }
    return sawDash;
}

std::vector<std::string> tableCells(const std::string& line)
{
    std::vector<std::string> out;
    std::string cell;
    bool escape = false;
    for (char c : line) {
        if (escape) { cell += c; escape = false; continue; }
        if (c == '\\') { escape = true; cell += c; continue; }
        if (c == '|') { out.push_back(trimCell(cell)); cell.clear(); continue; }
        cell += c;
    }
    out.push_back(trimCell(cell));
    if (!out.empty() && out.front().empty()) out.erase(out.begin());
    if (!out.empty() && out.back().empty()) out.pop_back();
    return out;
}

std::string idNamedIn(const std::string& text, const std::set<std::string>& known)
{
    for (const auto& id : known) {
        if (text.find(id) != std::string::npos) return id;
    }
    return {};
}

std::string frontLabelValue(const std::string& line, std::string& value)
{
    const std::size_t colon = line.find(':');
    if (colon == std::string::npos) return {};
    const std::string key = trimCell(line.substr(0, colon));
    if (key.empty()) return {};
    for (char c : key) {
        if (!std::isalnum(static_cast<unsigned char>(c)) && c != '_' && c != '-') return {};
    }
    value = trimCell(line.substr(colon + 1));
    return key;
}
}

namespace {

struct Clause {
    const char* name;
    const char* sentence;
};

std::vector<Clause> clausesOf(const TicketView& v)
{
    const catalog::LaneState& lane = v.liveLane;
    std::vector<Clause> out;
    if (lane.process == "idle")    out.push_back({"idle", "the agent asked and stopped"});
    if (lane.process == "blocked") out.push_back({"blocked", "it is waiting on your answer"});
    if (lane.process == "crashed") out.push_back({"crashed", "it died and nobody has looked"});
    if (lane.process == "finished" && lane.evidence == "claimed_unproven")
        out.push_back({"claimed_unproven", "it says done and cites nothing"});
    if (lane.process == "running" && lane.motion == "silent")
        out.push_back({"silent", "it is alive and going nowhere"});
    if (v.delivery.state == "stalled")
        out.push_back({"stalled", "a session was launched and is gone"});
    if (v.delivery.state == "unshipped")
        out.push_back({"unshipped", "work exists that nobody else can see"});
    return out;
}

void writeNeedsYouRow(std::ostringstream& o, const TicketView& v,
                      const std::vector<Clause>& clauses)
{
    o << "{\"ticket_id\":" << jstr(v.doc->id)
      << ",\"title\":" << jstr(v.doc->title)
      << ",\"clauses\":[";
    std::string detail;
    for (std::size_t i = 0; i < clauses.size(); ++i) {
        o << (i ? "," : "") << jstr(clauses[i].name);
        detail += (detail.empty() ? "" : "; ");
        detail += clauses[i].sentence;
    }
    o << "],\"detail\":" << jstr(detail)
      << ",\"lane\":" << (v.liveLane.lane.empty() ? "null" : jstr(v.liveLane.lane))
      << ",\"remedy\":"
      << (v.liveLane.lane.empty() ? std::string("null") : remedy::openLane(v.liveLane.lane))
      << "}";
}

void writeUnpushedRow(std::ostringstream& o, const TicketView& v, const catalog::Unpushed& u)
{
    o << "{\"ticket_id\":" << jstr(v.doc->id)
      << ",\"title\":" << jstr(v.doc->title)
      << ",\"branch\":" << jstr(u.branch)
      << ",\"commits\":" << u.commits
      << ",\"age_s\":" << u.ageS << "}";
}

}


std::string roleOf(const TicketView& v, const catalog::RolesLookup& roles)
{
    if (v.liveLane.lane.empty()) return {};
    if (!v.liveLane.role.empty()) return v.liveLane.role;
    if (!roles) return {};
    for (const catalog::Role& r : roles(v.doc->id))
        if (r.lane == v.liveLane.lane && !r.role.empty()) return r.role;
    return {};
}

void writeHomeTicketRow(std::ostringstream& o, const TicketView& v,
                        const std::set<std::string>& escaped,
                        const std::map<int, std::string>& reportsByRepo,
                        const catalog::RolesLookup& roles)
{
    std::string verdict = "null";
    const std::string& claim = v.doc->status;
    if (claim == "pass" || claim == "fail") {
        std::error_code ec;
        const auto reports = reportsByRepo.find(v.doc->repo);
        const std::string dir = reports == reportsByRepo.end() ? std::string() : reports->second;
        const std::string qa = dir + "/" + v.doc->id + "-qa.md";
        if (!dir.empty() && std::filesystem::exists(std::filesystem::path(qa), ec) && !ec)
            verdict = jstr(claim);
    }

    o << "{\"ticket_id\":" << jstr(v.doc->id)
      << ",\"title\":" << jstr(v.doc->title)
      << ",\"lane\":" << (v.liveLane.lane.empty() ? "null" : jstr(v.liveLane.lane))
      << ",\"role\":" << (roleOf(v, roles).empty() ? std::string("null")
                                                    : jstr(roleOf(v, roles)))
      << ",\"state\":{\"process\":" << jstr(v.liveLane.process.empty() ? std::string("not_started")
                                                                       : v.liveLane.process)
      << ",\"motion\":" << (v.liveLane.motion.empty() ? "null" : jstr(v.liveLane.motion))
      << ",\"evidence\":" << jstr(v.evidence) << "}"
      << ",\"delivery\":";
    writeDelivery(o, v.delivery);
    o << ",\"status\":" << jstr(v.doc->status)
      << ",\"verdict\":" << verdict
      << ",\"escaped\":"
      << (escaped.count(v.doc->id) ? "true" : "false")
      << "}";
}

std::string leadingDate(const std::string& cell)
{
    const std::string text = trimCell(cell);
    if (text.size() < 10) return {};
    for (std::size_t i = 0; i < 10; ++i) {
        const char c = text[i];
        const bool dash = (i == 4 || i == 7);
        if (dash ? c != '-' : !std::isdigit(static_cast<unsigned char>(c))) return {};
    }
    return text.substr(0, 10);
}

std::set<std::string> escapedIds(const std::set<std::string>& known)
{
    std::set<std::string> out;
    const manifest::Store::Data& data = manifest::Store::instance().data();
    for (const auto& repo : data.repos) {
        if (repo.docs.escapesAbs.empty()) continue;
        std::ifstream in(repo.docs.escapesAbs);
        if (!in) continue;

        std::string line;
        bool inTable = false;
        std::size_t column = std::string::npos;
        while (std::getline(in, line)) {
            const std::string trimmedLine = trimCell(line);
            if (!inTable) {
                if (trimmedLine.size() < 2 || trimmedLine.front() != '|') continue;
                const std::vector<std::string> header = tableCells(trimmedLine);
                if (!std::getline(in, line) || !isTableRule(trimCell(line))) continue;
                for (std::size_t c = 0; c < header.size(); ++c) {
                    std::string name = header[c];
                    for (char& ch : name) ch = static_cast<char>(std::tolower(
                        static_cast<unsigned char>(ch)));
                    if (name.find("escaped from") != std::string::npos) column = c;
                }
                inTable = true;
                continue;
            }
            if (trimmedLine.empty() || trimmedLine.front() != '|') break;
            if (column == std::string::npos) continue;
            const std::vector<std::string> cells = tableCells(trimmedLine);
            if (column >= cells.size()) continue;
            for (const std::string& id : known)
                if (cells[column].find(id) != std::string::npos) out.insert(id);
        }
    }
    return out;
}

catalog::Result catalog::renderHome(const DeliveryLookup& delivery, const LaneLookup& lanes,
                                    const UnpushedLookup& unpushed, const Occupancy& seats,
                                    const RolesLookup& roles)
{
    Store::Listing tkts = Store::instance().tickets();
    std::vector<TicketView> views = viewsOf(tkts.documents, delivery, {}, {}, lanes);

    std::map<std::string, int> counted;
    for (auto& state : deliveryStates()) counted[state] = 0;

    std::ostringstream needs, ahead;
    int needsTotal = 0, unpushedTotal = 0;
    for (auto& v : views) {
        auto known = counted.find(v.delivery.state);
        if (known != counted.end()) ++known->second;

        const std::vector<Clause> clauses = clausesOf(v);
        if (!clauses.empty()) {
            if (needsTotal++) needs << ",";
            writeNeedsYouRow(needs, v, clauses);
        }

        const Unpushed u = unpushed ? unpushed(v.doc->id) : Unpushed();
        if (v.delivery.state == "unshipped" && u.present) {
            if (unpushedTotal++) ahead << ",";
            writeUnpushedRow(ahead, v, u);
        }
    }

    std::ostringstream o;
    o << "{\"delivery\":[";
    const std::vector<std::string>& states = deliveryStates();
    for (std::size_t i = 0; i < states.size(); ++i)
        o << (i ? "," : "") << "{\"state\":" << jstr(states[i])
          << ",\"count\":" << counted[states[i]] << "}";
    o << "],\"tickets\":[";
    std::set<std::string> knownIds;
    for (const auto& view : views) knownIds.insert(view.doc->id);
    const std::set<std::string> escaped = escapedIds(knownIds);
    const manifest::Store::Data& mdata = manifest::Store::instance().data();
    std::map<int, std::string> reportsByRepo;
    for (std::size_t r = 0; r < mdata.repos.size(); ++r)
        reportsByRepo[static_cast<int>(r)] = mdata.repos[r].docs.reportsAbs;
    for (std::size_t i = 0; i < views.size(); ++i) {
        if (i) o << ",";
        writeHomeTicketRow(o, views[i], escaped, reportsByRepo, roles);
    }
    o << "]"
      << ",\"needs_you\":{\"total\":" << needsTotal << ",\"rows\":[" << needs.str() << "]}"
      << ",\"unpushed\":{\"total\":" << unpushedTotal << ",\"rows\":[" << ahead.str() << "]}"
      << ",\"lanes\":{\"occupied\":" << seats.occupied << ",\"total\":" << seats.total << "}"
      << ",\"warnings\":";
    writeWarnings(o, std::string());
    o << "}";
    return {200, o.str()};
}

const std::vector<std::string>& catalog::deliveryStates()
{
    static const std::vector<std::string> STATES {
        "not_started", "running", "stalled", "unshipped", "waiting", "done", "unknown"};
    return STATES;
}

std::string catalog::reportFileNameFor(const std::string& ticketId)
{
    if (ticketId.empty() || ticketId.find('/') != std::string::npos
        || ticketId.find("..") != std::string::npos) return "";
    return ticketId + "-report.md";
}

std::string catalog::ambiguousTicketJson(const std::string& id,
                                         const std::vector<std::string>& paths)
{
    std::ostringstream o;
    o << "{\"error\":{\"code\":\"ambiguous_ticket_id\""
      << ",\"message\":" << jstr("two of the repositories this manifest declares hold a ticket "
                                  "with id " + id + "; the tool will not choose between them")
      << ",\"detail\":{\"ticket_id\":" << jstr(id) << ",\"paths\":";
    writeStrings(o, paths);
    o << "}" << remedy::member(remedy::none()) << "}}";
    return o.str();
}

catalog::Result catalog::renderRequirements(const std::string& query, const std::string& filter,
                                            const DeliveryLookup& delivery,
                                            const LaneLookup& lanes)
{
    const std::string q = percentDecode(query);
    const std::string f = filter.empty() ? "all" : filter;
    if (f != "all" && f != "needs" && f != "running" && f != "blocked" && f != "done") {
        return {400, errorJson("bad_filter",
                               "filter must be one of all, needs, running, blocked or done",
                               "filter", filter, "null")};
    }

    auto& store = Store::instance();
    Store::Listing reqs = store.requirements();
    Store::Listing tkts = store.tickets();
    std::vector<TicketView> views = viewsOf(tkts.documents, delivery, {}, {}, lanes);

    const ParentIndex parentIndex = parentIndexOf(reqs.documents);
    const TicketsByParent byParent = ticketsByParent(views, parentIndex);
    const ReqTree tree = buildReqTree(reqs.documents, byParent);

    std::set<std::pair<int, std::string>> claimed;
    for (auto& doc : reqs.documents) claimed.insert({doc.repo, doc.id});

    const std::vector<std::string> declared = workspaceOrder();

    const bool laneFilter = (f == "running" || f == "blocked");

    const bool doneFilter = (f == "done");

    std::ostringstream rows;
    int shown = 0;
    for (std::size_t i = 0; i < reqs.documents.size(); ++i) {
        const Document& doc = reqs.documents[i];
        if (!q.empty() && !contains(doc.id, q) && !contains(doc.title, q)) continue;

        const ReqNode& node = tree.nodes[i];
        const std::vector<const TicketView*>& children = node.rolled;

        if (f == "needs" && needsCount(children) == 0) continue;
        if (laneFilter) continue;

        const Delivery aggregate = aggregateDelivery(children, doc, delivery);
        if (doneFilter && aggregate.state != "done") continue;

        if (shown) rows << ",";
        ++shown;
        writeRequirementRow(rows,
                            {doc.id, doc.title, doc.status, isoTime(doc.mtime), doc.path,
                             doc.parseError},
                            children, declared, aggregate, rowTreeOf(tree, node));
    }

    std::vector<const TicketView*> orphans;
    for (auto& v : views)
        if (!claimed.count(parentKeyOf(v.doc->repo, v.doc->parent, parentIndex))) orphans.push_back(&v);

    bool nameIsFree = true;
    for (auto& c : claimed)
        if (c.second == STANDALONE) nameIsFree = false;
    const bool matchesQuery = q.empty() || contains(STANDALONE, q) || contains(STANDALONE_TITLE, q);
    if (!orphans.empty() && nameIsFree && matchesQuery && !laneFilter
        && !(f == "needs" && needsCount(orphans) == 0)) {
        long long newest = 0;
        for (auto* c : orphans) newest = std::max(newest, c->doc->mtime);
        Document synthetic;
        synthetic.id = STANDALONE;
        synthetic.title = STANDALONE_TITLE;

        const Delivery aggregate = aggregateDelivery(orphans, synthetic, delivery);
        if (!doneFilter || aggregate.state == "done") {
            if (shown) rows << ",";
            ++shown;
            writeRequirementRow(rows, {STANDALONE, STANDALONE_TITLE, "", isoTime(newest), "", ""},
                                orphans, declared, aggregate,
                                {"", {}, static_cast<int>(orphans.size())});
        }
    }

    std::ostringstream o;
    o << "{\"requirements\":[" << rows.str() << "]"
      << ",\"total\":" << shown
      << ",\"in_flight\":0"
      << ",\"warnings\":";
    writeWarnings(o, tree.warnings);
    o << "}";
    return {200, o.str()};
}

catalog::Result catalog::renderRequirement(const std::string& id,
                                           const DeliveryLookup& delivery,
                                           const StageLookup& stage,
                                           const OutcomeLookup& outcome,
                                           const LaneLookup& lanes)
{
    auto& store = Store::instance();
    Store::Listing reqs = store.requirements();
    Document req;
    const bool synthetic = !store.requirementById(id, req);
    std::set<std::pair<int, std::string>> claimed;
    if (synthetic) {
        if (id != STANDALONE) {
            return {404, errorJson("not_found", "no requirement with id " + id,
                                   "path", store.requirementsPath(),
                                   remedy::reloadManifest())};
        }
        for (auto& doc : reqs.documents) claimed.insert({doc.repo, doc.id});
        req.id = STANDALONE;
        req.title = STANDALONE_TITLE;
        req.body = STANDALONE_SUMMARY;
    }

    Store::Listing tkts = store.tickets();
    std::vector<TicketView> views = viewsOf(tkts.documents, delivery, stage, outcome, lanes);
    const std::vector<std::string> declared = workspaceOrder();

    const ParentIndex parentIndex = parentIndexOf(reqs.documents);
    const TicketsByParent byParent = ticketsByParent(views, parentIndex);
    const ReqTree tree = buildReqTree(reqs.documents, byParent);
    const ReqNode* node = synthetic ? nullptr : tree.find(id);

    std::vector<std::string> order = declared;
    order.push_back(UNASSIGNED);

    int needs = 0, total = 0;
    std::map<std::string, std::vector<const TicketView*>> groups;
    for (auto& v : views) {
        const auto key = parentKeyOf(v.doc->repo, v.doc->parent, parentIndex);
        if (synthetic ? claimed.count(key) > 0
                      : (key.second != id || key.first != req.repo)) continue;
        groups[groupOf(*v.doc, declared)].push_back(&v);
        ++total;
        if (v.needsYou) ++needs;
        if (synthetic) req.mtime = std::max(req.mtime, v.doc->mtime);
    }

    if (synthetic && total == 0) {
        return {404, errorJson("not_found", "no requirement with id " + id,
                               "path", store.requirementsPath(),
                               remedy::reloadManifest())};
    }

    std::ostringstream o;
    o << "{\"id\":" << jstr(req.id)
      << ",\"title\":" << jstr(req.title)
      << ",\"status\":" << jstr(req.status)
      << ",\"path\":" << jstr(req.path)
      << ",\"updated_iso\":" << jstr(isoTime(req.mtime))
      << ",\"summary\":" << jstr(summarise(req.body))
      << ",\"body_markdown\":" << jstr(req.body)
      << ",\"needs_you\":{\"count\":" << needs << ",\"total\":" << total
      << ",\"label\":" << jstr(needsLabel(needs)) << "}"
      << ",\"state\":";
    {
        std::vector<const TicketView*> all;
        if (node) {
            all = node->rolled;
        } else {
            for (auto& [name, members] : groups)
                for (auto* m : members) all.push_back(m);
        }
        writeState(o, aggregateEvidence(all), std::string(), mostLive(all));
        o << ",\"delivery\":";
        writeDelivery(o, aggregateDelivery(all, req, delivery));
    }

    o << ",\"children\":[";
    if (node) {
        for (std::size_t k = 0; k < node->kids.size(); ++k) {
            const ReqNode& kid = tree.nodes[node->kids[k]];
            if (k) o << ",";
            o << "{\"id\":" << jstr(kid.doc->id)
              << ",\"title\":" << jstr(kid.doc->title)
              << ",\"repo\":" << jstr(repoNameOf(*kid.doc))
              << ",\"status\":" << jstr(kid.doc->status)
              << ",\"own_ticket_count\":" << kid.own.size()
              << ",\"ticket_count\":" << kid.rolled.size()
              << ",\"delivery\":";
            writeDelivery(o, aggregateDelivery(kid.rolled, *kid.doc, delivery));
            o << ",\"tickets\":";
            writeGroups(o, groupTickets(kid.own, declared), order);
            o << "}";
        }
    }
    o << "],\"groups\":";
    writeGroups(o, groups, order);
    o << ",\"warnings\":";
    writeWarnings(o, tree.warnings);
    o << "}";
    return {200, o.str()};
}

catalog::Result catalog::renderTicket(const std::string& id, const DeliveryLookup& delivery,
                                      const RunsLookup& runs, const StageLookup& stage,
                                      const RolesLookup& roles, const AuthorLookup& authors)
{
    auto& store = Store::instance();
    Document ticket;
    std::vector<std::string> claimants;
    switch (store.findTicket(id, ticket, claimants)) {
        case Store::Lookup::Missing:
            return {404, errorJson("not_found", "no ticket with id " + id,
                                   "path", store.ticketsPath(),
                                   remedy::reloadManifest())};
        case Store::Lookup::Ambiguous:
            return {409, ambiguousTicketJson(id, claimants)};
        case Store::Lookup::Found:
            break;
    }

    TicketView v;
    v.doc = &ticket;
    v.evidence = store.evidenceFor(ticket, store.runIds(), v.runId);
    if (delivery) v.delivery = delivery(ticket.id, ticket.status, catalog::reportFileNameFor(ticket.id));
    v.needsYou = (v.evidence == "claimed_unproven") || v.delivery.needsYou;
    resolveSequence(v);
    if (stage) v.reported = stage(ticket.id, v.sequence);

    std::ostringstream o;
    o << "{\"id\":" << jstr(ticket.id)
      << ",\"title\":" << jstr(ticket.title)
      << ",\"workspace\":" << jstr(ticket.workspace)
      << ",\"status\":" << jstr(ticket.status)
      << ownerMember(ownerOf(v, authors))
      << ",\"parent\":" << jstr(ticket.parent)
      << ",\"path\":" << jstr(ticket.path)
      << ",\"updated_iso\":" << jstr(isoTime(ticket.mtime))
      << ",\"stages\":";
    writeStrings(o, v.sequence);
    o << ",\"stage\":";
    writeStage(o, v);

    o << ",\"extras\":[";
    bool first = true;
    for (auto& label : manifest::Store::instance().data().ticketFields) {
        const std::string* value = ticket.field(label);
        if (!value || value->empty()) continue;
        if (!first) o << ",";
        first = false;
        o << "{\"label\":" << jstr(label) << ",\"value\":" << jstr(*value) << "}";
    }
    o << "]";

    o << ",\"body_markdown\":" << jstr(ticket.body)
      << ",\"lane\":null"
      << ",\"runs\":";
    const Runs counted = runs ? runs(ticket.id) : Runs{};
    o << "{\"count\":" << counted.count << ",\"duration_s\":" << counted.durationS
      << ",\"cost_usd\":" << money(counted.costUsd) << "}"
      << ",\"report\":{\"exists\":" << (v.evidence == "nothing_claimed" ? "false" : "true")
      << ",\"path\":" << jstr(store.reportPathFor(ticket))
      << ",\"evidence\":" << jstr(v.evidence) << "}"
      << ",\"needs_you\":" << (v.needsYou ? "true" : "false")
      << ",\"state\":";
    writeState(o, v.evidence, counted.lastOutcome);
    o << ",\"delivery\":";
    writeDelivery(o, v.delivery);

    const std::vector<Role> started = roles ? roles(ticket.id) : std::vector<Role>{};
    if (!started.empty()) {
        o << ",\"roles\":[";
        for (std::size_t i = 0; i < started.size(); ++i) {
            const Role& r = started[i];
            if (i) o << ",";
            o << "{\"role\":" << jstr(r.role) << ",\"stage\":" << jstr(r.stage)
              << ",\"state\":" << jstr(r.state);
            if (!r.lane.empty()) o << ",\"lane\":" << jstr(r.lane);
            if (!r.runId.empty()) o << ",\"run_id\":" << jstr(r.runId);
            o << "}";
        }
        o << "]";
    }

    if (!ticket.parseError.empty()) o << ",\"parse_error\":" << jstr(ticket.parseError);
    o << "}";
    return {200, o.str()};
}

catalog::Result catalog::renderReport(const std::string& ticketId)
{
    auto& store = Store::instance();
    Document ticket;
    std::vector<std::string> claimants;
    if (store.findTicket(ticketId, ticket, claimants) == Store::Lookup::Ambiguous)
        return {409, ambiguousTicketJson(ticketId, claimants)};

    Document report;
    if (!store.readReport(ticketId, report)) {
        return {404, errorJson("not_found", "no delivery report for " + ticketId,
                               "path", store.reportPathFor(ticketId),
                               remedy::reloadManifest())};
    }

    std::string runId;
    const std::string verdict = store.evidenceFor(ticketId, store.runIds(), runId);

    std::ostringstream o;
    o << "{\"ticket_id\":" << jstr(ticketId)
      << ",\"path\":" << jstr(report.path)
      << ",\"updated_iso\":" << jstr(isoTime(report.mtime))
      << ",\"body_markdown\":" << jstr(report.body)
      << ",\"evidence\":{\"verdict\":" << jstr(verdict);
    if (!runId.empty()) o << ",\"run_id\":" << jstr(runId);
    o << "}}";
    return {200, o.str()};
}

catalog::Result catalog::renderTickets(const TicketQuery& query, const DeliveryLookup& delivery,
                                       const RunsLookup& runs, const StageLookup& stage,
                                       const RolesLookup& roles, const LaneLookup& lanes,
                                       const AuthorLookup& authors)
{
    auto& store = Store::instance();
    Store::Listing reqs = store.requirements();
    Store::Listing tkts = store.tickets();
    std::vector<TicketView> views = viewsOf(tkts.documents, delivery, stage, {}, lanes);

    std::vector<std::string> declaredStages = manifestStages();
    for (const auto& v : views)
        for (const auto& name : v.sequence)
            if (std::find(declaredStages.begin(), declaredStages.end(), name)
                == declaredStages.end())
                declaredStages.push_back(name);

    const std::string q = percentDecode(query.q);
    const std::string wantProvenance =
        query.provenance.empty() ? std::string("all") : percentDecode(query.provenance);
    const std::string wantStage = percentDecode(query.stage);

    if (wantProvenance != "all"
        && std::find(provenanceValues().begin(), provenanceValues().end(), wantProvenance)
           == provenanceValues().end()) {
        std::vector<std::string> accepted {"all"};
        accepted.insert(accepted.end(), provenanceValues().begin(), provenanceValues().end());
        return {400, badFilterJson("provenance", wantProvenance, accepted)};
    }
    if (!wantStage.empty()
        && std::find(declaredStages.begin(), declaredStages.end(), wantStage)
           == declaredStages.end())
        return {400, badFilterJson("stage", wantStage, declaredStages)};

    std::vector<IndexEntry> searched;
    searched.reserve(views.size());
    for (const auto& v : views) {
        IndexEntry e;
        e.view = &v;
        e.owner = ownerOf(v, authors);
        e.stage = stageNameOf(v);
        e.repo = v.doc->repo;
        e.parent = v.doc->parent;
        const bool matches = q.empty() || contains(v.doc->id, q) || contains(v.doc->title, q)
            || (!e.owner.name.empty() && contains(e.owner.name, q))
            || (!v.liveLane.lane.empty() && contains(v.liveLane.lane, q));
        if (matches) searched.push_back(std::move(e));
    }

    std::map<std::string, int> stageCounts, provenanceCounts;
    for (const auto& e : searched) {
        if (!e.stage.empty()) ++stageCounts[e.stage];
        ++provenanceCounts[e.view->delivery.state];
    }

    std::vector<const IndexEntry*> matching;
    for (const auto& e : searched) {
        if (wantProvenance != "all" && e.view->delivery.state != wantProvenance) continue;
        if (!wantStage.empty() && e.stage != wantStage) continue;
        matching.push_back(&e);
    }

    const ParentIndex parentIndex = parentIndexOf(reqs.documents);
    std::map<std::pair<int, std::string>, std::vector<const IndexEntry*>> byParent;
    for (auto* e : matching) byParent[parentKeyOf(e->repo, e->parent, parentIndex)].push_back(e);

    std::set<std::pair<int, std::string>> claimed;
    for (const auto& doc : reqs.documents) claimed.insert({doc.repo, doc.id});

    struct GroupView {
        std::string parent;
        std::string title;
        std::vector<const IndexEntry*> members;
    };
    std::vector<GroupView> groups;
    for (const auto& doc : reqs.documents) {
        auto found = byParent.find({doc.repo, doc.id});
        if (found == byParent.end() || found->second.empty()) continue;
        groups.push_back({doc.id, doc.title, found->second});
    }
    std::vector<const IndexEntry*> orphans;
    for (auto* e : matching)
        if (!claimed.count(parentKeyOf(e->repo, e->parent, parentIndex))) orphans.push_back(e);
    if (!orphans.empty()) groups.push_back({STANDALONE, STANDALONE_TITLE, orphans});

    long long pageSize = boundedNumber(query.pageSize, 50, 1000000);
    bool clamped = false;
    if (pageSize > 200) {
        pageSize = 200;
        clamped = true;
    }
    const long long page = boundedNumber(query.page, 1, 1000000);
    const long long total = static_cast<long long>(matching.size());
    const long long pages = total <= 0 ? 1 : (total + pageSize - 1) / pageSize;
    const long long start = (page - 1) * pageSize;
    const long long from = start >= total ? 0 : start + 1;
    const long long to = start >= total ? 0 : std::min(start + pageSize, total);

    std::ostringstream rows;
    long long at = 0;
    for (std::size_t g = 0; g < groups.size(); ++g) {
        if (g) rows << ",";
        int merged = 0, pushed = 0;
        for (auto* e : groups[g].members) {
            if (e->view->delivery.state == "done") ++merged;
            else if (e->view->delivery.state == "waiting") ++pushed;
        }
        rows << "{\"parent\":" << jstr(groups[g].parent)
             << ",\"title\":" << jstr(groups[g].title)
             << ",\"count\":" << groups[g].members.size()
             << ",\"merged\":" << merged
             << ",\"pushed\":" << pushed
             << ",\"tickets\":[";
        bool firstRow = true;
        for (auto* e : groups[g].members) {
            const long long here = at++;
            if (here < start || here >= start + pageSize) continue;
            if (!firstRow) rows << ",";
            firstRow = false;
            writeIndexRow(rows, *e, runs, roles);
        }
        rows << "]}";
    }

    std::ostringstream o;
    o << "{\"groups\":[" << rows.str() << "]"
      << ",\"total\":" << total
      << ",\"from\":" << from
      << ",\"to\":" << to
      << ",\"page\":" << page
      << ",\"pages\":" << pages
      << ",\"page_size\":" << pageSize
      << ",\"facets\":{\"provenance\":[";
    for (std::size_t i = 0; i < provenanceValues().size(); ++i) {
        if (i) o << ",";
        auto found = provenanceCounts.find(provenanceValues()[i]);
        o << "{\"value\":" << jstr(provenanceValues()[i]) << ",\"count\":"
          << (found == provenanceCounts.end() ? 0 : found->second) << "}";
    }
    o << "],\"stage\":[";
    for (std::size_t i = 0; i < declaredStages.size(); ++i) {
        if (i) o << ",";
        auto found = stageCounts.find(declaredStages[i]);
        o << "{\"value\":" << jstr(declaredStages[i]) << ",\"count\":"
          << (found == stageCounts.end() ? 0 : found->second) << "}";
    }
    o << "]}"
      << ",\"applied\":{\"q\":" << jstr(q)
      << ",\"provenance\":" << jstr(wantProvenance)
      << ",\"stage\":" << jstr(wantStage) << "}"
      << ",\"warnings\":";
    writeWarnings(o, clamped
        ? "{\"code\":\"clamped\",\"field\":\"page_size\",\"message\":\"page_size was clamped to "
          "200, which is the maximum this endpoint serves\"}"
        : std::string());
    o << "}";
    return {200, o.str()};
}


std::vector<std::string> catalog::escapeRowDates()
{
    std::vector<std::string> out;
    const manifest::Store::Data& data = manifest::Store::instance().data();

    for (const auto& repo : data.repos) {
        if (repo.docs.escapesAbs.empty()) continue;
        std::ifstream in(repo.docs.escapesAbs);
        if (!in) continue;

        std::string line;
        bool inTable = false;
        while (std::getline(in, line)) {
            const std::string trimmedLine = trimCell(line);
            if (!inTable) {
                if (trimmedLine.size() < 2 || trimmedLine.front() != '|') continue;
                if (!std::getline(in, line) || !isTableRule(trimCell(line))) continue;
                inTable = true;
                continue;
            }
            if (trimmedLine.empty() || trimmedLine.front() != '|') break;
            for (const std::string& cell : tableCells(trimmedLine)) {
                const std::string date = leadingDate(cell);
                if (date.empty()) continue;
                out.push_back(date);
                break;
            }
        }
    }
    return out;
}

catalog::Result catalog::renderEscapes()
{
    const manifest::Store::Data& data = manifest::Store::instance().data();

    std::set<std::string> known;
    for (const auto& d : Store::instance().tickets().documents) known.insert(d.id);
    for (const auto& d : Store::instance().requirements().documents) known.insert(d.id);

    std::vector<std::string> columns;
    std::ostringstream rows;
    std::ostringstream warnings;
    bool anyDeclared = false, anyRow = false, anyWarning = false;
    std::size_t total = 0;
    std::string firstPath;

    for (const auto& repo : data.repos) {
        if (repo.docs.escapesAbs.empty()) continue;
        anyDeclared = true;
        if (firstPath.empty()) firstPath = repo.docs.escapes;

        std::ifstream in(repo.docs.escapesAbs);
        if (!in) {
            if (anyWarning) warnings << ",";
            warnings << "{\"code\":\"path_missing\",\"path\":" << jstr(repo.docs.escapes) << "}";
            anyWarning = true;
            continue;
        }

        std::string line;
        bool inTable = false;
        std::vector<std::string> header;
        while (std::getline(in, line)) {
            const std::string trimmedLine = trimCell(line);
            if (!inTable) {
                if (trimmedLine.size() < 2 || trimmedLine.front() != '|') continue;
                header = tableCells(trimmedLine);
                if (!std::getline(in, line) || !isTableRule(trimCell(line))) {
                    header.clear();
                    continue;
                }
                inTable = true;
                if (columns.empty()) columns = header;
                continue;
            }
            if (trimmedLine.empty() || trimmedLine.front() != '|') break;
            const std::vector<std::string> cells = tableCells(trimmedLine);
            if (anyRow) rows << ",";
            rows << "{\"cells\":[";
            for (std::size_t c = 0; c < header.size(); ++c) {
                if (c) rows << ",";
                const std::string text = c < cells.size() ? cells[c] : std::string();
                rows << "{\"column\":" << jstr(header[c]) << ",\"text\":" << jstr(text);
                const std::string id = idNamedIn(text, known);
                if (!id.empty()) rows << ",\"ticket_id\":" << jstr(id);
                rows << "}";
            }
            rows << "]}";
            anyRow = true;
            ++total;
        }
    }

    if (!anyDeclared) {
        return {404, std::string("{\"error\":{\"code\":\"not_found\",\"message\":")
                     + jstr("no declared repository declares `docs.escapes`, so there is no "
                            "ledger to read")
                     + ",\"detail\":{\"key\":\"docs.escapes\"}"
                     + remedy::member(remedy::none()) + "}}"};
    }

    std::ostringstream o;
    o << "{\"columns\":[";
    for (std::size_t c = 0; c < columns.size(); ++c) {
        if (c) o << ",";
        o << jstr(columns[c]);
    }
    o << "],\"rows\":[" << rows.str() << "],\"total\":" << total
      << ",\"path\":" << jstr(firstPath);
    if (anyWarning) o << ",\"warnings\":[" << warnings.str() << "]";
    o << "}";
    return {200, o.str()};
}

catalog::Result catalog::renderWiki()
{
    const manifest::Store::Data& data = manifest::Store::instance().data();

    std::ostringstream sections;
    std::ostringstream warnings;
    bool anyDeclared = false, anySection = false, anyWarning = false;
    std::size_t total = 0;

    for (const auto& repo : data.repos) {
        if (repo.docs.wikiAbs.empty()) continue;
        anyDeclared = true;

        std::error_code ec;
        if (!std::filesystem::is_directory(repo.docs.wikiAbs, ec)) {
            if (anyWarning) warnings << ",";
            warnings << "{\"code\":\"path_missing\",\"path\":" << jstr(repo.docs.wiki) << "}";
            anyWarning = true;
            continue;
        }

        std::vector<std::filesystem::path> files;
        for (const auto& entry : std::filesystem::directory_iterator(repo.docs.wikiAbs, ec)) {
            if (!entry.is_regular_file(ec)) continue;
            const std::string name = entry.path().filename().string();
            if (name.size() < 4 || name.compare(name.size() - 3, 3, ".md") != 0) continue;
            if (name[0] == '.' || name[0] == '_') continue;
            files.push_back(entry.path());
        }
        std::sort(files.begin(), files.end());

        for (const auto& file : files) {
            std::ifstream in(file);
            if (!in) continue;
            std::ostringstream buffer;
            buffer << in.rdbuf();
            const std::string text = buffer.str();

            const std::string stem = file.stem().string();
            std::string title = stem;
            std::ostringstream front;
            bool anyFront = false;
            std::string parseError;
            std::string body = text;

            if (text.rfind("---\n", 0) == 0) {
                const std::size_t close = text.find("\n---", 3);
                if (close == std::string::npos) {
                    parseError = "the frontmatter block is opened and never closed";
                } else {
                    const std::string block = text.substr(4, close - 4);
                    std::size_t eol = text.find('\n', close + 1);
                    body = eol == std::string::npos ? std::string() : text.substr(eol + 1);
                    std::istringstream lines(block);
                    std::string line;
                    while (std::getline(lines, line)) {
                        std::string value;
                        const std::string key = frontLabelValue(line, value);
                        if (key.empty()) continue;
                        if (key == "title") { title = value.empty() ? title : value; continue; }
                        if (anyFront) front << ",";
                        front << "{\"label\":" << jstr(key) << ",\"value\":" << jstr(value) << "}";
                        anyFront = true;
                    }
                }
            }

            if (anySection) sections << ",";
            sections << "{\"id\":" << jstr(stem)
                     << ",\"title\":" << jstr(title)
                     << ",\"path\":" << jstr(repo.docs.wiki + "/" + file.filename().string())
                     << ",\"bytes\":" << text.size()
                     << ",\"front\":[" << front.str() << "]"
                     << ",\"body_markdown\":" << jstr(body);
            if (!parseError.empty()) sections << ",\"parse_error\":" << jstr(parseError);
            sections << "}";
            anySection = true;
            ++total;
        }
    }

    if (!anyDeclared) {
        return {404, std::string("{\"error\":{\"code\":\"not_found\",\"message\":")
                     + jstr("no declared repository declares `docs.wiki`, so there are no pages "
                            "to read")
                     + ",\"detail\":{\"key\":\"docs.wiki\"}"
                     + remedy::member(remedy::none()) + "}}"};
    }

    std::ostringstream o;
    o << "{\"sections\":[" << sections.str() << "],\"total\":" << total;
    if (anyWarning) o << ",\"warnings\":[" << warnings.str() << "]";
    o << "}";
    return {200, o.str()};
}
