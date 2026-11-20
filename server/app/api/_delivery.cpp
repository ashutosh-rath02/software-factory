#include "delivery.h"

#include <chrono>
#include <filesystem>
#include <map>
#include <set>
#include <memory>
#include <string>
#include <system_error>
#include <utility>
#include <vector>


#include <glog/logging.h>

#include <core/worker.h>

#include <app/api/lane-view.h>
#include <app/api/socket.h>
#include <app/catalog/store.h>
#include <app/agent/history.h>
#include <app/agent/session.h>
#include <app/lane/git.h>
#include <app/lane/store.h>
#include <app/land/lander.h>
#include <app/manifest/store.h>
#include <app/permissions/store.h>

namespace {

namespace fs = std::filesystem;

std::map<std::string, std::string> lastSent;
std::set<std::string> endedForLanding;

void applyRefusal(delivery::Probe::State& state, const std::string& reason)
{
    if (reason.empty()) return;
    state.needsYou = true;
    state.detail = state.detail.empty() ? reason : state.detail + " — " + reason;
}

std::string refusalFor(const api::Refusals& refusals, const std::string& ticketId)
{
    if (!refusals) return {};
    auto found = refusals->find(ticketId);
    return found == refusals->end() ? std::string() : found->second;
}

bool planLanding(const catalog::Document& document,
                 const delivery::Probe::Snapshot& snapshot, land::Lander::Request& out)
{
    if (snapshot.base.empty() || document.id.empty() || document.path.empty()) return false;
    const delivery::Probe::Branch* branch =
        delivery::Probe::branchFor(snapshot, document.id);
    if (!branch || branch->name.empty() || branch->name == snapshot.base) return false;

    const manifest::Store::Data& data = manifest::Store::instance().data();
    if (data.root.empty() || document.repo < 0
        || document.repo >= static_cast<int>(data.repos.size())) return false;
    const std::string reportsAbs = data.repos[document.repo].docs.reportsAbs;
    if (reportsAbs.empty()) return false;

    std::error_code ec;
    const fs::path root(data.root);
    const fs::path reports = fs::relative(fs::path(reportsAbs), root, ec);
    if (ec || reports.empty()) return false;
    const fs::path ticket = fs::relative(fs::path(document.path), root, ec);
    if (ec || ticket.empty()) return false;
    if (reports.string().compare(0, 2, "..") == 0) return false;
    if (ticket.string().compare(0, 2, "..") == 0) return false;

    out.ticketId = document.id;
    out.title = document.title;
    out.branch = branch->name;
    out.base = snapshot.base;
    out.ticketPath = ticket.string();
    out.reportsDir = reports.string();
    out.worktree = branch->worktree;
    if (!branch->worktree.empty() && !data.worktreesDir.empty()) {
        std::error_code under;
        const fs::path where = fs::weakly_canonical(fs::path(branch->worktree), under);
        const fs::path mine = fs::weakly_canonical(fs::path(data.worktreesDir), under);
        if (!under) {
            const std::string a = where.string();
            const std::string b = mine.string();
            out.worktreeIsALane = a.size() > b.size() + 1
                && a.compare(0, b.size(), b) == 0 && a[b.size()] == '/';
        }
    }
    return true;
}

std::map<std::string, std::vector<land::Lander::Request::Session>> sessionsTheToolRan()
{
    std::map<std::string, std::vector<land::Lander::Request::Session>> out;
    const std::vector<std::string> runs = manifest::Store::instance().data().ledgers();
    if (runs.empty()) return out;
    for (const auto& meta : agent::history::all(runs)) {
        if (meta.ticketId.empty() || meta.role.empty() || meta.role == "run") continue;
        out[meta.ticketId].push_back({meta.runId, meta.role, meta.branch, meta.branchTip});
    }
    return out;
}

void askTheLanes(std::vector<land::Lander::Request>& plans,
                 const lane::Store::Snapshot& lanes)
{
    for (auto& plan : plans) {
        std::set<std::string> roles(plan.roles.begin(), plan.roles.end());
        for (const auto& record : lanes.lanes) {
            if (record.ticketId == plan.ticketId && !record.role.empty()
                && record.role != "run")
                roles.insert(record.role);
            if (!plan.worktree.empty() && record.worktree == plan.worktree) {
                plan.worktreeIsALane = true;
                if (record.pid > 0 && lane::git::isRunning(record.pid))
                    plan.checkoutIsIdle = false;
            }
        }
        plan.roles.assign(roles.begin(), roles.end());
    }
}

using Rows = std::vector<std::pair<std::string, delivery::Probe::State>>;

struct Pass {
    Rows rows;
    std::vector<land::Lander::Request> plans;
};

void publishRows(Rows& rows, const api::Refusals& refusals)
{
    for (auto& [id, state] : rows) {
        applyRefusal(state, refusalFor(refusals, id));
        const std::string body = delivery::Probe::toJson(state);
        auto seen = lastSent.find(id);
        if (seen != lastSent.end() && seen->second == body) continue;
        lastSent[id] = body;
        api::Socket::instance().publishDeliveryState(id, body, state.needsYou);
    }
}

void decideEveryTicket(Rows& rows, const delivery::Probe::Snapshot& snapshot,
                        const delivery::Probe::LiveLanes& live)
{
    for (auto& d : catalog::Store::instance().tickets().documents)
        rows.push_back({d.id, delivery::Probe::decide(
            snapshot, {d.id, d.status, catalog::reportFileNameFor(d.id)}, live)});
}

void releaseLandedLanes(const Rows& rows, const lane::Store::Snapshot& lanes,
                        const std::map<std::string, permissions::Store::Blocked>& blocked)
{
    std::set<std::string> runs;
    for (const auto& record : lanes.lanes) runs.insert(record.runId);
    for (auto it = endedForLanding.begin(); it != endedForLanding.end();)
        it = runs.count(*it) ? std::next(it) : endedForLanding.erase(it);

    std::set<std::string> landed;
    for (const auto& [id, state] : rows)
        if (state.state == "done") landed.insert(id);
    if (landed.empty()) return;

    for (const auto& record : lanes.lanes) {
        if (!landed.count(record.ticketId)) continue;
        if (record.pid > 0 && lane::git::isRunning(record.pid)) {
            if (record.runId.empty() || endedForLanding.count(record.runId)) continue;
            if (lane::axesFor(record, nullptr, api::laneBlocked(blocked)).process != "idle")
                continue;
            endedForLanding.insert(record.runId);
            LOG(INFO) << "lane " << record.name << " holds " << record.ticketId
                      << " whose work is on the base branch and whose session has finished its "
                      << "turn; ending its session so the lane can be released";
            agent::Store::instance().end(
                record.name, [name = record.name, runId = record.runId](agent::Store::Answer out) {
                    if (out.status < 300) return;
                    LOG(WARNING) << "lane " << name << ": ending run " << runId
                                 << " for its landed work was refused (" << out.status << " "
                                 << out.errorCode << "); the lane stays held until a person "
                                 << "releases it";
                });
            continue;
        }
        LOG(INFO) << "lane " << record.name << " holds " << record.ticketId
                  << " whose work is on the base branch and has no process in it; releasing "
                  << "the seat and keeping the branch";
        lane::Store::instance().release(record.name, false, false,
                                        [](lane::Store::Outcome) {});
    }
}

}

