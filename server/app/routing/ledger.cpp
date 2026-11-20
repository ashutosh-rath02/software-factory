#include "ledger.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdlib>
#include <filesystem>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <system_error>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <openssl/rand.h>

#include <app/manifest/json.h>

#include "bytes.h"

using manifest::Json;
using manifest::jstr;
namespace fs = std::filesystem;

struct RoutingFd {
    int value;
    explicit RoutingFd(int fd) : value(fd) {}
    ~RoutingFd() { if (value >= 0) ::close(value); }
    RoutingFd(const RoutingFd&) = delete;
    RoutingFd& operator=(const RoutingFd&) = delete;
    void reset(int fd) { if (value >= 0) ::close(value); value = fd; }
};

static int directory_(const fs::path& path, bool create)
{
    if (!path.is_absolute()) throw std::runtime_error("routing runtime path must be absolute");
    RoutingFd current(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
    if (current.value < 0) throw std::runtime_error("routing runtime root is unreadable");
    for (const auto& part : path.relative_path()) {
        if (part == "." || part == "..")
            throw std::runtime_error("routing runtime path is not normalized");
        int next = ::openat(current.value, part.c_str(),
                             O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (next < 0 && errno == ENOENT && create) {
            if (::mkdirat(current.value, part.c_str(), 0700) != 0 && errno != EEXIST)
                throw std::runtime_error("routing runtime directory could not be created");
            if (::fsync(current.value) != 0)
                throw std::runtime_error("routing directory could not be synchronized");
            next = ::openat(current.value, part.c_str(),
                            O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        }
        if (next < 0)
            throw std::runtime_error("routing evidence directory is not a real directory");
        current.reset(next);
    }
    const int result = current.value;
    current.value = -1;
    return result;
}

static std::string readBytes_(const std::string& path)
{
    const fs::path name(path);
    RoutingFd parent(directory_(name.parent_path(), false));
    RoutingFd fd(::openat(parent.value, name.filename().c_str(),
                          O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    if (fd.value < 0) throw std::runtime_error("could not read routing evidence: " + path);
    struct stat status {};
    if (::fstat(fd.value, &status) != 0 || !S_ISREG(status.st_mode))
        throw std::runtime_error("routing evidence is not a regular file: " + path);
    std::string output;
    char buffer[16384];
    for (;;) {
        const ssize_t count = ::read(fd.value, buffer, sizeof(buffer));
        if (count > 0) output.append(buffer, static_cast<std::size_t>(count));
        else if (count == 0) break;
        else if (errno != EINTR) {
            throw std::runtime_error("routing evidence could not be read completely: " + path);
        }
    }
    return output;
}

static void writeBytes_(const std::string& path, const std::string& bytes)
{
    const fs::path destination(path);
    RoutingFd parent(directory_(destination.parent_path(), true));
    struct stat prior {};
    const int exists = ::fstatat(parent.value, destination.filename().c_str(), &prior,
                                 AT_SYMLINK_NOFOLLOW);
    if (exists == 0 && !S_ISREG(prior.st_mode))
        throw std::runtime_error("routing destination is not a regular file");
    if (exists < 0 && errno != ENOENT)
        throw std::runtime_error("routing destination could not be inspected");
    int fd = -1;
    std::string temporary;
    static const char hex[] = "0123456789abcdef";
    for (int attempt = 0; attempt < 16; ++attempt) {
        std::array<unsigned char, 16> random {};
        if (RAND_bytes(random.data(), static_cast<int>(random.size())) != 1)
            throw std::runtime_error("routing temporary filename could not be generated");
        temporary = destination.filename().string() + ".tmp-";
        for (unsigned char byte : random) {
            temporary += hex[byte >> 4];
            temporary += hex[byte & 15];
        }
        fd = ::openat(parent.value, temporary.c_str(),
                       O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd >= 0 || errno != EEXIST) break;
    }
    if (fd < 0) throw std::runtime_error("routing intent could not be created");
    bool ok = true;
    std::size_t position = 0;
    while (position < bytes.size()) {
        const ssize_t count = ::write(fd, bytes.data() + position, bytes.size() - position);
        if (count > 0) position += static_cast<std::size_t>(count);
        else if (count < 0 && errno == EINTR) continue;
        else { ok = false; break; }
    }
    if (ok && ::fsync(fd) != 0) ok = false;
    if (::close(fd) != 0) ok = false;
    if (!ok || ::renameat(parent.value, temporary.c_str(), parent.value,
                          destination.filename().c_str()) != 0) {
        ::unlinkat(parent.value, temporary.c_str(), 0);
        throw std::runtime_error("routing intent could not be persisted");
    }
    if (::fsync(parent.value) != 0)
        throw std::runtime_error("routing directory could not be synchronized");
}

static void keys_(const Json& object, const std::set<std::string>& keys)
{
    if (!object.isObject()) throw std::runtime_error("routing journal contains a non-object");
    std::set<std::string> seen;
    for (const auto& member : object.members())
        if (!keys.count(member.first) || !seen.insert(member.first).second)
            throw std::runtime_error("routing journal contains an unknown or duplicate member");
    if (seen != keys) throw std::runtime_error("routing journal is incomplete");
}

static const Json& member_(const Json& object, const std::string& name, Json::Type type)
{
    const auto* value = object.find(name);
    if (!value || value->type() != type)
        throw std::runtime_error("routing journal has an invalid " + name);
    return *value;
}

static std::string string_(const Json& object, const std::string& name)
{
    return member_(object, name, Json::Type::String).str();
}

static std::uint64_t count_(const Json& object, const std::string& name)
{
    const auto& value = member_(object, name, Json::Type::Number);
    if (!value.isInteger() || value.number() < 0 || value.number() > 9007199254740991.0)
        throw std::runtime_error("routing journal has an invalid " + name);
    return static_cast<std::uint64_t>(value.number());
}

static int exit_(const Json& object, const std::string& name)
{
    const auto& value = member_(object, name, Json::Type::Number);
    if (!value.isInteger() || value.number() < -1 || value.number() > 255)
        throw std::runtime_error("routing journal has an invalid exit code");
    return static_cast<int>(value.number());
}

bool routing::validId(const std::string& value)
{
    return !value.empty() && value.size() <= 64
        && std::all_of(value.begin(), value.end(), [](unsigned char c) {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                || (c >= '0' && c <= '9') || c == '_' || c == '-';
        });
}

bool routing::validSource(const std::string& value)
{
    return value.size() == 64 && std::all_of(value.begin(), value.end(), [](unsigned char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}

bool routing::accepted(const Store::Step& step, int code)
{
    return std::find(step.acceptedExitCodes.begin(), step.acceptedExitCodes.end(), code)
           != step.acceptedExitCodes.end();
}

std::string routing::planJson(const Store::Request& request)
{
    std::ostringstream out;
    out << "[";
    for (std::size_t i = 0; i < request.steps.size(); ++i) {
        const auto& step = request.steps[i];
        out << (i ? "," : "") << "{\"which\":" << jstr(step.which)
            << ",\"accepted_exit_codes\":[";
        for (std::size_t j = 0; j < step.acceptedExitCodes.size(); ++j)
            out << (j ? "," : "") << step.acceptedExitCodes[j];
        out << "],\"reuse\":" << (step.reuse ? "true" : "false")
            << ",\"require_decision\":" << (step.requireDecision ? "true" : "false") << "}";
    }
    out << "]";
    return out.str();
}

std::string routing::laneFolder(const Store::Context& context)
{
    return (fs::path(context.runtimeFolder) / sha256(context.lane)).string();
}

std::string routing::recordPath(const Record& record)
{
    return (fs::path(laneFolder(record.context)) / (record.request.requestId + ".json")).string();
}

std::string routing::capturePath(const Record& record, int index)
{
    return (fs::path(laneFolder(record.context)) / "captures"
            / (record.request.requestId + "-" + std::to_string(index) + ".bin")).string();
}

std::string routing::captureBytes(const Receipt& receipt)
{
    const std::string bytes = readBytes_(receipt.capturePath);
    if (bytes.size() != receipt.bytes || sha256(bytes) != receipt.captureSha256)
        throw std::runtime_error("routing capture does not match its receipt");
    return bytes;
}

std::string routing::render(const Record& record, bool includeOutput)
{
    std::ostringstream out;
    out << "{\"request_id\":" << jstr(record.request.requestId)
        << ",\"lane\":" << jstr(record.context.lane)
        << ",\"ticket_id\":" << jstr(record.context.ticketId)
        << ",\"source\":" << jstr(record.source)
        << ",\"state\":" << jstr(record.state)
        << ",\"action\":" << jstr(record.action)
        << ",\"reason\":" << jstr(record.reason)
        << ",\"next_index\":" << record.nextIndex << ",\"receipts\":[";
    for (std::size_t i = 0; i < record.receipts.size(); ++i) {
        const auto& receipt = record.receipts[i];
        out << (i ? "," : "") << "{\"index\":" << receipt.index
            << ",\"which\":" << jstr(receipt.which)
            << ",\"command\":" << jstr(receipt.command)
            << ",\"command_id\":" << jstr(receipt.commandId)
            << ",\"source\":" << jstr(receipt.source)
            << ",\"source_after\":" << jstr(receipt.sourceAfter)
            << ",\"exit_code\":" << receipt.exitCode
            << ",\"capture_path\":" << jstr(receipt.capturePath)
            << ",\"capture_sha256\":" << jstr(receipt.captureSha256)
            << ",\"bytes\":" << receipt.bytes
            << ",\"accepted\":" << (receipt.accepted ? "true" : "false")
            << ",\"reused\":" << (receipt.reused ? "true" : "false")
            << ",\"complete\":" << (receipt.complete ? "true" : "false");
        if (includeOutput) out << ",\"output_base64\":" << jstr(base64(captureBytes(receipt)));
        out << "}";
    }
    out << "],\"checkpoint\":";
    if (record.checkpoint.id.empty()) out << "null";
    else out << "{\"id\":" << jstr(record.checkpoint.id)
             << ",\"kind\":\"repeated_error\",\"cause\":" << jstr(record.checkpoint.cause)
             << ",\"receipt_count\":" << record.checkpoint.receiptCount << "}";
    out << "}";
    return out.str();
}

std::string routing::serialize(const Record& record)
{
    const std::string body = render(record, false);
    std::ostringstream out;
    out << body.substr(0, body.size() - 1)
        << ",\"version\":1,\"workspace\":" << jstr(record.context.workspace)
        << ",\"incarnation\":" << jstr(record.context.incarnation)
        << ",\"steps\":" << planJson(record.request)
        << ",\"commands\":{";
    const char* separator = "";
    for (const auto& command : record.context.commands) {
        out << separator << jstr(command.first) << ":" << jstr(command.second);
        separator = ",";
    }
    out << "},\"ordinal\":" << record.ordinal
        << ",\"pending\":" << (record.pending ? "true" : "false")
        << ",\"pending_command_id\":" << jstr(record.pendingCommandId) << "}\n";
    return out.str();
}

routing::Record routing::readRecord(const Store::Context& context, const std::string& requestId)
{
    Record record;
    record.context = context;
    record.request.requestId = requestId;
    Json::Error error;
    const Json body = Json::parse(readBytes_(recordPath(record)), error);
    if (!error.ok) throw std::runtime_error("routing journal is malformed");
    keys_(body, {"version", "workspace", "incarnation", "steps", "commands", "ordinal",
                 "pending", "pending_command_id", "request_id", "lane", "ticket_id", "source",
                 "state", "action", "reason", "next_index", "receipts", "checkpoint"});
    if (count_(body, "version") != 1 || string_(body, "request_id") != requestId
        || string_(body, "lane") != context.lane)
        throw std::runtime_error("routing journal identity disagrees with its path");
    record.context.ticketId = string_(body, "ticket_id");
    record.context.workspace = string_(body, "workspace");
    record.context.incarnation = string_(body, "incarnation");
    record.context.commands.clear();
    for (const auto& command : member_(body, "commands", Json::Type::Object).members()) {
        if (!command.second.isString()
            || !record.context.commands.emplace(command.first, command.second.str()).second)
            throw std::runtime_error("routing journal has invalid commands");
    }
    for (const auto& item : member_(body, "steps", Json::Type::Array).items()) {
        keys_(item, {"which", "accepted_exit_codes", "reuse", "require_decision"});
        Store::Step step;
        step.which = string_(item, "which");
        for (const auto& code : member_(item, "accepted_exit_codes", Json::Type::Array).items()) {
            if (!code.isInteger() || code.number() < 0 || code.number() > 255)
                throw std::runtime_error("routing journal has an invalid exit policy");
            step.acceptedExitCodes.push_back(static_cast<int>(code.number()));
        }
        step.reuse = member_(item, "reuse", Json::Type::Bool).boolean();
        step.requireDecision = member_(item, "require_decision", Json::Type::Bool).boolean();
        record.request.steps.push_back(std::move(step));
    }
    record.source = string_(body, "source");
    record.state = string_(body, "state");
    record.action = string_(body, "action");
    record.reason = string_(body, "reason");
    record.nextIndex = count_(body, "next_index");
    record.ordinal = count_(body, "ordinal");
    record.pending = member_(body, "pending", Json::Type::Bool).boolean();
    record.pendingCommandId = string_(body, "pending_command_id");
    for (const auto& item : member_(body, "receipts", Json::Type::Array).items()) {
        keys_(item, {"index", "which", "command", "command_id", "source", "source_after",
                     "exit_code", "capture_path", "capture_sha256", "bytes", "accepted",
                     "reused", "complete"});
        Receipt receipt;
        const auto index = count_(item, "index");
        if (index > 15) throw std::runtime_error("routing receipt has an invalid index");
        receipt.index = static_cast<int>(index);
        receipt.which = string_(item, "which");
        receipt.command = string_(item, "command");
        receipt.commandId = string_(item, "command_id");
        receipt.source = string_(item, "source");
        receipt.sourceAfter = string_(item, "source_after");
        receipt.exitCode = exit_(item, "exit_code");
        receipt.capturePath = string_(item, "capture_path");
        receipt.captureSha256 = string_(item, "capture_sha256");
        receipt.bytes = count_(item, "bytes");
        receipt.accepted = member_(item, "accepted", Json::Type::Bool).boolean();
        receipt.reused = member_(item, "reused", Json::Type::Bool).boolean();
        receipt.complete = member_(item, "complete", Json::Type::Bool).boolean();
        record.receipts.push_back(std::move(receipt));
    }
    const Json* checkpoint = body.find("checkpoint");
    if (!checkpoint->isNull()) {
        keys_(*checkpoint, {"id", "kind", "cause", "receipt_count"});
        if (string_(*checkpoint, "kind") != "repeated_error")
            throw std::runtime_error("routing checkpoint kind is invalid");
        record.checkpoint.id = string_(*checkpoint, "id");
        record.checkpoint.cause = string_(*checkpoint, "cause");
        record.checkpoint.receiptCount = count_(*checkpoint, "receipt_count");
    }
    validate(record);
    return record;
}

void routing::validate(const Record& record)
{
    if (!validId(record.request.requestId) || !validSource(record.source)
        || record.request.steps.empty() || record.request.steps.size() > 16 || !record.ordinal
        || record.context.lane.empty() || record.context.incarnation.empty()
        || record.nextIndex != record.receipts.size()
        || record.receipts.size() > record.request.steps.size())
        throw std::runtime_error("routing journal identity or progress is contradictory");
    for (const auto& step : record.request.steps) {
        const std::set<int> codes(step.acceptedExitCodes.begin(), step.acceptedExitCodes.end());
        if (!record.context.commands.count(step.which) || codes.empty()
            || codes.size() != step.acceptedExitCodes.size() || *codes.begin() < 0
            || *codes.rbegin() > 255)
            throw std::runtime_error("routing journal has an invalid plan");
    }
    for (std::size_t i = 0; i < record.receipts.size(); ++i) {
        const auto& receipt = record.receipts[i];
        const auto& step = record.request.steps[i];
        const bool unknownTerminalSource = receipt.sourceAfter.empty()
            && i + 1 == record.receipts.size() && !receipt.reused
            && record.state == "needs_main" && record.action == "main"
            && record.reason == "source_unresolved";
        if (receipt.index != static_cast<int>(i) || receipt.which != step.which
            || receipt.command != record.context.commands.at(step.which)
            || receipt.commandId.empty() || receipt.source != record.source
            || (!validSource(receipt.sourceAfter) && !unknownTerminalSource)
            || !validSource(receipt.captureSha256)
            || !receipt.complete || receipt.accepted != accepted(step, receipt.exitCode)
            || receipt.capturePath != capturePath(record, receipt.index)
            || (receipt.reused && (!receipt.accepted || receipt.sourceAfter != receipt.source)))
            throw std::runtime_error("routing receipt is contradictory or incomplete");
        captureBytes(receipt);
        if (i + 1 < record.receipts.size()
            && (!receipt.accepted || receipt.sourceAfter != receipt.source || step.requireDecision))
            throw std::runtime_error("routing journal advanced through a deciding result");
    }
    if (record.state == "completed") {
        if (record.action != "main" || record.reason != "commands_completed" || record.pending
            || record.nextIndex != record.request.steps.size())
            throw std::runtime_error("routing completion is contradictory");
        for (std::size_t i = 0; i < record.receipts.size(); ++i)
            if (!record.receipts[i].accepted
                || record.receipts[i].sourceAfter != record.source
                || record.request.steps[i].requireDecision)
                throw std::runtime_error("routing completion lacks successful evidence");
    } else if (record.state == "running") {
        if (record.action != "wait" || record.nextIndex >= record.request.steps.size())
            throw std::runtime_error("routing running state is contradictory");
    } else if (record.state == "interrupted") {
        if (record.action != "main" || record.reason != "interrupted" || record.pending)
            throw std::runtime_error("routing interrupted state is contradictory");
    } else if (record.state != "needs_main"
               || (record.action != "main" && record.action != "advisor") || record.pending) {
        throw std::runtime_error("routing journal has an invalid state");
    }
    if (!record.checkpoint.id.empty()
        && (!validSource(record.checkpoint.cause) || record.checkpoint.receiptCount < 2
            || record.action != "advisor" || record.state != "needs_main"))
        throw std::runtime_error("routing checkpoint is contradictory");
    if (record.action == "advisor" && record.checkpoint.id.empty())
        throw std::runtime_error("routing advisory request has no checkpoint");
}

std::vector<routing::Record> routing::loadRecords(const Store::Context& context)
{
    std::vector<Record> records;
    const fs::path folder(laneFolder(context));
    std::error_code error;
    const auto status = fs::symlink_status(folder, error);
    if (error == std::errc::no_such_file_or_directory || (!error && !fs::exists(status)))
        return records;
    if (error || !fs::is_directory(status))
        throw std::runtime_error("routing lane journal directory is unreadable");
    RoutingFd checked(directory_(folder, false));
    for (const auto& entry : fs::directory_iterator(folder)) {
        if (entry.path().extension() != ".json") continue;
        const std::string id = entry.path().stem().string();
        if (!validId(id)) throw std::runtime_error("routing journal path is invalid");
        Record record = readRecord(context, id);
        if (record.state == "running") {
            record.state = "interrupted";
            record.action = "main";
            record.reason = "interrupted";
            record.pending = false;
            record.pendingCommandId.clear();
            saveRecord(record);
        }
        records.push_back(std::move(record));
    }
    std::sort(records.begin(), records.end(), [](const Record& a, const Record& b) {
        return a.ordinal < b.ordinal;
    });
    std::set<std::uint64_t> ordinals;
    for (const auto& record : records)
        if (!ordinals.insert(record.ordinal).second)
            throw std::runtime_error("routing journal order is contradictory");
    return records;
}

void routing::saveRecord(const Record& record)
{
    writeBytes_(recordPath(record), serialize(record));
}

void routing::saveCapture(const Receipt& receipt, const std::string& bytes)
{
    if (bytes.size() != receipt.bytes || sha256(bytes) != receipt.captureSha256)
        throw std::runtime_error("routing terminal capture is incomplete");
    writeBytes_(receipt.capturePath, bytes);
}

routing::Store::Answer routing::refusal(int status, const std::string& code,
                                       const std::string& message, const Store::Context& context,
                                       const std::string& detail, const std::string& remedy)
{
    Store::Answer answer;
    answer.status = status;
    answer.body = "{\"error\":{\"code\":" + jstr(code) + ",\"message\":" + jstr(message)
                + ",\"detail\":{" + (detail.empty() ? "\"lane\":" + jstr(context.lane) : detail)
                + "},\"remedy\":" + (remedy.empty() ? "null" : remedy) + "}}";
    return answer;
}
