#include "manager.h"

#include <cstdlib>

#include "delivery.h"
#include "lane-view.h"
#include "roles.h"
#include <app/agent/history.h>
#include <app/catalog/render.h>
#include <app/lane/store.h>
#include <app/manifest/store.h>
#include <glog/logging.h>

HTTP_ASYNC_HANDLER(api::Manager, Ticket)
{
    static const bool withoutAuthors = [] {
        const char* hooks = std::getenv("FACTORY_TEST_HOOKS");
        const char* drop = std::getenv("FACTORY_TEST_TICKET_WITHOUT_AUTHORS");
        const bool on = hooks && *hooks && drop && *drop;
        if (on)
            LOG(WARNING) << "FACTORY_TEST_TICKET_WITHOUT_AUTHORS is set: "
                            "GET /api/tickets/{id} serves no owner";
        return on;
    }();

    const std::string id = req.params.count("id") ? req.params.at("id") : std::string();

    const catalog::RunsLookup runs = [](const std::string& ticketId) {
        const auto totals = agent::history::totalsFor(
            manifest::Store::instance().data().ledgers(), ticketId);
        catalog::Runs out;
        out.count = totals.count;
        out.durationS = totals.durationS;
        out.costUsd = totals.costUsd;
        const auto rows = agent::history::forTicket(
            manifest::Store::instance().data().ledgers(), ticketId);
        if (!rows.empty()) out.lastOutcome = rows.front().meta.outcome;
        return out;
    };

    lane::Store::instance().stageHistory(
        [id, runs, respond](lane::Store::StageHistory history) {
            lane::Store::instance().roleStates(
                [id, runs, history, respond](lane::Store::RoleStates states) {
                    api::renderWithDeliveryAndAuthors(
                        [id, runs, history, states](const catalog::DeliveryLookup& look,
                                                    const catalog::AuthorLookup& authors) {
                            return catalog::renderTicket(
                                id, look, runs, api::stageLookup(history),
                                api::roleLookup(states),
                                withoutAuthors ? catalog::AuthorLookup{} : authors);
                        },
                        respond);
                });
        });
}
