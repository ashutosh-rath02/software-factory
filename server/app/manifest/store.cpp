#include "store.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <set>
#include <filesystem>
#include <fstream>
#include <sstream>

#include <pwd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <config.h>
#include <glog/logging.h>

#include <app/error/remedy.h>

#include "json.h"
#include "schema.h"

namespace fs = std::filesystem;

namespace {

constexpr const char* FACTORY_DIR = ".factory";
constexpr const char* MANIFEST_FILE = "factory.json";
constexpr const char* RUNS_DIR = "runs";
constexpr const char* ROUTING_DIR = ".routing";
constexpr const char* WORKTREES_DIR = "worktrees";
constexpr const char* LANES_DIR = "lanes";
constexpr const char* PERMISSIONS_FILE = "permissions.json";
constexpr const char* IDS_FILE = "ids.json";

std::string schemaPath()
{
    if (const char* env = std::getenv("FACTORY_SCHEMA")) return env;
    return FACTORY_SCHEMA_PATH;
}

bool readFile(const fs::path& p, std::string& out)
{
    std::ifstream in(p, std::ios::binary);
    if (!in) return false;
    std::ostringstream ss;
    ss << in.rdbuf();
    out = ss.str();
    return true;
}

bool parseVersion(const std::string& s, int& major, int& minor)
{
    std::size_t dot = s.find('.');
    if (dot == std::string::npos) return false;
    std::string head = s.substr(0, dot), tail = s.substr(dot + 1);
    auto digits = [](const std::string& part) {
        if (part.empty() || part.size() > 9) return false;
        for (char c : part)
            if (!std::isdigit(static_cast<unsigned char>(c))) return false;
        return true;
    };
    if (!digits(head) || !digits(tail)) return false;
    major = std::stoi(head);
    minor = std::stoi(tail);
    return true;
}

std::string developerHomeOfThisProcess()
{
    if (const char* home = std::getenv("HOME"))
        if (*home) return home;
    if (const struct passwd* pw = ::getpwuid(::getuid()))
        if (pw->pw_dir && *pw->pw_dir) return pw->pw_dir;
    return {};
}

bool isTooBroadForABind(const std::string& canonicalPath, const std::string& rawHome)
{
    if (canonicalPath.empty()) return false;
    if (canonicalPath == "/") return true;
    if (rawHome.empty()) return false;
    std::error_code ec;
    const fs::path resolved = fs::weakly_canonical(fs::path(rawHome), ec);
    const std::string home = ec ? rawHome : resolved.string();
    if (home.empty()) return false;
    if (canonicalPath == home) return true;
    const std::string prefix =
        canonicalPath.back() == '/' ? canonicalPath : canonicalPath + "/";
    return home.compare(0, prefix.size(), prefix) == 0;
}

const char* const CREDENTIAL_DIRECTORIES[] = {
    "~/.config/gh",
    "~/.config/gh-cli",
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.docker",
    "~/.netrc",
    "~/.git-credentials",
    "~/.config/git/credentials",
};

std::string collidesWithACredential(const std::string& canonicalPath, const std::string& rawHome)
{
    if (canonicalPath.empty()) return {};
    std::error_code ec;
    for (const char* declared : CREDENTIAL_DIRECTORIES) {
        std::string named = declared;
        if (named.compare(0, 2, "~/") == 0) {
            if (rawHome.empty()) continue;
            named = rawHome + named.substr(1);
        }
        const fs::path resolved = fs::weakly_canonical(fs::path(named), ec);
        const std::string member = ec ? named : resolved.string();
        if (member.empty()) continue;
        if (canonicalPath == member) return declared;
        const std::string under = member.back() == '/' ? member : member + "/";
        if (canonicalPath.compare(0, under.size(), under) == 0) return declared;
        const std::string over =
            canonicalPath.back() == '/' ? canonicalPath : canonicalPath + "/";
        if (member.compare(0, over.size(), over) == 0) return declared;
    }
    return {};
}

std::string interpreterOf(const std::string& program)
{
    std::ifstream in(program, std::ios::binary);
    if (!in) return {};
    char magic[2] = {0, 0};
    in.read(magic, 2);
    if (in.gcount() != 2 || magic[0] != '#' || magic[1] != '!') return {};
    std::string line;
    std::getline(in, line);
    const std::size_t begin = line.find_first_not_of(" \t");
    if (begin == std::string::npos) return {};
    std::size_t end = line.find_first_of(" \t", begin);
    if (end == std::string::npos) end = line.size();
    while (end > begin && (line[end - 1] == '\r' || line[end - 1] == '\n')) --end;
    return line.substr(begin, end - begin);
}

void appendStrings(const manifest::Json* node, std::vector<std::string>& out)
{
    if (!node || !node->isArray()) return;
    for (auto& item : node->items())
        if (item.isString()) out.push_back(item.str());
}

}

void manifest::Store::init()
{
    load_();
    switch (data_.state) {
        case State::Ok:
            LOG(INFO) << "manifest " << data_.version << " loaded from " << data_.manifestPath
                      << " (" << data_.workspaces.size() << " workspace(s), "
                      << data_.warnings.size() << " warning(s))";
            for (const Warning& w : data_.warnings)
                if (w.code == "command_unrunnable")
                    LOG(WARNING) << w.pointer << ": " << w.message;
            break;
        case State::Missing:
            LOG(WARNING) << "no manifest at " << data_.manifestPath;
            break;
        default:
            LOG(ERROR) << "manifest unusable: " << data_.errorMessage;
            break;
    }
}

namespace {

std::string gitDirNamedBy(const fs::path& dotGit)
{
    std::ifstream in(dotGit);
    std::string line;
    if (!in || !std::getline(in, line)) return {};
    const std::string tag = "gitdir:";
    if (line.compare(0, tag.size(), tag) != 0) return {};
    std::string path = line.substr(tag.size());
    while (!path.empty() && (path.front() == ' ' || path.front() == '\t')) path.erase(path.begin());
    while (!path.empty() && (path.back() == '\r' || path.back() == ' ')) path.pop_back();
    if (path.empty()) return {};
    if (path.front() != '/') path = (dotGit.parent_path() / path).string();
    return path;
}

bool namesRepository(const fs::path& dotGit)
{
    std::error_code ec;
    if (fs::is_directory(dotGit, ec)) return fs::exists(dotGit / "HEAD", ec);
    if (!fs::is_regular_file(dotGit, ec)) return false;
    const std::string gitDir = gitDirNamedBy(dotGit);
    return !gitDir.empty() && fs::exists(fs::path(gitDir) / "HEAD", ec);
}

std::string gitRootAbove(const fs::path& start)
{
    for (fs::path at = start; !at.empty(); at = at.parent_path()) {
        if (namesRepository(at / ".git")) return at.string();
        if (at == at.parent_path()) break;
    }
    return {};
}

}

