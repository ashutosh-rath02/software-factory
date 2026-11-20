#include "manager.h"

#include <map>
#include <memory>
#include <string>

#include "delivery.h"
#include "lane-view.h"
#include "roles.h"
#include <app/agent/history.h>
#include <app/catalog/render.h>
#include <app/lane/store.h>
#include <app/manifest/store.h>
#include <app/permissions/store.h>

HTTP_ASYNC_HANDLER(api::Manager, Tickets)
{
    catalog::TicketQuery query;
    query.q = api::queryValue(req, "q");
    query.provenance = api::queryValue(req, "provenance");
    query.stage = api::queryValue(req, "stage");
    query.page = api::queryValue(req, "page");
    query.pageSize = api::queryValue(req, "page_size");

    auto totals = std::make_shared<std::map<std::string, catalog::Runs>>();
    auto read = std::make_shared<bool>(false);
    const catalog::RunsLookup runs = [totals, read](const std::string& ticketId) {
        if (!*read) {
            *read = true;
            for (const auto& row : agent::history::forTicket(
                     manifest::Store::instance().data().ledgers(), std::string())) {
                catalog::Runs& into = (*totals)[row.meta.ticketId];
                ++into.count;
                into.durationS += row.meta.durationS;
                into.costUsd += row.meta.costUsd;
                if (into.lastOutcome.empty()) into.lastOutcome = row.meta.outcome;
            }
        }
        auto found = totals->find(ticketId);
        return found == totals->end() ? catalog::Runs() : found->second;
    };

    lane::Store::instance().list([query, runs, respond](lane::Store::Outcome out) {
        auto snapshot = std::make_shared<const lane::Store::Snapshot>(std::move(out.snapshot));
        permissions::Store::instance().blockedSnapshot(
            [query, runs, snapshot, respond](
                std::map<std::string, permissions::Store::Blocked> blocked) {
                lane::Store::instance().stageHistory(
                    [query, runs, snapshot, blocked, respond](
                        lane::Store::StageHistory history) {
                        lane::Store::instance().roleStates(
                            [query, runs, snapshot, blocked, history, respond](
                                lane::Store::RoleStates states) {
                                api::renderWithDeliveryAndAuthors(
                                    [query, runs, snapshot, blocked, history, states](
                                        const catalog::DeliveryLookup& look,
                                        const catalog::AuthorLookup& authors) {
                                        return catalog::renderTickets(
                                            query, look, runs,
                                            api::stageLookup(history),
                                            api::roleLookup(states),
                                            api::laneLookup(snapshot, blocked),
                                            authors);
                                    },
                                    respond);
                            });
                    });
            });
    });
}
