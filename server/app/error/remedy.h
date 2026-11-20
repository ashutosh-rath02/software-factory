#pragma once

#include <string>
#include <vector>

namespace remedy {

struct Cost {
    bool turn = false;
    bool session = false;
    bool worktree = false;
    bool ignoredFiles = false;
};

std::string retry();
std::string reloadManifest();
std::string claimLane(const std::string& ticketId, const std::string& workspace);
std::string openLane(const std::string& lane);
std::string startRun(const std::string& lane);
std::string endSession(const std::string& lane);
std::string killRun(const std::string& lane);
std::string recoverLane(const std::string& lane);
std::string releaseLane(const std::string& lane, bool force, Cost cost);
std::string discardIgnored(const std::string& lane, const std::vector<std::string>& paths,
                           Cost cost);
std::string releaseResource(const std::string& resource, const std::string& lane);

std::string none();

std::string member(const std::string& remedy);

}
