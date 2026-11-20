#pragma once

#include <map>
#include <string>
#include <vector>

namespace agent::transcript {

struct Entry {
    bool control = false;
    std::string method;
    int requestId = 0;
    std::string rpcId;
    std::string approvalTool;
    std::string approvalArgument;
    std::string threadId;
    std::string turnId;
    std::string rpcError;
    std::string goalStatus;
    bool hasUsage = false;
    long long inputTokens = 0;
    long long cachedInputTokens = 0;
    long long outputTokens = 0;
    long long reasoningOutputTokens = 0;
    std::string kind;
    std::string members;

    bool patch = false;
    std::string toolUseId;

    bool endsTurn = false;
    bool hasCost = false;
    double sessionCostUsd = 0;

    bool hasWords = false;
    std::string wordsKind;
    std::string words;
};

class Parser {
public:
    std::vector<Entry> feed(const std::string& bytes, long long nowMs);

    std::vector<Entry> flush(long long nowMs);

    int turns() const { return turns_; }
    double costUsd() const { return costUsd_; }
    const std::map<std::string, int>& unmapped() const { return unmapped_; }

    const std::string& rateLimit() const { return rateLimit_; }

private:
    void line_(const std::string& line, long long nowMs, std::vector<Entry>& out);

    struct Call {
        std::string kind;
        std::string tool;
        std::string path;
        long long seenMs = 0;
    };

    std::string buffer_;
    std::map<std::string, Call> calls_;
    std::map<std::string, int> unmapped_;
    std::string rateLimit_;
    int turns_ = 0;
    double costUsd_ = 0;
};

}
