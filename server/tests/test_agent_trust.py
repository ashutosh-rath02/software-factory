"""SRV-FIX-047 — the agent in a lane runs untrusted.

`SRV-FEAT-039` set `CLAUDE_CONFIG_DIR` to fix `Not logged in`, and it did. It also moved the
CLI's **trust store**, and only the credentials were checked: the developer's trust lives in
`$HOME/.claude.json` and the agent now reads `$CLAUDE_CONFIG_DIR/.claude.json`, so every run
this tool started dropped this repository's 107 `permissions.allow` entries and 7
`permissions.additionalDirectories` entries — silently, in the direction that looks like the
product working, because a dropped permission produces a request and a request looks normal.

Every fact below is measured against the real CLI and committed under `fixtures/cli-trust-*`,
per `docs/tickets/_TEMPLATE.md` §2 *A STAND-IN IS JUDGED AGAINST THE REAL THING*. Re-capture:

    cd factory/server && python3 tests/cli_trust.py

The three-way question §1 asks is answered by ONE cell —
`cli-trust-2026-07-30-config-dir-set-and-it-has-no-projects`: `$HOME/.claude.json` trusts the
workspace, `$CLAUDE_CONFIG_DIR/.claude.json` does not, and the CLI refuses the workspace anyway.
There is no fallback. The trust is read from the config directory and only from there.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT, _start_instance, stop_process
from test_lane import build_repo, git, status_of          # noqa: F401
from test_agent import agentenv                           # noqa: F401
import stand_in

SERVER = Path(__file__).resolve().parents[1]
APP = SERVER / "app"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = "cli-trust-2026-07-30-"

REAL_AGENT = shutil.which("claude")
REAL_CREDENTIALS = Path.home() / ".claude"
REAL_TOKEN = REAL_CREDENTIALS / ".credentials.json"

# What the CLI prints when a repository's declarations are dropped. Used to FIND the line, never
# as the proof that the permission is in force — the CLI may reword it and every case here also
# asserts on what the agent got.
IGNORING = re.compile(r"Ignoring \d+ permissions\.\w+ entr(?:y|ies) .*has not been trusted",
                      re.S)


def skipped(reason):
    """A skip is a RED check here, never a silent pass — `docs/operations/ESCAPES.md`, the row about a
    guarded section that reads as `n/n passed` without ever running."""
    pytest.fail(f"SKIPPED, AND THEREFORE RED: {reason}")


def requires_real_agent():
    if os.environ.get("FACTORY_REAL_CLI") == "0":
        pytest.skip("FACTORY_REAL_CLI=0 opts out of authenticated live Claude (release tier)")
    if not REAL_AGENT:
        skipped("no `claude` on PATH — this ticket's subject is that program's behaviour")
    if not REAL_TOKEN.is_file():
        skipped(f"no credentials at {REAL_TOKEN}; a lane cannot authenticate")


def capture(name):
    # A row of the enumeration may name a capture from another module's capture run
    # (`cli_home.py` measures what the CLI WRITES there — SRV-FIX-052), so a name that already
    # carries its own `cli-…` prefix is taken as it stands.
    path = FIXTURES / (name if name.startswith("cli-") else f"{CAPTURE}{name}")
    path = path.with_suffix(".json")
    if not path.is_file():
        skipped(f"the committed capture {path.name} is missing; re-run "
                f"`python3 tests/cli_trust.py` from factory/server")
    return json.loads(path.read_text())


# The agent's config directory has to be somewhere a lane can BIND, and a lane replaces `/tmp`
# with a tmpfs after the host binds are applied (`lane/confine.cpp`), so a directory under
# pytest's `tmp_path` is invisible inside the sandbox. `~/.cache` is the one writable place that
# survives, and every byte written there is removed on teardown and named in this ticket's report.
CACHE = Path.home() / ".cache" / "factory-har047"


@pytest.fixture
def config_dir():
    """A config directory for one test: the trust it declares, and the developer's real token
    reached by HARD LINK. No test ever writes to the developer's own config.

    A hard link and not a symlink, and not a copy: a lane read-binds this directory and nothing
    else under the home, so a symlink out of it DANGLES inside the sandbox and the agent comes
    back `Not logged in` (SRV-FEAT-039's FINDING 2, the same shape one directory over). A hard
    link is a second name for the same inode with the same owner and mode, so nothing is
    duplicated and the token's permissions travel with it. `mkdtemp` gives the directory 0700,
    and every byte of it is removed on teardown.
    """
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    made = []

    def build(trusts=(), marker=None):
        d = Path(tempfile.mkdtemp(prefix="cfg-", dir=CACHE))
        made.append(d)
        (d / ".claude.json").write_text(json.dumps(
            {"projects": {str(p): {"hasTrustDialogAccepted": True} for p in trusts}}, indent=2))
        if REAL_TOKEN.is_file():
            try:
                os.link(REAL_TOKEN, d / ".credentials.json")
            except OSError:                                   # a different filesystem
                shutil.copy2(REAL_TOKEN, d / ".credentials.json")
                os.chmod(d / ".credentials.json", 0o600)
        if marker is not None:
            (d / "marker.txt").write_text(marker + "\n")
        return d

    yield build
    for d in made:
        shutil.rmtree(d, ignore_errors=True)


def agent_block(config, trust=True):
    """The `agent` block this ticket's answer produces: the credential directory, and the fact
    the tool refuses to start without — stated by the repository, in the CLI's own words."""
    block = {"command": "claude", "credentials_dir": str(config)}
    if trust:
        block["config_trust"] = {"file": str(config / ".claude.json"),
                                 "keyed_by_path": "projects",
                                 "flag": "hasTrustDialogAccepted"}
    return block


@pytest.fixture
def trustenv(tmp_path, serve):
    """A synthetic repository that DECLARES a permission, plus a server pointed at it."""
    made = []

    class Env:
        def build(self, *, settings=None, agent=None, prepare=None, **kw):
            root = build_repo(tmp_path / f"repo{len(made)}", agent=agent, **kw)
            if settings is not None:
                (root / ".claude").mkdir(exist_ok=True)
                (root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
                git(root, "add", ".claude/settings.json")
                git(root, "commit", "-m", "settings")
            # The manifest is read once, when the server starts. Anything the load has to see —
            # a trust entry naming a root only now known — is written HERE, before that.
            if prepare is not None:
                prepare(Path(os.path.realpath(root)))
            baseline = status_of(root)
            made.append((root, baseline))
            self.root = root
            self.real = Path(os.path.realpath(root))
            self.baseline = baseline
            self.srv = serve(root, FACTORY_TEST_HOOKS="1")
            return self

        def claim(self, ticket="TCK-001", workspace="core"):
            return self.srv.post("/api/lanes",
                                 json={"ticket_id": ticket, "workspace": workspace})

        def unchanged(self):
            now = status_of(self.root)
            assert now == self.baseline, (
                f"THE MAIN CHECKOUT CHANGED.\nbefore:\n{self.baseline}\nafter:\n{now}")

    yield Env()
    for root, before in made:
        assert status_of(root) == before, f"the main checkout at {root} changed"


def entries_of(root, run_id, atleast=1, timeout=240, until=None):
    path = Path(root) / ".factory" / "runs" / run_id / "transcript.jsonl"
    deadline = time.time() + timeout
    entries = []
    while time.time() < deadline:
        try:
            entries = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        except OSError:
            entries = []
        if len(entries) >= atleast and (until is None or until(entries)):
            return entries
        time.sleep(0.2)
    return entries


# ── 1 · THE case: a run gets what the repository declared ────────────────────────────────────

def test_a_run_sees_the_repositorys_declared_permissions(trustenv, config_dir):
    """A lane's run sees the repository's `.claude/settings.json`, and can be shown to.

    Three assertions, and the second and third are the point. The transcript carries no
    `Ignoring …` entry — but a string assertion alone passes the day the CLI rewords the
    warning, so the case also asserts on WHAT THE AGENT GOT: a tool call the repository allowed
    and no built-in rule does, whose output is a nonce the agent cannot produce any other way.
    Measured both ways and committed — `…-turn-trusted-…` (bytes reached the agent, no denial)
    against `…-turn-untrusted-…` (the same declaration, an untrusted config, one denial,
    `CANNOT`).

    And the guarantee, which is what the fix actually is: the same repository with the trust
    taken out of its config does not start a quieter run, it does not start a lane at all.

    CHANGED BY SRV-FIX-052 — the VEHICLE, not the subject. The declared permission used to be a
    `Read` of a marker file inside the configuration directory, which worked because that
    directory was bound into the lane. It is not bound any more: a lane gets the credential file
    and nothing else of the developer's, so a marker sitting beside it is a file no lane can
    reach and the case would fail for a reason that is not its own. A declared `Bash` rule is the
    same statement — a tool call that only the repository's own declaration allows — and it needs
    nothing to be mounted.
    """
    requires_real_agent()
    marker = "har047-inforce-" + os.urandom(6).hex()
    cfg = config_dir(trusts=[])                           # trust is added once the root is known
    env = trustenv.build(
        settings={"permissions": {"allow": [f"Bash(echo {marker})"]}},
        agent=agent_block(cfg),
        prepare=lambda real: (cfg / ".claude.json").write_text(json.dumps(
            {"projects": {str(real): {"hasTrustDialogAccepted": True}}}, indent=2)))

    # ...and the manifest is understood: the key is resolved at load, not carried as an unknown.
    r = env.srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"the manifest declaring agent.config_trust did not load ({r.status_code}):\n{r.text}")
    resolved = dict(r.json().get("resolved", {}).get("paths", {}))
    assert resolved.get("agent.config_trust") == str(cfg / ".claude.json"), (
        f"the tool did not resolve the config it checks; resolved.paths = {resolved}")

    lane = env.claim()
    assert lane.status_code == 201, lane.text
    lane = lane.json()

    r = env.srv.post(f"/api/lanes/{lane['lane']}/runs", json={
        "prompt": f"Use the Bash tool to run exactly this command: echo {marker}\n"
                  f"Then reply with the one line it printed. If it could not run, reply CANNOT."})
    assert r.status_code == 201, r.text
    run = r.json()

    # Waited on the TURN, never on "some prose arrived" — the agent's first sentence is "I'll run
    # that." and a case that stops there is asserting about a moment before the fact it is asking
    # about (`docs/operations/ESCAPES.md`, *a test that waits for one fact and asserts about another*).
    entries = entries_of(env.root, run["run_id"],
                         until=lambda es: any(e.get("kind") == "turn" for e in es))
    blob = json.dumps(entries)

    dropped = [e for e in entries if IGNORING.search(e.get("text", ""))]
    assert not dropped, (
        "the run opened by announcing that this repository's declarations were thrown away:\n"
        + "\n".join(f"  {e['text']}" for e in dropped))

    ran = [e for e in entries
           if e.get("kind") == "tool" and marker in str(e.get("result", ""))]
    assert ran, (
        f"the tool call the repository allowed never produced its output. This is the "
        f"assertion the ticket exists for — the warning's absence is not evidence, the "
        f"permission being IN FORCE is.\n"
        f"kinds: {[e.get('kind') for e in entries]}\n{blob[:2000]}\n"
        f"server log tail:\n{env.srv.tail()}")

    env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    env.srv.post(f"/api/lanes/{lane['lane']}/kill", json={})
    env.unchanged()


# ── 2 · the fact the tool depends on is the fact the CLI reads ───────────────────────────────

def test_the_trust_the_tool_relies_on_is_named_where_it_is_read():
    """`CLAUDE_CONFIG_DIR` moves more than credentials, and this is where that is written down.

    §3's first question: what makes a redirection of an external program's configuration state
    ENUMERATE what it moved, rather than be judged by the one thing it was aimed at? This.
    `launch.h` carries the enumeration, every row names a committed capture, and this case
    re-derives every row from the capture — so a row nobody measured, or a claim a later CLI
    stops honouring, is a red test rather than a confident comment.

    It also asserts the negative that IS the answer to §1's three-way question: the CLI does not
    fall back to `$HOME/.claude.json`. A config the tool believes in and never reads is how this
    defect happened, so the belief is checked against the program.
    """
    header = (APP / "agent" / "launch.h").read_text()
    block = re.search(r"CLAUDE_CONFIG_DIR MOVES.*?(?=\n[^/])", header, re.S)
    assert block, (
        "app/agent/launch.h sets CLAUDE_CONFIG_DIR and does not enumerate what that moves. "
        "SRV-FEAT-039 aimed the variable at credentials, checked credentials, and silently "
        "moved the trust store — the enumeration is the structural half of SRV-FIX-047.")

    rows = re.findall(r"^//\s+(\S+)\s+\|\s+(\S+)\s+\|\s+(\S+)\s*$", block.group(0), re.M)
    assert len(rows) >= 3, f"the enumeration has {len(rows)} rows:\n{block.group(0)}"

    moved = {what: (where, cap) for what, where, cap in rows}
    assert "trust" in moved, (
        f"the enumeration does not name the trust store, which is what this ticket is: {moved}")
    assert "credentials" in moved, f"the enumeration does not name the credentials: {moved}"

    for what, (where, cap) in moved.items():
        rec = capture(cap)
        assert rec["env"]["CLAUDE_CONFIG_DIR"], (
            f"the capture backing {what!r} did not set CLAUDE_CONFIG_DIR, so it measured "
            f"nothing about what that variable moves: {cap}")
        assert not where.startswith("/"), (
            f"{what}: {where!r} is not a path under the config dir")

    # SRV-FIX-052's row is the one that travels the OTHER WAY, and it is why the enumeration is
    # not a list of files the CLI reads: it WRITES there, before it can start a shell. Two rows
    # of this list were checked against a read-only mount and the third could not have been.
    scratch = capture(moved["session-scratch"][1])
    assert any(p.startswith("session-env") for p in scratch["the_cli_created"]), (
        f"the capture backing the session scratch row shows the CLI creating nothing: "
        f"{scratch['the_cli_created']}")

    # `credentials` — authentication is read from the config directory. The capture's HOME is
    # empty and its only token is the symlink inside CLAUDE_CONFIG_DIR, and the agent
    # authenticated and answered.
    creds = capture(moved["credentials"][1])
    assert creds["the_agent_authenticated"] and creds["credentials_are_a_symlink_to"], creds
    assert not Path(creds["env"]["HOME"], ".credentials.json").exists()

    # `trust` — THE ANSWER TO §1's THREE-WAY QUESTION, read off the program.
    #   $HOME/.claude.json           trusts the workspace
    #   $CLAUDE_CONFIG_DIR/.claude.json  does not
    #   ...and the CLI refuses the workspace anyway ⇒ answer (1), no fallback to $HOME.
    trust = capture(moved["trust"][1])
    home_doc = json.loads(Path(trust["env"]["HOME"], ".claude.json").read_text()) \
        if Path(trust["env"]["HOME"], ".claude.json").is_file() else None
    assert trust["ignoring_lines"], (
        "the capture that is supposed to show the trust being lost shows nothing:\n"
        f"{trust['stderr']}")
    named = {m for line in trust["ignoring_lines"]
             for m in re.findall(r"in (\S+?)\.$", line)}
    assert named == {str(Path(trust["env"]["CLAUDE_CONFIG_DIR"]) / ".claude.json")}, (
        f"the CLI named {named} as the file it reads trust from, and the enumeration says "
        f"{moved['trust'][0]} under the config directory")
    if home_doc is not None:
        assert home_doc.get("projects"), (
            "the decider capture is worthless unless $HOME/.claude.json DID trust the "
            "workspace — otherwise it shows only that an untrusted workspace is untrusted")
        assert trust["cwd"] in home_doc["projects"], (
            f"$HOME/.claude.json in the decider capture does not trust {trust['cwd']}: "
            f"{list(home_doc['projects'])}")

    # And the tool learns none of those words. Every one of them is the REPOSITORY's, in its
    # hand-written manifest, because `manifest` may not ask `agent` and `agent` is the only
    # module allowed to know the CLI (SPEC.md §10). `test_transcript.py` guards the CLI's frame
    # names the same way; this guards its configuration vocabulary.
    offenders = []
    for path in sorted(APP.rglob("*")):
        # `app/agent/` is exempt for the reason §10 gives and `test_only_the_agent_module_knows_
        # the_cli` already encodes: it is the ONE module allowed to know the CLI, and the
        # enumeration above lives there precisely so nothing else has to.
        if path.suffix not in (".cpp", ".h") or path.parent.name == "agent":
            continue
        text = path.read_text()
        for needle in (".claude.json", "hasTrustDialogAccepted", "trustDialog"):
            if needle in text:
                offenders.append(f"{path.relative_to(APP.parent)}: {needle}")
    assert not offenders, (
        "the CLI's configuration vocabulary is hardcoded in the tool (SPEC.md §10). It belongs "
        "in the target repository's manifest, which is hand-written and committed:\n  "
        + "\n  ".join(offenders))

    # ...and the KEY the tool asks an operator to add is the key the agent will look up. Three
    # captures, and the third is the one that matters: from a linked worktree — inside the
    # repository or outside it — the agent keys on the repository the worktree was made FROM.
    # So a tool pointed at a linked worktree would ask for a key nothing reads, get it, and go
    # green over a run that still loses every permission. `manifest::Store` refuses that case
    # instead, and `test_an_untrusted_config_is_refused_at_load_not_at_the_run` reads it back.
    for cell in ("worktree-key-is-the-repository-root",
                 "worktree-key-from-a-worktree-inside-the-repo",
                 "worktree-key-from-a-worktree-outside-the-repo"):
        rec = capture(cell)
        assert rec["key_the_cli_named"] == rec["repository_root"], (
            f"{cell}: the agent keyed the trust on {rec['key_the_cli_named']!r} and the "
            f"repository root is {rec['repository_root']!r}")
    assert not capture("worktree-key-from-a-worktree-outside-the-repo")[
        "cwd_is_the_repository_root"], "the worktree cell measured the root itself"

    # ...and IF a repository declares `agent.config_trust`, every word of it is spelled the way
    # the CLI spells it.
    #
    # CHANGED BY SRV-FIX-052, and this is the one assertion of this ticket's that changed meaning.
    # It used to require THIS repository to declare the key — correct while a lane read its trust
    # out of a directory the tool did not own, and a hand edit in every developer's home once the
    # requirement is stated that way. The tool now GENERATES the trust it uses, per lane, in a
    # directory it owns (`test_lane_home.py::test_the_trust_the_agent_reads_is_the_one_the_tool_
    # generated`), so this repository declares nothing and
    # `test_manifest.py::test_a_repository_that_declares_no_credentials_still_starts_a_lane`
    # asserts that it stays that way. The refusal itself is untouched and still fires for a
    # repository that asks for it — what is weaker is not the check, it is the number of
    # repositories that need one.
    manifest = json.loads((REPO_ROOT / ".factory" / "factory.json").read_text())
    declared = manifest.get("agent", {}).get("config_trust")
    if declared:
        assert declared["keyed_by_path"] == "projects", declared
        assert declared["flag"] == "hasTrustDialogAccepted", declared
        assert Path(os.path.expanduser(declared["file"])).name == moved["trust"][0], (
            f"the manifest points at {declared['file']} and the CLI reads "
            f"{moved['trust'][0]} under the config directory")


# ── 3 · refused at load, not at the run ──────────────────────────────────────────────────────

def test_an_untrusted_config_is_refused_at_load_not_at_the_run(trustenv, config_dir):
    """F31's precedent, applied: a role naming a missing prompt file is refused at LOAD rather
    than at the moment it is needed, and so is a config that cannot carry this repository's
    permissions.

    The alternative — start the lane and let each run announce the loss on its own stderr — is
    what this repository did for a day, and it is worse than a failure: a run with no
    permissions works, answers, and asks about everything, which is indistinguishable from the
    product behaving normally.

    Two ways to be untrusted, and both are refused: the file has no entry for this repository,
    and the file is not there at all.
    """
    cfg = config_dir(trusts=[])                     # a config that trusts nothing
    missing = cfg / "not-a-config.json"

    for label, block in (
            ("no entry for this repository", agent_block(cfg)),
            ("no config file at all",
             {"command": "claude", "credentials_dir": str(cfg),
              "config_trust": {"file": str(missing), "keyed_by_path": "projects",
                               "flag": "hasTrustDialogAccepted"}})):
        env = trustenv.build(agent=block)

        r = env.srv.get("/api/manifest")
        assert r.status_code == 422, (
            f"[{label}] the manifest loaded against a config that cannot carry this "
            f"repository's permissions ({r.status_code}):\n{r.text}")
        body = r.json()["error"]
        assert body["code"] == "invalid_manifest", body
        detail = body["detail"]
        assert detail["reason"] == "agent_config_untrusted", detail
        assert detail["pointer"] == "/agent/config_trust", detail
        assert detail["path"] == str(block["config_trust"]["file"]), (
            f"[{label}] the refusal does not name the FILE to fix: {detail}")
        assert detail["field"] == f'projects["{env.real}"].hasTrustDialogAccepted', (
            f"[{label}] the refusal does not name the KEY to add: {detail}")
        # ...and the message is something an operator can act on without reading this test.
        assert str(env.real) in body["message"] and str(block["config_trust"]["file"]) \
            in body["message"], body["message"]

        # ...and no lane is ever claimed against it, exactly as a missing binary is not.
        r = env.claim()
        assert r.status_code >= 400, (
            f"[{label}] a lane was claimed against an untrusted config: {r.status_code} {r.text}")
        assert not list((Path(env.root) / ".factory" / "worktrees").glob("*")), \
            f"[{label}] a worktree was created against an untrusted config"
        env.unchanged()

    # ...and a repository root that is a LINKED WORKTREE is refused even when the config says
    # exactly what this check asked for. Measured (`…-worktree-key-*`): the agent keys on the
    # repository a worktree was made from, so satisfying the tool there buys a green tool over a
    # run that still loses every permission. A check that can say yes while the defect is live
    # is worse than no check, and this is the one place this ticket could have shipped one.
    main = trustenv.build(agent=agent_block(cfg),
                          prepare=lambda real: (cfg / ".claude.json").write_text(json.dumps(
                              {"projects": {str(real): {"hasTrustDialogAccepted": True}}},
                              indent=2)))
    linked = Path(main.root).parent / "linked-worktree"
    git(main.root, "worktree", "add", "-q", str(linked), "-b", "har047-linked", "HEAD")
    # The config is made to trust the WORKTREE — the very key a refusal would have asked for.
    (cfg / ".claude.json").write_text(json.dumps(
        {"projects": {str(Path(os.path.realpath(linked))): {"hasTrustDialogAccepted": True}}},
        indent=2))
    log = Path(tempfile.mkdtemp(prefix="har047-linked-")) / "server.log"
    inst = _start_instance(linked, log)
    try:
        r = inst.get("/api/manifest")
        assert r.status_code == 422, (
            f"the factory accepted a linked worktree as a repository root, so the key it would "
            f"ask an operator for is a key the agent never reads ({r.status_code}):\n{r.text}")
        detail = r.json()["error"]["detail"]
        assert detail["pointer"] == "/agent/config_trust", detail
        assert "linked worktree" in r.json()["error"]["message"], r.json()["error"]["message"]
    finally:
        stop_process(inst.proc)
        inst.handle.close()
    git(main.root, "worktree", "remove", "--force", str(linked))
    git(main.root, "branch", "-D", "har047-linked")

    # ...and the same repository, trusted, loads. Otherwise this case passes against a tool that
    # refuses everything.
    env = trustenv.build(
        agent=agent_block(cfg),
        prepare=lambda real: (cfg / ".claude.json").write_text(json.dumps(
            {"projects": {str(real): {"hasTrustDialogAccepted": True}}}, indent=2)))
    assert env.srv.get("/api/manifest").status_code == 200, env.srv.get("/api/manifest").text
    assert env.claim().status_code == 201
    env.unchanged()


# ── 4 · MANDATORY NEGATIVE — the agent's stderr is still recorded ────────────────────────────
#
# The cheapest way to make the SESSION tab look right is to stop reading the agent's stderr, and
# it loses every future warning the CLI will ever print. `SRV-FEAT-010` chose the interleaved
# pipe deliberately and §1 pre-decided it stays. This case is what makes that decision hold.

STDERR_AGENT = r"""#!/bin/sh
log=./agent-log.txt
: > "$log"
echo "argv=$*" >> "$log"
printf '%s\n' "$HAR047_STDERR_LINE" >&2
echo "ready" >> "$log"
""" + stand_in.FRAME_READER + r"""
while : ; do
  if read_frame ; then
    printf '%s\n' "in:$text" >> "$log"
    case "$text" in bye) break ;; esac
  else
    break
  fi
done
echo "eof" >> "$log"
"""


def test_a_warning_on_the_agents_stderr_is_still_recorded(agentenv, monkeypatch):
    """An arbitrary non-JSON line on the agent's stderr arrives as `kind:"error", raw:true`,
    and `unmapped.unparseable` counts it.

    Note for QA: this case is GREEN before this ticket's fix and green after it, on purpose.
    §1 mandates it as a guard against a WRONG fix rather than as a red-first criterion, and
    names the break: *"Watch it fail against a patch that filters stderr, commit that patch,
    take it out."* That patch is `app/lane/git.cpp`'s second `dup2` sending the child's stderr to
    `/dev/null`; the red run is committed beside this ticket's report.
    """
    marker = "har047-stderr-" + os.urandom(6).hex()
    monkeypatch.setenv("HAR047_STDERR_LINE", marker)
    env = agentenv.build(script=STDERR_AGENT.replace("$HAR047_STDERR_LINE", marker),
                         name="stderr-agent.sh")
    lane = env.claim()
    run = env.start(lane)

    path = Path(env.root) / ".factory" / "runs" / run["run_id"] / "transcript.jsonl"
    deadline = time.time() + 30
    entries = []
    while time.time() < deadline:
        try:
            entries = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        except OSError:
            entries = []
        if any(marker in e.get("text", "") for e in entries):
            break
        time.sleep(0.05)

    raw = [e for e in entries if marker in e.get("text", "")]
    assert raw, (
        f"a line the agent wrote to stderr never reached the transcript. Swallowing it is the "
        f"cheapest way to make this ticket's screen look right and lose every warning the CLI "
        f"will ever print.\nentries: {json.dumps(entries)[:2000]}\n"
        f"agent log:\n{env.log(lane)}")
    assert raw[0]["kind"] == "error" and raw[0].get("raw") is True, (
        f"the stderr line was recorded as something other than a raw error entry: {raw[0]}")

    env.srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    meta_path = Path(env.root) / ".factory" / "runs" / run["run_id"] / "meta.json"
    deadline = time.time() + 30
    meta = {}
    while time.time() < deadline:
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            meta = {}
        if meta.get("unmapped", {}).get("unparseable"):
            break
        time.sleep(0.05)
    assert meta.get("unmapped", {}).get("unparseable", 0) >= 1, (
        f"the line was recorded and NOT counted, so nobody reading `unmapped` would know the "
        f"CLI said something this tool has no kind for: {meta}")
    env.unchanged()
