#include "manager.h"

#include <app/agent/launch.h>
#include <app/agent/session.h>
#include <app/api/delivery.h>
#include <app/api/socket.h>
#include <app/catalog/store.h>
#include <app/lane/render.h>
#include <app/lane/store.h>
#include <app/manifest/store.h>
#include <app/permissions/store.h>
#include <glog/logging.h>

#include "lane-view.h"
#include "roles.h"
#include "run-view.h"

void api::Manager::init()
{
    catalog::Store::instance().onChange([](const catalog::Store::Change& change) {
        api::Socket::instance().publishCatalogChange(
            catalog::Store::kindName(change.kind), change.id, change.path, change.change);
        if (change.kind == catalog::Store::Kind::Ticket && !change.id.empty())
            api::publishStagesOf(change.id);
    });

    lane::Store::instance().onResource([](const lane::Store::ResourceView& view) {
        std::vector<std::string> lanes;
        if (!view.heldBy.empty()) lanes.push_back(view.heldBy);
        for (const auto& waiting : view.queue) lanes.push_back(waiting);
        api::Socket::instance().publishResource(lane::renderResource(view),
                                                api::resourceScopes(lanes));
    });
    watchDelivery();

    lane::Store::instance().onIsolation(
        [](const std::string& name, const lane::confine::Refusal& refusal) {
            api::Socket::instance().publishIsolation(lane::renderIsolation(name, refusal),
                                                     api::isolationScopes(name));
        });

    wireAgent_();
    wirePermissions_();
    wireLaneState_();
    wireRoles();
    registerTestHook_();
    registerLaneHooks_();
    registerForgetLaneHook_();
    registerFailSpawnHook_();
    registerPermissionHook_();
    registerPermissionSubscriberHook_();
    registerClockHook_();
}

void api::Manager::wireLaneState_()
{
    lane::Store::instance().onSilence([](const std::string& name, bool silent, long long forS) {
        if (!silent) {
            agent::Store::instance().noteSilence(name, false, forS);
            return;
        }

        permissions::Store::instance().blockedSnapshot(
            [name, forS](std::map<std::string, permissions::Store::Blocked> blocked) {
                if (blocked.count(name)) return;
                agent::Store::instance().noteSilence(name, true, forS);
            });
        api::publishLaneState(name);
    });

    agent::Store::instance().onRun([](const agent::Store::RunReport& report) {
        lane::Store::instance().noteOutcome(report.lane, report.outcome);
        if (report.outcome != "running")
            api::recordTheRefTheSessionLeft(report.lane, report.runId);
        api::publishLaneState(report.lane);
        api::publishDeliveryStates();
    });

    agent::Store::instance().onLaneReason([](const std::string& lane, const std::string& why) {
        lane::Store::instance().setWords(lane, "stopped", why);
    });

    agent::Store::instance().onGoalStatus(
        [](const std::string& lane, const std::string& status) {
            lane::Store::instance().setGoalStatus(lane, status);
            api::publishLaneState(lane);
        });

    agent::Store::instance().onTurn([](const agent::Store::TurnReport& report) {
        lane::Store::instance().setTurn(report.lane, report.active, report.ended);
        if (report.ended && !report.role.empty() && report.role != "run")
            api::recordTheRefTheSessionLeft(report.lane, report.runId);
        if (report.hasCost)
            api::publishLaneMetrics(report.lane, report.n, report.turnCostUsd, report.costUsd);
        api::publishLaneState(report.lane);
    });

    catalog::Store::instance().onChange([](const catalog::Store::Change& change) {
        if (change.kind == catalog::Store::Kind::Report) api::publishAllLaneStates();
    });

    lane::Store::instance().onResource([](const lane::Store::ResourceView&) {
        api::publishAllLaneStates();
    });

    lane::Store::instance().onMembership([](const lane::Store::Record& record, bool claimed) {
        if (!claimed) agent::Store::instance().forgetLane(record.name);
        api::publishLaneMembership(record.name, record.ticketId, record.workspace, claimed);
        api::publishDeliveryStates();
    });

    lane::Store::instance().onWords([](const std::string& name, const std::string& ticketId,
                                       const lane::Store::Words& words) {
        api::publishLaneWords(name, ticketId, words);
    });
}

