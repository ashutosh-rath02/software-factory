#pragma once

#include <cstddef>
#include <deque>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace manifest { class Json; }

namespace api {

std::string nowIso();

struct Scope {
    enum class Kind { None, Requirements, Ticket, Lane };

    Kind kind = Kind::None;
    std::string id;

    bool valid() const { return kind != Kind::None; }
    std::string key() const;
    std::string json() const;
};

Scope parseScope(const manifest::Json* scope);

std::vector<std::string> catalogChangeScopes(const std::string& kind, const std::string& id);

std::vector<std::string> resourceScopes(const std::vector<std::string>& lanes);

std::vector<std::string> isolationScopes(const std::string& lane);

std::vector<std::string> commandScopes(const std::string& lane, const std::string& ticketId);

std::vector<std::string> runScopes(const std::string& lane, const std::string& ticketId);

std::vector<std::string> laneStateScopes(const std::string& lane, const std::string& ticketId);

std::vector<std::string> permissionScopes(const std::string& lane, const std::string& ticketId);

class SequenceLog {
public:
    struct Rendered {
        std::string frame;
        std::string entry;
    };

    void setCapacity(std::size_t capacity) { capacity_ = capacity ? capacity : 1; }

    bool beginRun(const std::string& lane, const std::string& runId);

    long long append(const std::string& lane, const std::string& runId,
                     const std::function<Rendered(long long)>& render);

    void appendAt(const std::string& lane, const std::string& runId, long long seq,
                  const std::function<Rendered(long long)>& render);

    struct Resume {
        bool coverable = false;
        std::vector<std::string> frames;
        long long lastSeq = 0;
    };

    Resume resume(const std::string& lane, long long afterSeq) const;

    long long lastSeq(const std::string& lane) const;

    std::string snapshotData(const std::string& lane) const;

private:
    struct Frame { long long seq; long long order; Rendered rendered; };
    struct Lane {
        long long last = 0;
        long long order = 0;
        std::string runId;
        std::deque<Frame> frames;
    };

    std::map<std::string, Lane> lanes_;
    std::size_t capacity_ = 200;
};

}
