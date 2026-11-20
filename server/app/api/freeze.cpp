#include "freeze.h"

#include <app/manifest/store.h>

std::vector<permissions::Store::Frozen> api::freezeFor(const catalog::Document& ticket,
                                                       const std::string& reportedStage)
{
    std::vector<permissions::Store::Frozen> out;

    const manifest::Store::Data& data = manifest::Store::instance().data();
    if (!data.hasEvidence) return out;
    if (reportedStage != data.evidence.frozenAfter) return out;
    if (ticket.id.empty()) return out;

    for (const catalog::EvidenceRef& ref : catalog::evidenceRefs(ticket.body,
                                                                 data.evidence.linePrefix)) {
        permissions::Store::Frozen frozen;
        frozen.ticketId = ticket.id;
        frozen.criterion = ref.criterion;
        frozen.path = ref.path;
        out.push_back(std::move(frozen));
    }
    return out;
}
