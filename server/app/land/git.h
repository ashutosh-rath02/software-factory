#pragma once

#include <string>
#include <vector>

namespace land::git {

const std::vector<std::string>& subcommands();

struct Output {
    bool ok = false;
    int exitCode = -1;
    std::string text;
    std::string failure;
};

Output run(const std::string& cwd, const std::string& subcommand,
           const std::vector<std::string>& args,
           const std::vector<std::string>& config = {}, int timeoutMs = 120000);

std::vector<std::string> lines(const std::string& text);

std::string firstLine(const std::string& text);

}
