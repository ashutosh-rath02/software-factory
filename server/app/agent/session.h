#pragma once

#include <functional>
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <core/component.h>
#include <core/queue.h>
#include <core/stream.h>
#include <core/task.h>

#include <app/manifest/store.h>

#include "transcript.h"

COMPONENT(agent, Store)
    struct SpawnRequest {
        std::string lane;
        std::string label;
        std::vector<std::string> argv;
        std::vector<std::pair<std::string, std::string>> env;
        std::vector<std::string> unsetEnv;
        int stdinFd = -1;

        std::string configDirVar;
        std::string agentProfile;
    };

    struct SpawnResult {
        int pid = 0;
        std::string error;
        std::string errorCode;
        std::string ticketId;
        std::string role;
        std::string worktree;
        std::string program;
    };

    using Spawner = std::function<void(const SpawnRequest&, std::function<void(SpawnResult)>)>;

    struct StartRequest {
        std::string agentProfile;
        std::string lane;
        std::string worktree;
        std::string ticketId;
        manifest::Store::Tree tree;
        bool hasTree = false;
        std::string role;
        std::string prompt;
        bool hasPrompt = false;
        bool resume = true;
        std::string model;
        bool hasModel = false;
        std::string effort;
        bool hasEffort = false;
    };

    struct Answer {
        int status = 200;
        std::string errorCode;
        std::string errorRemedy;
        std::string errorMessage;
        std::string errorDetail;
        std::string body;
    };

    struct RunReport {
        std::string agentProfile;
        std::string driver;
        std::string model;
        manifest::Store::Tree tree;
        bool costKnown = true;
        std::string lane;
        std::string ticketId;
        std::string runId;
        std::string outcome;
        std::string startedIso;
        std::string endedIso;
        int n = 1;
        long long durationS = 0;
        int stage = 0;
        double costUsd = 0;
        int exitCode = -1;
        int signal = 0;
    };

    struct TurnReport {
        std::string lane;
        std::string runId;
        std::string role;
        bool active = false;
        bool ended = false;
        int n = 0;
        double turnCostUsd = 0;
        double costUsd = 0;
        bool hasCost = false;
    };

    struct EntryEvent {
        std::string lane;
        std::string runId;
        long long seq = 0;
        bool patch = false;
        std::string json;
    };

    struct TokenHolder {
        bool ok = false;
        std::string lane;
        std::string runId;
        std::string ticketId;
    };

    using Done = std::function<void(Answer)>;

    void init() override;
    void start() override;
    void stop() override;

    core::Queue& queue() { return queue_; }

    void setSpawner(Spawner spawner);
    using Approval = std::function<void(const std::string&, const std::string&,
                                        const std::string&, std::function<void(bool)>)>;
    void setApproval(Approval approval) { approval_ = std::move(approval); }

    using RunHandler = std::function<void(const RunReport&)>;
    void onRun(RunHandler handler);

    using LaneRunFn = std::function<void(const std::string& lane, const std::string& runId,
                                         const std::string& sessionId)>;
    void setLaneRun(LaneRunFn fn);

    using EntryHandler = std::function<void(const EntryEvent&)>;
    void onEntry(EntryHandler handler);

    using CostFn = std::function<void(const std::string& lane, double costUsd)>;
    void setLaneCost(CostFn fn);

    using WordsFn = std::function<void(const std::string& lane, const std::string& kind,
                                       const std::string& text)>;
    void setLaneWords(WordsFn fn);

    using TurnHandler = std::function<void(const TurnReport&)>;
    void onTurn(TurnHandler handler);

    void noteOutput(const std::string& lane, const std::string& bytes);

    void noteSilence(const std::string& lane, bool silent, long long silentForS);

    void forgetLane(const std::string& lane);

    void onLaneReason(std::function<void(const std::string&, const std::string&)> handler);

    using GoalHandler = std::function<void(const std::string&, const std::string&)>;
    void onGoalStatus(GoalHandler handler);

    void startRun(const StartRequest& request, Done done);
    void message(const std::string& lane, const std::string& text,
                 const std::string& requestId, Done done);
    void interrupt(const std::string& lane, Done done);
    void end(const std::string& lane, Done done);
    void kill(const std::string& lane, int graceS, Done done);

    void resolveToken(const std::string& token, std::function<void(TokenHolder)> done);

    void appendEntry(const std::string& lane, const std::string& members,
                     std::function<void(long long)> done);

    void noteExit(const std::string& lane, int pid, int status);

    void noteTurnEnded(const std::string& lane);

