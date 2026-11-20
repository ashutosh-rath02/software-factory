#pragma once

#include <string>

namespace agent::codex {

std::string initialize(int id);
std::string startThread(int id, const std::string& model, const std::string& cwd,
                        const std::string& resumeId,
                        const std::string& approvalPolicy = "on-request");
std::string startTurn(int id, const std::string& threadId, const std::string& text,
                      const std::string& effort = std::string());
std::string interrupt(int id, const std::string& threadId, const std::string& turnId);
std::string setGoal(int id, const std::string& threadId, const std::string& objective,
                    long long tokenBudget);

}
