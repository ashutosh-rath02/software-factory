#include "manager.h"

#include <core/worker.h>
#include <app/catalog/render.h>

HTTP_ASYNC_HANDLER(api::Manager, Report)
{
    const std::string id = req.params.count("id") ? req.params.at("id") : std::string();

    core::Worker::instance().post([id, respond] {
        const catalog::Result out = catalog::renderReport(id);
        http::Response resp;
        resp.status = out.status;
        resp.body = out.body;
        respond(std::move(resp));
    });
}
