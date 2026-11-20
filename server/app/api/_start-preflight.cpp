#include "manager.h"

#include <algorithm>
#include <deque>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>

#include <app/agent/launch.h>
#include <app/error/remedy.h>
#include <app/lane/store.h>
#include <app/lane/git.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <core/task.h>

static_assert(std::atomic<bool>::is_always_lock_free);
static_assert(std::atomic<int>::is_always_lock_free);

struct api::Manager::StartState {
    std::unique_ptr<core::Task> timer;
    std::function<void(http::Response)> done;
    int pid = 0;
    int exitCode = -1;
    bool spawnKnown = false;
    bool exited = false;
    bool timedOut = false;
    std::string spawnError;
    std::string output;
    std::string capturePath;
    std::deque<std::string> pendingOutput;
    bool writing = false;
    bool outputTruncated = false;
    bool captureFailed = false;
    std::shared_ptr<std::atomic<int>> stopPid = std::make_shared<std::atomic<int>>(0);
};

void api::Manager::publishStartSnapshot_()
{
    auto snapshot = std::make_shared<std::vector<std::shared_ptr<StartState>>>();
    for (const auto& [name, state] : starts_) snapshot->push_back(state);
    std::shared_ptr<const std::vector<std::shared_ptr<StartState>>> immutable = snapshot;
    std::atomic_store(&stopSnapshot_, std::move(immutable));
}

static http::Response preflightResponse(int status, const std::string& code,
                                  const std::string& lane, const std::string& reason,
                                  int exitCode = -1, const std::string& output = {},
                                  const std::string& capturePath = {}, bool truncated = false)
{
    http::Response response;
    response.status = status;
    response.body = "{\"error\":{\"code\":" + manifest::jstr(code)
                  + ",\"message\":" + manifest::jstr(reason)
                  + ",\"detail\":{\"lane\":" + manifest::jstr(lane)
                  + ",\"exit_code\":" + (exitCode < 0 ? "null" : std::to_string(exitCode))
                  + ",\"output\":" + manifest::jstr(output)
                  + ",\"capture_path\":" + manifest::jstr(capturePath)
                  + ",\"output_truncated\":" + (truncated ? "true" : "false") + "}"
                  + remedy::member(remedy::retry()) + "}}";
    return response;
}

void api::Manager::reserveStart(const std::string& lane,
                                std::function<void(bool, bool)> done)
{
    if (stoppingStarts_.load()) return done(false, false);
    bool configured = false;
    for (const auto& workspace : manifest::Store::instance().data().workspaces)
        configured |= workspace.hasStartPreflight;
    if (!configured) return done(true, false);
    lane::Store::instance().holdStart(lane, [this, lane, done = std::move(done)](
        bool exists, bool held) {
        startQueue_.post([this, lane, exists, held, done] {
            if (!exists) return done(false, true);
            if (stoppingStarts_.load() || !held || starts_.count(lane)) {
                if (held) lane::Store::instance().releaseStartHold(lane);
                return done(false, false);
            }
            starts_[lane] = std::make_shared<StartState>();
            publishStartSnapshot_();
            if (stoppingStarts_.load()) {
                starts_.erase(lane);
                publishStartSnapshot_();
                lane::Store::instance().releaseStartHold(lane);
                return done(false, false);
            }
            done(true, false);
        });
    });
}

void api::Manager::releaseStart(const std::string& lane, std::function<void()> done)
{
    startQueue_.post([this, lane, done = std::move(done)] {
        if (starts_.erase(lane)) {
            publishStartSnapshot_();
            return lane::Store::instance().releaseStartHold(lane, done);
        }
        if (done) done();
    });
}

void api::Manager::stopStartPreflights()
{
    stoppingStarts_.store(true);
    if (auto snapshot = std::atomic_load(&stopSnapshot_))
        for (const auto& state : *snapshot) {
            int pid = state->stopPid->load();
            if (pid > 0 && state->stopPid->compare_exchange_strong(pid, -pid)) {
                lane::git::signalGroup(pid, SIGKILL);
                state->stopPid->store(0);
            }
        }
    startQueue_.post([this] {
        for (auto& [lane, state] : starts_) {
            if (state->timer) state->timer->stop();
            state->done = {};
            state->timedOut = true;
            if (state->pid > 0) lane::Store::instance().killProcessIfCurrent(lane, state->pid);
            lane::Store::instance().releaseStartHold(lane);
        }
        starts_.clear();
        publishStartSnapshot_();
    });
}

void api::Manager::writePreflightCapture_(const std::string& lane,
                                          const std::shared_ptr<StartState>& state)
{
    if (state->writing || state->pendingOutput.empty()) return;
    state->writing = true;
    std::string chunk = std::move(state->pendingOutput.front());
    state->pendingOutput.pop_front();
    startQueue_.offload(
        [path = state->capturePath, chunk = std::move(chunk)] {
            std::ofstream out(path, std::ios::binary | std::ios::app);
            if (!out) return false;
            out.write(chunk.data(), static_cast<std::streamsize>(chunk.size()));
            return out.good();
        },
        [this, lane, state](bool written) {
            state->writing = false;
            if (!written) state->captureFailed = true;
            writePreflightCapture_(lane, state);
            resolvePreflight_(lane, state);
        });
}

