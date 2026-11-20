#include "lane-view.h"

#include <memory>
#include <set>
#include <sstream>

#include <core/worker.h>
#include <app/api/socket.h>
#include <app/api/subscription.h>
#include <app/catalog/store.h>
#include <app/lane/state.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>

namespace {

using manifest::jstr;
}

catalog::StageLookup api::stageLookup(const lane::Store::StageHistory& history)
{
    return [history](const std::string& ticketId, const std::vector<std::string>& sequence) {
        catalog::Stage out;
        auto known = history.find(ticketId);
        if (known == history.end()) return out;
        const lane::progress::Stage stage =
            lane::progress::of(sequence, std::string(), known->second);
        out.index = stage.index;
        out.round = stage.round;
        out.of = stage.of;
        return out;
    };
}

lane::ProgressFn api::laneProgress(const lane::Store::StageHistory& history)
{
    const auto& m = manifest::Store::instance().data();
    auto docs = std::make_shared<std::map<std::string, catalog::Document>>();
    for (auto& doc : catalog::Store::instance().tickets().documents)
        docs->emplace(doc.id, std::move(doc));

    auto samples = std::make_shared<lane::progress::Samples>();
    for (const auto& [ticketId, reports] : history) {
        auto doc = docs->find(ticketId);
        lane::progress::addSamples(*samples, doc == docs->end() ? std::string() : doc->second.workspace,
                                   reports);
    }

    auto stages = std::make_shared<const lane::Store::StageHistory>(history);
    std::set<std::string> done(m.doneStatus.begin(), m.doneStatus.end());
    const long long now = lane::state::nowMs();
    return [docs, samples, stages, done = std::move(done), fallback = m.stages, now](
               const std::string& ticketId) {
        static const std::vector<lane::progress::Report> none;
        auto doc = docs->find(ticketId);
        const bool known = doc != docs->end();
        const bool own = known && doc->second.hasStages;
        const std::vector<std::string>& sequence = own ? doc->second.stages : fallback;
        auto found = stages->find(ticketId);
        const auto& reports = found == stages->end() ? none : found->second;
        const bool finished = known && done.count(doc->second.status) > 0;

        lane::progress::Shown out;
        out.stage = lane::progress::of(sequence, own ? "ticket" : "manifest", reports);
        out.pct = lane::progress::percent(out.stage, finished);
        out.remainingS = lane::progress::remaining(*samples, known ? doc->second.workspace : "",
                                                   sequence, reports, finished, now);
        return out;
    };
}

std::string api::stageMembers(const std::string& lane, const std::string& ticketId,
                              const lane::progress::Shown& shown)
{
    const lane::progress::Stage& stage = shown.stage;
    std::ostringstream o;
    o << "\"lane\":" << jstr(lane)
      << ",\"ticket_id\":" << jstr(ticketId)
      << ",\"stage\":{\"index\":" << stage.index
      << ",\"total\":" << stage.total
      << ",\"name\":" << jstr(stage.name)
      << ",\"round\":" << stage.round
      << ",\"of\":" << stage.of
      << ",\"source\":" << jstr(stage.source) << "}"
      << ",\"progress_pct\":" << shown.pct
      << ",\"remaining_s\":";
    if (shown.remainingS < 0) o << "null"; else o << shown.remainingS;
    if (!stage.regressedFrom.empty()) o << ",\"regressed_from\":" << jstr(stage.regressedFrom);
    return o.str();
}

void api::publishStagesOf(const std::string& ticketId)
{
    lane::Store::instance().list([ticketId](lane::Store::Outcome out) {
        std::vector<std::string> lanes;
        for (const auto& record : out.snapshot.lanes)
            if (record.ticketId == ticketId) lanes.push_back(record.name);
        if (lanes.empty()) return;
        core::Worker::instance().post(
            [ticketId, lanes, history = std::move(out.snapshot.stages)] {
                const lane::progress::Shown shown = api::laneProgress(history)(ticketId);
                for (const auto& name : lanes)
                    api::Socket::instance().publishStage(api::stageMembers(name, ticketId, shown),
                                                         api::laneStateScopes(name, ticketId));
            });
    });
}

