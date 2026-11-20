#include "store.h"

#include <algorithm>
#include <deque>
#include <exception>
#include <stdexcept>
#include <utility>

#include <app/error/remedy.h>

#include "bytes.h"
#include "ledger.h"

template<class T>
struct RoutingResult {
    T value {};
    std::string error;
};

struct RoutingReuse {
    bool found = false;
    routing::Receipt receipt;
    std::string bytes;
};

struct routing::Store::Job {
    Record record;
    std::weak_ptr<Lane> lane;
    SnapshotFn snapshot;
    ExecuteFn execute;
    Done initialDone;
    std::string expectedJournal;
    std::string capture;
    std::string persistenceError;
    std::vector<std::function<void()>> waiting;
    unsigned long long revision = 0;
    unsigned long long generation = 0;
    bool writing = false;
    bool started = false;
    bool exited = false;
    bool finishing = false;
    int exitCode = -1;
};

struct routing::Store::Lane {
    Context context;
    std::map<std::string, JobPtr> jobs;
    std::deque<std::function<void(LanePtr)>> operations;
    std::string loadError;
    unsigned long long nextOrdinal = 1;
    bool loaded = false;
    bool operating = false;
};

void routing::Store::init()
{
    queue_.post([this] {
        stopping_ = false;
        lanes_.clear();
    });
}

void routing::Store::stop()
{
    queue_.post([this] { stopping_ = true; });
}

void routing::Store::enqueue_(Context context, std::function<void(LanePtr)> operation)
{
    const std::string key = context.runtimeFolder + "\n" + context.lane;
    auto found = lanes_.find(key);
    if (found != lanes_.end()) {
        found->second->operations.push_back(std::move(operation));
        return next_(found->second);
    }
    auto lane = std::make_shared<Lane>();
    lane->context = context;
    lane->operations.push_back(std::move(operation));
    lanes_[key] = lane;
    queue_.offload(
        [context] {
            RoutingResult<std::vector<Record>> result;
            try { result.value = loadRecords(context); }
            catch (const std::exception& error) { result.error = error.what(); }
            return result;
        },
        [this, lane](RoutingResult<std::vector<Record>> result) {
            lane->loadError = std::move(result.error);
            for (auto& record : result.value) {
                auto job = std::make_shared<Job>();
                job->record = std::move(record);
                job->lane = lane;
                job->expectedJournal = serialize(job->record);
                lane->nextOrdinal = std::max(lane->nextOrdinal,
                    static_cast<unsigned long long>(job->record.ordinal) + 1);
                lane->jobs[job->record.request.requestId] = job;
            }
            lane->loaded = true;
            next_(lane);
        });
}

void routing::Store::next_(const LanePtr& lane)
{
    if (stopping_ || !lane->loaded || lane->operating || lane->operations.empty()) return;
    lane->operating = true;
    auto operation = std::move(lane->operations.front());
    lane->operations.pop_front();
    operation(lane);
}

void routing::Store::release_(const LanePtr& lane)
{
    lane->operating = false;
    queue_.post([this, lane] { next_(lane); });
}

void routing::Store::submit(Context context, Request request, SnapshotFn snapshot,
                             ExecuteFn execute, Done done)
{
    queue_.post([this, context = std::move(context), request = std::move(request),
                 snapshot = std::move(snapshot), execute = std::move(execute),
                 done = std::move(done)]() mutable {
        bool valid = validId(request.requestId) && !request.steps.empty()
                     && request.steps.size() <= 16;
        for (auto& step : request.steps) {
            std::sort(step.acceptedExitCodes.begin(), step.acceptedExitCodes.end());
            valid = valid && !step.acceptedExitCodes.empty()
                && step.acceptedExitCodes.front() >= 0 && step.acceptedExitCodes.back() <= 255
                && std::adjacent_find(step.acceptedExitCodes.begin(), step.acceptedExitCodes.end())
                   == step.acceptedExitCodes.end() && context.commands.count(step.which);
        }
        if (!valid) return done(refusal(400, "bad_request", "routing requires a valid declared plan",
                                         context));
        enqueue_(context, [this, context, request, snapshot, execute, done](LanePtr lane) mutable {
            Done finish = [this, lane, done](Answer answer) {
                done(std::move(answer));
                release_(lane);
            };
            if (!lane->loadError.empty())
                return finish(refusal(409, "routing_unresolved", lane->loadError, context));
            admit_(lane, context, request, snapshot, execute, std::move(finish));
        });
    });
}

