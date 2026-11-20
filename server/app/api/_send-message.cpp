#include "manager.h"

#include <app/agent/session.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>

#include "lane-view.h"
#include "run-view.h"

namespace {

void sendMessage(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);

    lane::Store::instance().exists(name, [name, body = req.body, respond](bool exists) {
        if (!exists) return respond(api::noSuchLane(name));

        manifest::Json::Error err;
        const manifest::Json json = manifest::Json::parse(body, err);

        std::string text;
        std::string requestId;
        if (err.ok && json.isObject()) {
            if (const manifest::Json* v = json.find("text"); v && v->isString()) text = v->str();
            if (const manifest::Json* v = json.find("request_id"); v && v->isString())
                requestId = v->str();
        }

        agent::Store::instance().message(name, text, requestId, [respond](agent::Store::Answer answer) {
            respond(api::runResponse(answer));
        });
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, SendMessage) { sendMessage(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, SendMessageSegments) { sendMessage(req, std::move(respond)); }
