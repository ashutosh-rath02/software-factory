#include "manager.h"

#include "delivery.h"
#include <core/engine.h>
#include <app/manifest/store.h>

HTTP_ASYNC_HANDLER(api::Manager, Health)
{
    (void)req;
    delivery::Probe::instance().snapshotThen(
        [respond](std::shared_ptr<const delivery::Probe::Snapshot> snapshot) {
            http::Response resp;
            resp.body = manifest::Store::instance().healthJson(
                core::Engine::instance().uptimeSeconds(),
                delivery::Probe::healthFragment(snapshot.get()));
            respond(std::move(resp));
        });
}
