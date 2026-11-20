#include <app/error/remedy.h>
#include "manager.h"

#include <app/agent/history.h>
#include <app/catalog/store.h>
#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <core/worker.h>

HTTP_ASYNC_HANDLER(api::Manager, TicketRuns)
{
    const std::string id = req.params.count("id") ? req.params.at("id") : std::string();

    core::Worker::instance().post([id, respond] {
        auto& store = catalog::Store::instance();
        catalog::Document doc;
        const bool known = store.ticketById(id, doc);
        const std::string path = known ? doc.path : store.ticketsPath() + "/" + id + ".md";

        http::Response resp;
        if (!known) {
            resp.status = 404;
            resp.body = std::string(R"({"error":{"code":"not_found",)")
                      + R"("message":"no ticket by that id","detail":{"path":")"
                      + manifest::jsonEscape(path) + R"("})"
                      + remedy::member(remedy::reloadManifest()) + "}}";
            return respond(std::move(resp));
        }

        std::string body = "{\"runs\":[";
        bool first = true;
        for (const auto& row : agent::history::forTicket(manifest::Store::instance().data().ledgers(), id)) {
            if (!first) body += ",";
            first = false;
            body += row.json;
        }
        resp.body = body + "]}";
        respond(std::move(resp));
    });
}
