#pragma once

#include <functional>
#include <memory>
#include <cstddef>
#include <string>

#include <core/queue.h>

namespace core {

class Stream {
public:
    Stream(Queue& queue, int fd);
    ~Stream();
    Stream(const Stream&) = delete;
    Stream& operator=(const Stream&) = delete;

    void write(std::string data, std::function<void(bool)> done);
    void read(char* into, std::size_t max,
              std::function<void(bool ok, std::size_t n)> done);
    void close();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}
