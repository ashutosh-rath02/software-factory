"""SRV-FIX-351 — what an unattended session in THIS repository is allowed to run.

`WEB-FEAT-001`'s QA session, and `SRV-FEAT-363`'s after it, wrote a thorough verdict about a
product neither of them could execute a single command against:

    $ cd server && ./build.sh build      → This command requires approval
    $ pytest tests/test_home.py -v       → This command requires approval
    $ git add docs/reports/<id>-qa.md    → This command requires approval

The permission layer sits ABOVE the program, so no unit test of the tool reaches it, and the
failure is silent in the direction that matters: the session still writes a document, so a verdict
of *nothing verified* reads exactly like a verdict of *verified*.

This repository is its own git repository and is cloned into others (`CLAUDE.md`), so it may not
rely on a parent's `.claude/settings.json`. It carries its own, and these cases are what say so.

    cd server && pytest tests/test_session_permissions.py -v
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FACTORY = Path(__file__).resolve().parents[2]
LAUNCHERS = [FACTORY / ".claude" / "scripts" / "start-qa.sh"]

pytestmark = pytest.mark.repository


# ── the file, and what a rule means ──────────────────────────────────────────────────────────

def allow_rules():
    """The `--allowedTools` list each launcher passes, read out of the script that passes it.

    NOT `.claude/settings.json`. A project settings file is ignored entirely until the workspace
    is trusted — measured: a real session printed `Ignoring 35 permissions.allow entries from
    .claude/settings.json: this workspace has not been trusted` and refused every command, while
    the same session with `--allowedTools` on the command line ran them. Trust is a per-machine
    flag in `~/.claude.json`, so a settings file makes this repository's gates depend on a
    human having clicked something on whichever machine the clone lands on.

    The flag has no such dependency, and it puts the grant where the launch is: what a QA session
    may run is readable in the file that starts it, rather than in a project file whose effect is
    conditional on state kept somewhere else.
    """
    rules = []
    for path in LAUNCHERS:
        assert path.is_file(), (
            f"{path} does not exist, so nothing in this repository grants a headless session the "
            "commands its own gates are written in. SRV-FIX-351.")
        text = path.read_text()
        # The flag must be ON THE INVOCATION, not merely mentioned. A `"--allowedTools" in text`
        # check passes against a comment that explains the flag — measured: deleting the flag line
        # left three cases green because the paragraph above it says the word. A rule a grep can
        # satisfy from prose is not a rule.
        # Anchored on the INVOCATION line — `\bclaude\b` alone matches `.claude/scripts/...`
        # inside the tool list itself, which is a different line and carries no flag.
        invocation = re.search(r"^\s*(?:setsid\s+)?(?:nohup\s+)?claude\s(?:[^\n\\]|\\\n)*",
                               text, re.M)
        assert invocation, f"{path.name} does not invoke `claude` at all"
        # `--allowedTools <tools...>` is VARIADIC: without a `--` it eats the prompt and the
        # session dies with "Input must be provided either through stdin or as a prompt
        # argument". Measured — the first real launch after this file went green.
        assert re.search(r"--allowedTools\b.*?(?<!-)\s--\s", invocation.group(0), re.S), (
            f"{path.name} passes `--allowedTools` with no `--` before the prompt. The flag is "
            f"variadic, so the prompt is swallowed as another tool rule and the session exits "
            f"immediately: `Input must be provided either through stdin or as a prompt argument`.")
        assert "--allowedTools" in invocation.group(0), (
            f"{path.name} invokes claude without `--allowedTools`. `--permission-mode acceptEdits` "
            "sanctions the WRITE of the verdict and says nothing about Bash, which is why "
            "WEB-FEAT-001's QA wrote nine refusals and measured nothing.")
        # The list is held in a shell variable and passed by expansion, so read the assignment.
        # Line continuations are stripped: a grant split for readability is one list.
        m = re.search(r'ALLOWED_TOOLS="((?:[^"\\]|\\.)*)"', text, re.S)
        assert m, f"{path.name} passes --allowedTools but defines no ALLOWED_TOOLS to read"
        # Split on RULE boundaries, never on whitespace: `Bash(git add:*)` holds a space and
        # `.split()` would make it two rules that grant nothing.
        rules += re.findall(r"[A-Za-z]+\([^)]*\)|(?<![\w(])[A-Z][A-Za-z]+(?![\w(])",
                            m.group(1).replace("\\\n", " "))
    return rules


def bash_prefixes(rules):
    """The command prefixes the allow list grants, as `Bash(<prefix>:*)` and `Bash(<exact>)`."""
    out = []
    for rule in rules:
        m = re.fullmatch(r"Bash\((.*)\)", rule)
        if not m:
            continue
        body = m.group(1)
        out.append((body[:-2], True) if body.endswith(":*") else (body, False))
    return out


# NO NORMALISATION HERE, DELIBERATELY. A first attempt let `tools/conventions.py` be satisfied by
# a rule for `./tools/conventions.py`. The real permission layer matches the literal string, so
# that made this model KINDER than the boundary — and a model kinder than the boundary is how a
# gate says yes where the system says no. Measured: `server/tests/soak.sh --help` under a rule
# reading `Bash(./server/tests/soak.sh:*)` is `This command requires approval`. Every form a
# document writes is granted as that form.
def is_allowed(command, rules=None):
    """Does the allow list grant `command`? Prefix rules match a leading command word run."""
    for prefix, is_prefix in bash_prefixes(allow_rules() if rules is None else rules):
        if is_prefix and (command == prefix or command.startswith(prefix + " ")):
            return True
        if not is_prefix and command == prefix:
            return True
    return False


# ── the commands this repository's own documents tell an agent to run ────────────────────────

# §1 criterion 2 enumerates these and only these: `_TEMPLATE.md` §2, `WORKFLOW.md`, and both
# `.factory/prompts/*.md`. `CLAUDE.md` is NOT among them and is not read here — round 1 added it
# beyond what §1 asked, and the only command it contributes is `npm run dev`, which CLAUDE.md's
# table names as the HUMAN's local-development command. Narrowing a criterion is how a gate is
# softened, so the exclusion is not left as an omission: `npm run dev` has its own refusal case
# below.
DOC_SOURCES = [
    FACTORY / "docs" / "tickets" / "_TEMPLATE.md",
    FACTORY / "docs/technical/workflow/WORKFLOW.md",
    FACTORY / ".factory" / "prompts" / "run-ticket.md",
    FACTORY / ".factory" / "prompts" / "qa-ticket.md",
]

# WHAT COUNTS AS A COMMAND — decided by the filesystem and `PATH`, not by a list.
#
# This was a hand-written tuple of eight prefixes, and it is the reason this ticket failed QA three
# times in a row. Each verdict named one command the tuple could not see — `npm run build`, then
# `npm run gate:inventory`, then `server/tests/soak.sh` and `tools/conventions.py` — and each round
# added that one member while the BOUND stayed exactly where it was. The set a hand list can miss
# is unbounded, so that loop does not terminate. `documented_commands()` was described as derived
# from the documents and was in fact bounded by eight strings sitting eleven lines from the grant
# they check.
#
# A token is a command if the system says so: it names an executable file in this repository, or a
# program on `PATH`. Nothing here decides; both answers come from outside this file.

# Where a relative path in a document is written from. `WORKFLOW.md:72` writes
# `tools/conventions.py` with no `cd`, and it lives under `server/`.
COMMAND_ROOTS = [FACTORY, FACTORY / "server", FACTORY / "client" / "web"]


def names_a_command(token):
    """Is `token` the name of something this machine can run? Filesystem and PATH answer."""
    token = token.strip("`'\"")
    if not token or token.startswith("-"):
        return False
    if "/" in token:
        for root in COMMAND_ROOTS:
            candidate = (root / token.lstrip("./")).resolve()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return True
        return False
    return shutil.which(token) is not None


def harvest(found, line):
    """One written command → the head a rule has to match, or nothing."""
    # `cd <somewhere> && <the command>` — the command is what needs a rule
    while line.startswith("cd ") and "&&" in line:
        line = line.split("&&", 1)[1].strip()
    line = line.split("#", 1)[0].strip()
    if not names_a_command(line.split()[0] if line.split() else ""):
        return
    words = [w for w in line.split() if not w.startswith(("<", "\"", "'", "$"))]
    if not words:
        return
    # `./build.sh` joins `git push` as a verb whose OBJECT is the safety property: `build`
    # terminates and `run` blocks forever, and a head of `./build.sh` cannot tell them apart —
    # it collapses to one rule that grants both.
    depth = {"git": 2, "npm": 3, "npx": 2, "python3": 3, "python": 3, "node": 1,
             "./build.sh": 2}
    n = depth.get(words[0], 1)
    # `git push` is the one verb whose OBJECT is the safety property. `--allowedTools` is
    # allow-only — there is no deny list — so `Bash(git push:*)` would grant `git push --force`
    # too. The grant is the exact destinations the workflow documents, and the vocabulary this is
    # checked against has to carry them or the two can never agree.
    if words[:2] == ["git", "push"]:
        n = 4
    found.add(" ".join(words[:n]).rstrip(":,"))


def documented_commands():
    found = set()
    for path in DOC_SOURCES:
        if not path.is_file():
            continue
        fenced = False
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line.startswith("```"):
                fenced = not fenced
                continue
            # NOT `fenced` only. `_TEMPLATE.md:421` names `cd client && npm run build` as a BUILD
            # bullet and `WORKFLOW.md:115` names `npm run gate:inventory` in a table cell; both
            # were skipped in silence, so the derived vocabulary came out EXACTLY equal to the
            # grant — the check and the grant agreeing because both were written from one partial
            # read of the same documents. A command a document tells an agent to run counts
            # wherever it is written. (SRV-FIX-351 QA, F2.)
            if fenced:
                harvest(found, line.lstrip("$ ").strip())
                continue
            # Outside a fence, a command is written in backticks — in a bullet, a table cell, or
            # mid-sentence in a numbered step, which is where `_TEMPLATE.md:443` puts
            # `./build.sh run "$PWD"`. Every backticked span is offered and `harvest` keeps the
            # ones shaped like a command, so the set comes from the DOCUMENT rather than from
            # what this file thought to look for.
            for span in re.findall(r"`([^`]+)`", line):
                harvest(found, span.lstrip("$ ").strip())
    assert len(found) >= 8, sorted(found)
    return sorted(found)


# ── criterion 1 · the boundary, driven ───────────────────────────────────────────────────────

def versions_here():
    """What the programs answer when THIS process runs them — the arbiter for the session.

    The three are §1's three, verbatim, and they are probed BECAUSE one of them was outside the
    grant. Round 1 substituted `pytest --version` and `python3 -m pytest --version` — both inside
    the grant, and both answering the byte-identical string `pytest 9.0.2`, so two named probes
    made one assertion. A criterion narrowed onto what already works is the first way a gate lies,
    and no session may downgrade the request §1 made. (SRV-FIX-351 QA, F1.)
    """
    out = {}
    for name, argv in (("npm --version", ["npm", "--version"]),
                       ("python3 --version", ["python3", "--version"]),
                       ("pytest --version", ["pytest", "--version"])):
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, (name, r.returncode, r.stderr)
        out[name] = (r.stdout + r.stderr).strip().splitlines()[0]
    assert len(set(out.values())) == 3, (
        f"the three probes must answer three DISTINCT strings or the loop below asserts one of "
        f"them several times: {out}")
    return out


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("FACTORY_REAL_CLI") == "0",
                    reason="FACTORY_REAL_CLI=0 opts out of authenticated live Claude")
def test_a_headless_session_in_this_repository_can_run_its_own_gates():
    """§1 criterion 1 — asserted at the BOUNDARY, not by reading the file.

    A case that greps the JSON asserts a file; the thing that failed is the permission layer above
    the program. So a real headless session is launched with this repository as its cwd, exactly as
    `.claude/scripts/start-qa.sh` launches one, and is required to have RUN the three commands §1
    names — the strings it prints must be the strings this process gets from the same three
    programs, and they must be three different strings.

    `-p` cannot block: with no prompt available it refuses and says so, so this case cannot hang
    waiting for an approval that will never come.
    """
    mine = versions_here()
    prompt = (
        "Run exactly these three commands with the Bash tool and print each command's output "
        "verbatim, one per line, with no other text: `npm --version`, `python3 --version`, "
        "`pytest --version`."
    )
    # Launched with the grant the LAUNCHER carries, read out of it — so this case measures what
    # `start-qa.sh` actually gives a QA session and not what the test happens to pass. If the two
    # ever part, this is the case that says so.
    r = subprocess.run(
        ["claude", "-p", "--model", "claude-opus-5", "--permission-mode", "acceptEdits",
         # `--allowedTools <tools...>` is VARIADIC and swallows the prompt without the `--`.
         "--allowedTools", " ".join(allow_rules()), "--", prompt],
        cwd=str(FACTORY), capture_output=True, text=True, timeout=600,
        env=dict(os.environ, CLAUDE_CODE_DISABLE_TERMINAL_TITLE="1"))
    said = r.stdout + r.stderr

    assert "requires approval" not in said, (
        "the session was refused a command this repository's own gates are written in:\n" + said)
    for name, version in mine.items():
        assert version in said, (
            f"the session did not report what `{name}` really answers here "
            f"({version!r}); it produced:\n{said}")


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("FACTORY_REAL_CLI") == "0",
                    reason="FACTORY_REAL_CLI=0 opts out of authenticated live Claude")
def test_a_headless_session_can_stage_and_commit_its_own_verdict(tmp_path):
    """§1 criterion 1, facet 5 — a verdict that cannot be committed is a verdict nobody receives.

    Three `--version` probes show the session can READ. The thing a QA session must actually do at
    the end is `git add` and `git commit`, and round 1 asserted neither. The scratch repository is
    built HERE and the commit is verified HERE with `git log` — the session's own account of what
    it did is not evidence.
    """
    repo = tmp_path / "scratch"
    repo.mkdir()
    for argv in (["git", "init", "-q", "-b", "main"],
                 ["git", "config", "user.email", "gate@example.invalid"],
                 ["git", "config", "user.name", "gate"]):
        subprocess.run(argv, cwd=repo, check=True, capture_output=True, timeout=60)
    (repo / "verdict.md").write_text("# scratch verdict\n")

    r = subprocess.run(
        ["claude", "-p", "--model", "claude-opus-5", "--permission-mode", "acceptEdits",
         "--allowedTools", " ".join(allow_rules()), "--",
         "With the Bash tool, stage `verdict.md` with `git add` and then commit it with "
         "`git commit -m \"scratch: the verdict\"`. Print only what the commands printed."],
        cwd=str(repo), capture_output=True, text=True, timeout=600,
        env=dict(os.environ, CLAUDE_CODE_DISABLE_TERMINAL_TITLE="1"))

    log = subprocess.run(["git", "log", "--oneline"], cwd=repo,
                         capture_output=True, text=True, timeout=60)
    assert "scratch: the verdict" in log.stdout, (
        "the launched session could not commit. A QA session that cannot commit writes a verdict "
        f"nobody receives.\nsession said:\n{r.stdout + r.stderr}\ngit log:\n{log.stdout}")


# ── criterion 2 · every documented command has a rule ────────────────────────────────────────

def executables_named_in_the_documents():
    """A SECOND extraction, by a different rule: every backticked span whose first token is an
    executable FILE in this repository.

    Criterion 2 asked *is every harvested command allowed* and never *is every command harvested* —
    both sides came out of one function, so it could not disagree with itself. This walks the same
    documents with a rule that shares nothing with `harvest()`: no cd-stripping, no depth map, no
    notion of a head. If `documented_commands()` misses one of these, its derivation is incomplete
    and criterion 2's greenness means nothing.
    """
    found = set()
    for path in DOC_SOURCES:
        if not path.is_file():
            continue
        for span in re.findall(r"`([^`]+)`", path.read_text()):
            first = span.strip().lstrip("$ ").split()
            if not first:
                continue
            token = first[0]
            if "/" not in token:
                continue
            for root in COMMAND_ROOTS:
                candidate = (root / token.lstrip("./")).resolve()
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    found.add(token)
                    break
    return found


def test_the_derivation_finds_every_executable_the_documents_name():
    """THE PROPERTY THREE VERDICTS IN A ROW WERE ABOUT, asserted directly.

    Rounds 1-3 each fixed the one command the last verdict named and left the derivation's bound
    untouched: `npm run build`, then `npm run gate:inventory`, then `server/tests/soak.sh` and
    `tools/conventions.py`. What was never checked is that the derivation is COMPLETE. It is
    checked here, against an extraction that shares no code with it.
    """
    harvested = " ".join(documented_commands())
    missed = sorted(x for x in executables_named_in_the_documents()
                    if x.lstrip("./") not in harvested)
    assert not missed, (
        "these are executable files this repository's own documents name, and "
        f"`documented_commands()` does not produce them: {missed}. Criterion 2 is therefore green "
        f"over a vocabulary that is missing them, which is how this ticket failed QA three times.")


def test_the_two_commands_three_verdicts_found_by_hand_are_derived_now():
    """A regression name for the members QA had to find for me. If the classifier narrows again,
    these go red with the reason attached rather than at the next verdict."""
    got = documented_commands()
    for hand_found in ("server/tests/soak.sh", "./tools/conventions.py"):
        assert any(c.lstrip("./") == hand_found.lstrip("./") for c in got), (
            f"`{hand_found}` is named by the documents and is not in the derived vocabulary. "
            f"QA found this one by hand in round 3; the derivation is supposed to now. Got: {got}")


# NAMED BY A DOCUMENT, AND NOT AN INVOCATION AN AGENT MAKES. The classifier answers *can this
# machine run it*, which is the right question and a wider one than *does the workflow tell an
# agent to run it*. The gap is these, and each carries its reason so the decision is reviewable
# rather than absorbed into a list. `test_every_exemption_is_still_reachable` deletes dead ones.
NOT_AN_INVOCATION = {
    "GET":  "an HTTP method in an endpoint table (`GET :7777/api/lanes`). Also lwp-request on PATH",
    "POST": "an HTTP method (`POST /api/tickets/ids`). Also lwp-request on PATH",
    "HEAD": "a git refspec (`HEAD:main`). Also lwp-request on PATH",
    "ps":   "named bare, in prose about reading a process table; read-only and needs no rule",
    "grep": "named bare, in prose; read-only and needs no rule",
    "rg":   "named bare beside grep as an example of a cause-finding probe, not an invocation",
    "pgrep": "named bare, in prose; read-only and needs no rule",
    "claude": "the program `start-qa.sh` LAUNCHES. A QA session starting another QA session is the "
              "racing-verdicts failure start-qa.sh already refuses; the launcher is granted, the "
              "raw binary is not",
}


def test_every_exemption_is_still_reachable():
    """An exemption for something no document names any more is a hole nobody is watching."""
    named = {c for c in documented_commands()} | executables_named_in_the_documents()
    named |= {n for n in NOT_AN_INVOCATION if any(
        re.search(r"`" + re.escape(n) + r"(?:[\s:\]]|`)", p.read_text())
        for p in DOC_SOURCES if p.is_file())}
    dead = sorted(n for n in NOT_AN_INVOCATION if n not in named)
    assert not dead, (
        f"these exemptions name nothing in the documents any more: {dead}. Delete them — an "
        f"exemption that excuses nothing is a hole nobody is watching.")
    assert all(NOT_AN_INVOCATION.values()), "every exemption states its reason"


LANE_PROMPTS = [FACTORY / ".factory" / "prompts" / "run-ticket.md",
                FACTORY / ".factory" / "prompts" / "qa-ticket.md"]


def commands_only_the_lane_prompts_name():
    """Commands named ONLY by the lane prompts — a different session type, granted elsewhere.

    `.factory/prompts/*.md` instruct an agent the FACTORY starts inside a lane, whose permissions
    come from the lane facility. `start-qa.sh` says so itself: *"inside a lane you do NOT run this
    script at all"*. The prompts are also byte-identical to the PARENT repository's copies —
    `test_catalog.py::test_the_two_manifests_prompt_files_are_the_same_text` enforces it — so they
    cannot name a script that exists only here.

    That is why the progress ping is still `curl` there while `_TEMPLATE.md` uses `api.sh`. The
    exemption is SCOPED, not a blanket: it holds only while the command appears in no document that
    governs this session. If `curl` turns up in `_TEMPLATE.md` or `WORKFLOW.md`, it is out of this
    set on the next run and criterion 2 demands a rule for it.
    """
    ours, theirs = set(), set()
    for path in DOC_SOURCES:
        if not path.is_file():
            continue
        found = set()
        for raw in path.read_text().splitlines():
            for span in re.findall(r"`([^`]+)`", raw) or [raw.strip()]:
                harvest(found, span.lstrip("$ ").strip())
        (theirs if path in LANE_PROMPTS else ours).update(found)
    return theirs - ours


def test_the_lane_only_exemption_is_scoped_and_not_a_blanket(tmp_path, monkeypatch):
    """A real invocation is exempt only while it appears solely in a lane prompt."""
    copies = []
    for source in DOC_SOURCES:
        copied = tmp_path / source.relative_to(FACTORY)
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(source.read_bytes())
        copies.append(copied)
    monkeypatch.setitem(globals(), "DOC_SOURCES", copies)
    monkeypatch.setitem(globals(), "LANE_PROMPTS", [
        copied for copied in copies if copied.relative_to(tmp_path).parts[:2] ==
        (".factory", "prompts")])
    assert documented_commands()
    qa_prompt = next(path for path in LANE_PROMPTS if path.name == "qa-ticket.md")
    workflow = next(path for path in DOC_SOURCES if path.name == "WORKFLOW.md")
    invocation = "\n`curl -sS http://127.0.0.1:7777/api/health`\n"
    qa_prompt.write_text(qa_prompt.read_text() + invocation)
    lane_only = commands_only_the_lane_prompts_name()
    assert "curl" in lane_only
    for governs_this_session in ("pytest", "npm run test", "git commit"):
        assert governs_this_session not in lane_only, (
            f"`{governs_this_session}` was excused as lane-only, but the session's own documents "
            f"name it. The exemption has stopped being scoped.")
    workflow.write_text(workflow.read_text() + invocation)
    assert "curl" not in commands_only_the_lane_prompts_name()


def test_the_allow_list_covers_every_command_the_workflow_documents():
    """§1 criterion 2. Derived from the documents, so a workflow that grows a command and not a
    rule turns this red rather than being discovered inside a verdict."""
    lane_only = commands_only_the_lane_prompts_name()
    missing = [c for c in documented_commands()
               if c not in NEVER_TERMINATES and c not in NOT_AN_INVOCATION
               and c not in lane_only and not is_allowed(c)]
    assert not missing, (
        "this repository documents commands its own sessions are not allowed to run: "
        f"{missing}")


# Documented, and refused ANYWAY — the one carve-out criterion 2 allows, named here so it is
# bounded. Each of these blocks forever: a dev server, a preview server, and `./build.sh run`,
# which `_TEMPLATE.md:443` documents as the DOGFOOD step *a person drives on their own screen*. A
# headless session that starts one waits until its timeout having produced nothing — a live
# process that has stopped working, which every liveness probe reports as healthy. That is
# `SRV-FIX-124`'s class and the thing this ticket closes. A fourth such command appearing in the
# documents and not in this set turns criterion 2 red, which is the point of writing it down.
NEVER_TERMINATES = ("npm run dev", "npm run preview", "./build.sh run")


def test_curl_is_not_granted_because_the_bound_cannot_be_written_as_a_rule():
    """§1: `curl` against localhost, "Nothing broader" — and `--allowedTools` cannot say that.

    Measured at the boundary, every host-scoped form is refused, because the `:` inside the rule
    breaks its own prefix parse:

        Bash(curl -sS http://127.0.0.1:*)                    -> curl -sS http://127.0.0.1/  REFUSED
        Bash(curl -sS http://localhost:*)                    -> REFUSED
        Bash(curl -sS http://127.0.0.1:7777/api/health)      -> REFUSED
        Bash(curl -sS -X POST http://127.0.0.1:7777/…:*)     -> REFUSED

    The only rule that works is `Bash(curl:*)`, which reaches ANY host — SRV-FIX-351's QA round 2
    measured a session allowed to reach 192.0.2.1 under it. A grant that can only be unusable or
    unbounded is not the one §1 describes, so the bound moved out of the rule syntax and into a
    program: `.claude/scripts/api.sh` takes a PATH and compiles the host in.
    """
    for reaches_anywhere in ("curl http://192.0.2.1/", "curl -sS http://127.0.0.1:7777/api/health",
                             "wget http://192.0.2.1/", "nc 192.0.2.1 80"):
        assert not is_allowed(reaches_anywhere), (
            f"`{reaches_anywhere}` is granted. §1 bounds the session's network to localhost and no "
            f"`--allowedTools` rule can express that, so the grant must name a program that does.")
    assert is_allowed(".claude/scripts/api.sh GET /api/health"), (
        "the localhost-bounded replacement is not granted, so the workflow has no way to ask the "
        "tool for a ticket id and will fall back to counting files — SRV-FIX-188.")


def test_the_api_script_cannot_be_pointed_at_another_host():
    """The bound is only real if the program holds it. Driven, not read."""
    api = FACTORY / ".claude" / "scripts" / "api.sh"
    assert api.is_file() and os.access(api, os.X_OK), f"{api} is missing or not executable"
    for escape in ("http://192.0.2.1/x", "//192.0.2.1/x", "https://example.com/"):
        r = subprocess.run([str(api), "GET", escape], capture_output=True, text=True, timeout=60)
        assert r.returncode != 0, f"api.sh accepted {escape!r}: {r.stdout + r.stderr}"
    r = subprocess.run([str(api), "PROGRESS", "{}"], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, FACTORY_PROGRESS_URL="http://192.0.2.1/x"))
    assert r.returncode != 0 and "not local" in (r.stdout + r.stderr), (
        f"api.sh PROGRESS posted to a non-local URL: {r.stdout + r.stderr}")


def test_a_command_that_never_terminates_is_not_granted():
    """`npm run dev` is documented, and granting it would be the defect this ticket closes.

    A dev server does not exit. A headless session that starts one blocks until its timeout having
    produced nothing — a live process that has stopped working, which every liveness probe reports
    as healthy. That is `SRV-FIX-124`'s class exactly, so the one command dropped when `CLAUDE.md`
    left `DOC_SOURCES` is held out by an assertion instead of by an absence.
    """
    assert "./build.sh run" in documented_commands(), (
        "the carve-out is hypothetical: no document names a non-terminating command, so this "
        "case and criterion 2's exclusion are both dead weight and should be deleted.")
    for never_exits in NEVER_TERMINATES:
        assert not is_allowed(never_exits), (
            f"`{never_exits}` is granted to a QA session. It does not terminate, so the session "
            f"hangs until its timeout while looking alive — the failure SRV-FIX-124 is about.")


# ── criterion 3 · MANDATORY NEGATIVE ─────────────────────────────────────────────────────────

def test_a_command_the_workflow_does_not_document_is_not_allowed():
    """§1 criterion 3. A rule set that allows everything passes 1 and 2 trivially.

    So the grant has to be SCOPED: no blanket `Bash(*)`, and four commands outside the workflow's
    vocabulary stay refused. And the deny list keeps the two pushes a ticket may never make.
    """
    rules = allow_rules()
    # Every blanket form the CLI accepts, not the two that came to mind. A rule set with any of
    # these passes criteria 1 and 2 trivially and means nothing.
    for blanket in ("Bash(*)", "Bash(:*)", "Bash(*:*)", "Bash"):
        assert blanket not in rules, (
            f"`{blanket}` grants everything, so criteria 1 and 2 become unfalsifiable: {rules}")

    documented = set(documented_commands())
    for outsider in ("ssh user@host", "scp a b", "rm -rf /", "sudo apt-get install curl"):
        assert outsider.split()[0] not in {c.split()[0] for c in documented}, outsider
        assert not is_allowed(outsider, rules), (
            f"{outsider!r} is allowed, and nothing in this repository's workflow asks for it")

    # A force push is refused because NOTHING GRANTS IT, not because a deny rule names it.
    # `--allowedTools` is allow-only, so there is no deny list; `Bash(git push:*)` would have
    # matched `git push --force` and the two exact forms the workflow documents do not.
    for forced in ("git push --force origin main", "git push -f origin main",
                   "git push --force-with-lease origin main"):
        assert not is_allowed(forced, rules), (
            f"{forced!r} is allowed. With no deny list the grant must be tight enough that a "
            f"force push matches no rule — two exact forms, never `Bash(git push:*)`.")


# ═══════════════════════════════════════════════════════════════════════════════════════════
# SRV-FEAT-499 — the freeze, asserted at the boundary it exists to hold
# ═══════════════════════════════════════════════════════════════════════════════════════════
#
# A REFUSAL ABOUT A BOUNDARY IS ASSERTED AT THE BOUNDARY. Every other case for this feature reads
# a status code off `E24`; this one is inside a real lane, driving the real endpoint the agent's
# own hook posts to, and it asks the only question a status code cannot answer: **did the bytes on
# disk change**. `SRV-FIX-077` is the row — a guard asserted at the loader, and the hole was at the
# receiver.

import time

import stand_in
from test_lane import build_repo, git

# The stand-in does what `launch.cpp`'s hook does and nothing more: it asks the endpoint, and it
# writes only if the answer was not a refusal. It is the same decision the real hook takes —
# `answer.get("decision") == "deny"` → deny, anything else → allow — so a case here cannot pass
# against a stand-in that is kinder than the product.
EDITING_AGENT = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "token=${FACTORY_PROGRESS_TOKEN}" >> "$log"
echo "perm=${FACTORY_PERMISSION_URL}" >> "$log"
""" + stand_in.FRAME_READER + r"""
echo "ready" >> "$log"

while : ; do
  if read_frame ; then
    case "$text" in
      bye) break ;;
      edit:*)
        target=`printf '%s' "$text" | cut -d: -f2`
        payload=`printf '%s' "$text" | cut -d: -f3`
        body="{\"token\":\"${FACTORY_PROGRESS_TOKEN}\",\"tool\":\"Edit\",\"input\":{\"file_path\":\"${target}\"}}"
        echo "asking:${target}" >> "$log"
        answer=`/usr/bin/curl -s --max-time 120 -X POST -H 'Content-Type: application/json' \
                -d "$body" "${FACTORY_PERMISSION_URL}"`
        echo "answer:${answer}" >> "$log"
        case "$answer" in
          *'"decision":"deny"'*) printf '%s\n' "refused:${target}" >> "$log" ;;
          *) printf '%s\n' "$payload" > "$target" ; printf '%s\n' "wrote:${target}" >> "$log" ;;
        esac
        ;;
    esac
  else
    break
  fi
done
echo "eof" >> "$log"
"""

EVIDENCE_PATH = "server/tests/test_x.py"
ORIGINAL = "the case this criterion names\n"

BOUNDARY_TICKET = """---
id: TCK-BOUND
title: a ticket whose one criterion names the case that evidences it
status: todo
---

## 1. Request

- **Pass criteria**

  ```
  1. A person opens the surface and the row it drew is there.
     Evidence: server/tests/test_x.py::test_a
  ```
"""


def test_the_freeze_holds_at_the_boundary(tmp_path, serve):
    """§1 criterion 6, MANDATORY NEGATIVE AT THE BOUNDARY.

    A real lane, the real stand-in, the real `E24`, and the standing policy set to allow
    everything — so nothing but the freeze can be what stops the write. The claim is not that the
    endpoint answered `asked`; it is that **the file on disk still holds its old bytes** while the
    operator has not answered, and holds the new ones once they have.
    """
    root = build_repo(tmp_path / "boundary",
                      agent={"command": "/bin/sh agent.sh", "permission_mode": "never_ask"})
    (root / "agent.sh").write_text(EDITING_AGENT)

    manifest = json.loads((root / ".factory" / "factory.json").read_text())
    manifest["stages"] = ["SPEC", "TEST", "IMPL", "GATE"]
    manifest["evidence"] = {"line_prefix": "Evidence:", "frozen_after": "TEST"}
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    (root / "docs" / "tickets" / "TCK-BOUND.md").write_text(BOUNDARY_TICKET)
    case = root / EVIDENCE_PATH
    case.parent.mkdir(parents=True, exist_ok=True)
    case.write_text(ORIGINAL)
    git(root, "add", "-A")
    git(root, "commit", "-m", "the boundary fixture")

    srv = serve(root, FACTORY_TEST_HOOKS="1")
    claimed = srv.post("/api/lanes", json={"ticket_id": "TCK-BOUND", "workspace": "core"})
    assert claimed.status_code == 201, claimed.text
    lane, worktree = claimed.json()["lane"], Path(claimed.json()["worktree"])

    run = srv.post(f"/api/lanes/{lane}/runs", json={})
    assert run.status_code == 201, run.text
    pid = run.json()["pid"]

    def log():
        try:
            return (worktree / "agent-log.txt").read_text()
        except OSError:
            return ""

    def wait_log(needle, timeout=25):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in log():
                return log()
            time.sleep(0.05)
        raise AssertionError(f"{needle!r} never reached the agent log:\n{log()}")

    token = next(line.split("=", 1)[1] for line in wait_log("ready").splitlines()
                 if line.startswith("token="))

    in_lane = worktree / EVIDENCE_PATH
    assert in_lane.read_text() == ORIGINAL, in_lane.read_text()

    # The stage the manifest names, reported by the run itself.
    reported = srv.post("/api/progress", json={"token": token, "stage": "TEST"})
    assert reported.status_code == 202, reported.text

    conn = srv.ws()
    assert conn.recv()["m"] == "hello"
    conn.send({"m": "subscribe", "scope": {"lane": lane}})
    conn.wait_for("snapshot")

    told = srv.post(f"/api/lanes/{lane}/messages",
                    json={"text": f"edit:{EVIDENCE_PATH}:a line the agent decided to write"})
    assert told.status_code == 200, told.text

    m6 = conn.wait_for("permission.request", timeout=25)
    assert m6["frozen_by"] == {"ticket_id": "TCK-BOUND", "criterion": 1}, m6

    # THE BOUNDARY. Nothing has been answered, so nothing may have been written.
    time.sleep(1.0)
    assert in_lane.read_text() == ORIGINAL, (
        f"the agent's edit landed while the request was still waiting for an answer:\n"
        f"{in_lane.read_text()!r}\nagent log:\n{log()}")
    assert "wrote:" not in log(), f"the stand-in wrote before it was answered:\n{log()}"

    answered = srv.post("/api/permissions/decisions",
                        json={"request_id": m6["request_id"], "decision": "once"})
    assert answered.status_code == 200, answered.text

    wait_log("wrote:")
    deadline = time.time() + 10
    while time.time() < deadline and in_lane.read_text() == ORIGINAL:
        time.sleep(0.05)
    assert in_lane.read_text() == "a line the agent decided to write\n", (
        f"the operator allowed the edit and it did not land:\n{in_lane.read_text()!r}\n"
        f"agent log:\n{log()}")

    conn.close()
    srv.post(f"/api/lanes/{lane}/kill", json={})
    from test_agent import assert_reaped
    assert_reaped(pid, within=20, what="the boundary lane's run")


# ── SRV-FIX-532 cluster F4 — a bound is asserted over every rule that could defeat it ─────────
#
# `SRV-FIX-361`. `Bash(git fetch:*)` is a prefix rule whose ARGUMENT may be any URL, and a session
# carrying the launcher's own grant reached `http://192.0.2.1:9418/x.git` while `curl` to the same
# address was refused. The bound `SRV-FIX-351` moved out of the rule syntax and into a program was
# held for the one command that ticket was looking at.
#
# The claim is NOT *the session must reach nothing*: `origin` is not localhost and the merge steps
# the workflow documents are `git fetch` and `git fetch origin`. Two things were wrong and they
# have different fixes — the grant was wider than the workflow, and the sentence overstated.

TAKES_A_HOST = ("git fetch", "git clone", "git remote", "git pull", "git push",
                "curl", "wget", "nc", "ssh", "scp")

UNDOCUMENTED_FETCH = "git fetch http://192.0.2.1:9418/x.git"
DOCUMENTED_FETCHES = ("git fetch", "git fetch origin")


def test_a_fetch_from_a_url_the_workflow_does_not_document_is_refused():
    """§1 F4 — a fetch from a URL nobody documented is refused, and the documented one is not.

    `SRV-FIX-361` criterion 1. The control is in the same case on purpose: a fix that refuses
    every fetch passes the first half and breaks the workflow this grant exists to serve — a
    session that cannot fetch `main` cannot merge.

    RED before the fix: `git fetch http://192.0.2.1:9418/x.git` ran, and the only objection came
    from git (`fatal: not a git repository`) rather than from the permission layer.
    """
    assert not is_allowed(UNDOCUMENTED_FETCH), (
        f"`{UNDOCUMENTED_FETCH}` is granted. A prefix rule whose argument is a URL reaches any "
        f"host on the internet, which is the bound `SRV-FIX-351` moved into a program because no "
        f"rule can express it — held for `curl` and `git push`, and not for this.")
    for documented in DOCUMENTED_FETCHES:
        assert is_allowed(documented), (
            f"`{documented}` is not granted, so a QA session cannot bring `main` in before it "
            f"merges. The fix is a narrowing, never a removal.")


def test_every_granted_prefix_rule_whose_argument_can_name_a_host_is_enumerated():
    """§1 F4 — the assertion that makes the third occurrence the last one.

    `SRV-FIX-361` criterion 2. Derived from the GRANT, never from a list of commands somebody
    thought of: every `Bash(<prefix>:*)` rule the launcher passes is checked against the set of
    commands whose argument can name a host, and a host-taking command granted as a prefix rather
    than as exact documented forms turns this red.

    `SRV-FIX-351`'s own case asked *are these four refused* over four commands whose first word is
    ungranted, and never *what else in the grant takes a URL* — which is how a criterion ends up
    shaped like the finding that produced it.
    """
    prefixes = sorted(body for body, is_prefix in bash_prefixes(allow_rules()) if is_prefix)
    assert prefixes, "the grant carries no prefix rule at all, so this sweep examines nothing"

    offenders = []
    for prefix in prefixes:
        for command in TAKES_A_HOST:
            if prefix == command or prefix.startswith(command + " "):
                offenders.append(f"Bash({prefix}:*) — `{command}` takes a host or a URL")
    assert not offenders, (
        "these granted prefix rules carry the safety property in their ARGUMENT, so the bound is "
        "whatever the session types:\n  " + "\n  ".join(offenders)
        + "\ngrant them as the exact forms the workflow documents, the way `git push` and "
          "`git fetch` are granted.")

    reached = [c for c in TAKES_A_HOST
               if any(r == f"Bash({c})" or r.startswith(f"Bash({c} ") for r in allow_rules())]
    assert reached, (
        f"no host-taking command is granted in any form, so this sweep passes because the grant "
        f"is empty of them rather than because it is bounded: {prefixes}")


def test_the_documented_fetches_still_run():
    """§1 F-NEG — *refuse everything* cannot pass this ticket.

    `SRV-FIX-361` criterion 3, mandatory negative. The two forms `_TEMPLATE.md` §2 and the QA
    skill document are still granted, and the case is watched RED by deleting the replacement
    rules from the grant text — so it measures the rules rather than the absence of the old one.
    """
    text = LAUNCHERS[0].read_text()
    for documented in DOCUMENTED_FETCHES:
        assert is_allowed(documented), f"`{documented}` is not granted"

    without = [r for r in allow_rules() if "git fetch" not in r]
    assert len(without) == len(allow_rules()) - len(DOCUMENTED_FETCHES), (
        "the grant does not carry exactly the two documented fetch rules, so the break below "
        f"would prove nothing: {[r for r in allow_rules() if 'fetch' in r]}")
    for documented in DOCUMENTED_FETCHES:
        assert not is_allowed(documented, rules=without), (
            f"`{documented}` is still granted with every fetch rule deleted, so this case is "
            f"measuring something other than the rules it names")

    documented_in_the_workflow = [c for c in documented_commands() if c.startswith("git fetch")]
    assert documented_in_the_workflow, (
        "no document in this repository names `git fetch`, so the grant above is for a command "
        "the workflow does not use and the narrowing should be a removal")