catalog::DeliveryLookup api::deliveryLookup(
    std::shared_ptr<const delivery::Probe::Snapshot> snapshot, api::LiveLanes live,
    api::Refusals refusals)
{
    return [snapshot, live, refusals](const std::string& ticketId,
                                      const std::string& claimedStatus,
                                      const std::string& reportFileName) {
        catalog::Delivery out;
        if (!snapshot) {
            out.state = "unknown";
            out.claimed = claimedStatus;
            out.reason = "the first pass over this repository has not landed yet";
            return out;
        }
        delivery::Probe::State st = delivery::Probe::decide(
            *snapshot, {ticketId, claimedStatus, reportFileName},
            live ? *live : delivery::Probe::LiveLanes());
        applyRefusal(st, refusalFor(refusals, ticketId));
        out.state = st.state;
        out.detail = st.detail;
        out.claimed = st.claimed;
        out.reason = st.reason;
        out.disagrees = st.disagrees;
        out.needsYou = st.needsYou;
        return out;
    };
}

catalog::UnpushedLookup api::unpushedLookup(
    std::shared_ptr<const delivery::Probe::Snapshot> snapshot)
{
    return [snapshot](const std::string& ticketId) {
        catalog::Unpushed out;
        if (!snapshot) return out;
        const delivery::Probe::Branch* branch =
            delivery::Probe::branchFor(*snapshot, ticketId);
        if (!branch || branch->pushed || branch->ahead <= 0) return out;
        out.present = true;
        out.branch = branch->name;
        out.commits = branch->ahead;
        const long long now =
            std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
        if (branch->unpushedSinceS > 0 && now > branch->unpushedSinceS)
            out.ageS = now - branch->unpushedSinceS;
        return out;
    };
}

void api::withDelivery(
    std::function<void(std::shared_ptr<const delivery::Probe::Snapshot>, api::LiveLanes,
                       api::Refusals)> done)
{
    lane::Store::instance().list([done](lane::Store::Outcome out) {
        auto lanes = std::make_shared<const lane::Store::Snapshot>(std::move(out.snapshot));
        core::Worker::instance().post([lanes, done] {
            auto live = std::make_shared<delivery::Probe::LiveLanes>(api::liveLanes(*lanes));
            land::Lander::instance().refusalsThen(
                [done, live](std::shared_ptr<const land::Lander::Refusals> refusals) {
                    delivery::Probe::instance().snapshotThen(
                        [done, live, refusals](
                            std::shared_ptr<const delivery::Probe::Snapshot> snapshot) {
                            done(snapshot, live, refusals);
                        });
                });
        });
    });
}

