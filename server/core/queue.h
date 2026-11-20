#pragma once

#include <functional>
#include <memory>
#include <type_traits>
#include <utility>

namespace core {

class Queue {
public:
    Queue();
    ~Queue();
    Queue(const Queue&) = delete;
    Queue& operator=(const Queue&) = delete;

    void post(std::function<void()> work);

    template <class F, class G>
    void offload(F work, G done)
    {
        using R = std::invoke_result_t<F>;
        if constexpr (std::is_void_v<R>) {
            offloadErased(std::move(work), std::move(done));
        } else {
            auto slot = std::make_shared<R>();
            offloadErased(
                [slot, work = std::move(work)]() mutable { *slot = work(); },
                [slot, done = std::move(done)]() mutable { done(std::move(*slot)); });
        }
    }

    struct Impl;

private:
    void offloadErased(std::function<void()> work, std::function<void()> done);

    std::unique_ptr<Impl> impl_;
    friend struct QueueAccess;
};

}
