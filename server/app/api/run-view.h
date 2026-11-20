#pragma once

#include <string>
#include <vector>

#include <core/http.h>
#include <app/agent/session.h>

namespace api {

http::Response runResponse(const agent::Store::Answer& answer);

http::Response noSuchLane(const std::string& lane);

std::string renderRun(const agent::Store::RunReport& report);

}
