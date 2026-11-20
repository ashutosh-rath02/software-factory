#include "file.h"

#include <sys/inotify.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <map>
#include <set>

#include <boost/asio/bind_executor.hpp>
#include <boost/asio/buffer.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/posix/stream_descriptor.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/asio/strand.hpp>
#include <glog/logging.h>

#include <core/engine.h>
#include <core/worker.h>

namespace fs = std::filesystem;

namespace {

constexpr uint32_t MASK = IN_CREATE | IN_CLOSE_WRITE | IN_MOVED_TO | IN_MOVED_FROM | IN_DELETE
                        | IN_DELETE_SELF | IN_MOVE_SELF | IN_EXCL_UNLINK;

constexpr int RETRY_MS = 1000;
constexpr int POLL_MS = 250;

bool statEntry(const std::string& path, long long& mtime, unsigned long long& size)
{
    struct stat st {};
    if (::stat(path.c_str(), &st) != 0) return false;
    if (!S_ISREG(st.st_mode)) return false;
    mtime = static_cast<long long>(st.st_mtim.tv_sec) * 1000000000LL + st.st_mtim.tv_nsec;
    size = static_cast<unsigned long long>(st.st_size);
    return true;
}

}

struct file::Watch::Impl {
    using Strand = boost::asio::strand<boost::asio::io_context::executor_type>;

    struct Stamp {
        long long mtime = 0;
        unsigned long long size = 0;
        std::string tag;
    };

    struct Registration {
        Id id = 0;
        std::string dir;
        int wd = -1;
        core::Queue* queue = nullptr;
        Handler handler;
        Accept accept;
        Enrich enrich;

        std::map<std::string, Stamp> stamps;
        bool primed = false;
        bool running = false;
        bool wantsFull = false;
        std::set<std::string> dirty;
    };

    struct Pass {
        Id id = 0;
        std::string dir;
        bool full = false;
        std::vector<std::string> paths;
        Accept accept;
        Enrich enrich;
        std::map<std::string, Stamp> previous;
        bool primed = false;

        std::map<std::string, Stamp> stamps;
        std::vector<std::string> gone;
        std::vector<Change> changes;
    };

    void read();
    void parse(std::size_t bytes);
    void kick(Registration& reg);
    void adopt(std::shared_ptr<Pass> pass);
    void arm(Registration& reg);
    void retimeRetry();
    static void runPass(Pass& pass);

    int fd = -1;
    std::unique_ptr<boost::asio::posix::stream_descriptor> desc;
    std::unique_ptr<Strand> strand;
    std::unique_ptr<boost::asio::steady_timer> retry;
    std::vector<char> buf;

    std::map<Id, Registration> regs;
    std::map<int, Id> byWd;
    Id nextId = 1;
    bool started = false;
    bool stopping = false;
    bool retryArmed = false;
};

const char* file::name(Change::Kind kind)
{
    switch (kind) {
        case Change::Kind::Created:  return "created";
        case Change::Kind::Modified: return "modified";
        case Change::Kind::Deleted:  return "deleted";
    }
    return "modified";
}

file::Watch::Watch() : impl_(std::make_unique<Impl>()) {}

file::Watch::~Watch() = default;

bool file::Watch::available() const { return impl_->fd >= 0; }

void file::Watch::init()
{
    auto& io = core::Engine::instance().service();
    impl_->strand = std::make_unique<Impl::Strand>(boost::asio::make_strand(io));
    impl_->retry = std::make_unique<boost::asio::steady_timer>(io);
    impl_->buf.resize(64 * 1024);

    impl_->fd = ::inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
    if (impl_->fd < 0) {
        LOG(WARNING) << "inotify unavailable (" << std::strerror(errno)
                     << "); directories will be polled every " << POLL_MS << " ms";
        return;
    }
    impl_->desc = std::make_unique<boost::asio::posix::stream_descriptor>(io, impl_->fd);
}

void file::Watch::start()
{
    Impl* impl = impl_.get();
    impl->started = true;
    boost::asio::post(*impl->strand, [impl] {
        for (auto& [id, reg] : impl->regs) {
            reg.wantsFull = true;
            impl->kick(reg);
        }
        if (impl->desc) impl->read();
        impl->retimeRetry();
    });
}

