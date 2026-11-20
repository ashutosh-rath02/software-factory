#include <app/error/remedy.h>
#include "manager.h"

#include <memory>

#include <core/worker.h>
#include <app/lane/diff.h>
#include <app/lane/store.h>

#include "lane-view.h"

namespace {

void laneDiff(const http::Request& req, http::Responder respond)
{
    const std::string name = api::laneNameOf(req);
    auto path = req.params.find("path");

    lane::diff::Query query;
    query.lane = name;
    if (path != req.params.end()) {
        query.path = path->second;
        query.wantPatch = true;
    }

    lane::Store::instance().list([query, respond](lane::Store::Outcome out) mutable {
        const lane::Store::Record* record = nullptr;
        for (const auto& r : out.snapshot.lanes)
            if (r.name == query.lane) record = &r;

        if (!record) {
            http::Response missing;
            missing.status = 404;
            missing.body = R"({"error":{"code":"not_found",)"
                           R"("message":"no lane by that name is claimed","detail":{"lane":")"
                           + query.lane + R"("})"
                           + remedy::member(remedy::reloadManifest()) + "}}";
            return respond(std::move(missing));
        }

        query.branch = record->branch;
        query.worktree = record->worktree;

        core::Worker::instance().post([query, respond] {
            http::Response resp;
            resp.status = 200;
            resp.body = lane::diff::read(query);
            respond(std::move(resp));
        });
    });
}

}

HTTP_ASYNC_HANDLER(api::Manager, LaneDiff) { laneDiff(req, std::move(respond)); }

HTTP_ASYNC_HANDLER(api::Manager, LaneDiffSegments) { laneDiff(req, std::move(respond)); }
