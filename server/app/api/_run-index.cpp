#include <app/error/remedy.h>
#include "manager.h"

#include <cstdlib>
#include <ctime>
#include <iomanip>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <time.h>
#include <vector>

#include <app/agent/history.h>
#include <app/catalog/store.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <core/worker.h>

#include "lane-view.h"

using manifest::jstr;
static std::string number(double v)
{
    if (v == static_cast<long long>(v)) return std::to_string(static_cast<long long>(v));
    std::ostringstream o;
    o << std::setprecision(12) << v;
    return o.str();
}

static long long queryInt(const http::Request& req, const std::string& key, long long fallback)
{
    const auto found = req.params.find(key);
    if (found == req.params.end() || found->second.empty()) return fallback;
    char* end = nullptr;
    const long long value = std::strtoll(found->second.c_str(), &end, 10);
    if (end == found->second.c_str() || value <= 0) return fallback;
    return value;
}

static long long epochOf(const std::string& iso)
{
    std::tm parsed {};
    if (!::strptime(iso.c_str(), "%Y-%m-%dT%H:%M:%SZ", &parsed)) return -1;
    return static_cast<long long>(::timegm(&parsed));
}

static std::string titleOf(std::map<std::string, std::string>& seen, const std::string& ticketId)
{
    const auto known = seen.find(ticketId);
    if (known != seen.end()) return known->second;
    catalog::Document doc;
    const bool found = catalog::Store::instance().ticketById(ticketId, doc);
    return seen.emplace(ticketId, found ? doc.title : std::string()).first->second;
}

HTTP_ASYNC_HANDLER(api::Manager, RunIndex)
{
    const std::string before =
        req.params.count("before") ? req.params.at("before") : std::string();
    long long limit = queryInt(req, "limit", 100);
    if (limit > 500) limit = 500;

    core::Worker::instance().post(
        [before, limit, respond] {
            const std::vector<agent::history::Meta> runs =
                agent::history::all(manifest::Store::instance().data().ledgers());

            std::size_t from = 0;
            if (!before.empty()) {
                bool found = false;
                for (std::size_t i = 0; i < runs.size(); ++i) {
                    if (runs[i].runId != before) continue;
                    from = i + 1;
                    found = true;
                    break;
                }
                if (!found) {
                    http::Response bad;
                    bad.status = 400;
                    bad.body = std::string(R"({"error":{"code":"bad_request",)")
                             + R"("message":"the `before` cursor names a run this repository has )"
                             + R"(no record of","detail":{"param":"before","before":)"
                             + jstr(before) + "}"
                             + remedy::member(remedy::none()) + "}}";
                    return respond(std::move(bad));
                }
            }

            std::map<std::string, std::string> titles;
            const long long now = static_cast<long long>(std::time(nullptr));

            std::ostringstream o;
            o << "{\"runs\":[";
            bool first = true;
            for (std::size_t i = from;
                 i < runs.size() && static_cast<long long>(i - from) < limit; ++i) {
                const agent::history::Meta& meta = runs[i];
                const bool live = meta.endedIso.empty();
                long long durationS = meta.durationS;
                if (live) {
                    const long long startedAt = epochOf(meta.startedIso);
                    if (startedAt >= 0 && now > startedAt) durationS = now - startedAt;
                }
                if (!first) o << ",";
                first = false;
                o << "{\"run_id\":" << jstr(meta.runId)
                  << ",\"n\":" << meta.n
                  << ",\"kind\":" << jstr(api::runKind(meta.role))
                  << ",\"ticket_id\":" << jstr(meta.ticketId)
                  << ",\"ticket_title\":" << jstr(titleOf(titles, meta.ticketId))
                  << ",\"lane\":" << jstr(meta.lane)
                  << ",\"stage\":0"
                  << ",\"outcome\":" << jstr(meta.outcome)
                  << ",\"started_iso\":" << jstr(meta.startedIso);
                if (!live) o << ",\"ended_iso\":" << jstr(meta.endedIso);
                o << ",\"duration_s\":" << durationS
                  << ",\"cost_usd\":" << number(meta.costUsd)
                  << ",\"agent\":" << jstr(meta.agentProfile)
                  << ",\"driver\":" << jstr(meta.driver)
                  << ",\"model\":" << jstr(meta.model)
                  << ",\"tree\":" << manifest::Store::treeJson(meta.tree)
                  << ",\"effort\":" << (meta.hasEffort ? jstr(meta.effort)
                                                        : std::string("null"))
                  << ",\"cost_known\":" << (meta.driver == "codex" ? "false" : "true")
                  << ",\"session_id\":" << jstr(meta.sessionId) << "}";
            }
            o << "],\"total\":" << runs.size() << ",\"committed\":false}";

            http::Response resp;
            resp.body = o.str();
            respond(std::move(resp));
        });
}
