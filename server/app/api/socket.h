#pragma once

#include <chrono>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include <core/component.h>
#include <core/queue.h>
#include <core/task.h>
#include <core/ws.h>

#include "subscription.h"

namespace api {

class Connection : public std::enable_shared_from_this<Connection> {
public:
    using pointer = std::shared_ptr<Connection>;

    explicit Connection(ws::Connection::pointer transport);

    void start(std::string request);
    void send(const std::string& json);
    void close();

    void deliver(const std::string& scopeKey, const std::string& frame);
    void replaceTail(const std::string& lane, const std::string& data);
    bool subscribed(const std::string& scopeKey) const { return scopes_.count(scopeKey) > 0; }
    std::chrono::steady_clock::time_point lastHeard() const { return ws_->lastHeard(); }
    bool open() const { return ws_->open(); }

private:
    void dispatch_(const std::string& text);
    void subscribe_(const Scope& scope);
    void resume_(const std::string& lane, long long afterSeq);
    void snapshotReady_(const Scope& scope, const std::string& data,
                        bool replacesTail, long long seq);
    void fail_(const char* code, const std::string& message, const std::string& detail);

    ws::Connection::pointer ws_;

    std::set<std::string> scopes_;
    std::map<std::string, std::vector<std::string>> pending_;
};

class Socket : public core::Component {
    using CLASS = Socket;

public:
    static Socket& instance() { static Socket _instance; return _instance; }

    void init() override;
    void start() override;
    void stop() override;

    core::Queue& queue() { return queue_; }
    SequenceLog& log() { return log_; }

    void publishCatalogChange(const std::string& kind, const std::string& id,
                              const std::string& path, const std::string& change);

    void publishRun(const std::string& members,
                    const std::vector<std::string>& scopeKeys);

    void publishStage(const std::string& members,
                      const std::vector<std::string>& scopeKeys);

    void publishIsolation(const std::string& members,
                          const std::vector<std::string>& scopeKeys);

    void publishResource(const std::string& members,
                         const std::vector<std::string>& scopeKeys);

    void publishCommand(const std::string& members,
                        const std::vector<std::string>& scopeKeys);
    void publishDeliveryState(const std::string& ticketId, const std::string& deliveryJson,
                              bool needsYou);

    void publishLaneState(const std::string& lane, const std::string& key,
                          const std::string& members,
                          const std::vector<std::string>& scopeKeys);
    void forgetLaneState(const std::string& lane);

    void publishLaneMembership(const std::string& members,
                               const std::vector<std::string>& scopeKeys);

    void publishLaneMetrics(const std::string& members,
                            const std::vector<std::string>& scopeKeys);

    void publishLaneWords(const std::string& members,
                          const std::vector<std::string>& scopeKeys);

    void publishTranscript(const std::string& lane, const std::string& runId,
                           const std::string& entryJson,
                           std::function<void(long long)> done);

    void publishTranscriptAt(const std::string& lane, const std::string& runId, long long seq,
                             bool patch, const std::string& entryJson);

    void publishPermissionRequest(const std::string& members,
                                  const std::vector<std::string>& scopeKeys);

    void publishPermissionResolved(const std::string& members);
    void publishRules(const std::string& members);

    void deliver(const std::vector<std::string>& scopeKeys, const std::string& frame);
    void broadcast(const std::string& frame);
    size_t connectionCount() const { return connections_.size(); }

private:
    void sweep_();
    void tick_();
    void handOver_(const std::string& lane, const std::string& runId);

    std::set<Connection::pointer> connections_;
    std::map<std::string, std::string> laneState_;
    SequenceLog log_;
    core::Queue queue_;
    core::Task tickTask_ {queue_, [this] { tick_(); }};
    std::chrono::seconds idle_ {30};
    bool stopping_ = false;

    friend class Connection;
};

}
