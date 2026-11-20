#include "store.h"

#include <algorithm>
#include <cctype>
#include <random>
#include <sstream>


#include <app/error/remedy.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <core/engine.h>
#include <core/worker.h>
#include <glog/logging.h>

using manifest::jsonEscape;

using manifest::jstr;
std::string permissions::Store::hexId(const char* prefix)
{
    static std::mt19937_64 rng {std::random_device{}()};
    char buf[24];
    std::snprintf(buf, sizeof(buf), "%s-%08llx", prefix,
                  static_cast<unsigned long long>(rng() & 0xffffffffULL));
    return buf;
}

bool permissions::Store::isFileTool(const std::string& tool)
{
    return tool == "Read" || tool == "Write" || tool == "Edit" || tool == "MultiEdit"
        || tool == "NotebookEdit";
}

bool permissions::Store::isReadingTool(const std::string& tool)
{
    return tool == "Read" || tool == "Grep" || tool == "Glob" || tool == "LS"
        || tool == "NotebookRead";
}

bool permissions::Store::namesPath(const std::string& argument, const std::string& path)
{
    if (path.empty() || argument.empty()) return false;

    auto boundary = [](char c) {
        return !(std::isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '_' || c == '-');
    };

    for (std::size_t at = argument.find(path); at != std::string::npos;
         at = argument.find(path, at + 1)) {
        const bool before = at == 0 || boundary(argument[at - 1]);
        const std::size_t end = at + path.size();
        const bool after = end == argument.size() || boundary(argument[end]);
        if (before && after) return true;
    }
    return false;
}

std::string permissions::Store::policyName(Policy policy)
{
    switch (policy) {
        case Policy::EditFree: return "edit_free";
        case Policy::NeverAsk: return "never_ask";
        default: return "ask_all";
    }
}

bool permissions::Store::parsePolicy(const std::string& text, Policy& out)
{
    if (text == "ask_all") { out = Policy::AskAll; return true; }
    if (text == "edit_free") { out = Policy::EditFree; return true; }
    if (text == "never_ask") { out = Policy::NeverAsk; return true; }
    return false;
}

void permissions::Store::init()
{

    const manifest::Store::Data& data = manifest::Store::instance().data();
    manifestPath_ = data.manifestPath;
    rulesPath_ = data.permissionsPath;

    if (data.agent.hasPermissionMode) {
        Policy declared = Policy::AskAll;
        if (parsePolicy(data.agent.permissionMode, declared)) {
            policy_ = declared;
        } else {
            warnings_.push_back({"unknown_permission_mode",
                                 "agent.permission_mode is `" + data.agent.permissionMode
                                 + "`, which is not ask_all, edit_free or never_ask, so the "
                                   "policy is ask_all",
                                 manifestPath_});
        }
    }

    int n = 0;
    for (const auto& declared : data.agent.allowedTools) {
        Rule rule;
        rule.ruleId = "manifest-" + std::to_string(++n);
        rule.pattern = declared;
        rule.source = "manifest";
        rule.revocable = false;
        rule.parsed = parsePattern(declared);
        if (!rule.parsed.ok) {
            warnings_.push_back({"pattern_unparseable",
                                 "the pattern `" + declared + "` in agent.allowed_tools does not "
                                 "parse, so it grants nothing and every matching request is "
                                 "asked about",
                                 manifestPath_});
            continue;
        }
        rules_.push_back(rule);
    }

    if (!rulesPath_.empty()) {
        File file = loadFile(rulesPath_);
        for (auto& w : file.warnings) warnings_.push_back(w);
        for (auto& r : file.rules) rules_.push_back(r);
    }
}

void permissions::Store::start()
{
    LOG(INFO) << "permissions: policy " << policyName(policy_) << ", " << rules_.size()
              << " rule(s), " << warnings_.size() << " warning(s)";
}

void permissions::Store::stop()
{
    queue_.post( [this] {
        stopping_ = true;
        for (auto& [id, pending] : pending_) {
            Decision out;
            out.asked = true;
            out.reason = "voided";
            out.decision = "voided";
            out.requestId = id;
            if (pending.answer) pending.answer(out);
        }
        pending_.clear();
        blockedByLane_.clear();
    });
}

