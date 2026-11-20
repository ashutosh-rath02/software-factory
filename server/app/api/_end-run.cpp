#include "manager.h"

#include <app/agent/session.h>
#include <app/lane/store.h>

#include "lane-view.h"
#include "run-view.h"

namespace {

void endRun(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);
    lane::Store::instance().exists(name, [name, respond](bool exists) {
        if (!exists) return respond(api::noSuchLane(name));
        agent::Store::instance().end(name, [respond](agent::Store::Answer answer) {
            respond(api::runResponse(answer));
        });
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, EndRun) { endRun(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, EndRunSegments) { endRun(req, std::move(respond)); }
