#include "history.h"

#include <algorithm>
#include <dirent.h>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <chrono>

#include <glog/logging.h>

#include <app/manifest/json.h>

namespace {

using manifest::Json;

using manifest::jstr;
std::string num(double v)
{
    if (v == static_cast<long long>(v)) return std::to_string(static_cast<long long>(v));
    std::ostringstream o;
    o << std::setprecision(12) << v;
    return o.str();
}

std::string usageJson(const agent::history::Usage& usage)
{
    const double effective = static_cast<double>(usage.inputTokens)
                           - static_cast<double>(usage.cachedInputTokens)
                           + 0.1 * static_cast<double>(usage.cachedInputTokens)
                           + static_cast<double>(usage.outputTokens);
    std::ostringstream o;
    o << "{\"input_tokens\":" << usage.inputTokens
      << ",\"cached_input_tokens\":" << usage.cachedInputTokens
      << ",\"output_tokens\":" << usage.outputTokens
      << ",\"reasoning_output_tokens\":" << usage.reasoningOutputTokens
      << ",\"effective_tokens\":" << num(effective) << "}";
    return o.str();
}

std::string dirOf(const std::string& runsDir, const std::string& runId)
{
    return runsDir + "/" + runId;
}

void ensure(const std::string& path)
{
    ::mkdir(path.c_str(), 0755);
}

std::string slurp(const std::string& path, bool& ok)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) { ok = false; return {}; }
    std::ostringstream buf;
    buf << in.rdbuf();
    ok = true;
    return buf.str();
}

std::string stringAt(const Json& v, const std::string& key)
{
    const Json* found = v.isObject() ? v.find(key) : nullptr;
    return found && found->isString() ? found->str() : std::string();
}

double numberAt(const Json& v, const std::string& key)
{
    const Json* found = v.isObject() ? v.find(key) : nullptr;
    return found && found->isNumber() ? found->number() : 0;
}

long long epochOfIso(const std::string& iso)
{
    std::tm parsed {};
    if (iso.empty() || !::strptime(iso.c_str(), "%Y-%m-%dT%H:%M:%SZ", &parsed)) return -1;
    return static_cast<long long>(::timegm(&parsed));
}

std::string renderRow(const agent::history::Meta& meta)
{
    std::ostringstream o;
    o << "{\"run_id\":" << jstr(meta.runId)
      << ",\"agent\":" << jstr(meta.agentProfile)
      << ",\"driver\":" << jstr(meta.driver)
      << ",\"model\":" << jstr(meta.model)
      << ",\"tree\":" << manifest::Store::treeJson(meta.tree)
      << ",\"effort\":" << (meta.hasEffort ? jstr(meta.effort) : std::string("null"))
      << ",\"cost_known\":" << (meta.driver == "codex" ? "false" : "true")
      << ",\"n\":" << meta.n
      << ",\"started_iso\":" << jstr(meta.startedIso)
      << ",\"ended_iso\":" << (meta.endedIso.empty() ? std::string("null") : jstr(meta.endedIso))
      << ",\"outcome\":" << jstr(meta.outcome)
      << ",\"stage\":0"
      << ",\"duration_s\":" << meta.durationS
      << ",\"cost_usd\":" << num(meta.costUsd)
      << ",\"lane\":" << jstr(meta.lane)
      << ",\"session_id\":" << jstr(meta.sessionId);
    if (meta.hasUsage) o << ",\"usage\":" << usageJson(meta.usage);
    o << "}";
    return o.str();
}