void permissions::Store::onRequest(RequestHandler handler)
{
    requestHandlers_.push_back(std::move(handler));
}

void permissions::Store::onResolved(Broadcast handler)
{
    resolvedHandlers_.push_back(std::move(handler));
}

void permissions::Store::onRules(Broadcast handler)
{
    rulesHandlers_.push_back(std::move(handler));
}

void permissions::Store::onEntry(EntryHandler handler)
{
    entryHandlers_.push_back(std::move(handler));
}

std::vector<permissions::Rule> permissions::Store::grantedRules_() const
{
    std::vector<Rule> out;
    for (const auto& r : rules_) if (r.source == "granted") out.push_back(r);
    return out;
}

std::string permissions::Store::rulesBody_() const
{
    std::ostringstream o;
    o << "{\"policy\":" << jstr(policyName(policy_))
      << ",\"policy_source\":" << jstr(policyFromSession_ ? "session" : "manifest")
      << ",\"rules\":" << renderRulesJson(rules_)
      << ",\"warnings\":[";
    for (std::size_t i = 0; i < warnings_.size(); ++i) {
        if (i) o << ",";
        o << "{\"code\":" << jstr(warnings_[i].code)
          << ",\"message\":" << jstr(warnings_[i].message)
          << ",\"path\":" << jstr(warnings_[i].path) << "}";
    }
    o << "]}";
    return o.str();
}

void permissions::Store::emitRules_()
{
    std::ostringstream o;
    o << "\"policy\":" << jstr(policyName(policy_))
      << ",\"rules\":" << renderRulesJson(rules_);
    for (auto& h : rulesHandlers_) h(o.str());
}

void permissions::Store::emitEntry_(const std::string& lane, const std::string& runId,
                                    const std::string& json)
{
    if (lane.empty()) return;
    for (auto& h : entryHandlers_) h(lane, runId, json);
}

void permissions::Store::listRules(std::function<void(std::string)> done)
{
    queue_.post([this, done = std::move(done)] { done(rulesBody_()); });
}

void permissions::Store::blockedSnapshot(std::function<void(std::map<std::string, Blocked>)> done)
{
    queue_.post([this, done = std::move(done)] {
        std::map<std::string, Blocked> out;
        for (const auto& [lane, requestId] : blockedByLane_)
            out[lane] = Blocked {"permission", requestId};
        done(std::move(out));
    });
}

void permissions::Store::noteRun(const std::string& lane, const std::string& runId,
                                 const std::string& ticketId, const std::string& outcome)
{
    queue_.post([this, lane, runId, ticketId, outcome] {
        if (!ticketId.empty()) ticketByLane_[lane] = ticketId;
        if (outcome == "running") {
            runByLane_[lane] = runId;
            policyByRun_[runId] = policy_;
            return;
        }

        auto live = runByLane_.find(lane);
        if (live != runByLane_.end() && live->second == runId) runByLane_.erase(live);
        policyByRun_.erase(runId);
        frozenByRun_.erase(runId);

        std::vector<std::string> dead;
        for (const auto& [id, pending] : pending_)
            if (pending.runId == runId && !runId.empty()) dead.push_back(id);
        for (const auto& id : dead) {
            Decision out;
            out.asked = true;
            out.reason = "voided";
            out.decision = "voided";
            out.requestId = id;
            resolve_(id, "voided", {}, out);
        }
    });
}

void permissions::Store::freeze(const std::string& runId, std::vector<Frozen> paths,
                               std::function<void()> done)
{
    queue_.post([this, runId, paths = std::move(paths), done = std::move(done)] {
        if (!runId.empty() && !paths.empty()) frozenByRun_[runId] = paths;
        if (done) done();
    });
}

const permissions::Store::Frozen* permissions::Store::frozenBy_(const Ask& request,
                                                                const std::string& runId) const
{
    if (runId.empty() || !request.hasArgument || isReadingTool(request.tool)) return nullptr;
    const auto it = frozenByRun_.find(runId);
    if (it == frozenByRun_.end()) return nullptr;
    for (const Frozen& frozen : it->second)
        if (namesPath(request.argument, frozen.path)) return &frozen;
    return nullptr;
}

