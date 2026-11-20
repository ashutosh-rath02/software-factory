#pragma once

#include <map>
#include <memory>
#include <string>

#include <core/http.h>
#include <app/catalog/render.h>
#include <app/delivery/probe.h>
#include <app/lane/render.h>
#include <app/lane/store.h>
#include <app/permissions/store.h>

namespace api {

lane::EvidenceFn laneEvidence();

using LiveLanes = std::shared_ptr<delivery::Probe::LiveLanes>;

delivery::Probe::LiveLanes liveLanes(const lane::Store::Snapshot& lanes);

lane::DeliveryFn laneDelivery(std::shared_ptr<const delivery::Probe::Snapshot> snapshot,
                              LiveLanes live);

lane::BlockedFn laneBlocked(const std::map<std::string, permissions::Store::Blocked>& blocked);

catalog::StageLookup stageLookup(const lane::Store::StageHistory& history);

lane::ProgressFn laneProgress(const lane::Store::StageHistory& history);

std::string stageMembers(const std::string& lane, const std::string& ticketId,
                         const lane::progress::Shown& shown);

void publishStagesOf(const std::string& ticketId);

catalog::LaneLookup laneLookup(std::shared_ptr<const lane::Store::Snapshot> snapshot,
                               const std::map<std::string, permissions::Store::Blocked>& blocked);

void publishLaneState(const std::string& lane);
void publishAllLaneStates();

void publishLaneMembership(const std::string& lane, const std::string& ticketId,
                           const std::string& workspace, bool claimed);

void publishLaneMetrics(const std::string& lane, int turnN, double turnCostUsd, double costUsd);

void publishLaneWords(const std::string& lane, const std::string& ticketId,
                      const lane::Store::Words& words);

std::string laneNameOf(const http::Request& req);

std::string queryValue(const http::Request& req, const std::string& name);

std::string runKind(const std::string& role);
bool isRoleRun(const std::string& role);

}
