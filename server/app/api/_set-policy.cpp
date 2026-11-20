#include "manager.h"

#include <app/manifest/json.h>
#include <app/permissions/store.h>

#include "permission-view.h"

HTTP_ASYNC_HANDLER(api::Manager, SetPolicy)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    const manifest::Json* policy = err.ok ? body.find("policy") : nullptr;
    const std::string wanted = (policy && policy->isString()) ? policy->str() : std::string();

    permissions::Store::instance().setPolicy(wanted, [respond](permissions::Store::Answer answer) {
        respond(api::permissionResponse(answer));
    });
}
