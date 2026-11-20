#include "manager.h"

#include <cstdlib>
#include <sstream>
#include <vector>

#include <app/manifest/json.h>
#include <app/permissions/store.h>
#include <core/http.h>
#include <glog/logging.h>

void api::Manager::registerPermissionSubscriberHook_()
{
    const char* on = std::getenv("FACTORY_TEST_HOOKS");
    if (!on || !*on) return;

    LOG(WARNING) << "FACTORY_TEST_HOOKS is set: GET /api/_test/permission-subscribers "
                    "is registered and a second subscriber joins permissions.request";

    static std::vector<std::string> seen;

    permissions::Store::instance().onRequest(
        [](const std::string& lane, const std::string&, const std::string& payload) {
            manifest::Json::Error err;
            const manifest::Json body = manifest::Json::parse("{" + payload + "}", err);
            const manifest::Json* id = err.ok ? body.find("request_id") : nullptr;
            seen.push_back(lane + "/" + (id && id->isString() ? id->str() : std::string()));
        });

    http::Server::instance().route("GET", "/api/_test/permission-subscribers",
        [](const http::Request&, http::Response& resp) {
            std::ostringstream o;
            o << "{\"subscribers\":" << permissions::Store::instance().requestSubscribers()
              << ",\"seen\":[";
            for (std::size_t i = 0; i < seen.size(); ++i) {
                if (i) o << ",";
                o << "\"" << manifest::jsonEscape(seen[i]) << "\"";
            }
            o << "]}";
            resp.status = 200;
            resp.body = o.str();
        });
}
