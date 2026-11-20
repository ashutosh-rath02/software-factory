#include "task.h"

#include <memory>

#include <boost/asio/deadline_timer.hpp>
#include <boost/date_time/posix_time/posix_time_types.hpp>
#include <glog/logging.h>

#include <core/queue-impl.h>

struct core::Task::Impl : std::enable_shared_from_this<Impl> {
    std::function<void()> handler;
    boost::asio::deadline_timer timer;
    uint32_t interval = 0;
    int32_t loop = 0;
    bool stopping = false;
    unsigned long long generation = 0;

    Impl(Queue& queue, std::function<void()> h)
        : handler(std::move(h)), timer(strandOf(queue)) {}

    void scheduleNext()
    {
        if (stopping || loop == 0) return;
        const unsigned long long mine = ++generation;
        timer.expires_from_now(boost::posix_time::milliseconds(interval));
        auto self = shared_from_this();
        timer.async_wait([self, mine](const boost::system::error_code& ec) {
            self->handleTimeout(ec, mine);
        });
    }

    void handleTimeout(const boost::system::error_code& ec, unsigned long long mine)
    {
        if (ec) {
            if (ec != boost::asio::error::operation_aborted)
                DLOG(ERROR) << "Task timer error: " << ec.message();
            return;
        }
        if (stopping || loop == 0 || mine != generation) return;

        if (loop > 0) --loop;
        try {
            handler();
        } catch (const std::exception& ex) {
            DLOG(ERROR) << "Task handler exception: " << ex.what();
        }
        if (loop != 0 && mine == generation) scheduleNext();
    }
};

core::Task::Task(Queue& queue, std::function<void()> handler)
    : impl_(std::make_shared<Impl>(queue, std::move(handler))) {}

core::Task::Task(Task&&) noexcept = default;

core::Task& core::Task::operator=(Task&& other) noexcept
{
    if (this != &other) {
        stop();
        impl_ = std::move(other.impl_);
    }
    return *this;
}

core::Task::~Task() { stop(); }

void core::Task::start(uint32_t interval, int32_t loop)
{
    if (running()) {
        DLOG(WARNING) << "Task already running, stopping first";
        stop();
    }
    impl_->interval = interval;
    impl_->loop = loop;
    impl_->stopping = false;
    impl_->scheduleNext();
}

void core::Task::stop()
{
    if (!impl_) return;
    impl_->stopping = true;
    impl_->loop = 0;
    ++impl_->generation;
    boost::system::error_code ec;
    impl_->timer.cancel(ec);
    if (ec) DLOG(WARNING) << "Timer cancel error: " << ec.message();
}

bool core::Task::running() const { return impl_ && impl_->loop != 0; }
