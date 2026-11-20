#include "manager.h"

#include <app/permissions/store.h>

#include "permission-view.h"

HTTP_ASYNC_HANDLER(api::Manager, RevokeRule)
{
    const auto id = req.params.find("rule_id");
    const std::string ruleId = id == req.params.end() ? std::string() : id->second;
    permissions::Store::instance().revoke(ruleId, [respond](permissions::Store::Answer answer) {
        respond(api::permissionResponse(answer));
    });
}
