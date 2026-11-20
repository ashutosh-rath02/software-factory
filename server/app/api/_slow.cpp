#include "manager.h"
#include <thread>
#include <chrono>
#include <core/worker.h>

HTTP_HANDLER(api::Manager, Slow)
{
    resp.body = "{\"slow\":true}";
    core::Worker::instance().post([] {
        std::this_thread::sleep_for(std::chrono::seconds(2));
    });
}
