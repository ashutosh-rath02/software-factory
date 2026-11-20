#include "proc.h"

#include <cctype>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace fs = std::filesystem;

agent::proc::State agent::proc::state(int pid)
{
    if (pid <= 0) return State::Gone;

    std::ifstream stat("/proc/" + std::to_string(pid) + "/stat");
    if (!stat.is_open()) return State::Gone;

    std::ostringstream buffer;
    buffer << stat.rdbuf();
    const std::string line = buffer.str();

    const std::size_t close = line.rfind(')');
    if (close == std::string::npos) return State::Gone;

    std::istringstream rest(line.substr(close + 1));
    std::string field;
    if (!(rest >> field) || field.empty()) return State::Gone;

    return field[0] == 'Z' ? State::Zombie : State::Alive;
}

bool agent::proc::signalGroup(int pid, int sig)
{
    if (pid <= 0) return false;
    return ::kill(-static_cast<pid_t>(pid), sig) == 0;
}

int agent::proc::signalGroupMembers(int pid, int sig)
{
    if (pid <= 0) return 0;

    int signalled = 0;
    std::error_code ec;
    for (const auto& entry : fs::directory_iterator("/proc", ec)) {
        const std::string name = entry.path().filename().string();
        if (name.empty() || !std::isdigit(static_cast<unsigned char>(name[0]))) continue;

        const int candidate = std::atoi(name.c_str());
        if (candidate <= 0 || candidate == pid) continue;

        std::ifstream stat(entry.path() / "stat");
        if (!stat.is_open()) continue;
        std::ostringstream buffer;
        buffer << stat.rdbuf();
        const std::string line = buffer.str();

        const std::size_t close = line.rfind(')');
        if (close == std::string::npos) continue;

        std::istringstream rest(line.substr(close + 1));
        std::string state, ppid, pgrp;
        if (!(rest >> state >> ppid >> pgrp)) continue;
        if (std::atoi(pgrp.c_str()) != pid) continue;

        if (::kill(static_cast<pid_t>(candidate), sig) == 0) ++signalled;
    }
    return signalled;
}

bool agent::proc::openStdinChannel(const std::string& path, int& readFd, int& writeFd)
{
    readFd = -1;
    writeFd = -1;
    if (path.empty()) return false;

    std::error_code ec;
    fs::create_directories(fs::path(path).parent_path(), ec);

    ::unlink(path.c_str());
    if (::mkfifo(path.c_str(), 0600) != 0) return false;

    readFd = ::open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (readFd < 0) {
        ::unlink(path.c_str());
        return false;
    }
    ::fcntl(readFd, F_SETFL, 0);

    writeFd = ::open(path.c_str(), O_WRONLY | O_CLOEXEC);
    if (writeFd < 0) {
        ::close(readFd);
        readFd = -1;
        ::unlink(path.c_str());
        return false;
    }
    return true;
}

void agent::proc::removeStdinChannel(const std::string& path)
{
    if (!path.empty()) ::unlink(path.c_str());
}
