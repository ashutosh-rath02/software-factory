#include "stream.h"

#include <boost/asio/buffer.hpp>
#include <boost/asio/posix/stream_descriptor.hpp>
#include <boost/asio/write.hpp>

#include <core/queue-impl.h>

struct core::Stream::Impl {
    Queue& queue;
    boost::asio::posix::stream_descriptor fd;

    Impl(Queue& q, int raw) : queue(q), fd(strandOf(q), raw) {}
};

core::Stream::Stream(Queue& queue, int fd) : impl_(std::make_unique<Impl>(queue, fd)) {}

core::Stream::~Stream() { close(); }

void core::Stream::write(std::string data, std::function<void(bool)> done)
{
    Impl* impl = impl_.get();
    auto chunk = std::make_shared<std::string>(std::move(data));
    boost::asio::async_write(
        impl->fd, boost::asio::buffer(*chunk),
        [impl, chunk, done = std::move(done)](const boost::system::error_code& ec,
                                              std::size_t) mutable {
            done(!ec);
        });
}

void core::Stream::read(char* into, std::size_t max,
                        std::function<void(bool, std::size_t)> done)
{
    Impl* impl = impl_.get();
    impl->fd.async_read_some(
        boost::asio::buffer(into, max),
        [impl, done = std::move(done)](const boost::system::error_code& ec,
                                       std::size_t n) mutable {
            done(!ec, n);
        });
}

void core::Stream::close()
{
    boost::system::error_code ignored;
    impl_->fd.close(ignored);
}
