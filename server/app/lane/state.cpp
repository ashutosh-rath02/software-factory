#include "state.h"

#include <atomic>
#include <chrono>
#include <ctime>

namespace {

std::atomic<long long> g_offsetMs {0};

long long realMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

}

long long lane::state::nowMs() { return realMs() + g_offsetMs.load(std::memory_order_relaxed); }

void lane::state::advanceMs(long long deltaMs)
{
    g_offsetMs.fetch_add(deltaMs, std::memory_order_relaxed);
}

long long lane::state::offsetMs() { return g_offsetMs.load(std::memory_order_relaxed); }

std::string lane::state::isoOf(long long ms)
{
    const std::time_t seconds = static_cast<std::time_t>(ms / 1000);
    std::tm tm {};
    gmtime_r(&seconds, &tm);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return buf;
}

namespace {

std::string processOf(const lane::state::Inputs& in)
{
    if (in.worktreeMissing) return "crashed";
    if (in.queued) return "queued";
    if (in.live && in.blocked) return "blocked";
    if (in.live) return (in.turnActive || !in.turnEnded) ? "running" : "idle";
    if (in.outcome == "crashed") return "crashed";
    if (in.outcome == "killed by you") return "killed";
    if (in.outcome == "ended cleanly" || in.outcome == "completed") return "finished";
    return "not_started";
}

}

lane::state::Axes lane::state::axesOf(const Inputs& in)
{
    Axes out;
    out.process = processOf(in);
    out.evidence = in.evidence;

    if (in.live) {
        out.motion.sinceMs = in.lastOutputMs;
        const long long quietMs = in.now - in.lastOutputMs;
        if (!in.blocked && in.lastOutputMs > 0 && quietMs > SILENCE_MS) {
            out.motion.value = "silent";
            out.motion.silentForS = quietMs / 1000;
        } else {
            out.motion.value = "producing";
        }
    }

    out.alarm = (out.process == "running" && out.motion.value == "silent")
             || (out.process == "finished" && out.evidence == "claimed_unproven");

    if (out.process != "running" && out.process != "queued" && out.process != "blocked") {
        out.stopped.present = true;
        out.stopped.sinceMs = in.lastOutputMs;
        out.stopped.seconds = in.lastOutputMs > 0 && in.now > in.lastOutputMs
                                ? (in.now - in.lastOutputMs) / 1000 : 0;
        if (out.process == "not_started")      out.stopped.reason = "never_started";
        else if (in.delivery == "done")        out.stopped.reason = "work_landed";
        else if (out.process == "crashed")     out.stopped.reason = "crashed";
        else if (in.delivery == "unshipped")   out.stopped.reason = "holds_work";
        else                                   out.stopped.reason = "ended";
    }

    const bool wantsNothing = in.delivery == "done";

    out.needsYou = !wantsNothing && (out.process == "idle"
                || out.process == "blocked"
                || out.process == "crashed"
                || (out.process == "finished" && out.evidence == "claimed_unproven")
                || (out.process == "running" && out.motion.value == "silent"));
    return out;
}

long long lane::state::silenceDeadlineMs(const Inputs& in)
{
    if (!in.live || in.blocked || in.lastOutputMs <= 0) return 0;
    return in.lastOutputMs + SILENCE_MS;
}
