#pragma once

#include <string>
#include <utility>
#include <vector>

namespace lane::confine {

struct Policy {
    bool ok = false;
    std::string reason;

    std::string binary;
    std::string repoRoot;
    std::string worktree;
    std::string gitCommonDir;
    std::string gitDir;
    std::string objectsDir;
    std::string refsDir;
    std::string logsRefsDir;
    std::vector<std::string> foreignRefs;
    std::string home;
    std::string scratch;
    std::vector<std::string> runtimeDirectories;

    std::vector<std::string> writable;

    std::vector<std::string> writableInside;

    std::vector<std::string> hostReadOnly;

    struct Device {
        std::string node;
        std::string declared;
    };
    std::vector<Device> devices;

    struct Toolchain {
        std::string host;
        std::string inside;
    };
    std::vector<Toolchain> toolchain;

    std::string agentConfig;
    std::string agentConfigInside;

    std::string hiddenHome;

    std::string resolvConf;

    struct AgentBinary {
        std::string host;
        std::string inside;
    };
    std::vector<AgentBinary> agentBinaries;

    std::string path;
};

struct AgentBinary {
    std::string name;
    std::string path;
};

std::string developerHome();

Policy build(const std::string& repoRoot, const std::string& worktree,
             const std::string& branch,
             const std::vector<std::string>& hostReadOnly = {},
             const std::vector<std::string>& devices = {},
             const std::string& agentConfig = "",
             const std::vector<std::pair<std::string, std::string>>& toolchain = {},
             const std::vector<AgentBinary>& agentBinaries = {},
             const std::vector<std::string>& runtimeDirectories = {});

struct Command {
    std::string shell;
    std::vector<std::string> argv;
    std::vector<std::pair<std::string, std::string>> env;
    std::vector<std::string> unsetEnv;
};

std::vector<std::string> wrap(const Policy& policy, const Command& command);
std::vector<std::string> wrap(const Policy& policy, const std::string& shellCommand);

struct Refusal {
    bool refused = false;
    std::string operation;
    std::string path;
};

Refusal inspect(const Policy& policy, const std::string& line);

}
