#pragma once

#include <functional>
#include <map>
#include <string>
#include <vector>

namespace metrics {

struct Round {
    std::string ticketId;
    bool hasRole = false;
    long long startedAt = 0;
    long long activeS = 0;
    std::map<std::string, long long> stageSeconds;
};

struct Ticket {
    bool verdictDocument = false;
    std::string status;
    long long firstStageAt = 0;
    bool hasFirstStage = false;
    long long landedAt = 0;
    bool landed = false;
};

using ReworkLookup = std::function<bool(const std::string& ticketId, long long since)>;

struct Inputs {
    long long now = 0;
    long long days = 30;
    std::vector<Round> rounds;
    std::map<std::string, Ticket> tickets;
    std::vector<long long> ledgerDates;
    ReworkLookup rework;
};

std::string toJson(const Inputs& in);

}
