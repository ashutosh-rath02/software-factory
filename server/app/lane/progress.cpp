#include "progress.h"

#include <algorithm>

int lane::progress::indexOf(const std::vector<std::string>& sequence, const std::string& name)
{
    for (std::size_t i = 0; i < sequence.size(); ++i)
        if (sequence[i] == name) return static_cast<int>(i) + 1;
    return 0;
}

lane::progress::Stage lane::progress::of(const std::vector<std::string>& sequence,
                                         const std::string& source,
                                         const std::vector<Report>& history)
{
    Stage out;
    out.total = static_cast<int>(sequence.size());
    out.source = source;
    if (history.empty()) return out;

    const Report& last = history.back();
    out.name = last.name;
    out.index = indexOf(sequence, last.name);
    out.unknown = out.index == 0;

    if (last.hasRound) {
        out.round = last.round;
    } else {
        int seen = 0;
        for (const auto& r : history)
            if (r.name == last.name) ++seen;
        out.round = seen;
    }

    out.of = last.hasOf ? last.of : out.round;

    if (history.size() >= 2) {
        const Report& previous = history[history.size() - 2];
        const int before = indexOf(sequence, previous.name);
        if (before > 0 && out.index > 0 && out.index < before) out.regressedFrom = previous.name;
    }
    return out;
}

int lane::progress::percent(const Stage& stage, bool done)
{
    if (done) return 100;
    if (stage.index <= 0 || stage.total <= 0) return 0;
    return std::max(0, (stage.index - 1) * 100 / stage.total);
}

void lane::progress::addSamples(Samples& into, const std::string& workspace,
                                const std::vector<Report>& history)
{
    for (std::size_t i = 0; i < history.size(); ++i) {
        const Report& r = history[i];
        if (r.atMs <= 0) continue;
        long long until = r.endMs;
        if (until <= 0 && i + 1 < history.size() && !r.runId.empty()
            && history[i + 1].runId == r.runId)
            until = history[i + 1].atMs;
        if (until <= 0 || until < r.atMs) continue;
        into[{workspace, r.name}].push_back((until - r.atMs) / 1000);
    }
}

long long lane::progress::remaining(const Samples& samples, const std::string& workspace,
                                    const std::vector<std::string>& sequence,
                                    const std::vector<Report>& history, bool done, long long nowMs)
{
    if (done) return 0;
    if (history.empty()) return -1;
    const Report& last = history.back();
    const int index = indexOf(sequence, last.name);
    if (index == 0) return -1;

    long long total = 0;
    bool measured = false;
    for (std::size_t k = static_cast<std::size_t>(index - 1); k < sequence.size(); ++k) {
        auto found = samples.find({workspace, sequence[k]});
        if (found == samples.end() || found->second.empty()) continue;
        if (found->second.size() < 3) return -1;
        std::vector<long long> sorted = found->second;
        std::sort(sorted.begin(), sorted.end());
        long long median = sorted[(sorted.size() - 1) / 2];
        if (static_cast<int>(k) == index - 1 && last.atMs > 0)
            median = std::max(0LL, median - (nowMs - last.atMs) / 1000);
        total += median;
        measured = true;
    }
    return measured ? total : -1;
}
