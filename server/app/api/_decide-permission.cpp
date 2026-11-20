#include <app/error/remedy.h>
#include "manager.h"

#include <app/manifest/json.h>
#include <app/permissions/store.h>

#include "permission-view.h"

HTTP_ASYNC_HANDLER(api::Manager, DecidePermission)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    const manifest::Json* requestId = err.ok ? body.find("request_id") : nullptr;
    const manifest::Json* decision = err.ok ? body.find("decision") : nullptr;

    if (!requestId || !requestId->isString() || !decision || !decision->isString()) {
        http::Response bad;
        bad.status = 400;
        bad.body = R"({"error":{"code":"bad_request","message":"a request_id and a decision of )"
                   R"(once, always or deny","detail":{})"
                 + remedy::member(remedy::none()) + "}}";
        respond(std::move(bad));
        return;
    }

    permissions::Store::instance().decide(
        requestId->str(), decision->str(), [respond](permissions::Store::Answer answer) {
            respond(api::permissionResponse(answer));
        });
}
