#include "render.h"

#include <chrono>
#include <sstream>

#include <app/error/remedy.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>

#include "git.h"
#include "state.h"

using manifest::jsonEscape;

namespace {

using manifest::jstr;
std::string jstrOrNull(const std::string& s) { return s.empty() ? "null" : jstr(s); }

void writeStrings(std::ostringstream& o, const std::vector<std::string>& v)
{
    o << "[";
    for (std::size_t i = 0; i < v.size(); ++i) {
        if (i) o << ",";
        o << jstr(v[i]);
    }
    o << "]";
}

struct Derived {
    lane::state::Axes axes;
    bool vanished = false;
    bool blocked = false;
    std::string reason;
    std::string requestId;
};

Derived derive(const lane::Store::Record& record, const lane::EvidenceFn& evidence,
               const lane::BlockedFn& blockedBy, const lane::DeliveryFn& delivery)
{
    Derived out;
    out.vanished = lane::git::presenceOf(record.worktree) == lane::git::Presence::Absent;
    out.blocked = record.pid > 0 && !out.vanished && blockedBy
               && blockedBy(record, out.reason, out.requestId);
    const bool goalBlocked = record.pid > 0 && !out.vanished
        && (record.goalStatus == "blocked" || record.goalStatus == "budgetLimited"
            || record.goalStatus == "usageLimited");
    if (goalBlocked && !out.blocked) {
        out.blocked = true;
        out.reason = record.goalStatus;
        out.requestId.clear();
    }

    lane::state::Inputs in;
    in.worktreeMissing = out.vanished;
    in.queued = record.queued;
    in.live = record.pid > 0;
    in.blocked = out.blocked;
    in.turnActive = record.turnActive;
    in.turnEnded = record.turnEnded;
    in.outcome = record.outcome;
    in.lastOutputMs = record.lastOutputMs;
    in.evidence = evidence ? evidence(record) : std::string("nothing_claimed");
    in.delivery = delivery ? delivery(record) : std::string();
    in.now = lane::state::nowMs();
    out.axes = lane::state::axesOf(in);
    if (goalBlocked) out.axes.needsYou = true;
    return out;
}

void writeStopped(std::ostringstream& o, const Derived& d);

void writeState(std::ostringstream& o, const Derived& d, bool withSilentFor)
{
    o << "{\"process\":" << jstr(d.axes.process) << ",\"motion\":{\"value\":"
      << (d.axes.motion.value.empty() ? "null" : jstr(d.axes.motion.value))
      << ",\"silent_for_s\":" << (withSilentFor ? d.axes.motion.silentForS : 0)
      << ",\"since\":"
      << (d.axes.motion.sinceMs > 0 ? jstr(lane::state::isoOf(d.axes.motion.sinceMs)) : "null")
      << "},\"evidence\":" << jstr(d.axes.evidence)
      << ",\"stopped\":";
    writeStopped(o, d);
    if (d.blocked) {
        o << ",\"blocked\":{\"reason\":" << jstr(d.reason)
          << ",\"request_id\":" << jstr(d.requestId) << "}";
    } else {
        o << ",\"blocked\":null";
    }
    o << ",\"alarm\":" << (d.axes.alarm ? "true" : "false") << "}";
}

void writeStopped(std::ostringstream& o, const Derived& d)
{
    if (!d.axes.stopped.present) { o << "null"; return; }
    o << "{\"reason\":" << jstr(d.axes.stopped.reason)
      << ",\"seconds\":" << d.axes.stopped.seconds
      << ",\"since\":"
      << (d.axes.stopped.sinceMs > 0 ? jstr(lane::state::isoOf(d.axes.stopped.sinceMs)) : "null")
      << "}";
}

void writeWaitingFor(std::ostringstream& o, const lane::Store::Record& record)
{
    if (record.queued && !record.waiting.resource.empty()) {
        o << "{\"resource\":" << jstr(record.waiting.resource)
          << ",\"held_by\":" << jstrOrNull(record.waiting.heldBy)
          << ",\"holder_alive\":"
          << (record.waiting.heldBy.empty()
                  ? "null"
                  : (lane::git::isRunning(record.waiting.holderPid) ? "true" : "false"))
          << ",\"position\":" << record.waiting.position << "}";
    } else {
        o << "null";
    }
}

}

lane::state::Axes lane::axesFor(const Store::Record& record, const EvidenceFn& evidence,
                                const BlockedFn& blocked)
{
    return derive(record, evidence, blocked, nullptr).axes;
}

