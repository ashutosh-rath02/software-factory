#pragma once

#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/queue.h>

#include "rules.h"

COMPONENT(permissions, Store)
    struct Saved {
        bool ok = false;
        std::string error;
    };

    enum class Policy { AskAll, EditFree, NeverAsk };

    static std::string policyName(Policy policy);
    static bool parsePolicy(const std::string& text, Policy& out);

    struct Ask {
        std::string lane;
        std::string tool;
        std::string argument;
        bool hasArgument = false;
        std::string inputSummary;
    };

    struct Decision {
        bool allowed = false;
        bool asked = false;
        std::string reason;
        std::string decision;
        std::string ruleId;
        std::string requestId;
    };

    struct Frozen {
        std::string ticketId;
        int criterion = 0;
        std::string path;
    };

    struct Answer {
        int status = 200;
        std::string errorCode;
        std::string errorRemedy;
        std::string errorMessage;
        std::string errorDetail;
        std::string body;
    };

    struct Blocked {
        std::string reason;
        std::string requestId;
    };

    void init() override;
    void start() override;
    void stop() override;

    void ask(const Ask& request, std::function<void(Decision)> answer);

    void listRules(std::function<void(std::string)> done);
    void revoke(const std::string& ruleId, std::function<void(Answer)> done);
    void setPolicy(const std::string& policy, std::function<void(Answer)> done);
    void decide(const std::string& requestId, const std::string& decision,
                std::function<void(Answer)> done);

    using RequestHandler = std::function<void(const std::string& lane,
                                              const std::string& ticketId,
                                              const std::string& payload)>;
    void onRequest(RequestHandler handler);

    using Broadcast = std::function<void(const std::string& payload)>;
    void onResolved(Broadcast handler);
    void onRules(Broadcast handler);

    using EntryHandler = std::function<void(const std::string& lane, const std::string& runId,
                                            const std::string& entryJson)>;
    void onEntry(EntryHandler handler);

    std::size_t requestSubscribers() const { return requestHandlers_.size(); }

    void noteRun(const std::string& lane, const std::string& runId,
                 const std::string& ticketId, const std::string& outcome);

    void freeze(const std::string& runId, std::vector<Frozen> paths,
                std::function<void()> done);

    void blockedSnapshot(std::function<void(std::map<std::string, Blocked>)> done);

private:
    struct Pending {
        std::string requestId;
        std::string lane;
        std::string runId;
        std::string ticketId;
        std::string tool;
        std::string argument;
        bool hasArgument = false;
        std::string rulePattern;
        std::string frozenTicket;
        int frozenCriterion = 0;
        std::function<void(Decision)> answer;
    };

    std::string decideNow_(const Ask& request, const std::string& runId, std::string& ruleId,
                           const Frozen*& frozen);
    const Frozen* frozenBy_(const Ask& request, const std::string& runId) const;
    void emitRules_();
    void emitEntry_(const std::string& lane, const std::string& runId, const std::string& json);
    void resolve_(const std::string& requestId, const std::string& decision,
                  const std::string& ruleId, const Decision& outcome);
    std::vector<Rule> grantedRules_() const;
    std::string rulesBody_() const;
    static std::string hexId(const char* prefix);
    static bool isFileTool(const std::string& tool);
    static bool isReadingTool(const std::string& tool);
    static bool namesPath(const std::string& argument, const std::string& path);

    core::Queue queue_;
    std::string rulesPath_;
    std::string manifestPath_;

    Policy policy_ = Policy::AskAll;
    bool policyFromSession_ = false;

    std::vector<Rule> rules_;
    std::vector<Warning> warnings_;

    std::map<std::string, Pending> pending_;
    std::map<std::string, std::string> settled_;
    std::map<std::string, std::string> blockedByLane_;
    std::map<std::string, std::string> runByLane_;
    std::map<std::string, std::string> ticketByLane_;
    std::map<std::string, Policy> policyByRun_;
    std::map<std::string, std::vector<Frozen>> frozenByRun_;

    std::vector<RequestHandler> requestHandlers_;
    std::vector<Broadcast> resolvedHandlers_;
    std::vector<Broadcast> rulesHandlers_;
    std::vector<EntryHandler> entryHandlers_;

    unsigned long long nextId_ = 1;
    bool stopping_ = false;
COMPONENT_END
