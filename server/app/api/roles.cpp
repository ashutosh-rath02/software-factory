#include "roles.h"

#include <filesystem>
#include <map>
#include <memory>
#include <utility>

#include <glog/logging.h>

#include <app/agent/session.h>
#include <app/catalog/store.h>
#include <app/manifest/store.h>
#include "manager.h"
#include "start-run.h"
#include <core/worker.h>

#include "prompt.h"

namespace {

const manifest::Store::Role* declared(const std::string& name)
{
    for (const auto& role : manifest::Store::instance().data().roles)
        if (role.name == name) return &role;
    return nullptr;
}

void abandon(const lane::Store::RoleStart& start, const std::string& why,
             const std::string& claimedLane = std::string())
{
    LOG(WARNING) << "role " << start.role << " for " << start.ticketId << " did not start: "
                 << why;
    lane::Store::instance().forgetRole(start);
    if (claimedLane.empty()) return;
    lane::Store::instance().release(claimedLane, false, false, [claimedLane](lane::Store::Outcome out) {
        if (out.status >= 400)
            LOG(WARNING) << "the lane " << claimedLane << " claimed for a role that never started "
                         << "could not be released: " << out.errorCode;
    });
}

struct Deferred {
    lane::Store::RoleStart start;
    std::string prompt;
};

std::map<std::string, Deferred>& deferred()
{
    static std::map<std::string, Deferred> waiting;
    return waiting;
}

void spawn(const lane::Store::RoleStart& start, const std::string& name, const std::string& prompt)
{
    api::Manager::instance().reserveStart(name, [start, name, prompt](bool reserved, bool missing) {
        if (missing) return abandon(start, "the role lane no longer exists");
        if (!reserved) return abandon(start, "a start is already pending for this lane");
        lane::Store::instance().startGate(name, [start, name, prompt](lane::Store::Outcome held) {
        if (held.status >= 400 || held.lane.ticketId != start.ticketId
            || held.lane.role != start.role || held.lane.workspace != start.workspace) {
            api::Manager::instance().releaseStart(name);
            return abandon(start, "the role lane changed before start");
        }
        agent::Store::StartRequest request;
        request.lane = name;
        request.worktree = held.lane.worktree;
        request.ticketId = start.ticketId;
        request.role = start.role;
        request.agentProfile = manifest::Store::instance().data().defaultAgent;
        request.prompt = prompt;
        request.hasPrompt = !prompt.empty();
        request.resume = false;
        api::startSelectedRun(std::move(request), start.workspace,
            [start, name](http::Response answer) {
                if (answer.status >= 400) return abandon(start, answer.body, name);
                lane::Store::instance().noteRoleStarted(start, name);
            });
        });
    });
}

void startRun(const lane::Store::RoleStart& start, const std::string& name, const std::string& prompt)
{
    lane::Store::instance().startGate(name, [start, name, prompt](lane::Store::Outcome out) {
        if (out.status == 409 && out.errorCode == "resource_busy") {
            LOG(INFO) << "role " << start.role << " for " << start.ticketId << " waits in " << name
                      << ": " << out.errorMessage;
            deferred()[name] = Deferred{start, prompt};
            return;
        }
        if (out.status >= 400)
            return abandon(start, out.errorCode + ": " + out.errorMessage, name);
        if (out.lane.ticketId != start.ticketId || out.lane.role != start.role
            || out.lane.workspace != start.workspace)
            return abandon(start, "the role lane changed before start");
        spawn(start, name, prompt);
    });
}

void promptThenRun(const lane::Store::RoleStart& start, const std::string& name,
                   const std::string& worktree, const std::string& branch,
                   const std::string& promptPath)
{
    core::Worker::instance().post([start, name, worktree, branch, promptPath] {
        const api::prompt::Read read =
            api::prompt::readFor(promptPath, start.ticketId, worktree);
        if (!read.found)
            return abandon(start, "the prompt at " + promptPath + " could not be read", name);
        if (!read.reachable)
            return abandon(start, "the ticket " + start.ticketId + " is not on this role "
                                  "lane's branch " + branch + ", so " + read.inLane + " does not "
                                  "exist and the agent could not open it", name);
        startRun(start, name,
                 api::prompt::substitute(
                     read.text, api::prompt::values(start.ticketId, read.ticketPath,
                                                    worktree, start.triggeredByRun)));
    });
}

void launch(const lane::Store::RoleStart& start)
{
    const manifest::Store::Role* role = declared(start.role);
    if (!role) return abandon(start, "the manifest no longer declares it");

    lane::Store::ClaimRequest claim;
    claim.ticketId = start.ticketId;
    claim.workspace = start.workspace;
    claim.role = start.role;

    const std::string promptPath = role->promptAbs;
    lane::Store::instance().claim(claim, [start, promptPath](lane::Store::Outcome out) {
        if (out.status >= 400)
            return abandon(start, out.errorCode + ": " + out.errorMessage);
        promptThenRun(start, out.lane.name, out.lane.worktree, out.lane.branch, promptPath);
    });
}

}

void api::rolesOnStage(const std::string& name, const std::string& ticketId,
                       const std::string& runId, const std::string& stage)
{
    const auto& data = manifest::Store::instance().data();
    if (!data.hasRoles) return;

    for (const auto& role : data.roles) {
        if (role.startOnStage != stage) continue;
        lane::Store::RoleStart start;
        start.ticketId = ticketId;
        start.role = role.name;
        start.stage = stage;
        start.triggeredByLane = name;
        start.triggeredByRun = runId;
        lane::Store::instance().requestRole(start, [](lane::Store::RoleAdmission admission) {
            if (admission.admitted) launch(admission.start);
        });
    }
}

void api::wireRoles()
{
    lane::Store::instance().onRoleStart([](const lane::Store::RoleStart& start) { launch(start); });

    lane::Store::instance().onResourceGranted(
        [](const std::string& name, const std::string& ticketId, const std::string& role) {
            const auto it = deferred().find(name);
            if (it == deferred().end()) return;
            const Deferred waiting = it->second;
            deferred().erase(it);
            if (waiting.start.ticketId != ticketId || waiting.start.role != role) {
                LOG(WARNING) << "the role start held in " << name << " was for "
                             << waiting.start.ticketId << "/" << waiting.start.role
                             << " and that lane now carries " << ticketId << "/" << role
                             << "; it is dropped rather than started against the wrong work";
                lane::Store::instance().forgetRole(waiting.start);
                return;
            }
            spawn(waiting.start, name, waiting.prompt);
        });
}

catalog::RolesLookup api::roleLookup(const lane::Store::RoleStates& states)
{
    return [states](const std::string& ticketId) {
        std::vector<catalog::Role> out;
        const auto it = states.find(ticketId);
        if (it == states.end()) return out;
        for (const auto& state : it->second) {
            catalog::Role role;
            role.role = state.role;
            role.stage = state.stage;
            role.state = state.state;
            role.lane = state.lane;
            role.runId = state.runId;
            out.push_back(std::move(role));
        }
        return out;
    };
}
