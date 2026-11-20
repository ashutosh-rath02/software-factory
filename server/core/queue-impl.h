#pragma once

#include <boost/asio/io_context.hpp>
#include <boost/asio/strand.hpp>

#include <core/queue.h>

namespace core {

using Strand = boost::asio::strand<boost::asio::io_context::executor_type>;

struct Queue::Impl {
    Strand strand;
    explicit Impl(boost::asio::io_context& io) : strand(boost::asio::make_strand(io)) {}
};

struct QueueAccess {
    static Strand& strand(Queue& queue) { return queue.impl_->strand; }
};

inline Strand& strandOf(Queue& queue) { return QueueAccess::strand(queue); }

}
