#include <app/error/remedy.h>
#include "manager.h"

#include <filesystem>
#include <memory>
#include <sstream>

#include <core/worker.h>
#include <app/catalog/store.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>

#include "lane-view.h"

namespace {

std::vector<std::string> workspacesNamedBy(const catalog::Document& doc)
{
    std::vector<std::string> out;
    const std::string* named = doc.field("workspaces");
    if (!named) return out;
    std::istringstream words(*named);
    std::string one;
    while (words >> one) {
        while (!one.empty() && (one.front() == ',' || one.front() == '`')) one.erase(0, 1);
        while (!one.empty() && (one.back() == ',' || one.back() == '`')) one.pop_back();
        if (!one.empty()) out.push_back(one);
    }
    return out;
}

std::string bad(const char* code, const char* message, const std::string& remedyJson)
{
    return std::string("{\"error\":{\"code\":\"") + code + "\",\"message\":\"" + message
         + "\",\"detail\":{}" + remedy::member(remedyJson) + "}}";
}

const std::string* stringMember(const manifest::Json& body, const char* key)
{
    const manifest::Json* v = body.find(key);
    return (v && v->isString()) ? &v->str() : nullptr;
}

std::string documentPath(const std::string& path)
{
    const std::string& root = manifest::Store::instance().data().root;
    if (root.empty() || path.empty()) return path;
    std::error_code ec;
    const std::filesystem::path rel =
        std::filesystem::relative(std::filesystem::path(path), std::filesystem::path(root), ec);
    if (ec || rel.empty() || rel.string().compare(0, 2, "..") == 0) return path;
    return rel.string();
}

}

HTTP_ASYNC_HANDLER(api::Manager, ClaimLane)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    if (!err.ok || !body.isObject()) {
        http::Response resp;
        resp.status = 400;
        resp.body = bad("bad_request", "the body is not a JSON object", remedy::none());
        return respond(std::move(resp));
    }

    if (manifest::Store::instance().data().state != manifest::Store::State::Ok) {
        http::Response resp;
        resp.status = 409;
        resp.body = bad("invalid_manifest",
                        manifest::jsonEscape(
                            manifest::Store::instance().data().errorMessage).c_str(),
                        remedy::reloadManifest());
        return respond(std::move(resp));
    }

    const std::string* ticket = stringMember(body, "ticket_id");
    const std::string* workspace = stringMember(body, "workspace");
    if (!ticket || ticket->empty() || !workspace || workspace->empty()) {
        http::Response resp;
        resp.status = 400;
        resp.body = bad("bad_request", "ticket_id and workspace are required", remedy::none());
        return respond(std::move(resp));
    }

    lane::Store::ClaimRequest request;
    request.ticketId = *ticket;
    request.workspace = *workspace;
    if (const std::string* role = stringMember(body, "role")) request.role = *role;
    if (const std::string* rid = stringMember(body, "request_id")) request.requestId = *rid;
    if (request.role.empty()) request.role = "run";

    core::Worker::instance().post([request, respond] {
        catalog::Document doc;
        std::vector<std::string> claimants;
        const bool known = catalog::Store::instance().findTicket(request.ticketId, doc, claimants)
                         == catalog::Store::Lookup::Found;
        const std::string lookedIn = catalog::Store::instance().ticketsPath();

        if (!claimants.empty()) {
            http::Response resp;
            resp.status = 409;
            resp.body = catalog::ambiguousTicketJson(request.ticketId, claimants);
            return respond(std::move(resp));
        }
        if (!known) {
            http::Response resp;
            resp.status = 404;
            resp.body = "{\"error\":{\"code\":\"not_found\",\"message\":\"no ticket by that "
                        "id exists in the repository this tool was pointed at\",\"detail\":{"
                        "\"ticket_id\":\"" + manifest::jsonEscape(request.ticketId)
                      + "\",\"path\":\"" + manifest::jsonEscape(lookedIn) + "\"}"
                      + remedy::member(remedy::reloadManifest()) + "}}";
            return respond(std::move(resp));
        }

        if (doc.status == "pass") {
            http::Response resp;
            resp.status = 409;
            resp.body = "{\"error\":{\"code\":\"already_passed\",\"message\":\"this ticket's "
                        "document says `status: pass`; the work after a pass is a new ticket, and "
                        "the tool does not write the repository's documents\",\"detail\":{"
                        "\"ticket_id\":\"" + manifest::jsonEscape(request.ticketId)
                      + "\",\"status\":\"" + manifest::jsonEscape(doc.status)
                      + "\",\"path\":\"" + manifest::jsonEscape(documentPath(doc.path)) + "\"}"
                      + remedy::member(remedy::none()) + "}}";
            return respond(std::move(resp));
        }

        lane::Store::ClaimRequest extras = request;
        extras.alsoWorkspaces = workspacesNamedBy(doc);

        lane::Store::instance().claim(extras, [respond](lane::Store::Outcome out) {
            if (out.status >= 400) {
                http::Response resp;
                resp.status = out.status;
                resp.body = lane::renderError(out);
                return respond(std::move(resp));
            }

            core::Worker::instance().post(
                [lane = std::move(out.lane), history = std::move(out.snapshot.stages), respond] {
                    http::Response resp;
                    resp.status = 201;
                    resp.body = lane::renderLane(lane, api::laneProgress(history)(lane.ticketId),
                                                 api::laneEvidence());
                    respond(std::move(resp));
                });
        });
    });
}