std::string permissions::Store::decideNow_(const Ask& request, const std::string& runId,
                                           std::string& ruleId, const Frozen*& frozen)
{
    Policy policy = policy_;
    auto captured = policyByRun_.find(runId);
    if (!runId.empty() && captured != policyByRun_.end()) policy = captured->second;

    frozen = frozenBy_(request, runId);
    if (frozen) return {};

    if (policy == Policy::NeverAsk) return "policy";
    if (policy == Policy::EditFree && isFileTool(request.tool)) return "policy";

    for (auto& rule : rules_) {
        if (!ruleMatches(rule, request.tool, request.hasArgument ? request.argument
                                                                 : std::string())) continue;
        ++rule.matchCount;
        ruleId = rule.ruleId;
        return "rule";
    }
    return {};
}

void permissions::Store::ask(const Ask& request, std::function<void(Decision)> answer)
{
    queue_.post([this, request, answer = std::move(answer)] {
        if (stopping_) {
            Decision out;
            out.reason = "voided";
            if (answer) answer(out);
            return;
        }

        const std::string runId = runByLane_.count(request.lane) ? runByLane_[request.lane]
                                                                 : std::string();
        std::string ruleId;
        const Frozen* frozen = nullptr;
        const std::string allowedBy = decideNow_(request, runId, ruleId, frozen);
        if (!allowedBy.empty()) {
            Decision out;
            out.allowed = true;
            out.asked = false;
            out.reason = allowedBy;
            out.ruleId = ruleId;
            if (answer) answer(out);
            return;
        }

        Pending pending;
        pending.requestId = hexId("req");
        pending.lane = request.lane;
        pending.runId = runId;
        pending.ticketId = ticketByLane_.count(request.lane) ? ticketByLane_[request.lane]
                                                             : std::string();
        pending.tool = request.tool;
        pending.argument = request.argument;
        pending.hasArgument = request.hasArgument;
        pending.rulePattern = renderRulePattern(request.tool, request.argument,
                                                request.hasArgument);
        if (frozen) {
            pending.frozenTicket = frozen->ticketId;
            pending.frozenCriterion = frozen->criterion;
        }
        pending.answer = answer;

        const Miss miss = nearestMiss(rules_, request.tool,
                                      request.hasArgument ? request.argument : std::string());

        std::ostringstream body;
        body << "\"lane\":" << jstr(pending.lane)
             << ",\"request_id\":" << jstr(pending.requestId)
             << ",\"tool\":" << jstr(pending.tool)
             << ",\"input_summary\":" << jstr(request.inputSummary.empty() ? pending.argument
                                                                           : request.inputSummary)
             << ",\"rule_that_would_be_written\":" << jstr(pending.rulePattern);
        if (miss.found) {
            body << ",\"nearest_miss\":{\"rule_id\":" << jstr(miss.ruleId)
                 << ",\"pattern\":" << jstr(miss.pattern)
                 << ",\"source\":" << jstr(miss.source)
                 << ",\"differs_on\":" << jstr(miss.differsOn)
                 << ",\"matched_prefix\":" << jstr(miss.matchedPrefix)
                 << ",\"why_it_did_not_match\":" << jstr(miss.why) << "}";
        }
        if (pending.frozenCriterion) {
            body << ",\"frozen_by\":{\"ticket_id\":" << jstr(pending.frozenTicket)
                 << ",\"criterion\":" << pending.frozenCriterion << "}";
        }
        body << ",\"expires_at\":null";

        std::ostringstream entry;
        entry << "{\"kind\":\"permission_request\""
              << ",\"request_id\":" << jstr(pending.requestId)
              << ",\"tool\":" << jstr(pending.tool)
              << ",\"input_summary\":" << jstr(pending.argument)
              << ",\"rule_that_would_be_written\":" << jstr(pending.rulePattern) << "}";

        const std::string lane = pending.lane;
        const std::string ticketId = pending.ticketId;
        blockedByLane_[lane] = pending.requestId;
        pending_[pending.requestId] = std::move(pending);

        for (auto& h : requestHandlers_) h(lane, ticketId, body.str());
        emitEntry_(lane, runId, entry.str());
    });
}