void manifest::Store::load_()
{
    data_ = Data();

    std::error_code ec;
    fs::path root = fs::weakly_canonical(fs::path(given_), ec);
    if (ec) root = fs::absolute(fs::path(given_));
    data_.root = root.string();
    data_.name = root.filename().string();
    data_.manifestPath = (root / FACTORY_DIR / MANIFEST_FILE).string();
    data_.runsDir = (root / FACTORY_DIR / RUNS_DIR).string();
    data_.routingDir = (root / FACTORY_DIR / RUNS_DIR / ROUTING_DIR).string();
    data_.worktreesDir = (root / FACTORY_DIR / WORKTREES_DIR).string();
    data_.lanesDir = (root / FACTORY_DIR / LANES_DIR).string();
    data_.permissionsPath = (root / FACTORY_DIR / PERMISSIONS_FILE).string();
    data_.idsPath = (root / FACTORY_DIR / IDS_FILE).string();
    data_.gitRoot = gitRootAbove(root);

    if (!fs::is_directory(root)) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "repo_root_missing";
        data_.errorMessage = "no repository at " + data_.root;
        return;
    }

    if (!fs::exists(fs::path(data_.manifestPath))) {
        data_.state = State::Missing;
        data_.errorCode = "not_found";
        data_.errorReason = "manifest_missing";
        data_.errorMessage = "no manifest at " + data_.manifestPath;
        return;
    }

    std::string text;
    if (!readFile(fs::path(data_.manifestPath), text)) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "unreadable";
        data_.errorMessage = "cannot read " + data_.manifestPath;
        return;
    }

    Json::Error perr;
    Json doc = Json::parse(text, perr);
    if (!perr.ok) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "parse_error";
        data_.errorOffset = static_cast<long long>(perr.offset);
        data_.errorMessage = data_.manifestPath + " is not JSON: " + perr.message
                           + " (at byte " + std::to_string(perr.offset) + ")";
        return;
    }

    if (!doc.isObject()) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "wrong_type";
        data_.errorPointer = "";
        data_.hasErrorPointer = true;
        data_.errorMessage = "the manifest must be a JSON object";
        return;
    }

    const Json* version = doc.find("factory");
    if (!version || !version->isString()) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = version ? "wrong_type" : "required";
        data_.errorPointer = "/factory";
        data_.hasErrorPointer = true;
        data_.errorMessage = "the manifest does not say which version it is (/factory)";
        return;
    }
    data_.version = version->str();

    int major = 0, minor = 0;
    if (!parseVersion(data_.version, major, minor)) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "bad_version";
        data_.errorPointer = "/factory";
        data_.hasErrorPointer = true;
        data_.errorMessage = "\"" + data_.version + "\" is not a major.minor version";
        return;
    }

    int ourMajor = 0, ourMinor = 0;
    parseVersion(Config::FACTORY_VERSION, ourMajor, ourMinor);

    if (major != ourMajor) {
        data_.state = State::UnsupportedMajor;
        data_.errorCode = "unsupported_manifest_major";
        data_.errorReason = "unsupported_major";
        data_.errorMessage = data_.manifestPath + " asks for manifest version " + data_.version
                           + "; this build speaks " + Config::FACTORY_VERSION
                           + ". Refusing to guess.";
        return;
    }
    if (minor != ourMinor) {
        data_.warnings.push_back({"unknown_minor", "/factory", "",
                                  "manifest minor " + data_.version + " differs from this build's "
                                  + Config::FACTORY_VERSION + "; unknown keys are ignored"});
    }

    std::string schemaText;
    if (!readFile(fs::path(schemaPath()), schemaText)) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "schema_unavailable";
        data_.errorMessage = "cannot read the manifest schema at " + schemaPath();
        return;
    }
    Json::Error serr;
    Json schema = Json::parse(schemaText, serr);
    if (!serr.ok) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "schema_unavailable";
        data_.errorMessage = "the manifest schema at " + schemaPath() + " is not JSON";
        return;
    }

    Violation bad;
    std::vector<UnknownKey> unknown;
    if (!validate(schema, doc, bad, unknown)) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = bad.reason;
        data_.errorPointer = bad.pointer;
        data_.hasErrorPointer = true;
        data_.errorMessage = bad.message;
        return;
    }
    for (auto& u : unknown)
        data_.warnings.push_back({"unknown_key", u.pointer, "", u.message});

    if (const Json* ws = doc.find("workspaces")) {
        for (auto& m : ws->members()) {
            Workspace w;
            w.name = m.first;
            w.path = m.second.stringOr("path", "");
            w.repo = m.second.stringOr("repo", "");
            if (const Json* g = m.second.find("gate")) { w.gate = g->str(); w.hasGate = true; }
            if (const Json* b = m.second.find("build")) { w.build = b->str(); w.hasBuild = true; }
            if (const Json* v = m.second.find("review")) { w.review = v->str(); w.hasReview = true; }
            if (const Json* p = m.second.find("start_preflight")) {
                w.hasStartPreflight = true;
                w.startPreflight.command = p->find("command")->str();
                if (w.startPreflight.command.find_first_not_of(" \t\r\n")
                    == std::string::npos) {
                    data_.state = State::Invalid;
                    data_.errorCode = "invalid_manifest";
                    data_.errorReason = "empty_command";
                    data_.errorPointer = "/workspaces/" + w.name + "/start_preflight/command";
                    data_.hasErrorPointer = true;
                    data_.errorMessage = "start_preflight.command must contain a command";
                    return;
                }
                if (const Json* t = p->find("timeout_seconds"))
                    w.startPreflight.timeoutSeconds = static_cast<int>(t->number());
                appendStrings(p->find("exempt_ticket_ids"), w.startPreflight.exemptTicketIds);
            }
            std::vector<std::string> declared;
            appendStrings(m.second.find("toolchain"), declared);
            for (auto& d : declared) w.toolchain.push_back({d, ""});
            data_.workspaces.push_back(std::move(w));
        }
    }

    if (const Json* res = doc.find("resources")) {
        data_.hasResources = true;
        for (auto& m : res->members()) {
            Resource r;
            r.name = m.first;
            if (const Json* e = m.second.find("exclusive")) r.exclusive = e->boolean();
            appendStrings(m.second.find("needed_by"), r.neededBy);
            appendStrings(m.second.find("devices"), r.devices);

            if (!r.devices.empty() && !r.exclusive) {
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorReason = "devices_need_exclusive";
                data_.errorPointer = "/resources/" + m.first + "/exclusive";
                data_.hasErrorPointer = true;
                data_.errorField = "exclusive";
                data_.hasErrorField = true;
                data_.errorMessage = "this resource declares devices and is not exclusive, so no "
                                     "lane ever holds it and the devices would be bound into none; "
                                     "make it exclusive or take the devices off it";
                return;
            }

            for (std::size_t di = 0; di < r.devices.size(); ++di) {
                const std::string& given = r.devices[di];
                const std::string pointer = "/resources/" + m.first + "/devices/"
                                          + std::to_string(di);
                std::error_code ec;
                const fs::path real = fs::weakly_canonical(fs::path(given), ec);
                const std::string shown = ec ? given : real.string();

                struct ::stat st {};
                if (::stat(shown.c_str(), &st) != 0) {
                    data_.state = State::Invalid;
                    data_.errorCode = "invalid_manifest";
                    data_.errorReason = "device_not_found";
                    data_.errorPointer = pointer;
                    data_.hasErrorPointer = true;
                    data_.errorPath = shown;
                    data_.hasErrorPath = true;
                    data_.errorMessage = "this resource declares a device that is not on this "
                                         "machine; a lane would hold the lock and have nothing to "
                                         "use, which is the defect the field exists to fix";
                    return;
                }
                if (!S_ISCHR(st.st_mode) && !S_ISBLK(st.st_mode)) {
                    data_.state = State::Invalid;
                    data_.errorCode = "invalid_manifest";
                    data_.errorReason = "not_a_device";
                    data_.errorPointer = pointer;
                    data_.hasErrorPointer = true;
                    data_.errorPath = shown;
                    data_.hasErrorPath = true;
                    data_.errorMessage = "a resource's `devices` entry must be a character or "
                                         "block device node; binding anything else hands the lane "
                                         "read-write access to whatever is behind it";
                    return;
                }
            }
            if (const Json* d = m.second.find("description")) {
                r.description = d->str();
                r.hasDescription = true;
            }
            data_.resources.push_back(std::move(r));
        }
    }

    if (const Json* lanes = doc.find("lanes"))
        if (const Json* host = lanes->find("host"))
            data_.lanesHost = static_cast<int>(host->number());

    const Json* reposBlock = doc.find("repos");
    const Json* docsBlock = doc.find("docs");
    if (reposBlock) {
        if (docsBlock) {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorReason = "docs_and_repos";
            data_.errorPointer = "/docs";
            data_.hasErrorPointer = true;
            data_.errorField = "docs";
            data_.hasErrorField = true;
            data_.errorMessage = "this manifest declares `repos` AND a top-level `docs`; the "
                                 "documents belong to a repository, so move `docs` inside the "
                                 "repo it describes and delete the top-level one";
            return;
        }
        data_.hasRepos = true;
        for (auto& m : reposBlock->members()) {
            Repo r;
            r.name = m.first;
            r.path = m.second.stringOr("path", ".");
            if (const Json* d = m.second.find("docs")) {
                r.docs.epics = d->stringOr("epics", r.docs.epics);
                r.docs.tickets = d->stringOr("tickets", r.docs.tickets);
                r.docs.reports = d->stringOr("reports", r.docs.reports);
                r.docs.wiki = d->stringOr("wiki", r.docs.wiki);
                r.docs.escapes = d->stringOr("escapes", r.docs.escapes);
            }
            data_.repos.push_back(std::move(r));
        }
    } else {
        Repo r;
        if (docsBlock) {
            r.docs.epics = docsBlock->stringOr("epics", r.docs.epics);
            r.docs.tickets = docsBlock->stringOr("tickets", r.docs.tickets);
            r.docs.reports = docsBlock->stringOr("reports", r.docs.reports);
            r.docs.wiki = docsBlock->stringOr("wiki", r.docs.wiki);
            r.docs.escapes = docsBlock->stringOr("escapes", r.docs.escapes);
        }
        data_.repos.push_back(std::move(r));
    }

    if (const Json* s = doc.find("stages")) {
        data_.hasStages = true;
        appendStrings(s, data_.stages);
    }

    if (const Json* st = doc.find("status")) {
        if (const Json* fl = st->find("in_flight")) {
            data_.hasInFlightStatus = true;
            appendStrings(fl, data_.inFlightStatus);
        }
        if (const Json* dn = st->find("done")) {
            data_.hasDoneStatus = true;
            appendStrings(dn, data_.doneStatus);
        }
    }

    if (const Json* ev = doc.find("evidence")) {
        data_.hasEvidence = true;
        data_.evidence.linePrefix = ev->stringOr("line_prefix", "");
        data_.evidence.frozenAfter = ev->stringOr("frozen_after", "");

        bool declared = false;
        for (const auto& stage : data_.stages)
            if (stage == data_.evidence.frozenAfter) declared = true;
        if (!declared) {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorReason = "stage_not_declared";
            data_.errorPointer = "/evidence/frozen_after";
            data_.hasErrorPointer = true;
            data_.errorField = "frozen_after";
            data_.hasErrorField = true;
            data_.errorMessage = "\"" + data_.evidence.frozenAfter + "\" is not one of the stages "
                                 "this manifest declares, so no run could ever report it and the "
                                 "freeze could never fire; name a stage from `stages` or take the "
                                 "`evidence` block out";
            return;
        }
    }

    if (const Json* disp = doc.find("display")) {
        if (const Json* tf = disp->find("ticket_fields")) {
            data_.hasTicketFields = true;
            appendStrings(tf, data_.ticketFields);
        }
    }

    auto readAgent = [&](const Json* ag, const std::string& name) {
        Agent profile;
        profile.name = name;
        profile.command = ag->stringOr("command", profile.command);
        if (const Json* pm = ag->find("permission_mode")) {
            profile.permissionMode = pm->str();
            profile.hasPermissionMode = true;
        }
        if (const Json* mo = ag->find("model")) {
            profile.model = mo->str();
            profile.hasModel = true;
        }
        if (const Json* at = ag->find("allowed_tools")) {
            profile.hasAllowedTools = true;
            appendStrings(at, profile.allowedTools);
        }
        if (const Json* cd = ag->find("credentials_dir")) {
            profile.credentialsDir = cd->str();
            profile.hasCredentialsDir = true;
        }
        if (const Json* sp = ag->find("start_prompt_file")) {
            profile.startPromptFile = sp->str();
            profile.hasStartPromptFile = true;
        }

        if (const Json* ct = ag->find("config_trust")) {
            profile.hasConfigTrust = true;
            if (const Json* f = ct->find("file")) profile.configTrust.file = f->str();
            if (const Json* k = ct->find("keyed_by_path"))
                profile.configTrust.keyedByPath = k->str();
            if (const Json* g = ct->find("flag")) profile.configTrust.flag = g->str();
        }
        profile.driver = ag->stringOr("driver", "claude");
        appendStrings(ag->find("models"), profile.models);
        if (const Json* model = ag->find("default_model")) {
            profile.model = model->str();
            profile.hasModel = true;
        }
        if (const Json* efforts = ag->find("efforts")) {
            profile.hasEfforts = true;
            appendStrings(efforts, profile.efforts);
        }
        if (const Json* effort = ag->find("default_effort")) {
            profile.defaultEffort = effort->str();
            profile.hasDefaultEffort = true;
        }
        return profile;
    };
    if (const Json* profiles = doc.find("agents")) {
        if (doc.find("agent") || !profiles->isObject() || profiles->members().empty()) {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorPointer = "/agents";
            data_.hasErrorPointer = true;
            data_.errorMessage = "declare either agent or a non-empty agents object";
            return;
        }
        data_.defaultAgent = doc.stringOr("default_agent", "");
        for (const auto& [name, value] : profiles->members()) {
            Agent profile = readAgent(&value, name);
            if (name.empty() || name.find_first_not_of(
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") != std::string::npos
                || (profile.driver != "claude" && profile.driver != "codex")
                || profile.models.empty()
                || profile.model.empty()
                || std::find(profile.models.begin(), profile.models.end(), profile.model)
                   == profile.models.end()) {
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorPointer = "/agents/" + name;
                data_.hasErrorPointer = true;
                data_.errorMessage = "profile needs a supported driver and a default_model in models";
                return;
            }
            if (profile.hasDefaultEffort
                && std::find(profile.efforts.begin(), profile.efforts.end(),
                             profile.defaultEffort) == profile.efforts.end()) {
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorReason = "undeclared_effort";
                data_.errorPointer = "/agents/" + name + "/default_effort";
                data_.hasErrorPointer = true;
                data_.errorField = profile.defaultEffort;
                data_.hasErrorField = true;
                data_.errorMessage = "\"" + profile.defaultEffort + "\" is not one of the efforts "
                                     "the profile \"" + name + "\" declares, so it could never be "
                                     "selected; name one of its `efforts` or take `default_effort` "
                                     "out";
                return;
            }
            data_.agents.push_back(std::move(profile));
        }
    } else {
        if (const Json* ag = doc.find("agent")) data_.agent = readAgent(ag, "default");
        data_.agents.push_back(data_.agent);
    }

    if (const Json* trees = doc.find("trees")) {
        data_.hasTrees = true;
        const auto validName = [](const std::string& value) {
            return !value.empty() && value != "." && value != ".."
                && value.find_first_not_of(
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
                   == std::string::npos;
        };
        for (const auto& [name, value] : trees->members()) {
            if (!validName(name)) {
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorPointer = "/trees/" + name;
                data_.hasErrorPointer = true;
                data_.errorMessage = "tree preset name must be a file-safe segment";
                return;
            }
            Tree tree;
            tree.name = name;
            tree.decider = value.stringOr("decider", "");
            if (tree.decider != "rules") {
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorPointer = "/trees/" + name + "/decider";
                data_.hasErrorPointer = true;
                data_.errorMessage = "only the rules tree decider is supported";
                return;
            }
            appendStrings(value.find("agents"), tree.agents);
            if (const Json* continuation = value.find("continuation")) {
                tree.continuation = continuation->str();
                if (tree.continuation != "goal") {
                    data_.state = State::Invalid;
                    data_.errorCode = "invalid_manifest";
                    data_.errorPointer = "/trees/" + name + "/continuation";
                    data_.hasErrorPointer = true;
                    data_.errorMessage = "only goal continuation is supported";
                    return;
                }
            }
            if (const Json* compact = value.find("compact_on_stage")) {
                if (compact->boolean()) {
                    data_.state = State::Invalid;
                    data_.errorCode = "invalid_manifest";
                    data_.errorPointer = "/trees/" + name + "/compact_on_stage";
                    data_.hasErrorPointer = true;
                    data_.errorMessage = "stage compaction is deferred to SRV-FEAT-596";
                    return;
                }
                tree.compactOnStage = compact->boolean();
                tree.hasCompactOnStage = true;
            }
            if (const Json* budget = value.find("token_budget"))
                tree.tokenBudget = static_cast<long long>(budget->number());
            for (const auto& agentName : tree.agents) {
                if (validName(agentName)) continue;
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorPointer = "/trees/" + name + "/agents";
                data_.hasErrorPointer = true;
                data_.errorMessage = "tree agent names must be file-safe segments";
                return;
            }
            data_.trees.push_back(std::move(tree));
        }
        data_.defaultTree = doc.stringOr("default_tree", "");
        bool found = false;
        for (const auto& tree : data_.trees)
            if (tree.name == data_.defaultTree) found = true;
        if (!found) {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorPointer = "/default_tree";
            data_.hasErrorPointer = true;
            data_.errorMessage = "default_tree must name a declared tree preset";
            return;
        }
    } else if (doc.find("default_tree")) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorPointer = "/default_tree";
        data_.hasErrorPointer = true;
        data_.errorMessage = "default_tree requires trees";
        return;
    }

    if (const Json* re = doc.find("run_effort")) {
        data_.runEffort = re->str();
        data_.hasRunEffort = true;
    }

    if (const Json* rs = doc.find("roles")) {
        data_.hasRoles = true;
        for (auto& m : rs->members()) {
            Role r;
            r.name = m.first;
            if (const Json* s = m.second.find("start_on_stage")) r.startOnStage = s->str();
            r.lane = m.second.stringOr("lane", "new");
            if (const Json* p = m.second.find("prompt_file")) r.promptFile = p->str();
            if (const Json* e = m.second.find("effort")) {
                r.effort = e->str();
                r.hasEffort = true;
            }
            data_.roles.push_back(std::move(r));
        }
    }

    struct Entry { std::string key; std::string pointer; std::string* declared; std::string* abs; };
    for (auto& repo : data_.repos) {
        const bool named = !repo.name.empty();
        const std::string keyPrefix = named ? "repos." + repo.name + "." : std::string();
        const std::string ptrPrefix = named ? "/repos/" + repo.name : std::string();
        if (named) {
            std::string abs;
            if (!resolveUnderRoot_(repo.path, ptrPrefix + "/path", "repos." + repo.name, abs))
                return;
            repo.absPath = abs;
        } else {
            repo.absPath = data_.root;
        }

        const Entry docEntries[] = {
            {keyPrefix + "docs.epics",   ptrPrefix + "/docs/epics",
             &repo.docs.epics,   &repo.docs.epicsAbs},
            {keyPrefix + "docs.tickets", ptrPrefix + "/docs/tickets",
             &repo.docs.tickets, &repo.docs.ticketsAbs},
            {keyPrefix + "docs.reports", ptrPrefix + "/docs/reports",
             &repo.docs.reports, &repo.docs.reportsAbs},
        };
        for (auto& e : docEntries) {
            std::string abs;
            if (!resolveUnder_(repo.absPath, *e.declared, e.pointer, e.key, abs)) return;
            *e.abs = abs;
        }

        const Entry optional[] = {
            {keyPrefix + "docs.wiki",    ptrPrefix + "/docs/wiki",
             &repo.docs.wiki,    &repo.docs.wikiAbs},
            {keyPrefix + "docs.escapes", ptrPrefix + "/docs/escapes",
             &repo.docs.escapes, &repo.docs.escapesAbs},
        };
        for (auto& e : optional) {
            if (e.declared->empty()) continue;
            std::string abs;
            if (!resolveUnder_(repo.absPath, *e.declared, e.pointer, e.key, abs)) return;
            *e.abs = abs;
        }
    }

    for (auto& w : data_.workspaces) {
        const Repo* owner = repoForWorkspace_(w);
        if (!owner) return;
        std::string abs;
        if (!resolveUnder_(owner->absPath, w.path, "/workspaces/" + w.name + "/path",
                           "workspaces." + w.name, abs)) return;
        w.absPath = abs;
    }
    if (!resolveToolchains_()) return;
    resolveCommands_();

    const auto effortIsUndeclared = [&](const std::string& value) {
        for (const Agent& profile : data_.agents)
            if (profile.hasEfforts
                && std::find(profile.efforts.begin(), profile.efforts.end(), value)
                   == profile.efforts.end())
                return profile.name;
        return std::string();
    };
    const auto refuseEffort = [&](const std::string& pointer, const std::string& value,
                                  const std::string& profileName, const std::string& what) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "undeclared_effort";
        data_.errorPointer = pointer;
        data_.hasErrorPointer = true;
        data_.errorField = value;
        data_.hasErrorField = true;
        data_.errorMessage = "\"" + value + "\" is not one of the efforts the profile \""
                           + profileName + "\" declares, so " + what + " could never be launched "
                             "at it. A start refused once a role is already running is a line in a "
                             "log nobody reads, so it is refused here instead.";
    };

    if (data_.hasRunEffort) {
        const std::string offender = effortIsUndeclared(data_.runEffort);
        if (!offender.empty()) {
            refuseEffort("/run_effort", data_.runEffort, offender, "a run");
            return;
        }
    }
    for (const Role& r : data_.roles) {
        if (!r.hasEffort) continue;
        const std::string offender = effortIsUndeclared(r.effort);
        if (!offender.empty()) {
            refuseEffort("/roles/" + r.name + "/effort", r.effort, offender,
                         "the role \"" + r.name + "\"");
            return;
        }
    }

    for (auto& r : data_.roles) {
        const std::string pointer = "/roles/" + r.name;
        if (r.lane != "new") {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorReason = "unsupported_role_lane";
            data_.errorPointer = pointer + "/lane";
            data_.hasErrorPointer = true;
            data_.errorMessage = "\"" + r.lane + "\" is not a lane a role can start in; the only "
                                 "value is \"new\", and a new lane is what makes the started role "
                                 "independent of the one that triggered it";
            return;
        }
        if (r.startOnStage.empty()) {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorReason = "empty_stage";
            data_.errorPointer = pointer + "/start_on_stage";
            data_.hasErrorPointer = true;
            data_.errorMessage = pointer + "/start_on_stage is empty; a role starts on a stage "
                                 "this repository reports, and the tool guesses at none";
            return;
        }
        std::string abs;
        if (!resolveUnderRoot_(r.promptFile, pointer + "/prompt_file",
                               "roles." + r.name + ".prompt_file", abs)) return;
        r.promptAbs = abs;
        if (!fs::exists(fs::path(abs))) {
            data_.state = State::Invalid;
            data_.errorCode = "invalid_manifest";
            data_.errorReason = "prompt_missing";
            data_.errorPointer = pointer + "/prompt_file";
            data_.hasErrorPointer = true;
            data_.errorPath = abs;
            data_.hasErrorPath = true;
            data_.errorMessage = "the role \"" + r.name + "\" names a prompt at \"" + r.promptFile
                               + "\" and there is no file there. A role is refused at load rather "
                                 "than when its stage fires.";
            return;
        }
    }

    bool defaultFound = false;
    for (Agent& profile : data_.agents) {
        data_.agent = profile;
        const std::size_t pathsBefore = data_.resolvedPaths.size();
        if (!resolveAgentPaths_() || !resolveStartPrompt_()) {
            if (doc.find("agents") && data_.errorPointer.compare(0, 7, "/agent/") == 0)
                data_.errorPointer.replace(0, 7, "/agents/" + profile.name + "/");
            return;
        }
        if (doc.find("agents"))
            for (std::size_t i = pathsBefore; i < data_.resolvedPaths.size(); ++i)
                if (data_.resolvedPaths[i].first.compare(0, 6, "agent.") == 0)
                    data_.resolvedPaths[i].first.replace(0, 6, "agents." + profile.name + ".");
        profile = data_.agent;
        if (profile.name == data_.defaultAgent) defaultFound = true;
    }
    if (!defaultFound) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorPointer = "/default_agent";
        data_.hasErrorPointer = true;
        data_.errorMessage = "default_agent must name a declared profile";
        return;
    }
    for (const Agent& profile : data_.agents)
        if (profile.name == data_.defaultAgent) data_.agent = profile;

    data_.state = State::Ok;
}

