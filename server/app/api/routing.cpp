#include "routing.h"

#include <algorithm>
#include <set>
#include <utility>

#include <app/error/remedy.h>
#include <app/lane/command.h>
#include <app/lane/git.h>
#include <app/manifest/store.h>

#include "lane-view.h"
#include "manager.h"
#include "routing-inputs.h"
#include "socket.h"
#include "subscription.h"

http::Response api::Routing::error(int status, const std::string& code,
                                   const std::string& message, const std::string& detail)
{
    http::Response response;
    response.status = status;
    const std::string action = code == "not_declared" ? remedy::reloadManifest()
                            : code == "not_found" ? remedy::retry() : remedy::none();
    response.body = "{\"error\":{\"code\":" + manifest::jstr(code)
                  + ",\"message\":" + manifest::jstr(message)
                  + ",\"detail\":{" + detail + "}" + remedy::member(action) + "}}";
    return response;
}

bool api::Routing::requestId(const std::string& value)
{
    if (value.empty() || value.size() > 64) return false;
    for (unsigned char c : value)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
              || (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
    return true;
}

bool api::Routing::fields(const manifest::Json& body, const std::vector<std::string>& allowed)
{
    if (!body.isObject()) return false;
    std::set<std::string> seen;
    for (const auto& member : body.members()) {
        if (std::find(allowed.begin(), allowed.end(), member.first) == allowed.end()
            || !seen.insert(member.first).second) return false;
    }
    return true;
}

bool api::Routing::parse(const std::string& text, routing::Store::Request& request)
{
    manifest::Json::Error parsed;
    const auto body = manifest::Json::parse(text, parsed);
    if (!parsed.ok || !fields(body, {"request_id", "steps"})) return false;
    const auto* id = body.find("request_id");
    const auto* steps = body.find("steps");
    if (!id || !id->isString() || !requestId(id->str()) || !steps || !steps->isArray()
        || steps->items().empty() || steps->items().size() > 16) return false;
    request.requestId = id->str();
    for (const auto& item : steps->items()) {
        if (!fields(item, {"which", "accepted_exit_codes", "reuse", "require_decision"}))
            return false;
        const auto* which = item.find("which");
        const auto* codes = item.find("accepted_exit_codes");
        const auto* reuse = item.find("reuse");
        const auto* decision = item.find("require_decision");
        if (!which || !which->isString() || which->str().empty() || !codes || !codes->isArray()
            || codes->items().empty() || (reuse && !reuse->isBool())
            || (decision && !decision->isBool())) return false;
        routing::Store::Step step;
        step.which = which->str();
        step.reuse = reuse && reuse->boolean();
        step.requireDecision = decision && decision->boolean();
        std::set<int> seen;
        for (const auto& code : codes->items()) {
            if (!code.isInteger() || code.number() < 0 || code.number() > 255) return false;
            const int exit = static_cast<int>(code.number());
            if (!seen.insert(exit).second) return false;
            step.acceptedExitCodes.push_back(exit);
        }
        std::sort(step.acceptedExitCodes.begin(), step.acceptedExitCodes.end());
        request.steps.push_back(std::move(step));
    }
    return true;
}

std::string api::Routing::incarnation(const lane::Store::Record& record)
{
    return manifest::jstr(record.name) + ":" + manifest::jstr(record.ticketId) + ":"
        + manifest::jstr(record.workspace) + ":" + manifest::jstr(record.role) + ":"
        + manifest::jstr(record.branch) + ":" + manifest::jstr(record.worktree) + ":"
        + std::to_string(record.claimedAtMs);
}

void api::Routing::context(const std::string& name,
                            std::function<void(routing::Store::Context, http::Response)> done)
{
    lane::Store::instance().list([name, done](lane::Store::Outcome outcome) {
        const lane::Store::Record* record = nullptr;
        for (const auto& lane : outcome.snapshot.lanes)
            if (lane.name == name) record = &lane;
        if (!record) return done({}, error(404, "not_found", "no lane by that name is claimed",
                                          "\"lane\":" + manifest::jstr(name)));
        const auto& manifest = manifest::Store::instance().data();
        routing::Store::Context context;
        context.lane = name;
        context.ticketId = record->ticketId;
        context.workspace = record->workspace;
        context.incarnation = incarnation(*record);
        context.runtimeFolder = manifest.routingDir;
        context.pid = record->pid;
        context.queued = record->queued;
        for (const auto& workspace : manifest.workspaces) {
            if (workspace.name != record->workspace) continue;
            for (const auto& which : manifest::Store::commandKeys()) {
                const auto* command = workspace.command(which);
                if (command) context.commands.emplace(which, *command);
            }
        }
        if (record->queued) {
            context.busyCode = "resource_busy";
            context.busyDetail = "\"lane\":" + manifest::jstr(name)
                               + ",\"resource\":" + manifest::jstr(record->waiting.resource)
                               + ",\"held_by\":" + manifest::jstr(record->waiting.heldBy);
            context.busyRemedy = remedy::retry();
        } else if (record->pid > 0) {
            context.busyCode = "wrong_state";
            context.busyDetail = "\"lane\":" + manifest::jstr(name)
                               + ",\"pid\":" + std::to_string(record->pid);
            context.busyRemedy = remedy::retry();
        }
        done(std::move(context), {});
    });
}

routing::Store::SnapshotFn api::Routing::snapshot()
{
    return [](const routing::Store::Context& context,
              std::function<void(routing::Store::Snapshot)> done) {
        Manager::instance().routingSnapshot(context, std::move(done));
    };
}

void api::Manager::routingSnapshot(const routing::Store::Context& context,
                                   std::function<void(routing::Store::Snapshot)> done)
{
    lane::Store::instance().list([this, context, done](lane::Store::Outcome outcome) {
        for (const auto& record : outcome.snapshot.lanes) {
            if (record.name != context.lane) continue;
            if (Routing::incarnation(record) != context.incarnation)
                return done({{}, "lane incarnation changed"});
            const auto& manifest = manifest::Store::instance();
            const std::string effective = manifest.toJson();
            const std::string path = manifest.data().manifestPath;
            startQueue_.offload([record, effective, path] {
                const auto readGit = [worktree = record.worktree](
                        const std::vector<std::string>& args) {
                    return lane::git::runQuiet(worktree, args);
                };
                const auto result = routingInputs::fingerprint(record, effective, path, readGit);
                return routing::Store::Snapshot{result.source, result.error};
            }, std::move(done));
            return;
        }
        done({{}, "lane is no longer claimed"});
    });
}

routing::Store::ExecuteFn api::Routing::execute()
{
    return [](const routing::Store::Context& context, const routing::Store::Step& step,
              std::function<void(routing::Store::Event)> event,
              std::function<void(routing::Store::Started)> done) {
        lane::Store::instance().list([context, step, event, done](lane::Store::Outcome outcome) {
            const lane::Store::Record* record = nullptr;
            for (const auto& lane : outcome.snapshot.lanes)
                if (lane.name == context.lane) record = &lane;
            if (!record || incarnation(*record) != context.incarnation) {
                routing::Store::Started refused;
                refused.status = 409;
                refused.errorCode = "routing_unresolved";
                refused.errorMessage = "lane incarnation changed before dispatch";
                refused.errorDetail = "\"lane\":" + manifest::jstr(context.lane);
                refused.errorRemedy = remedy::none();
                return done(std::move(refused));
            }
            lane::command::start(context.lane, step.which,
                [event](const lane::command::Event& output) {
                    Socket::instance().publishCommand(lane::command::renderEvent(output),
                        commandScopes(output.lane, output.ticketId));
                    event({output.chunk, output.exited, output.exitCode});
                }, [done](lane::command::Started started) {
                    done({started.status, started.commandId, started.errorCode,
                          started.errorMessage, started.errorDetail, started.errorRemedy});
                }, *record);
        });
    };
}

routing::Store::Done api::Routing::response(http::Responder respond)
{
    return [respond](routing::Store::Answer answer) {
        http::Response response;
        response.status = answer.status;
        response.body = std::move(answer.body);
        respond(std::move(response));
    };
}

void api::Routing::submit(const http::Request& req, http::Responder respond)
{
    routing::Store::Request request;
    if (!parse(req.body, request))
        return respond(error(400, "bad_request", "invalid routing request", {}));
    const auto& keys = manifest::Store::commandKeys();
    for (const auto& step : request.steps) {
        if (std::find(keys.begin(), keys.end(), step.which) == keys.end())
            return respond(error(400, "bad_which", "unknown workspace command",
                                  "\"which\":" + manifest::jstr(step.which)));
    }
    context(laneNameOf(req), [request, respond](routing::Store::Context context,
                                                http::Response refusal) {
        if (refusal.status >= 400) return respond(std::move(refusal));
        for (const auto& step : request.steps) {
            if (!context.commands.count(step.which))
                return respond(error(404, "not_declared", "workspace does not declare the command",
                    "\"which\":" + manifest::jstr(step.which)
                    + ",\"workspace\":" + manifest::jstr(context.workspace)));
        }
        routing::Store::instance().submit(std::move(context), request, snapshot(), execute(),
                                          response(respond));
    });
}

void api::Routing::get(const http::Request& req, http::Responder respond)
{
    const auto id = req.params.find("request_id");
    if (id == req.params.end() || !requestId(id->second))
        return respond(error(400, "bad_request", "invalid routing request ID", {}));
    const auto option = req.params.find("include_output");
    if (option != req.params.end() && option->second != "true")
        return respond(error(400, "bad_request", "include_output must be true", {}));
    const bool includeOutput = option != req.params.end();
    const std::string requestId = id->second;
    context(laneNameOf(req), [requestId, includeOutput, respond](routing::Store::Context context,
                                                                http::Response refusal) {
        if (refusal.status >= 400) return respond(std::move(refusal));
        routing::Store::instance().get(std::move(context), requestId, includeOutput, snapshot(),
                                       response(respond));
    });
}
