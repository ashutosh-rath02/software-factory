#include "routing-inputs.h"

#include <algorithm>
#include <cerrno>
#include <filesystem>
#include <map>
#include <stdexcept>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <app/lane/git.h>
#include <app/routing/bytes.h>

namespace api::routingInputs {

struct Descriptor {
    int value = -1;

    explicit Descriptor(int fd) : value(fd) {}
    Descriptor(const Descriptor&) = delete;
    Descriptor& operator=(const Descriptor&) = delete;
    Descriptor(Descriptor&& other) noexcept : value(std::exchange(other.value, -1)) {}
    Descriptor& operator=(Descriptor&& other) noexcept
    {
        if (value >= 0) ::close(value);
        value = std::exchange(other.value, -1);
        return *this;
    }
    ~Descriptor() { if (value >= 0) ::close(value); }
};

struct GitInputs {
    std::string head;
    std::string branch;
    std::map<std::string, bool> paths;
    std::vector<std::string> indexEntries;
};

struct SourceInput {
    struct stat status{};
    bool missing = false;
};

static void field(std::string& frame, const std::string& name, const std::string& bytes)
{
    frame += std::to_string(name.size()) + ":" + name;
    frame += std::to_string(bytes.size()) + ":" + bytes;
}

static std::vector<std::string> components(const std::string& path)
{
    if (path.empty() || path.front() == '/' || path.find('\0') != std::string::npos)
        throw std::runtime_error("invalid Git-visible source path");
    std::vector<std::string> out;
    std::size_t begin = 0;
    while (begin < path.size()) {
        const std::size_t slash = path.find('/', begin);
        const std::string part = path.substr(begin, slash == std::string::npos
                                                        ? std::string::npos : slash - begin);
        if (part.empty() || part == "." || part == "..")
            throw std::runtime_error("invalid Git-visible source path");
        out.push_back(part);
        if (slash == std::string::npos) break;
        begin = slash + 1;
        if (begin == path.size()) throw std::runtime_error("invalid Git-visible source path");
    }
    return out;
}

static Descriptor parentDirectory(int root, const std::vector<std::string>& path, bool& missing)
{
    missing = false;
    Descriptor current(::fcntl(root, F_DUPFD_CLOEXEC, 0));
    if (current.value < 0) throw std::runtime_error("cannot open source directory");
    for (std::size_t index = 0; index + 1 < path.size(); ++index) {
        struct stat status{};
        if (::fstatat(current.value, path[index].c_str(), &status, AT_SYMLINK_NOFOLLOW) != 0) {
            if (errno == ENOENT) {
                missing = true;
                return Descriptor(-1);
            }
            throw std::runtime_error("cannot inspect source ancestor: " + path[index]);
        }
        if (!S_ISDIR(status.st_mode))
            throw std::runtime_error("source ancestor is not a real directory: " + path[index]);
        Descriptor next(::openat(current.value, path[index].c_str(),
                                 O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
        if (next.value < 0)
            throw std::runtime_error("cannot open source ancestor: " + path[index]);
        current = std::move(next);
    }
    return current;
}

static Descriptor absoluteDirectory(const std::string& path)
{
    const std::filesystem::path absolute(path);
    if (!absolute.is_absolute()) throw std::runtime_error("source root must be absolute");
    Descriptor root(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    if (root.value < 0) throw std::runtime_error("cannot open filesystem root");
    const std::string relative = absolute.relative_path().string();
    if (relative.empty()) return root;
    auto names = components(relative);
    names.push_back("unused");
    bool missing = false;
    Descriptor directory = parentDirectory(root.value, names, missing);
    if (missing) throw std::runtime_error("source directory is missing: " + path);
    return directory;
}

static bool unchanged(const struct stat& before, const struct stat& after)
{
    return before.st_dev == after.st_dev && before.st_ino == after.st_ino
        && before.st_mode == after.st_mode && before.st_size == after.st_size
        && before.st_mtim.tv_sec == after.st_mtim.tv_sec
        && before.st_mtim.tv_nsec == after.st_mtim.tv_nsec
        && before.st_ctim.tv_sec == after.st_ctim.tv_sec
        && before.st_ctim.tv_nsec == after.st_ctim.tv_nsec;
}

static std::string regularBytes(int parent, const std::string& name, struct stat& status)
{
    Descriptor file(::openat(parent, name.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    if (file.value < 0) throw std::runtime_error("cannot read source file: " + name);
    if (::fstat(file.value, &status) != 0 || !S_ISREG(status.st_mode))
        throw std::runtime_error("source is not a regular file: " + name);
    std::string bytes;
    char buffer[65536];
    for (;;) {
        const ssize_t count = ::read(file.value, buffer, sizeof buffer);
        if (count == 0) break;
        if (count < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error("cannot read complete source file: " + name);
        }
        bytes.append(buffer, static_cast<std::size_t>(count));
    }
    struct stat after{};
    if (::fstat(file.value, &after) != 0 || !unchanged(status, after)
        || status.st_size < 0 || static_cast<unsigned long long>(status.st_size) != bytes.size())
        throw std::runtime_error("source file changed while being read: " + name);
    struct stat pathStatus{};
    if (::fstatat(parent, name.c_str(), &pathStatus, AT_SYMLINK_NOFOLLOW) != 0
        || !unchanged(status, pathStatus))
        throw std::runtime_error("source file changed while being read: " + name);
    return bytes;
}

static std::string manifestBytes(const std::string& path)
{
    const std::filesystem::path absolute(path);
    if (!absolute.is_absolute() || absolute.filename().empty())
        throw std::runtime_error("manifest path must name an absolute file");
    Descriptor parent = absoluteDirectory(absolute.parent_path().string());
    struct stat status{};
    return regularBytes(parent.value, absolute.filename().string(), status);
}

static lane::git::Result gitRead(const GitRead& readGit, const std::vector<std::string>& args)
{
    std::vector<std::string> safe{"--no-optional-locks", "-c", "core.fsmonitor=false",
                                  "-c", "core.untrackedCache=false", "-c", "diff.external="};
    safe.insert(safe.end(), args.begin(), args.end());
    return readGit(safe);
}

static void addPaths(GitInputs& inputs, const lane::git::Result& listed, bool tracked, bool modes,
                     bool indexEntries = false)
{
    if (!listed.ok) throw std::runtime_error("cannot enumerate Git-visible source files");
    std::size_t begin = 0;
    while (begin < listed.out.size()) {
        const std::size_t end = listed.out.find('\0', begin);
        if (end == std::string::npos) throw std::runtime_error("incomplete Git-visible source list");
        const std::string entry = listed.out.substr(begin, end - begin);
        std::string name = entry;
        if (modes) {
            const std::size_t tab = entry.find('\t');
            if (tab == std::string::npos)
                throw std::runtime_error("invalid Git-visible source entry");
            if (entry.compare(0, 7, "160000 ") == 0)
                throw std::runtime_error("Git submodule inputs are not covered by routing reuse");
            name = entry.substr(tab + 1);
        }
        components(name);
        inputs.paths[name] = inputs.paths[name] || tracked;
        if (indexEntries) inputs.indexEntries.push_back(entry);
        begin = end + 1;
    }
}

static GitInputs gitInputs(const GitRead& readGit)
{
    GitInputs inputs;
    const auto head = gitRead(readGit, {"rev-parse", "--verify", "HEAD^{commit}"});
    if (!head.ok || head.out.empty()) throw std::runtime_error("cannot resolve source HEAD");
    inputs.head = head.out;
    const auto branch = gitRead(readGit, {"symbolic-ref", "--quiet", "HEAD"});
    if (!branch.ok && branch.code != 1) throw std::runtime_error("cannot resolve source branch");
    inputs.branch = branch.ok ? branch.out : "detached";
    addPaths(inputs, gitRead(readGit, {"ls-tree", "-r", "-z", "HEAD"}), true, true);
    addPaths(inputs, gitRead(readGit, {"ls-files", "--cached", "--stage", "-z", "--full-name"}),
             true, true, true);
    addPaths(inputs, gitRead(readGit, {"ls-files", "--others", "--exclude-standard", "-z",
                                      "--full-name"}), false, false);
    std::sort(inputs.indexEntries.begin(), inputs.indexEntries.end());
    return inputs;
}

static SourceInput sourceFile(std::string& frame, int root, const std::string& path, bool tracked)
{
    const auto names = components(path);
    bool missing = false;
    Descriptor parent = parentDirectory(root, names, missing);
    struct stat status{};
    if (!missing && ::fstatat(parent.value, names.back().c_str(), &status,
                              AT_SYMLINK_NOFOLLOW) != 0) {
        if (errno == ENOENT) missing = true;
        else throw std::runtime_error("cannot inspect source file: " + path);
    }
    field(frame, "path", path);
    if (missing) {
        if (!tracked) throw std::runtime_error("untracked source disappeared: " + path);
        field(frame, "type", "deleted");
        return SourceInput{{}, true};
    }
    std::string bytes;
    if (S_ISREG(status.st_mode)) {
        bytes = regularBytes(parent.value, names.back(), status);
        field(frame, "type", "regular");
    } else if (S_ISLNK(status.st_mode)) {
        std::vector<char> buffer(256);
        for (;;) {
            const ssize_t count = ::readlinkat(parent.value, names.back().c_str(),
                                                buffer.data(), buffer.size());
            if (count < 0) throw std::runtime_error("cannot read source symlink: " + path);
            if (static_cast<std::size_t>(count) < buffer.size()) {
                bytes.assign(buffer.data(), static_cast<std::size_t>(count));
                break;
            }
            buffer.resize(buffer.size() * 2);
        }
        struct stat after{};
        if (::fstatat(parent.value, names.back().c_str(), &after, AT_SYMLINK_NOFOLLOW) != 0
            || !unchanged(status, after))
            throw std::runtime_error("source symlink changed while being read: " + path);
        field(frame, "type", "symlink");
    } else {
        throw std::runtime_error("source file type is not covered by routing reuse: " + path);
    }
    field(frame, "executable", std::to_string(status.st_mode & (S_IXUSR | S_IXGRP | S_IXOTH)));
    field(frame, "bytes", std::to_string(bytes.size()));
    field(frame, "sha256", routing::sha256(bytes));
    return SourceInput{status, false};
}

static void revalidate(int root, const std::string& path, const SourceInput& observed)
{
    const auto names = components(path);
    bool missing = false;
    Descriptor parent = parentDirectory(root, names, missing);
    struct stat current{};
    if (!missing && ::fstatat(parent.value, names.back().c_str(), &current,
                              AT_SYMLINK_NOFOLLOW) != 0) {
        if (errno == ENOENT) missing = true;
        else throw std::runtime_error("cannot reinspect source file: " + path);
    }
    if (observed.missing != missing || (!missing && !unchanged(observed.status, current)))
        throw std::runtime_error("source file changed while inputs were being read: " + path);
}

Result fingerprint(const lane::Store::Record& record, const std::string& effectiveManifest,
                   const std::string& currentManifestPath, const GitRead& readGit)
{
    Result result;
    try {
        Descriptor worktree = absoluteDirectory(record.worktree);
        struct stat rootStatus{};
        if (::fstat(worktree.value, &rootStatus) != 0)
            throw std::runtime_error("cannot inspect source root");
        const std::string currentManifest = manifestBytes(currentManifestPath);
        const GitInputs before = gitInputs(readGit);
        std::string frame;
        field(frame, "format", "routing-source-v2");
        field(frame, "lane", record.name);
        field(frame, "ticket", record.ticketId);
        field(frame, "workspace", record.workspace);
        field(frame, "role", record.role);
        field(frame, "branch", record.branch);
        field(frame, "worktree", record.worktree);
        field(frame, "claimed_at", std::to_string(record.claimedAtMs));
        field(frame, "worktree_device", std::to_string(rootStatus.st_dev));
        field(frame, "worktree_inode", std::to_string(rootStatus.st_ino));
        field(frame, "head", before.head);
        field(frame, "symbolic_ref", before.branch);
        field(frame, "effective_manifest", effectiveManifest);
        field(frame, "manifest_path", currentManifestPath);
        field(frame, "current_manifest", currentManifest);
        field(frame, "index_entry_count", std::to_string(before.indexEntries.size()));
        for (const auto& entry : before.indexEntries) field(frame, "index_entry", entry);
        field(frame, "file_count", std::to_string(before.paths.size()));
        std::map<std::string, SourceInput> observed;
        for (const auto& [path, tracked] : before.paths)
            observed.emplace(path, sourceFile(frame, worktree.value, path, tracked));
        const GitInputs after = gitInputs(readGit);
        if (before.head != after.head || before.branch != after.branch || before.paths != after.paths
            || before.indexEntries != after.indexEntries
            || currentManifest != manifestBytes(currentManifestPath))
            throw std::runtime_error("source inputs changed while being read");
        for (const auto& [path, input] : observed) revalidate(worktree.value, path, input);
        Descriptor currentRoot = absoluteDirectory(record.worktree);
        struct stat afterRoot{};
        if (::fstat(currentRoot.value, &afterRoot) != 0 || rootStatus.st_dev != afterRoot.st_dev
            || rootStatus.st_ino != afterRoot.st_ino)
            throw std::runtime_error("source root changed while being read");
        result.source = routing::sha256(frame);
    } catch (const std::exception& error) {
        result.error = error.what();
    }
    return result;
}

}