void routing::Store::admit_(const LanePtr& lane, Context context, Request request,
                           SnapshotFn snapshot, ExecuteFn execute, Done done)
{
    const auto known = lane->jobs.find(request.requestId);
    if (known != lane->jobs.end()) {
        if (planJson(known->second->record.request) != planJson(request))
            return done(refusal(409, "routing_conflict", "this request ID retains a different plan",
                                context));
        return inspect_(known->second, context, false, snapshot, std::move(done));
    }
    for (const auto& saved : lane->jobs) {
        if (saved.second->writing)
            return ready_(saved.second, [this, lane, context, request, snapshot, execute, done] {
                admit_(lane, context, request, snapshot, execute, done);
            });
        if (saved.second->record.state == "running")
            return done(refusal(409, "routing_busy", "this lane already has an active routing request",
                                context, {}, remedy::retry()));
    }
    if (context.pid > 0 || context.queued) {
        const std::string code = context.busyCode.empty()
            ? (context.queued ? "resource_busy" : "wrong_state") : context.busyCode;
        return done(refusal(409, code, "this lane is not free to execute the routing plan", context,
                            context.busyDetail, context.busyRemedy));
    }
    snapshot(context, [this, lane, context, request, snapshot, execute, done](Snapshot observed) {
        queue_.post([this, lane, context, request, snapshot, execute, done,
                     observed = std::move(observed)]() mutable {
            if (stopping_) return;
            if (!observed.error.empty() || !validSource(observed.source))
                return done(refusal(409, "routing_unresolved", observed.error.empty()
                    ? "source identity is unavailable" : observed.error, context));
            std::vector<std::pair<Record, std::string>> records;
            for (const auto& saved : lane->jobs)
                records.emplace_back(saved.second->record, saved.second->expectedJournal);
            queue_.offload(
                [records] {
                    RoutingResult<bool> result;
                    try {
                        for (const auto& saved : records) {
                            const auto read = readRecord(saved.first.context,
                                                         saved.first.request.requestId);
                            if (serialize(read) != saved.second)
                                throw std::runtime_error("routing journal differs from its retained state");
                        }
                        result.value = true;
                    } catch (const std::exception& error) { result.error = error.what(); }
                    return result;
                },
                [this, lane, context, request, snapshot, execute, done, observed](
                    RoutingResult<bool> result) mutable {
                    if (stopping_) return;
                    if (!result.error.empty())
                        return done(refusal(409, "routing_unresolved", result.error, context));
                    auto job = std::make_shared<Job>();
                    job->record.context = context;
                    job->record.request = request;
                    job->record.source = observed.source;
                    job->record.ordinal = lane->nextOrdinal++;
                    job->lane = lane;
                    job->snapshot = snapshot;
                    job->execute = execute;
                    job->initialDone = done;
                    lane->jobs[request.requestId] = job;
                    commit_(job, [this, job](std::string error) {
                        if (!error.empty()) {
                            auto done = std::move(job->initialDone);
                            return done(refusal(409, "routing_unresolved", error, job->record.context));
                        }
                        advance_(job);
                    });
                });
        });
    });
}

void routing::Store::get(Context context, std::string requestId, bool includeOutput,
                          SnapshotFn snapshot, Done done)
{
    queue_.post([this, context = std::move(context), requestId = std::move(requestId), includeOutput,
                 snapshot = std::move(snapshot), done = std::move(done)]() mutable {
        if (!validId(requestId))
            return done(refusal(400, "bad_request", "routing request ID is invalid", context));
        enqueue_(context, [this, context, requestId, includeOutput, snapshot, done](LanePtr lane) {
            Done finish = [this, lane, done](Answer answer) {
                done(std::move(answer));
                release_(lane);
            };
            if (!lane->loadError.empty())
                return finish(refusal(409, "routing_unresolved", lane->loadError, context));
            const auto known = lane->jobs.find(requestId);
            if (known == lane->jobs.end())
                return finish(refusal(404, "not_found", "no retained routing request has that ID",
                                      context, {}, remedy::retry()));
            inspect_(known->second, context, includeOutput, snapshot, std::move(finish));
        });
    });
}

