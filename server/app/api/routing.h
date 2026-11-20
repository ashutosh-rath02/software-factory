#pragma once

#include <core/http.h>
#include <app/routing/store.h>
#include <app/lane/store.h>
#include <app/manifest/json.h>

namespace api {

class Routing {
public:
    static void submit(const http::Request& req, http::Responder respond);
    static void get(const http::Request& req, http::Responder respond);
    static std::string incarnation(const lane::Store::Record& record);

private:
    static http::Response error(int status, const std::string& code,
                                const std::string& message, const std::string& detail);
    static bool requestId(const std::string& value);
    static bool fields(const manifest::Json& body, const std::vector<std::string>& allowed);
    static bool parse(const std::string& body, routing::Store::Request& request);
    static void context(const std::string& lane,
                         std::function<void(routing::Store::Context, http::Response)> done);
    static routing::Store::SnapshotFn snapshot();
    static routing::Store::ExecuteFn execute();
    static routing::Store::Done response(http::Responder respond);
};

}
