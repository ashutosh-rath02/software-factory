#include <app/agent/history.h>
#include <app/agent/session.h>
#include <app/api/delivery.h>
#include <app/delivery/probe.h>
#include <app/lane/git.h>
#include <app/lane/store.h>
#include <app/manifest/store.h>
#include <core/worker.h>

void api::recordTheRefTheSessionLeft(const std::string& lane, const std::string& runId)
{
    if (runId.empty() || lane.empty()) return;
    lane::Store::instance().list([lane, runId](lane::Store::Outcome out) {
        std::string branch;
        for (const auto& record : out.snapshot.lanes)
            if (record.name == lane && record.runId == runId) branch = record.branch;
        if (branch.empty()) return;
        const std::string root = manifest::Store::instance().data().gitRoot;
        const std::string runs = manifest::Store::instance().data().runsDir;
        if (root.empty() || runs.empty()) return;
        core::Worker::instance().postSerialized([runs, root, runId, branch] {
            const lane::git::Result tip =
                lane::git::run(root, {"rev-parse", "--verify", "--quiet",
                                      "refs/heads/" + branch});
            std::string sha = tip.ok ? tip.out : std::string();
            while (!sha.empty() && (sha.back() == '\n' || sha.back() == '\r')) sha.pop_back();
            agent::history::noteRef(runs, runId, branch, sha);
            delivery::Probe::instance().requestPass();
        });
    });
}
