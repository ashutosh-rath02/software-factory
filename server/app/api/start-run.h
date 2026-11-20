#pragma once

#include <app/agent/session.h>
#include <core/http.h>

namespace api {

void startSelectedRun(agent::Store::StartRequest request, const std::string& workspace,
                      const http::Responder& respond);

}