lane::StateFrame lane::renderLaneState(const Store::Record& record, const EvidenceFn& evidence,
                                       const BlockedFn& blockedBy, const DeliveryFn& delivery)
{
    const Derived d = derive(record, evidence, blockedBy, delivery);

    StateFrame frame;
    for (const bool withSilentFor : {true, false}) {
        std::ostringstream o;
        o << "\"lane\":" << jstr(record.name)
          << ",\"ticket_id\":" << jstr(record.ticketId)
          << ",\"state\":";
        writeState(o, d, withSilentFor);
        o << ",\"waiting_for\":";
        writeWaitingFor(o, record);
        o << ",\"needs_you\":" << (d.axes.needsYou ? "true" : "false");
        (withSilentFor ? frame.members : frame.key) = o.str();
    }
    return frame;
}

std::string lane::renderLane(const Store::Record& record, const progress::Shown& shown,
                             const EvidenceFn& evidence,
                             const BlockedFn& blockedBy, const DeliveryFn& delivery)
{
    const Derived d = derive(record, evidence, blockedBy, delivery);
    const bool vanished = d.vanished;

    const long long elapsed =
        record.claimedAtMs > 0 ? (state::nowMs() - record.claimedAtMs) / 1000 : 0;

    std::ostringstream o;
    o << "{\"lane\":" << jstr(record.name)
      << ",\"ticket_id\":" << jstr(record.ticketId)
      << ",\"workspace\":" << jstr(record.workspace)
      << ",\"role\":" << jstr(record.role)
      << ",\"branch\":" << jstr(record.branch)
      << ",\"worktree\":" << jstr(record.worktree)
      << ",\"state\":";
    writeState(o, d, true);
    o << ",\"needs_you\":" << (d.axes.needsYou ? "true" : "false");

    if (vanished) {
        o << ",\"detail\":{\"reason\":\"worktree_missing\",\"path\":" << jstr(record.worktree)
          << "}";
    } else {
        o << ",\"detail\":null";
    }

    o << ",\"stage\":{\"index\":" << shown.stage.index << ",\"total\":" << shown.stage.total
      << ",\"round\":" << shown.stage.round << "}"
      << ",\"progress_pct\":" << shown.pct
      << ",\"remaining_s\":";
    if (shown.remainingS < 0) o << "null"; else o << shown.remainingS;
    o << ",\"elapsed_s\":" << elapsed
      << ",\"cost_usd\":" << record.costUsd
      << ",\"last_words\":" << (record.hasWords ? renderWords(record.lastWords) : "null")
      << ",\"run_id\":" << jstrOrNull(record.runId)
      << ",\"session_id\":" << jstrOrNull(record.sessionId)
      << ",\"resources_held\":";
    writeStrings(o, record.resourcesHeld);

    o << ",\"waiting_for\":";
    writeWaitingFor(o, record);

    o << "}";
    return o.str();
}

std::string lane::renderRecover(const Store::Outcome& outcome)
{
    std::ostringstream o;
    o << "{\"lane\":" << jstr(outcome.lane.name)
      << ",\"recovered\":" << (outcome.recovered ? "true" : "false")
      << ",\"session_id\":" << jstr(outcome.lane.sessionId)
      << ",\"killed\":" << (outcome.killed ? "true" : "false")
      << ",\"destroys\":[]"
      << ",\"detail\":{\"re_carried\":[";
    for (std::size_t i = 0; i < outcome.reCarried.size(); ++i)
        o << (i ? "," : "") << jstr(outcome.reCarried[i]);
    o << "]";
    if (outcome.killed) o << ",\"killed_pid\":" << outcome.killedPid;
    o << "},\"seats\":{\"host\":{\"total\":" << outcome.snapshot.seatsTotal
      << ",\"used\":" << outcome.snapshot.seatsUsed << "}}}";
    return o.str();
}

std::string lane::renderWords(const Store::Words& words)
{
    std::ostringstream o;
    o << "{\"kind\":" << jstr(words.kind)
      << ",\"text\":" << jstr(words.text)
      << ",\"at\":" << jstr(words.at) << "}";
    return o.str();
}

