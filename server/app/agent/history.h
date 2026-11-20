#pragma once

#include <map>
#include <set>
#include <string>
#include <app/manifest/store.h>
#include <vector>

namespace agent::history {

struct Usage {
    long long inputTokens = 0;
    long long cachedInputTokens = 0;
    long long outputTokens = 0;
    long long reasoningOutputTokens = 0;
};

struct Meta {
    std::string agentProfile;
    std::string driver;
    std::string model;
    manifest::Store::Tree tree;
    std::string effort;
    bool hasEffort = false;
    std::string runId;
    std::string lane;
    std::string ticketId;
    std::string role;
    std::string sessionId;
    std::string branch;
    std::string branchTip;
    std::string startedIso;
    std::string endedIso;
    std::string outcome;
    int n = 1;
    long long durationS = 0;
    double costUsd = 0;
    Usage usage;
    bool hasUsage = false;
    int turns = 0;
    std::map<std::string, int> unmapped;

    std::string settledFrom;

    int exitCode = -1;
    int signal = 0;

    std::string rateLimit;
};

void write(const std::string& runsDir, const Meta& meta);

void noteRef(const std::string& runsDir, const std::string& runId, const std::string& branch,
             const std::string& tip);

void append(const std::string& runsDir, const std::string& runId,
            const std::vector<std::string>& lines);

struct Row {
    Meta meta;
    std::string json;
};
std::vector<Row> forTicket(const std::string& runsDir, const std::string& ticketId);
std::vector<Row> forTicket(const std::vector<std::string>& ledgers, const std::string& ticketId);

std::vector<Meta> all(const std::string& runsDir);
std::vector<Meta> all(const std::vector<std::string>& ledgers);

struct Totals {
    int count = 0;
    long long durationS = 0;
    double costUsd = 0;
};
Totals totalsFor(const std::string& runsDir, const std::string& ticketId);
Totals totalsFor(const std::vector<std::string>& ledgers, const std::string& ticketId);

struct Replay {
    bool found = false;
    std::string path;
    std::string body;
};
Replay transcriptOf(const std::string& runsDir, const std::string& runId,
                    long long afterSeq, int limit);
Replay transcriptOf(const std::vector<std::string>& ledgers, const std::string& runId,
                    long long afterSeq, int limit);

struct Activity {
    std::string runId;
    std::string ticketId;
    std::string role;
    std::string startedIso;
    long long startedAt = 0;
    long long activeS = 0;
    std::map<std::string, long long> stageSeconds;
    std::map<std::string, long long> firstStageAt;
};
std::vector<Activity> activity(const std::string& runsDir);
std::vector<Activity> activity(const std::vector<std::string>& ledgers);

struct Reported {
    std::string name;
    int round = 0;
    bool hasRound = false;
    int of = 0;
    bool hasOf = false;
    long long atMs = 0;
    long long endMs = 0;
    std::string runId;
};
std::map<std::string, std::vector<Reported>> stagesByTicket(const std::string& runsDir);
std::map<std::string, std::vector<Reported>> stagesByTicket(const std::vector<std::string>& ledgers);

std::map<std::string, std::string> lastOutcomeByTicket(const std::string& runsDir);
std::map<std::string, std::string> lastOutcomeByTicket(const std::vector<std::string>& ledgers);

int settleOrphans(const std::string& runsDir, const std::set<std::string>& liveRunIds);

}
