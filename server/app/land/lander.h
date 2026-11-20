#pragma once

#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/queue.h>

COMPONENT(land, Lander)
    struct Request {
        std::string ticketId;
        std::string title;
        std::string branch;
        std::string base;
        std::string ticketPath;
        std::string reportsDir;
        std::string worktree;
        std::vector<std::string> roles;
        struct Session {
            std::string runId;
            std::string role;
            std::string branch;
            std::string tip;
        };
        std::vector<Session> sessions;
        bool checkoutIsIdle = true;
        bool worktreeIsALane = false;
    };

    using Refusals = std::map<std::string, std::string>;

    void init() override;
    void stop() override;

    void consider(const std::vector<Request>& requests);

    void refusalsThen(std::function<void(std::shared_ptr<const Refusals>)> done);

private:
    struct Attempt {
        std::string ticketId;
        std::string fingerprint;
        std::string refusal;
        std::string landedTip;
        bool examined = false;
    };

    enum class Verdict {
        None,
        Pass,
        Fail,
        Ambiguous,
    };

    struct Scratch {
        std::string path;
        std::string head;
        std::string refusal;
    };

    struct Candidate {
        std::string branch;
        std::string tip;
    };

    struct Decision {
        int chosen = -1;
        std::string divergence;
    };

    void runNext_();
    void publish_();

    Attempt attempt_(const Request& request, const std::string& previous,
                     const std::string& landed);
    std::string bringBaseIn_(const Request& request, const std::string& branchTip,
                             const std::string& commit, std::string& refusalOut);
    std::string squash_(const Request& request, const std::string& baseTip,
                        const std::string& mergedTip, std::string& refusalOut);
    struct Written {
        std::string author;
        std::vector<std::string> paths;
    };
    static bool isVerdictPath_(const Request& request, const std::string& path);
    std::vector<Written> workOn_(const Request& request, const std::string& mergedTip);
    std::string authorOf_(const Request& request, const std::string& mergedTip,
                          const std::vector<std::string>& everyone);
    struct Holder {
        std::string path;
        bool known = false;
    };

    Holder holderOf_(const std::string& branch);
    std::vector<std::string> pathsInTheWay_(const std::string& at, const std::string& from,
                                            const std::string& to);
    std::string moveBase_(const Request& request, const std::string& baseTip,
                          const std::string& landed, const Holder& holder);
    void reap_(const Request& request, const std::string& mergedTip);

    Scratch openScratch_(const std::string& purpose, const std::string& at);
    bool ownScratchAt_(const std::string& path);
    std::string adoptedFromRemote_(const Request& request, const std::string& baseTip,
                                   const std::string& remoteTip);
    void closeScratch_(const Scratch& scratch);
    std::string verdictPath_(const Request& request, const std::string& ref) const;
    bool refHasVerdict_(const Request& request, const std::string& ref);
    std::vector<std::string> otherVerdictDocuments_(const Request& request,
                                                    const std::string& ref);
    std::vector<Candidate> candidatesFor_(const Request& request, const std::string& branchTip);
    Decision decideAmong_(const std::vector<Candidate>& deciding);
    std::string sessionThatWroteTheVerdict_(const Request& request,
                                            const std::string& readBranch,
                                            const std::string& path);
    std::string remoteRefFor_(const std::string& base);

    static std::string statusOf_(const std::string& document);
    static std::string headingOf_(const std::string& document);
    static Verdict verdictOf_(const std::string& heading);
    static std::string resolve_(const std::string& cwd, const std::string& what);
    static std::string trim_(const std::string& value);

    std::string root_;
    core::Queue queue_;
    std::vector<Request> pending_;
    std::map<std::string, std::string> fingerprints_;
    std::map<std::string, std::string> landedTips_;
    Refusals refusals_;
    std::shared_ptr<const Refusals> published_;
    bool busy_ = false;
    bool stopping_ = false;
COMPONENT_END
