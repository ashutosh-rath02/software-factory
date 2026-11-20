#include <app/error/remedy.h>
#include "manager.h"

#include <cstdlib>

#include <app/lane/state.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>
#include <core/http.h>
#include <glog/logging.h>

void api::Manager::registerClockHook_()
{
    const char* on = std::getenv("FACTORY_TEST_HOOKS");
    if (!on || !*on) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: POST /api/_test/clock is registered";

    http::Server::instance().route("POST", "/api/_test/clock",
        [](const http::Request& req, http::Response& resp) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse(req.body, err);
            const manifest::Json* advance = body.find("advance_s");
            if (!err.ok || !advance || !advance->isNumber()) {
                resp.status = 400;
                resp.body = R"({"error":{"code":"bad_request",)"
                            R"("message":"advance_s, in seconds","detail":{})"
                          + remedy::member(remedy::none()) + "}}";
                return;
            }

            lane::state::advanceMs(static_cast<long long>(advance->number() * 1000));
            lane::Store::instance().retimeSilence();

            resp.status = 202;
            resp.body = "{\"accepted\":true,\"offset_s\":"
                      + std::to_string(lane::state::offsetMs() / 1000)
                      + ",\"now\":\"" + lane::state::isoOf(lane::state::nowMs()) + "\"}";
        });
}
