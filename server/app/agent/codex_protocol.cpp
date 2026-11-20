#include "codex_protocol.h"

#include <app/manifest/json.h>

static std::string request(int id, const std::string& method, const std::string& params)
{
    return "{\"id\":" + std::to_string(id) + ",\"method\":" + manifest::jstr(method)
         + ",\"params\":{" + params + "}}";
}

std::string agent::codex::initialize(int id)
{
    return request(id, "initialize",
                   "\"clientInfo\":{\"name\":\"factory\",\"version\":\"0.1\"}");
}

std::string agent::codex::startThread(int id, const std::string& model,
                                    const std::string& cwd, const std::string& resumeId,
                                    const std::string& approvalPolicy)
{
    std::string params = "\"model\":" + manifest::jstr(model)
                       + ",\"cwd\":" + manifest::jstr(cwd)
                       + ",\"sandbox\":\"danger-full-access\",\"approvalPolicy\":"
                       + manifest::jstr(approvalPolicy);
    if (!resumeId.empty()) params += ",\"threadId\":" + manifest::jstr(resumeId);
    return request(id, resumeId.empty() ? "thread/start" : "thread/resume", params);
}

std::string agent::codex::startTurn(int id, const std::string& threadId,
                                  const std::string& text, const std::string& effort)
{
    std::string params = "\"threadId\":" + manifest::jstr(threadId)
                       + ",\"input\":[{\"type\":\"text\",\"text\":" + manifest::jstr(text) + "}]";
    if (!effort.empty()) params += ",\"effort\":" + manifest::jstr(effort);
    return request(id, "turn/start", params);
}

std::string agent::codex::interrupt(int id, const std::string& threadId,
                                  const std::string& turnId)
{
    return request(id, "turn/interrupt", "\"threadId\":" + manifest::jstr(threadId)
                   + ",\"turnId\":" + manifest::jstr(turnId));
}

std::string agent::codex::setGoal(int id, const std::string& threadId,
                                  const std::string& objective, long long tokenBudget)
{
    std::string params = "\"threadId\":" + manifest::jstr(threadId)
                       + ",\"objective\":" + manifest::jstr(objective)
                       + ",\"status\":\"active\"";
    if (tokenBudget > 0) params += ",\"tokenBudget\":" + std::to_string(tokenBudget);
    return request(id, "thread/goal/set", params);
}