private:
    struct Run {
        std::string agentProfile;
        std::string model;
        manifest::Store::Tree tree;
        std::string effort;
        bool codex = false;
        std::string codexApprovalPolicy = "on-request";
        int rpcId = 3;
        std::string codexThread;
        std::string codexTurn;
        bool goalSet = false;
        int goalRequestId = 0;
        bool hasUsage = false;
        long long inputTokens = 0;
        long long cachedInputTokens = 0;
        long long outputTokens = 0;
        long long reasoningOutputTokens = 0;
        std::string worktree;
        std::string initialPrompt;
        Done starting;
        std::shared_ptr<core::Task> startupTimeout;
        bool failed = false;
        std::string lane;
        std::string ticketId;
        std::string role;
        std::string runId;
        std::string sessionId;
        std::string token;
        std::string fifoPath;
        int pid = 0;
        int exitCode = -1;
        int signal = 0;
        int n = 1;
        long long startedMs = 0;
        std::string startedIso;

        bool turnActive = false;
        bool ending = false;
        bool killing = false;
        bool conversationGone = false;
        long long entrySeq = 0;

        std::vector<std::string> held;

        std::shared_ptr<core::Stream> in;
        std::string outbox;
        bool writing = false;
        bool closeAfterFlush = false;
        std::shared_ptr<core::Task> grace;

        transcript::Parser parser;
        std::map<std::string, long long> seqByCall;
        long long silenceSeq = 0;
        std::vector<std::string> pendingLines;
        bool writingLog = false;
        double costUsd = 0;
    };

    using RunPtr = std::shared_ptr<Run>;

    RunPtr find_(const std::string& lane);
    void write_(const RunPtr& run, const std::string& text);
    void writeFrame_(const RunPtr& run, const std::string& frame);
    void finishStart_(const RunPtr& run, const std::string& error = {});
    void pump_(const RunPtr& run);
    void closeStdin_(const RunPtr& run);
    void deliverHeld_(const RunPtr& run, int& delivered);
    void settleOrphans_(const std::string& exceptRunId = {});
    void publish_(const RunPtr& run, const std::string& outcome);
    Answer noSuchRun_(const std::string& lane) const;

    long long emit_(const RunPtr& run, const std::string& members, bool patch,
                    const std::string& toolUseId);
    long long emitAt_(const RunPtr& run, long long seq, const std::string& members, bool patch);
    void apply_(const RunPtr& run, const transcript::Entry& entry);
    void flushLog_(const RunPtr& run);
    void writeMeta_(const RunPtr& run, const std::string& outcome);
    void endTurn_(const RunPtr& run);
    void publishTurn_(const RunPtr& run, bool active, bool ended, bool hasCost,
                      double turnCostUsd);

    static std::string jsonEscape(const std::string& s);
    static long long nowMs();
    static std::string nowIso();
    static std::string dollars(double v);
    static std::string silenceMembers(long long forS, bool silent);
    static std::string trimmed(const std::string& s);

    core::Queue queue_;
    std::map<std::string, RunPtr> runs_;
    std::map<std::string, std::string> sessionByLane_;
    std::map<std::string, int> runCountByLane_;
    std::map<std::string, std::string> answeredRequests_;
    std::vector<RunHandler> runHandlers_;
    std::vector<EntryHandler> entryHandlers_;
    std::vector<TurnHandler> turnHandlers_;
    Spawner spawner_;
    Approval approval_;
    LaneRunFn laneRun_;
    CostFn laneCost_;
    using ReasonFn = std::function<void(const std::string&, const std::string&)>;
    ReasonFn laneReason_;
    GoalHandler goalStatus_;
    WordsFn laneWords_;
    int nextRun_ = 1;
    bool stopping_ = false;
COMPONENT_END
