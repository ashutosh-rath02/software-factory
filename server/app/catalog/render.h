#pragma once

#include <functional>
#include <string>
#include <vector>

namespace catalog {

struct Result {
    int status = 200;
    std::string body;
};

struct Delivery {
    std::string state = "unknown";
    std::string detail;
    std::string claimed;
    std::string reason;
    bool disagrees = false;
    bool needsYou = false;
};

using DeliveryLookup = std::function<Delivery(const std::string& ticketId,
                                              const std::string& claimedStatus,
                                              const std::string& reportFileName)>;

struct Runs {
    int count = 0;
    long long durationS = 0;
    double costUsd = 0;

    std::string lastOutcome;
};

using RunsLookup = std::function<Runs(const std::string& ticketId)>;

struct Stage {
    int index = 0;
    int round = 1;
    int of = 1;
};

using StageLookup = std::function<Stage(const std::string& ticketId,
                                        const std::vector<std::string>& sequence)>;

struct Role {
    std::string role;
    std::string stage;
    std::string state;
    std::string lane;
    std::string runId;
};

using RolesLookup = std::function<std::vector<Role>(const std::string& ticketId)>;

using OutcomeLookup = std::function<std::string(const std::string& ticketId)>;

struct LaneState {
    std::string lane;
    std::string process;
    std::string motion;
    std::string evidence;
    std::string role;
};

using LaneLookup = std::function<LaneState(const std::string& ticketId)>;

struct Unpushed {
    bool present = false;
    std::string branch;
    int commits = 0;
    long long ageS = 0;
};

using UnpushedLookup = std::function<Unpushed(const std::string& ticketId)>;

struct Occupancy {
    int occupied = 0;
    int total = 0;
};

const std::vector<std::string>& deliveryStates();

using AuthorLookup = std::function<std::string(const std::string& ticketId)>;

struct TicketQuery {
    std::string q;
    std::string provenance;
    std::string stage;
    std::string page;
    std::string pageSize;
};

Result renderRequirements(const std::string& query, const std::string& filter,
                          const DeliveryLookup& delivery,
                          const LaneLookup& lanes = {});
Result renderRequirement(const std::string& id, const DeliveryLookup& delivery,
                         const StageLookup& stage = {},
                         const OutcomeLookup& outcome = {},
                         const LaneLookup& lanes = {});
Result renderTickets(const TicketQuery& query, const DeliveryLookup& delivery,
                     const RunsLookup& runs,
                     const StageLookup& stage,
                     const RolesLookup& roles,
                     const LaneLookup& lanes,
                     const AuthorLookup& authors);
Result renderTicket(const std::string& id, const DeliveryLookup& delivery,
                    const RunsLookup& runs,
                    const StageLookup& stage,
                    const RolesLookup& roles,
                    const AuthorLookup& authors);
Result renderReport(const std::string& ticketId);

Result renderEscapes();
std::vector<std::string> escapeRowDates();
Result renderWiki();

Result renderHome(const DeliveryLookup& delivery, const LaneLookup& lanes,
                  const UnpushedLookup& unpushed, const Occupancy& seats,
                  const RolesLookup& roles = nullptr);

std::string reportFileNameFor(const std::string& ticketId);

std::string ambiguousTicketJson(const std::string& id, const std::vector<std::string>& paths);

}
