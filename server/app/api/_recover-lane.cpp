#include "manager.h"

#include <app/agent/launch.h>
#include <app/agent/session.h>
#include <app/lane/store.h>
#include <app/manifest/store.h>

#include "lane-view.h"
#include "run-view.h"

namespace {

void answer(const lane::Store::Outcome& out, const http::Responder& respond)
{
    http::Response resp;
    resp.status = out.status;
    resp.body = out.status >= 400 ? lane::renderError(out) : lane::renderRecover(out);
    respond(std::move(resp));
}

void recoverLane(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);
    lane::Store::instance().exists(name, [name, respond](bool exists) {
        if (!exists) return respond(api::noSuchLane(name));

        agent::Store::instance().kill(name, 2, [name, respond](agent::Store::Answer) {
            const manifest::Store::Data& data = manifest::Store::instance().data();
            const agent::launch::ConfigSeed seed =
                agent::launch::configSeed(data.agent, data.root);
            lane::Store::instance().recover(name, seed.borrowed,
                                            [respond](lane::Store::Outcome out) { answer(out, respond); });
        });
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, RecoverLane) { recoverLane(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, RecoverLaneSegments) { recoverLane(req, std::move(respond)); }
