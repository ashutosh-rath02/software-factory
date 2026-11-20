#include "subscription.h"

#include <chrono>
#include <ctime>
#include <sstream>

#include <app/manifest/json.h>

namespace api {

std::string nowIso()
{
    const std::time_t t = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm tm {};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

std::string Scope::key() const
{
    switch (kind) {
    case Kind::Requirements: return "requirements";
    case Kind::Ticket:       return "ticket:" + id;
    case Kind::Lane:         return "lane:" + id;
    default:                 return {};
    }
}

std::string Scope::json() const
{
    switch (kind) {
    case Kind::Requirements: return "{\"requirements\":true}";
    case Kind::Ticket:       return "{\"ticket\":\"" + manifest::jsonEscape(id) + "\"}";
    case Kind::Lane:         return "{\"lane\":\"" + manifest::jsonEscape(id) + "\"}";
    default:                 return "null";
    }
}

Scope parseScope(const manifest::Json* scope)
{
    Scope out;
    if (!scope || !scope->isObject() || scope->members().size() != 1) return out;

    const auto& [name, value] = scope->members().front();
    if (name == "requirements" && value.isBool() && value.boolean()) {
        out.kind = Scope::Kind::Requirements;
    } else if (name == "ticket" && value.isString() && !value.str().empty()) {
        out.kind = Scope::Kind::Ticket;
        out.id = value.str();
    } else if (name == "lane" && value.isString() && !value.str().empty()) {
        out.kind = Scope::Kind::Lane;
        out.id = value.str();
    }
    return out;
}

std::vector<std::string> catalogChangeScopes(const std::string& kind, const std::string& id)
{
    if (kind == "ticket" || kind == "report") return {"requirements", "ticket:" + id};
    return {"requirements"};
}

std::vector<std::string> resourceScopes(const std::vector<std::string>& lanes)
{
    std::vector<std::string> out {"requirements"};
    for (const auto& name : lanes) out.push_back("lane:" + name);
    return out;
}

std::vector<std::string> isolationScopes(const std::string& lane)
{
    return {"requirements", "lane:" + lane};
}

std::vector<std::string> commandScopes(const std::string& lane, const std::string& ticketId)
{
    std::vector<std::string> out {"lane:" + lane};
    if (!ticketId.empty()) out.push_back("ticket:" + ticketId);
    return out;
}

bool SequenceLog::beginRun(const std::string& lane, const std::string& runId)
{
    if (runId.empty()) return false;

    Lane& l = lanes_[lane];
    if (l.runId == runId) return false;
    const bool replaces = !l.runId.empty();
    l.runId = runId;
    if (!replaces) return false;

    l.frames.clear();
    l.last = 0;
    l.order = 0;
    return true;
}

long long SequenceLog::append(const std::string& lane, const std::string& runId,
                              const std::function<Rendered(long long)>& render)
{
    Lane& l = lanes_[lane];
    l.runId = runId;
    const long long seq = ++l.last;
    l.frames.push_back({seq, ++l.order, render(seq)});
    while (l.frames.size() > capacity_) l.frames.pop_front();
    return seq;
}

void SequenceLog::appendAt(const std::string& lane, const std::string& runId, long long seq,
                           const std::function<Rendered(long long)>& render)
{
    if (seq <= 0) return;
    Lane& l = lanes_[lane];
    l.runId = runId;
    if (seq > l.last) l.last = seq;
    l.frames.push_back({seq, ++l.order, render(seq)});
    while (l.frames.size() > capacity_) l.frames.pop_front();
}

long long SequenceLog::lastSeq(const std::string& lane) const
{
    auto it = lanes_.find(lane);
    return it == lanes_.end() ? 0 : it->second.last;
}

SequenceLog::Resume SequenceLog::resume(const std::string& lane, long long afterSeq) const
{
    Resume out;
    auto it = lanes_.find(lane);
    if (it == lanes_.end()) {
        out.coverable = (afterSeq == 0);
        return out;
    }

    const Lane& l = it->second;
    out.lastSeq = l.last;
    const long long oldest = l.frames.empty() ? l.last + 1 : l.frames.front().seq;
    if (afterSeq > l.last || afterSeq < oldest - 1) return out;

    out.coverable = true;
    long long cut = 0;
    for (const auto& f : l.frames)
        if (f.seq <= afterSeq && f.order > cut) cut = f.order;
    for (const auto& f : l.frames)
        if (f.order > cut) out.frames.push_back(f.rendered.frame);
    return out;
}

std::string SequenceLog::snapshotData(const std::string& lane) const
{
    auto it = lanes_.find(lane);
    std::ostringstream o;
    o << "{\"lane\":\"" << manifest::jsonEscape(lane) << "\"";
    if (it == lanes_.end()) {
        o << ",\"run_id\":null,\"entries\":[],\"next_after_seq\":0,\"complete\":true}";
        return o.str();
    }

    const Lane& l = it->second;
    o << ",\"run_id\":" << (l.runId.empty() ? std::string("null")
                                            : "\"" + manifest::jsonEscape(l.runId) + "\"")
      << ",\"entries\":[";
    bool first = true;
    for (const auto& f : l.frames) {
        if (!first) o << ",";
        first = false;
        o << f.rendered.entry;
    }
    const long long oldest = l.frames.empty() ? l.last + 1 : l.frames.front().seq;
    o << "],\"next_after_seq\":" << l.last
      << ",\"complete\":" << (oldest <= 1 ? "true" : "false") << "}";
    return o.str();
}

}

std::vector<std::string> api::runScopes(const std::string& lane, const std::string& ticketId)
{
    std::vector<std::string> keys {Scope{Scope::Kind::Requirements, {}}.key()};
    if (!lane.empty()) keys.push_back(Scope{Scope::Kind::Lane, lane}.key());
    if (!ticketId.empty()) keys.push_back(Scope{Scope::Kind::Ticket, ticketId}.key());
    return keys;
}

std::vector<std::string> api::laneStateScopes(const std::string& lane,
                                              const std::string& ticketId)
{
    return runScopes(lane, ticketId);
}

std::vector<std::string> api::permissionScopes(const std::string& lane,
                                               const std::string& ticketId)
{
    return runScopes(lane, ticketId);
}
