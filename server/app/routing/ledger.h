#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "store.h"

namespace routing {

struct Receipt {
    int index = 0;
    std::string which;
    std::string command;
    std::string commandId;
    std::string source;
    std::string sourceAfter;
    int exitCode = -1;
    std::string capturePath;
    std::string captureSha256;
    std::size_t bytes = 0;
    bool accepted = false;
    bool reused = false;
    bool complete = true;
};

struct Checkpoint {
    std::string id;
    std::string cause;
    std::size_t receiptCount = 0;
};

struct Record {
    Store::Context context;
    Store::Request request;
    std::string source;
    std::string state = "running";
    std::string action = "wait";
    std::string reason = "command_running";
    std::size_t nextIndex = 0;
    std::vector<Receipt> receipts;
    Checkpoint checkpoint;
    std::uint64_t ordinal = 0;
    bool pending = false;
    std::string pendingCommandId;
};

bool validId(const std::string& value);
bool validSource(const std::string& value);
bool accepted(const Store::Step& step, int code);
std::string planJson(const Store::Request& request);
std::string laneFolder(const Store::Context& context);
std::string recordPath(const Record& record);
std::string capturePath(const Record& record, int index);
std::string captureBytes(const Receipt& receipt);
std::string render(const Record& record, bool includeOutput);
std::string serialize(const Record& record);
Record readRecord(const Store::Context& context, const std::string& requestId);
std::vector<Record> loadRecords(const Store::Context& context);
void validate(const Record& record);
void saveRecord(const Record& record);
void saveCapture(const Receipt& receipt, const std::string& bytes);
Store::Answer refusal(int status, const std::string& code, const std::string& message,
                     const Store::Context& context, const std::string& detail = {},
                     const std::string& remedy = {});

}