void api::Manager::resolvePreflight_(const std::string& lane,
                                     const std::shared_ptr<StartState>& state)
{
    if (!state->spawnKnown || (state->pid > 0 && !state->exited)
        || state->writing || !state->pendingOutput.empty()) return;
    if (state->timer) state->timer->stop();
    if (!state->done) return;
    auto done = std::move(state->done);
    if (stoppingStarts_.load())
        return done(preflightResponse(409, "start_preflight_failed", lane,
                                      "the start was cancelled during shutdown",
                                      state->exitCode, state->output, state->capturePath,
                                      state->outputTruncated));
    if (state->timedOut)
        return done(preflightResponse(504, "start_preflight_timeout", lane,
                                      "the workspace start preflight timed out",
                                      state->exitCode, state->output, state->capturePath,
                                      state->outputTruncated));
    if (state->pid <= 0 || state->exitCode != 0 || state->captureFailed)
        return done(preflightResponse(409, "start_preflight_failed", lane,
                                      state->spawnError.empty()
                                          ? "the workspace start preflight failed"
                                          : state->spawnError,
                                      state->exitCode, state->output, state->capturePath,
                                      state->outputTruncated));
    const std::string capturePath = state->capturePath;
    startQueue_.offload([capturePath] {
        std::error_code ignored;
        std::filesystem::remove(capturePath, ignored);
    }, [] {});
    done(http::Response{});
}

void api::Manager::runStartPreflight(const std::string& lane, const std::string& workspace,
                                      const std::string& ticketId, const std::string& profile,
                                      const std::string& model,
                                      std::function<void(http::Response)> done)
{
    if (stoppingStarts_.load())
        return done(preflightResponse(409, "start_preflight_failed", lane,
                                      "the start was cancelled during shutdown"));
    const manifest::Store::Workspace* selected = nullptr;
    for (const auto& w : manifest::Store::instance().data().workspaces)
        if (w.name == workspace) selected = &w;
    if (!selected || !selected->hasStartPreflight) return done(http::Response{});
    const auto config = selected->startPreflight;
    if (std::find(config.exemptTicketIds.begin(), config.exemptTicketIds.end(), ticketId)
        != config.exemptTicketIds.end()) return done(http::Response{});

    startQueue_.post([this, lane, workspace, ticketId, profile, model, config,
                      done = std::move(done)]() mutable {
        auto held = starts_.find(lane);
        if (stoppingStarts_.load() || held == starts_.end())
            return done(preflightResponse(409, "start_preflight_failed", lane,
                                          "the start reservation was lost"));
        auto state = held->second;
        state->done = std::move(done);
        state->capturePath = manifest::Store::instance().data().runsDir
                           + "/.start-preflight/" + agent::launch::randomHex(12) + ".log";
        startQueue_.offload(
            [path = state->capturePath] {
                std::error_code ec;
                std::filesystem::create_directories(std::filesystem::path(path).parent_path(), ec);
                if (ec) return false;
                std::ofstream out(path, std::ios::binary | std::ios::trunc);
                return out.good();
            },
            [this, lane, workspace, ticketId, profile, model, config, state](bool ready) {
        const auto live = starts_.find(lane);
        if (stoppingStarts_.load() || live == starts_.end()
            || live->second != state || !state->done) {
            const std::string path = state->capturePath;
            startQueue_.offload([path] {
                std::error_code ignored;
                std::filesystem::remove(path, ignored);
            }, [] {});
            return;
        }
        if (!ready) {
            auto callback = std::move(state->done);
            return callback(preflightResponse(409, "start_preflight_failed", lane,
                                             "could not create start preflight capture",
                                             -1, {}, state->capturePath));
        }
        state->timer = std::make_unique<core::Task>(startQueue_, [this, lane, state] {
            state->timedOut = true;
            if (state->pid > 0) lane::Store::instance().killProcessIfCurrent(lane, state->pid);
            resolvePreflight_(lane, state);
        });
        state->timer->start(config.timeoutSeconds * 1000, 1);

        lane::Store::Launch launch;
        launch.shell = config.command;
        launch.label = "start preflight";
        launch.agentProfile = profile;
        launch.cancelled = &stoppingStarts_;
        launch.stopPid = state->stopPid;
        const auto& data = manifest::Store::instance().data();
        const manifest::Store::Agent* selectedAgent = &data.agent;
        for (const auto& candidate : data.agents)
            if (candidate.name == profile) selectedAgent = &candidate;
        launch.configDirVar = selectedAgent->driver == "codex"
            ? "CODEX_HOME" : agent::launch::configDirVariable();
        launch.env = {{"FACTORY_TICKET_ID", ticketId}, {"FACTORY_WORKSPACE", workspace},
                      {"FACTORY_AGENT_PROFILE", profile}, {"FACTORY_AGENT_MODEL", model}};
        launch.sink.chunk = [this, lane, state](const std::string& text) {
            startQueue_.post([this, lane, state, text] {
                if (state->output.size() + text.size() > 4096)
                    state->outputTruncated = true;
                if (state->output.size() < 4096)
                    state->output.append(text.substr(0, 4096 - state->output.size()));
                state->pendingOutput.push_back(text);
                writePreflightCapture_(lane, state);
            });
        };
        launch.sink.exit = [this, lane, state](int code) {
            startQueue_.post([this, lane, state, code] {
                state->exitCode = code;
                state->exited = true;
                if (state->timer) state->timer->stop();
                resolvePreflight_(lane, state);
            });
        };
        lane::Store::instance().startProcess(lane, launch,
            [this, lane, state](lane::Store::Spawn result) {
                startQueue_.post([this, lane, state, result = std::move(result)] {
                    state->spawnKnown = true;
                    state->pid = result.pid;
                    state->spawnError = result.error;
                    if (state->pid <= 0 && state->timer) state->timer->stop();
                    if (state->timedOut && state->pid > 0)
                        lane::Store::instance().killProcessIfCurrent(lane, state->pid);
                    resolvePreflight_(lane, state);
                });
            });
            });
    });
}