catalog::AuthorLookup api::authorLookup(
    std::shared_ptr<const delivery::Probe::Snapshot> snapshot)
{
    return [snapshot](const std::string& ticketId) {
        if (!snapshot) return std::string();
        const delivery::Probe::Branch* branch =
            delivery::Probe::branchFor(*snapshot, ticketId);
        return branch ? branch->author : std::string();
    };
}

void api::renderWithDeliveryAndAuthors(
    std::function<catalog::Result(const catalog::DeliveryLookup&,
                                  const catalog::AuthorLookup&)> render,
    http::Responder respond)
{
    auto job = std::make_shared<std::function<catalog::Result(const catalog::DeliveryLookup&,
                                                              const catalog::AuthorLookup&)>>(
        std::move(render));
    api::withDelivery(
        [job, respond](std::shared_ptr<const delivery::Probe::Snapshot> snapshot,
                       api::LiveLanes live, api::Refusals refusals) {
            core::Worker::instance().post([job, snapshot, live, refusals, respond] {
                const catalog::Result out = (*job)(api::deliveryLookup(snapshot, live, refusals),
                                                   api::authorLookup(snapshot));
                http::Response resp;
                resp.status = out.status;
                resp.body = out.body;
                respond(std::move(resp));
            });
        });
}

void api::renderWithDelivery(
    std::function<catalog::Result(const catalog::DeliveryLookup&)> render,
    http::Responder respond)
{
    auto job = std::make_shared<std::function<catalog::Result(const catalog::DeliveryLookup&)>>(
        std::move(render));
    api::withDelivery(
        [job, respond](std::shared_ptr<const delivery::Probe::Snapshot> snapshot,
                       api::LiveLanes live, api::Refusals refusals) {
            core::Worker::instance().post([job, snapshot, live, refusals, respond] {
                const catalog::Result out = (*job)(api::deliveryLookup(snapshot, live, refusals));
                http::Response resp;
                resp.status = out.status;
                resp.body = out.body;
                respond(std::move(resp));
            });
        });
}

void api::watchDelivery()
{
    delivery::Probe::instance().onSnapshot(
        [](std::shared_ptr<const delivery::Probe::Snapshot> snapshot) {
            lane::Store::instance().list([snapshot](lane::Store::Outcome out) {
                auto lanes = std::make_shared<const lane::Store::Snapshot>(
                    std::move(out.snapshot));
                delivery::Probe::instance().queue().offload(
                    [snapshot, lanes] {
                        const std::map<std::string,
                                       std::vector<land::Lander::Request::Session>> ran =
                            sessionsTheToolRan();
                        const delivery::Probe::LiveLanes live = api::liveLanes(*lanes);
                        Pass pass;
                        decideEveryTicket(pass.rows, *snapshot, live);
                        for (auto& d : catalog::Store::instance().tickets().documents) {
                            land::Lander::Request plan;
                            if (!planLanding(d, *snapshot, plan)) continue;
                            if (auto seen = ran.find(d.id); seen != ran.end()) {
                                plan.sessions = seen->second;
                                std::set<std::string> roles;
                                for (const auto& session : seen->second)
                                    roles.insert(session.role);
                                plan.roles.assign(roles.begin(), roles.end());
                            }
                            pass.plans.push_back(plan);
                        }
                        return pass;
                    },
                    [lanes](Pass pass) {
                        permissions::Store::instance().blockedSnapshot(
                            [pass, lanes](std::map<std::string, permissions::Store::Blocked>
                                              blocked) mutable {
                                land::Lander::instance().refusalsThen(
                                    [pass, lanes, blocked](api::Refusals refusals) mutable {
                                        publishRows(pass.rows, refusals);
                                        releaseLandedLanes(pass.rows, *lanes, blocked);
                                        askTheLanes(pass.plans, *lanes);
                                        land::Lander::instance().consider(pass.plans);
                                    });
                            });
                    });
            });
        });
}

void api::publishDeliveryStates()
{
    delivery::Probe::instance().snapshotThen(
        [](std::shared_ptr<const delivery::Probe::Snapshot> snapshot) {
            if (!snapshot) return;
            lane::Store::instance().list([snapshot](lane::Store::Outcome out) {
                auto lanes = std::make_shared<const lane::Store::Snapshot>(
                    std::move(out.snapshot));
                delivery::Probe::instance().queue().offload(
                    [snapshot, lanes] {
                        const delivery::Probe::LiveLanes live = api::liveLanes(*lanes);
                        Rows rows;
                        decideEveryTicket(rows, *snapshot, live);
                        return rows;
                    },
                    [](Rows rows) {
                        land::Lander::instance().refusalsThen(
                            [rows](api::Refusals refusals) mutable {
                                publishRows(rows, refusals);
                            });
                    });
            });
        });
}