void routing::Store::ready_(const JobPtr& job, std::function<void()> work)
{
    if (job->writing) job->waiting.push_back(std::move(work));
    else work();
}

void routing::Store::inspect_(const JobPtr& job, Context context, bool includeOutput,
                              SnapshotFn snapshot, Done done)
{
    ready_(job, [this, job, context, includeOutput, snapshot, done] {
        const auto& retained = job->record.context;
        if (retained.ticketId != context.ticketId || retained.workspace != context.workspace
            || retained.incarnation != context.incarnation)
            return done(refusal(409, "routing_unresolved",
                                "the retained routing request belongs to a different lane claim",
                                context));
        if (!job->persistenceError.empty())
            return done(refusal(409, "routing_unresolved", job->persistenceError, context));
        snapshot(context, [this, job, context, includeOutput, snapshot, done](Snapshot observed) {
            queue_.post([this, job, context, includeOutput, snapshot, done,
                         observed = std::move(observed)] {
                if (stopping_) return;
                if (!observed.error.empty() || !validSource(observed.source))
                    return done(refusal(409, "routing_unresolved", observed.error.empty()
                        ? "source identity is unavailable" : observed.error, context));
                if (job->writing)
                    return ready_(job, [this, job, context, includeOutput, snapshot, done] {
                        inspect_(job, context, includeOutput, snapshot, done);
                    });
                const auto revision = job->revision;
                const auto expected = job->expectedJournal;
                const auto record = job->record;
                queue_.offload(
                    [record, expected, includeOutput, observed] {
                        RoutingResult<std::string> result;
                        try {
                            const auto read = readRecord(record.context, record.request.requestId);
                            if (serialize(read) != expected)
                                throw std::runtime_error("routing journal differs from its retained state");
                            if (read.state == "completed" && read.source != observed.source)
                                throw std::runtime_error("the completed routing source has changed");
                            result.value = render(read, includeOutput);
                        } catch (const std::exception& error) { result.error = error.what(); }
                        return result;
                    },
                    [this, job, context, includeOutput, snapshot, done, revision](
                        RoutingResult<std::string> result) {
                        if (stopping_) return;
                        if (job->writing || job->revision != revision)
                            return ready_(job, [this, job, context, includeOutput, snapshot, done] {
                                inspect_(job, context, includeOutput, snapshot, done);
                            });
                        if (!result.error.empty())
                            return done(refusal(409, "routing_unresolved", result.error, context));
                        done({200, std::move(result.value)});
                    });
            });
        });
    });
}

void routing::Store::commit_(const JobPtr& job, std::function<void(std::string)> done)
{
    job->writing = true;
    ++job->revision;
    const auto record = job->record;
    queue_.offload(
        [record] {
            RoutingResult<std::string> result;
            try {
                saveRecord(record);
                result.value = serialize(record);
            } catch (const std::exception& error) { result.error = error.what(); }
            return result;
        },
        [this, job, done](RoutingResult<std::string> result) {
            job->writing = false;
            if (result.error.empty()) job->expectedJournal = std::move(result.value);
            else {
                job->persistenceError = result.error;
                job->record.state = "needs_main";
                job->record.action = "main";
                job->record.reason = "evidence_unresolved";
                job->record.pending = false;
            }
            auto waiting = std::move(job->waiting);
            job->waiting.clear();
            done(std::move(result.error));
            for (auto& work : waiting) ready_(job, std::move(work));
        });
}

void routing::Store::delivered_(const JobPtr& job, int status)
{
    if (!job->initialDone) return;
    auto done = std::move(job->initialDone);
    if (!job->persistenceError.empty())
        return done(refusal(409, "routing_unresolved", job->persistenceError, job->record.context));
    done({status, render(job->record, false)});
}

