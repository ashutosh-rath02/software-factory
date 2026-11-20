#pragma once

#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/queue.h>

COMPONENT(routing, Store)
    struct Step {
        std::string which;
        std::vector<int> acceptedExitCodes;
        bool reuse = false;
        bool requireDecision = false;
    };

    struct Request {
        std::string requestId;
        std::vector<Step> steps;
    };

    struct Context {
        std::string lane;
        std::string ticketId;
        std::string workspace;
        std::string incarnation;
        std::string runtimeFolder;
        std::map<std::string, std::string> commands;
        int pid = 0;
        bool queued = false;
        std::string busyCode;
        std::string busyDetail;
        std::string busyRemedy;
    };

    struct Snapshot {
        std::string source;
        std::string error;
    };

    struct Started {
        int status = 202;
        std::string commandId;
        std::string errorCode;
        std::string errorMessage;
        std::string errorDetail;
        std::string errorRemedy;
    };

    struct Event {
        std::string chunk;
        bool exited = false;
        int exitCode = -1;
    };

    struct Answer {
        int status = 200;
        std::string body;
    };

    using SnapshotFn = std::function<void(const Context&, std::function<void(Snapshot)>)>;
    using ExecuteFn = std::function<void(const Context&, const Step&,
                                         std::function<void(Event)>,
                                         std::function<void(Started)>)>;
    using Done = std::function<void(Answer)>;

    void init() override;
    void stop() override;

    void submit(Context context, Request request, SnapshotFn snapshot,
                ExecuteFn execute, Done done);
    void get(Context context, std::string requestId, bool includeOutput,
             SnapshotFn snapshot, Done done);

private:
    struct Job;
    struct Lane;
    using JobPtr = std::shared_ptr<Job>;
    using LanePtr = std::shared_ptr<Lane>;
    void enqueue_(Context context, std::function<void(LanePtr)> operation);
    void next_(const LanePtr& lane);
    void release_(const LanePtr& lane);
    void admit_(const LanePtr& lane, Context context, Request request,
                SnapshotFn snapshot, ExecuteFn execute, Done done);
    void inspect_(const JobPtr& job, Context context, bool includeOutput,
                  SnapshotFn snapshot, Done done);
    void ready_(const JobPtr& job, std::function<void()> work);
    void commit_(const JobPtr& job, std::function<void(std::string)> done);
    void advance_(const JobPtr& job);
    void dispatch_(const JobPtr& job);
    void started_(const JobPtr& job, unsigned long long generation, Started started);
    void event_(const JobPtr& job, unsigned long long generation, Event event);
    void terminal_(const JobPtr& job);
    void settle_(const JobPtr& job, const std::string& reason);
    void delivered_(const JobPtr& job, int status);
    void checkpoint_(const JobPtr& job);
    core::Queue queue_;
    std::map<std::string, LanePtr> lanes_;
    bool stopping_ = false;
COMPONENT_END
