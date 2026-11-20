#include "permission-view.h"

#include <app/error/remedy.h>
#include <app/manifest/json.h>

namespace {

using manifest::jstr;
}

http::Response api::permissionResponse(const permissions::Store::Answer& answer)
{
    http::Response resp;
    resp.status = answer.status;
    if (answer.errorCode.empty()) {
        resp.body = answer.body;
        return resp;
    }
    resp.body = "{\"error\":{\"code\":" + jstr(answer.errorCode)
              + ",\"message\":" + jstr(answer.errorMessage)
              + ",\"detail\":{" + answer.errorDetail + "}"
              + remedy::member(answer.errorRemedy) + "}}";
    return resp;
}