void file::Watch::stop()
{
    Impl* impl = impl_.get();
    if (!impl->strand) return;
    boost::asio::post(*impl->strand, [impl] {
        impl->stopping = true;
        if (impl->retry) impl->retry->cancel();
        boost::system::error_code ec;
        if (impl->desc) impl->desc->close(ec);
    });
}

file::Id file::Watch::add(const std::string& dir, core::Queue& queue, Handler handler,
                          Accept accept, Enrich enrich)
{
    if (impl_->started)
        LOG(FATAL) << "file::Watch::add(" << dir << ") after start(): core::Engine::start() runs "
                      "every start() concurrently with the pool, so this mutates a registry a "
                      "pool thread is already reading. Register in init().";

    const Id id = impl_->nextId++;
    Impl::Registration reg;
    reg.id = id;
    reg.dir = dir;
    reg.queue = &queue;
    reg.handler = std::move(handler);
    reg.accept = std::move(accept);
    reg.enrich = std::move(enrich);
    reg.wantsFull = true;
    impl_->regs[id] = std::move(reg);
    impl_->arm(impl_->regs[id]);
    return id;
}

void file::Watch::remove(Id id)
{
    Impl* impl = impl_.get();
    if (!impl->strand) return;
    boost::asio::post(*impl->strand, [impl, id] {
        auto it = impl->regs.find(id);
        if (it == impl->regs.end()) return;
        if (it->second.wd >= 0) {
            impl->byWd.erase(it->second.wd);
            if (impl->fd >= 0) ::inotify_rm_watch(impl->fd, it->second.wd);
        }
        impl->regs.erase(it);
    });
}

void file::Watch::Impl::arm(Registration& reg)
{
    if (fd < 0 || reg.wd >= 0 || reg.dir.empty()) return;
    const int wd = ::inotify_add_watch(fd, reg.dir.c_str(), MASK);
    if (wd < 0) {
        VLOG(1) << "file::Watch cannot watch " << reg.dir << " yet: " << std::strerror(errno);
        return;
    }
    reg.wd = wd;
    byWd[wd] = reg.id;
    reg.wantsFull = true;
}

void file::Watch::Impl::retimeRetry()
{
    if (stopping || !retry || retryArmed) return;
    const bool needed = fd < 0
        || std::any_of(regs.begin(), regs.end(),
                       [](const auto& kv) { return kv.second.wd < 0; });
    if (!needed) return;

    retryArmed = true;
    retry->expires_after(std::chrono::milliseconds(fd < 0 ? POLL_MS : RETRY_MS));
    retry->async_wait(boost::asio::bind_executor(*strand,
        [this](const boost::system::error_code& ec) {
            retryArmed = false;
            if (ec || stopping) return;
            for (auto& [id, reg] : regs) {
                arm(reg);
                if (fd < 0 || reg.wd < 0) reg.wantsFull = true;
                kick(reg);
            }
            retimeRetry();
        }));
}

void file::Watch::Impl::read()
{
    if (stopping || !desc) return;
    desc->async_read_some(boost::asio::buffer(buf), boost::asio::bind_executor(*strand,
        [this](const boost::system::error_code& ec, std::size_t bytes) {
            if (ec) {
                if (ec != boost::asio::error::operation_aborted && !stopping)
                    LOG(ERROR) << "file::Watch read failed: " << ec.message();
                return;
            }
            parse(bytes);
            read();
        }));
}

void file::Watch::Impl::parse(std::size_t bytes)
{
    std::size_t off = 0;
    while (off + sizeof(struct inotify_event) <= bytes) {
        const auto* ev = reinterpret_cast<const struct inotify_event*>(buf.data() + off);
        off += sizeof(struct inotify_event) + ev->len;

        if (ev->mask & IN_Q_OVERFLOW) {
            LOG(WARNING) << "file::Watch queue overflowed; re-reading every watched directory";
            for (auto& [id, reg] : regs) reg.wantsFull = true;
            continue;
        }

        auto found = byWd.find(ev->wd);
        if (found == byWd.end()) continue;
        auto entry = regs.find(found->second);
        if (entry == regs.end()) continue;
        Registration& reg = entry->second;

        if (ev->mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_UNMOUNT | IN_IGNORED)) {
            byWd.erase(ev->wd);
            reg.wd = -1;
            reg.wantsFull = true;
            continue;
        }
        if (ev->len && ev->name[0]) reg.dirty.insert(reg.dir + "/" + ev->name);
    }

    for (auto& [id, reg] : regs) kick(reg);
    retimeRetry();
}

