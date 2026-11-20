#pragma once

#include <functional>
#include <optional>
#include <string>

#include "store.h"

namespace lane::command {

struct Event {
    std::string lane;
    std::string ticketId;
    std::string commandId;
    std::string which;
    std::string chunk;
    int exitCode = 0;
    bool exited = false;
};

using Emit = std::function<void(const Event&)>;

struct Started {
    int status = 202;
    std::string lane;
    std::string commandId;
    std::string which;
    std::string command;

    std::string errorCode;
    std::string errorRemedy;
    std::string errorMessage;
    std::string errorDetail;
};

void start(const std::string& lane, const std::string& which, Emit emit,
           std::function<void(Started)> done, std::optional<Store::Record> expected = {});

std::string renderStarted(const Started& started);
std::string renderError(const Started& started);
std::string renderEvent(const Event& event);

}
