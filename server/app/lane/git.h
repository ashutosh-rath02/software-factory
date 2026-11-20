#pragma once

#include <string>
#include <vector>

namespace lane::git {

struct Result {
    bool ok = false;
    int code = -1;
    std::string out;
};

Result run(const std::string& repoRoot, const std::vector<std::string>& args);

Result runQuiet(const std::string& cwd, const std::vector<std::string>& args);

bool isRepository(const std::string& repoRoot);
bool branchExists(const std::string& repoRoot, const std::string& branch);

enum class Presence {
    Absent,
    Present,
    Unknown,
};

Presence presenceOf(const std::string& path);

Result pruneWorktrees(const std::string& repoRoot);

// The branch is created only when it does not already exist. A release keeps the branch, so
// re-claiming a lane on the same (ticket, role) checks the same branch out again and the work is
// still there — which is the whole reason a release never deletes one. What that branch must
// CONTAIN on a re-claim is `OnExisting` below.
//
// SRV-FIX-184 — `base` is the commit-ish a NEW branch is cut from, and the caller says which it
// is. It used to be the literal `HEAD`, which is the MAIN CHECKOUT's commit: right for a run lane,
// and wrong for a role lane, whose whole purpose is the commits some other lane made. Cut from
// `HEAD` while a concurrent session committed to the base, `SRV-FIX-174`'s QA lane carried three
// commits that had nothing to do with the ticket and none of the fix it existed to judge.
//
// This function does not choose the base and must not: `HEAD` and `ticket/<id>` are two candidate
// sources that hold the same commit most of the time, and a default here is a default nobody
// reading a claim can see. `lane::Store::reserve_` resolves it.
//
// SRV-FIX-189 — and what happens when the branch is ALREADY there, which is every claim after
// the first. `E13` keeps the branch, so this is the ordinary path and not a corner.
//
// `Checkout` is what that path always did: check the branch out exactly where it was left. Right
// for a run lane — a lane re-claimed is as its own session left it, and a tool that moved
// somebody's branch between claims would be rewriting their work.
//
// `TakeWork` is the role's. A role judges the work and the work MOVES between rounds: the round
// after a QA FAIL opens with a RED test on the run branch, and a role lane cut from its own old
// branch judges the round before it — failing a second time for work already done, or, worse,
// passing a criterion the earlier round already held. So `base` is MERGED INTO the branch: the
// role's own commits are kept (its verdict is the evidence a release exists to preserve), `base`
// itself is not moved, and a `base` already in the branch's history is a no-op rather than an
// empty commit per claim.
enum class OnExisting {
    Checkout,
    TakeWork,
};

// `workNotTakenOut`, when given, is set on the ONE failure that is not git declining to make a
// worktree: the checkout was made, `base` would not come in, and everything was put back — the
// branch where it was, the checkout gone. The caller names that state itself, because "git could
// not create the worktree" and "the work under review will not merge into this role's own
// commits" have different ways out, and the second has none the tool may take (E12's
// `work_conflicts`, §11 check 12's exemption table).
//
// Nothing here half-applies. A tree carrying conflict markers from two rounds is worse than the
// stale tree this ticket is about, because it looks current.
Result addWorktree(const std::string& repoRoot, const std::string& branch,
                   const std::string& path, const std::string& base,
                   OnExisting onExisting, bool* workNotTakenOut = nullptr);

bool isDirty(const std::string& worktreePath, int& changesOut);

int unmergedCommits(const std::string& worktreePath, std::string& branchOut);

std::vector<std::string> ignoredContent(const std::string& worktreePath, bool& okOut,
                                        int limit = 5);

struct Holding {
    bool asked = false;
    int uncommitted = 0;
    int unmerged = 0;
    std::string branch;
    std::vector<std::string> ignored; // BY NAME. A count is the answer that sent a person to the
};

Holding whatItHolds(const std::string& worktreePath);

Result removeWorktree(const std::string& repoRoot, const std::string& path);

bool isRegisteredWorktree(const std::string& repoRoot, const std::string& path);

std::string worktreeForBranch(const std::string& repoRoot, const std::string& branch);

struct Registered {
    std::string path;
    std::string branch;
};

std::vector<Registered> registeredWorktrees(const std::string& repoRoot);

int spawnConfined(const std::vector<std::string>& argv, const std::string& cwd, int& readFdOut,
                  int stdinFd = -1, std::string* execErrorOut = nullptr);

void signalGroup(int pid, int sig);

bool groupIsEmpty(int pid);

bool isRunning(int pid);

}