std::string rawValueAt(const std::string& text, const std::string& key)
{
    const std::string needle = "\"" + key + "\":";
    const std::size_t at = text.find(needle);
    if (at == std::string::npos) return {};
    std::size_t i = at + needle.size();
    while (i < text.size() && std::isspace(static_cast<unsigned char>(text[i]))) ++i;
    if (i >= text.size()) return {};
    if (text.compare(i, 4, "null") == 0) return {};
    const std::size_t start = i;
    int depth = 0;
    bool inString = false, escaped = false;
    for (; i < text.size(); ++i) {
        const char c = text[i];
        if (inString) {
            if (escaped) escaped = false;
            else if (c == '\\') escaped = true;
            else if (c == '"') inString = false;
            continue;
        }
        if (c == '"') { inString = true; continue; }
        if (c == '{' || c == '[') ++depth;
        else if (c == '}' || c == ']') { if (--depth == 0) return text.substr(start, i - start + 1); }
        else if (depth == 0 && (c == ',' || c == '}')) return text.substr(start, i - start);
    }
    return {};
}

bool readMeta(const std::string& path, agent::history::Meta& out)
{
    bool ok = false;
    const std::string text = slurp(path, ok);
    if (!ok) return false;
    Json::Error err;
    const Json meta = Json::parse(text, err);
    if (!err.ok || !meta.isObject()) return false;

    out.runId = stringAt(meta, "run_id");
    if (out.runId.empty()) return false;
    out.lane = stringAt(meta, "lane");
    out.ticketId = stringAt(meta, "ticket_id");
    out.role = stringAt(meta, "role");
    out.sessionId = stringAt(meta, "session_id");
    out.agentProfile = stringAt(meta, "agent");
    out.driver = stringAt(meta, "driver");
    out.model = stringAt(meta, "model");
    out.tree.name = "off";
    if (const Json* tree = meta.find("tree"); tree && tree->isObject()) {
        out.tree.name = tree->stringOr("name", "off");
        out.tree.decider = tree->stringOr("decider", "rules");
        out.tree.continuation = stringAt(*tree, "continuation");
        if (const Json* compact = tree->find("compact_on_stage"); compact && compact->isBool()) {
            out.tree.compactOnStage = compact->boolean();
            out.tree.hasCompactOnStage = true;
        }
        out.tree.tokenBudget = static_cast<long long>(numberAt(*tree, "token_budget"));
        if (const Json* agents = tree->find("agents"); agents && agents->isArray())
            for (const auto& agent : agents->items())
                if (agent.isString()) out.tree.agents.push_back(agent.str());
    }
    if (const Json* v = meta.find("effort"); v && v->isString() && !v->str().empty()) {
        out.effort = v->str();
        out.hasEffort = true;
    }
    out.branch = stringAt(meta, "branch");
    out.branchTip = stringAt(meta, "branch_tip");
    out.startedIso = stringAt(meta, "started_iso");
    out.endedIso = stringAt(meta, "ended_iso");
    out.outcome = stringAt(meta, "outcome");
    out.n = static_cast<int>(numberAt(meta, "n"));
    out.durationS = static_cast<long long>(numberAt(meta, "duration_s"));
    out.costUsd = numberAt(meta, "cost_usd");
    if (const Json* usage = meta.find("usage"); usage && usage->isObject()) {
        out.usage.inputTokens = static_cast<long long>(numberAt(*usage, "input_tokens"));
        out.usage.cachedInputTokens = static_cast<long long>(numberAt(*usage, "cached_input_tokens"));
        out.usage.outputTokens = static_cast<long long>(numberAt(*usage, "output_tokens"));
        out.usage.reasoningOutputTokens = static_cast<long long>(numberAt(*usage, "reasoning_output_tokens"));
        out.hasUsage = true;
    }
    out.turns = static_cast<int>(numberAt(meta, "turns"));

    if (const Json* v = meta.find("exit_code"); v && v->isNumber())
        out.exitCode = static_cast<int>(v->number());
    if (const Json* v = meta.find("signal"); v && v->isNumber())
        out.signal = static_cast<int>(v->number());
    out.rateLimit = rawValueAt(text, "rate_limit");
    if (const Json* v = meta.find("unmapped"); v && v->isObject())
        for (const auto& [key, count] : v->members())
            if (count.isNumber()) out.unmapped[key] = static_cast<int>(count.number());
    out.settledFrom = stringAt(meta, "settled_from");
    return true;
}

