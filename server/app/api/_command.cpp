#include "manager.h"

#include <app/lane/command.h>
#include <app/manifest/json.h>

#include "lane-view.h"
#include "socket.h"
#include "subscription.h"

namespace {

void runCommand(const http::Request& req, http::Responder respond)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    const manifest::Json* which = body.find("which");
    const std::string asked = which && which->isString() ? which->str() : std::string();

    auto emit = [](const lane::command::Event& event) {
        api::Socket::instance().publishCommand(
            lane::command::renderEvent(event),
            api::commandScopes(event.lane, event.ticketId));
    };

    lane::command::start(api::laneNameOf(req), asked, emit,
                         [respond](lane::command::Started started) {
        http::Response resp;
        resp.status = started.status;
        resp.body = started.status >= 400 ? lane::command::renderError(started)
                                          : lane::command::renderStarted(started);
        respond(std::move(resp));
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, RunCommand) { runCommand(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, RunCommandSegments) { runCommand(req, std::move(respond)); }