bool manifest::Store::resolveAgentPaths_()
{
    auto invalid = [&](const char* reason, const std::string& pointer,
                       const std::string& path, const std::string& message) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = reason;
        data_.errorPointer = pointer;
        data_.hasErrorPointer = true;
        data_.errorPath = path;
        data_.hasErrorPath = true;
        data_.errorMessage = message;
        return false;
    };

    std::istringstream words(data_.agent.command);
    std::string first;
    words >> first;
    if (first.empty())
        return invalid("empty_agent_command", "/agent/command", data_.manifestPath,
                       "/agent/command is empty; there is no program to run a lane with");

    std::error_code ec;
    fs::path found;
    if (first.find('/') != std::string::npos) {
        fs::path given(first);
        found = given.is_absolute() ? given : (fs::path(data_.root) / given);
    } else {
        const char* path = std::getenv("PATH");
        std::string rest = path ? path : "/usr/local/bin:/usr/bin:/bin";
        std::size_t at = 0;
        while (at <= rest.size()) {
            const std::size_t colon = rest.find(':', at);
            const std::string dir = rest.substr(at, colon == std::string::npos
                                                    ? std::string::npos : colon - at);
            if (!dir.empty()) {
                fs::path candidate = fs::path(dir) / first;
                if (fs::exists(candidate, ec) && ::access(candidate.c_str(), X_OK) == 0) {
                    found = candidate;
                    break;
                }
            }
            if (colon == std::string::npos) break;
            at = colon + 1;
        }
        if (found.empty())
            return invalid("agent_binary_missing", "/agent/command", first,
                           "\"" + first + "\" is not on PATH, so no lane could ever run this "
                           "repository's agent. The tool refuses the manifest rather than "
                           "starting lanes that die one at a time.");
    }

    fs::path real = fs::canonical(found, ec);
    if (ec) real = found;
    if (!fs::is_regular_file(real, ec) || ::access(real.c_str(), X_OK) != 0)
        return invalid("agent_binary_missing", "/agent/command", real.string(),
                       "\"" + first + "\" resolves to " + real.string()
                       + ", which is not an executable file");

    if (const std::string interpreter = interpreterOf(real.string()); !interpreter.empty()) {
        std::error_code interpEc;
        if (!fs::is_regular_file(fs::path(interpreter), interpEc)
            || ::access(interpreter.c_str(), X_OK) != 0)
            return invalid("agent_binary_missing", "/agent/command", interpreter,
                           "\"" + first + "\" resolves to " + real.string()
                         + ", a script whose interpreter \"" + interpreter + "\" is not an "
                           "executable file on this machine. Every lane would answer `201` for a "
                           "run whose process dies at `execve`, and a pid that never became the "
                           "agent is not a started run.");
    }

    data_.agent.binaryAbs = real.string();
    data_.agent.binaryDir = real.parent_path().string();

    if (isTooBroadForABind(data_.agent.binaryDir, developerHomeOfThisProcess()))
        return invalid("agent_binary_too_broad", "/agent/command", data_.agent.binaryDir,
                       "\"" + first + "\" resolves to " + data_.agent.binaryAbs
                     + ", whose directory is a whole home or the root of this machine. That "
                       "directory is read-bound into every lane for every workspace, which is the "
                       "boundary this tool exists to hold, removed by one manifest line. Put the "
                       "agent binary somewhere of its own.");
    data_.resolvedPaths.emplace_back("agent.binary", data_.agent.binaryAbs);
    data_.resolvedPaths.emplace_back("agent.binary_dir", data_.agent.binaryDir);

    if (!data_.agent.hasCredentialsDir) return resolveConfigTrust_();

    std::string declared = data_.agent.credentialsDir;
    if (declared.empty())
        return invalid("empty_path", "/agent/credentials_dir", data_.manifestPath,
                       "/agent/credentials_dir is empty; remove the key to bind nothing");
    if (declared.compare(0, 2, "~/") == 0) {
        const std::string home = developerHomeOfThisProcess();
        if (home.empty())
            return invalid("agent_credentials_missing", "/agent/credentials_dir", declared,
                           "\"" + declared + "\" starts at the home directory and this process "
                           "has no HOME to expand it against");
        declared = home + declared.substr(1);
    }

    fs::path credentials = fs::weakly_canonical(fs::path(declared), ec);
    if (ec) credentials = fs::path(declared);
    const char* laneHome = std::getenv("FACTORY_LANE_HOME");
    if (!fs::is_directory(credentials, ec) && laneHome && *laneHome
        && data_.agent.credentialsDir.compare(0, 2, "~/") == 0
        && !carriedCredentials_.empty()) {
        std::error_code carriedEc;
        if (fs::is_directory(fs::path(carriedCredentials_), carriedEc)) {
            credentials = fs::weakly_canonical(fs::path(carriedCredentials_), carriedEc);
            if (carriedEc) credentials = fs::path(carriedCredentials_);
        }
    }
    if (!fs::is_directory(credentials, ec))
        return invalid("agent_credentials_missing", "/agent/credentials_dir",
                       credentials.string(),
                       "the agent's credentials are declared at \"" + data_.agent.credentialsDir
                       + "\" and there is no directory there. An agent that discovers mid-run "
                         "that it cannot authenticate fails at the worst possible time.");

    if (isTooBroadForABind(credentials.string(), developerHomeOfThisProcess()))
        return invalid("agent_credentials_too_broad", "/agent/credentials_dir",
                       credentials.string(),
                       "the agent's credentials are declared at \"" + data_.agent.credentialsDir
                     + "\", which is a whole home or the root of this machine. Name the directory "
                       "the credentials are in, not the directory it sits in.");

    data_.agent.credentialsAbs = credentials.string();
    data_.resolvedPaths.emplace_back("agent.credentials", data_.agent.credentialsAbs);
    return resolveConfigTrust_();
}

