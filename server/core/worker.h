#pragma once

#include <functional>
#include <vector>
#include <thread>
#include <memory>
#include <boost/asio.hpp>
#include <core/component.h>

namespace core {

class Queue;

class Worker : public Component {
public:
    static Worker& instance() { static Worker w; return w; }
    virtual ~Worker() {}

    void init() override;
    void start() override;
    void stop() override;

    void post(std::function<void()> task);
    void postSerialized(std::function<void()> task);

private:
    friend class Queue;

    void post(std::function<void()> task, std::function<void()> callback);

    Worker() = default;

    boost::asio::io_context workerIo_;
    boost::asio::strand<boost::asio::io_context::executor_type> serialStrand_{
        boost::asio::make_strand(workerIo_)};
    std::unique_ptr<boost::asio::executor_work_guard<boost::asio::io_context::executor_type>> work_;
    std::vector<std::thread> threads_;
    std::size_t poolSize_ = 2;
};

}