namespace {

int hexOf(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

std::string percentDecoded(const std::string& s)
{
    std::string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size()) {
            const int hi = hexOf(s[i + 1]), lo = hexOf(s[i + 2]);
            if (hi >= 0 && lo >= 0) {
                out.push_back(static_cast<char>(hi * 16 + lo));
                i += 2;
                continue;
            }
        }
        out.push_back(s[i]);
    }
    return out;
}

}

namespace {

int processRank(const std::string& process)
{
    if (process == "running") return 0;
    if (process == "blocked") return 1;
    if (process == "idle") return 2;
    if (process == "queued") return 3;
    return 4;
}

bool agentIsAlive(const std::string& process)
{
    return process == "running" || process == "blocked" || process == "idle";
}

}

delivery::Probe::LiveLanes api::liveLanes(const lane::Store::Snapshot& lanes)
{
    delivery::Probe::LiveLanes out;
    std::map<std::string, int> best;
    for (const auto& record : lanes.lanes) {
        if (record.ticketId.empty()) continue;
        const std::string process = lane::axesFor(record, nullptr, nullptr).process;
        if (!agentIsAlive(process)) continue;
        const int rank = processRank(process);
        auto seen = best.find(record.ticketId);
        if (seen != best.end() && seen->second <= rank) continue;
        best[record.ticketId] = rank;
        out[record.ticketId] = {record.name, process == "idle"};
    }
    return out;
}

lane::DeliveryFn api::laneDelivery(std::shared_ptr<const delivery::Probe::Snapshot> snapshot,
                                   api::LiveLanes live)
{
    return [snapshot, live](const lane::Store::Record& record) -> std::string {
        if (!snapshot || record.ticketId.empty()) return {};
        auto& store = catalog::Store::instance();
        for (const auto& doc : store.tickets().documents) {
            if (doc.id != record.ticketId) continue;
            return delivery::Probe::decide(
                *snapshot, {doc.id, doc.status, catalog::reportFileNameFor(doc.id)},
                live ? *live : delivery::Probe::LiveLanes()).state;
        }
        return {};
    };
}

lane::EvidenceFn api::laneEvidence()
{
    return [](const lane::Store::Record& record) {
        auto& store = catalog::Store::instance();
        std::string runId;
        return store.evidenceFor(record.ticketId, store.runIds(), runId);
    };
}

catalog::LaneLookup api::laneLookup(std::shared_ptr<const lane::Store::Snapshot> snapshot,
                                    const std::map<std::string, permissions::Store::Blocked>& blocked)
{
    return [snapshot, blocked](const std::string& ticketId) {
        catalog::LaneState best;
        int bestRank = 5;
        for (const auto& record : snapshot->lanes) {
            if (record.ticketId != ticketId) continue;
            const lane::state::Axes axes =
                lane::axesFor(record, nullptr, api::laneBlocked(blocked));
            if (processRank(axes.process) >= bestRank) continue;
            bestRank = processRank(axes.process);
            best.lane = record.name;
            best.role = record.role;
            best.process = axes.process;
            best.motion = axes.motion.value;
            best.evidence = axes.evidence;
        }
        return best;
    };
}

lane::BlockedFn api::laneBlocked(const std::map<std::string, permissions::Store::Blocked>& blocked)
{
    return [blocked](const lane::Store::Record& record, std::string& reason, std::string& requestId) {
        auto it = blocked.find(record.name);
        if (it == blocked.end()) return false;
        reason = it->second.reason;
        requestId = it->second.requestId;
        return true;
    };
}

namespace {

void withRecord(const std::string& name, std::function<void(const lane::Store::Record*)> done)
{
    lane::Store::instance().list([name, done](lane::Store::Outcome out) {
        for (const auto& record : out.snapshot.lanes)
            if (record.name == name) return done(&record);
        done(nullptr);
    });
}

}

