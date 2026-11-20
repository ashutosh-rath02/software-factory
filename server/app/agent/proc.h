#pragma once

#include <string>

namespace agent::proc {

enum class State {
    Gone,
    Zombie,
    Alive
};

State state(int pid);

bool signalGroup(int pid, int sig);

int signalGroupMembers(int pid, int sig);

bool openStdinChannel(const std::string& path, int& readFd, int& writeFd);

void removeStdinChannel(const std::string& path);

}
