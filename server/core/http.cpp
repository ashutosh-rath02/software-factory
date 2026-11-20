#include "http.h"
#include <core/net.h>
#include <config.h>
#include <core/engine.h>
#include <glog/logging.h>
#include <sstream>
#include <chrono>
#include <cctype>

void http::Server::init()
{
    // init() is the one single-threaded window in the process — see the threading contract in
    // core/engine.h — so the strand is made here and read from handlers afterwards.
    acceptStrand_ = std::make_unique<boost::asio::strand<boost::asio::io_context::executor_type>>(
        boost::asio::make_strand(core::Engine::instance().service()));
    LOG(INFO) << "HTTP initialized";
}

void http::Server::start()
{
    using tcp = boost::asio::ip::tcp;
    acceptor_ = std::make_unique<tcp::acceptor>(*acceptStrand_,
                                                core::bindEndpoint(Config::HTTP_PORT));
    accept_();
    LOG(INFO) << "HTTP listening on :" << Config::HTTP_PORT;
}

void http::Server::stop()
{
    // core::Engine calls this on whichever pool thread took the signal, while the accept
    // handler is running on another. Closing here is the write that raced with it; it belongs
    // on the executor the acceptor lives on, like every other touch of that object.
    if (!acceptStrand_) return;
    boost::asio::post(*acceptStrand_, [this] {
        if (acceptor_) acceptor_->close();
    });
}

namespace {

std::vector<std::string> splitPath(const std::string& path)
{
    std::vector<std::string> out;
    std::size_t i = 0;
    while (i < path.size()) {
        while (i < path.size() && path[i] == '/') ++i;
        std::size_t start = i;
        while (i < path.size() && path[i] != '/') ++i;
        if (i > start) out.push_back(path.substr(start, i - start));
    }
    return out;
}

}

void http::Server::addRoute_(Route route)
{
    route.segments = splitPath(route.path);
    for (auto& s : route.segments)
        if (s.size() > 2 && s.front() == '{' && s.back() == '}') route.parameterised = true;
    routes_.push_back(std::move(route));
}

// Literal segments must be equal; a `{name}` segment matches one segment and binds it. Nothing
// matches across a `/`, so `/api/tickets/{id}` and `/api/tickets/{id}/report` are different
// routes rather than one route with a suffix to pick apart.
bool http::Server::matches_(const Route& route, Request& req)
{
    if (route.method != req.method) return false;
    if (!route.parameterised) return route.path == req.path;

    auto actual = splitPath(req.path);
    if (actual.size() != route.segments.size()) return false;
    for (std::size_t i = 0; i < actual.size(); ++i) {
        const std::string& want = route.segments[i];
        if (want.size() > 2 && want.front() == '{' && want.back() == '}') continue;
        if (want != actual[i]) return false;
    }
    for (std::size_t i = 0; i < actual.size(); ++i) {
        const std::string& want = route.segments[i];
        if (want.size() > 2 && want.front() == '{' && want.back() == '}')
            req.params[want.substr(1, want.size() - 2)] = actual[i];
    }
    return true;
}

void http::Server::route(const std::string& method, const std::string& path, Handler handler)
{
    addRoute_({method, path, {}, false, std::move(handler), nullptr});
}

void http::Server::routeAsync(const std::string& method, const std::string& path,
                              AsyncHandler handler)
{
    addRoute_({method, path, {}, false, nullptr, std::move(handler)});
}

void http::Server::upgrade(const std::string& path, UpgradeHandler handler)
{
    upgrades_.push_back({path, std::move(handler)});
}

