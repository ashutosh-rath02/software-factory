#include "worker.h"
#include <core/engine.h>
#include <glog/logging.h>

void core::Worker::init()
{
    const char* v = std::getenv("WORKER_THREADS");
    if (v) poolSize_ = std::max(1, std::atoi(v));
    LOG(INFO) << "Worker pool initialized (" << poolSize_ << " threads)";
}

void core::Worker::start()
{
    work_ = std::make_unique<boost::asio::io_context::work>(workerIo_);
    for (std::size_t i = 0; i < poolSize_; ++i) {
        threads_.emplace_back([this] {
            while (true) {
                try {
                    workerIo_.run();
                    break;
                } catch (std::exception& e) {
                    LOG(ERROR) << "Worker exception: " << e.what();
                }
            }
        });
    }
    LOG(INFO) << "Worker pool started (" << poolSize_ << " threads)";
}

void core::Worker::stop()
{
    work_.reset();
    workerIo_.stop();
    for (auto& t : threads_) {
        if (t.joinable()) t.join();
    }
    threads_.clear();
    LOG(INFO) << "Worker pool stopped";
}

void core::Worker::post(std::function<void()> task)
{
    workerIo_.post([task = std::move(task)] {
        try {
            task();
        } catch (std::exception& e) {
            LOG(ERROR) << "Worker task error: " << e.what();
        }
    });
}

void core::Worker::postSerialized(std::function<void()> task)
{
    boost::asio::post(serialStrand_, [task = std::move(task)] {
        try {
            task();
        } catch (std::exception& e) {
            LOG(ERROR) << "Worker task error: " << e.what();
        }
    });
}

void core::Worker::post(std::function<void()> task, std::function<void()> callback)
{
    auto& engineIo = Engine::instance().service();
    workerIo_.post([task = std::move(task), callback = std::move(callback), &engineIo]() {
        try {
            task();
        } catch (std::exception& e) {
            LOG(ERROR) << "Worker task error: " << e.what();
        }
        boost::asio::post(engineIo, std::move(callback));
    });
}
