#include <app/error/remedy.h>
#include "manager.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <time.h>
#include <vector>

#include <app/agent/history.h>
#include <app/catalog/render.h>
#include <app/catalog/store.h>
#include <app/delivery/git.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <app/metrics/metrics.h>
#include <core/worker.h>

#include "delivery.h"
#include "lane-view.h"

static const long long METRICS_MIN_DAYS = 1;
static const long long METRICS_MAX_DAYS = 3650;
static const char* METRICS_LANDING_CAP = "--max-count=50000";
static const char* METRICS_DOCUMENT_CAP = "--max-count=5000";
static const int METRICS_LANDING_TIMEOUT_MS = 30000;
static const int METRICS_DOCUMENT_TIMEOUT_MS = 15000;

static bool metricsWindow(const http::Request& req, long long& days)
{
    days = 30;
    if (!req.params.count("days")) return true;

    const std::string raw = api::queryValue(req, "days");
    if (raw.empty()) return false;
    for (char c : raw)
        if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    days = std::strtoll(raw.c_str(), nullptr, 10);
    return days >= METRICS_MIN_DAYS && days <= METRICS_MAX_DAYS;
}

static std::string metricsRefusal(const std::string& asked)
{
    return std::string(R"({"error":{"code":"bad_window",)")
         + R"("message":"`days` is a whole number of days between 1 and 3650",)"
         + R"("detail":{"param":"days","days":")" + manifest::jsonEscape(asked) + R"("})"
         + remedy::member(remedy::none()) + "}}";
}

static long long metricsEpochOfDate(const std::string& date)
{
    std::tm parsed {};
    if (!::strptime(date.c_str(), "%Y-%m-%d", &parsed)) return -1;
    return static_cast<long long>(::timegm(&parsed));
}

static std::string metricsRelativeTo(const std::string& root, const std::string& path)
{
    if (root.empty() || path.empty()) return {};
    if (path.compare(0, root.size(), root) != 0) return {};
    std::string rel = path.substr(root.size());
    while (!rel.empty() && rel.front() == '/') rel.erase(rel.begin());
    return rel;
}

static std::vector<std::string> metricsDocumentPathspecs(const std::string& root)
{
    std::vector<std::string> out;
    for (const auto& repo : manifest::Store::instance().data().repos) {
        for (const std::string* dir : {&repo.docs.epicsAbs, &repo.docs.ticketsAbs,
                                       &repo.docs.reportsAbs, &repo.docs.wikiAbs}) {
            const std::string rel = metricsRelativeTo(root, *dir);
            if (!rel.empty()) out.push_back(":(exclude)" + rel + "/");
        }
        const std::string file = metricsRelativeTo(root, repo.docs.escapesAbs);
        if (!file.empty()) out.push_back(":(exclude)" + file);
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    return out;
}

static std::string metricsSubjectId(const std::string& subject)
{
    const std::size_t end = subject.find_first_of(": \t");
    return end == std::string::npos ? subject : subject.substr(0, end);
}

static std::vector<std::string> metricsLines(const std::string& text)
{
    std::vector<std::string> out;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty() && line.compare(0, 7, "commit ") != 0) out.push_back(line);
    }
    return out;
}

static void metricsReadRounds(const std::vector<agent::history::Activity>& history,
                              metrics::Inputs& in)
{
    for (const auto& row : history) {
        metrics::Round round;
        round.ticketId = row.ticketId;
        round.hasRole = api::isRoleRun(row.role);
        round.startedAt = row.startedAt;
        round.activeS = row.activeS;
        round.stageSeconds = row.stageSeconds;
        in.rounds.push_back(std::move(round));
    }
}