std::vector<agent::history::Meta> readAll(const std::string& runsDir)
{
    std::vector<agent::history::Meta> out;
    DIR* dir = ::opendir(runsDir.c_str());
    if (!dir) return out;
    while (dirent* entry = ::readdir(dir)) {
        const std::string name = entry->d_name;
        if (name == "." || name == "..") continue;
        agent::history::Meta meta;
        if (readMeta(runsDir + "/" + name + "/meta.json", meta)) out.push_back(std::move(meta));
    }
    ::closedir(dir);

    std::sort(out.begin(), out.end(), [](const auto& a, const auto& b) {
        if (a.startedIso != b.startedIso) return a.startedIso > b.startedIso;
        return a.n > b.n;
    });
    return out;
}

}

void agent::history::write(const std::string& runsDir, const Meta& source)
{
    ensure(runsDir);
    const std::string dir = dirOf(runsDir, source.runId);
    ensure(dir);

    Meta current = source;
    Meta recorded;
    if (readMeta(dir + "/meta.json", recorded)) {
        if (current.branch.empty()) current.branch = recorded.branch;
        if (current.branchTip.empty()) current.branchTip = recorded.branchTip;
        if (!current.hasUsage && recorded.hasUsage) {
            current.usage = recorded.usage;
            current.hasUsage = true;
        }
    }
    const Meta& meta = current;

    std::ostringstream o;
    o << "{\"run_id\":" << jstr(current.runId)
      << ",\"lane\":" << jstr(meta.lane)
      << ",\"ticket_id\":" << jstr(meta.ticketId)
      << ",\"agent\":" << jstr(meta.agentProfile)
      << ",\"driver\":" << jstr(meta.driver)
      << ",\"model\":" << jstr(meta.model)
      << ",\"tree\":" << manifest::Store::treeJson(meta.tree)
      << ",\"effort\":" << (meta.hasEffort ? jstr(meta.effort) : std::string("null"))
      << ",\"session_id\":" << jstr(meta.sessionId)
      << ",\"n\":" << meta.n
      << ",\"started_iso\":" << jstr(meta.startedIso)
      << ",\"ended_iso\":" << (meta.endedIso.empty() ? std::string("null") : jstr(meta.endedIso))
      << ",\"outcome\":" << jstr(meta.outcome)
      << ",\"duration_s\":" << meta.durationS
      << ",\"cost_usd\":" << num(meta.costUsd)
      << ",\"turns\":" << meta.turns
      << ",\"exit_code\":" << (meta.exitCode < 0 ? std::string("null")
                                                 : std::to_string(meta.exitCode))
      << ",\"signal\":" << (meta.signal > 0 ? std::to_string(meta.signal) : std::string("null"));
    if (!meta.role.empty()) o << ",\"role\":" << jstr(meta.role);
    if (!meta.branch.empty()) o << ",\"branch\":" << jstr(meta.branch);
    if (!meta.branchTip.empty()) o << ",\"branch_tip\":" << jstr(meta.branchTip);
    if (!meta.rateLimit.empty()) o << ",\"rate_limit\":" << meta.rateLimit;
    if (meta.hasUsage) o << ",\"usage\":" << usageJson(meta.usage);
    if (!meta.settledFrom.empty()) o << ",\"settled_from\":" << jstr(meta.settledFrom);
    o << ",\"unmapped\":{";
    bool first = true;
    for (const auto& [name, count] : meta.unmapped) {
        if (!first) o << ",";
        first = false;
        o << jstr(name) << ":" << count;
    }
    o << "}}";

    const std::string finalPath = dir + "/meta.json";
    const std::string tmpPath = finalPath + ".tmp";
    {
        std::ofstream out(tmpPath, std::ios::binary | std::ios::trunc);
        if (!out) return;
        out << o.str() << "\n";
    }
    ::rename(tmpPath.c_str(), finalPath.c_str());
}

