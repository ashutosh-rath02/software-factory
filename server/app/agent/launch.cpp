#include "launch.h"

#include <cstdlib>
#include <filesystem>

#include <random>
#include <sstream>
#include <unistd.h>

#include <app/manifest/json.h>

namespace {

std::mt19937_64& generator()
{
    static thread_local std::mt19937_64 engine(std::random_device{}());
    return engine;
}

std::string tomlString(const std::string& value)
{
    std::string out = "\"";
    for (unsigned char c : value) {
        switch (c) {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\b': out += "\\b"; break;
        case '\t': out += "\\t"; break;
        case '\n': out += "\\n"; break;
        case '\f': out += "\\f"; break;
        case '\r': out += "\\r"; break;
        default:
            if (c >= 0x20 && c != 0x7f) out.push_back(static_cast<char>(c));
        }
    }
    return out + "\"";
}

}

std::vector<std::string> agent::launch::words(const std::string& command)
{
    std::vector<std::string> out;
    std::istringstream in(command);
    std::string word;
    while (in >> word) out.push_back(word);
    return out;
}

std::string agent::launch::randomHex(int bytes)
{
    static const char* digits = "0123456789abcdef";
    std::string out;
    out.reserve(static_cast<std::size_t>(bytes) * 2);
    std::uniform_int_distribution<int> nibble(0, 15);
    for (int i = 0; i < bytes * 2; ++i) out.push_back(digits[nibble(generator())]);
    return out;
}

std::string agent::launch::newSessionId()
{
    const std::string hex = randomHex(16);
    return hex.substr(0, 8) + "-" + hex.substr(8, 4) + "-4" + hex.substr(13, 3) + "-a"
         + hex.substr(17, 3) + "-" + hex.substr(20, 12);
}

std::string agent::launch::missingTreeAgent(const std::string& driver,
                                            const std::string& worktree,
                                            const std::vector<std::string>& agents)
{
    const std::string directory = driver == "codex" ? ".codex/agents" : ".claude/agents";
    const std::string extension = driver == "codex" ? ".toml" : ".md";
    for (const auto& name : agents) {
        const std::filesystem::path path =
            std::filesystem::path(worktree) / directory / (name + extension);
        const std::filesystem::path provider = path.parent_path().parent_path();
        if (!std::filesystem::is_directory(std::filesystem::symlink_status(provider)) ||
            !std::filesystem::is_directory(std::filesystem::symlink_status(path.parent_path())))
            return path.string();
        if (!std::filesystem::is_regular_file(std::filesystem::symlink_status(path)))
            return path.string();
    }
    return {};
}

std::vector<std::string> agent::launch::build(const manifest::Store::Agent& agent,
                                              const Options& options)
{
    std::vector<std::string> argv = words(agent.command);
    if (argv.empty()) return argv;

    if (!agent.binaryAbs.empty()) argv[0] = agent.binaryAbs;

    if (agent.driver == "codex") {
        argv.insert(argv.end(), {"app-server", "--listen", "stdio://"});
        return argv;
    }

    argv.emplace_back("--print");
    argv.emplace_back("--input-format");
    argv.emplace_back("stream-json");
    argv.emplace_back("--output-format");
    argv.emplace_back("stream-json");
    argv.emplace_back("--verbose");

    const std::string model = options.hasModel ? options.model : agent.model;
    if (!model.empty()) {
        argv.emplace_back("--model");
        argv.push_back(model);
    }
    if (!options.effort.empty()) {
        argv.emplace_back("--effort");
        argv.push_back(options.effort);
    }
    argv.emplace_back("--permission-mode");
    argv.emplace_back("auto");
    if (agent.hasAllowedTools && !agent.allowedTools.empty()) {
        std::string joined;
        for (const auto& tool : agent.allowedTools) {
            if (!joined.empty()) joined.push_back(',');
            joined += tool;
        }
        argv.emplace_back("--allowed-tools");
        argv.push_back(joined);
    }

    argv.emplace_back(options.resume ? "--resume" : "--session-id");
    argv.push_back(options.sessionId);

    return argv;
}

