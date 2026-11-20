#pragma once

#include <core/component.h>
#include <core/http.h>
#include <core/queue.h>
#include <app/routing/store.h>
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

COMPONENT(api, Manager)
    void init() override;
    void start() override;
    void stop() override;

    void reserveStart(const std::string& lane,
                      std::function<void(bool reserved, bool missing)> done);
    void releaseStart(const std::string& lane, std::function<void()> done = {});
    void stopStartPreflights();
    void runStartPreflight(const std::string& lane, const std::string& workspace,
                           const std::string& ticketId, const std::string& profile,
                           const std::string& model,
                           std::function<void(http::Response)> done);

    void routingSnapshot(const routing::Store::Context& context,
                         std::function<void(routing::Store::Snapshot)> done);

    HTTP_ASYNC_SERVICE(GET, "/api/health", Health)
    HTTP_SERVICE(GET, "/api/manifest", Manifest)
    HTTP_SERVICE(GET, "/api/slow", Slow)

    HTTP_ASYNC_SERVICE(GET, "/api/requirements", Requirements)
    HTTP_ASYNC_SERVICE(GET, "/api/requirements/{id}", Requirement)
    HTTP_ASYNC_SERVICE(GET, "/api/tickets", Tickets)
    HTTP_ASYNC_SERVICE(GET, "/api/tickets/{id}", Ticket)
    HTTP_ASYNC_SERVICE(GET, "/api/tickets/{id}/report", Report)

    HTTP_ASYNC_SERVICE(GET, "/api/home", Home)

    HTTP_ASYNC_SERVICE(GET, "/api/lanes", Lanes)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes", ClaimLane)
    HTTP_ASYNC_SERVICE(DELETE, "/api/lanes/{lane}", ReleaseLane)
    HTTP_ASYNC_SERVICE(DELETE, "/api/lanes/{lane}/{n}", ReleaseLaneSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/runs", StartRun)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/runs", StartRunSegments)

    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/commands", RunCommand)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/commands", RunCommandSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/routing", SubmitRouting)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/routing", SubmitRoutingSegments)
    HTTP_ASYNC_SERVICE(GET, "/api/lanes/{lane}/routing/{request_id}", RoutingState)
    HTTP_ASYNC_SERVICE(GET, "/api/lanes/{lane}/{n}/routing/{request_id}", RoutingStateSegments)
    HTTP_ASYNC_SERVICE(GET, "/api/lanes/{lane}/diff", LaneDiff)
    HTTP_ASYNC_SERVICE(DELETE, "/api/resources/{resource}/holder", ReleaseResource)
    HTTP_ASYNC_SERVICE(GET, "/api/lanes/{lane}/{n}/diff", LaneDiffSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/messages", SendMessage)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/messages", SendMessageSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/interrupt", InterruptRun)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/interrupt", InterruptRunSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/end", EndRun)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/end", EndRunSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/kill", KillRun)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/kill", KillRunSegments)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/recover", RecoverLane)
    HTTP_ASYNC_SERVICE(POST, "/api/lanes/{lane}/{n}/recover", RecoverLaneSegments)

    HTTP_ASYNC_SERVICE(GET, "/api/tickets/{id}/runs", TicketRuns)
    HTTP_ASYNC_SERVICE(GET, "/api/runs", RunIndex)
    HTTP_ASYNC_SERVICE(GET, "/api/metrics", Metrics)
    HTTP_SERVICE(GET, "/api/escapes", Escapes)
    HTTP_SERVICE(GET, "/api/wiki", Wiki)
    HTTP_ASYNC_SERVICE(GET, "/api/runs/{run_id}/transcript", RunTranscript)

    HTTP_ASYNC_SERVICE(GET, "/api/permissions/rules", PermissionRules)
    HTTP_ASYNC_SERVICE(DELETE, "/api/permissions/rules/{rule_id}", RevokeRule)
    HTTP_ASYNC_SERVICE(PUT, "/api/permissions/policy", SetPolicy)
    HTTP_ASYNC_SERVICE(POST, "/api/permissions/decisions", DecidePermission)

    HTTP_ASYNC_SERVICE(POST, "/api/progress", Progress)

    HTTP_ASYNC_SERVICE(POST, "/api/tickets/ids", AllocateTicketId)

    HTTP_ASYNC_SERVICE(POST, "/api/permission", PermissionAsk)

private:
    struct StartState;
    core::Queue startQueue_;
    std::atomic<bool> stoppingStarts_ {false};
    std::map<std::string, std::shared_ptr<StartState>> starts_;
    std::shared_ptr<const std::vector<std::shared_ptr<StartState>>> stopSnapshot_;
    void publishStartSnapshot_();
    void resolvePreflight_(const std::string& lane, const std::shared_ptr<StartState>& state);
    void writePreflightCapture_(const std::string& lane,
                                const std::shared_ptr<StartState>& state);
    void registerTestHook_();
    void registerLaneHooks_();
    void registerForgetLaneHook_();
    void registerFailSpawnHook_();
    void wireAgent_();
    void wirePermissions_();
    void wireLaneState_();
    void registerClockHook_();
    void registerPermissionHook_();
    void registerPermissionSubscriberHook_();
COMPONENT_END