bool manifest::Store::resolveToolchains_()
{
    const std::string realHome = developerHomeOfThisProcess();

    for (auto& w : data_.workspaces) {
        for (std::size_t i = 0; i < w.toolchain.size(); ++i) {
            ToolchainPath& t = w.toolchain[i];
            const std::string pointer = "/workspaces/" + w.name + "/toolchain/"
                                      + std::to_string(i);
            auto invalid = [&](const char* reason, const std::string& path,
                               const std::string& message) {
                data_.state = State::Invalid;
                data_.errorCode = "invalid_manifest";
                data_.errorReason = reason;
                data_.errorPointer = pointer;
                data_.hasErrorPointer = true;
                data_.errorPath = path;
                data_.hasErrorPath = true;
                data_.errorMessage = message;
                return false;
            };

            if (t.declared.empty())
                return invalid("empty_path", data_.manifestPath,
                               pointer + " is empty; remove the entry to bind nothing");

            std::string host = t.declared;
            if (host.compare(0, 2, "~/") == 0 || host == "~") {
                if (realHome.empty())
                    return invalid("toolchain_not_found", t.declared,
                                   "\"" + t.declared + "\" starts at the home directory and this "
                                   "process cannot tell where that is");
                host = realHome + host.substr(1);
            } else if (host.front() != '/') {
                return invalid("toolchain_not_absolute", t.declared,
                               "\"" + t.declared + "\" is neither absolute nor under `~/`. A "
                               "toolchain is a place on this machine, not a place in the "
                               "repository — the tool joins no path of its own.");
            }

            std::error_code ec;
            fs::path real = fs::weakly_canonical(fs::path(host), ec);
            if (ec) real = fs::path(host);
            const std::string shown = real.string();

            if (isTooBroadForABind(shown, realHome))
                return invalid("toolchain_too_broad", shown,
                               "\"" + t.declared + "\" is a whole home or the root of this "
                               "machine. It would be read-bound into every lane for this "
                               "workspace, which is the boundary this tool exists to hold, "
                               "removed by one manifest line. Name the toolchain, not the "
                               "directory it sits in.");

            if (const std::string named = collidesWithACredential(shown, realHome);
                !named.empty())
                return invalid("toolchain_holds_a_credential", shown,
                               "\"" + t.declared + "\" is, is under, or contains \"" + named
                             + "\", which holds a credential. It would be read-bound into every "
                               "lane for this workspace, and a lane that can reach the forge can "
                               "push, delete a branch, or open a pull request as the operator. "
                               "Name the toolchain, never the credential.");


            if (!fs::exists(real, ec)) {
                w.buildable = false;
                data_.warnings.push_back(
                    {"workspace_unbuildable", pointer, shown,
                     "the workspace \"" + w.name + "\" needs \"" + t.declared
                   + "\" to build and there is nothing there, so no lane for it can be claimed. "
                     "Every other workspace in this manifest is unaffected.",
                     w.name, t.declared});
                continue;
            }

            t.hostPath = shown;
            data_.resolvedPaths.emplace_back("workspaces." + w.name + ".toolchain." + t.declared,
                                             shown);
        }
    }
    return true;
}

