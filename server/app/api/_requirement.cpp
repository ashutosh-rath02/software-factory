#include "manager.h"

#include <memory>

#include "delivery.h"
#include "lane-view.h"
#include <app/agent/history.h>
#include <app/catalog/render.h>
#include <app/lane/store.h>
#include <app/manifest/store.h>
#include <app/permissions/store.h>

HTTP_ASYNC_HANDLER(api::Manager, Requirement)
{
    const std::string id = req.params.count("id") ? req.params.at("id") : std::string();

    lane::Store::instance().list([id, respond](lane::Store::Outcome out) {
        auto snapshot = std::make_shared<const lane::Store::Snapshot>(std::move(out.snapshot));
        permissions::Store::instance().blockedSnapshot(
            [id, snapshot, respond](std::map<std::string, permissions::Store::Blocked> blocked) {
                lane::Store::instance().stageHistory(
                    [id, snapshot, blocked, respond](lane::Store::StageHistory history) {
                        api::renderWithDelivery(
                            [id, snapshot, blocked, history](const catalog::DeliveryLookup& look) {
                                const auto outcomes = agent::history::lastOutcomeByTicket(
                                    manifest::Store::instance().data().ledgers());
                                const catalog::OutcomeLookup outcome =
                                    [&outcomes](const std::string& ticketId) {
                                        const auto found = outcomes.find(ticketId);
                                        return found == outcomes.end() ? std::string()
                                                                       : found->second;
                                    };
                                return catalog::renderRequirement(
                                    id, look, api::stageLookup(history), outcome,
                                    api::laneLookup(snapshot, blocked));
                            },
                            respond);
                    });
            });
    });
}
