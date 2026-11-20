#include "prompt.h"

#include <filesystem>
#include <fstream>
#include <sstream>
#include <system_error>

#include <app/catalog/store.h>
#include <app/manifest/store.h>

namespace {

std::string inLane(const std::string& path, const std::string& worktree)
{
    const manifest::Store::Data& m = manifest::Store::instance().data();
    const std::string& base = m.gitRoot.empty() ? m.root : m.gitRoot;

    if (path.empty() || base.empty() || worktree.empty()) return path;
    if (path.size() <= base.size()) return path;
    if (path.compare(0, base.size(), base) != 0) return path;
    if (path[base.size()] != '/') return path;
    return worktree + path.substr(base.size());
}

}

std::string api::prompt::ticketPathInLane(const std::string& ticketPath,
                                          const std::string& worktree)
{
    return inLane(ticketPath, worktree);
}

std::vector<std::pair<std::string, std::string>> api::prompt::values(
    const std::string& ticketId, const std::string& ticketPath,
    const std::string& worktree, const std::string& triggeredByRun)
{
    const manifest::Store::Data& m = manifest::Store::instance().data();
    return {{"ticket_id", ticketId},
            {"ticket_path", inLane(ticketPath, worktree)},
            {"repo_root", worktree.empty() ? m.root : worktree},
            {"triggered_by_run", triggeredByRun}};
}

std::string api::prompt::substitute(
    const std::string& text, const std::vector<std::pair<std::string, std::string>>& values)
{
    std::string out = text;
    for (const auto& [name, value] : values) {
        const std::string token = "{" + name + "}";
        for (std::size_t at = out.find(token); at != std::string::npos;
             at = out.find(token, at + value.size()))
            out.replace(at, token.size(), value);
    }
    return out;
}

std::string api::prompt::trim(const std::string& text)
{
    const std::size_t first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    const std::size_t last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1);
}

bool api::prompt::read(const std::string& path, std::string& out)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::ostringstream ss;
    ss << in.rdbuf();
    out = ss.str();
    return true;
}

api::prompt::Read api::prompt::readFor(const std::string& promptPath,
                                       const std::string& ticketId,
                                       const std::string& worktree)
{
    Read out;
    out.found = read(promptPath, out.text);
    catalog::Document doc;
    if (catalog::Store::instance().ticketById(ticketId, doc)) out.ticketPath = doc.path;
    out.inLane = ticketPathInLane(out.ticketPath, worktree);
    if (!out.inLane.empty()) {
        std::error_code ec;
        out.reachable = std::filesystem::exists(std::filesystem::path(out.inLane), ec) && !ec;
    }
    return out;
}