std::string agent::launch::resolveEffort(const manifest::Store::Agent& agent,
                                        const std::string& role,
                                        const std::string& requested)
{
    if (!agent.hasEfforts || agent.efforts.empty()) return {};
    if (!requested.empty()) return requested;

    const auto& data = manifest::Store::instance().data();
    for (const auto& declared : data.roles)
        if (!role.empty() && declared.name == role && role != "run")
            return declared.hasEffort ? declared.effort : agent.defaultEffort;
    if (data.hasRunEffort) return data.runEffort;
    return agent.defaultEffort;
}

std::string agent::launch::configDirVariable()
{
    return "CLAUDE_CONFIG_DIR";
}

std::string agent::launch::carriedConfigDir()
{
    const char* carried = ::getenv(configDirVariable().c_str());
    return carried && *carried ? std::string(carried) : std::string();
}

std::string agent::launch::permissionHookName() { return "permission-hook.py"; }
std::string agent::launch::permissionUrlVariable() { return "FACTORY_PERMISSION_URL"; }

namespace {

const char* PERMISSION_HOOK = R"PY(import json, os, sys, urllib.request

def out(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}))

try:
    call = json.load(sys.stdin)
except Exception:
    sys.exit(0)                      # not a call this hook understands: the CLI decides

url = os.environ.get("FACTORY_PERMISSION_URL")
token = os.environ.get("FACTORY_PROGRESS_TOKEN")
if not url or not token:
    sys.exit(0)                      # no channel: fail OPEN, see launch.cpp

body = json.dumps({"token": token,
                   "tool": call.get("tool_name", ""),
                   "input": call.get("tool_input", {}),
                   "tool_use_id": call.get("tool_use_id", "")}).encode()
req = urllib.request.Request(url, data=body,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=3600) as r:
        answer = json.load(r)
except Exception:
    sys.exit(0)                      # the tool is down, not saying no

if answer.get("decision") == "deny":
    out("deny", answer.get("reason") or "refused")
else:
    out("allow", answer.get("reason") or "allowed")
)PY";

}

agent::launch::ConfigSeed agent::launch::configSeed(const manifest::Store::Agent& agent,
                                                    const std::string& repoRoot)
{
    ConfigSeed seed;
    if (repoRoot.empty()) return seed;

    if (agent.driver == "codex") {
        seed.runtimeDirectories.push_back(
            "/tmp/codex-daemon-" + std::to_string(static_cast<unsigned long>(::geteuid())));
        seed.seeded.emplace_back("config.toml",
            "[projects." + tomlString(repoRoot) + "]\ntrust_level = \"trusted\"\n");
        if (!agent.credentialsAbs.empty())
            seed.borrowed.emplace_back("auth.json", agent.credentialsAbs + "/auth.json");
        return seed;
    }

    seed.seeded.emplace_back(".claude.json",
                            "{\"projects\":{\"" + manifest::jsonEscape(repoRoot)
                            + "\":{\"hasTrustDialogAccepted\":true}}}\n");

    if (!agent.credentialsAbs.empty())
        seed.borrowed.emplace_back(".credentials.json",
                                agent.credentialsAbs + "/.credentials.json");

    if (!agent.hasPermissionMode || agent.permissionMode.empty()) return seed;

    seed.seeded.emplace_back(permissionHookName(), std::string(PERMISSION_HOOK));
    seed.seeded.emplace_back(
        "settings.json",
        std::string("{\"hooks\":{\"PreToolUse\":[{\"matcher\":\"\",\"hooks\":[{"
                    "\"type\":\"command\",\"command\":\"python3 ")
        + "$CLAUDE_CONFIG_DIR/" + permissionHookName() + "\"}]}]}}\n");
    return seed;
}

std::string agent::launch::userMessage(const std::string& text)
{
    return "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\","
           "\"text\":\"" + manifest::jsonEscape(text) + "\"}]}}";
}
