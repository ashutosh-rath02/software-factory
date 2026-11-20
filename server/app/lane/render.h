#pragma once

#include <functional>
#include <string>

#include "state.h"
#include "store.h"

namespace lane {

using EvidenceFn = std::function<std::string(const Store::Record&)>;

using BlockedFn = std::function<bool(const Store::Record&, std::string& reason, std::string& requestId)>;

using DeliveryFn = std::function<std::string(const Store::Record&)>;

using ProgressFn = std::function<progress::Shown(const std::string& ticketId)>;

std::string renderLane(const Store::Record& record, const progress::Shown& shown,
                       const EvidenceFn& evidence,
                       const BlockedFn& blocked = nullptr, const DeliveryFn& delivery = nullptr);

state::Axes axesFor(const Store::Record& record, const EvidenceFn& evidence,
                    const BlockedFn& blocked = nullptr);

struct StateFrame {
    std::string members;
    std::string key;
};

StateFrame renderLaneState(const Store::Record& record, const EvidenceFn& evidence,
                           const BlockedFn& blocked, const DeliveryFn& delivery = nullptr);
std::string renderLanes(const Store::Snapshot& snapshot, const ProgressFn& progress,
                        const EvidenceFn& evidence,
                        const BlockedFn& blocked = nullptr, const DeliveryFn& delivery = nullptr);
std::string renderRelease(const Store::Outcome& outcome);

std::string renderRecover(const Store::Outcome& outcome);
std::string renderWords(const Store::Words& words);
std::string renderResource(const Store::ResourceView& view);
std::string renderIsolation(const std::string& lane,
                            const confine::Refusal& refusal);
std::string renderError(const Store::Outcome& outcome);

}
