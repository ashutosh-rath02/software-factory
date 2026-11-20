#pragma once

#include <string>
#include <vector>

#include <app/manifest/store.h>

// CLAUDE_CONFIG_DIR MOVES (SRV-FIX-047) — everything the CLI resolves from that directory once
// the variable is set, not only the credentials it was aimed at. Each row is
// `<what> | <file under the directory> | <the committed capture that measured it>`, and
// `tests/test_agent_trust.py::test_the_trust_the_tool_relies_on_is_named_where_it_is_read`
// re-derives every row from its capture — so a row nobody measured is a red test rather than a
// confident sentence. SRV-FEAT-039 checked the first row, and the second went with it in
// silence: the developer's trust stayed in `$HOME/.claude.json`, the agent read the config
// directory's file of the same name, and this repository's 107 declared permissions were
// dropped on every run for a day.
//
//   credentials     | .credentials.json | cli-trust-2026-07-30-turn-trusted-config-the-permission-is-in-force
//   trust           | .claude.json      | cli-trust-2026-07-30-config-dir-set-and-it-has-no-projects
//   project-state   | .claude.json      | cli-trust-2026-07-30-config-dir-carries-the-trust
//   session-scratch | session-env/      | cli-home-2026-07-30-a-writable-config-directory-runs-a-command
//
// The redirection is TOTAL, not a fallback: with the variable set, a trust recorded in
// `$HOME/.claude.json` is not consulted at all. That is the second row's capture and it is the
// whole of SRV-FIX-047's finding.
//
// The FOURTH row is SRV-FIX-052's, and it is the one that decides where the directory may be:
// the CLI WRITES there, before it can start a shell. Two rows of this list were checked against a
// directory mounted read-only and the third was not, so a lane could read a ticket, reason about
// it, and not run a single command. A redirection of an external program's configuration is
// judged by what it MOVED and by which way each piece of it travels — never by the one thing it
// was aimed at.

namespace agent::launch {

struct Options {
    std::string sessionId;
    bool resume = false;
    std::string model;
    bool hasModel = false;
    std::string effort;
};

std::string resolveEffort(const manifest::Store::Agent& agent, const std::string& role,
                          const std::string& requested);

std::vector<std::string> words(const std::string& command);

std::vector<std::string> build(const manifest::Store::Agent& agent, const Options& options);

std::string missingTreeAgent(const std::string& driver, const std::string& worktree,
                             const std::vector<std::string>& agents);

std::string configDirVariable();
std::string carriedConfigDir();

struct ConfigSeed {
    std::vector<std::pair<std::string, std::string>> seeded;
    std::vector<std::pair<std::string, std::string>> borrowed;
    std::vector<std::string> runtimeDirectories;
};

std::string permissionHookName();
std::string permissionUrlVariable();

ConfigSeed configSeed(const manifest::Store::Agent& agent, const std::string& repoRoot);

std::string userMessage(const std::string& text);

std::string randomHex(int bytes);

std::string newSessionId();

}