void permissions::Store::resolve_(const std::string& requestId, const std::string& decision,
                                  const std::string& ruleId, const Decision& outcome)
{
    auto it = pending_.find(requestId);
    if (it == pending_.end()) return;
    Pending pending = std::move(it->second);
    pending_.erase(it);
    settled_[requestId] = (decision == "voided") ? "voided" : "answered";

    auto blocked = blockedByLane_.find(pending.lane);
    if (blocked != blockedByLane_.end() && blocked->second == requestId)
        blockedByLane_.erase(blocked);

    {
        std::ostringstream o;
        o << "\"lane\":" << jstr(pending.lane)
          << ",\"request_id\":" << jstr(requestId)
          << ",\"decision\":" << jstr(decision);
        if (!ruleId.empty()) o << ",\"rule_id\":" << jstr(ruleId);
        for (auto& h : resolvedHandlers_) h(o.str());
    }

    std::ostringstream entry;
    entry << "{\"kind\":\"permission_decision\""
          << ",\"request_id\":" << jstr(requestId)
          << ",\"decision\":" << jstr(decision)
          << ",\"rule\":" << (ruleId.empty() ? "null" : jstr(pending.rulePattern)) << "}";
    emitEntry_(pending.lane, pending.runId, entry.str());

    if (pending.answer) pending.answer(outcome);
}

void permissions::Store::decide(const std::string& requestId, const std::string& decision,
                                std::function<void(Answer)> done)
{
    queue_.post([this, requestId, decision, done = std::move(done)] {
        if (decision != "once" && decision != "always" && decision != "deny") {
            Answer bad;
            bad.status = 400;
            bad.errorCode = "bad_decision";
            bad.errorRemedy = remedy::none();
            bad.errorMessage = "a decision is once, always or deny";
            bad.errorDetail = "\"given\":" + jstr(decision);
            done(bad);
            return;
        }

        auto it = pending_.find(requestId);
        if (it == pending_.end()) {
            auto settled = settled_.find(requestId);
            if (settled != settled_.end() && settled->second == "voided") {
                Answer gone;
                gone.status = 410;
                gone.errorCode = "gone";
                gone.errorRemedy = remedy::none();
                gone.errorMessage = "the run this request belonged to is gone, so nothing was "
                                    "granted";
                gone.errorDetail = "\"state\":\"voided\"";
                done(gone);
                return;
            }
            Answer missing;
            missing.status = 404;
            missing.errorCode = "not_found";
            missing.errorRemedy = remedy::reloadManifest();
            missing.errorMessage = "no such pending permission request";
            missing.errorDetail = std::string("\"state\":")
                + jstr(settled != settled_.end() ? settled->second : "unknown");
            done(missing);
            return;
        }

        if (decision != "always") {
            Decision out;
            out.allowed = (decision == "once");
            out.asked = true;
            out.reason = "person";
            out.decision = decision;
            out.requestId = requestId;
            resolve_(requestId, decision, {}, out);

            Answer ok;
            ok.body = "{\"decision\":" + jstr(decision) + ",\"unblocked\":true}";
            done(ok);
            return;
        }

        Rule rule;
        rule.ruleId = hexId("rule");
        rule.pattern = it->second.rulePattern;
        rule.source = "granted";
        rule.grantedIso = nowIso();
        rule.revocable = true;
        rule.parsed = parsePattern(rule.pattern);

        std::vector<Rule> granted = grantedRules_();
        granted.push_back(rule);

        const std::string path = rulesPath_;
        queue_.offload(
            [path, granted] {
                Saved saved;
                saved.ok = saveFile(path, granted, saved.error);
                return saved;
            },
            [this, requestId, rule, done](Saved saved) {
                if (!saved.ok) {
                    Answer failed;
                    failed.status = 500;
                    failed.errorCode = "write_failed";
                    failed.errorRemedy = remedy::retry();
                    failed.errorMessage = "the policy file could not be written, so nothing "
                                          "was granted and the request is still waiting";
                    failed.errorDetail = "\"detail\":" + jstr(saved.error);
                    done(failed);
                    return;
                }
                if (!pending_.count(requestId)) {
                    Answer gone;
                    gone.status = 410;
                    gone.errorCode = "gone";
                    gone.errorRemedy = remedy::none();
                    gone.errorMessage = "the request went away while the rule was being "
                                        "written";
                    gone.errorDetail = "\"state\":\"voided\"";
                    done(gone);
                    return;
                }
                rules_.push_back(rule);
                emitRules_();

                Decision out;
                out.allowed = true;
                out.asked = true;
                out.reason = "person";
                out.decision = "always";
                out.ruleId = rule.ruleId;
                out.requestId = requestId;
                resolve_(requestId, "always", rule.ruleId, out);

                Answer ok;
                ok.body = "{\"decision\":\"always\",\"rule_id\":" + jstr(rule.ruleId)
                        + ",\"unblocked\":true}";
                done(ok);
            });
    });
}

