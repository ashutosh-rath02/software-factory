#include "manager.h"

#include <app/permissions/store.h>

HTTP_ASYNC_HANDLER(api::Manager, PermissionRules)
{
    (void)req;
    permissions::Store::instance().listRules([respond](std::string body) {
        http::Response resp;
        resp.status = 200;
        resp.body = std::move(body);
        respond(std::move(resp));
    });
}
