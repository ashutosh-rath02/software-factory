#include <app/error/remedy.h>
#include "manager.h"

#include <sstream>

#include <app/agent/session.h>
#include <app/api/socket.h>
#include <app/catalog/store.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <app/permissions/store.h>
#include <core/worker.h>

#include "freeze.h"
#include "lane-view.h"
#include "roles.h"
#include "subscription.h"

namespace {

using manifest::jsonEscape;

using manifest::jstr;
void refuse(const http::Responder& respond)
{
    http::Response out;
    out.status = 404;
    out.body = R"({"error":{"code":"not_found","message":"no live run holds that token",)"
               R"("detail":{})" + remedy::member(remedy::reloadManifest()) + "}}";
    respond(std::move(out));
}

void badRequest(const http::Responder& respond)
{
    http::Response out;
    out.status = 400;
    out.body = R"({"error":{"code":"bad_request","message":"a token and a stage","detail":{})"
             + remedy::member(remedy::none()) + "}}";
    respond(std::move(out));
}

std::string stageEntry(const lane::progress::Stage& stage, const std::string& note)
{
    std::ostringstream o;
    o << "\"kind\":\"stage\",\"stage\":" << jstr(stage.name) << ",\"round\":" << stage.round;
    if (!note.empty()) o << ",\"note\":" << jstr(note);
    return o.str();
}

void announce(const agent::Store::TokenHolder& who, const std::string& note,
              const http::Responder& respond,
              const std::vector<permissions::Store::Frozen>& frozen,
              const lane::progress::Stage& stage, const lane::Store::StageHistory& history)
{
    api::Socket::instance().publishStage(
        api::stageMembers(who.lane, who.ticketId, api::laneProgress(history)(who.ticketId)),
        api::laneStateScopes(who.lane, who.ticketId));

    agent::Store::instance().appendEntry(who.lane, stageEntry(stage, note), nullptr);

    api::publishLaneState(who.lane);

    auto finish = [who, stage, respond] {
        http::Response out;
        out.status = 202;
        out.body = std::string("{\"accepted\":true,\"unknown_stage\":")
                 + (stage.unknown ? "true" : "false") + "}";
        respond(std::move(out));

        api::rolesOnStage(who.lane, who.ticketId, who.runId, stage.name);
    };

    if (frozen.empty()) return finish();
    permissions::Store::instance().freeze(who.runId, frozen, finish);
}

}

HTTP_ASYNC_HANDLER(api::Manager, Progress)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    const manifest::Json* token = err.ok ? body.find("token") : nullptr;
    const manifest::Json* name = err.ok ? body.find("stage") : nullptr;

    if (!token || !token->isString() || token->str().empty()
        || !name || !name->isString() || name->str().empty())
        return badRequest(respond);

    lane::progress::Report report;
    report.name = name->str();
    if (const manifest::Json* v = body.find("round"); v && v->isInteger()) {
        report.round = static_cast<int>(v->number());
        report.hasRound = true;
    }
    if (const manifest::Json* v = body.find("of"); v && v->isInteger()) {
        report.of = static_cast<int>(v->number());
        report.hasOf = true;
    }
    const std::string note = body.find("note") && body.find("note")->isString()
                           ? body.find("note")->str() : std::string();

    agent::Store::instance().resolveToken(
        token->str(), [report, note, respond](agent::Store::TokenHolder who) {
            if (!who.ok) return refuse(respond);

            core::Worker::instance().post(
                [who, report, note, respond] {
                    lane::Store::StageRequest request;
                    request.ticketId = who.ticketId;
                    request.lane = who.lane;
                    request.source = "manifest";
                    request.report = report;
                    request.report.runId = who.runId;

                    catalog::Document doc;
                    const bool known = catalog::Store::instance().ticketById(who.ticketId, doc);
                    if (known && doc.hasStages) {
                        request.sequence = doc.stages;
                        request.source = "ticket";
                    } else {
                        request.sequence = manifest::Store::instance().data().stages;
                    }

                    auto frozen = api::freezeFor(doc, report.name);

                    lane::Store::instance().reportStage(
                        request, [who, note, respond, frozen](lane::progress::Stage stage) {
                            lane::Store::instance().stageHistory(
                                [who, note, respond, frozen, stage](
                                    lane::Store::StageHistory history) {
                                    core::Worker::instance().post(
                                        [who, note, respond, frozen, stage,
                                         history = std::move(history)] {
                                            announce(who, note, respond, frozen, stage, history);
                                        });
                                });
                        });
                });
        });
}
