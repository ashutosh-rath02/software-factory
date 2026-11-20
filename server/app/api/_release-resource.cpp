#include "manager.h"

#include <sstream>

#include <app/lane/store.h>
#include <app/lane/render.h>
#include <app/manifest/json.h>

#include "lane-view.h"

namespace {

using manifest::jstr;
std::string renderFreed(const lane::Store::Outcome& out)
{
    std::ostringstream o;
    o << "{\"resource\":" << jstr(out.resource)
      << ",\"lane\":" << jstr(out.lane.name)
      << ",\"freed\":" << (out.freed ? "true" : "false")
      << ",\"reason\":" << jstr(out.reason)
      << ",\"holder\":" << (out.holder.empty() ? "null" : jstr(out.holder))
      << ",\"holder_pid\":" << (out.holderPid > 0 ? std::to_string(out.holderPid) : "null")
      << ",\"released\":[";
    for (std::size_t i = 0; i < out.freedResources.size(); ++i)
        o << (i ? "," : "") << jstr(out.freedResources[i]);
    o << "],\"resources\":[";
    for (std::size_t i = 0; i < out.snapshot.resources.size(); ++i)
        o << (i ? "," : "") << "{" << lane::renderResource(out.snapshot.resources[i]) << "}";
    o << "],\"seats\":{\"host\":{\"total\":" << out.snapshot.seatsTotal
      << ",\"used\":" << out.snapshot.seatsUsed << "}}}";
    return o.str();
}

}

HTTP_ASYNC_HANDLER(api::Manager, ReleaseResource)
{
    const std::string resource = api::queryValue(req, "resource");
    const std::string name = api::queryValue(req, "lane");
    lane::Store::instance().releaseResource(resource, name, [respond](lane::Store::Outcome out) {
        http::Response resp;
        resp.status = out.status;
        resp.body = out.status >= 400 ? lane::renderError(out) : renderFreed(out);
        respond(std::move(resp));
    });
}
