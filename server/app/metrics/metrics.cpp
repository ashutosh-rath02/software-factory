#include "metrics.h"

#include <algorithm>
#include <iomanip>
#include <sstream>

#include <app/manifest/json.h>

static std::string metricsNumber(double v)
{
    if (v == static_cast<long long>(v)) return std::to_string(static_cast<long long>(v));
    std::ostringstream o;
    o << std::setprecision(12) << v;
    return o.str();
}

static std::string metricsOptional(bool has, double v)
{
    return has ? metricsNumber(v) : std::string("null");
}

static double metricsMedian(std::vector<double> values)
{
    std::sort(values.begin(), values.end());
    const std::size_t n = values.size();
    if (n % 2) return values[n / 2];
    return (values[n / 2 - 1] + values[n / 2]) / 2.0;
}

static double metricsMean(const std::vector<double>& values)
{
    double total = 0;
    for (double v : values) total += v;
    return total / static_cast<double>(values.size());
}

static void metricsSeries(std::ostringstream& o, const std::string& name,
                          const std::vector<double>& values)
{
    o << "\"" << name << "\":{\"median\":"
      << metricsOptional(!values.empty(), values.empty() ? 0 : metricsMedian(values))
      << ",\"mean\":"
      << metricsOptional(!values.empty(), values.empty() ? 0 : metricsMean(values))
      << ",\"n\":" << values.size() << "}";
}

std::string metrics::toJson(const Inputs& in)
{
    const long long cutoff = in.now - in.days * 86400;

    std::vector<const Round*> window;
    for (const Round& r : in.rounds)
        if (r.startedAt >= cutoff && r.startedAt <= in.now) window.push_back(&r);

    std::map<std::string, int> roundsByTicket;
    std::map<std::string, long long> firstRoundAt;
    std::map<std::string, int> roleRunsByTicket;
    std::vector<double> activeMinutes;
    std::map<std::string, std::vector<double>> stageMinutes;

    for (const Round* r : window) {
        if (r->ticketId.empty()) continue;
        if (r->hasRole) {
            ++roleRunsByTicket[r->ticketId];
            continue;
        }
        ++roundsByTicket[r->ticketId];
        auto first = firstRoundAt.find(r->ticketId);
        if (first == firstRoundAt.end() || r->startedAt < first->second)
            firstRoundAt[r->ticketId] = r->startedAt;
        if (r->activeS <= 0) continue;
        activeMinutes.push_back(static_cast<double>(r->activeS) / 60.0);
        for (const auto& [name, seconds] : r->stageSeconds)
            stageMinutes[name].push_back(static_cast<double>(seconds) / 60.0);
    }

    std::vector<double> perTicket;
    for (const auto& [id, count] : roundsByTicket) perTicket.push_back(count);

    int firstPass = 0;
    for (const auto& [id, runs] : roleRunsByTicket) {
        const auto known = in.tickets.find(id);
        if (known == in.tickets.end()) continue;
        if (runs != 1) continue;
        if (!known->second.verdictDocument) continue;
        if (known->second.status != "pass") continue;
        ++firstPass;
    }

    std::vector<double> specToLand;
    for (const auto& [id, ticket] : in.tickets) {
        if (!ticket.landed || !ticket.hasFirstStage) continue;
        if (ticket.landedAt < cutoff || ticket.landedAt > in.now) continue;
        if (ticket.landedAt < ticket.firstStageAt) continue;
        specToLand.push_back(static_cast<double>(ticket.landedAt - ticket.firstStageAt) / 3600.0);
    }

    int reworked = 0;
    for (const auto& [id, since] : firstRoundAt)
        if (in.rework && in.rework(id, since)) ++reworked;

    int escapes = 0;
    for (long long at : in.ledgerDates)
        if (at >= cutoff && at <= in.now) ++escapes;

    std::ostringstream o;
    o << "{\"window_days\":" << in.days
      << ",\"runs\":" << window.size()
      << ",\"tickets_with_verdict\":" << roleRunsByTicket.size()
      << ",\"first_pass_qa_rate\":"
      << metricsOptional(!roleRunsByTicket.empty(),
                         roleRunsByTicket.empty()
                             ? 0
                             : static_cast<double>(firstPass)
                                   / static_cast<double>(roleRunsByTicket.size()))
      << ",\"rounds_per_ticket\":{\"median\":"
      << metricsOptional(!perTicket.empty(), perTicket.empty() ? 0 : metricsMedian(perTicket))
      << ",\"mean\":"
      << metricsOptional(!perTicket.empty(), perTicket.empty() ? 0 : metricsMean(perTicket))
      << "},\"stage_minutes\":[";
    bool first = true;
    for (const auto& [name, values] : stageMinutes) {
        if (!first) o << ",";
        first = false;
        o << "{\"name\":\"" << manifest::jsonEscape(name)
          << "\",\"median\":" << metricsNumber(metricsMedian(values))
          << ",\"mean\":" << metricsNumber(metricsMean(values))
          << ",\"n\":" << values.size() << "}";
    }
    o << "],";
    metricsSeries(o, "active_minutes", activeMinutes);
    o << ",\"spec_to_land_hours\":{\"median\":"
      << metricsOptional(!specToLand.empty(), specToLand.empty() ? 0 : metricsMedian(specToLand))
      << ",\"n\":" << specToLand.size()
      << "},\"escapes_in_window\":" << escapes
      << ",\"rework_after_build\":{\"tickets\":" << reworked
      << ",\"share\":"
      << metricsOptional(!firstRoundAt.empty(),
                         firstRoundAt.empty()
                             ? 0
                             : static_cast<double>(reworked)
                                   / static_cast<double>(firstRoundAt.size()))
      << "}}";
    return o.str();
}
