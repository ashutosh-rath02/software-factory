#include <app/error/remedy.h>
#include "manager.h"

#include <cstdlib>

#include <app/manifest/json.h>
#include <app/permissions/store.h>
#include <glog/logging.h>

void api::Manager::registerPermissionHook_()
{
    const char* on = std::getenv("FACTORY_TEST_HOOKS");
    if (!on || !*on) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: POST /api/_test/permission-ask is registered";

    http::Server::instance().routeAsync("POST", "/api/_test/permission-ask",
        [](const http::Request& req, http::Responder respond) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse(req.body, err);
            const manifest::Json* lane = err.ok ? body.find("lane") : nullptr;
            const manifest::Json* tool = err.ok ? body.find("tool") : nullptr;
            const manifest::Json* input = err.ok ? body.find("input") : nullptr;
            if (!lane || !lane->isString() || !tool || !tool->isString()) {
                http::Response bad;
                bad.status = 400;
                bad.body = R"({"error":{"code":"bad_request","message":"lane and tool")"
                         + remedy::member(remedy::none()) + "}}";
                respond(std::move(bad));
                return;
            }

            permissions::Store::Ask ask;
            ask.lane = lane->str();
            ask.tool = tool->str();
            if (input && input->isObject())
                ask.argument = permissions::argumentOf(ask.tool, *input, ask.hasArgument);

            permissions::Store::instance().ask(ask, [respond](permissions::Store::Decision decision) {
                std::string out = std::string("{\"allowed\":")
                    + (decision.allowed ? "true" : "false")
                    + ",\"asked\":" + (decision.asked ? "true" : "false")
                    + ",\"reason\":\"" + manifest::jsonEscape(decision.reason) + "\"";
                if (!decision.decision.empty())
                    out += ",\"decision\":\"" + manifest::jsonEscape(decision.decision) + "\"";
                if (!decision.ruleId.empty())
                    out += ",\"rule_id\":\"" + manifest::jsonEscape(decision.ruleId) + "\"";
                if (!decision.requestId.empty())
                    out += ",\"request_id\":\"" + manifest::jsonEscape(decision.requestId) + "\"";
                out += "}";

                http::Response resp;
                resp.status = 200;
                resp.body = out;
                respond(std::move(resp));
            });
        });
}
