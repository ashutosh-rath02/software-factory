#pragma once

#include <string>
#include <vector>

namespace delivery {

const std::vector<std::string>& gitSubcommands();

struct Output {
    bool ok = false;
    int exitCode = -1;
    std::string text;
    std::string failure;
};

Output git(const std::string& cwd, const std::string& subcommand,
           const std::vector<std::string>& args, int timeoutMs = 5000);

Output run(const std::string& cwd, const std::vector<std::string>& argv, int timeoutMs = 8000);

}