void agent::history::noteRef(const std::string& runsDir, const std::string& runId,
                             const std::string& branch, const std::string& tip)
{
    if (runsDir.empty() || runId.empty()) return;
    const std::string path = dirOf(runsDir, runId) + "/meta.json";
    Meta meta;
    if (!readMeta(path, meta)) return;
    if (meta.branch == branch && meta.branchTip == tip) return;
    meta.branch = branch;
    meta.branchTip = tip;
    write(runsDir, meta);
}

void agent::history::append(const std::string& runsDir, const std::string& runId,
                            const std::vector<std::string>& lines)
{
    if (lines.empty()) return;
    ensure(runsDir);
    const std::string dir = dirOf(runsDir, runId);
    ensure(dir);
    std::ofstream out(dir + "/transcript.jsonl", std::ios::binary | std::ios::app);
    if (!out) return;
    for (const auto& line : lines) out << line << "\n";
}

std::vector<agent::history::Row> agent::history::forTicket(const std::string& runsDir,
                                                           const std::string& ticketId)
{
    std::vector<Row> out;
    for (auto& meta : readAll(runsDir)) {
        if (!ticketId.empty() && meta.ticketId != ticketId) continue;
        Row row;
        row.json = renderRow(meta);
        row.meta = std::move(meta);
        out.push_back(std::move(row));
    }
    return out;
}

std::vector<agent::history::Meta> agent::history::all(const std::string& runsDir)
{
    return readAll(runsDir);
}

agent::history::Totals agent::history::totalsFor(const std::string& runsDir,
                                                 const std::string& ticketId)
{
    Totals out;
    for (const auto& row : forTicket(runsDir, ticketId)) {
        ++out.count;
        out.durationS += row.meta.durationS;
        out.costUsd += row.meta.costUsd;
    }
    return out;
}

agent::history::Replay agent::history::transcriptOf(const std::string& runsDir,
                                                    const std::string& runId,
                                                    long long afterSeq, int limit)
{
    Replay out;
    out.path = dirOf(runsDir, runId) + "/transcript.jsonl";

    bool ok = false;
    const std::string text = slurp(out.path, ok);
    if (!ok) return out;
    out.found = true;

    std::istringstream in(text);
    std::string line;
    std::vector<std::string> kept;
    long long last = afterSeq;
    bool complete = true;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        Json::Error err;
        const Json entry = Json::parse(line, err);
        if (!err.ok || !entry.isObject()) continue;
        const long long seq = static_cast<long long>(numberAt(entry, "seq"));
        if (seq <= afterSeq) continue;
        if (static_cast<int>(kept.size()) >= limit) { complete = false; break; }
        kept.push_back(line);
        last = seq;
    }

    std::ostringstream o;
    o << "{\"run_id\":" << jstr(runId) << ",\"entries\":[";
    bool first = true;
    for (const auto& k : kept) {
        if (!first) o << ",";
        first = false;
        o << k;
    }
    o << "],\"next_after_seq\":" << last << ",\"complete\":" << (complete ? "true" : "false")
      << "}";
    out.body = o.str();
    return out;
}

