#pragma once

#include <string>
#include <functional>
#include <map>
#include <vector>
#include <chrono>
#include <memory>
#include <boost/asio.hpp>
#include <core/component.h>

namespace http {

struct Request {
    std::string method;
    std::string path;
    std::string body;
    std::map<std::string, std::string> headers;
    std::map<std::string, std::string> params;
};

struct Response {
    int status = 200;
    std::string body;
    std::string contentType = "application/json";
};

using Handler = std::function<void(const Request&, Response&)>;

// An action that cannot answer from memory answers later.
//
// SPEC.md §10 and every ticket's §2 say the engine thread is never blocked, and this server's
// job is reading a repository's documents off a disk it does not own. A synchronous handler has
// nowhere to put that read except the io thread, so the shape that keeps the rule has to exist
// in the router: the handler is handed a `Responder`, posts its work to a core::Worker, and the
// response is written whenever it comes back. Calling the responder from another thread is the
// normal case and is safe — it hops to the io_context before it touches the socket.
using Responder = std::function<void(Response)>;
using AsyncHandler = std::function<void(const Request&, Responder)>;

// A protocol upgrade hands the connection over and stops being HTTP's problem.
//
// This exists so a WebSocket lives at a path on the one port rather than on a port of its own.
// SPEC.md §6 says "one socket, /ws" — a path — and a second listener would be a second thing to
// secure, a second thing the client's dev proxy has to know about, and a second origin for the
// browser. The handler receives the socket and the request bytes already read, which is exactly
// what Beast's async_accept overload wants.
using UpgradeHandler =
    std::function<void(std::shared_ptr<boost::asio::ip::tcp::socket>, std::string)>;

class Server : public core::Component {
public:
    static constexpr size_t MAX_BODY_SIZE = 20 * 1024 * 1024;

    virtual ~Server() {}
    static Server& instance() { static Server _instance; return _instance; }

    void init() override;
    void start() override;
    void stop() override;

    // `path` may carry `{name}` segments; a match binds the segment into req.params[name].
    void route(const std::string& method, const std::string& path, Handler handler);
    void routeAsync(const std::string& method, const std::string& path, AsyncHandler handler);
    void upgrade(const std::string& path, UpgradeHandler handler);

private:
    void accept_();
    void handleConnection_(std::shared_ptr<boost::asio::ip::tcp::socket> sock);
    void readBody_(std::shared_ptr<boost::asio::ip::tcp::socket> sock,
                   std::shared_ptr<std::string> data, size_t remaining);
    void processRequest_(std::shared_ptr<boost::asio::ip::tcp::socket> sock,
                         const std::string& raw);
    Request parseRequest_(const std::string& raw);
    std::string buildResponse_(const Response& resp);

    // The acceptor is reached from two threads of one pool: the accept handler re-arms it,
    // and stop() closes it on whichever thread core::Engine took the signal on. asio's rule
    // for it is "Distinct objects: safe. Shared objects: unsafe", so it gets an executor of
    // its own — the acceptor is CONSTRUCTED on this strand, which makes every completion
    // handler asio invokes for it run there too, and stop() posts its close onto the same
    // strand instead of reaching across threads. Nothing else moves onto it: accepted
    // sockets keep the io_context, so connections still run on the whole pool.
    std::unique_ptr<boost::asio::strand<boost::asio::io_context::executor_type>> acceptStrand_;
    std::unique_ptr<boost::asio::ip::tcp::acceptor> acceptor_;

    struct Route {
        std::string method;
        std::string path;
        std::vector<std::string> segments;    // "{id}" is a binding, anything else is literal
        bool parameterised = false;
        Handler handler;
        AsyncHandler async;
    };
    std::vector<Route> routes_;
    void addRoute_(Route route);
    static bool matches_(const Route& route, Request& req);
    void write_(std::shared_ptr<boost::asio::ip::tcp::socket> sock, const Request& req,
                const Response& resp, std::chrono::steady_clock::time_point start);

    struct Upgrade { std::string path; UpgradeHandler handler; };
    std::vector<Upgrade> upgrades_;
    bool tryUpgrade_(std::shared_ptr<boost::asio::ip::tcp::socket> sock,
                     const std::string& raw, size_t headerEnd);
};

}

// ── HTTP Service Macros ──
#define HTTP_SERVICE(method, path, name) \
private: \
    struct _http_##name##_reg { \
        _http_##name##_reg(CLASS* self) { \
            http::Server::instance().route(#method, path, \
                [self](const http::Request& req, http::Response& resp) { \
                    self->handle##name##_(req, resp); \
                }); \
        } \
    } _http_##name##_reg_{this}; \
    void handle##name##_(const http::Request& req, http::Response& resp);

#define HTTP_HANDLER(cls, name) \
    void cls::handle##name##_([[maybe_unused]] const http::Request& req, http::Response& resp)

#define HTTP_ASYNC_SERVICE(method, path, name) \
private: \
    struct _http_##name##_reg { \
        _http_##name##_reg(CLASS* self) { \
            http::Server::instance().routeAsync(#method, path, \
                [self](const http::Request& req, http::Responder respond) { \
                    self->handle##name##_(req, std::move(respond)); \
                }); \
        } \
    } _http_##name##_reg_{this}; \
    void handle##name##_(const http::Request& req, http::Responder respond);

#define HTTP_ASYNC_HANDLER(cls, name) \
    void cls::handle##name##_([[maybe_unused]] const http::Request& req, http::Responder respond)