// Does this look like a protocol upgrade for a path someone claimed? If so the socket is handed
// over whole — request bytes included — and this server never touches it again.
bool http::Server::tryUpgrade_(std::shared_ptr<boost::asio::ip::tcp::socket> sock,
                                const std::string& raw, size_t headerEnd)
{
    if (upgrades_.empty()) return false;

    std::string head = raw.substr(0, headerEnd);
    std::string lower = head;
    for (auto& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (lower.find("upgrade: websocket") == std::string::npos) return false;

    // Request line: METHOD SP path SP HTTP/1.1
    auto lineEnd = head.find("\r\n");
    auto first = head.find(' ');
    if (first == std::string::npos || lineEnd == std::string::npos) return false;
    auto second = head.find(' ', first + 1);
    if (second == std::string::npos || second > lineEnd) return false;
    std::string path = head.substr(first + 1, second - first - 1);
    auto q = path.find('?');
    if (q != std::string::npos) path = path.substr(0, q);

    for (auto& u : upgrades_) {
        if (u.path == path) {
            u.handler(sock, raw);
            return true;
        }
    }

    // An upgrade for a path nobody claimed is refused, not silently parsed as a GET. A client
    // that asked for a socket and received a JSON body has to guess what went wrong.
    auto out = std::make_shared<std::string>(
        "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    boost::asio::async_write(*sock, boost::asio::buffer(*out), [sock, out](auto, auto) {});
    return true;
}

void http::Server::accept_()
{
    auto& io = core::Engine::instance().service();
    auto sock = std::make_shared<boost::asio::ip::tcp::socket>(io);
    acceptor_->async_accept(*sock, [this, sock](boost::system::error_code ec) {
        if (!ec) handleConnection_(sock);
        if (acceptor_->is_open()) accept_();
    });
}

void http::Server::handleConnection_(std::shared_ptr<boost::asio::ip::tcp::socket> sock)
{
    auto buf = std::make_shared<std::vector<char>>(8192);
    sock->async_read_some(boost::asio::buffer(buf->data(), buf->size()),
        [this, sock, buf](boost::system::error_code ec, size_t n) {
            if (ec) {
                LOG(WARNING) << "HTTP read error: " << ec.message();
                return;
            }

            std::string headerData(buf->data(), n);

            auto headerEnd = headerData.find("\r\n\r\n");
            if (headerEnd == std::string::npos) {
                LOG(WARNING) << "HTTP: incomplete headers";
                auto out = std::make_shared<std::string>(
                    "HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                boost::asio::async_write(*sock, boost::asio::buffer(*out),
                    [sock, out](auto, auto) {});
                return;
            }

            if (tryUpgrade_(sock, headerData, headerEnd)) return;

            size_t contentLength = 0;
            auto clPos = headerData.find("Content-Length: ");
            if (clPos == std::string::npos) clPos = headerData.find("content-length: ");
            if (clPos != std::string::npos) {
                try { contentLength = std::stoul(headerData.substr(clPos + 16, 20)); }
                catch (...) { contentLength = 0; }
            }

            if (contentLength > MAX_BODY_SIZE) {
                LOG(WARNING) << "HTTP: body too large (" << contentLength << " bytes)";
                auto out = std::make_shared<std::string>(
                    "HTTP/1.1 413 Payload Too Large\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                boost::asio::async_write(*sock, boost::asio::buffer(*out),
                    [sock, out](auto, auto) {});
                return;
            }

            size_t bodyStart = headerEnd + 4;
            size_t bodyReceived = (n > bodyStart) ? n - bodyStart : 0;
            size_t bodyRemaining = (contentLength > bodyReceived) ? contentLength - bodyReceived : 0;

            if (bodyRemaining == 0) {
                processRequest_(sock, headerData);
            } else {
                auto fullData = std::make_shared<std::string>(headerData);
                fullData->reserve(bodyStart + contentLength);
                readBody_(sock, fullData, bodyRemaining);
            }
        });
}

void http::Server::readBody_(std::shared_ptr<boost::asio::ip::tcp::socket> sock,
                              std::shared_ptr<std::string> data,
                              size_t remaining)
{
    auto buf = std::make_shared<std::vector<char>>(std::min(remaining, (size_t)65536));
    sock->async_read_some(boost::asio::buffer(buf->data(), buf->size()),
        [this, sock, data, buf, remaining](boost::system::error_code ec, size_t n) {
            if (ec) return;
            data->append(buf->data(), n);
            size_t left = (remaining > n) ? remaining - n : 0;
            if (left == 0) {
                processRequest_(sock, *data);
            } else {
                readBody_(sock, data, left);
            }
        });
}

void http::Server::processRequest_(std::shared_ptr<boost::asio::ip::tcp::socket> sock,
                                    const std::string& raw)
{
    auto start = std::chrono::steady_clock::now();
    auto req = parseRequest_(raw);

    Response resp;
    if (req.method == "OPTIONS") {
        resp.status = 204;
        resp.body = "";
        write_(sock, req, resp, start);
        return;
    }

    const Route* hit = nullptr;
    for (auto& r : routes_) {
        if (matches_(r, req)) { hit = &r; break; }
    }

    if (!hit) {
        resp.status = 404;
        resp.body = R"({"error":"not found"})";
        write_(sock, req, resp, start);
        return;
    }

    if (hit->async) {
        // The request outlives this frame, so it is copied; the responder may be called from a
        // worker thread, so it hops back to the io_context before touching the socket.
        auto held = std::make_shared<Request>(req);
        auto& io = core::Engine::instance().service();
        Responder respond = [this, sock, held, start, &io](Response late) {
            boost::asio::post(io, [this, sock, held, start, late = std::move(late)]() mutable {
                write_(sock, *held, late, start);
            });
        };
        try {
            hit->async(*held, respond);
        } catch (std::exception& e) {
            LOG(ERROR) << "Handler error: " << e.what();
            respond(Response{500, R"({"error":"internal server error"})", "application/json"});
        } catch (...) {
            LOG(ERROR) << "Handler unknown error";
            respond(Response{500, R"({"error":"internal server error"})", "application/json"});
        }
        return;
    }

    try {
        hit->handler(req, resp);
    } catch (std::exception& e) {
        LOG(ERROR) << "Handler error: " << e.what();
        resp.status = 500;
        resp.body = R"({"error":"internal server error"})";
    } catch (...) {
        LOG(ERROR) << "Handler unknown error";
        resp.status = 500;
        resp.body = R"({"error":"internal server error"})";
    }
    write_(sock, req, resp, start);
}

void http::Server::write_(std::shared_ptr<boost::asio::ip::tcp::socket> sock, const Request& req,
                          const Response& resp, std::chrono::steady_clock::time_point start)
{
    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start).count();
    LOG(INFO) << req.method << " " << req.path << " " << resp.status << " " << elapsed << "ms";

    auto out = std::make_shared<std::string>(buildResponse_(resp));
    boost::asio::async_write(*sock, boost::asio::buffer(*out),
        [sock, out](boost::system::error_code, size_t) {});
}

http::Request http::Server::parseRequest_(const std::string& raw)
{
    Request req;
    std::istringstream ss(raw);
    ss >> req.method >> req.path;

    auto qpos = req.path.find('?');
    if (qpos != std::string::npos) {
        std::string query = req.path.substr(qpos + 1);
        std::istringstream qs(query);
        std::string param;
        while (std::getline(qs, param, '&')) {
            auto eq = param.find('=');
            if (eq != std::string::npos)
                req.params[param.substr(0, eq)] = param.substr(eq + 1);
        }
        req.path = req.path.substr(0, qpos);   // the query is in params; routing is on the path
    }

    std::string line;
    std::getline(ss, line);
    while (std::getline(ss, line) && line != "\r" && !line.empty()) {
        if (line.back() == '\r') line.pop_back();
        auto pos = line.find(": ");
        if (pos != std::string::npos)
            req.headers[line.substr(0, pos)] = line.substr(pos + 2);
    }

    auto bodyPos = raw.find("\r\n\r\n");
    if (bodyPos != std::string::npos)
        req.body = raw.substr(bodyPos + 4);

    return req;
}

std::string http::Server::buildResponse_(const Response& resp)
{
    std::ostringstream ss;
    ss << "HTTP/1.1 " << resp.status << " OK\r\n"
       << "Content-Type: " << resp.contentType << "\r\n"
       << "Content-Length: " << resp.body.size() << "\r\n"
       << "Access-Control-Allow-Origin: " << Config::CORS_ORIGIN << "\r\n"
       << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
       << "Access-Control-Allow-Headers: Content-Type, Authorization\r\n"
       << "X-Content-Type-Options: nosniff\r\n"
       << "X-Frame-Options: DENY\r\n"
       << "X-XSS-Protection: 1; mode=block\r\n"
       << "Connection: close\r\n"
       << "\r\n"
       << resp.body;
    return ss.str();
}
