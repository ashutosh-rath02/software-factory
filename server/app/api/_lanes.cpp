#include "manager.h"

#include <map>
#include <memory>

#include <core/worker.h>
#include <app/catalog/store.h>
#include <app/delivery/probe.h>
#include <app/lane/store.h>
#include <app/permissions/store.h>

#include "lane-view.h"

HTTP_ASYNC_HANDLER(api::Manager, Lanes)
{
    (void)req;
    lane::Store::instance().list([respond](lane::Store::Outcome out) {
        auto snapshot = std::make_shared<lane::Store::Snapshot>(std::move(out.snapshot));
        permissions::Store::instance().blockedSnapshot(
            [snapshot, respond](std::map<std::string, permissions::Store::Blocked> blocked) {
        delivery::Probe::instance().snapshotThen(
            [snapshot, respond, blocked](
                std::shared_ptr<const delivery::Probe::Snapshot> dsnap) {
                core::Worker::instance().post(
                    [snapshot, blocked, dsnap, respond] {
                        auto live = std::make_shared<delivery::Probe::LiveLanes>(
                            api::liveLanes(*snapshot));

                        http::Response resp;
                        resp.status = 200;
                        resp.body = lane::renderLanes(*snapshot,
                                                      api::laneProgress(snapshot->stages),
                                                      api::laneEvidence(),
                                                      api::laneBlocked(blocked),
                                                      api::laneDelivery(dsnap, live));
                        respond(std::move(resp));
                    });
            });
            });
    });
}
