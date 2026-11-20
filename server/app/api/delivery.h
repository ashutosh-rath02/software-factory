#pragma once

#include <functional>
#include <memory>

#include <core/http.h>

#include <app/agent/session.h>
#include <app/api/lane-view.h>
#include <app/catalog/render.h>
#include <app/delivery/probe.h>
#include <app/land/lander.h>

namespace api {

using Refusals = std::shared_ptr<const land::Lander::Refusals>;

catalog::DeliveryLookup deliveryLookup(std::shared_ptr<const delivery::Probe::Snapshot> snapshot,
                                       LiveLanes live, Refusals refusals = nullptr);

catalog::UnpushedLookup unpushedLookup(std::shared_ptr<const delivery::Probe::Snapshot> snapshot);

void withDelivery(std::function<void(std::shared_ptr<const delivery::Probe::Snapshot>,
                                     LiveLanes, Refusals)> done);
catalog::AuthorLookup authorLookup(std::shared_ptr<const delivery::Probe::Snapshot> snapshot);

void renderWithDelivery(std::function<catalog::Result(const catalog::DeliveryLookup&)> render,
                        http::Responder respond);

void renderWithDeliveryAndAuthors(
    std::function<catalog::Result(const catalog::DeliveryLookup&,
                                  const catalog::AuthorLookup&)> render,
    http::Responder respond);

void watchDelivery();

void recordTheRefTheSessionLeft(const std::string& lane, const std::string& runId);

void publishDeliveryStates();

}
