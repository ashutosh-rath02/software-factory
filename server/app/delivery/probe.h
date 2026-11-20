#pragma once

#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/queue.h>
#include <core/file.h>
#include <core/task.h>


COMPONENT(delivery, Probe)
    struct Branch {
        std::string name;
        std::string tip;
        int ahead = 0;
        bool merged = false;
        bool pushed = false;
        enum class Dirt { Unknown, Clean, Dirty };
        Dirt dirty = Dirt::Unknown;
        std::string worktree;
        int pullRequest = 0;
        long long unpushedSinceS = 0;
        std::string author;
    };

    struct Snapshot {
        bool available = false;
        std::string reason;
        std::string base;
        std::string baseTip;
        std::string forge;
        bool forgeAnswered = false;
        std::map<std::string, Branch> branches;
        std::set<std::string> artefacts;
        std::set<std::string> mergedTips;
        std::vector<std::string> used;

        std::vector<std::string> inFlight;

        std::string fingerprint;
    };

    struct Subject {
        std::string ticketId;
        std::string claimedStatus;
        std::string reportFileName;
    };

    struct Holder {
        std::string lane;
        bool finished = false;
    };

    using LiveLanes = std::map<std::string, Holder>;

    struct State {
        std::string state = "unknown";
        std::string detail;
        std::string claimed;
        std::string reason;
        bool disagrees = false;
        bool needsYou = false;
    };

    static std::string healthFragment(const Snapshot* snapshot);

    static std::string toJson(const State& state);

    core::Queue& queue() { return queue_; }

    void init() override;
    void start() override;
    void stop() override;

    void snapshotThen(std::function<void(std::shared_ptr<const Snapshot>)> done);
    void requestPass();

    using ChangeHandler = std::function<void(std::shared_ptr<const Snapshot>)>;
    void onChange(ChangeHandler handler);
    void onSnapshot(ChangeHandler handler);

    static State decide(const Snapshot& snapshot, const Subject& subject,
                        const LiveLanes& live);

    static const Branch* branchFor(const Snapshot& snapshot, const std::string& ticketId);

private:
    void scheduleScan_();
    Snapshot scan_();
    void scanRepo_(const std::string& root, const std::vector<std::string>& reportsRel,
                   Snapshot& s, std::set<std::string>& used);
    void publish_(std::shared_ptr<Snapshot> snapshot);
    void startPass_();
    void watchRefs_();
    static std::vector<std::string> lines(const std::string& text);
    static std::pair<std::string, std::string> split(const std::string& line, char sep);
    static std::string strip(const std::string& value, const std::string& prefix);
    static int envInt(const char* name, int fallback);
    static std::string plural(int n, const char* one, const char* many);

    FILE_WATCH(Ref)
public:

    std::string root_;
    std::vector<std::string> reportsRel_;
    struct SubRepo { std::string root; std::vector<std::string> reportsRel; };
    std::vector<SubRepo> subRepos_;
    int pollMs_ = 5000;
    bool forgeUsable_ = true;

    core::Queue queue_;

    void pollTick_();
    core::Task pollTask_ {queue_, [this] { pollTick_(); }};
    std::shared_ptr<const Snapshot> snapshot_;
    std::vector<std::function<void(std::shared_ptr<const Snapshot>)>> waiters_;
    std::vector<ChangeHandler> handlers_;
    std::vector<ChangeHandler> snapshotHandlers_;
    std::vector<std::string> inFlightStatus_;
    bool passInFlight_ = false;
    bool passRequested_ = false;
    bool stopping_ = false;
COMPONENT_END