void routing::Store::settle_(const JobPtr& job, const std::string& reason)
{
    job->record.state = "needs_main";
    job->record.action = "main";
    job->record.reason = reason;
    job->record.pending = false;
    job->record.pendingCommandId.clear();
    commit_(job, [this, job, reason](std::string error) {
        if (job->initialDone && (reason == "source_unresolved" || reason == "evidence_unresolved"
                                || reason == "lane_unresolved")) {
            auto done = std::move(job->initialDone);
            return done(refusal(409, "routing_unresolved", error.empty()
                ? "routing stopped because " + reason : error, job->record.context));
        }
        delivered_(job, 202);
    });
}

void routing::Store::advance_(const JobPtr& job)
{
    if (stopping_ || job->record.state != "running") return;
    ready_(job, [this, job] {
        job->snapshot(job->record.context, [this, job](Snapshot observed) {
            queue_.post([this, job, observed = std::move(observed)] {
                if (stopping_ || job->record.state != "running") return;
                if (!observed.error.empty() || !validSource(observed.source))
                    return settle_(job, "source_unresolved");
                if (observed.source != job->record.source) return settle_(job, "source_changed");
                const auto lane = job->lane.lock();
                if (!lane) return settle_(job, "lane_unresolved");
                const auto index = job->record.nextIndex;
                const auto step = job->record.request.steps.at(index);
                std::vector<std::pair<Record, std::string>> records;
                for (const auto& saved : lane->jobs)
                    if (step.reuse || saved.second == job)
                        records.emplace_back(saved.second->record, saved.second->expectedJournal);
                queue_.offload(
                    [records, record = job->record, step] {
                        RoutingResult<RoutingReuse> result;
                        try {
                            for (const auto& saved : records) {
                                const auto read = readRecord(saved.first.context,
                                                             saved.first.request.requestId);
                                if (serialize(read) != saved.second)
                                    throw std::runtime_error("routing journal differs from its retained state");
                                if (!step.reuse || read.source != record.source) continue;
                                for (const auto& receipt : read.receipts) {
                                    const auto& prior = read.request.steps.at(receipt.index);
                                    if (!receipt.accepted || !receipt.complete
                                        || receipt.sourceAfter != record.source
                                        || receipt.command != record.context.commands.at(step.which)
                                        || prior.acceptedExitCodes != step.acceptedExitCodes) continue;
                                    result.value.found = true;
                                    result.value.receipt = receipt;
                                    result.value.bytes = captureBytes(receipt);
                                }
                            }
                        } catch (const std::exception& error) { result.error = error.what(); }
                        return result;
                    },
                    [this, job, step](RoutingResult<RoutingReuse> result) {
                        if (stopping_) return;
                        if (!result.error.empty()) return settle_(job, "evidence_unresolved");
                        if (!result.value.found) return dispatch_(job);
                        auto receipt = result.value.receipt;
                        receipt.index = static_cast<int>(job->record.nextIndex);
                        receipt.which = step.which;
                        try { receipt.capturePath = capturePath(job->record, receipt.index); }
                        catch (const std::exception&) { return settle_(job, "evidence_unresolved"); }
                        receipt.reused = true;
                        const auto bytes = std::move(result.value.bytes);
                        job->snapshot(job->record.context,
                            [this, job, step, receipt, bytes](Snapshot current) {
                                queue_.post([this, job, step, receipt, bytes, current] {
                                    if (stopping_) return;
                                    if (!current.error.empty() || !validSource(current.source))
                                        return settle_(job, "source_unresolved");
                                    if (current.source != job->record.source)
                                        return settle_(job, "source_changed");
                                    queue_.offload(
                                        [receipt, bytes] {
                                            RoutingResult<bool> saved;
                                            try { saveCapture(receipt, bytes); saved.value = true; }
                                            catch (const std::exception& error) { saved.error = error.what(); }
                                            return saved;
                                        },
                                        [this, job, step, receipt](RoutingResult<bool> saved) {
                                            if (stopping_) return;
                                            if (!saved.error.empty()) return settle_(job, "evidence_unresolved");
                                            job->record.receipts.push_back(receipt);
                                            job->record.nextIndex = job->record.receipts.size();
                                            if (step.requireDecision) {
                                                job->record.state = "needs_main";
                                                job->record.action = "main";
                                                job->record.reason = "decision_required";
                                            } else if (job->record.nextIndex == job->record.request.steps.size()) {
                                                job->record.state = "completed";
                                                job->record.action = "main";
                                                job->record.reason = "commands_completed";
                                            }
                                            commit_(job, [this, job](std::string error) {
                                                delivered_(job, 202);
                                                if (error.empty()) advance_(job);
                                            });
                                        });
                                });
                            });
                    });
            });
        });
    });
}