namespace {

bool isShellWord(const std::string& w)
{
    static const std::set<std::string> words = {
        ":", ".", "[", "[[", "]]", "!", "alias", "bg", "break", "builtin", "case", "cd",
        "command", "continue", "declare", "do", "done", "echo", "elif", "else", "enable", "esac",
        "eval", "exec", "exit", "export", "false", "fg", "fi", "for", "function", "getopts",
        "hash", "help", "history", "if", "in", "jobs", "kill", "let", "local", "logout", "printf",
        "pwd", "read", "readonly", "return", "select", "set", "shift", "shopt", "source",
        "suspend", "test", "then", "time", "times", "trap", "true", "type", "typeset", "ulimit",
        "umask", "unalias", "unset", "until", "wait", "while",
    };
    return words.count(w) > 0;
}

bool isUnreadableWord(const std::string& w)
{
    if (w.empty() || w.front() == '-') return true;
    if (w.find_first_of("$`*?[]~") != std::string::npos) return true;
    for (unsigned char c : w)
        if (std::isalnum(c)) return false;
    return true;
}

bool isAssignmentWord(const std::string& w)
{
    const std::size_t eq = w.find('=');
    if (eq == 0 || eq == std::string::npos) return false;
    if (std::isdigit(static_cast<unsigned char>(w[0]))) return false;
    for (std::size_t i = 0; i < eq; ++i) {
        const unsigned char c = w[i];
        if (!(std::isalnum(c) || c == '_')) return false;
    }
    return true;
}

std::vector<std::string> programsIn(const std::string& command)
{
    std::vector<std::string> out;
    std::string word;
    bool haveWord = false;
    bool wantProgram = true;
    char quote = 0;

    auto endWord = [&] {
        if (!haveWord) return;
        if (wantProgram && !isAssignmentWord(word)) {
            out.push_back(word);
            wantProgram = false;
        }
        word.clear();
        haveWord = false;
    };

    for (std::size_t i = 0; i < command.size(); ++i) {
        const char c = command[i];
        if (quote) {
            if (c == quote) quote = 0;
            else { word.push_back(c); haveWord = true; }
            continue;
        }
        switch (c) {
        case '\'':
        case '"':
            quote = c;
            haveWord = true;
            continue;
        case '\\':
            if (i + 1 < command.size()) { word.push_back(command[++i]); haveWord = true; }
            continue;
        case ' ':
        case '\t':
            endWord();
            continue;
        case '&':
        case '|':
        case ';':
        case '\n':
        case '(':
        case ')':
        case '{':
        case '}':
            endWord();
            wantProgram = true;
            continue;
        case '<':
        case '>':
            endWord();
            while (i + 1 < command.size() && command[i + 1] != ' ' && command[i + 1] != '\t'
                   && command[i + 1] != ';' && command[i + 1] != '\n')
                ++i;
            continue;
        default:
            word.push_back(c);
            haveWord = true;
        }
    }
    endWord();
    return out;
}

bool isInside(const std::string& path, const std::string& root)
{
    if (root.empty() || path.empty()) return false;
    if (path == root) return true;
    return path.compare(0, root.size() + 1, root + "/") == 0;
}

bool isExecutableFile(const fs::path& p)
{
    std::error_code ec;
    return fs::is_regular_file(p, ec) && ::access(p.c_str(), X_OK) == 0;
}

std::string canonicalOf(const std::string& p)
{
    std::error_code ec;
    const fs::path real = fs::weakly_canonical(fs::path(p), ec);
    return ec ? p : real.string();
}

const char* const kUsrExecutableDirs[] = {
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
    "/usr/games", "/usr/local/games",
};

void addExecutableDirs(const fs::path& root, int maxDepth, std::size_t budget,
                       std::vector<std::string>& out)
{
    std::error_code ec;
    if (!fs::is_directory(root, ec)) return;
    out.push_back(root.string());

    std::vector<fs::path> level {root};
    for (int depth = 0; depth < maxDepth && !level.empty() && budget > 0; ++depth) {
        std::vector<fs::path> next;
        for (const fs::path& dir : level) {
            std::error_code it_ec;
            fs::directory_iterator it(dir, fs::directory_options::skip_permission_denied, it_ec);
            for (const fs::directory_iterator end; !it_ec && it != end; it.increment(it_ec)) {
                if (budget == 0) break;
                std::error_code e;
                if (it->is_symlink(e) || !it->is_directory(e)) continue;
                --budget;
                const std::string name = it->path().filename().string();
                if (name == "bin" || name == "sbin") out.push_back(it->path().string());
                next.push_back(it->path());
            }
        }
        level.swap(next);
    }
}

}

