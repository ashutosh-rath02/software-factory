"""WEB-FIX-205 — a schema change runs the consuming workspace's GATE, not only its build.

Paths written `factory/…` in the prose below are HISTORICAL: they are what the tree looked like
while the factory was a subdirectory of the repository it was pointed at. This repository is its
own git repository and every path this file resolves is relative to IT — see section 5, which is
what it cost to learn that a check resolving one directory higher is a check that never ran.

`SRV-FIX-188` is a **factory-server** ticket. It added `POST /api/tickets/ids` to
`factory/docs/technical/contracts/http.schema.json` and regenerated `factory/client/web/src/types/api.ts`, which is the
correct and only allowed way to change that file. Its build target was the C++; its gate was
`pytest tests/ -v`. Nothing in its sequence ran the client, and `main` was pushed with
`factory/client/web/tests/api-types.test.ts::the_paths_are_the_ones_SPEC_5_names` red — eleven paths
in the generated file against ten in the case's literal. Two days earlier `SRV-FEAT-180` widened
`Resource` in the same file and left the same workspace not compiling. Same class, twice.

**Why the build is not enough, which is the whole of this ticket.** An extra path in a generated
type breaks no type: the client still builds. The command that says no is the client's own
**gate** — `cd factory/client/web && npm run test`, which `.factory/factory.json` declares for
`factory-client` — and no factory-server ticket runs it. A forcing case that stops at the build
reports success over a workspace whose declared gate is red, which is the same silence one notch
shallower.

So both commands are taken from the manifest and both are run. `.factory/factory.json` already
declares a `build` and a `gate` for every workspace that has them; this file is what makes a
schema edit pay for both of them.

**What it costs, stated plainly.** Nothing at all to a ticket that does not touch a declared
source: the trigger is one `git diff` over four paths and no toolchain is looked for. A ticket
that DOES touch one pays `npm run build` **and** `npm run test` in `factory/client/web`. The ticket
pre-decided that trade — the cost falls on exactly the class that produced both escapes, and it
is the class that has no other gate.

**Not landed here:** `WEB-FIX-200` owns the build half of this mechanism and the case that keeps
the trigger list honest. It had not landed when this file was written, so the build is run here
too rather than left unguarded; when `WEB-FIX-200` merges, its
`test_a_schema_change_builds_the_workspace_generated_from_it` is a subset of the case below and
the two are resolved into one.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
CLIENT = REPO / "client" / "web"
MANIFEST = REPO / ".factory" / "factory.json"


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def base_against_main():
    """The commit this branch left `main` at, or None if git cannot say.

    None is treated as CHANGED by the caller. A check that cannot tell whether the schema moved
    and answers *no* is the shape of miss this file exists for.
    """
    found = _git("merge-base", "HEAD", "origin/main")
    return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None


def schemas_changed_since(base):
    """Which of the declared sources differ between `base` and the WORKING TREE.

    Working tree, not `HEAD`: a session that edits a schema and runs the suite before committing
    is the ordinary case, and it is the moment the answer is worth having.
    """
    sources = generated_from()
    found = _git("diff", "--name-only", base, "--", *sources)
    if found.returncode != 0:
        return sorted(sources)
    return sorted(set(found.stdout.split()) & set(sources))


def changed_sources():
    base = base_against_main()
    return sorted(generated_from()) if base is None else schemas_changed_since(base)


def npm():
    """The `npm` this repository's client is built with, or None.

    A lane's PATH does not carry it: the manifest binds `~/.nvm` read-only and nothing sources it,
    so the version directory is looked at directly rather than assumed absent.
    """
    found = shutil.which("npm")
    if found:
        return found
    for bindir in sorted((Path.home() / ".nvm" / "versions" / "node").glob("*/bin")):
        if (bindir / "npm").is_file():
            return str(bindir / "npm")
    return None


def declared(name, key):
    """The `build` or `gate` command `.factory/factory.json` declares for a workspace.

    Taken from the manifest rather than written here. The escape was a workspace being unchecked
    by a gate that could not see it; a check that then hardcodes `npm run test` can only ever
    force the ONE workspace somebody had in mind. The manifest is where this repository says what
    building and gating a workspace mean, so it is what gets run.
    """
    workspaces = json.loads(MANIFEST.read_text())["workspaces"]
    assert name in workspaces, (
        f"a generated artefact was assigned to `{name}`, which "
        f"{MANIFEST.relative_to(REPO)} does not declare")
    command = workspaces[name].get(key)
    assert command, (
        f"`{name}` has a generated artefact read from a source that can change here, and the "
        f"manifest declares no `{key}` for it, so no gate can say whether it still holds.\n"
        f"  .factory/factory.json is hand-written by the developer — this needs a ticket, not an "
        f"edit from an agent.")
    return command


def commands_for(name):
    """The two commands a change to this workspace's source must pay for, in the order run.

    The build first, because a workspace that does not compile makes its suite's output noise —
    and both, because a build that passes is not a gate that passed (WEB-FIX-205).
    """
    return [declared(name, "build"), declared(name, "gate")]


def run(command):
    """Run one declared command from the repository root and hand back the result."""
    env = dict(os.environ)
    if "npm" in command:
        tool = npm()
        assert tool, (
            f"`{command}` needs npm, which was not found on PATH or under ~/.nvm.\n"
            f"  export PATH=\"$HOME/.nvm/versions/node/<version>/bin:$PATH\"")
        env["PATH"] = os.pathsep.join([str(Path(tool).parent), os.environ.get("PATH", "")])
    return subprocess.run(["bash", "-c", command], cwd=REPO, env=env,
                          capture_output=True, text=True)


# ── 1 · the live case ────────────────────────────────────────────────────────────────────────

def test_a_schema_change_runs_the_consuming_workspaces_gate():
    changed = changed_sources()
    if not changed:
        return

    failures = []
    sources = generated_from()
    for path in changed:
        name = sources[path]
        for command in commands_for(name):
            done = run(command)
            if done.returncode != 0:
                failures.append(
                    f"{path} changed on this branch and `{name}` does not pass `{command}`.\n"
                    f"exit={done.returncode}\n{done.stdout}{done.stderr}")

    assert not failures, (
        "a source this workspace's files are GENERATED from changed, and the workspace's own "
        "declared commands say no:\n\n" + "\n\n".join(failures))


# ── 2 · negative (mandatory) — the build alone leaves the widened schema red ──────────────────

def test_only_running_the_build_would_leave_the_widened_schema_red():
    """The forbidden cheap fix, executed rather than argued.

    `WEB-FIX-205`'s escape is put back into the working tree — the `E26` line taken out of
    `tests/api-types.test.ts`, which is the state `origin/main` was pushed in, and a no-op on a
    tree that still carries it — and then each declared command is asked about it in turn.

    The **build** is silent: `toEqual` is a runtime assertion, and `tsc` type-checks a ten-member
    object literal exactly as happily as an eleven-member one, so a forcing case that stops there
    reports success over a workspace whose own gate is red. The **gate** names the case. That
    difference is the whole reason case 1 runs both.

    It fires on the same trigger as case 1, for case 1's reason: the cost belongs on the class of
    ticket that changes a source, and nowhere else.
    """
    if not changed_sources():
        return

    case = CLIENT / "tests" / "api-types.test.ts"
    original = case.read_text()
    escaped = original.replace("      E26: '/api/tickets/ids',\n", "")
    build, gate = commands_for("factory-client")
    try:
        case.write_text(escaped)
        built = run(build)
        gated = run(gate)
    finally:
        case.write_text(original)

    blind = f"{built.stdout}{built.stderr}"
    assert "api-types" not in blind and "the_paths_are_the_ones_SPEC_5_names" not in blind, (
        f"`{build}` mentions the widened schema, so this negative no longer says what it claims — "
        f"re-derive it before trusting case 1:\nexit={built.returncode}\n{blind[-4000:]}")

    seen = f"{gated.stdout}{gated.stderr}"
    assert gated.returncode != 0 and "the_paths_are_the_ones_SPEC_5_names" in seen, (
        f"`{gate}` did not refuse the widened schema, so running it after the build buys nothing "
        f"and case 1 is ceremony.\nexit={gated.returncode}\n{seen[-4000:]}")


# ── 3 · negative — the gate is really among the commands, and is not the build ────────────────

def test_the_forcing_case_runs_the_declared_gate_and_not_only_the_build():
    """Case 2 costs a toolchain and fires on a trigger; this one costs nothing and always runs.

    The cheap fix case 2 forbids is one deleted line in `commands_for`, and on a branch that
    touches no declared source there would be nothing to notice it. So the composition is
    asserted directly, against the manifest and not against a string written here.
    """
    for name in sorted(set(generated_from().values())):
        commands = commands_for(name)
        assert commands == [declared(name, "build"), declared(name, "gate")], (
            f"the commands a source change makes `{name}` pay for are {commands}, which are no "
            f"longer the manifest's `build` and `gate` for it")
        assert commands[0] != commands[1], (
            f"the manifest declares the same command as `{name}`'s build and its gate, so "
            f"running both is one command twice and this ticket's mechanism is not there")


# ── 4 · SRV-FIX-202 round 3 — the same question, asked without a toolchain ────────────────────
#
# `SRV-FIX-202` failed QA twice. `factory/docs/reports/SRV-FIX-202-round3-analysis.md` is the
# analysis the second failure bought, and this block is the one structural change it licenses.
#
# **What the three cases above cannot do here.** Every one of them answers the content question by
# RUNNING the consuming workspace's declared commands, and `factory-client`'s are `npm run build`
# and `npm run test`. A lane claimed for `factory` or for `factory-server` is given `~/.gitconfig`
# and `~/.config/gh`; it has no `npm`, no `~/.nvm` and no `node_modules`. So in the lane where a
# `factory-server` ticket edits `factory/docs/technical/contracts/http.schema.json`, case 1 dies on `run()`'s
# `assert tool` — with a message about `npm` — before it reaches the file it was written to guard.
#
# That is precisely how round 2 of `SRV-FIX-202` shipped: it added `program` to `$defs/warning` in
# `factory/docs/technical/contracts/http.schema.json`, left `factory/client/web/src/types/api.ts` byte-identical to
# `origin/main`, and the case that exists to catch exactly that was red for `npm not found`. QA's
# words for it, and they are the sentence this block is written from:
#
#   *"an environment skip that reads as a lane problem is a gate that says nothing where it should
#    say no."*
#
# **What is added, and what it deliberately is not.** The byte-exact question —
# `expect(generate(schema)).toBe(checkedIn)`, in `factory/client/web/tests/api-schema.test.ts` — stays
# where it is, inside the workspace whose toolchain can ask it. Underneath it goes the weaker
# question that **cannot be skipped**: a source changed since this branch left `main`, and a file
# generated from it did not. That is `git diff` and nothing else — no node, no npm, no
# `node_modules`, no browser — so it is red in the lane where the edit is actually made.
#
# **Why the relation is read off the generators rather than written here.** Nothing in this
# repository mapped a source to the ARTEFACT generated from it, which is why the only checkable
# form of *"the generated file is current"* cost a toolchain. Writing that map by hand would put a
# fourth list beside three that already exist and leave it to rot — which is exactly what happened
# to the hand-written one this file used to carry, and `WEB-FIX-370` is the bill.
# `client/web/scripts/gen-*.mjs` already declares both ends — `const SCHEMA/NOCTURNE/PEN =
# new URL(<source>)` and `const OUT = new URL(<artefact>)` — so a generator added tomorrow extends
# this check the day it is added, and one that changes where it writes moves the check with it.

CLIENT_SCRIPTS = CLIENT / "scripts"

_DECLARED_URL = re.compile(
    r"const\s+(\w+)\s*=\s*new URL\(\s*'([^']+)'\s*,\s*import\.meta\.url\s*\)")


def _relative(path):
    return str(Path(path).resolve().relative_to(REPO))


def sources_and_artefact(script, text=None):
    """What one generator reads and what it writes, taken from the generator's own source.

    `OUT` is the artefact by the convention all three scripts already share; every other
    `new URL(..., import.meta.url)` in the file is a source it is generated from.
    """
    sources, artefact = [], None
    for name, rel in _DECLARED_URL.findall(script.read_text() if text is None else text):
        resolved = _relative(script.parent / rel)
        if name == "OUT":
            artefact = resolved
        else:
            sources.append(resolved)
    return sorted(sources), artefact


def generates(commit=None):
    """Generator declarations from the working tree or the named historical commit."""
    found = {}
    if commit is None:
        scripts = sorted(CLIENT_SCRIPTS.glob("gen-*.mjs"))
    else:
        listed = _git("ls-tree", "-r", "--name-only", commit, "--", "client/web/scripts")
        assert listed.returncode == 0, listed.stderr
        scripts = [REPO / rel for rel in listed.stdout.splitlines()
                   if Path(rel).match("gen-*.mjs")]
    assert scripts, "no generators were found in the requested source tree"
    for script in scripts:
        text = None
        if commit is not None:
            captured = _git("show", f"{commit}:{script.relative_to(REPO)}")
            assert captured.returncode == 0, captured.stderr
            text = captured.stdout
        sources, artefact = sources_and_artefact(script, text)
        assert artefact, (
            f"{script.name} declares no `const OUT = new URL(..., import.meta.url)`, so nothing "
            f"here can say which file it generates and a change to its source would go unwatched")
        assert sources, (
            f"{script.name} declares an OUT and no source, so nothing here can say what would "
            f"make its output stale")
        for source in sources:
            found.setdefault(source, []).append(artefact)
    return {source: sorted(set(artefacts)) for source, artefacts in found.items()}


def workspace_of(artefact):
    """The workspace whose declared `path` contains this artefact — the manifest's answer.

    Longest declared path wins, because `factory` declares `.` and every workspace is under it;
    the one that owns a file is the most specific one that contains it.
    """
    workspaces = json.loads(MANIFEST.read_text())["workspaces"]
    owners = sorted(
        (name for name, spec in workspaces.items()
         if Path(artefact).is_relative_to(Path(spec["path"]))),
        key=lambda name: len(Path(workspaces[name]["path"]).parts), reverse=True)
    assert owners, (
        f"{artefact} is generated and lies under no workspace {MANIFEST.relative_to(REPO)} "
        f"declares, so no build and no gate can be asked whether it still holds")
    return owners[0]


def generated_from():
    """source → the workspace whose files are GENERATED from it, derived end to end.

    The generators say which artefact a source produces; the manifest says whose that artefact is.
    Neither end is typed here, and that is the whole of `WEB-FIX-370`'s structural half: the list
    that used to sit at the top of this file named `client/web/design/Ticket.dc.html`, which no
    generator reads and which is not a file, under a path prefix this repository stopped having.
    """
    found = {}
    for source, artefacts in generates().items():
        owners = {workspace_of(artefact) for artefact in artefacts}
        assert len(owners) == 1, (
            f"{source} generates {artefacts}, which belong to {sorted(owners)} — a change to it "
            f"has to pay for every one of them, and this map can only name one")
        found[source] = owners.pop()
    return found


def changed_paths_since(rev):
    """Every path that differs between `rev` and the WORKING TREE, for case 4's reason.

    Working tree rather than HEAD, exactly as `schemas_changed_since` above: the moment worth
    asking about is the one where a session has edited a schema and not yet regenerated.

    The case below asks at two revisions, because one is not enough (`WEB-FIX-370`). Against the
    merge-base it answers *this branch never regenerated*, which is `SRV-FIX-453`'s shape; against
    `HEAD` it answers *you have edited a source since your last commit and not regenerated*, which
    is the shape a branch that ALREADY regenerated once hides — the artefact is in the branch's
    diff for the earlier edit, so the base question is silent about the second one.
    """
    found = _git("diff", "--name-only", rev)
    return set(found.stdout.split()) if found.returncode == 0 else set()


def stale_artefacts(changed, mapping):
    """The (source, artefact) pairs where the source moved off `base` and the artefact did not.

    A pure function of two sets, so the two negatives below can hand it the states this branch
    happens not to be in.
    """
    return sorted(
        (source, artefact)
        for source in sorted(set(mapping) & set(changed))
        for artefact in mapping[source]
        if artefact not in changed)


def test_a_changed_source_leaves_no_file_generated_from_it_at_its_baseline():
    """SRV-FIX-202 round 3, transcribed from round 2's QA reproduction.

    The state it pins, measured by QA on `SRV-FIX-202`'s round-2 branch and reproduced in the
    round-3 lane before this case was written:

        $ git diff origin/main --name-only -- factory/docs/technical/contracts/http.schema.json
        factory/docs/technical/contracts/http.schema.json
        $ git diff origin/main --name-only -- factory/client/web/src/types/api.ts
        (nothing)

    Nothing about a toolchain reaches this case, which is the whole of its value: it is the same
    finding case 1 would have made, in a lane that cannot run case 1.
    """
    base = base_against_main()
    assert base, (
        "git cannot say where this branch left `origin/main`, so this case cannot tell whether a "
        "generated file is stale. It refuses rather than passes: a check that answers *no* when it "
        "cannot answer is the miss this file exists for.")

    mapping = generates()
    stale = {}
    for since, what in ((base, f"changed since {base[:8]}"), ("HEAD", "changed since HEAD")):
        for pair in stale_artefacts(changed_paths_since(since), mapping):
            stale.setdefault(pair, what)

    assert not stale, (
        "a source changed and a file GENERATED from it did not move with it, so what the client "
        "compiles and tests against no longer describes the contract:\n\n"
        + "\n".join(
            f"  {source}\n"
            f"      {what}, and {artefact} did not.\n"
            f"      cd client/web && npm run gen:api-types && npm run gen:types && "
            f"npm run gen:tokens\n"
            f"      then `npm run test`, which asserts the generated file byte for byte."
            for (source, artefact), what in sorted(stale.items())))


def test_the_baseline_check_names_a_generated_file_that_stayed_behind():
    """Negative (mandatory) — the cheap fix that reports nothing.

    The state is the one above, handed in directly rather than depending on this branch: a source
    in the changed set, its artefact absent from it. A check that answers `[]` here is the check
    round 2 shipped past.
    """
    mapping = {"docs/technical/contracts/http.schema.json": ["client/web/src/types/api.ts"]}
    assert stale_artefacts({"docs/technical/contracts/http.schema.json"}, mapping) == [
        ("docs/technical/contracts/http.schema.json", "client/web/src/types/api.ts")]


def test_the_baseline_check_is_quiet_when_the_generated_file_moved_with_its_source():
    """Negative (mandatory) — the cheap fix that reports everything.

    A source and its artefact both changed is the ordinary, correct commit. A check that still
    names it makes the case above unbelievable, and every regeneration would have to argue with it.
    """
    mapping = {"docs/technical/contracts/http.schema.json": ["client/web/src/types/api.ts"]}
    both = {"docs/technical/contracts/http.schema.json", "client/web/src/types/api.ts"}
    assert stale_artefacts(both, mapping) == []
    assert stale_artefacts(set(), mapping) == []



# ── 5 · WEB-FIX-370 — the pairs are every pair, and the predicate is asked at a COMMIT ────────
#
# `SRV-FIX-453` edited `docs/technical/contracts/http.schema.json`, left `client/web/src/types/api.ts` at its
# baseline bytes, and merged. Everything above was written to refuse exactly that and none of it
# did, because every path in this file resolved one directory ABOVE this repository: `REPO` was
# the factory's PARENT, so `MANIFEST` named a file that does not exist, `GENERATED_FROM`'s four
# keys were paths that do not exist, and `_git` ran outside the repository being judged. Five of
# the seven cases were red for an environment reason on every branch, which is a set a session
# reads as `main`'s and owns none of.
#
# So the root is this repository — `CLAUDE.md`: *a path that walks above this directory is a
# defect* — and the source → artefact map stays derived from the generators, which is the only
# list here that has never drifted. `GENERATED_FROM` had, twice over: it named
# `client/web/design/Ticket.dc.html`, which no generator reads and which is not a file.
#
# The predicate is `stale_artefacts`, unchanged and pure. Case 4 feeds it the working tree
# against this branch's merge-base; the two negatives below feed it ONE COMMIT, which is what a
# ticket becomes on `main` under the squash rule — so the commit that caused this is a case
# rather than a paragraph.

CAUSED_THIS = "325425deebc2c14d343aa864da0f484323b66eaf"   # SRV-FIX-453, the schema without the type
DOCUMENTS_ONLY = "7c1a3eae7c4b3f38357c046f75d75c1aa7f84f23"  # SRV-FIX-453 filing its own ticket
SERVER_ONLY = "759767853eb15cb6cfc8418d8ed697f3f8b6545f"   # SRV-FIX-376, server/ and nothing else


def changed_in(sha):
    """Every path one commit touched, repo-relative — the unit `main` is written in.

    One commit per ticket reaches `main` (`git merge --squash`), so a commit here is a ticket,
    and asking the predicate about a commit is asking it about a ticket that landed.
    """
    assert _git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0, (
        f"{sha[:8]} is not in this repository, so the case cannot be driven at it. It refuses "
        f"rather than passes: a negative that cannot reach its own commit has not run.")
    found = _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    assert found.returncode == 0, f"git could not list {sha[:8]}: {found.stderr}"
    return set(found.stdout.split())


def test_every_generator_pair_is_watched():
    """§1 criterion 2 — the three pairs, derived from the generators and not typed here.

    `CLAUDE.md` names three commands that turn a source of truth into code. This asserts the
    watched set IS that set — one artefact per `gen-*.mjs`, every source and every artefact a
    real file in this repository, and every artefact under a workspace the manifest declares a
    `build` and a `gate` for, because those are the two commands a change to it has to pay for.
    A pair nobody derived is a pair nobody watches, and `GENERATED_FROM` naming a file that does
    not exist is what that looks like when it is written down by hand.
    """
    scripts = sorted(CLIENT_SCRIPTS.glob("gen-*.mjs"))
    assert len(scripts) >= 3, (
        f"{CLIENT_SCRIPTS} holds {len(scripts)} generators and CLAUDE.md names three commands "
        f"that turn a source of truth into code — this sweep has examined nothing")

    mapping = generates()
    assert mapping, "no generator declares a source, so this check watches nothing"

    artefacts = {artefact for artefacts in mapping.values() for artefact in artefacts}
    assert len(artefacts) == len(scripts), (
        f"{len(scripts)} generators write {sorted(artefacts)} — one of them is unwatched")

    for source, generated in sorted(mapping.items()):
        assert (REPO / source).is_file(), f"{source} is generated from and is not a file here"
        for artefact in generated:
            assert (REPO / artefact).is_file(), f"{artefact} is generated and is not a file here"
            name = workspace_of(artefact)
            assert declared(name, "build") and declared(name, "gate"), name


def test_the_commit_that_landed_a_schema_without_its_generated_file_is_named():
    """§1 criterion 3, MANDATORY NEGATIVE — the predicate at the commit that caused this.

    A guard that passes on the commit it was written for is evidence about the guard. This drives
    the same `stale_artefacts` case 4 drives, over the same derived map, at `325425de`, and
    requires it to name the pair by the generator paths that commit used.
    """
    assert stale_artefacts(changed_in(CAUSED_THIS), generates(CAUSED_THIS)) == [
        ("docs/http.schema.json", "client/web/src/types/api.ts")]


def test_a_commit_that_moves_no_generator_source_is_not_named():
    """§1 criterion 4, MANDATORY NEGATIVE — a guard that fires on everything gets switched off.

    Two commits that touch nothing any generator reads: one all documents, one all server code.
    Both are real commits on `main`, so this is the predicate's answer about work that actually
    landed rather than about a set assembled here.
    """
    mapping = generates()
    for sha in (DOCUMENTS_ONLY, SERVER_ONLY):
        changed = changed_in(sha)
        assert changed, f"{sha[:8]} touched no file, so this negative examined nothing"
        assert stale_artefacts(changed, mapping) == [], sha


from test_isolation import laneenv, run_in_lane                    # noqa: E402,F401


# ── SRV-FIX-532 cluster E3 — the check a schema change pays for is one a LANE can run ─────────
#
# `SRV-FIX-517`. Every case above runs the consuming workspace's declared commands from the
# REPOSITORY ROOT, which is a developer's workstation. Every ticket is worked in a lane, and a
# `factory-server` lane is bound `~/.gitconfig` and `~/.local/bin` and deliberately not `~/.nvm` —
# so the gate a schema edit is supposed to pay for was one no lane could run, and the forcing case
# was a check that only ever ran where nobody works.


def test_the_consuming_workspaces_commands_run_inside_a_lane(laneenv, tmp_path):
    """§1 E3 — the consuming workspace's declared commands run INSIDE a lane that names it.

    Driven on a repository built here rather than on this one, because what is under test is the
    RULE — a ticket naming a second workspace gets that workspace's toolchain — and this
    repository's own consuming toolchain (`~/.nvm`) is a fact about the machine. The consuming
    workspace's declared command EXECs something that only exists inside its toolchain, so the
    assertion is the command running and not a bind in an argv.
    """
    toolchain = tmp_path / "consumer-toolchain"
    (toolchain / "bin").mkdir(parents=True)
    (toolchain / "bin" / "generate").write_text("#!/bin/sh\necho generated-inside-the-lane\n")
    (toolchain / "bin" / "generate").chmod(0o755)

    env = laneenv.build(
        workspaces={"core": {"path": ".", "toolchain": []},
                    "consumer": {"path": ".", "toolchain": [str(toolchain)],
                                 "build": f"{toolchain}/bin/generate"}},
        tickets=("TCK-001",))
    root = Path(env.root)
    (root / "docs" / "tickets" / "TCK-001.md").write_text(
        "---\nid: TCK-001\nworkspace: core\ntitle: a schema change\nstatus: todo\n"
        "workspaces: core consumer\n---\n\nbody\n")
    # Only the ticket: `git add -A` here sweeps in whatever else the fixture left beside it, and
    # the teardown asserts the main checkout is untouched for exactly that reason.
    subprocess.run(["git", "add", "docs/tickets/TCK-001.md"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "a ticket that names the consumer"], cwd=root,
                   check=True, capture_output=True)

    lane = env.claim()
    command = json.loads((root / ".factory" / "factory.json").read_text())[
        "workspaces"]["consumer"]["build"]
    rc, out = run_in_lane(env, lane, command)
    assert rc == 0 and "generated-inside-the-lane" in out, (
        f"the consuming workspace's DECLARED build could not run inside a lane whose ticket names "
        f"that workspace: rc={rc} {out!r}\n"
        f"That is the check a schema change is supposed to pay for, and it only ever ran on a "
        f"workstation.")


def test_a_lane_missing_the_consuming_toolchain_is_refused_by_name(laneenv, tmp_path):
    """§1 E-NEG — a lane that does NOT hold the consuming toolchain says so, by name.

    MANDATORY NEGATIVE. The failure a session actually got was `npm: command not found`, which
    reads as *this gate is broken* rather than *this gate is not mine to run* — and three sessions
    debugged the wrong thing. A lane whose ticket does not name the consuming workspace must fail
    in a way that names the workspace and the toolchain it was not given.

    Asserted against the tool's own claim line, which is where §1 lets it say what a lane holds
    without widening a surface.
    """
    toolchain = tmp_path / "consumer-toolchain"
    (toolchain / "bin").mkdir(parents=True)
    (toolchain / "bin" / "generate").write_text("#!/bin/sh\necho generated\n")
    (toolchain / "bin" / "generate").chmod(0o755)

    env = laneenv.build(
        workspaces={"core": {"path": ".", "toolchain": []},
                    "consumer": {"path": ".", "toolchain": [str(toolchain)],
                                 "build": f"{toolchain}/bin/generate"}},
        tickets=("TCK-001",))
    lane = env.claim()

    said = [line for line in env.srv.log.read_text().splitlines()
            if "holds the toolchain of" in line]
    assert said, "the tool never said what this lane holds, so a session cannot tell whose gate it is"
    assert "consumer" not in said[-1], (
        f"a lane whose ticket names no extra workspace reports the consumer's toolchain: "
        f"{said[-1]}")
    assert "no other workspace" in said[-1], (
        f"the line does not say the lane holds nothing else, so *not mine to run* is not sayable "
        f"from it: {said[-1]}")

    rc, out = run_in_lane(env, lane, f"{toolchain}/bin/generate")
    assert rc != 0, (
        f"a lane that was never given the consuming toolchain ran its command anyway: {out!r}")
