#include <app/error/remedy.h>
#include "manager.h"

#include <sstream>

#include <app/catalog/ids.h>
#include <app/manifest/json.h>

namespace {

using manifest::jsonEscape;

using manifest::jstr;
void badRequest(const http::Responder& respond)
{
    http::Response out;
    out.status = 400;
    out.body = R"({"error":{"code":"bad_request","message":"a prefix, as a non-empty string",)"
               R"("detail":{})" + remedy::member(remedy::none()) + "}}";
    respond(std::move(out));
}

}

HTTP_ASYNC_HANDLER(api::Manager, AllocateTicketId)
{
    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(req.body, err);
    const manifest::Json* prefix = err.ok ? body.find("prefix") : nullptr;
    if (!prefix || !prefix->isString() || prefix->str().empty()) return badRequest(respond);

    catalog::Ids::instance().allocate(prefix->str(), [respond](catalog::Ids::Allocation a) {
        http::Response out;
        if (a.ok) {
            std::ostringstream o;
            o << "{\"id\":" << jstr(a.id)
              << ",\"prefix\":" << jstr(a.prefix)
              << ",\"series\":" << jstr(a.series)
              << ",\"number\":" << a.number << "}";
            out.status = 201;
            out.body = o.str();
            respond(std::move(out));
            return;
        }

        if (!a.writeError.empty()) {
            out.status = 500;
            out.body = R"({"error":{"code":"write_failed","message":)"
                     + jstr("the id ledger could not be written, so no id was issued")
                     + R"(,"detail":{"path_error":)" + jstr(a.writeError) + "}"
                     + remedy::member(remedy::retry()) + "}}";
            respond(std::move(out));
            return;
        }

        std::ostringstream known;
        known << "[";
        for (std::size_t i = 0; i < a.known.size(); ++i)
            known << (i ? "," : "") << jstr(a.known[i]);
        known << "]";

        out.status = 422;
        out.body = R"({"error":{"code":"unknown_prefix","message":)"
                 + jstr("no declared repository's tickets use the prefix `" + a.prefix
                        + "`, so the tool will not start a numbering series for it")
                 + R"(,"detail":{"prefix":)" + jstr(a.prefix)
                 + ",\"known\":" + known.str() + "}"
                 + remedy::member(remedy::none()) + "}}";
        respond(std::move(out));
    });
}
