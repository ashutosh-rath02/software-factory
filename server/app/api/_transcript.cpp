#include <app/error/remedy.h>
#include "manager.h"

#include <cstdlib>

#include <app/agent/history.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <core/worker.h>

namespace {

long long intQuery(const http::Request& req, const std::string& key, long long fallback)
{
    auto found = req.params.find(key);
    if (found == req.params.end() || found->second.empty()) return fallback;
    char* end = nullptr;
    const long long value = std::strtoll(found->second.c_str(), &end, 10);
    if (end == found->second.c_str() || value < 0) return fallback;
    return value;
}

}

HTTP_ASYNC_HANDLER(api::Manager, RunTranscript)
{
    const std::string runId = req.params.count("run_id") ? req.params.at("run_id") : std::string();
    const long long afterSeq = intQuery(req, "after_seq", 0);
    long long limit = intQuery(req, "limit", 200);
    if (limit <= 0) limit = 200;
    if (limit > 1000) limit = 1000;

    core::Worker::instance().post([runId, afterSeq, limit, respond] {
        const agent::history::Replay out = agent::history::transcriptOf(
            manifest::Store::instance().data().ledgers(), runId, afterSeq, static_cast<int>(limit));

        http::Response resp;
        if (!out.found) {
            resp.status = 404;
            resp.body = std::string(R"({"error":{"code":"not_found",)")
                      + R"("message":"no transcript was recorded for that run, or it has )"
                      + R"(been cleaned","detail":{"path":")"
                      + manifest::jsonEscape(out.path) + R"("})"
                      + remedy::member(remedy::none()) + "}}";
        } else {
            resp.body = out.body;
        }
        respond(std::move(resp));
    });
}
