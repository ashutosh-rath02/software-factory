#pragma once

#include <list>
#include <vector>
#include <chrono>
#include <functional>
#include <boost/asio.hpp>
#include <core/component.h>

namespace core {

// ── The threading contract ───────────────────────────────────────────────────────────────
//
// A description of what this engine already does, written next to the code it constrains.
// `core/` is vendored into other products, and every reader so far inferred the model from
// the code and inferred it wrong: twelve files in this repository say "the engine thread",
// singular, and hang the rule "never add a mutex to event-driven code" on it — a rule that is
// only sound under a model this engine does not implement. That cost a reverted ticket and an
// escape. A contract that lives in a report is a contract the next product does not read.
//
// THREADS. start() spawns a POOL on ONE io_context: four threads, or `ENGINE_THREADS` if it
// is set. There is no "the engine thread" — two handlers can be executing at the same instant
// on different threads of that pool, and asio serialises nothing on your behalf. core::Worker
// runs two further threads (`WORKER_THREADS`) on an io_context of its own.
//
// LIFECYCLE — which thread each call arrives on, which is the question a component actually
// has to answer:
//
//   init()   every component in registration order, on the main thread, BEFORE a single pool
//            thread exists. This is the only single-threaded window in the process: state
//            written here and never written again needs no executor, and a strand a component
//            intends to own is made here.
//   start()  every component in registration order, on the main thread, AFTER the pool is
//            already running. It is concurrent with live handlers — arming a timer or opening
//            an acceptor here is already a shared write.
//   stop()   every component in registration order, SYNCHRONOUSLY, on whichever pool thread
//            ran the SIGINT/SIGTERM handler or the drain timer, while every other pool thread
//            is still executing handlers. io_context::stop() follows the last one immediately,
//            so work a stop() posts may never run — at process exit the OS closes what is left.
//
// THE RULE THAT STANDS ON THAT. State reached from more than one handler belongs on a strand,
// and never a mutex: a mutex blocks an io thread in order to protect state, and blocking one
// of four stalls everything queued behind it. A component that owns an asio object — an
// acceptor, a timer, a socket; "distinct objects safe, shared objects unsafe" — gives that
// object a strand, lets its handlers run there, and POSTS its stop() there. http::Server,
// catalog::Store and api::Socket each do exactly that. Engine cannot do it on a component's
// behalf: a post onto this io_context is not serialised with a handler already running on
// another of its threads, so only the component's own executor can serialise its own state.
class Engine {
public:
    void start();
    void stop();

    template<class C>
    void registerComponent() {
        components_.push_back(&C::instance());
    }

    using CleanupCallback = std::function<void()>;
    void registerCleanup(CleanupCallback cb) { cleanupCallbacks_.push_back(std::move(cb)); }

    static Engine& instance() {
        static Engine engine;
        return engine;
    }

    boost::asio::io_context& service() { return service_; }
    uint64_t uptimeSeconds() const {
        return std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::steady_clock::now() - startTime_).count();
    }

private:
    Engine();
    void execute_();
    void drain_();
    void forceStop_();
    void startCleanupTimer_();

    boost::asio::io_context service_;
    boost::asio::signal_set signals_;
    boost::asio::steady_timer drainTimer_;
    boost::asio::steady_timer cleanupTimer_;
    std::size_t poolSize_;
    std::list<Component*> components_;
    std::vector<CleanupCallback> cleanupCallbacks_;
    std::chrono::steady_clock::time_point startTime_;
    bool draining_ = false;
};

}