void permissions::Store::revoke(const std::string& ruleId, std::function<void(Answer)> done)
{
    queue_.post([this, ruleId, done = std::move(done)] {
        auto it = std::find_if(rules_.begin(), rules_.end(),
                               [&](const Rule& r) { return r.ruleId == ruleId; });
        if (it == rules_.end()) {
            Answer missing;
            missing.status = 404;
            missing.errorCode = "not_found";
            missing.errorRemedy = remedy::reloadManifest();
            missing.errorMessage = "no such rule";
            missing.errorDetail = "\"rule_id\":" + jstr(ruleId);
            done(missing);
            return;
        }
        if (!it->revocable) {
            Answer refused;
            refused.status = 409;
            refused.errorCode = "not_revocable";
            refused.errorRemedy = remedy::reloadManifest();
            refused.errorMessage = "this rule is declared in the manifest; edit "
                                 + manifestPath_ + " to change it";
            refused.errorDetail = "\"path\":" + jstr(manifestPath_)
                                + ",\"field\":\"agent.allowed_tools\"";
            done(refused);
            return;
        }

        std::vector<Rule> granted;
        for (const auto& r : rules_) if (r.source == "granted" && r.ruleId != ruleId)
            granted.push_back(r);

        const std::string path = rulesPath_;
        queue_.offload(
            [path, granted] {
                Saved saved;
                saved.ok = saveFile(path, granted, saved.error);
                return saved;
            },
            [this, ruleId, done](Saved saved) {
                if (!saved.ok) {
                    Answer failed;
                    failed.status = 500;
                    failed.errorCode = "write_failed";
                    failed.errorRemedy = remedy::retry();
                    failed.errorMessage = "the policy file could not be rewritten, so the "
                                          "rule is still in force";
                    failed.errorDetail = "\"detail\":" + jstr(saved.error);
                    done(failed);
                    return;
                }
                rules_.erase(std::remove_if(rules_.begin(), rules_.end(),
                                            [&](const Rule& r) { return r.ruleId == ruleId; }),
                             rules_.end());
                emitRules_();
                int remaining = 0;
                for (const auto& r : rules_) if (r.revocable) ++remaining;
                Answer ok;
                ok.body = "{\"revoked\":true,\"remaining\":" + std::to_string(remaining) + "}";
                done(ok);
            });
    });
}

void permissions::Store::setPolicy(const std::string& policy, std::function<void(Answer)> done)
{
    queue_.post([this, policy, done = std::move(done)] {
        Policy parsed = Policy::AskAll;
        if (!parsePolicy(policy, parsed)) {
            Answer bad;
            bad.status = 400;
            bad.errorCode = "bad_policy";
            bad.errorRemedy = remedy::none();
            bad.errorMessage = "a policy is ask_all, edit_free or never_ask";
            bad.errorDetail = "\"allowed\":[\"ask_all\",\"edit_free\",\"never_ask\"]";
            done(bad);
            return;
        }
        policy_ = parsed;
        policyFromSession_ = true;
        emitRules_();

        Answer ok;
        ok.body = "{\"policy\":" + jstr(policyName(policy_)) + ",\"applies_to\":\"new_runs\"}";
        done(ok);
    });
}
