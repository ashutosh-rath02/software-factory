#include "manager.h"

#include <app/agent/session.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>

#include "lane-view.h"
#include "run-view.h"

namespace {

void killRun(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);
    lane::Store::instance().exists(name, [name, body = req.body, respond](bool exists) {
        if (!exists) return respond(api::noSuchLane(name));

        manifest::Json::Error err;
        const manifest::Json json = manifest::Json::parse(body, err);
        int graceS = 5;
        if (err.ok && json.isObject()) {
            if (const manifest::Json* v = json.find("grace_s"); v && v->isNumber())
                graceS = static_cast<int>(v->number());
        }

        agent::Store::instance().kill(name, graceS, [respond](agent::Store::Answer answer) {
            respond(api::runResponse(answer));
        });
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, KillRun) { killRun(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, KillRunSegments) { killRun(req, std::move(respond)); }
