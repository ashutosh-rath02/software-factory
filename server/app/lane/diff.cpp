#include "diff.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <map>
#include <set>
#include <sstream>

#include <glog/logging.h>

#include <app/manifest/json.h>

#include "git.h"

namespace {

const std::vector<std::string> READ_ONLY = {
    "diff", "log", "ls-files", "merge-base", "rev-parse",
};

using manifest::jstr;
std::vector<std::string> lines(const std::string& text)
{
    std::vector<std::string> out;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (!line.empty()) out.push_back(line);
    }
    return out;
}

std::string unquoted(const std::string& raw)
{
    if (raw.size() < 2 || raw.front() != '"' || raw.back() != '"') return raw;
    std::string out;
    for (std::size_t i = 1; i + 1 < raw.size(); ++i) {
        if (raw[i] != '\\') { out.push_back(raw[i]); continue; }
        if (++i + 1 > raw.size()) break;
        const char c = raw[i];
        if (c >= '0' && c <= '7') {
            int value = 0, digits = 0;
            while (digits < 3 && i < raw.size() - 1 && raw[i] >= '0' && raw[i] <= '7') {
                value = value * 8 + (raw[i] - '0');
                ++i;
                ++digits;
            }
            --i;
            out.push_back(static_cast<char>(value));
            continue;
        }
        switch (c) {
            case 'n': out.push_back('\n'); break;
            case 't': out.push_back('\t'); break;
            case 'r': out.push_back('\r'); break;
            default: out.push_back(c); break;
        }
    }
    return out;
}

std::string statusWord(char letter)
{
    switch (letter) {
        case 'A': return "added";
        case 'M': return "modified";
        case 'D': return "deleted";
        case 'R': return "renamed";
        case 'C': return "copied";
        case 'T': return "typechanged";
        default: return std::string(1, static_cast<char>(std::tolower(letter)));
    }
}

struct File {
    std::string path;
    std::string status;
    int added = 0;
    int removed = 0;
    bool committed = false;
};

struct Session {
    std::string worktree;
    std::set<std::string> used;

    lane::git::Result run(const std::string& subcommand, const std::vector<std::string>& args)
    {
        lane::git::Result refused;
        if (std::find(READ_ONLY.begin(), READ_ONLY.end(), subcommand) == READ_ONLY.end()) {
            refused.out = "refused: `git " + subcommand + "` is not one of the read-only "
                          "subcommands the diff is allowed to run";
            LOG(ERROR) << "lane: diff " << refused.out;
            return refused;
        }
        std::vector<std::string> argv {"--no-optional-locks", subcommand};
        argv.insert(argv.end(), args.begin(), args.end());

        std::ostringstream shown;
        for (std::size_t i = 0; i < argv.size(); ++i) shown << (i ? " " : "") << argv[i];
        LOG(INFO) << "lane: diff git " << shown.str();

        used.insert(subcommand);
        return lane::git::runQuiet(worktree, argv);
    }
};

void numstat(const std::string& text, std::map<std::string, File>& files, bool committed)
{
    for (const auto& line : lines(text)) {
        std::istringstream in(line);
        std::string added, removed, path;
        if (!(in >> added >> removed)) continue;
        std::getline(in, path);
        while (!path.empty() && (path.front() == ' ' || path.front() == '\t')) path.erase(0, 1);
        if (path.empty()) continue;
        const std::size_t arrow = path.find(" => ");
        if (arrow != std::string::npos) path = path.substr(arrow + 4);
        path = unquoted(path);

        File& file = files[path];
        file.path = path;
        if (added != "-") file.added += std::atoi(added.c_str());
        if (removed != "-") file.removed += std::atoi(removed.c_str());
        if (committed) file.committed = true;
    }
}

void nameStatus(const std::string& text, std::map<std::string, File>& files, bool committed)
{
    for (const auto& line : lines(text)) {
        const std::size_t tab = line.find('\t');
        if (tab == std::string::npos || tab == 0) continue;
        const char letter = line[0];
        std::string path = line.substr(tab + 1);
        const std::size_t second = path.find('\t');
        if (second != std::string::npos) path = path.substr(second + 1);
        path = unquoted(path);

        File& file = files[path];
        file.path = path;
        if (committed || file.status.empty()) file.status = statusWord(letter);
        if (committed) file.committed = true;
    }
}

int addedLinesOf(const std::string& path)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) return 0;
    int lines = 0;
    bool anything = false, endedWithNewline = false, binary = false;
    char buf[4096];
    while (in.read(buf, sizeof buf) || in.gcount() > 0) {
        const std::streamsize n = in.gcount();
        for (std::streamsize i = 0; i < n; ++i) {
            anything = true;
            if (buf[i] == '\0') binary = true;
            endedWithNewline = buf[i] == '\n';
            if (buf[i] == '\n') ++lines;
        }
        if (binary) return 0;
    }
    if (anything && !endedWithNewline) ++lines;
    return lines;
}

}