static void metricsReadTickets(const std::string& root,
                               const std::vector<agent::history::Activity>& history,
                               std::map<std::string, std::string>& documentOf,
                               metrics::Inputs& in)
{
    const manifest::Store::Data& data = manifest::Store::instance().data();
    std::map<int, std::string> reportsByRepo;
    for (std::size_t r = 0; r < data.repos.size(); ++r)
        reportsByRepo[static_cast<int>(r)] = data.repos[r].docs.reportsAbs;

    std::map<std::string, std::string> firstStageOf;
    for (const auto& doc : catalog::Store::instance().tickets().documents) {
        if (doc.id.empty()) continue;
        metrics::Ticket ticket;
        ticket.status = doc.status;
        const auto reports = reportsByRepo.find(doc.repo);
        if (reports != reportsByRepo.end() && !reports->second.empty()) {
            std::error_code ec;
            const std::string qa = reports->second + "/" + doc.id + "-qa.md";
            ticket.verdictDocument =
                std::filesystem::exists(std::filesystem::path(qa), ec) && !ec;
        }
        in.tickets[doc.id] = ticket;

        const std::vector<std::string>& sequence =
            doc.hasStages && !doc.stages.empty() ? doc.stages : data.stages;
        if (!sequence.empty()) firstStageOf[doc.id] = sequence.front();
        documentOf[doc.id] = metricsRelativeTo(root, doc.path);
    }

    for (const auto& row : history) {
        const auto wanted = firstStageOf.find(row.ticketId);
        if (wanted == firstStageOf.end()) continue;
        const auto reported = row.firstStageAt.find(wanted->second);
        if (reported == row.firstStageAt.end() || reported->second < 0) continue;
        metrics::Ticket& ticket = in.tickets[row.ticketId];
        if (!ticket.hasFirstStage || reported->second < ticket.firstStageAt) {
            ticket.hasFirstStage = true;
            ticket.firstStageAt = reported->second;
        }
    }
}

static void metricsReadLedger(metrics::Inputs& in)
{
    for (const std::string& date : catalog::escapeRowDates()) {
        const long long at = metricsEpochOfDate(date);
        if (at >= 0) in.ledgerDates.push_back(at);
    }
}

HTTP_ASYNC_HANDLER(api::Manager, Metrics)
{
    long long days = 30;
    if (!metricsWindow(req, days)) {
        http::Response bad;
        bad.status = 400;
        bad.body = metricsRefusal(api::queryValue(req, "days"));
        return respond(std::move(bad));
    }

    delivery::Probe::instance().snapshotThen(
        [days, respond](std::shared_ptr<const delivery::Probe::Snapshot> snapshot) {
            const std::string base = snapshot ? snapshot->base : std::string();

            core::Worker::instance().post([days, base, respond] {
                const manifest::Store::Data& data = manifest::Store::instance().data();
                const std::string root = data.gitRoot.empty() ? data.root : data.gitRoot;
                const std::vector<agent::history::Activity> history =
                    agent::history::activity(data.ledgers());

                metrics::Inputs in;
                in.now = static_cast<long long>(std::time(nullptr));
                in.days = days;

                std::map<std::string, std::string> documentOf;
                metricsReadRounds(history, in);
                metricsReadTickets(root, history, documentOf, in);
                metricsReadLedger(in);

                if (!base.empty() && !root.empty()) {
                    std::vector<std::string> args {"--no-merges", METRICS_LANDING_CAP,
                                                   "--format=%ct%x1f%s", base, "--"};
                    for (const auto& spec : metricsDocumentPathspecs(root)) args.push_back(spec);
                    const delivery::Output landed =
                        delivery::git(root, "rev-list", args, METRICS_LANDING_TIMEOUT_MS);
                    for (const std::string& line : metricsLines(landed.text)) {
                        const std::size_t cut = line.find('\x1f');
                        if (cut == std::string::npos) continue;
                        const auto known =
                            in.tickets.find(metricsSubjectId(line.substr(cut + 1)));
                        if (known == in.tickets.end()) continue;
                        const long long at =
                            std::strtoll(line.substr(0, cut).c_str(), nullptr, 10);
                        if (at <= 0) continue;
                        known->second.landed = true;
                        known->second.landedAt = at;
                    }

                    in.rework = [root, base, documentOf](const std::string& id, long long since) {
                        const auto document = documentOf.find(id);
                        if (document == documentOf.end() || document->second.empty()) return false;
                        const delivery::Output touched = delivery::git(
                            root, "rev-list",
                            {"--no-merges", METRICS_DOCUMENT_CAP, "--format=%ct", base, "--",
                             document->second}, METRICS_DOCUMENT_TIMEOUT_MS);
                        for (const std::string& line : metricsLines(touched.text))
                            if (std::strtoll(line.c_str(), nullptr, 10) > since) return true;
                        return false;
                    };
                }

                http::Response resp;
                resp.body = metrics::toJson(in);
                respond(std::move(resp));
            });
        });
}
