#pragma once

#include <array>
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <core/component.h>
#include <core/queue.h>
#include <core/stream.h>
#include <core/task.h>

#include "confine.h"
#include "git.h"
#include "progress.h"

COMPONENT(lane, Store)
    struct Waiting {
        std::string resource;
        std::string heldBy;
        int position = 0;
        int holderPid = 0;
    };

    struct Words {
        std::string kind;
        std::string text;
        std::string at;
    };

    struct Record {
        std::string name;
        std::string ticketId;
        std::string workspace;
        std::string role;
        std::string branch;
        std::string worktree;
        std::vector<std::string> alsoWorkspaces;
        bool queued = false;
        Waiting waiting;
        std::vector<std::string> resourcesHeld;
        long long claimedAtMs = 0;
        int pid = 0;
        bool worktreeReady = false;
        int absentPasses = 0;

        std::string runId;
        std::string sessionId;
        double costUsd = 0;
        Words lastWords;
        bool hasWords = false;
        bool wordsPending = false;

        long long lastOutputMs = 0;
        bool silentAnnounced = false;
        bool turnActive = false;
        bool turnEnded = false;
        std::string outcome;
        std::string goalStatus;
    };

    struct ResourceView {
        std::string name;
        std::string description;
        bool hasDescription = false;
        bool exclusive = false;
        std::string heldBy;
        int holderPid = 0;
        std::vector<std::string> queue;
    };

    static bool sameIncarnation(const Record& left, const Record& right);

    struct Warning {
        std::string code;
        std::string message;
        std::string path;
    };

    struct Snapshot {
        std::vector<Record> lanes;
        std::vector<ResourceView> resources;
        std::vector<Warning> warnings;
        int seatsTotal = 0;
        int seatsUsed = 0;
        progress::History stages;
    };

    struct Outcome {
        int status = 200;
        std::string errorCode;
        std::string errorMessage;
        std::string errorDetail;
        std::string errorRemedy;

        Record lane;
        Snapshot snapshot;

        bool released = false;
        bool worktreeRemoved = false;
        bool killed = false;
        int killedPid = 0;
        bool recovered = false;
        std::vector<std::string> reCarried;
        std::string reason;
        int changes = 0;
        std::vector<std::string> ignored;

        bool freed = false;
        std::string resource;
        std::string holder;
        int holderPid = 0;
        std::vector<std::string> freedResources;
    };

    struct ClaimRequest {
        std::string ticketId;
        std::string workspace;
        std::string role = "run";
        std::string requestId;
        std::vector<std::string> alsoWorkspaces;
    };

    struct Preflight {
        bool ok = true;
        std::string errorCode;
        std::string errorMessage;
        std::string errorDetail;
        std::string errorRemedy;
    };

    static Preflight preflight(const std::string& workspace,
                        const std::vector<std::pair<std::string, std::string>>& toolchain,
                        const std::vector<std::pair<std::string, std::string>>& devices);

    struct Spawn {
        int pid = 0;
        std::string error;
        bool laneExists = false;
        std::string busy;
        std::string errorCode;
        std::string ticketId;
        std::string role;
        std::string worktree;
        std::string program;
    };

    struct Sink {
        std::function<void(const std::string& text)> chunk;
        std::function<void(int exitCode)> exit;
    };

    using StopPid = std::shared_ptr<std::atomic<int>>;

    struct Launch {
        std::vector<std::string> argv;
        std::string shell;
        std::vector<std::pair<std::string, std::string>> env;
        std::vector<std::string> unsetEnv;
        int stdinFd = -1;
        std::string label;
        Sink sink;
        bool holdResourcesUntilExit = false;

        std::string configDirVar;
        std::string agentProfile;
        std::vector<std::pair<std::string, std::string>> configSeeded;
        std::vector<std::pair<std::string, std::string>> configBorrowed;
        std::vector<std::string> runtimeDirectories;
        std::atomic<bool>* cancelled = nullptr;
        StopPid stopPid;
        std::optional<Record> expected;
    };

    using ConfigPreparer = std::function<void(const std::string&, const std::string&,
                            std::vector<std::pair<std::string, std::string>>&,
                            std::vector<std::pair<std::string, std::string>>&,
                            std::vector<std::string>&)>;
    void onPrepareConfig(ConfigPreparer prepare);

    using Done = std::function<void(Outcome)>;

    struct StageRequest {
        std::string ticketId;
        std::string lane;
        std::vector<std::string> sequence;
        std::string source;
        progress::Report report;
    };

    using StageHistory = progress::History;

    struct RoleStart {
        std::string ticketId;
        std::string role;
        std::string stage;
        std::string triggeredByLane;
        std::string triggeredByRun;
        std::string workspace;
    };

    struct RoleAdmission {
        bool admitted = false;
        bool deferred = false;
        bool refused = false;
        std::string reason;
        RoleStart start;
    };

    struct RoleState {
        std::string role;
        std::string stage;
        std::string state;
        std::string lane;
        std::string runId;
    };

    using RoleStates = std::map<std::string, std::vector<RoleState>>;

    void init() override;
    void start() override;
    void stop() override;
    core::Queue& exec() { return exec_; }

    using ResourceHandler = std::function<void(const ResourceView&)>;
    void onResource(ResourceHandler handler);

    using MembershipHandler = std::function<void(const Record&, bool claimed)>;
    void onMembership(MembershipHandler handler);

    using IsolationHandler =
        std::function<void(const std::string& lane, const confine::Refusal&)>;
    void onIsolation(IsolationHandler handler);

    void list(Done done);
    void claim(const ClaimRequest& request, Done done);
    void release(const std::string& lane, bool force, bool discardIgnored, Done done);

    void releaseResource(const std::string& resource, const std::string& lane, Done done);

    void recover(const std::string& lane,
                 const std::vector<std::pair<std::string, std::string>>& borrowed, Done done);

    void startGate(const std::string& lane, Done done);
    void holdStart(const std::string& lane,
                   std::function<void(bool exists, bool held)> done);
    void releaseStartHold(const std::string& lane, std::function<void()> done = {});

    void forgetLane(const std::string& lane, std::function<void(bool)> done);

    void failNextSpawns(int times, std::function<void()> done);

    void attachProcess(const std::string& lane, const std::string& command,
                       std::function<void(Spawn)> done);

    void attachProcess(const std::string& lane, const std::string& command,
                       const std::string& label, Sink sink,
                       std::function<void(Spawn)> done, std::optional<Record> expected = {});

    void startProcess(const std::string& lane, const Launch& launch,
                      std::function<void(Spawn)> done);
    void killProcessIfCurrent(const std::string& lane, int pid);

    using ExitHandler =
        std::function<void(const std::string& lane, int pid, int status)>;
    void onProcessExit(ExitHandler handler);

    void setRun(const std::string& lane, const std::string& runId, const std::string& sessionId);

    void setCost(const std::string& lane, double costUsd);

    void setWords(const std::string& lane, const std::string& kind, const std::string& text);

    using WordsHandler = std::function<void(const std::string& lane, const std::string& ticketId,
                                            const Words&)>;
    void onWords(WordsHandler handler);

    void reportStage(const StageRequest& request, std::function<void(progress::Stage)> done);

    void stageHistory(std::function<void(StageHistory)> done);

    void requestRole(const RoleStart& request, std::function<void(RoleAdmission)> done);

    using RoleHandler = std::function<void(const RoleStart&)>;
    void onRoleStart(RoleHandler handler);

    using GrantHandler = std::function<void(const std::string& lane,
                                            const std::string& ticketId,
                                            const std::string& role)>;
    void onResourceGranted(GrantHandler handler);

    void noteRoleStarted(const RoleStart& start, const std::string& lane);
    void forgetRolesAt_(const std::string& lane);

    void forgetRole(const RoleStart& start);

    void roleStates(std::function<void(RoleStates)> done);

    void noteActivity(const std::string& lane);

    void setTurn(const std::string& lane, bool active, bool ended);

    void setGoalStatus(const std::string& lane, const std::string& status);

    void noteOutcome(const std::string& lane, const std::string& outcome);

    using SilenceHandler = std::function<void(const std::string& lane, bool silent,
                                              long long silentForS)>;
    void onSilence(SilenceHandler handler);

    void retimeSilence();

    void exists(const std::string& lane, std::function<void(bool)> done);

    bool testHooksEnabled() const;

