#include <app/error/remedy.h>
#include "manager.h"

#include <memory>

#include <app/agent/session.h>
#include <app/manifest/json.h>
#include <app/permissions/rules.h>
#include <app/permissions/store.h>
#include <core/http.h>

namespace {

void refuse(const http::Responder& respond)
{
    http::Response resp;
    resp.status = 404;
    resp.body = R"({"error":{"code":"not_found","message":"no live run holds that token",)"
                R"("detail":{})" + remedy::member(remedy::reloadManifest()) + "}}";
    respond(std::move(resp));
}

void badRequest(const http::Responder& respond)
{
    http::Response resp;
    resp.status = 400;
    resp.body = R"({"error":{"code":"bad_request",)"
                R"("message":"a token, a tool and the tool's input","detail":{})"
              + remedy::member(remedy::none()) + "}}";
    respond(std::move(resp));
}

}

HTTP_ASYNC_HANDLER(api::Manager, PermissionAsk)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    const manifest::Json* token = err.ok ? body.find("token") : nullptr;
    const manifest::Json* tool = err.ok ? body.find("tool") : nullptr;
    const manifest::Json* input = err.ok ? body.find("input") : nullptr;

    if (!token || !token->isString() || token->str().empty()
        || !tool || !tool->isString() || tool->str().empty())
        return badRequest(respond);

    const std::string toolName = tool->str();
    auto argument = std::make_shared<std::string>();
    auto hasArgument = std::make_shared<bool>(false);
    if (input && input->isObject())
        *argument = permissions::argumentOf(toolName, *input, *hasArgument);

    agent::Store::instance().resolveToken(
        token->str(),
        [toolName, argument, hasArgument, respond](agent::Store::TokenHolder who) {
            if (!who.ok) return refuse(respond);

            permissions::Store::Ask ask;
            ask.lane = who.lane;
            ask.tool = toolName;
            ask.argument = *argument;
            ask.hasArgument = *hasArgument;
            ask.inputSummary = *argument;

            permissions::Store::instance().ask(
                ask, [respond](permissions::Store::Decision decision) {
                    http::Response resp;
                    resp.status = 200;
                    resp.body = std::string("{\"decision\":\"")
                              + (decision.allowed ? "allow" : "deny")
                              + "\",\"reason\":\"" + manifest::jsonEscape(decision.reason)
                              + "\",\"asked\":" + (decision.asked ? "true" : "false") + "}";
                    respond(std::move(resp));
                });
        });
}
