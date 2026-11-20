#include <app/api/socket.h>

#include <algorithm>
#include <sstream>

#include <config.h>
#include <core/engine.h>
#include <core/http.h>
#include <core/worker.h>
#include <app/api/delivery.h>
#include <app/catalog/render.h>
#include <app/manifest/json.h>
#include <glog/logging.h>


namespace api {

namespace {

std::string frame(const char* name, const std::string& seq, const std::string& payload)
{
    std::ostringstream o;
    o << "{\"m\":\"" << name << "\",\"ts\":\"" << nowIso() << "\",\"seq\":" << seq;
    if (!payload.empty()) o << "," << payload;
    o << "}";
    return o.str();
}

using manifest::jstr;
long long integerOr(const manifest::Json* v, long long fallback)
{
    return (v && v->isInteger()) ? static_cast<long long>(v->number()) : fallback;
}

std::string stampedEntry(const std::string& entryJson, long long seq)
{
    const std::size_t open = entryJson.find('{');
    if (open == std::string::npos) return entryJson;
    const std::string head = "{\"seq\":" + std::to_string(seq) + ",\"ts\":\"" + nowIso() + "\"";
    const std::size_t rest = entryJson.find_first_not_of(" \t\r\n", open + 1);
    if (rest == std::string::npos || entryJson[rest] == '}') return head + "}";
    return head + "," + entryJson.substr(rest);
}

}

Connection::Connection(ws::Connection::pointer transport) : ws_(std::move(transport)) {}

void Connection::start(std::string request)
{
    auto self = shared_from_this();
    ws_->start(std::move(request),
        [this, self] {
            send(frame("hello", "null", "\"factory\":" + jstr(Config::FACTORY_VERSION)));
        },
        [this, self](const std::string& text) { dispatch_(text); });
}


void Connection::dispatch_(const std::string& text)
{
    manifest::Json::Error err;
    const manifest::Json doc = manifest::Json::parse(text, err);
    if (!err.ok || !doc.isObject())
        return fail_("bad_frame", "the frame is not a JSON object", {});

    const manifest::Json* name = doc.find("m");
    if (!name || !name->isString())
        return fail_("bad_frame", "the frame carries no message name", {});

    const std::string& m = name->str();
    if (m == "ping") {
        send(frame("pong", "null", {}));
    } else if (m == "subscribe") {
        const manifest::Json* scopeJson = doc.find("scope");
        if (!scopeJson || !scopeJson->isObject())
            return fail_("bad_frame", "subscribe carries no scope object", {});
        const Scope scope = parseScope(scopeJson);
        if (!scope.valid())
            return fail_("unknown_scope",
                         "a scope is one of requirements, ticket or lane",
                         "\"detail\":{\"named\":" + jstr(
                             scopeJson->members().empty() ? std::string()
                                                          : scopeJson->members().front().first)
                             + "}");
        subscribe_(scope);
    } else if (m == "resume") {
        const manifest::Json* lane = doc.find("lane");
        const manifest::Json* after = doc.find("after_seq");
        if (!lane || !lane->isString() || lane->str().empty() || !after || !after->isInteger()
            || integerOr(after, -1) < 0)
            return fail_("bad_frame", "resume needs a lane and an after_seq of at least 0", {});
        resume_(lane->str(), integerOr(after, 0));
    } else {
        fail_("unknown_message", "no such message", "\"detail\":{\"m\":" + jstr(m) + "}");
    }
}

void Connection::fail_(const char* code, const std::string& message, const std::string& detail)
{
    std::string payload = "\"code\":" + jstr(code) + ",\"message\":" + jstr(message);
    if (!detail.empty()) payload += "," + detail;
    send(frame("error", "null", payload));
}

void Connection::subscribe_(const Scope& scope)
{
    const std::string key = scope.key();
    scopes_.insert(key);
    pending_[key];

    if (scope.kind == Scope::Kind::Lane) {
        auto& log = Socket::instance().log();
        snapshotReady_(scope, log.snapshotData(scope.id), false, log.lastSeq(scope.id));
        return;
    }

    auto self = shared_from_this();
    renderWithDeliveryAndAuthors(
        [scope](const catalog::DeliveryLookup& look, const catalog::AuthorLookup& authors) {
            return scope.kind == Scope::Kind::Ticket
                       ? catalog::renderTicket(scope.id, look, {}, {}, {}, authors)
                       : catalog::renderRequirements({}, {}, look);
        },
        [self, scope](http::Response resp) {
            Socket::instance().queue().post([self, scope, body = resp.body] {
                self->snapshotReady_(scope, body, false, -1);
            });
        });
}

void Connection::resume_(const std::string& lane, long long afterSeq)
{
    Scope scope;
    scope.kind = Scope::Kind::Lane;
    scope.id = lane;
    scopes_.insert(scope.key());
    pending_.erase(scope.key());

    auto& log = Socket::instance().log();
    const auto decision = log.resume(lane, afterSeq);
    if (!decision.coverable) {
        snapshotReady_(scope, log.snapshotData(lane), true, log.lastSeq(lane));
        return;
    }
    for (const auto& f : decision.frames) send(f);
}

void Connection::snapshotReady_(const Scope& scope, const std::string& data,
                                bool replacesTail, long long seq)
{
    std::ostringstream payload;
    payload << "\"scope\":" << scope.json() << ",\"data\":" << (data.empty() ? "{}" : data);
    if (replacesTail) payload << ",\"replaces_tail\":true";

    const std::string seqText = (scope.kind == Scope::Kind::Lane && seq > 0)
                                    ? std::to_string(seq) : std::string("null");
    send(frame("snapshot", seqText, payload.str()));

    const std::string key = scope.key();
    auto held = pending_.find(key);
    if (held == pending_.end()) return;
    for (const auto& f : held->second) send(f);
    pending_.erase(held);
}

void Connection::replaceTail(const std::string& lane, const std::string& data)
{
    Scope scope;
    scope.kind = Scope::Kind::Lane;
    scope.id = lane;
    snapshotReady_(scope, data, true, 0);
}

void Connection::deliver(const std::string& scopeKey, const std::string& f)
{
    auto held = pending_.find(scopeKey);
    if (held != pending_.end()) { held->second.push_back(f); return; }
    send(f);
}

void Connection::send(const std::string& json) { ws_->send(json); }


void Connection::close() { ws_->close(); }

void Socket::init()
{
    log_.setCapacity(static_cast<std::size_t>(Config::TRANSCRIPT_LOG));
    idle_ = std::chrono::seconds(Config::WS_IDLE_S);

    ws::serve("/ws", queue_, "factory",
        [this](ws::Connection::pointer transport, std::string request) {
            auto conn = std::make_shared<Connection>(std::move(transport));
            queue_.post([this, conn, request = std::move(request)]() mutable {
                connections_.insert(conn);
                conn->start(std::move(request));
            });
        });
}

void Socket::start()
{
    LOG(INFO) << "socket at /ws on :" << Config::HTTP_PORT
              << " (idle " << idle_.count() << "s, log " << Config::TRANSCRIPT_LOG << ")";
    sweep_();
}

void Socket::sweep_()
{
    if (stopping_) return;
    const auto period = std::max(std::chrono::seconds(1), idle_ / 3);
    tickTask_.start(static_cast<uint32_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(period).count()), 1);
}

