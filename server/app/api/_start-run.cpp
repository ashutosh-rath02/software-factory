#include <app/error/remedy.h>
#include "manager.h"

#include <filesystem>
#include <algorithm>
#include <memory>
#include <utility>

#include <app/agent/session.h>
#include <app/agent/launch.h>
#include <app/catalog/store.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <core/worker.h>

#include "lane-view.h"
#include "prompt.h"
#include "run-view.h"
#include "start-run.h"

void api::startSelectedRun(agent::Store::StartRequest request, const std::string& workspace,
                           const http::Responder& respond)
{
    const auto& data = manifest::Store::instance().data();
    if (!request.hasTree) {
        request.tree.name = data.defaultTree;
        for (const auto& tree : data.trees)
            if (tree.name == data.defaultTree) request.tree = tree;
        request.hasTree = data.hasTrees;
    }
    std::string model = request.model;
    if (!request.hasModel)
        for (const auto& profile : data.agents)
            if (profile.name == request.agentProfile) model = profile.model;
    const std::string lane = request.lane;
    const std::string ticketId = request.ticketId;
    const std::string profileName = request.agentProfile;
    std::string driver;
    for (const auto& profile : data.agents)
        if (profile.name == profileName) driver = profile.driver;
    if (driver != "codex" && !request.tree.continuation.empty()) {
        const std::string field = "continuation";
        http::Response refused;
        refused.status = 409;
        refused.body = "{\"error\":{\"code\":\"invalid_agent_selection\",\"message\":"
                     + manifest::jstr(field + " requires a Codex profile")
                     + ",\"detail\":{\"field\":" + manifest::jstr(field) + "}"
                     + remedy::member(remedy::reloadManifest()) + "}}";
        return Manager::instance().releaseStart(
            lane, [respond, refused = std::move(refused)]() mutable {
                respond(std::move(refused));
            });
    }
    core::Worker::instance().post(
        [request = std::move(request), lane, workspace, ticketId, profileName, model,
         driver, respond]() mutable {
            const std::string missing = agent::launch::missingTreeAgent(
                driver, request.worktree, request.tree.agents);
            if (!missing.empty()) {
                http::Response refused;
                refused.status = 409;
                refused.body = "{\"error\":{\"code\":\"tree_agent_missing\",\"message\":"
                             + manifest::jstr("the selected tree needs an agent file absent from the lane")
                             + ",\"detail\":{\"path\":" + manifest::jstr(missing) + "}"
                             + remedy::member(remedy::reloadManifest()) + "}}";
                return api::Manager::instance().releaseStart(
                    lane, [respond, refused = std::move(refused)]() mutable {
                        respond(std::move(refused));
                    });
            }
            api::Manager::instance().runStartPreflight(
                lane, workspace, ticketId, profileName, model,
                [request = std::move(request), lane, respond](http::Response checked) mutable {
            if (checked.status != 200) {
                return api::Manager::instance().releaseStart(
                    lane, [respond, checked = std::move(checked)]() mutable {
                        respond(std::move(checked));
                    });
            }
            agent::Store::instance().startRun(request, [lane, respond](agent::Store::Answer answer) {
                api::Manager::instance().releaseStart(
                    lane, [respond, answer = std::move(answer)]() mutable {
                        respond(api::runResponse(answer));
                    });
            });
                });
        });
}

