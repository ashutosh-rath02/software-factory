#include "session.h"

#include <chrono>
#include <csignal>
#include <iomanip>
#include <sstream>
#include <sys/wait.h>

#include <glog/logging.h>

#include <config.h>
#include <core/engine.h>
#include <core/worker.h>
#include <app/error/remedy.h>
#include <app/manifest/store.h>

#include "history.h"
#include "launch.h"
#include "codex_protocol.h"
#include <algorithm>
#include "proc.h"
#include <app/manifest/json.h>

std::string agent::Store::jsonEscape(const std::string& s)
{
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    static const char* hex = "0123456789abcdef";
                    out += "\\u00";
                    out += hex[(c >> 4) & 0xF];
                    out += hex[c & 0xF];
                } else {
                    out += c;
                }
        }
    }
    return out;
}

using manifest::jstr;
long long agent::Store::nowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

std::string agent::Store::nowIso()
{
    const auto now = std::chrono::system_clock::now();
    const std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

std::string agent::Store::dollars(double v)
{
    std::ostringstream o;
    o << "$" << std::fixed << std::setprecision(2) << v;
    return o.str();
}

std::string agent::Store::silenceMembers(long long silentForS, bool patch)
{
    return std::string("\"kind\":\"silence\"") + (patch ? ",\"patch\":true" : "")
         + ",\"silent_for_s\":" + std::to_string(silentForS < 0 ? 0 : silentForS);
}

std::string agent::Store::trimmed(const std::string& s)
{
    const auto first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const auto last = s.find_last_not_of(" \t\r\n");
    return s.substr(first, last - first + 1);
}

void agent::Store::init()
{
}

void agent::Store::start()
{
    LOG(INFO) << "agent ready";
    settleOrphans_();
}

void agent::Store::settleOrphans_(const std::string& exceptRunId)
{
    const std::string dir = manifest::Store::instance().data().runsDir;
    if (dir.empty()) return;
    std::set<std::string> live;
    for (const auto& [lane, run] : runs_) { (void)lane; if (run) live.insert(run->runId); }
    if (!exceptRunId.empty()) live.insert(exceptRunId);
    core::Worker::instance().postSerialized([dir, live] { history::settleOrphans(dir, live); });
}

void agent::Store::stop()
{
    stopping_ = true;
}

void agent::Store::setSpawner(Spawner spawner) { spawner_ = std::move(spawner); }
void agent::Store::onRun(RunHandler handler) { runHandlers_.push_back(std::move(handler)); }
void agent::Store::setLaneRun(LaneRunFn fn) { laneRun_ = std::move(fn); }
void agent::Store::onEntry(EntryHandler handler) { entryHandlers_.push_back(std::move(handler)); }
void agent::Store::setLaneCost(CostFn fn) { laneCost_ = std::move(fn); }

void agent::Store::setLaneWords(WordsFn fn) { laneWords_ = std::move(fn); }

void agent::Store::onTurn(TurnHandler handler) { turnHandlers_.push_back(std::move(handler)); }

void agent::Store::publishTurn_(const RunPtr& run, bool active, bool ended, bool hasCost,
                                double turnCostUsd)
{
    TurnReport report;
    report.lane = run->lane;
    report.runId = run->runId;
    report.role = run->role;
    report.active = active;
    report.ended = ended;
    report.n = run->parser.turns();
    report.turnCostUsd = turnCostUsd;
    report.costUsd = run->costUsd;
    report.hasCost = hasCost;
    for (const auto& handler : turnHandlers_) handler(report);
}

agent::Store::RunPtr agent::Store::find_(const std::string& lane)
{
    auto it = runs_.find(lane);
    return it == runs_.end() ? RunPtr{} : it->second;
}

agent::Store::Answer agent::Store::noSuchRun_(const std::string& lane) const
{
    Answer out;
    out.status = 409;
    out.errorCode = "wrong_state";
    out.errorRemedy = remedy::startRun(lane);
    out.errorMessage = "this lane holds no live run";
    out.errorDetail = "\"lane\":" + jstr(lane) + ",\"state\":\"not_started\"";
    return out;
}

void agent::Store::write_(const RunPtr& run, const std::string& text)
{
    writeFrame_(run, run->codex
                     ? codex::startTurn(run->rpcId++, run->codexThread, text, run->effort)
                     : launch::userMessage(text));
}

void agent::Store::writeFrame_(const RunPtr& run, const std::string& frame)
{
    run->outbox += frame;
    run->outbox += "\n";
    pump_(run);
}

void agent::Store::pump_(const RunPtr& run)
{
    if (run->writing || !run->in) return;
    if (run->outbox.empty()) {
        if (run->closeAfterFlush) closeStdin_(run);
        return;
    }

    auto chunk = std::make_shared<std::string>(std::move(run->outbox));
    run->outbox.clear();
    run->writing = true;

    run->in->write(*chunk, [this, run](bool ok) {
        run->writing = false;
        if (!ok) {
            run->outbox.clear();
            return;
        }
        pump_(run);
    });
}

void agent::Store::closeStdin_(const RunPtr& run)
{
    if (!run->in) return;
    run->in->close();
    run->in.reset();
}

void agent::Store::deliverHeld_(const RunPtr& run, int& delivered)
{
    if (run->codex) {
        delivered = 0;
        if (run->turnActive || run->held.empty()) return;
        const std::string text = run->held.front();
        run->held.erase(run->held.begin());
        write_(run, text);
        run->turnActive = true;
        delivered = 1;
        return;
    }
    delivered = static_cast<int>(run->held.size());
    for (const auto& text : run->held) write_(run, text);
    run->held.clear();
}

void agent::Store::publish_(const RunPtr& run, const std::string& outcome)
{
    RunReport report;
    report.agentProfile = run->agentProfile;
    report.driver = run->codex ? "codex" : "claude";
    report.model = run->model;
    report.tree = run->tree;
    report.costKnown = !run->codex;
    report.lane = run->lane;
    report.ticketId = run->ticketId;
    report.runId = run->runId;
    report.outcome = outcome;
    report.startedIso = run->startedIso;
    report.n = run->n;
    report.durationS = run->startedMs > 0 ? (nowMs() - run->startedMs) / 1000 : 0;
    report.costUsd = run->costUsd;
    report.exitCode = run->exitCode;
    report.signal = run->signal;
    if (outcome != "running") report.endedIso = nowIso();
    for (const auto& handler : runHandlers_) handler(report);
}

long long agent::Store::emit_(const RunPtr& run, const std::string& members, bool patch,
                              const std::string& toolUseId)
{
    long long seq = 0;
    if (patch) {
        auto known = run->seqByCall.find(toolUseId);
        if (known == run->seqByCall.end()) return 0;
        seq = known->second;
        run->seqByCall.erase(known);
    } else {
        seq = ++run->entrySeq;
        if (!toolUseId.empty()) run->seqByCall[toolUseId] = seq;
    }
    return emitAt_(run, seq, members, patch);
}

long long agent::Store::emitAt_(const RunPtr& run, long long seq, const std::string& members,
                                bool patch)
{
    const std::string json = "{\"seq\":" + std::to_string(seq) + ",\"ts\":" + jstr(nowIso())
                           + "," + members + "}";

    run->pendingLines.push_back(json);
    flushLog_(run);

    EntryEvent event;
    event.lane = run->lane;
    event.runId = run->runId;
    event.seq = seq;
    event.patch = patch;
    event.json = json;
    for (const auto& handler : entryHandlers_) handler(event);
    return seq;
}

void agent::Store::apply_(const RunPtr& run, const transcript::Entry& entry)
{
    if (entry.control) {
        if (!run->codex) return;
        if (entry.method == "turn/started") {
            if (entry.threadId == run->codexThread && !entry.turnId.empty()) {
                run->codexTurn = entry.turnId;
                if (!run->turnActive) {
                    run->turnActive = true;
                    publishTurn_(run, true, false, false, 0);
                }
            }
            return;
        }
        if (entry.method == "thread/goal/updated") {
            if (entry.threadId == run->codexThread && goalStatus_
                && (entry.goalStatus == "active" || entry.goalStatus == "complete"
                    || entry.goalStatus == "blocked" || entry.goalStatus == "budgetLimited"
                    || entry.goalStatus == "usageLimited"))
                goalStatus_(run->lane, entry.goalStatus);
            return;
        }
        if (entry.method == "thread/tokenUsage/updated") {
            if (entry.threadId != run->codexThread || !entry.hasUsage) return;
            run->hasUsage = true;
            run->inputTokens = entry.inputTokens;
            run->cachedInputTokens = entry.cachedInputTokens;
            run->outputTokens = entry.outputTokens;
            run->reasoningOutputTokens = entry.reasoningOutputTokens;
            writeMeta_(run, "running");
            return;
        }
        if (!entry.rpcId.empty()) {
            if (entry.approvalTool.empty() || !approval_) {
                writeFrame_(run, "{\"id\":" + entry.rpcId
                    + ",\"error\":{\"code\":-32601,\"message\":\"unsupported client request\"}}");
            } else {
                approval_(run->lane, entry.approvalTool, entry.approvalArgument,
                    [this, run, id = entry.rpcId](bool allowed) {
                        queue_.post([this, run, id, allowed] {
                            if (find_(run->lane) != run) return;
                            writeFrame_(run, "{\"id\":" + id + ",\"result\":{\"decision\":"
                                + jstr(allowed ? "accept" : "decline") + "}}");
                        });
                    });
            }
            return;
        }
        if (!entry.rpcError.empty()) {
            emit_(run, "\"kind\":\"error\",\"text\":" + jstr(entry.rpcError), false, {});
            if (run->starting) {
                run->failed = true;
                finishStart_(run, entry.rpcError);
                proc::signalGroup(run->pid, SIGTERM);
            } else endTurn_(run);
            return;
        }
        if (run->goalRequestId > 0 && entry.requestId == run->goalRequestId) {
            run->goalRequestId = 0;
            if (entry.threadId != run->codexThread || entry.goalStatus != "active") {
                run->failed = true;
                finishStart_(run, "the goal was not accepted as active");
                proc::signalGroup(run->pid, SIGTERM);
                return;
            }
            run->goalSet = true;
            if (!run->initialPrompt.empty()) {
                run->turnActive = true;
                write_(run, run->initialPrompt);
                publishTurn_(run, true, false, false, 0);
            }
            finishStart_(run);
            return;
        }
        if (entry.requestId == 1) {
            writeFrame_(run, "{\"method\":\"initialized\",\"params\":{}}");
            writeFrame_(run, codex::startThread(2, run->model, run->worktree, run->codexThread,
                                                run->codexApprovalPolicy));
        } else if (entry.requestId == 2 && !entry.threadId.empty()) {
            run->codexThread = entry.threadId;
            run->sessionId = entry.threadId;
            sessionByLane_[run->lane + ":" + run->agentProfile] = entry.threadId;
            if (laneRun_) laneRun_(run->lane, run->runId, run->sessionId);
            writeMeta_(run, "running");
            if (run->tree.continuation == "goal" && !run->goalSet) {
                run->goalRequestId = run->rpcId++;
                writeFrame_(run, codex::setGoal(run->goalRequestId, run->codexThread,
                    run->ticketId + ": complete the ticket and independent QA handover",
                    run->tree.tokenBudget));
            } else {
                if (!run->initialPrompt.empty()) write_(run, run->initialPrompt);
                finishStart_(run);
            }
        }
        if (!entry.turnId.empty()) run->codexTurn = entry.turnId;
        return;
    }
    emit_(run, entry.members, entry.patch, entry.toolUseId);

    if (entry.hasWords && laneWords_) laneWords_(run->lane, entry.wordsKind, entry.words);

    if (entry.hasCost) {
        const double turnCost = entry.sessionCostUsd - run->costUsd;
        run->costUsd = entry.sessionCostUsd;
        if (laneCost_) laneCost_(run->lane, run->costUsd);
        publishTurn_(run, run->turnActive, false, true, turnCost);
        writeMeta_(run, "running");
    }
    if (entry.endsTurn) endTurn_(run);
}

void agent::Store::onLaneReason(std::function<void(const std::string&, const std::string&)> h)
{
    laneReason_ = std::move(h);
}

void agent::Store::onGoalStatus(GoalHandler handler)
{
    goalStatus_ = std::move(handler);
}

void agent::Store::forgetLane(const std::string& lane)
{
    queue_.post([this, lane] {
        for (auto it = sessionByLane_.begin(); it != sessionByLane_.end();) {
            if (it->first == lane || it->first.compare(0, lane.size() + 1, lane + ":") == 0)
                it = sessionByLane_.erase(it);
            else ++it;
        }
        runCountByLane_.erase(lane);
    });
}

void agent::Store::flushLog_(const RunPtr& run)
{
    if (run->writingLog || run->pendingLines.empty()) return;
    run->writingLog = true;

    auto lines = std::make_shared<std::vector<std::string>>(std::move(run->pendingLines));
    run->pendingLines.clear();
    const std::string dir = manifest::Store::instance().data().runsDir;
    const std::string runId = run->runId;

    queue_.offload(
        [dir, runId, lines] { history::append(dir, runId, *lines); },
        [this, run] {
            run->writingLog = false;
            flushLog_(run);
        });
}

void agent::Store::writeMeta_(const RunPtr& run, const std::string& outcome)
{
    history::Meta meta;
    meta.agentProfile = run->agentProfile;
    meta.driver = run->codex ? "codex" : "claude";
    meta.model = run->model;
    meta.tree = run->tree;
    meta.effort = run->effort;
    meta.hasEffort = !run->effort.empty();
    meta.runId = run->runId;
    meta.lane = run->lane;
    meta.ticketId = run->ticketId;
    meta.role = run->role;
    meta.sessionId = run->sessionId;
    meta.startedIso = run->startedIso;
    meta.outcome = outcome;
    meta.n = run->n;
    meta.durationS = run->startedMs > 0 ? (nowMs() - run->startedMs) / 1000 : 0;
    meta.costUsd = run->costUsd;
    meta.turns = run->parser.turns();
    meta.unmapped = run->parser.unmapped();
    meta.exitCode = run->exitCode;
    meta.signal = run->signal;
    meta.rateLimit = run->parser.rateLimit();
    meta.hasUsage = run->hasUsage;
    meta.usage.inputTokens = run->inputTokens;
    meta.usage.cachedInputTokens = run->cachedInputTokens;
    meta.usage.outputTokens = run->outputTokens;
    meta.usage.reasoningOutputTokens = run->reasoningOutputTokens;
    if (outcome != "running") meta.endedIso = nowIso();

    const std::string dir = manifest::Store::instance().data().runsDir;
    core::Worker::instance().postSerialized([dir, meta] { history::write(dir, meta); });
}

void agent::Store::endTurn_(const RunPtr& run)
{
    run->turnActive = false;
    run->codexTurn.clear();
    publishTurn_(run, false, true, false, 0);
    if (run->codex) {
        int delivered = 0;
        deliverHeld_(run, delivered);
        if (delivered) publishTurn_(run, true, false, false, 0);
        else if (run->ending) {
            run->closeAfterFlush = true;
            pump_(run);
        }
    }
}

void agent::Store::finishStart_(const RunPtr& run, const std::string& error)
{
    if (!run->starting) return;
    if (run->startupTimeout) run->startupTimeout->stop();
    auto done = std::move(run->starting);
    run->starting = {};
    Answer out;
    if (!error.empty()) {
        out.status = 502;
        out.errorCode = "agent_start_failed";
        out.errorMessage = error;
        out.errorRemedy = remedy::reloadManifest();
    } else {
        out.status = 201;
        out.body = "{\"run_id\":" + jstr(run->runId)
                 + ",\"agent\":" + jstr(run->agentProfile)
                 + ",\"driver\":" + jstr(run->codex ? "codex" : "claude")
                 + ",\"model\":" + jstr(run->model)
                 + ",\"tree\":" + manifest::Store::treeJson(run->tree)
                 + ",\"effort\":" + (run->effort.empty() ? std::string("null")
                                                          : jstr(run->effort))
                 + ",\"session_id\":" + jstr(run->sessionId)
                 + ",\"pid\":" + std::to_string(run->pid) + "}";
    }
    done(std::move(out));
}

void agent::Store::startRun(const StartRequest& request, Done done)
{
    queue_.post([this, request, done = std::move(done)] {
        if (find_(request.lane)) {
            Answer out;
            out.status = 409;
            out.errorCode = "wrong_state";
            out.errorRemedy = remedy::endSession(request.lane);
            out.errorMessage = "this lane already holds a live run";
            out.errorDetail = "\"lane\":" + jstr(request.lane) + ",\"state\":\"running\"";
            return done(std::move(out));
        }
        if (!spawner_) {
            Answer out;
            out.status = 500;
            out.errorCode = "spawn_failed";
            out.errorRemedy = remedy::retry();
            out.errorMessage = "no spawner is wired";
            out.errorDetail = "\"lane\":" + jstr(request.lane);
            return done(std::move(out));
        }

        const std::string prompt = request.hasPrompt && !trimmed(request.prompt).empty()
                                 ? request.prompt : std::string();

        const auto& manifestData = manifest::Store::instance().data();
        const std::string profileName = request.agentProfile.empty()
                                      ? manifestData.defaultAgent : request.agentProfile;
        const manifest::Store::Agent* profile = nullptr;
        for (const auto& declared : manifestData.agents)
            if (declared.name == profileName) profile = &declared;
        const bool effortUndeclared =
            request.hasEffort
            && (request.effort.empty() || !profile || !profile->hasEfforts
                || std::find(profile->efforts.begin(), profile->efforts.end(), request.effort)
                   == profile->efforts.end());
        if (!profile || effortUndeclared
            || (request.hasModel && !profile->models.empty()
                && std::find(profile->models.begin(), profile->models.end(), request.model)
                   == profile->models.end())) {
            Answer out;
            out.status = 400;
            out.errorCode = "invalid_agent_selection";
            out.errorMessage = effortUndeclared
                             ? "select one of the declared profile's efforts"
                             : "select a declared agent and one of its models";
            out.errorRemedy = remedy::reloadManifest();
            return done(std::move(out));
        }

        auto run = std::make_shared<Run>();
        run->agentProfile = profileName;
        run->model = request.hasModel ? request.model : profile->model;
        run->tree = request.tree;
        if (run->tree.name.empty()) run->tree.name = "off";
        run->effort = launch::resolveEffort(*profile, request.role,
                                            request.hasEffort ? request.effort : std::string());
        run->codex = profile->driver == "codex";
        run->codexApprovalPolicy = "on-request";
        if (profile->hasPermissionMode && profile->permissionMode == "edit_free")
            run->codexApprovalPolicy = "never";
        else if (profile->hasPermissionMode && profile->permissionMode == "ask_all")
            run->codexApprovalPolicy = "untrusted";
        run->initialPrompt = prompt;
        run->lane = request.lane;
        run->ticketId = request.ticketId;
        run->runId = "run-" + std::to_string(nextRun_++) + "-" + launch::randomHex(4);
        run->token = launch::randomHex(16);
        run->startedMs = nowMs();
        run->startedIso = nowIso();
        run->n = ++runCountByLane_[request.lane];
        run->turnActive = !prompt.empty() && run->tree.continuation != "goal";

        auto known = sessionByLane_.find(request.lane + ":" + profileName);
        const bool canResume = request.resume && known != sessionByLane_.end();
        run->sessionId = canResume ? known->second : launch::newSessionId();
        if (run->codex && canResume) run->codexThread = known->second;

        const manifest::Store::Data& data = manifest::Store::instance().data();
        run->fifoPath = data.runsDir + "/" + run->runId + "/stdin";

        launch::Options options;
        options.sessionId = run->sessionId;
        options.resume = canResume;
        options.model = run->model;
        options.hasModel = !run->model.empty();
        options.effort = run->effort;

        SpawnRequest spawn;
        spawn.lane = request.lane;
        spawn.agentProfile = profileName;
        spawn.label = run->runId;
        spawn.argv = launch::build(*profile, options);
        spawn.env = {
            {"FACTORY_RUN_ID", run->runId},
            {"FACTORY_PROGRESS_URL",
             "http://127.0.0.1:" + std::to_string(Config::HTTP_PORT) + "/api/progress"},
            {launch::permissionUrlVariable(),
             "http://127.0.0.1:" + std::to_string(Config::HTTP_PORT) + "/api/permission"},
            {"FACTORY_PROGRESS_TOKEN", run->token},
            {"FACTORY_TREE", run->tree.name},
        };
        if (request.hasTree) {
            std::string names;
            for (const auto& name : run->tree.agents) {
                if (!names.empty()) names += " ";
                names += name;
            }
            spawn.env.emplace_back("FACTORY_TREE_AGENTS", names);
            spawn.env.emplace_back("FACTORY_TREE_DECIDER", run->tree.decider);
        } else {
            spawn.unsetEnv = {"FACTORY_TREE_AGENTS", "FACTORY_TREE_DECIDER"};
        }
        spawn.configDirVar = run->codex ? "CODEX_HOME" : launch::configDirVariable();

        if (spawn.argv.empty()) {
            Answer out;
            out.status = 409;
            out.errorCode = "agent_not_declared";
            out.errorRemedy = remedy::reloadManifest();
            out.errorMessage = "this repository declares no agent command to run";
            out.errorDetail = "\"lane\":" + jstr(request.lane) + ",\"field\":\"agent.command\"";
            return done(std::move(out));
        }

        const std::string fifoPath = run->fifoPath;
        queue_.offload(
            [fifoPath] {
                std::pair<int, int> fds {-1, -1};
                proc::openStdinChannel(fifoPath, fds.first, fds.second);
                return fds;
            },
            [this, run, spawn, done, prompt](std::pair<int, int> fds) {
                if (fds.first < 0) {
                    Answer out;
                    out.status = 500;
                    out.errorCode = "spawn_failed";
                    out.errorRemedy = remedy::retry();
                    out.errorMessage = "the run's stdin channel could not be created";
                    out.errorDetail = "\"lane\":" + jstr(run->lane)
                                    + ",\"path\":" + jstr(run->fifoPath);
                    writeMeta_(run, "crashed");
                    return done(std::move(out));
                }

                SpawnRequest request = spawn;
                request.stdinFd = fds.first;
                const int writeFd = fds.second;

                spawner_(request, [this, run, writeFd, done, prompt](SpawnResult result) {
                    queue_.post([this, run, writeFd, done, result, prompt] {
                        if (result.pid <= 0) {
                            ::close(writeFd);
                            proc::removeStdinChannel(run->fifoPath);
                            Answer out;
                            out.status = result.errorCode == "not_found" ? 404
                                       : result.errorCode == "confinement_unavailable" ? 409
                                       : 500;
                            out.errorCode = result.errorCode.empty() ? "spawn_failed"
                                                                     : result.errorCode;
                            out.errorRemedy =
                                out.errorCode == "not_found" ? remedy::reloadManifest()
                              : out.errorCode == "wrong_state" ? remedy::endSession(run->lane)
                                                               : remedy::retry();
                            out.errorMessage =
                                !result.error.empty() ? result.error
                              : out.errorCode == "not_found"
                                    ? "no lane by that name is claimed"
                              : out.errorCode == "wrong_state"
                                    ? "this lane already holds a live process"
                                    : "the run could not be started";
                            out.errorDetail = "\"lane\":" + jstr(run->lane);
                            if (!result.program.empty())
                                out.errorDetail += ",\"program\":" + jstr(result.program);
                            if (!result.error.empty())
                                out.errorDetail += ",\"reason\":" + jstr(result.error);
                            writeMeta_(run, "crashed");
                            return done(std::move(out));
                        }

                        run->pid = result.pid;
                        run->ticketId = result.ticketId;
                        run->role = result.role;
                        run->worktree = result.worktree;
                        run->in = std::make_shared<core::Stream>(queue_, writeFd);
                        runs_[run->lane] = run;
                        run->starting = done;
                        if (run->codex) {
                            run->startupTimeout = std::make_shared<core::Task>(queue_, [this, run] {
                                if (!run->starting) return;
                                run->failed = true;
                                finishStart_(run, "Codex did not initialize within 30 seconds");
                                proc::signalGroup(run->pid, SIGKILL);
                            });
                            run->startupTimeout->start(30000, 1);
                            writeFrame_(run, codex::initialize(1));
                        }
                        else {
                            sessionByLane_[run->lane + ":" + run->agentProfile] = run->sessionId;
                            if (!prompt.empty()) write_(run, prompt);
                        }
                        if (laneRun_) laneRun_(run->lane, run->runId, run->sessionId);
                        writeMeta_(run, "running");
                        publish_(run, "running");
                        publishTurn_(run, run->turnActive, false, false, 0);

                        if (!run->codex) finishStart_(run);
                    });
                });
            });
    });
}

void agent::Store::message(const std::string& lane, const std::string& text,
                           const std::string& requestId, Done done)
{
    queue_.post([this, lane, text, requestId, done = std::move(done)] {
        if (!requestId.empty()) {
            auto seen = answeredRequests_.find(requestId);
            if (seen != answeredRequests_.end()) {
                Answer out;
                out.body = seen->second;
                return done(std::move(out));
            }
        }

        RunPtr run = find_(lane);
        if (!run) return done(noSuchRun_(lane));

        const std::string clean = trimmed(text);
        if (clean.empty()) {
            Answer out;
            out.status = 400;
            out.errorCode = "empty_message";
            out.errorRemedy = remedy::none();
            out.errorMessage = "a message with no text in it is not a message";
            out.errorDetail = "\"lane\":" + jstr(lane);
            return done(std::move(out));
        }

        std::string status;
        if (run->turnActive || run->starting) {
            run->held.push_back(clean);
            status = "held";
        } else {
            write_(run, clean);
            run->turnActive = true;
            publishTurn_(run, true, false, false, 0);
            status = "delivered";
        }

        const long long seq = emit_(run, "\"kind\":\"human\",\"text\":" + jstr(clean)
                                       + ",\"mark\":" + jstr(status), false, {});
        if (status == "held")
            emit_(run, "\"kind\":\"held\",\"held_count\":"
                       + std::to_string(run->held.size()), false, {});

        Answer out;
        out.body = "{\"status\":" + jstr(status)
                 + ",\"held_count\":" + std::to_string(run->held.size())
                 + ",\"entry_seq\":" + std::to_string(seq) + "}";
        if (!requestId.empty()) answeredRequests_[requestId] = out.body;
        done(std::move(out));
    });
}

void agent::Store::interrupt(const std::string& lane, Done done)
{
    queue_.post([this, lane, done = std::move(done)] {
        RunPtr run = find_(lane);
        if (!run) return done(noSuchRun_(lane));

        const bool midTurn = run->turnActive;
        if (run->codex) {
            if (midTurn && !run->codexTurn.empty())
                writeFrame_(run, codex::interrupt(run->rpcId++, run->codexThread, run->codexTurn));
            Answer out;
            out.body = std::string("{\"interrupted\":")
                     + (midTurn && !run->codexTurn.empty() ? "true" : "false")
                     + ",\"delivered\":0}";
            return done(std::move(out));
        }
        run->turnActive = false;

        const int pid = run->pid;
        queue_.offload(
            [pid, midTurn] { if (midTurn) proc::signalGroupMembers(pid, SIGINT); },
            [this, run, midTurn, done] {
                if (midTurn)
                    emit_(run, "\"kind\":\"stopped\",\"text\":"
                               + jstr("STOPPED BY YOU · turn "
                                      + std::to_string(run->parser.turns() + 1) + " · "
                                      + dollars(run->costUsd))
                               + ",\"sub\":\"what already ran stayed on disk — nothing was "
                                 "reverted, and the session is still alive\"",
                          false, {});

                int delivered = 0;
                deliverHeld_(run, delivered);
                if (delivered > 0) {
                    run->turnActive = true;
                    publishTurn_(run, true, false, false, 0);
                }

                Answer out;
                out.body = std::string("{\"interrupted\":") + (midTurn ? "true" : "false")
                         + ",\"delivered\":" + std::to_string(delivered) + "}";
                done(std::move(out));
            });
    });
}

void agent::Store::end(const std::string& lane, Done done)
{
    queue_.post([this, lane, done = std::move(done)] {
        RunPtr run = find_(lane);
        if (!run) return done(noSuchRun_(lane));

        run->ending = true;

        int delivered = 0;
        deliverHeld_(run, delivered);

        emit_(run, "\"kind\":\"ended\",\"text\":"
                   + jstr("SESSION ENDED · " + std::to_string(run->parser.turns()) + " turns · "
                          + dollars(run->costUsd))
                   + ",\"sub\":\"the session id is retained, so a later run can resume it\"",
              false, {});

        if (!run->codex || !run->turnActive) {
            run->closeAfterFlush = true;
            pump_(run);
        }

        Answer out;
        out.status = 202;
        out.body = "{\"ending\":true}";
        done(std::move(out));
    });
}

void agent::Store::kill(const std::string& lane, int graceS, Done done)
{
    queue_.post([this, lane, graceS, done = std::move(done)] {
        RunPtr run = find_(lane);
        if (!run) {
            Answer out;
            out.body = "{\"killing\":false}";
            return done(std::move(out));
        }

        run->killing = true;
        emit_(run, "\"kind\":\"killed\",\"text\":"
                   + jstr(std::string("KILLED BY YOU · ")
                          + (run->turnActive ? "mid-turn" : "between turns"))
                   + ",\"sub\":\"the process group was signalled, not just its leader\"",
              false, {});
        proc::signalGroup(run->pid, SIGTERM);

        const int pid = run->pid;
        const std::string name = run->lane;
        run->grace = std::make_shared<core::Task>(queue_, [this, name, pid] {
            RunPtr still = find_(name);
            if (!still || still->pid != pid) return;
            proc::signalGroup(pid, SIGKILL);
        });
        run->grace->start((graceS > 0 ? graceS : 5) * 1000, 1);

        Answer out;
        out.status = 202;
        out.body = "{\"killing\":true}";
        done(std::move(out));
    });
}

void agent::Store::noteTurnEnded(const std::string& lane)
{
    queue_.post([this, lane] {
        if (RunPtr run = find_(lane)) endTurn_(run);
    });
}

static const char* const NO_CONVERSATION = "No conversation found with session ID";

void agent::Store::noteOutput(const std::string& lane, const std::string& bytes)
{
    queue_.post([this, lane, bytes] {
        RunPtr run = find_(lane);
        if (!run) return;
        if (!run->conversationGone
            && bytes.find(NO_CONVERSATION) != std::string::npos)
            run->conversationGone = true;
        for (const auto& entry : run->parser.feed(bytes, nowMs())) apply_(run, entry);
    });
}

void agent::Store::noteSilence(const std::string& lane, bool silent, long long silentForS)
{
    queue_.post([this, lane, silent, silentForS] {
        RunPtr run = find_(lane);
        if (!run) return;
        if (silent) {
            run->silenceSeq = emit_(run, silenceMembers(silentForS, false), false, {});
            return;
        }
        if (run->silenceSeq == 0) return;
        emitAt_(run, run->silenceSeq, silenceMembers(silentForS, true), true);
        run->silenceSeq = 0;
    });
}

void agent::Store::resolveToken(const std::string& token,
                                std::function<void(TokenHolder)> done)
{
    queue_.post([this, token, done = std::move(done)] {
        TokenHolder out;
        if (!token.empty())
            for (const auto& [lane, run] : runs_)
                if (run->token == token) {
                    out.ok = true;
                    out.lane = lane;
                    out.runId = run->runId;
                    out.ticketId = run->ticketId;
                    break;
                }
        if (done) done(out);
    });
}

void agent::Store::appendEntry(const std::string& lane, const std::string& members,
                               std::function<void(long long)> done)
{
    queue_.post([this, lane, members, done = std::move(done)] {
        RunPtr run = find_(lane);
        const long long seq = run ? emit_(run, members, false, std::string()) : 0;
        if (done) done(seq);
    });
}

void agent::Store::noteExit(const std::string& lane, int pid, int status)
{
    queue_.post([this, lane, pid, status] {
        RunPtr run = find_(lane);
        if (!run || run->pid != pid) return;

        std::string outcome, reason;
        if (run->failed) outcome = "crashed";
        else if (run->ending)  outcome = "ended cleanly";
        else if (run->killing) outcome = "killed by you";
        else if (WIFEXITED(status) && WEXITSTATUS(status) == 0) outcome = "completed";
        else outcome = "crashed";
        if (run->conversationGone)
            reason = "the conversation " + run->sessionId + " is not there any more";

        if (WIFEXITED(status)) {
            run->exitCode = WEXITSTATUS(status);
            run->signal = 0;
        } else if (WIFSIGNALED(status)) {
            run->exitCode = -1;
            run->signal = WTERMSIG(status);
        }

        LOG(INFO) << "run " << run->runId << " in " << lane << " " << outcome
                  << (run->signal > 0 ? " on signal " + std::to_string(run->signal)
                      : run->exitCode >= 0 ? " with exit code " + std::to_string(run->exitCode)
                                           : std::string())
                  << " after " << (run->startedMs > 0 ? (nowMs() - run->startedMs) / 1000 : 0)
                  << "s, " << run->parser.turns() << " turn(s)";

        run->turnActive = false;
        finishStart_(run, "the agent exited before its session was ready");
        if (run->grace) run->grace->stop();
        closeStdin_(run);
        proc::removeStdinChannel(run->fifoPath);

        for (const auto& entry : run->parser.flush(nowMs())) apply_(run, entry);

        writeMeta_(run, outcome);
        if (!reason.empty() && laneReason_) laneReason_(lane, reason);
        publish_(run, outcome);
        const std::string endedRunId = run->runId;
        runs_.erase(lane);
        settleOrphans_(endedRunId);
    });
}
