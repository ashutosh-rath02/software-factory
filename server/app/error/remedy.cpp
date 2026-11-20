#include "remedy.h"

#include <sstream>

#include <app/manifest/json.h>

namespace {

using manifest::jstr;
std::string nouns(const remedy::Cost& cost)
{
    std::ostringstream o;
    o << "[";
    const char* sep = "";
    if (cost.turn)         { o << sep << "\"turn\"";          sep = ","; }
    if (cost.session)      { o << sep << "\"session\"";       sep = ","; }
    if (cost.worktree)     { o << sep << "\"worktree\"";      sep = ","; }
    if (cost.ignoredFiles) { o << sep << "\"ignored_files\""; sep = ","; }
    o << "]";
    return o.str();
}

std::string build(const char* action, const std::string& target, const std::string& label,
                  const remedy::Cost& cost, const std::string& confirm = std::string())
{
    std::ostringstream o;
    o << "{\"action\":\"" << action << "\""
      << ",\"target\":" << target
      << ",\"label\":" << jstr(label)
      << ",\"destroys\":" << nouns(cost);
    if (!confirm.empty()) o << ",\"confirm\":" << jstr(confirm);
    o << "}";
    return o.str();
}

std::string laneTarget(const std::string& lane)
{
    return "{\"lane\":" + jstr(lane) + "}";
}

}

std::string remedy::retry()
{
    return build("retry", "{}", "Try again", {});
}

std::string remedy::reloadManifest()
{
    return build("reload_manifest", "{}", "Reload the manifest", {});
}

std::string remedy::claimLane(const std::string& ticketId, const std::string& workspace)
{
    return build("claim_lane",
                 "{\"ticket\":" + jstr(ticketId) + ",\"workspace\":" + jstr(workspace) + "}",
                 "Claim a lane", {});
}

std::string remedy::openLane(const std::string& lane)
{
    return build("open_lane", laneTarget(lane), "Open " + lane, {});
}

std::string remedy::startRun(const std::string& lane)
{
    return build("start_run", laneTarget(lane), "Start a run", {});
}

std::string remedy::endSession(const std::string& lane)
{
    return build("end_session", laneTarget(lane), "End the session after this turn", {});
}

std::string remedy::killRun(const std::string& lane)
{
    Cost cost;
    cost.turn = true;
    return build("kill_run", laneTarget(lane), "Kill the process now", cost,
                 "This ends the turn in flight, and whatever the agent had produced in it that "
                 "it had not written down.");
}

std::string remedy::recoverLane(const std::string& lane)
{
    return build("recover_lane", laneTarget(lane), "Recover " + lane, {});
}

std::string remedy::releaseLane(const std::string& lane, bool force, Cost cost)
{
    std::string confirm;
    if (cost.session || cost.worktree) {
        confirm = "Releasing " + lane + " ";
        confirm += cost.session ? "ends its session" : "removes nothing of its session";
        if (cost.worktree) confirm += " and removes its checkout";
        confirm += ". The branch is kept, by name.";
    }
    return build("release_lane",
                 "{\"lane\":" + jstr(lane) + ",\"force\":" + (force ? "true" : "false") + "}",
                 "Release " + lane, cost, confirm);
}

std::string remedy::discardIgnored(const std::string& lane, const std::vector<std::string>& paths,
                                   Cost cost)
{
    std::ostringstream target;
    target << "{\"lane\":" << jstr(lane) << ",\"paths\":[";
    for (std::size_t i = 0; i < paths.size(); ++i) target << (i ? "," : "") << jstr(paths[i]);
    target << "]}";

    std::ostringstream confirm;
    confirm << "Releasing " << lane << " removes its checkout and the files git was told to "
            << "ignore inside it";
    for (std::size_t i = 0; i < paths.size(); ++i)
        confirm << (i ? ", " : ": ") << paths[i];
    confirm << ". They cannot be got back.";
    return build("discard_ignored", target.str(), "Release " + lane + ", discarding what is "
                 "ignored inside it", cost, confirm.str());
}

std::string remedy::releaseResource(const std::string& resource, const std::string& lane)
{
    return build("release_resource",
                 "{\"resource\":" + jstr(resource) + ",\"lane\":" + jstr(lane) + "}",
                 "Free " + resource, {});
}

std::string remedy::none()
{
    return "null";
}

std::string remedy::member(const std::string& remedy)
{
    return remedy.empty() ? std::string() : ",\"remedy\":" + remedy;
}
