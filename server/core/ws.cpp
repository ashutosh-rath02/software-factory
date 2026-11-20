#include "ws.h"

#include <vector>

#include <boost/asio/ip/tcp.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <glog/logging.h>

#include <core/http.h>
#include <core/queue-impl.h>

namespace beast = boost::beast;
namespace websocket = beast::websocket;

struct ws::Connection::Impl {
    websocket::stream<boost::asio::ip::tcp::socket> stream;
    core::Strand& strand;
    beast::flat_buffer readBuf;
    std::vector<std::shared_ptr<const std::string>> outbox;
    bool writing = false;
    bool open = false;
    std::chrono::steady_clock::time_point lastHeard = std::chrono::steady_clock::now();
    OnText onText;

    Impl(boost::asio::ip::tcp::socket socket, core::Queue& queue)
        : stream(std::move(socket)), strand(core::strandOf(queue)) {}
};

ws::Connection::Connection() = default;

ws::Connection::~Connection() = default;

namespace ws {

struct Upgrade {
    static Connection::pointer make(boost::asio::ip::tcp::socket socket, core::Queue& queue,
                                    const std::string& serverName)
    {
        auto conn = Connection::pointer(new Connection());
        conn->impl_ = std::make_unique<Connection::Impl>(std::move(socket), queue);
        conn->impl_->stream.set_option(websocket::stream_base::decorator(
            [serverName](websocket::response_type& res) {
                res.set(beast::http::field::server, serverName);
            }));
        return conn;
    }
};

}

void ws::serve(const std::string& path, core::Queue& queue, const std::string& serverName,
               std::function<void(Connection::pointer, std::string)> onConnection)
{
    http::Server::instance().upgrade(
        path, [&queue, serverName, onConnection = std::move(onConnection)](
                  std::shared_ptr<boost::asio::ip::tcp::socket> sock, std::string request) {
            onConnection(ws::Upgrade::make(std::move(*sock), queue, serverName), std::move(request));
        });
}

void ws::Connection::start(std::string request, OnOpen onOpen, OnText onText)
{
    Impl* impl = impl_.get();
    impl->onText = std::move(onText);
    auto self = shared_from_this();
    auto held = std::make_shared<std::string>(std::move(request));

    impl->stream.async_accept(boost::asio::buffer(*held), boost::asio::bind_executor(
        impl->strand, [this, self, held, onOpen = std::move(onOpen)](beast::error_code ec) {
            if (ec) {
                LOG(WARNING) << "ws accept: " << ec.message();
                return;
            }
            impl_->stream.text(true);
            impl_->open = true;
            impl_->lastHeard = std::chrono::steady_clock::now();

            auto read = std::make_shared<std::function<void()>>();
            *read = [this, self, read] {
                impl_->stream.async_read(impl_->readBuf, boost::asio::bind_executor(
                    impl_->strand, [this, self, read](beast::error_code ec, std::size_t) {
                        if (ec) { impl_->open = false; return; }
                        impl_->lastHeard = std::chrono::steady_clock::now();
                        const std::string text = beast::buffers_to_string(impl_->readBuf.data());
                        impl_->readBuf.consume(impl_->readBuf.size());
                        if (impl_->onText) impl_->onText(text);
                        (*read)();
                    }));
            };
            (*read)();

            if (onOpen) onOpen();
        }));
}

void ws::Connection::send(const std::string& text)
{
    Impl* impl = impl_.get();
    if (!impl->open) return;
    impl->outbox.push_back(std::make_shared<const std::string>(text));
    if (impl->writing) return;

    auto self = shared_from_this();
    auto write = std::make_shared<std::function<void()>>();
    *write = [this, self, write] {
        if (impl_->outbox.empty()) { impl_->writing = false; return; }
        impl_->writing = true;
        auto msg = impl_->outbox.front();
        impl_->stream.async_write(boost::asio::buffer(*msg), boost::asio::bind_executor(
            impl_->strand, [this, self, msg, write](beast::error_code ec, std::size_t) {
                if (ec) { impl_->open = false; impl_->writing = false; return; }
                impl_->outbox.erase(impl_->outbox.begin());
                (*write)();
            }));
    };
    (*write)();
}

void ws::Connection::close()
{
    impl_->open = false;
    beast::error_code ec;
    impl_->stream.next_layer().close(ec);
}

bool ws::Connection::open() const { return impl_->open; }

std::chrono::steady_clock::time_point ws::Connection::lastHeard() const
{
    return impl_->lastHeard;
}