namespace {

std::string promptFor(const std::string& role, const std::string& profile)
{
    const auto& data = manifest::Store::instance().data();
    if (!role.empty() && role != "run") {
        for (const auto& declared : data.roles)
            if (declared.name == role && !declared.promptAbs.empty()) return declared.promptAbs;
    }
    for (const auto& agent : data.agents)
        if (agent.name == profile) return agent.startPromptAbs;
    return {};
}

void startWithDeclaredPrompt(const std::string& ticketId, const std::string& worktree,
                             const std::string& branch, const std::string& role,
                             const std::string& workspace,
                             agent::Store::StartRequest request,
                             const http::Responder& respond)
{
    const std::string promptPath = promptFor(role, request.agentProfile);

    core::Worker::instance().post(
        [promptPath, ticketId, worktree, branch, workspace, request, respond]() mutable {
            const api::prompt::Read read = api::prompt::readFor(promptPath, ticketId, worktree);
            if (!read.found) {
                http::Response resp;
                resp.status = 500;
                resp.body = "{\"error\":{\"code\":\"start_prompt_unreadable\",\"message\":\"this "
                            "repository's start prompt could not be read, so there is nothing to "
                            "start the run with\",\"detail\":{\"path\":\""
                          + manifest::jsonEscape(promptPath) + "\",\"field\":"
                            "\"agent.start_prompt_file\"}"
                          + remedy::member(remedy::reloadManifest()) + "}}";
                return api::Manager::instance().releaseStart(
                    request.lane, [respond, resp = std::move(resp)]() mutable {
                        respond(std::move(resp));
                    });
            }
            if (!read.reachable) {
                http::Response resp;
                resp.status = 409;
                resp.body = "{\"error\":{\"code\":\"ticket_not_on_branch\",\"message\":\"this "
                            "ticket is not on the lane's branch, so the agent could not open it; "
                            "commit it and start again\",\"detail\":{\"ticket_id\":\""
                          + manifest::jsonEscape(ticketId) + "\",\"path\":\""
                          + manifest::jsonEscape(read.inLane) + "\",\"branch\":\""
                          + manifest::jsonEscape(branch) + "\"}"
                          + remedy::member(remedy::retry()) + "}}";
                return api::Manager::instance().releaseStart(
                    request.lane, [respond, resp = std::move(resp)]() mutable {
                        respond(std::move(resp));
                    });
            }
            request.prompt = api::prompt::substitute(
                read.text, api::prompt::values(ticketId, read.ticketPath, worktree,
                                               std::string()));
            request.prompt = api::prompt::trim(request.prompt);
            request.hasPrompt = !request.prompt.empty();
            api::startSelectedRun(std::move(request), workspace, respond);
        });
}

void startRun(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);
    api::Manager::instance().reserveStart(name, [name, body = req.body, respond](
        bool reserved, bool missing) {
      if (missing) return respond(api::noSuchLane(name));
      if (!reserved) {
          http::Response resp;
          resp.status = 409;
          resp.body = "{\"error\":{\"code\":\"start_preflight_running\","
                      "\"message\":\"a start is already pending for this lane\","
                      "\"detail\":{\"lane\":" + manifest::jstr(name) + "}"
                      + remedy::member(remedy::retry()) + "}}";
          return respond(std::move(resp));
      }
      lane::Store::instance().startGate(name, [name, body, respond](lane::Store::Outcome gate) {
        if (!gate.errorCode.empty()) {
            http::Response resp;
            resp.status = gate.status;
            resp.body = lane::renderError(gate);
            return api::Manager::instance().releaseStart(
                name, [respond, resp = std::move(resp)]() mutable { respond(std::move(resp)); });
        }

        manifest::Json::Error err;
        const manifest::Json json = manifest::Json::parse(body, err);
        const auto bad = [&respond, &name](const std::string& message) {
            http::Response resp;
            resp.status = 400;
            resp.body = "{\"error\":{\"code\":\"invalid_agent_selection\",\"message\":"
                      + manifest::jstr(message) + ",\"detail\":{}"
                      + remedy::member(remedy::reloadManifest()) + "}}";
            api::Manager::instance().releaseStart(
                name, [respond, resp = std::move(resp)]() mutable { respond(std::move(resp)); });
        };
        if (!body.empty() && (!err.ok || !json.isObject()))
            return bad("the start request must be a JSON object");
        for (const auto* field : {"agent", "model", "prompt", "tree", "effort"})
            if (const auto* value = json.find(field); value && !value->isString())
                return bad(std::string(field) + " must be a string");

        agent::Store::StartRequest request;
        request.lane = name;
        request.worktree = gate.lane.worktree;
        request.ticketId = gate.lane.ticketId;
        request.role = gate.lane.role;
        request.agentProfile = manifest::Store::instance().data().defaultAgent;
        if (err.ok && json.isObject()) {
            if (const auto* v = json.find("agent"); v && v->isString())
                request.agentProfile = v->str();
            if (const auto* v = json.find("tree"); v && v->isString()) {
                for (const auto& tree : manifest::Store::instance().data().trees)
                    if (tree.name == v->str()) {
                        request.tree = tree;
                        request.hasTree = true;
                    }
                if (!request.hasTree) return bad("select a declared tree preset");
            }
            if (const manifest::Json* v = json.find("prompt"); v && v->isString()) {
                request.prompt = v->str();
                request.hasPrompt = !request.prompt.empty();
            }
            if (const manifest::Json* v = json.find("resume"); v && v->isBool())
                request.resume = v->boolean();
            if (const manifest::Json* v = json.find("model"); v && v->isString()) {
                request.model = v->str();
                request.hasModel = !request.model.empty();
            }
            if (const manifest::Json* v = json.find("effort"); v && v->isString()) {
                request.effort = v->str();
                request.hasEffort = true;
            }
        }

        const manifest::Store::Agent* profile = nullptr;
        for (const auto& declared : manifest::Store::instance().data().agents)
            if (declared.name == request.agentProfile) profile = &declared;
        if (!profile) return bad("select a declared agent profile");
        if (const auto* model = json.find("model"); model && model->str().empty())
            return bad("model must not be empty");
        if (request.hasModel && !profile->models.empty()
            && std::find(profile->models.begin(), profile->models.end(), request.model)
               == profile->models.end())
            return bad("select one of the declared profile's models");
        if (request.hasEffort) {
            if (request.effort.empty()) return bad("effort must not be empty");
            if (!profile->hasEfforts || profile->efforts.empty())
                return bad("this profile declares no efforts, so none can be selected for it");
            if (std::find(profile->efforts.begin(), profile->efforts.end(), request.effort)
                == profile->efforts.end())
                return bad("select one of the declared profile's efforts");
        }
        if (!request.hasPrompt && profile->hasStartPromptFile)
            return startWithDeclaredPrompt(gate.lane.ticketId, gate.lane.worktree,
                                           gate.lane.branch, gate.lane.role, gate.lane.workspace,
                                           std::move(request), respond);

        api::startSelectedRun(std::move(request), gate.lane.workspace, respond);
      });
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, StartRun) { startRun(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, StartRunSegments) { startRun(req, std::move(respond)); }