std::vector<agent::history::Activity> agent::history::activity(const std::string& runsDir)
{
    std::vector<Activity> out;
    for (const Meta& meta : readAll(runsDir)) {
        Activity row;
        row.runId = meta.runId;
        row.ticketId = meta.ticketId;
        row.role = meta.role;
        row.startedIso = meta.startedIso;
        row.startedAt = epochOfIso(meta.startedIso);

        bool ok = false;
        const std::string body = slurp(dirOf(runsDir, meta.runId) + "/transcript.jsonl", ok);
        if (!ok) { out.push_back(std::move(row)); continue; }

        struct Entry {
            long long seq = 0;
            long long at = 0;
            std::string kind;
            std::string stage;
            long long durationS = 0;
        };
        std::vector<Entry> entries;
        std::istringstream in(body);
        std::string line;
        while (std::getline(in, line)) {
            if (line.empty()) continue;
            Json::Error err;
            const Json entry = Json::parse(line, err);
            if (!err.ok || !entry.isObject()) continue;
            const std::string iso = stringAt(entry, "ts");
            if (iso.empty()) continue;
            Entry e;
            e.seq = static_cast<long long>(numberAt(entry, "seq"));
            e.at = epochOfIso(iso);
            e.kind = stringAt(entry, "kind");
            e.stage = stringAt(entry, "stage");
            e.durationS = static_cast<long long>(numberAt(entry, "duration_s"));
            entries.push_back(std::move(e));
        }
        std::stable_sort(entries.begin(), entries.end(),
                         [](const Entry& a, const Entry& b) { return a.seq < b.seq; });

        long long lastWorkAt = -1;
        std::vector<const Entry*> stages;
        for (const Entry& e : entries) {
            if (e.kind == "turn") { row.activeS += e.durationS; continue; }
            if (e.kind == "silence") continue;
            lastWorkAt = e.at;
            if (e.kind == "stage" && !e.stage.empty()) stages.push_back(&e);
        }
        for (std::size_t i = 0; i < stages.size(); ++i) {
            const long long until = i + 1 < stages.size() ? stages[i + 1]->at : lastWorkAt;
            if (until < 0) continue;
            row.stageSeconds[stages[i]->stage] += until - stages[i]->at;
            auto seen = row.firstStageAt.find(stages[i]->stage);
            if (seen == row.firstStageAt.end() || stages[i]->at < seen->second)
                row.firstStageAt[stages[i]->stage] = stages[i]->at;
        }
        out.push_back(std::move(row));
    }
    return out;
}

std::map<std::string, std::vector<agent::history::Reported>>
agent::history::stagesByTicket(const std::string& runsDir)
{
    std::vector<Meta> runs = readAll(runsDir);
    std::sort(runs.begin(), runs.end(), [](const Meta& a, const Meta& b) {
        if (a.startedIso != b.startedIso) return a.startedIso < b.startedIso;
        return a.n < b.n;
    });

    std::map<std::string, std::vector<Reported>> out;
    for (const Meta& meta : runs) {
        if (meta.ticketId.empty() || meta.runId.empty()) continue;

        bool ok = false;
        const std::string body = slurp(dirOf(runsDir, meta.runId) + "/transcript.jsonl", ok);
        if (!ok) continue;

        std::istringstream in(body);
        std::string line;
        int reported = 0;
        while (std::getline(in, line)) {
            if (line.find("\"kind\":\"stage\"") == std::string::npos) continue;

            Json::Error err;
            const Json entry = Json::parse(line, err);
            if (!err.ok || !entry.isObject()) continue;
            if (stringAt(entry, "kind") != "stage") continue;

            Reported report;
            report.name = stringAt(entry, "stage");
            if (report.name.empty()) continue;
            if (const Json* r = entry.find("round"); r && r->isNumber()) {
                report.hasRound = true;
                report.round = static_cast<int>(r->number());
            }
            if (const Json* o = entry.find("of"); o && o->isNumber()) {
                report.hasOf = true;
                report.of = static_cast<int>(o->number());
            }
            if (const long long at = epochOfIso(stringAt(entry, "ts")); at >= 0)
                report.atMs = at * 1000;
            report.runId = meta.runId;
            out[meta.ticketId].push_back(std::move(report));
            ++reported;
        }
        if (reported > 0)
            if (const long long ended = epochOfIso(meta.endedIso); ended >= 0)
                out[meta.ticketId].back().endMs = ended * 1000;
    }
    return out;
}

std::map<std::string, std::string>
agent::history::lastOutcomeByTicket(const std::string& runsDir)
{
    std::map<std::string, std::string> out;
    std::map<std::string, std::string> newest;
    for (const Meta& meta : readAll(runsDir)) {
        if (meta.ticketId.empty()) continue;
        if (meta.outcome.empty()) continue;
        auto seen = newest.find(meta.ticketId);
        if (seen != newest.end() && meta.startedIso < seen->second) continue;
        newest[meta.ticketId] = meta.startedIso;
        out[meta.ticketId] = meta.outcome;
    }
    return out;
}

