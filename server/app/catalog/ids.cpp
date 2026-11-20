#include "ids.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>

#include <fcntl.h>
#include <unistd.h>

#include <app/manifest/json.h>
#include <app/manifest/store.h>
#include <glog/logging.h>

#include "store.h"

std::string catalog::Ids::numbered(const std::string& prefix, long long number)
{
    std::ostringstream o;
    o << prefix << "-";
    const std::string digits = std::to_string(number);
    for (std::size_t i = digits.size(); i < 3; ++i) o << '0';
    o << digits;
    return o.str();
}

bool catalog::Ids::writeLedger(const std::string& path, const std::string& body,
                               std::string& error)
{
    const std::string tmp = path + ".tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        if (!out) { error = "cannot write " + tmp; return false; }
        out << body;
        out.flush();
        if (!out) { error = "cannot write " + tmp; return false; }
    }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) {
        std::remove(tmp.c_str());
        error = "cannot replace " + path;
        return false;
    }
    return true;
}

void catalog::Ids::init()
{
    path_ = manifest::Store::instance().data().idsPath;
    if (path_.empty()) return;

    std::ifstream in(path_, std::ios::binary);
    if (!in) return;
    std::ostringstream text;
    text << in.rdbuf();

    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(text.str(), err);
    const manifest::Json* series = err.ok ? body.find("series") : nullptr;
    if (!series || !series->isObject()) {
        LOG(WARNING) << "ids: " << path_ << " is not readable as a ledger, so the sequence "
                     << "restarts from the highest id on disk";
        return;
    }
    for (const auto& [name, value] : series->members())
        if (value.isInteger()) spent_[name] = static_cast<long long>(value.number());
}

void catalog::Ids::start()
{
    LOG(INFO) << "ids: " << spent_.size() << " series carried over from " << path_;
}

void catalog::Ids::stop()
{
    queue_.post([this] { stopping_ = true; });
}

bool catalog::Ids::readLedger(const std::string& path, std::map<std::string, long long>& out)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::ostringstream text;
    text << in.rdbuf();

    manifest::Json::Error err;
    const manifest::Json body = manifest::Json::parse(text.str(), err);
    const manifest::Json* series = err.ok ? body.find("series") : nullptr;
    if (!series || !series->isObject()) return false;
    for (const auto& [name, value] : series->members())
        if (value.isInteger()) out[name] = static_cast<long long>(value.number());
    return true;
}

catalog::Ids::Held catalog::Ids::hold(const std::string& path)
{
    Held held;
    if (path.empty()) {
        held.error = "no id ledger path";
        return held;
    }
    held.path = path + ".lock";
    for (int tries = 0; tries < LOCK_TRIES; ++tries) {
        const int fd = ::open(held.path.c_str(), O_CREAT | O_EXCL | O_WRONLY, 0600);
        if (fd >= 0) {
            ::close(fd);
            held.ok = true;
            return held;
        }
        if (errno != EEXIST) {
            held.error = "the id ledger's lock at " + held.path + " could not be taken: "
                       + std::strerror(errno);
            held.path.clear();
            return held;
        }
        ::usleep(LOCK_WAIT_US);
    }
    held.error = "the id ledger's lock at " + held.path + " is held by another factory and did "
                 "not free within " + std::to_string(LOCK_TRIES * LOCK_WAIT_US / 1000) + "ms";
    held.path.clear();
    return held;
}

void catalog::Ids::release(const Held& held)
{
    if (!held.path.empty()) ::remove(held.path.c_str());
}

void catalog::Ids::allocate(const std::string& prefix, Done done)
{
    const std::string path = path_;
    queue_.offload(
        [prefix, path] {
            Allocation out;
            out.prefix = prefix;

            const Store::IdSurvey survey = catalog::Store::instance().surveyIds();
            if (!std::binary_search(survey.prefixes.begin(), survey.prefixes.end(), prefix)) {
                out.known = survey.prefixes;
                return out;
            }
            out.series = Store::seriesOf(prefix);

            const Held held = hold(path);
            if (!held.ok) {
                out.writeError = held.error;
                return out;
            }

            std::map<std::string, long long> spent;
            readLedger(path, spent);

            const auto onDisk = survey.highest.find(out.series);
            long long mark = onDisk == survey.highest.end() ? 0 : onDisk->second;
            const auto recorded = spent.find(out.series);
            if (recorded != spent.end()) mark = std::max(mark, recorded->second);

            out.number = mark + 1;
            out.id = numbered(prefix, out.number);
            spent[out.series] = out.number;

            std::string error;
            const bool wrote = writeLedger(path, ledgerOf(spent), error);
            release(held);
            if (!wrote) {
                out.writeError = error.empty() ? "the id ledger could not be written" : error;
                LOG(ERROR) << "ids: " << out.writeError;
                return out;
            }
            out.ok = true;
            return out;
        },
        [this, done](Allocation out) {
            if (out.ok) spent_[out.series] = out.number;
            done(out);
        });
}

std::string catalog::Ids::ledgerOf(const std::map<std::string, long long>& spent)
{
    std::ostringstream o;
    o << "{\n  \"version\": 1,\n"
      << "  \"note\": \"ticket ids this tool has handed out, by series (SPEC.md E26). "
         "The tool's own record, not the repository's: the high-water mark comes from the "
         "documents on disk and this only remembers what was reserved and never written.\",\n"
      << "  \"series\": {";
    bool first = true;
    for (const auto& [series, number] : spent) {
        o << (first ? "\n" : ",\n");
        first = false;
        o << "    \"" << manifest::jsonEscape(series) << "\": " << number;
    }
    o << (first ? "" : "\n  ") << "}\n}\n";
    return o.str();
}