void file::Watch::Impl::kick(Registration& reg)
{
    if (stopping || reg.running) return;
    if (!reg.wantsFull && reg.dirty.empty()) return;

    auto pass = std::make_shared<Pass>();
    pass->id = reg.id;
    pass->dir = reg.dir;
    pass->full = reg.wantsFull;
    pass->accept = reg.accept;
    pass->enrich = reg.enrich;
    pass->primed = reg.primed;
    if (pass->full) {
        pass->previous = reg.stamps;
    } else {
        pass->paths.assign(reg.dirty.begin(), reg.dirty.end());
        for (const auto& path : pass->paths) {
            auto known = reg.stamps.find(path);
            if (known != reg.stamps.end()) pass->previous[path] = known->second;
        }
    }

    reg.running = true;
    reg.wantsFull = false;
    reg.dirty.clear();

    core::Worker::instance().post([this, pass] {
        try {
            runPass(*pass);
        } catch (std::exception& e) {
            LOG(ERROR) << "Watch pass error: " << e.what();
        }
        boost::asio::post(*strand, [this, pass] { adopt(pass); });
    });
}

void file::Watch::Impl::runPass(Pass& pass)
{
    auto stampOf = [&pass](const std::string& path, Stamp& out) {
        if (pass.accept && !pass.accept(path)) return false;
        return statEntry(path, out.mtime, out.size);
    };

    if (pass.full) {
        std::error_code ec;
        for (auto it = fs::directory_iterator(fs::path(pass.dir), ec);
             !ec && it != fs::directory_iterator(); it.increment(ec)) {
            const std::string path = it->path().string();
            Stamp s;
            if (stampOf(path, s)) pass.stamps[path] = s;
        }
    } else {
        for (const auto& path : pass.paths) {
            Stamp s;
            if (stampOf(path, s)) pass.stamps[path] = s;
            else pass.gone.push_back(path);
        }
    }

    for (auto& [path, stamp] : pass.stamps) {
        auto old = pass.previous.find(path);
        if (old == pass.previous.end()) {
            stamp.tag = pass.enrich ? pass.enrich(path) : std::string();
            if (pass.primed)
                pass.changes.push_back({Change::Kind::Created, pass.dir, path, stamp.tag});
        } else if (old->second.mtime != stamp.mtime || old->second.size != stamp.size) {
            stamp.tag = pass.enrich ? pass.enrich(path) : std::string();
            pass.changes.push_back({Change::Kind::Modified, pass.dir, path, stamp.tag});
        } else {
            stamp.tag = old->second.tag;
        }
    }

    if (pass.full) {
        for (const auto& [path, stamp] : pass.previous)
            if (pass.stamps.find(path) == pass.stamps.end())
                pass.changes.push_back({Change::Kind::Deleted, pass.dir, path, stamp.tag});
    } else {
        for (const auto& path : pass.gone) {
            auto old = pass.previous.find(path);
            if (old == pass.previous.end()) continue;
            pass.changes.push_back({Change::Kind::Deleted, pass.dir, path, old->second.tag});
        }
    }
}

void file::Watch::Impl::adopt(std::shared_ptr<Pass> pass)
{
    auto it = regs.find(pass->id);
    if (it == regs.end()) return;
    Registration& reg = it->second;
    reg.running = false;

    if (pass->full) {
        reg.stamps = std::move(pass->stamps);
    } else {
        for (auto& [path, stamp] : pass->stamps) reg.stamps[path] = stamp;
        for (const auto& path : pass->gone) reg.stamps.erase(path);
    }
    reg.primed = true;

    if (!stopping) {
        core::Queue* queue = reg.queue;
        Handler handler = reg.handler;
        for (const auto& change : pass->changes)
            queue->post([handler, change] { handler(change); });
    }

    kick(reg);
}

void file::Watcher::watch(const std::string& dir, core::Queue& queue, Accept accept,
                          Enrich enrich)
{
    void* self = self_;
    Thunk thunk = thunk_;
    ids_.push_back(Watch::instance().add(
        dir, queue, [self, thunk](const Change& change) { thunk(self, change); },
        std::move(accept), std::move(enrich)));
}

void file::Watcher::stop()
{
    for (Id id : ids_) Watch::instance().remove(id);
    ids_.clear();
}
