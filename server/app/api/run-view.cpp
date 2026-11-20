#include "run-view.h"

#include <sstream>

#include <app/error/remedy.h>
#include <app/manifest/json.h>

namespace {

using manifest::jstr;
}

http::Response api::runResponse(const agent::Store::Answer& answer)
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

http::Response api::noSuchLane(const std::string& lane)
{
    http::Response resp;
    resp.status = 404;
    resp.body = "{\"error\":{\"code\":\"not_found\",\"message\":\"no lane by that name is "
                "claimed\",\"detail\":{\"lane\":" + jstr(lane) + "}"
              + remedy::member(remedy::reloadManifest()) + "}}";
    return resp;
}

std::string api::renderRun(const agent::Store::RunReport& report)
{
    std::ostringstream o;
    o << "\"lane\":" << jstr(report.lane)
      << ",\"agent\":" << jstr(report.agentProfile)
      << ",\"driver\":" << jstr(report.driver)
      << ",\"model\":" << jstr(report.model)
      << ",\"cost_known\":" << (report.costKnown ? "true" : "false")
      << ",\"run_id\":" << jstr(report.runId)
      << ",\"n\":" << report.n
      << ",\"outcome\":" << jstr(report.outcome)
      << ",\"started_iso\":" << jstr(report.startedIso)
      << ",\"ended_iso\":" << (report.endedIso.empty() ? "null" : jstr(report.endedIso))
      << ",\"stage\":" << report.stage
      << ",\"duration_s\":" << report.durationS
      << ",\"cost_usd\":" << report.costUsd
      << ",\"exit_code\":" << (report.exitCode < 0 ? std::string("null")
                                                   : std::to_string(report.exitCode))
      << ",\"signal\":" << (report.signal > 0 ? std::to_string(report.signal)
                                              : std::string("null"));
    return o.str();
}