const std::vector<std::string>& lane::diff::gitSubcommands() { return READ_ONLY; }

std::string lane::diff::read(const Query& query)
{
    Session g;
    g.worktree = query.worktree;

    const git::Result headResult = g.run("rev-parse", {"HEAD"});
    const std::string head = headResult.ok && !lines(headResult.out).empty()
                           ? lines(headResult.out).front() : std::string();

    std::string baseBranch;
    for (const char* candidate : {"main", "master"}) {
        if (g.run("rev-parse", {"--verify", "--quiet",
                                  std::string("refs/heads/") + candidate}).ok) {
            baseBranch = candidate;
            break;
        }
    }

    std::string base;
    if (!baseBranch.empty() && !head.empty()) {
        const git::Result merged = g.run("merge-base", {baseBranch, "HEAD"});
        if (merged.ok && !lines(merged.out).empty()) base = lines(merged.out).front();
    }

    std::map<std::string, File> files;
    std::vector<std::string> commits;

    if (!base.empty() && !head.empty() && base != head) {
        numstat(g.run("diff", {"--numstat", "--find-renames", base, "HEAD"}).out, files, true);
        nameStatus(g.run("diff", {"--name-status", "--find-renames", base, "HEAD"}).out,
                   files, true);

        const git::Result log = g.run("log", {"--format=%H%x1f%s%x1f%cI", base + "..HEAD"});
        for (const auto& line : lines(log.out)) {
            const std::size_t one = line.find('\x1f');
            if (one == std::string::npos) continue;
            const std::size_t two = line.find('\x1f', one + 1);
            if (two == std::string::npos) continue;
            std::ostringstream commit;
            commit << "{\"sha\":" << jstr(line.substr(0, one))
                   << ",\"subject\":" << jstr(line.substr(one + 1, two - one - 1))
                   << ",\"iso\":" << jstr(line.substr(two + 1)) << "}";
            commits.push_back(commit.str());
        }
    }

    if (!head.empty()) {
        numstat(g.run("diff", {"--numstat", "--find-renames", "HEAD"}).out, files, false);
        nameStatus(g.run("diff", {"--name-status", "--find-renames", "HEAD"}).out, files, false);
    }

    std::set<std::string> untracked;
    for (const auto& path : lines(g.run("ls-files", {"--others", "--exclude-standard"}).out)) {
        const std::string clean = unquoted(path);
        untracked.insert(clean);
        File& file = files[clean];
        file.path = clean;
        file.status = "added";
        file.added += addedLinesOf(query.worktree + "/" + clean);
    }

    std::string patch;
    if (query.wantPatch && !query.path.empty()
        && query.path.front() != '/' && query.path.find("..") == std::string::npos) {
        if (untracked.count(query.path)) {
            patch = g.run("diff", {"--no-index", "--", "/dev/null", query.path}).out;
        } else if (!base.empty()) {
            patch = g.run("diff", {base, "--", query.path}).out;
        } else if (!head.empty()) {
            patch = g.run("diff", {"HEAD", "--", query.path}).out;
        }
    }

    int added = 0, removed = 0;
    std::ostringstream body;
    body << "{\"lane\":" << jstr(query.lane)
         << ",\"branch\":" << jstr(query.branch)
         << ",\"base\":" << (base.empty() ? "null" : jstr(base))
         << ",\"base_branch\":" << (baseBranch.empty() ? "null" : jstr(baseBranch))
         << ",\"files\":[";
    bool first = true;
    for (const auto& entry : files) {
        const File& file = entry.second;
        added += file.added;
        removed += file.removed;
        body << (first ? "" : ",")
             << "{\"path\":" << jstr(file.path)
             << ",\"status\":" << jstr(file.status.empty() ? "modified" : file.status)
             << ",\"added\":" << file.added
             << ",\"removed\":" << file.removed
             << ",\"committed\":" << (file.committed ? "true" : "false") << "}";
        first = false;
    }
    body << "],\"commits\":[";
    for (std::size_t i = 0; i < commits.size(); ++i) body << (i ? "," : "") << commits[i];
    body << "],\"totals\":{\"files\":" << files.size()
         << ",\"added\":" << added
         << ",\"removed\":" << removed
         << ",\"commits\":" << commits.size() << "}";
    if (query.wantPatch) body << ",\"patch\":" << jstr(patch);
    body << "}";

    std::ostringstream allows, ran;
    allows << "[";
    for (std::size_t i = 0; i < READ_ONLY.size(); ++i) allows << (i ? "," : "") << jstr(READ_ONLY[i]);
    allows << "]";
    ran << "[";
    bool firstUsed = true;
    for (const auto& sub : g.used) {
        ran << (firstUsed ? "" : ",") << jstr(sub);
        firstUsed = false;
    }
    ran << "]";
    LOG(INFO) << "lane: diff allows " << allows.str() << " used " << ran.str();

    return body.str();
}
