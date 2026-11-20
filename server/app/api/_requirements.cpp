#include "manager.h"

#include <memory>

#include "delivery.h"
#include "lane-view.h"
#include <app/catalog/render.h>
#include <app/lane/store.h>
#include <app/permissions/store.h>

HTTP_ASYNC_HANDLER(api::Manager, Requirements)
{
    auto q = req.params.count("q") ? req.params.at("q") : std::string();
    auto filter = req.params.count("filter") ? req.params.at("filter") : std::string();

    lane::Store::instance().list([q, filter, respond](lane::Store::Outcome out) {
        auto snapshot = std::make_shared<const lane::Store::Snapshot>(std::move(out.snapshot));
        permissions::Store::instance().blockedSnapshot(
            [q, filter, snapshot, respond](std::map<std::string, permissions::Store::Blocked> blocked) {
                api::renderWithDelivery(
                    [q, filter, snapshot, blocked](const catalog::DeliveryLookup& look) {
                        return catalog::renderRequirements(q, filter, look,
                                                           api::laneLookup(snapshot, blocked));
                    },
                    respond);
            });
    });
}
