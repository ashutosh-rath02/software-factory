#include "manager.h"

#include <app/lane/store.h>

#include "lane-view.h"

namespace {

void releaseLane(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);
    auto force = req.params.find("force");
    const bool forced = force != req.params.end()
                     && (force->second == "true" || force->second == "1");

    auto discard = req.params.find("discard_ignored");
    const bool discardIgnored = discard != req.params.end()
                             && (discard->second == "true" || discard->second == "1");

    lane::Store::instance().release(name, forced, discardIgnored, [respond](lane::Store::Outcome out) {
        http::Response resp;
        resp.status = out.status;
        resp.body = out.status >= 400 ? lane::renderError(out) : lane::renderRelease(out);
        respond(std::move(resp));
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, ReleaseLane) { releaseLane(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, ReleaseLaneSegments) { releaseLane(req, std::move(respond)); }
