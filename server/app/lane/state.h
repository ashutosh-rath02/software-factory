#pragma once

#include <string>

namespace lane::state {

constexpr long long SILENCE_MS = 120000;

long long nowMs();
void advanceMs(long long deltaMs);
long long offsetMs();

std::string isoOf(long long ms);

struct Motion {
    std::string value;
    long long silentForS = 0;
    long long sinceMs = 0;
};

struct Inputs {
    bool worktreeMissing = false;
    std::string delivery;
    bool queued = false;
    bool live = false;
    bool blocked = false;
    bool turnActive = false;
    bool turnEnded = false;
    std::string outcome;
    long long lastOutputMs = 0;
    std::string evidence = "nothing_claimed";
    long long now = 0;
};

struct Stopped {
    bool present = false;
    std::string reason;      ///< work_landed · verdict_failed · holds_work · never_started · ended
    long long sinceMs = 0;
    long long seconds = 0;
};

struct Axes {
    std::string process;
    Motion motion;
    std::string evidence;
    bool alarm = false;
    bool needsYou = false;
    Stopped stopped;
};

Axes axesOf(const Inputs& in);

long long silenceDeadlineMs(const Inputs& in);
}
