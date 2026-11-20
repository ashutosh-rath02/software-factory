#pragma once

#include <map>
#include <string>
#include <utility>
#include <vector>

namespace lane::progress {

struct Report {
    std::string name;
    int round = 0;
    bool hasRound = false;
    int of = 0;
    bool hasOf = false;
    long long atMs = 0;
    long long endMs = 0;
    std::string runId;
};

struct Stage {
    int index = 0;
    int total = 0;
    std::string name;
    int round = 1;
    int of = 1;
    std::string source;
    std::string regressedFrom;
    bool unknown = false;
};

struct Shown {
    Stage stage;
    int pct = 0;
    long long remainingS = -1;
};

using History = std::map<std::string, std::vector<Report>>;
using Samples = std::map<std::pair<std::string, std::string>, std::vector<long long>>;

int indexOf(const std::vector<std::string>& sequence, const std::string& name);

Stage of(const std::vector<std::string>& sequence, const std::string& source,
         const std::vector<Report>& history);

int percent(const Stage& stage, bool done);

void addSamples(Samples& into, const std::string& workspace, const std::vector<Report>& history);

long long remaining(const Samples& samples, const std::string& workspace,
                    const std::vector<std::string>& sequence, const std::vector<Report>& history,
                    bool done, long long nowMs);

}
