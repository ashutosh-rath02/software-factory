#include "queue.h"

#include <boost/asio/post.hpp>

#include <core/engine.h>
#include <core/queue-impl.h>
#include <core/worker.h>

core::Queue::Queue() : impl_(std::make_unique<Impl>(Engine::instance().service())) {}

core::Queue::~Queue() = default;

void core::Queue::post(std::function<void()> work)
{
    boost::asio::post(strandOf(*this), std::move(work));
}

void core::Queue::offloadErased(std::function<void()> work, std::function<void()> done)
{
    Worker::instance().post(std::move(work), [this, done = std::move(done)]() mutable {
        boost::asio::post(strandOf(*this), std::move(done));
    });
}
