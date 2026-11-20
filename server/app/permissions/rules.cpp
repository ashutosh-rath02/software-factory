#include "rules.h"

#include <algorithm>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <random>
#include <sstream>

#include <app/manifest/json.h>

using manifest::Json;
using manifest::jsonEscape;

namespace {

using manifest::jstr;
bool toolChar(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
        || c == '_' || c == '.' || c == '-';
}

struct Tok {
    bool star = false;
    char c = 0;
};

std::vector<Tok> tokenize(const std::string& glob, bool& ok)
{
    ok = true;
    std::vector<Tok> out;
    for (std::size_t i = 0; i < glob.size(); ++i) {
        const char c = glob[i];
        if (c == '\\') {
            if (i + 1 >= glob.size()) { ok = false; return out; }
            const char next = glob[i + 1];
            if (next != '*' && next != '\\') { ok = false; return out; }
            Tok t; t.c = next; out.push_back(t);
            ++i;
            continue;
        }
        Tok t;
        if (c == '*') t.star = true; else t.c = c;
        out.push_back(t);
    }
    return out;
}

std::string clip(const std::string& s, std::size_t most)
{
    if (s.size() <= most) return s;
    return s.substr(0, most) + "…";
}

}

std::string permissions::nowIso()
{
    const std::time_t now = std::time(nullptr);
    std::tm tm {};
    gmtime_r(&now, &tm);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

permissions::Pattern permissions::parsePattern(const std::string& text)
{
    Pattern out;
    if (text.empty()) return out;

    const std::size_t open = text.find('(');
    if (open == std::string::npos) {
        for (char c : text) if (!toolChar(c)) return out;
        out.tool = text;
        out.ok = true;
        return out;
    }

    if (text.back() != ')' || open == 0 || open + 1 > text.size() - 1) return out;
    const std::string tool = text.substr(0, open);
    for (char c : tool) if (!toolChar(c)) return out;

    out.glob = text.substr(open + 1, text.size() - open - 2);
    bool ok = false;
    tokenize(out.glob, ok);
    if (!ok) return out;

    out.tool = tool;
    out.hasArgument = true;
    out.ok = true;
    return out;
}

permissions::Reach permissions::reach(const std::string& glob, const std::string& text)
{
    Reach out;
    bool ok = false;
    const std::vector<Tok> toks = tokenize(glob, ok);
    if (!ok) return out;

    const std::size_t n = toks.size(), m = text.size();
    std::vector<std::vector<char>> seen(n + 1, std::vector<char>(m + 1, 0));
    std::vector<std::pair<std::size_t, std::size_t>> stack;
    seen[0][0] = 1;
    stack.emplace_back(0, 0);

    while (!stack.empty()) {
        const auto [i, j] = stack.back();
        stack.pop_back();
        if (i >= n) continue;
        if (toks[i].star) {
            if (!seen[i + 1][j]) { seen[i + 1][j] = 1; stack.emplace_back(i + 1, j); }
            if (j < m && !seen[i][j + 1]) { seen[i][j + 1] = 1; stack.emplace_back(i, j + 1); }
        } else if (j < m && text[j] == toks[i].c) {
            if (!seen[i + 1][j + 1]) { seen[i + 1][j + 1] = 1; stack.emplace_back(i + 1, j + 1); }
        }
    }

    out.matched = seen[n][m] != 0;

    std::size_t furthest = 0;
    for (std::size_t j = 0; j <= m; ++j)
        for (std::size_t i = 0; i <= n; ++i)
            if (seen[i][j]) { furthest = std::max(furthest, j); break; }
    out.consumed = furthest;

    for (std::size_t i = 0; i <= n; ++i) {
        if (!seen[i][furthest] || i >= n || toks[i].star) continue;
        for (std::size_t k = i; k < n && !toks[k].star; ++k) out.expected.push_back(toks[k].c);
        break;
    }
    return out;
}

std::string permissions::escapeForPattern(const std::string& literal)
{
    std::string out;
    out.reserve(literal.size());
    for (char c : literal) {
        if (c == '\\' || c == '*') out.push_back('\\');
        out.push_back(c);
    }
    return out;
}

std::string permissions::renderRulePattern(const std::string& tool, const std::string& argument,
                                           bool hasArgument)
{
    if (!hasArgument) return tool;
    return tool + "(" + escapeForPattern(argument) + ")";
}

bool permissions::ruleMatches(const Rule& rule, const std::string& tool,
                              const std::string& argument)
{
    if (!rule.parsed.ok) return false;
    if (rule.parsed.tool != tool) return false;
    if (!rule.parsed.hasArgument) return true;
    return reach(rule.parsed.glob, argument).matched;
}

permissions::Miss permissions::nearestMiss(const std::vector<Rule>& rules,
                                           const std::string& tool,
                                           const std::string& argument)
{
    Miss best;
    std::size_t bestConsumed = 0;
    Reach bestReach;

    for (const auto& rule : rules) {
        if (!rule.parsed.ok || rule.parsed.tool != tool || !rule.parsed.hasArgument) continue;
        const Reach r = reach(rule.parsed.glob, argument);
        if (r.matched) continue;
        if (best.found && r.consumed <= bestConsumed) continue;
        best.found = true;
        best.ruleId = rule.ruleId;
        best.pattern = rule.pattern;
        best.source = rule.source;
        best.differsOn = "argument";
        bestConsumed = r.consumed;
        bestReach = r;
    }
    if (!best.found) return best;

    best.matchedPrefix = argument.substr(0, bestConsumed);
    const std::string rest = argument.substr(bestConsumed);
    const std::size_t window = std::max<std::size_t>(bestReach.expected.size(), 8);
    const std::string found = rest.empty() ? std::string() : clip(rest, window);

    std::ostringstream why;
    if (bestConsumed == 0) {
        why << "the rule requires `" << bestReach.expected << "` where the argument ";
        why << (found.empty() ? "is empty" : "begins `" + found + "`");
    } else {
        why << "the rule matches this argument up to `" << best.matchedPrefix
            << "`, then requires `" << bestReach.expected << "` where the argument ";
        why << (found.empty() ? "ends" : "has `" + found + "`");
    }
    best.why = why.str();
    return best;
}

std::string permissions::argumentOf(const std::string& tool, const Json& input, bool& has)
{
    has = false;
    if (!input.isObject()) return {};

    auto take = [&](const char* key) -> const Json* {
        const Json* v = input.find(key);
        return (v && v->isString()) ? v : nullptr;
    };

    if (tool == "Bash") {
        if (const Json* v = take("command")) { has = true; return v->str(); }
    }
    if (tool == "Read" || tool == "Write" || tool == "Edit" || tool == "MultiEdit"
        || tool == "NotebookEdit") {
        if (const Json* v = take("file_path")) { has = true; return v->str(); }
        if (const Json* v = take("notebook_path")) { has = true; return v->str(); }
    }
    for (const auto& [key, value] : input.members()) {
        (void)key;
        if (value.isString()) { has = true; return value.str(); }
    }
    return {};
}

permissions::File permissions::loadFile(const std::string& path)
{
    File out;
    std::ifstream in(path, std::ios::binary);
    if (!in) return out;

    std::ostringstream buf;
    buf << in.rdbuf();

    Json::Error err;
    const Json doc = Json::parse(buf.str(), err);
    auto reject = [&](const std::string& message) {
        out.rules.clear();
        out.warnings.push_back({"rules_unreadable", message, path});
        return out;
    };
    if (!err.ok || !doc.isObject())
        return reject("the policy file is not readable JSON, so no rule in it is in force");

    const Json* rules = doc.find("rules");
    if (!rules || !rules->isArray())
        return reject("the policy file has no `rules` array, so no rule in it is in force");

    for (const auto& item : rules->items()) {
        if (!item.isObject())
            return reject("a rule in the policy file is not an object");
        const Json* id = item.find("rule_id");
        const Json* pattern = item.find("pattern");
        const Json* granted = item.find("granted_iso");
        if (!id || !id->isString() || !pattern || !pattern->isString()
            || !granted || !granted->isString())
            return reject("a rule in the policy file is missing rule_id, pattern or granted_iso");

        Rule rule;
        rule.ruleId = id->str();
        rule.pattern = pattern->str();
        rule.grantedIso = granted->str();
        rule.source = "granted";
        rule.revocable = true;
        rule.parsed = parsePattern(rule.pattern);
        if (!rule.parsed.ok) {
            out.warnings.push_back({"pattern_unparseable",
                                    "the pattern `" + rule.pattern + "` does not parse, so it "
                                    "grants nothing and every matching request is asked about",
                                    path});
            continue;
        }
        out.rules.push_back(rule);
    }
    return out;
}

bool permissions::saveFile(const std::string& path, const std::vector<Rule>& granted,
                           std::string& error)
{
    std::ostringstream body;
    body << "{\n  \"version\": 1,\n  \"rules\": [";
    for (std::size_t i = 0; i < granted.size(); ++i) {
        body << (i ? ",\n" : "\n");
        body << "    {\n"
             << "      \"rule_id\": " << jstr(granted[i].ruleId) << ",\n"
             << "      \"pattern\": " << jstr(granted[i].pattern) << ",\n"
             << "      \"granted_iso\": " << jstr(granted[i].grantedIso) << "\n"
             << "    }";
    }
    body << (granted.empty() ? "" : "\n  ") << "]\n}\n";

    const std::string tmp = path + ".tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        if (!out) { error = "cannot write " + tmp; return false; }
        out << body.str();
        out.flush();
        if (!out) { error = "cannot write " + tmp; return false; }
    }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) {
        std::remove(tmp.c_str());
        error = "cannot replace " + path;
        return false;
    }
    return true;
}

std::string permissions::renderRulesJson(const std::vector<Rule>& rules)
{
    std::ostringstream o;
    o << "[";
    for (std::size_t i = 0; i < rules.size(); ++i) {
        const Rule& r = rules[i];
        if (i) o << ",";
        o << "{\"rule_id\":" << jstr(r.ruleId)
          << ",\"pattern\":" << jstr(r.pattern)
          << ",\"source\":" << jstr(r.source)
          << ",\"granted_iso\":" << (r.grantedIso.empty() ? "null" : jstr(r.grantedIso))
          << ",\"match_count\":" << r.matchCount
          << ",\"revocable\":" << (r.revocable ? "true" : "false")
          << "}";
    }
    o << "]";
    return o.str();
}