void api::Manager::wirePermissions_()
{
    permissions::Store::instance().onRequest(
        [](const std::string& lane, const std::string& ticketId, const std::string& payload) {
            api::Socket::instance().publishPermissionRequest(
                payload, api::permissionScopes(lane, ticketId));
            api::publishLaneState(lane);
        });

    permissions::Store::instance().onResolved([](const std::string& payload) {
        api::Socket::instance().publishPermissionResolved(payload);
    });
    permissions::Store::instance().onRules([](const std::string& payload) {
        api::Socket::instance().publishRules(payload);
    });

    permissions::Store::instance().onEntry(
        [](const std::string& lane, const std::string& runId, const std::string& entry) {
            api::Socket::instance().publishTranscript(lane, runId, entry, nullptr);
            lane::Store::instance().noteActivity(lane);
            api::publishLaneState(lane);
        });

    agent::Store::instance().onRun([](const agent::Store::RunReport& report) {
        permissions::Store::instance().noteRun(report.lane, report.runId, report.ticketId,
                                               report.outcome);
    });
}

void api::Manager::wireAgent_()
{
    lane::Store::instance().onPrepareConfig(
        [](const std::string& profile, const std::string& worktree,
           std::vector<std::pair<std::string, std::string>>& seeded,
           std::vector<std::pair<std::string, std::string>>& borrowed,
           std::vector<std::string>& runtimeDirectories) {
            const auto& data = manifest::Store::instance().data();
            const manifest::Store::Agent* selected = &data.agent;
            for (const auto& candidate : data.agents)
                if (candidate.name == profile) selected = &candidate;
            const agent::launch::ConfigSeed config =
                agent::launch::configSeed(*selected, worktree);
            seeded = config.seeded;
            borrowed = config.borrowed;
            runtimeDirectories = config.runtimeDirectories;
        });
    agent::Store::instance().setApproval(
        [](const std::string& lane, const std::string& tool, const std::string& argument,
           std::function<void(bool)> done) {
            permissions::Store::Ask ask;
            ask.lane = lane;
            ask.tool = tool;
            ask.argument = argument;
            ask.hasArgument = !argument.empty();
            ask.inputSummary = argument;
            permissions::Store::instance().ask(ask,
                [done](permissions::Store::Decision decision) { done(decision.allowed); });
        });
    agent::Store::instance().setSpawner(
        [](const agent::Store::SpawnRequest& request,
           std::function<void(agent::Store::SpawnResult)> done) {
            lane::Store::Launch launch;
            launch.label = request.label;
            launch.argv = request.argv;
            launch.env = request.env;
            launch.unsetEnv = request.unsetEnv;
            launch.stdinFd = request.stdinFd;
            launch.configDirVar = request.configDirVar;
            launch.agentProfile = request.agentProfile;
            const std::string name = request.lane;
            launch.sink.chunk = [name](const std::string& text) {
                agent::Store::instance().noteOutput(name, text);
            };
            lane::Store::instance().startProcess(
                request.lane, launch, [done](lane::Store::Spawn spawn) {
                    agent::Store::SpawnResult result;
                    result.pid = spawn.pid;
                    result.error = spawn.error;
                    result.errorCode = spawn.errorCode;
                    result.ticketId = spawn.ticketId;
                    result.role = spawn.role;
                    result.worktree = spawn.worktree;
                    result.program = spawn.program;
                    done(result);
                });
        });

    lane::Store::instance().onProcessExit([](const std::string& name, int pid, int status) {
        agent::Store::instance().noteExit(name, pid, status);
    });

    agent::Store::instance().setLaneRun(
        [](const std::string& name, const std::string& runId, const std::string& sessionId) {
            lane::Store::instance().setRun(name, runId, sessionId);
        });

    agent::Store::instance().onRun([](const agent::Store::RunReport& report) {
        api::Socket::instance().publishRun(api::renderRun(report),
                                           api::runScopes(report.lane, report.ticketId));
    });

    agent::Store::instance().onEntry([](const agent::Store::EntryEvent& event) {
        api::Socket::instance().publishTranscriptAt(event.lane, event.runId, event.seq,
                                                    event.patch, event.json);
    });

    agent::Store::instance().setLaneCost([](const std::string& name, double costUsd) {
        lane::Store::instance().setCost(name, costUsd);
    });

    agent::Store::instance().setLaneWords(
        [](const std::string& name, const std::string& kind, const std::string& text) {
            lane::Store::instance().setWords(name, kind, text);
        });
}

void api::Manager::start() { LOG(INFO) << "api ready"; }
void api::Manager::stop() { stopStartPreflights(); }
