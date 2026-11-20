#pragma once

#include <functional>
#include <map>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/queue.h>

COMPONENT(catalog, Ids)
    struct Allocation {
        bool ok = false;
        std::string id;
        std::string prefix;
        std::string series;
        long long number = 0;
        std::vector<std::string> known;
        std::string writeError;
    };

    void init() override;
    void start() override;
    void stop() override;

    using Done = std::function<void(Allocation)>;
    void allocate(const std::string& prefix, Done done);

private:
    struct Held {
        bool ok = false;
        std::string path;
        std::string error;
    };

    static constexpr int LOCK_TRIES = 200;
    static constexpr int LOCK_WAIT_US = 10000;

    static Held hold(const std::string& path);
    static void release(const Held& held);
    static bool readLedger(const std::string& path, std::map<std::string, long long>& out);
    static std::string ledgerOf(const std::map<std::string, long long>& spent);
    static std::string numbered(const std::string& prefix, long long number);
    static bool writeLedger(const std::string& path, const std::string& body, std::string& error);

    std::string path_;
    core::Queue queue_;
    std::map<std::string, long long> spent_;
    bool stopping_ = false;
COMPONENT_END
