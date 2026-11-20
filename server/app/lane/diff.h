#pragma once

#include <string>
#include <vector>

namespace lane::diff {

const std::vector<std::string>& gitSubcommands();

struct Query {
    std::string lane;
    std::string branch;
    std::string worktree;
    std::string path;
    bool wantPatch = false;
};

std::string read(const Query& query);

}