int agent::history::settleOrphans(const std::string& runsDir,
                                  const std::set<std::string>& liveRunIds)
{
    const auto settleTime = [] {
        const std::time_t t = std::chrono::system_clock::to_time_t(
            std::chrono::system_clock::now());
        std::tm tm {};
        gmtime_r(&t, &tm);
        std::ostringstream o;
        o << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
        return o.str();
    };
    int settled = 0;
    for (Meta meta : readAll(runsDir)) {
        if (meta.outcome != "running") continue;
        if (liveRunIds.count(meta.runId)) continue;
        meta.settledFrom = meta.outcome;
        meta.outcome = "orphaned";
        meta.endedIso = settleTime();
        write(runsDir, meta);
        ++settled;
        LOG(WARNING) << "settled orphaned run " << meta.runId << " (" << meta.ticketId
                     << "): its record said `running` and no lane holds it";
    }
    return settled;
}

std::vector<agent::history::Row> agent::history::forTicket(const std::vector<std::string>& ledgers,
                                                           const std::string& ticketId)
{
    std::vector<Row> out;
    for (const auto& dir : ledgers) {
        auto rows = forTicket(dir, ticketId);
        out.insert(out.end(), rows.begin(), rows.end());
    }
    std::stable_sort(out.begin(), out.end(),
                     [](const Row& a, const Row& b) { return a.meta.startedIso > b.meta.startedIso; });
    return out;
}

std::vector<agent::history::Meta> agent::history::all(const std::vector<std::string>& ledgers)
{
    std::vector<Meta> out;
    for (const auto& dir : ledgers) {
        auto rows = all(dir);
        out.insert(out.end(), rows.begin(), rows.end());
    }
    std::stable_sort(out.begin(), out.end(),
                     [](const Meta& a, const Meta& b) { return a.startedIso > b.startedIso; });
    return out;
}

agent::history::Totals agent::history::totalsFor(const std::vector<std::string>& ledgers,
                                                 const std::string& ticketId)
{
    Totals out;
    for (const auto& dir : ledgers) {
        const Totals t = totalsFor(dir, ticketId);
        out.count += t.count;
        out.durationS += t.durationS;
        out.costUsd += t.costUsd;
    }
    return out;
}

agent::history::Replay agent::history::transcriptOf(const std::vector<std::string>& ledgers,
                                                    const std::string& runId, long long afterSeq,
                                                    int limit)
{
    Replay fallback;
    for (const auto& dir : ledgers) {
        Replay r = transcriptOf(dir, runId, afterSeq, limit);
        if (r.found) return r;
        if (fallback.path.empty()) fallback = r;
        for (const Meta& meta : all(dir))
            if (meta.runId == runId) return r;
    }
    return fallback;
}

std::vector<agent::history::Activity> agent::history::activity(const std::vector<std::string>& ledgers)
{
    std::vector<Activity> out;
    for (const auto& dir : ledgers) {
        auto rows = activity(dir);
        out.insert(out.end(), rows.begin(), rows.end());
    }
    return out;
}

std::map<std::string, std::vector<agent::history::Reported>>
agent::history::stagesByTicket(const std::vector<std::string>& ledgers)
{
    std::map<std::string, std::vector<Reported>> out;
    for (const auto& dir : ledgers)
        for (auto& [ticketId, reports] : stagesByTicket(dir)) {
            auto& into = out[ticketId];
            into.insert(into.end(), reports.begin(), reports.end());
        }
    return out;
}

std::map<std::string, std::string>
agent::history::lastOutcomeByTicket(const std::vector<std::string>& ledgers)
{
    std::map<std::string, std::string> out;
    for (const auto& dir : ledgers)
        for (auto& [ticketId, outcome] : lastOutcomeByTicket(dir))
            out.emplace(ticketId, outcome);
    return out;
}
