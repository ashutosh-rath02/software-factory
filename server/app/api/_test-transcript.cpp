#include <app/error/remedy.h>
#include "manager.h"

#include <cstdlib>
#include <sstream>

#include <app/api/socket.h>
#include <app/manifest/json.h>
#include <core/http.h>
#include <glog/logging.h>

void api::Manager::registerTestHook_()
{
    const char* on = std::getenv("FACTORY_TEST_HOOKS");
    if (!on || !*on) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: POST /api/_test/transcript is registered";

    http::Server::instance().routeAsync("POST", "/api/_test/transcript",
        [](const http::Request& req, http::Responder respond) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse(req.body, err);
            const manifest::Json* lane = body.find("lane");
            const manifest::Json* run = body.find("run_id");
            const manifest::Json* entry = body.find("entry");
            if (!err.ok || !lane || !lane->isString() || !run || !run->isString()
                || !entry || !entry->isObject()) {
                http::Response bad;
                bad.status = 400;
                bad.body = R"({"error":{"code":"bad_request",)"
                           R"("message":"lane, run_id and an entry object")"
                         + remedy::member(remedy::none()) + "}}";
                respond(std::move(bad));
                return;
            }

            std::ostringstream entryJson;
            entryJson << "{";
            bool first = true;
            for (const auto& [key, value] : entry->members()) {
                if (!first) entryJson << ",";
                first = false;
                entryJson << "\"" << manifest::jsonEscape(key) << "\":";
                if (value.isString()) entryJson << "\"" << manifest::jsonEscape(value.str())
                                                << "\"";
                else if (value.isBool()) entryJson << (value.boolean() ? "true" : "false");
                else if (value.isNumber()) entryJson << value.number();
                else entryJson << "null";
            }
            entryJson << "}";

            api::Socket::instance().publishTranscript(
                lane->str(), run->str(), entryJson.str(),
                [respond](long long seq) {
                    http::Response ok;
                    ok.status = 202;
                    ok.body = "{\"accepted\":true,\"seq\":" + std::to_string(seq) + "}";
                    respond(std::move(ok));
                });
        });
}