namespace {

using Workspace = manifest::Store::Workspace;

struct CommandSlot {
    const char* key;
    std::string Workspace::* text;
    bool Workspace::* declared;
};

const CommandSlot kCommands[] = {
    {"build",  &Workspace::build,  &Workspace::hasBuild},
    {"gate",   &Workspace::gate,   &Workspace::hasGate},
    {"review", &Workspace::review, &Workspace::hasReview},
};

}

const std::vector<std::string>& manifest::Store::commandKeys()
{
    static const std::vector<std::string> keys = [] {
        std::vector<std::string> out;
        for (const CommandSlot& slot : kCommands) out.emplace_back(slot.key);
        return out;
    }();
    return keys;
}

const std::string* manifest::Store::Workspace::command(const std::string& key) const
{
    for (const CommandSlot& slot : kCommands)
        if (key == slot.key) return this->*slot.declared ? &(this->*slot.text) : nullptr;
    return nullptr;
}

std::vector<manifest::Store::WorkspaceCommand> manifest::Store::Workspace::declaredCommands() const
{
    std::vector<WorkspaceCommand> out;
    for (const std::string& key : commandKeys())
        if (const std::string* text = command(key)) out.push_back({key, *text});
    return out;
}

void manifest::Store::resolveCommands_()
{
    for (const auto& w : data_.workspaces) {
        std::vector<std::string> roots {canonicalOf("/usr")};
        for (const auto& t : w.toolchain)
            if (!t.hostPath.empty()) roots.push_back(canonicalOf(t.hostPath));
        if (!data_.root.empty()) roots.push_back(canonicalOf(data_.root));

        auto insideTheLane = [&roots](const std::string& p) {
            for (const auto& r : roots) if (isInside(p, r)) return true;
            return false;
        };

        std::vector<std::string> dirs;
        for (const char* usr : kUsrExecutableDirs) {
            const std::string real = canonicalOf(usr);
            if (insideTheLane(real)) dirs.push_back(real);
        }
        for (const auto& t : w.toolchain)
            if (!t.hostPath.empty()) addExecutableDirs(fs::path(t.hostPath), 6, 4000, dirs);
        if (!data_.root.empty()) addExecutableDirs(fs::path(data_.root) / "bin", 0, 0, dirs);

        auto commands = w.declaredCommands();
        if (w.hasStartPreflight)
            commands.push_back({"start_preflight/command", w.startPreflight.command});
        for (const auto& command : commands) {
            const std::string pointer = "/workspaces/" + w.name + "/" + command.key;
            std::set<std::string> reported;

            for (const std::string& program : programsIn(command.text)) {
                if (isShellWord(program) || isUnreadableWord(program)) continue;
                if (reported.count(program)) continue;

                bool found = false;
                if (program.find('/') != std::string::npos) {
                    const fs::path given(program);
                    const fs::path abs = given.is_absolute()
                        ? given : (fs::path(w.absPath.empty() ? data_.root : w.absPath) / given);
                    const std::string real = canonicalOf(abs.string());
                    found = isExecutableFile(fs::path(real)) && insideTheLane(real);
                } else {
                    for (const std::string& dir : dirs)
                        if (isExecutableFile(fs::path(dir) / program)) { found = true; break; }
                }
                if (found) continue;

                reported.insert(program);
                data_.warnings.push_back(
                    {"command_unrunnable", pointer, "",
                     "the workspace \"" + w.name + "\" declares a " + command.key + " of \""
                       + command.text + "\" and nothing a lane claimed for it is given provides \""
                       + program + "\". A lane sees /usr, the toolchain this workspace declares "
                         "and its own worktree. Declare where \"" + program + "\" is in workspaces."
                       + w.name + ".toolchain, or declare a " + command.key
                       + " a lane can run — a lane IS still claimed for this workspace, so this "
                         "notice at load is the only warning before GATE.",
                     "workspaces." + w.name + ".toolchain", program});
            }
        }
    }
}

bool manifest::Store::resolveConfigTrust_()
{
    if (!data_.agent.hasConfigTrust) return true;

    auto invalid = [&](const std::string& path, const std::string& field,
                       const std::string& message) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "agent_config_untrusted";
        data_.errorPointer = "/agent/config_trust";
        data_.hasErrorPointer = true;
        data_.errorPath = path;
        data_.hasErrorPath = true;
        data_.errorField = field;
        data_.hasErrorField = true;
        data_.errorMessage = message;
        return false;
    };

    ConfigTrust& ct = data_.agent.configTrust;
    const std::string field =
        ct.keyedByPath + "[\"" + data_.root + "\"]." + ct.flag;

    for (const auto& pair : {std::make_pair("file", &ct.file),
                             std::make_pair("keyed_by_path", &ct.keyedByPath),
                             std::make_pair("flag", &ct.flag)}) {
        if (pair.second->empty())
            return invalid(data_.manifestPath, field,
                           std::string("/agent/config_trust/") + pair.first
                           + " is empty; remove agent.config_trust to check nothing");
    }

    std::error_code gitEc;
    const fs::path dotGit = fs::path(data_.root) / ".git";
    if (fs::is_regular_file(dotGit, gitEc))
        return invalid(dotGit.string(), field,
                       "\"" + data_.root + "\" is a linked worktree, not a checkout's own root, "
                       "and an agent keys its configuration on the repository a worktree was "
                       "made FROM. Whatever this check asked you to add would be a key nothing "
                       "reads. Point the factory at the main checkout, whose lanes are worktrees "
                       "of it and which is what agent.config_trust can speak for.");

    std::string declared = ct.file;
    if (declared.compare(0, 2, "~/") == 0) {
        const char* home = std::getenv("HOME");
        if (!home)
            return invalid(declared, field,
                           "agent.config_trust.file starts at the home directory and this "
                           "process has no HOME to expand it against");
        declared = std::string(home) + declared.substr(1);
    }
    std::error_code ec;
    fs::path file = fs::weakly_canonical(fs::path(declared), ec);
    if (ec) file = fs::path(declared);
    ct.fileAbs = file.string();

    const std::string fix =
        "the agent's configuration at \"" + ct.fileAbs + "\" does not record \"" + data_.root
        + "\" as trusted, so every run this tool starts would silently throw away the "
          "permissions this repository declares and ask about each one instead. Set "
        + field + " to true in that file — it is the file the agent READS, which on a machine "
          "where the agent has ever been run interactively is not the one the developer has "
          "been editing. RESTART THIS TOOL AFTERWARDS: the manifest is read once, at startup, so "
          "pressing Start again without a restart produces this same refusal from the copy that "
          "was loaded before the fix. And consider removing agent.config_trust altogether — "
          "without it the tool generates the trust each lane needs, in a directory it owns, and "
          "nobody has to edit a file in their home at all (SRV-FIX-052).";

    std::ifstream in(ct.fileAbs);
    if (!in) return invalid(ct.fileAbs, field, "there is no readable file there. " + fix);

    std::ostringstream buffer;
    buffer << in.rdbuf();
    Json::Error err;
    const Json doc = Json::parse(buffer.str(), err);
    if (!err.ok || !doc.isObject())
        return invalid(ct.fileAbs, field,
                       "that file is not a JSON object, so nothing can be read out of it. "
                       + fix);

    const Json* byPath = doc.find(ct.keyedByPath);
    const Json* mine = (byPath && byPath->isObject()) ? byPath->find(data_.root) : nullptr;
    const Json* flag = (mine && mine->isObject()) ? mine->find(ct.flag) : nullptr;
    if (!flag || !flag->isBool() || !flag->boolean()) return invalid(ct.fileAbs, field, fix);

    data_.resolvedPaths.emplace_back("agent.config_trust", ct.fileAbs);
    return true;
}

