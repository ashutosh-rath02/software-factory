#pragma once

#include <chrono>
#include <functional>
#include <memory>
#include <string>

#include <core/queue.h>

namespace ws {

class Connection : public std::enable_shared_from_this<Connection> {
public:
    using pointer = std::shared_ptr<Connection>;
    using OnText = std::function<void(const std::string& text)>;
    using OnOpen = std::function<void()>;

    ~Connection();

    void start(std::string request, OnOpen onOpen, OnText onText);
    void send(const std::string& text);
    void close();

    bool open() const;
    std::chrono::steady_clock::time_point lastHeard() const;

private:
    Connection();
    struct Impl;
    std::unique_ptr<Impl> impl_;
    friend struct Upgrade;
};

void serve(const std::string& path, core::Queue& queue, const std::string& serverName,
           std::function<void(Connection::pointer, std::string request)> onConnection);

}
