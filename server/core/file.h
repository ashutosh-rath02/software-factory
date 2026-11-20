#pragma once

#include <functional>
#include <memory>
#include <string>
#include <vector>

#include <core/component.h>
#include <core/queue.h>

namespace file {

struct Change {
    enum class Kind { Created, Modified, Deleted };

    Kind kind = Kind::Modified;
    std::string dir;
    std::string path;
    std::string tag;
};

const char* name(Change::Kind kind);

using Handler = std::function<void(const Change&)>;
using Accept = std::function<bool(const std::string& path)>;
using Enrich = std::function<std::string(const std::string& path)>;
using Id = unsigned long long;

class Watch : public core::Component {
public:
    static Watch& instance() { static Watch _instance; return _instance; }
    ~Watch() override;

    void init() override;
    void start() override;
    void stop() override;

    Id add(const std::string& dir, core::Queue& queue, Handler handler,
           Accept accept = {}, Enrich enrich = {});
    void remove(Id id);

    bool available() const;

private:
    Watch();

    struct Impl;
    std::unique_ptr<Impl> impl_;
};

class Watcher {
public:
    using Thunk = void (*)(void*, const Change&);

    Watcher(void* self, Thunk thunk) : self_(self), thunk_(thunk) {}
    Watcher(const Watcher&) = delete;
    Watcher& operator=(const Watcher&) = delete;
    ~Watcher() { stop(); }

    void watch(const std::string& dir, core::Queue& queue, Accept accept = {},
               Enrich enrich = {});
    void stop();

private:
    void* self_;
    Thunk thunk_;
    std::vector<Id> ids_;
};

}

#define FILE_WATCH(name) \
private: \
    void handle##name##_(const file::Change& change); \
    file::Watcher name##Watch_ { this, [](void* self, const file::Change& change) { \
        static_cast<CLASS*>(self)->handle##name##_(change); \
    } };

#define FILE_HANDLER(cls, name) \
    void cls::handle##name##_(const file::Change& change)
