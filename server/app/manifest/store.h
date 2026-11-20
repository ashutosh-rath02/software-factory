#pragma once

#include <string>
#include <utility>
#include <vector>

#include <core/component.h>

COMPONENT(manifest, Store)

    struct ToolchainPath {
        std::string declared;
        std::string hostPath;
    };

    struct WorkspaceCommand {
        std::string key;
        std::string text;
    };

    struct Workspace {
        struct StartPreflight {
            std::string command;
            int timeoutSeconds = 15;
            std::vector<std::string> exemptTicketIds;
        };
        std::string name;
        std::string path;
        bool buildable = true;
        std::string absPath;

        std::string repo;
        std::string gate;
        std::string build;
        std::string review;
        bool hasGate = false;
        bool hasBuild = false;
        bool hasReview = false;
        bool hasStartPreflight = false;
        StartPreflight startPreflight;

        std::vector<ToolchainPath> toolchain;

        const std::string* command(const std::string& key) const;
        std::vector<WorkspaceCommand> declaredCommands() const;
    };

    static const std::vector<std::string>& commandKeys();

    struct Resource {
        std::string name;
        bool exclusive = false;
        std::vector<std::string> neededBy;
        std::string description;
        bool hasDescription = false;

        std::vector<std::string> devices;
    };

    struct Docs {
        std::string epics = "docs/epics";
        std::string tickets = "docs/tickets";
        std::string reports = "docs/reports";

        std::string wiki;
        std::string escapes;

        std::string epicsAbs;
        std::string ticketsAbs;
        std::string reportsAbs;
        std::string wikiAbs;
        std::string escapesAbs;
    };

    struct Repo {
        std::string name;
        std::string path = ".";
        std::string absPath;
        Docs docs;
    };

    struct ConfigTrust {
        std::string file;
        std::string keyedByPath;
        std::string flag;
        std::string fileAbs;
    };

    struct Agent {
        std::string name = "default";
        std::string driver = "claude";
        std::vector<std::string> models;
        std::string command = "claude";
        std::string permissionMode;
        std::string model;
        bool hasPermissionMode = false;
        bool hasModel = false;
        std::vector<std::string> allowedTools;
        bool hasAllowedTools = false;

        std::vector<std::string> efforts;
        bool hasEfforts = false;
        std::string defaultEffort;
        bool hasDefaultEffort = false;

        std::string credentialsDir;
        bool hasCredentialsDir = false;

        std::string startPromptFile;
        bool hasStartPromptFile = false;
        std::string startPromptAbs;

        std::string binaryAbs;
        std::string binaryDir;
        std::string credentialsAbs;

        ConfigTrust configTrust;
        bool hasConfigTrust = false;
    };

    struct Tree {
        std::string name;
        std::string decider = "rules";
        std::vector<std::string> agents;
        std::string continuation;
        bool compactOnStage = false;
        bool hasCompactOnStage = false;
        long long tokenBudget = 0;
    };
    static std::string treeJson(const Tree& tree);

    struct Evidence {
        std::string linePrefix;
        std::string frozenAfter;
    };

    struct Role {
        std::string name;
        std::string startOnStage;
        std::string lane = "new";
        std::string promptFile;
        std::string promptAbs;
        std::string effort;
        bool hasEffort = false;
    };

    struct Warning {
        std::string code;
        std::string pointer;
        std::string path;
        std::string message;
        std::string field{};
        std::string program{};
    };

    enum class State {
        Ok,
        Missing,
        UnsupportedMajor,
        Invalid
    };

    struct Data {
        State state = State::Missing;
        std::string version;
        std::string root;
        std::string gitRoot;
        std::string name;
        std::string manifestPath;
        std::string runsDir;
        std::string routingDir;
        std::string worktreesDir;
        std::string lanesDir;
        std::string permissionsPath;
        std::string idsPath;

        std::string errorCode;
        std::string errorMessage;
        std::string errorPointer;
        std::string errorReason;
        long long errorOffset = -1;
        bool hasErrorPointer = false;
        std::string errorPath;
        bool hasErrorPath = false;

        std::string errorField;
        bool hasErrorField = false;

        std::string errorProgram;
        bool hasErrorProgram = false;

        std::vector<Workspace> workspaces;
        std::vector<Resource> resources;
        bool hasResources = false;
        int lanesHost = 3;
        std::vector<Repo> repos;
        bool hasRepos = false;

        std::vector<std::string> ledgers() const
        {
            std::vector<std::string> out;
            if (!runsDir.empty()) out.push_back(runsDir);
            for (const auto& repo : repos) {
                if (repo.absPath.empty() || repo.absPath == root) continue;
                out.push_back(repo.absPath + "/.factory/runs");
            }
            return out;
        }
        std::vector<std::string> stages;
        bool hasStages = false;
        std::vector<std::string> ticketFields;
        bool hasTicketFields = false;
        std::vector<std::string> inFlightStatus;
        bool hasInFlightStatus = false;
        std::vector<std::string> doneStatus;
        bool hasDoneStatus = false;
        Agent agent;
        std::vector<Agent> agents;
        std::string defaultAgent = "default";
        std::vector<Tree> trees;
        std::string defaultTree = "off";
        bool hasTrees = false;
        std::string runEffort;
        bool hasRunEffort = false;
        std::vector<Role> roles;
        bool hasRoles = false;
        Evidence evidence;
        bool hasEvidence = false;

        std::vector<std::pair<std::string, std::string>> resolvedPaths;
        std::vector<Warning> warnings;
    };

    void init() override;

    void setRoot(const std::string& root) { given_ = root; }

    void setCarriedCredentialsDir(const std::string& dir) { carriedCredentials_ = dir; }

    const Data& data() const { return data_; }

    int httpStatus() const;
    std::string toJson() const;
    std::string errorJson() const;
    std::string healthJson(unsigned long long uptimeSeconds,
                           const std::string& extra = "") const;

private:
    void load_();
    bool resolveUnderRoot_(const std::string& declared, const std::string& pointer,
                           const std::string& key, std::string& absOut);
    bool resolveUnder_(const std::string& base, const std::string& declared,
                       const std::string& pointer, const std::string& key, std::string& absOut);
    const Repo* repoForWorkspace_(const Workspace& w);
    bool resolveAgentPaths_();
    bool resolveToolchains_();

    void resolveCommands_();
    bool resolveStartPrompt_();

    bool resolveConfigTrust_();

    std::string given_;
    std::string carriedCredentials_;
    Data data_;
COMPONENT_END