void api::publishLaneState(const std::string& name)
{
    withRecord(name, [name](const lane::Store::Record* found) {
        if (!found) {
            api::Socket::instance().forgetLaneState(name);
            return;
        }
        auto record = std::make_shared<lane::Store::Record>(*found);
        permissions::Store::instance().blockedSnapshot(
            [record](std::map<std::string, permissions::Store::Blocked> blocked) {
                core::Worker::instance().post([record, blocked] {
                    const lane::StateFrame frame = lane::renderLaneState(
                        *record, api::laneEvidence(), api::laneBlocked(blocked));
                    api::Socket::instance().publishLaneState(
                        record->name, frame.key, frame.members,
                        api::laneStateScopes(record->name, record->ticketId));
                });
            });
    });
}

void api::publishLaneMembership(const std::string& lane, const std::string& ticketId,
                                const std::string& workspace, bool claimed)
{
    std::ostringstream members;
    members << "\"lane\":" << jstr(lane)
            << ",\"ticket_id\":" << jstr(ticketId)
            << ",\"workspace\":" << jstr(workspace)
            << ",\"change\":" << jstr(claimed ? "claimed" : "released");
    api::Socket::instance().publishLaneMembership(members.str(),
                                                  api::laneStateScopes(lane, ticketId));
}

void api::publishAllLaneStates()
{
    lane::Store::instance().list([](lane::Store::Outcome out) {
        for (const auto& record : out.snapshot.lanes) api::publishLaneState(record.name);
    });
}

void api::publishLaneMetrics(const std::string& name, int turnN, double turnCostUsd,
                             double costUsd)
{
    lane::Store::instance().list([name, turnN, turnCostUsd, costUsd](lane::Store::Outcome out) {
        const lane::Store::Record* found = nullptr;
        for (const auto& record : out.snapshot.lanes)
            if (record.name == name) found = &record;
        if (!found) return;
        const long long elapsed = found->claimedAtMs > 0
            ? (lane::state::nowMs() - found->claimedAtMs) / 1000 : 0;
        core::Worker::instance().post(
            [name, turnN, turnCostUsd, costUsd, elapsed, ticketId = found->ticketId,
             history = std::move(out.snapshot.stages)] {
                const long long left = api::laneProgress(history)(ticketId).remainingS;
                std::ostringstream o;
                o << "\"lane\":\"" << manifest::jsonEscape(name) << "\""
                  << ",\"elapsed_s\":" << elapsed
                  << ",\"cost_usd\":" << costUsd
                  << ",\"remaining_s\":";
                if (left < 0) o << "null"; else o << left;
                o << ",\"turn\":{\"n\":" << turnN << ",\"cost_usd\":" << turnCostUsd << "}";
                api::Socket::instance().publishLaneMetrics(o.str(),
                                                           api::laneStateScopes(name, ticketId));
            });
    });
}

void api::publishLaneWords(const std::string& lane, const std::string& ticketId,
                           const ::lane::Store::Words& words)
{
    std::ostringstream members;
    members << "\"lane\":" << jstr(lane)
            << ",\"ticket_id\":" << jstr(ticketId)
            << ",\"last_words\":" << ::lane::renderWords(words);
    api::Socket::instance().publishLaneWords(members.str(),
                                             api::laneStateScopes(lane, ticketId));
}

bool api::isRoleRun(const std::string& role)
{
    if (role.empty()) return false;
    for (const manifest::Store::Role& declared : manifest::Store::instance().data().roles)
        if (declared.name == role) return true;
    return false;
}

std::string api::runKind(const std::string& role)
{
    return isRoleRun(role) ? role : std::string("run");
}

std::string api::queryValue(const http::Request& req, const std::string& name)
{
    auto found = req.params.find(name);
    return found == req.params.end() ? std::string() : percentDecoded(found->second);
}

std::string api::laneNameOf(const http::Request& req)
{
    auto head = req.params.find("lane");
    if (head == req.params.end()) return {};
    std::string name = percentDecoded(head->second);
    auto tail = req.params.find("n");
    if (tail != req.params.end()) name += "/" + percentDecoded(tail->second);
    return name;
}
