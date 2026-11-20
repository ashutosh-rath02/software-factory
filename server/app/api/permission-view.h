#pragma once

#include <core/http.h>
#include <app/permissions/store.h>

namespace api {

http::Response permissionResponse(const permissions::Store::Answer& answer);

}
