#include "transcript.h"

#include <cmath>
#include <iomanip>
#include <set>
#include <sstream>

#include <app/manifest/json.h>

namespace {

using manifest::Json;

using manifest::jstr;
std::string num(double v)
{
    if (v == static_cast<long long>(v)) return std::to_string(static_cast<long long>(v));
    std::ostringstream o;
    o << std::setprecision(12) << v;
    return o.str();
}

std::string dump(const Json& v)
{
    switch (v.type()) {
    case Json::Type::Null:   return "null";
    case Json::Type::Bool:   return v.boolean() ? "true" : "false";
    case Json::Type::Number: return num(v.number());
    case Json::Type::String: return jstr(v.str());
    case Json::Type::Array: {
        std::string out = "[";
        bool first = true;
        for (const auto& item : v.items()) {
            if (!first) out += ",";
            first = false;
            out += dump(item);
        }
        return out + "]";
    }
    case Json::Type::Object: {
        std::string out = "{";
        bool first = true;
        for (const auto& [key, value] : v.members()) {
            if (!first) out += ",";
            first = false;
            out += jstr(key) + ":" + dump(value);
        }
        return out + "}";
    }
    }
    return "null";
}

const Json* member(const Json& v, const std::string& key)
{
    return v.isObject() ? v.find(key) : nullptr;
}

std::string stringAt(const Json& v, const std::string& key)
{
    const Json* found = member(v, key);
    return found && found->isString() ? found->str() : std::string();
}

double numberAt(const Json& v, const std::string& key)
{
    const Json* found = member(v, key);
    return found && found->isNumber() ? found->number() : 0;
}

bool boolAt(const Json& v, const std::string& key)
{
    const Json* found = member(v, key);
    return found && found->isBool() && found->boolean();
}

constexpr std::size_t WORDS_LIMIT = 160;

std::string clipped(const std::string& s, std::size_t limit = 400)
{
    if (s.size() <= limit) return s;
    std::size_t cut = limit;
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    return s.substr(0, cut) + "…";
}

std::string firstLineClipped(const std::string& s, std::size_t limit)
{
    std::size_t begin = 0;
    while (begin < s.size()) {
        const std::size_t nl = s.find('\n', begin);
        const std::size_t stop = nl == std::string::npos ? s.size() : nl;
        std::size_t end = stop;
        if (end > begin && s[end - 1] == '\r') --end;
        if (s.find_first_not_of(" \t", begin) < end) {
            std::size_t points = 0;
            std::size_t at = begin;
            while (at < end && points < limit) {
                ++at;
                while (at < end && (static_cast<unsigned char>(s[at]) & 0xC0) == 0x80) ++at;
                ++points;
            }
            if (at >= end) return s.substr(begin, end - begin);
            return s.substr(begin, at - begin) + "…";
        }
        if (nl == std::string::npos) break;
        begin = nl + 1;
    }
    return {};
}

bool editsAFile(const std::string& tool)
{
    static const std::set<std::string> edits {"Edit", "MultiEdit", "Write", "NotebookEdit"};
    return edits.count(tool) > 0;
}

std::string summaryOf(const std::string& tool, const Json* input)
{
    if (!input || !input->isObject()) return {};
    for (const char* key : {"command", "file_path", "path", "pattern", "url", "prompt", "query"}) {
        const Json* found = input->find(key);
        if (found && found->isString() && !found->str().empty()) return clipped(found->str(), 200);
    }
    for (const auto& [name, value] : input->members())
        if (value.isString() && !value.str().empty()) return clipped(value.str(), 200);
    (void)tool;
    return {};
}

std::string resultText(const Json& block)
{
    const Json* content = member(block, "content");
    if (!content) return {};
    if (content->isString()) return content->str();
    if (!content->isArray()) return dump(*content);
    std::string out;
    for (const auto& item : content->items()) {
        const std::string text = stringAt(item, "text");
        if (text.empty()) continue;
        if (!out.empty()) out += "\n";
        out += text;
    }
    return out;
}

struct Patch {
    int added = 0;
    int removed = 0;
    std::string diff;
    bool present = false;
};

Patch patchOf(const Json* toolUseResult)
{
    Patch out;
    if (!toolUseResult || !toolUseResult->isObject()) return out;

    const Json* hunks = toolUseResult->find("structuredPatch");
    if (hunks && hunks->isArray() && !hunks->items().empty()) {
        std::ostringstream diff;
        for (const auto& hunk : hunks->items()) {
            diff << "@@ -" << num(numberAt(hunk, "oldStart")) << ","
                 << num(numberAt(hunk, "oldLines")) << " +" << num(numberAt(hunk, "newStart"))
                 << "," << num(numberAt(hunk, "newLines")) << " @@\n";
            const Json* lines = hunk.find("lines");
            if (!lines || !lines->isArray()) continue;
            for (const auto& line : lines->items()) {
                if (!line.isString()) continue;
                diff << line.str() << "\n";
                if (!line.str().empty() && line.str()[0] == '+') ++out.added;
                if (!line.str().empty() && line.str()[0] == '-') ++out.removed;
            }
        }
        out.diff = diff.str();
        out.present = true;
        return out;
    }

    const Json* content = toolUseResult->find("content");
    if (content && content->isString()) {
        std::ostringstream diff;
        std::istringstream in(content->str());
        std::string line;
        while (std::getline(in, line)) {
            diff << "+" << line << "\n";
            ++out.added;
        }
        out.diff = diff.str();
        out.present = true;
    }
    return out;
}

}