std::string lane::renderResource(const Store::ResourceView& view)
{
    std::ostringstream o;
    o << "\"name\":" << jstr(view.name)
      << ",\"exclusive\":" << (view.exclusive ? "true" : "false")
      << ",\"description\":" << (view.hasDescription ? jstr(view.description) : "null")
      << ",\"held_by\":" << jstrOrNull(view.heldBy)
      << ",\"queue\":";
    writeStrings(o, view.queue);
    return o.str();
}

std::string lane::renderIsolation(const std::string& name, const confine::Refusal& refusal)
{
    std::ostringstream o;
    o << "\"lane\":" << jstr(name)
      << ",\"operation\":" << jstr(refusal.operation)
      << ",\"path\":" << jstr(refusal.path)
      << ",\"refused\":true";
    return o.str();
}

std::string lane::renderLanes(const Store::Snapshot& snapshot, const ProgressFn& progress,
                              const EvidenceFn& evidence,
                              const BlockedFn& blocked, const DeliveryFn& delivery)
{
    std::ostringstream o;
    o << "{\"lanes\":[";
    for (std::size_t i = 0; i < snapshot.lanes.size(); ++i) {
        if (i) o << ",";
        o << renderLane(snapshot.lanes[i], progress(snapshot.lanes[i].ticketId), evidence, blocked,
                        delivery);
    }
    o << "],\"resources\":[";
    for (std::size_t i = 0; i < snapshot.resources.size(); ++i) {
        if (i) o << ",";
        const Store::ResourceView& view = snapshot.resources[i];
        o << "{" << renderResource(view) << ",\"holder_alive\":"
          << (view.heldBy.empty() ? "null" : (git::isRunning(view.holderPid) ? "true" : "false"))
          << "}";
    }
    o << "],\"seats\":{\"host\":{\"total\":" << snapshot.seatsTotal
      << ",\"used\":" << snapshot.seatsUsed << "}}";
    const auto& m = manifest::Store::instance().data();
    const bool refused = m.state != manifest::Store::State::Ok;
    if (refused || !snapshot.warnings.empty()) {
        o << ",\"warnings\":[";
        if (refused) {
            o << "{\"code\":\"invalid_manifest\",\"message\":" << jstr(m.errorMessage);
            if (m.hasErrorPointer) o << ",\"pointer\":" << jstr(m.errorPointer);
            if (m.hasErrorPath)    o << ",\"path\":" << jstr(m.errorPath);
            if (m.hasErrorField)   o << ",\"field\":" << jstr(m.errorField);
            o << "}";
        }
        for (std::size_t i = 0; i < snapshot.warnings.size(); ++i) {
            const Store::Warning& w = snapshot.warnings[i];
            o << ((refused || i) ? "," : "")
              << "{\"code\":" << jstr(w.code)
              << ",\"message\":" << jstr(w.message)
              << ",\"path\":" << jstr(w.path) << "}";
        }
        o << "]";
    }
    o << "}";
    return o.str();
}

std::string lane::renderRelease(const Store::Outcome& outcome)
{
    std::ostringstream o;
    o << "{\"lane\":" << jstr(outcome.lane.name)
      << ",\"released\":" << (outcome.released ? "true" : "false")
      << ",\"worktree_removed\":" << (outcome.worktreeRemoved ? "true" : "false")
      << ",\"branch_kept\":true"
      << ",\"branch\":" << jstr(outcome.lane.branch)
      << ",\"killed\":" << (outcome.killed ? "true" : "false")
      << ",\"detail\":{\"reason\":" << jstr(outcome.reason)
      << ",\"path\":" << jstr(outcome.lane.worktree)
      << ",\"changes\":" << outcome.changes;
    if (!outcome.ignored.empty()) {
        o << ",\"ignored\":[";
        for (std::size_t i = 0; i < outcome.ignored.size(); ++i)
            o << (i ? "," : "") << jstr(outcome.ignored[i]);
        o << "]";
    }
    if (outcome.killed) o << ",\"killed_pid\":" << outcome.killedPid;
    o << "},\"seats\":{\"host\":{\"total\":" << outcome.snapshot.seatsTotal
      << ",\"used\":" << outcome.snapshot.seatsUsed << "}}}";
    return o.str();
}

std::string lane::renderError(const Store::Outcome& outcome)
{
    std::ostringstream o;
    o << "{\"error\":{\"code\":" << jstr(outcome.errorCode)
      << ",\"message\":" << jstr(outcome.errorMessage)
      << ",\"detail\":{" << outcome.errorDetail << "}"
      << remedy::member(outcome.errorRemedy) << "}}";
    return o.str();
}
