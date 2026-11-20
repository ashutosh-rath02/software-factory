#pragma once

#include <cstdint>
#include <functional>
#include <memory>

#include <core/queue.h>

namespace core {

class Task {
public:
    Task(Queue& queue, std::function<void()> handler);
    ~Task();
    Task(const Task&) = delete;
    Task& operator=(const Task&) = delete;
    Task(Task&&) noexcept;
    Task& operator=(Task&&) noexcept;

    void start(uint32_t interval, int32_t loop = 1);
    void stop();
    bool running() const;

private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};

}