void Socket::tick_()
{
    if (stopping_) return;
    const auto now = std::chrono::steady_clock::now();
    for (auto it = connections_.begin(); it != connections_.end();) {
        const auto& c = *it;
        if (!c->open() || now - c->lastHeard() > idle_) {
            c->close();
            it = connections_.erase(it);
        } else {
            ++it;
        }
    }
    sweep_();
}

void Socket::stop()
{
    queue_.post([this] {
        stopping_ = true;
        tickTask_.stop();
        for (auto& c : connections_) c->close();
        connections_.clear();
    });
}

void Socket::deliver(const std::vector<std::string>& scopeKeys, const std::string& f)
{
    queue_.post([this, scopeKeys, f] {
        for (auto& c : connections_)
            for (const auto& key : scopeKeys)
                if (c->subscribed(key)) { c->deliver(key, f); break; }
    });
}

void Socket::broadcast(const std::string& f)
{
    queue_.post([this, f] {
        for (auto& c : connections_) c->send(f);
    });
}

void Socket::publishPermissionRequest(const std::string& members,
                                      const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("permission.request", "null", members));
}

void Socket::publishPermissionResolved(const std::string& members)
{
    broadcast(frame("permission.resolved", "null", members));
}

void Socket::publishRules(const std::string& members)
{
    broadcast(frame("rules", "null", members));
}

