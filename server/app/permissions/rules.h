#pragma once

#include <cstddef>
#include <string>
#include <vector>

namespace manifest { class Json; }

namespace permissions {

struct Pattern {
    bool ok = false;
    std::string tool;
    bool hasArgument = false;
    std::string glob;
};

Pattern parsePattern(const std::string& text);

struct Reach {
    bool matched = false;
    std::size_t consumed = 0;
    std::string expected;
};

Reach reach(const std::string& glob, const std::string& text);

std::string escapeForPattern(const std::string& literal);

std::string renderRulePattern(const std::string& tool, const std::string& argument,
                              bool hasArgument);

struct Rule {
    std::string ruleId;
    std::string pattern;
    std::string source;
    std::string grantedIso;
    long long matchCount = 0;
    bool revocable = false;
    Pattern parsed;
};

struct Warning {
    std::string code;
    std::string message;
    std::string path;
};

struct File {
    std::vector<Rule> rules;
    std::vector<Warning> warnings;
};

File loadFile(const std::string& path);

bool saveFile(const std::string& path, const std::vector<Rule>& granted, std::string& error);

std::string renderRulesJson(const std::vector<Rule>& rules);

bool ruleMatches(const Rule& rule, const std::string& tool, const std::string& argument);

struct Miss {
    bool found = false;
    std::string ruleId;
    std::string pattern;
    std::string source;
    std::string differsOn;
    std::string matchedPrefix;
    std::string why;
};

Miss nearestMiss(const std::vector<Rule>& rules, const std::string& tool,
                 const std::string& argument);

std::string argumentOf(const std::string& tool, const manifest::Json& input, bool& has);

std::string nowIso();

}
