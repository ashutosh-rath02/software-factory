#include "command.h"

#include <algorithm>
#include <atomic>
#include <memory>
#include <sstream>


#include <core/worker.h>
#include <app/error/remedy.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>

#include "git.h"
#include "store.h"

namespace {

using manifest::jstr;
std::string nextId()
{
    static std::atomic<long long> counter {1};
    const long long n = counter.fetch_add(1);
    std::ostringstream o;
    o << "cmd/";
    if (n < 10) o << "0";
    o << n;
    return o.str();
}

const manifest::Store::Workspace* workspaceNamed(const std::string& name)
{
    for (const auto& w : manifest::Store::instance().data().workspaces)
        if (w.name == name) return &w;
    return nullptr;
}

}

void lane::command::start(const std::string& name, const std::string& which, Emit emit,
                          std::function<void(Started)> done, std::optional<Store::Record> expected)
{
    Store::instance().list([name, which, emit, done, expected](Store::Outcome listed) {
        Started started;
        started.lane = name;
        started.which = which;

        const Store::Record* record = nullptr;
        for (const auto& r : listed.snapshot.lanes)
            if (r.name == name) record = &r;

        if (!record) {
            started.status = 404;
            started.errorCode = "not_found";
            started.errorRemedy = remedy::reloadManifest();
            started.errorMessage = "no lane by that name is claimed";
            started.errorDetail = "\"lane\":" + jstr(name);
            return done(started);
        }

        if (expected && !Store::sameIncarnation(*record, *expected)) {
            started.status = 409;
            started.errorCode = "routing_unresolved";
            started.errorRemedy = remedy::none();
            started.errorMessage = "the lane incarnation changed before command admission";
            started.errorDetail = "\"lane\":" + jstr(name);
            return done(started);
        }

        const std::vector<std::string>& names = manifest::Store::commandKeys();
        if (std::find(names.begin(), names.end(), which) == names.end()) {
            std::string list, sentence;
            for (std::size_t i = 0; i < names.size(); ++i) {
                if (i) list += ",";
                list += jstr(names[i]);
                if (i) sentence += i + 1 == names.size() ? " or " : ", ";
                sentence += names[i];
            }
            started.status = 400;
            started.errorCode = "bad_which";
            started.errorRemedy = remedy::none();
            started.errorMessage = "a command is one of the workspace's own: " + sentence;
            started.errorDetail = "\"which\":" + jstr(which)
                                + ",\"declared\":[" + list + "]";
            return done(started);
        }

        const manifest::Store::Workspace* workspace = workspaceNamed(record->workspace);
        const std::string* text = workspace ? workspace->command(which) : nullptr;
        if (!text) {
            started.status = 404;
            started.errorCode = "not_declared";
            started.errorRemedy = remedy::reloadManifest();
            started.errorMessage = "this workspace declares no " + which + " command";
            started.errorDetail =
                "\"field\":" + jstr("workspaces." + record->workspace + "." + which)
                + ",\"workspace\":" + jstr(record->workspace)
                + ",\"path\":" + jstr(manifest::Store::instance().data().manifestPath);
            return done(started);
        }

        if (record->queued) {
            const Store::Waiting waiting = record->waiting;
            const int holderPid = waiting.holderPid;
            const bool alive = git::isRunning(holderPid);

            started.status = 409;
            started.errorCode = "resource_busy";
            started.errorRemedy = alive
                ? remedy::releaseLane(waiting.heldBy, true,
                                      remedy::Cost{false, true, true, false})
                : remedy::releaseResource(waiting.resource, waiting.heldBy);
            started.errorMessage = alive
                ? "an exclusive resource this lane needs is held by another lane"
                : "an exclusive resource this lane needs is held by a lane whose "
                  "process is not running";
            started.errorDetail = "\"lane\":" + jstr(name)
                + ",\"resource\":" + jstr(waiting.resource)
                + ",\"held_by\":" + jstr(waiting.heldBy)
                + ",\"holder_alive\":" + (alive ? "true" : "false")
                + ",\"holder_pid\":" + std::to_string(holderPid)
                + ",\"position\":" + std::to_string(waiting.position);
            return done(started);
        }

        started.command = *text;
        started.commandId = nextId();

        const std::string ticketId = record->ticketId;
        const std::string commandId = started.commandId;

        Event base;
        base.lane = name;
        base.ticketId = ticketId;
        base.commandId = commandId;
        base.which = which;

        Store::Sink sink;
        if (emit) {
            sink.chunk = [emit, base](const std::string& text) {
                Event e = base;
                e.chunk = text;
                emit(e);
            };
            sink.exit = [emit, base](int code) {
                Event e = base;
                e.exited = true;
                e.exitCode = code;
                emit(e);
            };
        }

        const Store::Record admitted = *record;
        Store::instance().attachProcess(
            name, started.command, commandId, sink, [started, done](Store::Spawn spawn) mutable {
                if (!spawn.error.empty()) {
                    started.status = spawn.errorCode == "spawn_failed" ? 500 : 409;
                    started.errorCode = spawn.errorCode.empty() ? "confinement_unavailable"
                                                                : spawn.errorCode;
                    started.errorRemedy = remedy::retry();
                    started.errorMessage = spawn.error;
                    started.errorDetail = "\"lane\":" + jstr(started.lane)
                                        + ",\"program\":" + jstr(spawn.program)
                                        + ",\"reason\":" + jstr(spawn.error);
                    return done(started);
                }
                if (spawn.pid <= 0) {
                    if (!spawn.laneExists) {
                        started.status = 404;
                        started.errorCode = "not_found";
                        started.errorRemedy = remedy::reloadManifest();
                        started.errorMessage = "no lane by that name is claimed";
                        started.errorDetail = "\"lane\":" + jstr(started.lane);
                        return done(started);
                    }
                    started.status = 409;
                    started.errorCode = "wrong_state";
                    started.errorRemedy = remedy::endSession(started.lane);
                    started.errorMessage = "this lane already holds a live process";
                    started.errorDetail = "\"lane\":" + jstr(started.lane)
                                        + ",\"state\":\"running\",\"command_id\":"
                                        + (spawn.busy.empty() ? "null" : jstr(spawn.busy));
                    return done(started);
                }
                started.status = 202;
                done(started);
            }, admitted);
    });
}

std::string lane::command::renderStarted(const Started& started)
{
    std::ostringstream o;
    o << "{\"lane\":" << jstr(started.lane)
      << ",\"command_id\":" << jstr(started.commandId)
      << ",\"which\":" << jstr(started.which)
      << ",\"command\":" << jstr(started.command) << "}";
    return o.str();
}

std::string lane::command::renderError(const Started& started)
{
    std::ostringstream o;
    o << "{\"error\":{\"code\":" << jstr(started.errorCode)
      << ",\"message\":" << jstr(started.errorMessage)
      << ",\"detail\":{" << started.errorDetail << "}"
      << remedy::member(started.errorRemedy) << "}}";
    return o.str();
}

std::string lane::command::renderEvent(const Event& event)
{
    std::ostringstream o;
    o << "\"lane\":" << jstr(event.lane)
      << ",\"command_id\":" << jstr(event.commandId)
      << ",\"which\":" << jstr(event.which);
    if (event.exited) o << ",\"exit_code\":" << event.exitCode;
    else o << ",\"chunk\":" << jstr(event.chunk);
    return o.str();
}