void Socket::publishCatalogChange(const std::string& kind, const std::string& id,
                                  const std::string& path, const std::string& change)
{
    std::ostringstream payload;
    payload << "\"kind\":" << jstr(kind) << ",\"id\":" << jstr(id)
            << ",\"path\":" << jstr(path) << ",\"change\":" << jstr(change);
    deliver(catalogChangeScopes(kind, id), frame("catalog.changed", "null", payload.str()));
}

void Socket::publishResource(const std::string& members,
                             const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("resource", "null", members));
}

void Socket::publishDeliveryState(const std::string& ticketId, const std::string& deliveryJson,
                                  bool needsYou)
{
    std::ostringstream payload;
    payload << "\"lane\":null,\"ticket_id\":" << jstr(ticketId)
            << ",\"delivery\":" << deliveryJson
            << ",\"needs_you\":" << (needsYou ? "true" : "false");
    deliver(catalogChangeScopes("ticket", ticketId),
            frame("lane.state", "null", payload.str()));
}

void Socket::publishLaneState(const std::string& lane, const std::string& key,
                              const std::string& members,
                              const std::vector<std::string>& scopeKeys)
{
    queue_.post([this, lane, key, members, scopeKeys] {
        auto last = laneState_.find(lane);
        if (last != laneState_.end() && last->second == key) return;
        laneState_[lane] = key;
        const std::string f = frame("lane.state", "null", members);
        for (auto& c : connections_)
            for (const auto& scope : scopeKeys)
                if (c->subscribed(scope)) { c->deliver(scope, f); break; }
    });
}

void Socket::forgetLaneState(const std::string& lane)
{
    queue_.post([this, lane] { laneState_.erase(lane); });
}

void Socket::publishLaneMembership(const std::string& members,
                                   const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("lane.membership", "null", members));
}

void Socket::publishLaneMetrics(const std::string& members,
                                const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("lane.metrics", "null", members));
}

void Socket::publishLaneWords(const std::string& members,
                             const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("lane.words", "null", members));
}

void Socket::publishCommand(const std::string& members,
                            const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("command", "null", members));
}

void Socket::publishRun(const std::string& members,
                        const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("run", "null", members));
}

void Socket::publishStage(const std::string& members,
                          const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("stage", "null", members));
}

void Socket::publishIsolation(const std::string& members,
                              const std::vector<std::string>& scopeKeys)
{
    deliver(scopeKeys, frame("isolation", "null", members));
}

void Socket::handOver_(const std::string& lane, const std::string& runId)
{
    if (!log_.beginRun(lane, runId)) return;
    const std::string key = "lane:" + lane;
    const std::string data = log_.snapshotData(lane);
    for (auto& c : connections_)
        if (c->subscribed(key)) c->replaceTail(lane, data);
}

void Socket::publishTranscript(const std::string& lane, const std::string& runId,
                               const std::string& entryJson,
                               std::function<void(long long)> done)
{
    queue_.post([this, lane, runId, entryJson, done = std::move(done)] {
        handOver_(lane, runId);
        std::string sent;
        const long long seq = log_.append(lane, runId, [&](long long s) {
            SequenceLog::Rendered out;
            out.entry = stampedEntry(entryJson, s);
            std::ostringstream payload;
            payload << "\"lane\":" << jstr(lane) << ",\"run_id\":" << jstr(runId)
                    << ",\"entry\":" << out.entry;
            sent = out.frame = frame("transcript", std::to_string(s), payload.str());
            return out;
        });
        const std::string key = "lane:" + lane;
        for (auto& c : connections_)
            if (c->subscribed(key)) c->deliver(key, sent);
        if (done) done(seq);
    });
}

void Socket::publishTranscriptAt(const std::string& lane, const std::string& runId, long long seq,
                                 bool patch, const std::string& entryJson)
{
    queue_.post([this, lane, runId, seq, patch, entryJson] {
        handOver_(lane, runId);
        std::string sent;
        log_.appendAt(lane, runId, seq, [&](long long s) {
            SequenceLog::Rendered out;
            out.entry = entryJson;
            std::ostringstream payload;
            payload << "\"lane\":" << jstr(lane) << ",\"run_id\":" << jstr(runId)
                    << ",\"entry\":" << out.entry;
            if (patch) payload << ",\"patch\":true";
            sent = out.frame = frame("transcript", std::to_string(s), payload.str());
            return out;
        });
        const std::string key = "lane:" + lane;
        for (auto& c : connections_)
            if (c->subscribed(key)) c->deliver(key, sent);
    });
}

}