bool manifest::Store::resolveStartPrompt_()
{
    if (!data_.agent.hasStartPromptFile) return true;

    std::string abs;
    if (!resolveUnderRoot_(data_.agent.startPromptFile, "/agent/start_prompt_file",
                           "agent.start_prompt", abs)) return false;
    data_.agent.startPromptAbs = abs;

    if (!fs::exists(fs::path(abs))) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "start_prompt_missing";
        data_.errorPointer = "/agent/start_prompt_file";
        data_.hasErrorPointer = true;
        data_.errorPath = abs;
        data_.hasErrorPath = true;
        data_.errorMessage = "/agent/start_prompt_file names \"" + data_.agent.startPromptFile
                           + "\" and there is no file there. What starting a lane means is "
                             "refused at load rather than when somebody presses Start.";
        return false;
    }
    return true;
}

const manifest::Store::Repo* manifest::Store::repoForWorkspace_(const Workspace& w)
{
    if (!w.repo.empty()) {
        for (const Repo& r : data_.repos)
            if (r.name == w.repo) return &r;
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "unknown_repo";
        data_.errorPointer = "/workspaces/" + w.name + "/repo";
        data_.hasErrorPointer = true;
        data_.errorField = "repo";
        data_.hasErrorField = true;
        data_.errorMessage = "the workspace \"" + w.name + "\" belongs to a repository \""
                           + w.repo + "\" that no `repos` entry declares";
        return nullptr;
    }

    if (!data_.hasRepos) return &data_.repos.front();

    data_.state = State::Invalid;
    data_.errorCode = "invalid_manifest";
    data_.errorReason = "workspace_repo_missing";
    data_.errorPointer = "/workspaces/" + w.name + "/repo";
    data_.hasErrorPointer = true;
    data_.errorField = "repo";
    data_.hasErrorField = true;
    data_.errorMessage = "this manifest declares `repos`, so every workspace has to say which one "
                         "it belongs to — \"" + w.name + "\" says nothing, and its `path` would "
                         "have to be resolved against a repository the tool picked for it";
    return nullptr;
}

bool manifest::Store::resolveUnderRoot_(const std::string& declared, const std::string& pointer,
                                        const std::string& key, std::string& absOut)
{
    return resolveUnder_(data_.root, declared, pointer, key, absOut);
}

bool manifest::Store::resolveUnder_(const std::string& base, const std::string& declared,
                                    const std::string& pointer, const std::string& key,
                                    std::string& absOut)
{
    if (declared.empty()) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "empty_path";
        data_.errorPointer = pointer;
        data_.hasErrorPointer = true;
        data_.errorMessage = pointer + " is empty; the repository root is \".\"";
        return false;
    }

    std::error_code ec;
    fs::path candidate(declared);
    fs::path joined = candidate.is_absolute() ? candidate : (fs::path(base) / candidate);
    fs::path resolved = fs::weakly_canonical(joined, ec);
    if (ec) resolved = joined.lexically_normal();

    std::string abs = resolved.string();
    bool inside = (abs == base) || (abs.rfind(base + "/", 0) == 0);
    if (!inside) {
        data_.state = State::Invalid;
        data_.errorCode = "invalid_manifest";
        data_.errorReason = "path_escapes_repo";
        data_.errorPointer = pointer;
        data_.hasErrorPointer = true;
        data_.errorMessage = "\"" + declared + "\" leaves the repository "
                           + (base == data_.root ? "this tool was pointed at"
                                                 : "it is declared in (" + base + ")");
        return false;
    }

    absOut = abs;
    data_.resolvedPaths.emplace_back(key, abs);
    if (!fs::exists(resolved)) {
        data_.warnings.push_back({"path_missing", pointer, abs,
                                  "\"" + declared + "\" does not exist"});
    }
    return true;
}

int manifest::Store::httpStatus() const
{
    switch (data_.state) {
        case State::Ok:               return 200;
        case State::Missing:          return 404;
        case State::UnsupportedMajor: return 409;
        case State::Invalid:          return 422;
    }
    return 500;
}

namespace {

void writeStrings(std::ostringstream& o, const std::vector<std::string>& v)
{
    o << "[";
    for (std::size_t i = 0; i < v.size(); ++i) {
        if (i) o << ",";
        o << "\"" << manifest::jsonEscape(v[i]) << "\"";
    }
    o << "]";
}

}

