#include "engine.h"
#include <glog/logging.h>
#include <thread>
#include <vector>

core::Engine::Engine()
    : signals_(service_)
    , drainTimer_(service_)
    , cleanupTimer_(service_)
    , poolSize_([]() -> std::size_t {
        const char* v = std::getenv("ENGINE_THREADS");
        return v ? std::max(1, std::atoi(v)) : 4;
    }())
    , startTime_(std::chrono::steady_clock::now())
{
    signals_.add(SIGINT);
    signals_.add(SIGTERM);
    signals_.async_wait([this](auto, auto) { stop(); });
}

void core::Engine::start()
{
    for (auto& c : components_) {
        try {
            c->init();
        } catch (std::exception& e) {
            LOG(FATAL) << "Component init failed: " << e.what();
        }
    }

    std::vector<std::thread> threads;
    for (std::size_t i = 0; i < poolSize_; ++i)
        threads.emplace_back([this] { execute_(); });

    for (auto& c : components_) c->start();

    if (!cleanupCallbacks_.empty()) startCleanupTimer_();

    for (auto& t : threads) t.join();
}

void core::Engine::execute_()
{
    while (true) {
        try {
            service_.run();
            break;
        } catch (std::exception& ex) {
            LOG(ERROR) << "Exception: " << ex.what();
        }
    }
}

void core::Engine::stop()
{
    if (draining_) {
        LOG(INFO) << "Force shutdown (second signal)";
        forceStop_();
        return;
    }
    LOG(INFO) << "Graceful shutdown — draining connections (10s)...";
    draining_ = true;
    drain_();
}

void core::Engine::drain_()
{
    drainTimer_.expires_after(std::chrono::seconds(10));
    drainTimer_.async_wait([this](boost::system::error_code ec) {
        if (ec) return;
        LOG(INFO) << "Drain timeout — forcing shutdown";
        forceStop_();
    });

    signals_.async_wait([this](auto, auto) {
        LOG(INFO) << "Force shutdown (second signal)";
        drainTimer_.cancel();
        forceStop_();
    });
}

void core::Engine::startCleanupTimer_()
{
    cleanupTimer_.expires_after(std::chrono::hours(1));
    cleanupTimer_.async_wait([this](boost::system::error_code ec) {
        if (ec) return;
        LOG(INFO) << "Running periodic cleanup...";
        for (auto& cb : cleanupCallbacks_) {
            try { cb(); } catch (std::exception& e) {
                LOG(ERROR) << "Cleanup error: " << e.what();
            }
        }
        startCleanupTimer_();
    });
}

void core::Engine::forceStop_()
{
    LOG(INFO) << "Shutting down...";
    cleanupTimer_.cancel();
    for (auto& c : components_) c->stop();
    service_.stop();
}