void routing::Store::dispatch_(const JobPtr& job)
{
    if (stopping_) return;
    job->record.pending = true;
    job->record.pendingCommandId.clear();
    job->capture.clear();
    job->started = false;
    job->exited = false;
    job->finishing = false;
    const auto generation = ++job->generation;
    commit_(job, [this, job, generation](std::string error) {
        if (stopping_) return;
        if (!error.empty()) return delivered_(job, 202);
        job->snapshot(job->record.context, [this, job, generation](Snapshot current) {
            queue_.post([this, job, generation, current] {
                if (stopping_ || job->generation != generation) return;
                if (!current.error.empty() || !validSource(current.source))
                    return settle_(job, "source_unresolved");
                if (current.source != job->record.source) return settle_(job, "source_changed");
                try {
                    job->execute(job->record.context,
                        job->record.request.steps.at(job->record.nextIndex),
                        [this, job, generation](Event event) {
                            queue_.post([this, job, generation, event = std::move(event)]() mutable {
                                event_(job, generation, std::move(event));
                            });
                        },
                        [this, job, generation](Started started) {
                            queue_.post([this, job, generation, started = std::move(started)]() mutable {
                                started_(job, generation, std::move(started));
                            });
                        });
                } catch (const std::exception& error) {
                    Started failed;
                    failed.status = 500;
                    failed.errorCode = "routing_unresolved";
                    failed.errorMessage = error.what();
                    started_(job, generation, std::move(failed));
                }
            });
        });
    });
}

void routing::Store::started_(const JobPtr& job, unsigned long long generation, Started started)
{
    if (stopping_ || job->generation != generation || job->record.state != "running"
        || job->started) return;
    if (started.status >= 400 || started.commandId.empty()) {
        job->record.state = "needs_main";
        job->record.action = "main";
        job->record.reason = "command_not_started";
        job->record.pending = false;
        commit_(job, [this, job, started](std::string error) mutable {
            if (!job->initialDone) return;
            auto done = std::move(job->initialDone);
            if (!error.empty()) return done(refusal(409, "routing_unresolved", error, job->record.context));
            done(refusal(started.status >= 400 ? started.status : 409,
                         started.errorCode.empty() ? "routing_unresolved" : started.errorCode,
                         started.errorMessage.empty() ? "the declared command did not start"
                                                      : started.errorMessage,
                         job->record.context, started.errorDetail, started.errorRemedy));
        });
        return;
    }
    job->started = true;
    job->record.pendingCommandId = started.commandId;
    commit_(job, [this, job](std::string error) {
        delivered_(job, 202);
        if (error.empty() && job->exited) terminal_(job);
    });
}

void routing::Store::event_(const JobPtr& job, unsigned long long generation, Event event)
{
    if (stopping_ || job->generation != generation || job->record.state != "running"
        || job->exited) return;
    job->capture += event.chunk;
    if (!event.exited) return;
    job->exited = true;
    job->exitCode = event.exitCode;
    if (job->started && !job->writing) terminal_(job);
}