private:
    struct Reservation {
        bool adoptable = false;
        std::string worktree;
        int number = 0;
        int from = 0;
    };

    struct ClaimScan {
        Preflight verdict;
        Reservation scan;
    };

    struct Worktree {
        git::Result result;
        git::Holding held;
        bool blocked = false;
        bool unreadable = false;
        bool ours = false;
        bool onDisk = false;
        bool conflicted = false;
        int changes = 0;
    };

    struct Exit {
        int status = 0;
        int code = -1;
    };

    struct Started {
        Spawn spawn;
        confine::Policy policy;
        int readFd = -1;
    };

    struct PendingSpawn {
        Record record;
        StopPid stopPid;
        bool complete = false;
        bool releaseRequested = false;
        bool discardIgnored = false;
        Done releaseDone;
    };

    void completePending_(const std::string& lane, std::shared_ptr<PendingSpawn> pending);
    void release(const std::string& lane, bool force, bool discardIgnored, Done done,
                 std::shared_ptr<PendingSpawn> pending);

    static Reservation scanFor_(const std::string& worktreeRoot, const std::string& registered,
                                int from);
    void scan_(const std::string& branch, int from, std::function<void(Reservation)> done);
    static std::vector<std::pair<std::string, std::string>> toolchainFor_(
        const std::string& workspace, const std::vector<std::string>& also);
    void reserve_(const ClaimRequest& request, const std::string& role,
                  const Reservation& scan, Done done);

    struct Watch {
        Watch(core::Queue& queue, int fd) : out(queue, fd) {}
        core::Stream out;
        std::array<char, 4096> buf {};
        std::string line;
        confine::Policy policy;
        std::string lane;
        std::string label;
        Sink sink;
        bool holdResourcesUntilExit = false;
        StopPid stopPid;
        std::function<void()> afterReap;
        int pid = 0;
    };

    struct RoleRecord {
        RoleStart start;
        std::string state;
        std::string lane;
        std::string runId;
    };

    void settle_(const std::string& lane, int pid, std::shared_ptr<Outcome> result, Done done,
                 int attempt);

    void kill_(int pid, int waited, std::function<void(bool)> done);

    void watch_(std::shared_ptr<Watch> watch);
    void report_(const std::shared_ptr<Watch>& watch, const std::string& line);
    void finish_(std::shared_ptr<Watch> watch);

    void armSweep_();
    void sweep_();

    void arm_(const std::string& lane);
    void disarm_(const std::string& lane);
    void publishWords_(const std::string& lane);
    void armWords_(const std::string& lane);
    void stamp_(const std::string& lane);
    void endSilence_(Record& record);

    bool seatFree_() const;
    void drainRoles_();

    Snapshot snapshot_() const;
    std::vector<std::string> resourcesFor_(const std::string& workspace) const;
    ResourceView viewOf_(const std::string& name) const;
    std::vector<ResourceView> resourceViews_() const;
    bool grant_(Record& record);
    void reclaimFromStalledTriggers_(std::vector<std::string>& changed);
    void reconcile_();
    bool triggerStillWorking_(const Record& record) const;
    void advance_(std::vector<std::string>& changed);
    void publish_(const std::vector<std::string>& changed);
    Record* find_(const std::string& name);
    std::string branchFor_(const std::string& ticketId, const std::string& role) const;
    static std::string ticketFor_(const std::string& branch, std::string& roleOut);

    std::string root_;
    std::string gitRoot_;
    std::string worktreeRoot_;
    std::string lanesDir_;
    std::string agentConfigRoot_;
    ConfigPreparer prepareConfig_;
    int configLockFd_ = -1;
    void sweepAgentConfigRoots_();
    int seats_ = 3;

    core::Queue exec_;

    void sweepTick_();
    core::Task sweepTask_ {exec_, [this] { sweepTick_(); }};
    std::vector<Record> lanes_;

    void recover_();
    void adopt_();
    std::vector<Warning> warnings_;
    void persist_(const Record& record);
    void forget_(const std::string& name);
    std::map<std::string, std::string> holder_;
    std::map<std::string, std::vector<std::string>> queue_;
    std::map<std::string, std::string> byRequestId_;
    StageHistory stages_;
    std::map<std::string, RoleRecord> roles_;
    std::vector<std::string> pendingRoles_;
    int startingRoles_ = 0;
    std::vector<RoleHandler> roleHandlers_;
    std::vector<GrantHandler> grantHandlers_;
    std::vector<ResourceHandler> handlers_;
    std::vector<IsolationHandler> isolationHandlers_;
    std::vector<MembershipHandler> membershipHandlers_;
    std::vector<ExitHandler> exitHandlers_;
    std::vector<SilenceHandler> silenceHandlers_;
    std::vector<WordsHandler> wordsHandlers_;
    std::map<std::string, std::shared_ptr<Watch>> watches_;
    std::map<std::string, std::shared_ptr<PendingSpawn>> spawning_;
    std::set<std::string> startHolds_;
    std::map<std::string, core::Task> silence_;
    std::map<std::string, core::Task> words_;
    std::map<std::string, core::Task> settling_;
    std::map<int, core::Task> killing_;

    int nextNumber_ = 1;
    int failSpawns_ = 0;
    bool stopping_ = false;
COMPONENT_END
