#include <app/error/remedy.h>
#include "manager.h"

#include <app/lane/store.h>
#include <app/manifest/json.h>
#include <core/http.h>
#include <glog/logging.h>

namespace {

std::string jstring(const std::string& s) { return "\"" + manifest::jsonEscape(s) + "\""; }
}

void api::Manager::registerLaneHooks_()
{
    if (!lane::Store::instance().testHooksEnabled()) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: POST /api/_test/lane-process is registered";

    http::Server::instance().routeAsync("POST", "/api/_test/lane-process",
        [](const http::Request& req, http::Responder respond) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse(req.body, err);
            const manifest::Json* lane = body.find("lane");
            const manifest::Json* command = body.find("command");
            if (!err.ok || !lane || !lane->isString() || !command || !command->isString()) {
                http::Response bad;
                bad.status = 400;
                bad.body = R"({"error":{"code":"bad_request",)"
                           R"("message":"a lane and a command","detail":{})"
                         + remedy::member(remedy::none()) + "}}";
                return respond(std::move(bad));
            }

            ::lane::Store::instance().attachProcess(
                lane->str(), command->str(),
                [respond, name = lane->str()](const ::lane::Store::Spawn& spawn) {
                    http::Response resp;
                    if (!spawn.error.empty()) {
                        const std::string code = spawn.errorCode.empty()
                            ? std::string("confinement_unavailable") : spawn.errorCode;
                        resp.status = code == "spawn_failed" ? 500 : 409;
                        resp.body = R"({"error":{"code":)" + jstring(code)
                                    + R"(,"message":)" + jstring(spawn.error)
                                    + R"(,"detail":{"lane":)" + jstring(name)
                                    + R"(,"program":)" + jstring(spawn.program)
                                    + R"(,"reason":)" + jstring(spawn.error) + "}"
                                    + remedy::member(remedy::retry()) + "}}";
                    } else if (spawn.pid <= 0) {
                        resp.status = 409;
                        resp.body = R"({"error":{"code":"wrong_state",)"
                                    R"("message":"no such lane, or it already holds a process",)"
                                    R"("detail":{})"
                                  + remedy::member(remedy::endSession(name)) + "}}";
                    } else {
                        resp.status = 202;
                        resp.body = "{\"pid\":" + std::to_string(spawn.pid) + "}";
                    }
                    respond(std::move(resp));
                });
        });
}

void api::Manager::registerFailSpawnHook_()
{
    if (!lane::Store::instance().testHooksEnabled()) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: POST /api/_test/fail-next-spawn is registered";

    http::Server::instance().routeAsync("POST", "/api/_test/fail-next-spawn",
        [](const http::Request& req, http::Responder respond) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse(req.body, err);
            const manifest::Json* times = body.find("times");
            const int n = times && times->isNumber() ? static_cast<int>(times->number()) : 1;
            ::lane::Store::instance().failNextSpawns(n, [respond, n] {
                http::Response resp;
                resp.status = 200;
                resp.body = "{\"failing_next\":" + std::to_string(n) + "}";
                respond(std::move(resp));
            });
        });
}

void api::Manager::registerForgetLaneHook_()
{
    if (!lane::Store::instance().testHooksEnabled()) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: POST /api/_test/forget-lane is registered";

    http::Server::instance().routeAsync("POST", "/api/_test/forget-lane",
        [](const http::Request& req, http::Responder respond) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse(req.body, err);
            const manifest::Json* lane = body.find("lane");
            if (!err.ok || !lane || !lane->isString()) {
                http::Response bad;
                bad.status = 400;
                bad.body = R"({"error":{"code":"bad_request",)"
                           R"("message":"a lane","detail":{})"
                         + remedy::member(remedy::none()) + "}}";
                return respond(std::move(bad));
            }
            ::lane::Store::instance().forgetLane(lane->str(), [respond](bool had) {
                http::Response resp;
                resp.status = 200;
                resp.body = std::string("{\"forgotten\":") + (had ? "true" : "false") + "}";
                respond(std::move(resp));
            });
        });
}
