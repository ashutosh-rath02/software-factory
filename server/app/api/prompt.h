#pragma once

#include <string>
#include <utility>
#include <vector>

namespace api::prompt {

std::string substitute(const std::string& text,
                       const std::vector<std::pair<std::string, std::string>>& values);

std::string trim(const std::string& text);

std::string ticketPathInLane(const std::string& ticketPath, const std::string& worktree);

std::vector<std::pair<std::string, std::string>> values(const std::string& ticketId,
                                                        const std::string& ticketPath,
                                                        const std::string& worktree,
                                                        const std::string& triggeredByRun);

bool read(const std::string& path, std::string& out);

struct Read {
    bool found = false;
    bool reachable = true;
    std::string text;
    std::string ticketPath;
    std::string inLane;
};

Read readFor(const std::string& promptPath, const std::string& ticketId,
             const std::string& worktree);

}