void routing::Store::terminal_(const JobPtr& job)
{
    if (stopping_ || job->finishing || !job->started || !job->exited || job->writing) return;
    job->finishing = true;
    const auto generation = job->generation;
    const auto bytes = std::make_shared<std::string>(std::move(job->capture));
    job->snapshot(job->record.context, [this, job, generation, bytes](Snapshot observed) {
        queue_.post([this, job, generation, bytes, observed] {
            if (stopping_ || job->generation != generation) return;
            Receipt receipt;
            receipt.index = static_cast<int>(job->record.nextIndex);
            const auto step = job->record.request.steps.at(receipt.index);
            receipt.which = step.which;
            receipt.command = job->record.context.commands.at(step.which);
            receipt.commandId = job->record.pendingCommandId;
            receipt.source = job->record.source;
            receipt.sourceAfter = observed.error.empty() && validSource(observed.source)
                ? observed.source : std::string{};
            receipt.exitCode = job->exitCode;
            try { receipt.capturePath = capturePath(job->record, receipt.index); }
            catch (const std::exception&) { return settle_(job, "evidence_unresolved"); }
            receipt.bytes = bytes->size();
            receipt.accepted = accepted(step, receipt.exitCode);
            queue_.offload(
                [receipt, bytes]() mutable {
                    RoutingResult<Receipt> result;
                    try {
                        receipt.captureSha256 = sha256(*bytes);
                        saveCapture(receipt, *bytes);
                        result.value = std::move(receipt);
                    } catch (const std::exception& error) { result.error = error.what(); }
                    return result;
                },
                [this, job, generation, step, observed](RoutingResult<Receipt> result) {
                    if (stopping_ || job->generation != generation) return;
                    if (!result.error.empty()) return settle_(job, "evidence_unresolved");
                    job->record.receipts.push_back(std::move(result.value));
                    job->record.nextIndex = job->record.receipts.size();
                    job->record.pending = false;
                    job->record.pendingCommandId.clear();
                    const auto& receipt = job->record.receipts.back();
                    if (!observed.error.empty() || !validSource(observed.source)) {
                        job->record.state = "needs_main";
                        job->record.action = "main";
                        job->record.reason = "source_unresolved";
                    } else if (receipt.sourceAfter != receipt.source) {
                        job->record.state = "needs_main";
                        job->record.action = "main";
                        job->record.reason = "source_changed";
                    } else if (!receipt.accepted) {
                        job->record.state = "needs_main";
                        job->record.action = "main";
                        job->record.reason = "command_failed";
                        checkpoint_(job);
                    } else if (step.requireDecision) {
                        job->record.state = "needs_main";
                        job->record.action = "main";
                        job->record.reason = "decision_required";
                    } else if (job->record.nextIndex == job->record.request.steps.size()) {
                        job->record.state = "completed";
                        job->record.action = "main";
                        job->record.reason = "commands_completed";
                    }
                    commit_(job, [this, job](std::string error) {
                        delivered_(job, 202);
                        if (error.empty()) advance_(job);
                    });
                });
        });
    });
}

void routing::Store::checkpoint_(const JobPtr& job)
{
    const auto lane = job->lane.lock();
    if (!lane) return;
    std::vector<Record> records;
    for (const auto& saved : lane->jobs) records.push_back(saved.second->record);
    std::sort(records.begin(), records.end(), [](const Record& a, const Record& b) {
        return a.ordinal < b.ordinal;
    });
    std::vector<Receipt> actual;
    std::size_t count = 0;
    for (const auto& record : records)
        for (const auto& receipt : record.receipts) {
            ++count;
            if (!receipt.reused) actual.push_back(receipt);
        }
    if (actual.size() < 2) return;
    const auto& last = actual.back();
    const auto& prior = actual[actual.size() - 2];
    if (last.accepted || prior.accepted || last.source != last.sourceAfter
        || prior.source != prior.sourceAfter || last.source != prior.source
        || last.command != prior.command || last.exitCode != prior.exitCode
        || last.captureSha256 != prior.captureSha256) return;
    try {
        const std::string cause = sha256(last.source + "\n" + std::to_string(last.command.size())
            + ":" + last.command + "\n" + std::to_string(last.exitCode) + "\n" + last.captureSha256);
        Checkpoint checkpoint;
        checkpoint.id = "checkpoint-" + cause;
        checkpoint.cause = cause;
        checkpoint.receiptCount = count;
        for (const auto& record : records)
            if (record.checkpoint.cause == cause) { checkpoint = record.checkpoint; break; }
        job->record.checkpoint = std::move(checkpoint);
        job->record.action = "advisor";
    } catch (const std::exception&) {
        job->record.action = "main";
        job->record.reason = "evidence_unresolved";
    }
}