std::vector<agent::transcript::Entry> agent::transcript::Parser::feed(const std::string& bytes,
                                                                     long long nowMs)
{
    std::vector<Entry> out;
    buffer_ += bytes;
    std::size_t start = 0;
    for (;;) {
        const std::size_t nl = buffer_.find('\n', start);
        if (nl == std::string::npos) break;
        line_(buffer_.substr(start, nl - start), nowMs, out);
        start = nl + 1;
    }
    buffer_.erase(0, start);
    return out;
}

std::vector<agent::transcript::Entry> agent::transcript::Parser::flush(long long nowMs)
{
    std::vector<Entry> out;
    if (!buffer_.empty()) {
        line_(buffer_, nowMs, out);
        buffer_.clear();
    }
    return out;
}

void agent::transcript::Parser::line_(const std::string& raw, long long nowMs,
                                      std::vector<Entry>& out)
{
    std::string line = raw;
    while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
    if (line.empty()) return;

    Json::Error err;
    const Json frame = Json::parse(line, err);
    if (!err.ok || !frame.isObject()) {
        Entry entry;
        entry.kind = "error";
        entry.members = "\"kind\":\"error\",\"raw\":true,\"text\":" + jstr(clipped(line));
        out.push_back(std::move(entry));
        ++unmapped_["unparseable"];
        return;
    }

    const std::string type = stringAt(frame, "type");
    const std::string method = stringAt(frame, "method");
    if (const auto* id = member(frame, "id"); id && !method.empty()) {
        Entry entry;
        entry.control = true;
        entry.rpcId = dump(*id);
        entry.method = method;
        if (const auto* parameters = member(frame, "params")) {
            if (method == "item/commandExecution/requestApproval") {
                entry.approvalTool = "Bash";
                entry.approvalArgument = stringAt(*parameters, "command");
            } else if (method == "item/fileChange/requestApproval") {
                entry.approvalTool = "Edit";
                entry.approvalArgument = stringAt(*parameters, "grantRoot");
            }
        }
        out.push_back(std::move(entry));
        return;
    }
    const Json* params = frame.find("params");
    if (const Json* id = frame.find("id"); id && id->isNumber() && method.empty()) {
        Entry entry;
        entry.control = true;
        entry.requestId = static_cast<int>(id->number());
        if (const Json* error = frame.find("error"))
            entry.rpcError = stringAt(*error, "message");
        if (const Json* result = frame.find("result")) {
            if (const Json* thread = result->find("thread"))
                entry.threadId = stringAt(*thread, "id");
            if (const Json* turn = result->find("turn"))
                entry.turnId = stringAt(*turn, "id");
            if (const Json* goal = result->find("goal")) {
                entry.threadId = stringAt(*goal, "threadId");
                entry.goalStatus = stringAt(*goal, "status");
            }
        }
        out.push_back(std::move(entry));
        return;
    }
    if (method == "turn/started" && params) {
        Entry entry;
        entry.control = true;
        entry.method = method;
        entry.threadId = stringAt(*params, "threadId");
        if (const Json* turn = params->find("turn")) entry.turnId = stringAt(*turn, "id");
        out.push_back(std::move(entry));
        return;
    }

    if (method == "thread/goal/updated" && params) {
        Entry entry;
        entry.control = true;
        entry.method = method;
        entry.threadId = stringAt(*params, "threadId");
        if (const Json* goal = params->find("goal"))
            entry.goalStatus = stringAt(*goal, "status");
        out.push_back(std::move(entry));
        return;
    }

    if (method == "thread/tokenUsage/updated" && params) {
        const Json* usage = params->find("tokenUsage");
        const Json* total = usage ? usage->find("total") : nullptr;
        if (!total || !total->isObject()) return;
        const Json* input = total->find("inputTokens");
        const Json* cached = total->find("cachedInputTokens");
        const Json* output = total->find("outputTokens");
        const Json* reasoning = total->find("reasoningOutputTokens");
        if (!input || !cached || !output || !reasoning
            || !input->isInteger() || !cached->isInteger()
            || !output->isInteger() || !reasoning->isInteger()
            || input->number() < 0 || cached->number() < 0
            || output->number() < 0 || reasoning->number() < 0
            || cached->number() > input->number()) return;
        Entry entry;
        entry.control = true;
        entry.method = method;
        entry.threadId = stringAt(*params, "threadId");
        entry.hasUsage = true;
        entry.inputTokens = static_cast<long long>(input->number());
        entry.cachedInputTokens = static_cast<long long>(cached->number());
        entry.outputTokens = static_cast<long long>(output->number());
        entry.reasoningOutputTokens = static_cast<long long>(reasoning->number());
        out.push_back(std::move(entry));
        return;
    }

    if (method == "item/completed" && params) {
        const Json* item = params->find("item");
        if (!item || !item->isObject()) {
            ++unmapped_["codex:missing-item"];
            return;
        }
        const std::string itemType = stringAt(*item, "type");
        Entry entry;
        if (itemType == "fileChange") {
            const Json* changes = item->find("changes");
            if (!changes || !changes->isArray()) {
                ++unmapped_["codex:missing-changes"];
                return;
            }
            for (const auto& change : changes->items()) {
                const std::string path = stringAt(change, "path");
                const Json* kind = member(change, "kind");
                const std::string operation = kind ? stringAt(*kind, "type") : std::string();
                const std::string diff = stringAt(change, "diff");
                int added = 0;
                int removed = 0;
                bool hunk = false;
                std::istringstream lines(diff);
                std::string line;
                while (std::getline(lines, line)) {
                    if (operation == "update" && line.rfind("@@", 0) == 0) hunk = true;
                    if (operation == "add" || (hunk && !line.empty() && line[0] == '+')) ++added;
                    if (operation == "delete" || (hunk && !line.empty() && line[0] == '-')) ++removed;
                }
                Entry edit;
                edit.kind = "file_edit";
                edit.members = "\"kind\":\"file_edit\",\"tool\":\"apply_patch\",\"summary\":"
                             + jstr(path) + ",\"ok\":"
                             + (stringAt(*item, "status") == "completed" ? "true" : "false")
                             + ",\"duration_s\":0,\"path\":" + jstr(path)
                             + ",\"added\":" + std::to_string(added)
                             + ",\"removed\":" + std::to_string(removed)
                             + ",\"diff\":" + jstr(clipped(diff, 20000));
                edit.hasWords = true;
                edit.wordsKind = edit.kind;
                edit.words = "apply_patch " + path;
                out.push_back(std::move(edit));
            }
            return;
        } else if (itemType == "agentMessage") {
            const std::string text = stringAt(*item, "text");
            entry.kind = "prose";
            entry.members = "\"kind\":\"prose\",\"text\":" + jstr(text);
            entry.hasWords = true;
            entry.wordsKind = "prose";
            entry.words = firstLineClipped(text, WORDS_LIMIT);
        } else if (itemType == "commandExecution") {
            const Json* exitCode = item->find("exitCode");
            const bool ok = stringAt(*item, "status") == "completed"
                         && exitCode && exitCode->isNumber() && exitCode->number() == 0;
            const std::string command = stringAt(*item, "command");
            entry.kind = "tool";
            entry.members = "\"kind\":\"tool\",\"tool\":\"shell\",\"summary\":"
                          + jstr(command) + ",\"ok\":" + (ok ? "true" : "false")
                          + ",\"duration_s\":0,\"result\":"
                          + jstr(clipped(stringAt(*item, "aggregatedOutput"), 4000));
            entry.hasWords = true;
            entry.wordsKind = "tool";
            entry.words = firstLineClipped(command, WORDS_LIMIT);
        } else {
            ++unmapped_["codex:" + itemType];
            return;
        }
        out.push_back(std::move(entry));
        return;
    }

    if (method == "turn/completed" && params) {
        const Json* turn = params->find("turn");
        if (!turn || !turn->isObject()) {
            ++unmapped_["codex:missing-turn"];
            return;
        }
        if (stringAt(*turn, "status") == "failed") {
            const Json* error = turn->find("error");
            Entry entry;
            entry.kind = "error";
            entry.members = "\"kind\":\"error\",\"text\":"
                          + jstr(error ? stringAt(*error, "message") : "Codex turn failed");
            out.push_back(std::move(entry));
        }
        Entry entry;
        entry.kind = "turn";
        entry.endsTurn = true;
        entry.threadId = stringAt(*params, "threadId");
        entry.turnId = stringAt(*turn, "id");
        entry.members = "\"kind\":\"turn\",\"n\":" + std::to_string(++turns_)
                      + ",\"duration_s\":0,\"cost_usd\":0";
        out.push_back(std::move(entry));
        return;
    }

    if (type == "assistant" || type == "user") {
        const Json* message = frame.find("message");
        const Json* content = message ? member(*message, "content") : nullptr;
        if (!content || !content->isArray()) {
            ++unmapped_[type + ":no-content"];
            return;
        }
        for (const auto& block : content->items()) {
            const std::string blockType = stringAt(block, "type");

            if (blockType == "text") {
                const std::string text = stringAt(block, "text");
                if (text.find_first_not_of(" \t\r\n") == std::string::npos) continue;
                Entry entry;
                entry.kind = "prose";
                entry.members = "\"kind\":\"prose\",\"text\":" + jstr(text);
                entry.hasWords = true;
                entry.wordsKind = "prose";
                entry.words = firstLineClipped(text, WORDS_LIMIT);
                out.push_back(std::move(entry));

            } else if (blockType == "tool_use") {
                const std::string tool = stringAt(block, "name");
                const Json* input = member(block, "input");
                const bool edit = editsAFile(tool);

                Call call;
                call.kind = edit ? "file_edit" : "tool";
                call.tool = tool;
                call.path = input ? stringAt(*input, "file_path") : std::string();
                call.seenMs = nowMs;
                const std::string id = stringAt(block, "id");
                if (!id.empty()) calls_[id] = call;

                std::ostringstream members;
                members << "\"kind\":" << jstr(call.kind) << ",\"tool\":" << jstr(tool)
                        << ",\"summary\":" << jstr(summaryOf(tool, input))
                        << ",\"ok\":null,\"duration_s\":0";
                if (edit) members << ",\"path\":" << jstr(call.path)
                                  << ",\"added\":0,\"removed\":0,\"diff\":\"\"";
                if (input) members << ",\"input\":" << dump(*input);

                Entry entry;
                entry.kind = call.kind;
                entry.toolUseId = id;
                entry.members = members.str();
                entry.hasWords = true;
                entry.wordsKind = call.kind;
                entry.words = edit && !call.path.empty() ? tool + " " + call.path : tool;
                out.push_back(std::move(entry));

            } else if (blockType == "tool_result") {
                const std::string id = stringAt(block, "tool_use_id");
                const bool failed = boolAt(block, "is_error");
                const std::string text = resultText(block);
                auto call = calls_.find(id);

                if (call == calls_.end()) {
                    Entry entry;
                    entry.kind = "tool_result";
                    entry.members = "\"kind\":\"tool_result\",\"text\":" + jstr(clipped(text, 4000));
                    out.push_back(std::move(entry));
                    continue;
                }

                const Patch patch = call->second.kind == "file_edit"
                                        ? patchOf(frame.find("tool_use_result"))
                                        : Patch{};
                const long long elapsed =
                    call->second.seenMs > 0 ? (nowMs - call->second.seenMs) / 1000 : 0;

                std::ostringstream members;
                members << "\"kind\":" << jstr(call->second.kind) << ",\"patch\":true"
                        << ",\"ok\":" << (failed ? "false" : "true")
                        << ",\"duration_s\":" << (elapsed < 0 ? 0 : elapsed)
                        << ",\"result\":" << jstr(clipped(text, 4000));
                if (call->second.kind == "file_edit")
                    members << ",\"path\":" << jstr(call->second.path)
                            << ",\"added\":" << patch.added << ",\"removed\":" << patch.removed
                            << ",\"diff\":" << jstr(clipped(patch.diff, 20000));

                Entry entry;
                entry.kind = call->second.kind;
                entry.patch = true;
                entry.toolUseId = id;
                entry.members = members.str();
                out.push_back(std::move(entry));
                calls_.erase(call);

            } else {
                ++unmapped_[type + ":" + (blockType.empty() ? "?" : blockType)];
            }
        }
        return;
    }

    if (type == "result") {
        const double total = numberAt(frame, "total_cost_usd");
        const double turnCost = total > costUsd_ ? total - costUsd_ : 0;
        if (total > costUsd_) costUsd_ = total;
        ++turns_;

        if (boolAt(frame, "is_error")) {
            Entry error;
            error.kind = "error";
            error.members = "\"kind\":\"error\",\"text\":"
                          + jstr(clipped(stringAt(frame, "result"), 4000));
            out.push_back(std::move(error));
        }

        std::ostringstream members;
        members << "\"kind\":\"turn\",\"n\":" << turns_ << ",\"duration_s\":"
                << num(std::floor(numberAt(frame, "duration_ms") / 1000.0))
                << ",\"cost_usd\":" << num(turnCost);

        Entry entry;
        entry.kind = "turn";
        entry.endsTurn = true;
        entry.hasCost = true;
        entry.sessionCostUsd = costUsd_;
        entry.members = members.str();
        out.push_back(std::move(entry));
        return;
    }

    if (type == "rate_limit_event") rateLimit_ = line;
    ++unmapped_[type.empty() ? "?" : type];
}
