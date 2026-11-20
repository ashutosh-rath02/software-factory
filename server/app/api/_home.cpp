#include "manager.h"

#include <map>
#include <memory>

#include <core/worker.h>

#include "delivery.h"
#include "lane-view.h"
#include "roles.h"
#include <app/catalog/render.h>
#include <app/lane/store.h>
#include <app/permissions/store.h>

HTTP_ASYNC_HANDLER(api::Manager, Home)
{
    (void)req;

    lane::Store::instance().list([respond](lane::Store::Outcome out) {
        auto snapshot = std::make_shared<const lane::Store::Snapshot>(std::move(out.snapshot));
        permissions::Store::instance().blockedSnapshot(
            [snapshot, respond](std::map<std::string, permissions::Store::Blocked> blocked) {
        lane::Store::instance().roleStates(
            [snapshot, blocked, respond](lane::Store::RoleStates states) {
                api::withDelivery(
                    [snapshot, blocked, states, respond](
                        std::shared_ptr<const delivery::Probe::Snapshot> probed,
                        api::LiveLanes live, api::Refusals refusals) {
                        core::Worker::instance().post(
                            [snapshot, blocked, probed, states, live, refusals, respond] {
                                catalog::Occupancy seats;
                                seats.occupied = snapshot->seatsUsed;
                                seats.total = snapshot->seatsTotal;
                                const catalog::Result body = catalog::renderHome(
                                    api::deliveryLookup(probed, live, refusals),
                                    api::laneLookup(snapshot, blocked),
                                    api::unpushedLookup(probed), seats,
                                    api::roleLookup(states));

                                http::Response resp;
                                resp.status = body.status;
                                resp.body = body.body;
                                respond(std::move(resp));
                            });
                    });
            });
            });
    });
}