std::string manifest::Store::toJson() const
{
    std::ostringstream o;
    o << "{\"factory\":\"" << jsonEscape(data_.version) << "\""
      << ",\"repo\":{\"name\":\"" << jsonEscape(data_.name) << "\""
      << ",\"root\":\"" << jsonEscape(data_.root) << "\""
      << ",\"git_root\":\"" << jsonEscape(data_.gitRoot) << "\"}"
      << ",\"path\":\"" << jsonEscape(data_.manifestPath) << "\"";

    if (data_.hasRepos) {
        o << ",\"repos\":{";
        for (std::size_t i = 0; i < data_.repos.size(); ++i) {
            const auto& r = data_.repos[i];
            if (i) o << ",";
            const std::string repoName = r.name.empty() ? data_.name : r.name;
            o << "\"" << jsonEscape(repoName) << "\":{\"path\":\"" << jsonEscape(r.path) << "\""
              << ",\"docs\":{\"epics\":\"" << jsonEscape(r.docs.epics) << "\""
              << ",\"tickets\":\"" << jsonEscape(r.docs.tickets) << "\""
              << ",\"reports\":\"" << jsonEscape(r.docs.reports) << "\"";
            if (!r.docs.wiki.empty()) o << ",\"wiki\":\"" << jsonEscape(r.docs.wiki) << "\"";
            if (!r.docs.escapes.empty())
                o << ",\"escapes\":\"" << jsonEscape(r.docs.escapes) << "\"";
            o << "}}";
        }
        o << "}";
    }

    o << ",\"workspaces\":{";
    for (std::size_t i = 0; i < data_.workspaces.size(); ++i) {
        const auto& w = data_.workspaces[i];
        if (i) o << ",";
        o << "\"" << jsonEscape(w.name) << "\":{\"path\":\"" << jsonEscape(w.path) << "\"";
        if (!w.repo.empty()) o << ",\"repo\":\"" << jsonEscape(w.repo) << "\"";
        if (w.hasGate) o << ",\"gate\":\"" << jsonEscape(w.gate) << "\"";
        if (w.hasBuild) o << ",\"build\":\"" << jsonEscape(w.build) << "\"";
        if (w.hasReview) o << ",\"review\":\"" << jsonEscape(w.review) << "\"";
        if (w.hasStartPreflight) {
            o << ",\"start_preflight\":{\"command\":\""
              << jsonEscape(w.startPreflight.command) << "\""
              << ",\"timeout_seconds\":" << w.startPreflight.timeoutSeconds;
            if (!w.startPreflight.exemptTicketIds.empty()) {
                o << ",\"exempt_ticket_ids\":";
                writeStrings(o, w.startPreflight.exemptTicketIds);
            }
            o << "}";
        }
        if (!w.toolchain.empty()) {
            o << ",\"toolchain\":[";
            for (std::size_t t = 0; t < w.toolchain.size(); ++t) {
                if (t) o << ",";
                o << "\"" << jsonEscape(w.toolchain[t].declared) << "\"";
            }
            o << "]";
        }
        o << "}";
    }
    o << "}";

    if (data_.hasResources) {
        o << ",\"resources\":{";
        for (std::size_t i = 0; i < data_.resources.size(); ++i) {
            const auto& r = data_.resources[i];
            if (i) o << ",";
            o << "\"" << jsonEscape(r.name) << "\":{\"exclusive\":"
              << (r.exclusive ? "true" : "false");
            if (!r.neededBy.empty()) { o << ",\"needed_by\":"; writeStrings(o, r.neededBy); }
            if (!r.devices.empty()) { o << ",\"devices\":"; writeStrings(o, r.devices); }
            if (r.hasDescription) o << ",\"description\":\"" << jsonEscape(r.description) << "\"";
            o << "}";
        }
        o << "}";
    }

    o << ",\"lanes\":{\"host\":" << data_.lanesHost << "}";

    auto writeDocs = [&](const Docs& d) {
        o << "{\"epics\":\"" << jsonEscape(d.epics) << "\""
          << ",\"tickets\":\"" << jsonEscape(d.tickets) << "\""
          << ",\"reports\":\"" << jsonEscape(d.reports) << "\"}";
    };
    if (data_.hasRepos) {
        o << ",\"repos\":{";
        for (std::size_t i = 0; i < data_.repos.size(); ++i) {
            const auto& r = data_.repos[i];
            if (i) o << ",";
            o << "\"" << jsonEscape(r.name) << "\":{\"path\":\"" << jsonEscape(r.path)
              << "\",\"docs\":";
            writeDocs(r.docs);
            o << "}";
        }
        o << "}";
    } else if (!data_.repos.empty()) {
        o << ",\"docs\":";
        writeDocs(data_.repos.front().docs);
    }

    if (data_.hasStages) { o << ",\"stages\":"; writeStrings(o, data_.stages); }
    if (data_.hasInFlightStatus || data_.hasDoneStatus) {
        o << ",\"status\":{";
        if (data_.hasInFlightStatus) {
            o << "\"in_flight\":";
            writeStrings(o, data_.inFlightStatus);
        }
        if (data_.hasDoneStatus) {
            o << (data_.hasInFlightStatus ? "," : "") << "\"done\":";
            writeStrings(o, data_.doneStatus);
        }
        o << "}";
    }
    if (data_.hasTicketFields) {
        o << ",\"display\":{\"ticket_fields\":";
        writeStrings(o, data_.ticketFields);
        o << "}";
    }
    if (data_.hasEvidence) {
        o << ",\"evidence\":{\"line_prefix\":\"" << jsonEscape(data_.evidence.linePrefix)
          << "\",\"frozen_after\":\"" << jsonEscape(data_.evidence.frozenAfter) << "\"}";
    }

    if (data_.hasRunEffort) o << ",\"run_effort\":" << jstr(data_.runEffort);
    o << ",\"default_agent\":" << jstr(data_.defaultAgent) << ",\"agents\":{";
    bool firstProfile = true;
    for (const Agent& profile : data_.agents) {
        if (!firstProfile) o << ",";
        firstProfile = false;
        o << jstr(profile.name) << ":{\"driver\":" << jstr(profile.driver)
          << ",\"command\":" << jstr(profile.command)
          << ",\"default_model\":" << jstr(profile.model) << ",\"models\":";
        writeStrings(o, profile.models);
        if (profile.hasEfforts) {
            o << ",\"efforts\":";
            writeStrings(o, profile.efforts);
        }
        if (profile.hasDefaultEffort)
            o << ",\"default_effort\":" << jstr(profile.defaultEffort);
        o << "}";
    }
    o << "}";
    if (data_.hasTrees) {
        o << ",\"default_tree\":" << jstr(data_.defaultTree) << ",\"trees\":{";
        for (std::size_t i = 0; i < data_.trees.size(); ++i) {
            if (i) o << ",";
            const Tree& tree = data_.trees[i];
            o << jstr(tree.name) << ":{\"decider\":" << jstr(tree.decider)
              << ",\"agents\":";
            writeStrings(o, tree.agents);
            if (!tree.continuation.empty())
                o << ",\"continuation\":" << jstr(tree.continuation);
            if (tree.hasCompactOnStage)
                o << ",\"compact_on_stage\":" << (tree.compactOnStage ? "true" : "false");
            if (tree.tokenBudget > 0) o << ",\"token_budget\":" << tree.tokenBudget;
            o << "}";
        }
        o << "}";
    }
    o << ",\"agent\":{\"command\":\"" << jsonEscape(data_.agent.command) << "\"";
    if (data_.agent.hasPermissionMode)
        o << ",\"permission_mode\":\"" << jsonEscape(data_.agent.permissionMode) << "\"";
    if (data_.agent.hasAllowedTools) {
        o << ",\"allowed_tools\":";
        writeStrings(o, data_.agent.allowedTools);
    }
    if (data_.agent.hasModel) o << ",\"model\":\"" << jsonEscape(data_.agent.model) << "\"";
    if (data_.agent.hasCredentialsDir)
        o << ",\"credentials_dir\":\"" << jsonEscape(data_.agent.credentialsDir) << "\"";
    if (data_.agent.hasStartPromptFile)
        o << ",\"start_prompt_file\":\"" << jsonEscape(data_.agent.startPromptFile) << "\"";
    o << "}";

    if (data_.hasRoles) {
        o << ",\"roles\":{";
        for (std::size_t i = 0; i < data_.roles.size(); ++i) {
            const auto& r = data_.roles[i];
            if (i) o << ",";
            o << "\"" << jsonEscape(r.name) << "\":{\"start_on_stage\":\""
              << jsonEscape(r.startOnStage) << "\",\"lane\":\"" << jsonEscape(r.lane)
              << "\",\"prompt_file\":\"" << jsonEscape(r.promptFile) << "\"";
            if (r.hasEffort) o << ",\"effort\":" << jstr(r.effort);
            o << "}";
        }
        o << "}";
    }

    o << ",\"resolved\":{\"paths\":{";
    for (std::size_t i = 0; i < data_.resolvedPaths.size(); ++i) {
        if (i) o << ",";
        o << "\"" << jsonEscape(data_.resolvedPaths[i].first) << "\":\""
          << jsonEscape(data_.resolvedPaths[i].second) << "\"";
    }
    o << "}}";

    o << ",\"warnings\":[";
    for (std::size_t i = 0; i < data_.warnings.size(); ++i) {
        const auto& w = data_.warnings[i];
        if (i) o << ",";
        o << "{\"code\":\"" << jsonEscape(w.code) << "\""
          << ",\"pointer\":\"" << jsonEscape(w.pointer) << "\"";
        if (!w.path.empty()) o << ",\"path\":\"" << jsonEscape(w.path) << "\"";
        if (!w.field.empty()) o << ",\"field\":\"" << jsonEscape(w.field) << "\"";
        if (!w.program.empty()) o << ",\"program\":\"" << jsonEscape(w.program) << "\"";
        o << ",\"message\":\"" << jsonEscape(w.message) << "\"}";
    }
    o << "]}";

    return o.str();
}

namespace {

std::string remedyFor(const std::string& code)
{
    if (code == "invalid_manifest" || code == "unsupported_manifest_major"
        || code == "not_found" || code == "workspace_unrunnable")
        return remedy::reloadManifest();
    return {};
}

}

std::string manifest::Store::errorJson() const
{
    std::ostringstream o;
    o << "{\"error\":{\"code\":\"" << jsonEscape(data_.errorCode) << "\""
      << ",\"message\":\"" << jsonEscape(data_.errorMessage) << "\""
      << ",\"detail\":{\"path\":\""
      << jsonEscape(data_.hasErrorPath ? data_.errorPath : data_.manifestPath) << "\"";
    if (data_.hasErrorPath) o << ",\"manifest\":\"" << jsonEscape(data_.manifestPath) << "\"";

    if (data_.state == State::UnsupportedMajor) {
        o << ",\"manifest\":\"" << jsonEscape(data_.version) << "\""
          << ",\"supported\":\"" << Config::FACTORY_VERSION << "\"";
    }
    if (data_.state == State::Invalid) {
        o << ",\"reason\":\"" << jsonEscape(data_.errorReason) << "\"";
        if (data_.hasErrorPointer)
            o << ",\"pointer\":\"" << jsonEscape(data_.errorPointer) << "\"";
        if (data_.hasErrorField)
            o << ",\"field\":\"" << jsonEscape(data_.errorField) << "\"";
        if (data_.hasErrorProgram)
            o << ",\"program\":\"" << jsonEscape(data_.errorProgram) << "\"";
        if (data_.errorOffset >= 0) o << ",\"offset\":" << data_.errorOffset;
    }
    o << "}"
      << remedy::member(remedyFor(data_.errorCode))
      << "}}";
    return o.str();
}

std::string manifest::Store::healthJson(unsigned long long uptimeSeconds,
                                       const std::string& extra) const
{
    const bool ok = data_.state == State::Ok;
    std::ostringstream o;
    o << "{\"factory\":\"" << Config::FACTORY_VERSION << "\""
      << ",\"manifest\":\"" << (ok ? "ok" : "invalid") << "\""
      << ",\"repo\":{\"name\":\"" << jsonEscape(data_.name) << "\""
      << ",\"root\":\"" << jsonEscape(data_.root) << "\""
      << ",\"git_root\":\"" << jsonEscape(data_.gitRoot) << "\"}"
      << ",\"uptime_s\":" << uptimeSeconds;
    if (!extra.empty()) o << "," << extra;
    if (!ok) {
        o << ",\"detail\":{\"reason\":\"" << jsonEscape(data_.errorMessage) << "\""
          << ",\"code\":\"" << jsonEscape(data_.errorCode) << "\"}";
    }
    o << "}";
    return o.str();
}

std::string manifest::Store::treeJson(const Tree& tree)
{
    std::ostringstream o;
    o << "{\"name\":" << jstr(tree.name)
      << ",\"decider\":" << jstr(tree.decider)
      << ",\"agents\":";
    writeStrings(o, tree.agents);
    if (!tree.continuation.empty()) o << ",\"continuation\":" << jstr(tree.continuation);
    if (tree.hasCompactOnStage)
        o << ",\"compact_on_stage\":" << (tree.compactOnStage ? "true" : "false");
    if (tree.tokenBudget > 0) o << ",\"token_budget\":" << tree.tokenBudget;
    o << "}";
    return o.str();
}
