"""SRV-FEAT-003 — the manifest is the one door.

Every case builds its own synthetic repository under the test's temporary directory and points
a server at it. Nothing here reads the repository these tests live in: a manifest module that
only works against Acme is the exact failure the module exists to prevent.
"""

import json
import os
import re
import shutil
from pathlib import Path

import pytest
import requests

from conftest import SERVER_DIR


def manifest_of(srv):
    r = srv.get("/api/manifest")
    assert r.status_code == 200, r.text
    return r.json()


def test_unknown_major_refused(repo, serve):
    """F1 — a major we do not speak stops the load, and names both versions.

    `SPEC.md` F1 is this case. The refusal has to be legible without reading the code, which is
    why the message must carry the manifest's version *and* the build's.
    """
    root = repo.manifest({
        "factory": "9.0",
        "workspaces": {"quarantined-workspace": {"path": "."}},
        "docs": {"epics": "quarantined-epics"},
    })
    srv = serve(root)

    r = srv.get("/api/manifest")
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "unsupported_manifest_major", err
    assert "9.0" in err["message"], err["message"]
    assert "0.1" in err["message"], err["message"]
    assert err["detail"]["manifest"] == "9.0" and err["detail"]["supported"] == "0.1", err

    # ...and no other endpoint serves data out of the refused manifest.
    for path in ("/api/health", "/api/manifest", "/api/slow"):
        body = srv.get(path).text
        assert "quarantined-workspace" not in body, f"{path} served a refused manifest: {body}"
        assert "quarantined-epics" not in body, f"{path} served a refused manifest: {body}"

    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["manifest"] == "invalid", health.text


def test_unknown_minor_loads_with_warnings(repo, serve):
    """An unknown minor is additive by contract: load it, report what you did not recognise."""
    root = repo.manifest({
        "factory": "0.9",
        "workspaces": {"core": {"path": "."}},
        "lanes": {"host": 2, "parallel_universes": 4},
    })
    srv = serve(root)
    body = manifest_of(srv)

    assert body["factory"] == "0.9", body
    seen = {(w["code"], w.get("pointer")) for w in body["warnings"]}
    assert ("unknown_key", "/lanes/parallel_universes") in seen, body["warnings"]
    assert ("unknown_minor", "/factory") in seen, body["warnings"]

    # ignored means ignored: the key survives nowhere but the warning that reports it, and
    # its recognised sibling is untouched.
    assert body["lanes"] == {"host": 2}, body["lanes"]
    served = {k: v for k, v in body.items() if k != "warnings"}
    assert "parallel_universes" not in json.dumps(served), served


def test_paths_resolve_to_absolute(repo, serve):
    """Downstream receives absolute paths, and a missing directory is reported, not fatal."""
    repo.mkdir("requirements")
    repo.mkdir("docs/tickets")
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "requirements", "tickets": "docs/tickets", "reports": "nowhere/at/all"},
    })
    srv = serve(root)
    body = manifest_of(srv)

    paths = body["resolved"]["paths"]
    for key in ("docs.epics", "docs.tickets", "docs.reports"):
        assert key in paths, paths
        assert os.path.isabs(paths[key]), paths[key]
        assert paths[key].startswith(str(repo.real)), (key, paths[key], str(repo.real))

    assert paths["docs.epics"] == str(repo.real / "requirements"), paths
    # E3's rule: a missing directory is a manifest mistake, not a load failure.
    assert paths["docs.reports"] == str(repo.real / "nowhere/at/all"), paths
    missing = [w for w in body["warnings"] if w["code"] == "path_missing"]
    assert [w["path"] for w in missing] == [str(repo.real / "nowhere/at/all")], body["warnings"]


def test_missing_manifest_is_404_with_path(repo, serve):
    """No manifest: say where you looked. Nine of ten of these are a path, not a bug."""
    srv = serve(repo.root)

    r = srv.get("/api/manifest")
    assert r.status_code == 404, r.text
    err = r.json()["error"]
    assert err["code"] == "not_found", err
    assert err["detail"]["path"] == str(repo.real / ".factory" / "factory.json"), err


def test_health_admits_a_broken_manifest(repo, serve):
    """A process that is up must be able to say it is up with a broken configuration."""
    root = repo.manifest("{ this is not json")
    srv = serve(root)

    h = srv.get("/api/health")
    assert h.status_code == 200, h.text
    body = h.json()
    assert body["factory"] == "0.1", body
    assert body["manifest"] == "invalid", body
    assert body["repo"]["root"] == str(repo.real), body
    assert body["repo"]["name"] == repo.real.name, body
    assert body["detail"]["reason"], body
    assert isinstance(body["uptime_s"], int), body

    m = srv.get("/api/manifest")
    assert m.status_code == 422, m.text
    err = m.json()["error"]
    assert err["code"] == "invalid_manifest", err
    # a syntax error has an offset, not a JSON pointer — there is no document to point into
    assert err["detail"]["reason"] == "parse_error", err
    assert err["detail"]["offset"] >= 0, err


def test_defaults_are_filled_not_guessed(repo, serve):
    """The five defaults the schema documents, and nothing else invented."""
    root = repo.manifest({"factory": "0.1", "workspaces": {"core": {"path": "."}}})
    srv = serve(root)
    body = manifest_of(srv)

    assert body["lanes"] == {"host": 3}, body
    assert body["docs"] == {"epics": "docs/epics",
                            "tickets": "docs/tickets",
                            "reports": "docs/reports"}, body
    assert body["agent"] == {"command": "claude"}, body

    # absent, not invented: no gate, no build, no empty strings standing in for them
    assert body["workspaces"] == {"core": {"path": "."}}, body["workspaces"]
    for key in ("stages", "display", "resources"):
        assert key not in body, f"{key} was invented: {body.get(key)!r}"


def test_a_path_leaving_the_repo_is_refused(repo, serve):
    """The tool serves the repository it was pointed at and no other.

    Rule 4 of the shape, and the one QA is told to attack: a manifest that escapes its own
    repository would let every downstream reader out with it.
    """
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "../../etc"},
    })
    srv = serve(root)

    r = srv.get("/api/manifest")
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["pointer"] == "/docs/epics", err
    assert err["detail"]["reason"] == "path_escapes_repo", err
    assert "/etc" not in json.dumps(err.get("detail", {}).get("resolved", "")), err


def test_a_version_that_is_not_major_minor_is_refused(repo, serve):
    """Found by attacking the module after it went green, and kept so it stays refused.

    `"0"`, `"0.1.5"` and `"-1.0"` all loaded as major 0 at first — `stoi` reads a prefix and
    shrugs at the rest. Reading a prefix and shrugging is guessing, and the one rule this
    module has is that it does not guess.
    """
    for bad in ("0", "0.1.5", "-1.0", "zero.one", "0.", ".1"):
        root = repo.manifest({"factory": bad, "workspaces": {"core": {"path": "."}}})
        srv = serve(root)
        r = srv.get("/api/manifest")
        assert r.status_code == 422, f"{bad!r} loaded: {r.text}"
        err = r.json()["error"]
        assert err["detail"]["reason"] == "bad_version", (bad, err)
        assert err["detail"]["pointer"] == "/factory", (bad, err)


def test_shapes_that_are_typos_are_not_read_as_intent(repo, serve):
    """Also from the post-green attack: two more places the tool was filling in a meaning."""
    root = repo.manifest({"factory": "0.1", "workspaces": {"core": {"path": ""}}})
    r = serve(root).get("/api/manifest")
    assert r.status_code == 422, r.text
    assert r.json()["error"]["detail"]["reason"] == "empty_path", r.text
    assert r.json()["error"]["detail"]["pointer"] == "/workspaces/core/path", r.text

    root = repo.manifest([1, 2, 3])
    r = serve(root).get("/api/manifest")
    assert r.status_code == 422, r.text
    assert r.json()["error"]["detail"]["reason"] == "wrong_type", r.text


def test_start_preflight_shape_is_validated_and_exposed(repo, serve):
    declared = {"command": "test -f ready.ok", "timeout_seconds": 3,
                "exempt_ticket_ids": ["TCK-001"]}
    root = repo.manifest({"factory": "0.1", "workspaces": {
        "core": {"path": ".", "start_preflight": declared}}})
    response = serve(root).get("/api/manifest")
    assert response.status_code == 200, response.text
    assert response.json()["workspaces"]["core"]["start_preflight"] == declared

    for bad in ({"command": ""}, {"command": " \t "},
                {"command": "true", "timeout_seconds": 0},
                {"command": "true", "timeout_seconds": 61},
                {"command": "true", "exempt_ticket_ids": [""]}):
        root = repo.manifest({"factory": "0.1", "workspaces": {
            "core": {"path": ".", "start_preflight": bad}}})
        response = serve(root).get("/api/manifest")
        assert response.status_code == 422, (bad, response.text)


def test_start_preflight_uses_workspace_command_reachability_warning(repo, serve):
    root = repo.manifest({"factory": "0.1", "workspaces": {
        "core": {"path": ".", "start_preflight": {
            "command": "missing-preflight-program --check"}}}})
    response = serve(root).get("/api/manifest")
    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert any(w.get("code") == "command_unrunnable"
               and w.get("pointer") == "/workspaces/core/start_preflight/command"
               and w.get("program") == "missing-preflight-program" for w in warnings), warnings


TARGET_REPO_KNOWLEDGE = re.compile(
    r"docs/(epics|tickets|reports)"          # a document path is the manifest's to know
    r"|\.factory"                            # even the tool's own directory in someone's repo
    r"|firmware|acme|esp32|\broti\b"        # this repository, by name
    r"|\bboard\b|\bseam\b|\bbench\b"         # this repository's vocabulary
    r"|commons|\bslave\b|\bcamera\b",
    re.I)


def boundary_sweep():
    """The `S1` grep, returned as what it EXAMINED, what it SKIPPED and what it FOUND.

    Lifted out of the case below by `SRV-FIX-447` so the sweep itself can be asserted. A grep
    that skipped every file reports the same empty offender list as a clean tree, and the cheap
    way to make the case below green has always been to widen the skip rather than move the
    knowledge — `test_the_module_boundary_grep_sweeps_what_it_claims` is what refuses that.
    """
    app = SERVER_DIR / "app"
    examined, skipped, offenders = {}, [], []
    for path in sorted(app.rglob("*")):
        if path.suffix not in (".h", ".cpp"):
            continue
        rel = str(path.relative_to(SERVER_DIR))
        if "manifest" in path.parts:             # app/manifest/ is the one door; not app/api/_manifest.cpp
            skipped.append(rel)
            continue
        lines = path.read_text().splitlines()
        examined[rel] = lines
        for n, line in enumerate(lines, 1):
            if TARGET_REPO_KNOWLEDGE.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    return examined, skipped, offenders


@pytest.mark.repository
def test_no_module_but_manifest_knows_a_path():
    """Negative, and the one this whole product rests on.

    `SPEC.md` §10 names this as the boundary that decays first. Asserted in source rather than
    in prose, because a boundary asserted only in prose is `SRV-FEAT-002` happening again.
    Cheap on purpose: every later ticket extends the word list rather than replacing the test.

    `@pytest.mark.repository` is `SRV-FIX-447`'s structural change and not decoration. This is an
    architectural rule over the whole of `app/`, filed in the test file of ONE module, and the
    tier rules run a module's test file only for a ticket that touched that module. `SRV-FIX-416`
    touched `app/lane`; this case is what refused what it wrote; nothing in its gate opened this
    file, and it stayed red on `main` for four days. Every ticket runs `pytest tests/ -m
    repository` — the commit that sets `status: awaiting-qa` is a document change — so the marker
    is what makes a rule about every module readable by every ticket.
    """
    _, _, offenders = boundary_sweep()

    assert offenders == [], (
        "target-repository knowledge outside app/manifest/:\n" + "\n".join(offenders))


# `SRV-FIX-447`'s evidence, verbatim off `main` at `e33fc473`: the three lines in
# `app/lane/store.cpp` that joined the tool's own directory name themselves. They are the
# population the sweep above has to be able to see, so they are what the case below feeds it.
LANE_STORE_LEAK = (
    'return root + "/.factory/lanes/" + flat + ".json";',
    'const fs::path dir = fs::path(root_) / ".factory" / "lanes";',
    'fs::create_directories(fs::path(root_) / ".factory" / "lanes", ec);',
)


@pytest.mark.repository
def test_the_module_boundary_grep_sweeps_what_it_claims():
    """MANDATORY NEGATIVE (`SRV-FIX-447`) — the fix may not be an exemption.

    The case above is an empty list, and there are three ways to produce one: move the knowledge
    into `app/manifest/`, excuse the file that holds it, or stop recognising the words. Only the
    first is a fix. So this case asserts the sweep rather than its verdict, in three parts, each
    of which is one of those ways:

    - the grandfathered set is EXACTLY the one door — derived off the filesystem, never a list
      typed here, so an entry added to excuse `app/lane/store.cpp` fails whatever it is spelled;
    - `app/lane/store.cpp` is in the population, with more lines than the highest line number
      `SRV-FIX-447` §1 quoted, so a sweep that examined nothing is red rather than green;
    - the detector still recognises all three of those lines, so softening the pattern is not a
      way past the case above either.
    """
    examined, skipped, _ = boundary_sweep()

    assert examined, (
        "the boundary sweep examined no file at all — it reports a clean tree by looking at "
        "nothing, which is the shape every ratchet in this repository has failed in")

    one_door = sorted(str(p.relative_to(SERVER_DIR))
                      for p in (SERVER_DIR / "app" / "manifest").rglob("*")
                      if p.suffix in (".h", ".cpp"))
    assert one_door, "app/manifest/ holds no source — the derivation below is measuring nothing"
    assert sorted(skipped) == one_door, (
        "the boundary grep's grandfathered set is no longer exactly app/manifest/:\n"
        f"  skipped:  {sorted(skipped)}\n"
        f"  the door: {one_door}\n"
        "S1 has one exemption and it is the door itself. A module excused from the grep is a "
        "module that knows a target repository has a shape, which is the thing being asserted")

    leaked = "app/lane/store.cpp"
    assert leaked in examined, (
        f"{leaked} is not in the swept population — it holds the three lines SRV-FIX-447 was "
        "filed for, and a sweep that cannot see them cannot say they are gone")
    assert len(examined[leaked]) >= 395, (
        f"{leaked} is {len(examined[leaked])} lines; SRV-FIX-447 §1 quoted offences at 200, 284 "
        "and 395, so a sweep reading fewer than that read a different file. If the store was "
        "split into one action per file — which this codebase's style would welcome — this line "
        "is the thing to update, and the population it was standing in for is `examined` above")

    blind = [line for line in LANE_STORE_LEAK if not TARGET_REPO_KNOWLEDGE.search(line)]
    assert blind == [], (
        "the pattern no longer recognises the lines this ticket exists to remove, so the case "
        f"above is green because it stopped looking: {blind}")


def test_a_repository_root_is_given_not_discovered(serve):
    """Negative: no root, no server. Discovery is how a tool serves a repository nobody chose."""
    code, out = serve.launch([])
    assert code == 2, f"exit {code}: {out}"
    assert "--repo" in out, out


# ── SRV-FIX-048 · agent.start_prompt_file ════════════════════════════════════════════════════
#
# What a start MEANS is the repository's to say (SPEC.md E18, §10). The two cases below are the
# pair every declared-path key in this file already has: it is proven AT LOAD, and a repository
# that declares nothing is untouched.

def test_a_start_prompt_file_that_is_not_there_is_refused_at_load(repo, serve):
    """`E2` reports the manifest invalid with `detail.path` naming the file that is not there.

    At load, not at the moment somebody presses Start. The failure this rule exists to prevent is
    a lane that dies at the worst possible time — after a claim, after a worktree, with a person
    watching a run that will never say anything. Same rule, same shape and the same reason as
    `test_a_role_naming_a_missing_prompt_file_is_refused_at_load`.
    """
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "agent": {"command": "/bin/sh", "start_prompt_file": ".factory/prompts/run-ticket.md"},
    })
    srv = serve(root)

    r = srv.get("/api/manifest")
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["pointer"] == "/agent/start_prompt_file", err
    assert err["detail"]["path"].endswith(".factory/prompts/run-ticket.md"), err
    assert str(repo.real) in err["detail"]["path"], err

    # ...and the process is up and says so, which is what E1 is for.
    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["manifest"] == "invalid", health.text

    # ...and the same manifest with the file present loads.
    repo.write(".factory/prompts/run-ticket.md", "Run ticket {ticket_path}.\n")
    ok = serve(root).get("/api/manifest")
    assert ok.status_code == 200, ok.text
    assert ok.json()["agent"]["start_prompt_file"] == ".factory/prompts/run-ticket.md", ok.text


def test_a_repository_that_declares_no_credentials_still_starts_a_lane(tmp_path, serve):
    """The default path needs no `credentials_dir` and no `config_trust`.

    `SRV-FIX-047` made a lane refuse to start unless the agent's configuration recorded this
    repository as trusted. That was the right refusal for a config the tool does not own — and it
    made a hand edit in every developer's home a precondition for using the tool, on every
    machine, with the obvious remedy (accept the dialog once, interactively) writing to a
    DIFFERENT file. No test can fail because of that, so this one is written to.

    Two halves. A repository that declares only a command starts a lane and runs a command in it;
    and THIS repository's own manifest asks nobody to edit anything before their first lane.
    """
    from test_isolation import run_in_lane
    from test_lane import build_repo, status_of

    root = build_repo(tmp_path / "plain-agent", agent={"command": "claude"})
    baseline = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1",
                FACTORY_AGENT_CONFIG_ROOT=str(tmp_path / "agent-homes"))

    r = srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"a repository that declares nothing but an agent command did not load: {r.text}")

    claimed = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()

    class Env:                                    # what `run_in_lane` reads, and nothing more
        pass

    env = Env()
    env.srv = srv
    env.root = root
    rc, out = run_in_lane(env, lane, "echo har052-default-path")
    assert rc == 0 and "har052-default-path" in out, (
        f"a lane on a repository that declares no credentials could not run a command: {out!r}")

    assert status_of(root) == baseline, "the main checkout changed"

    # ...and this repository asks for no hand edit either. `agent.config_trust` remains available
    # for a repository that deliberately points its agent at a configuration the tool does not
    # own; the default path generates the trust it needs, so declaring it here would put a manual
    # JSON edit back in front of every clone.
    manifest = json.loads(
        (Path(os.path.realpath(SERVER_DIR.parents[0])) / ".factory" / "factory.json").read_text())
    assert "config_trust" not in manifest.get("agent", {}), (
        "this repository's manifest still declares agent.config_trust, so a team member who "
        "clones it must hand-edit a JSON file in their own home before any lane will start")


def test_no_start_prompt_declared_changes_nothing(tmp_path, serve):
    """A manifest with no such key behaves exactly as it did before this key existed: `E18` with
    no prompt writes nothing, and the tool invents neither a path nor a default.

    `F31`'s `test_no_roles_block_changes_nothing`, restated one layer up. **The factory is
    pointed at repositories that have never heard of this feature**, and a tool that reached for
    `.factory/prompts/…` because it is where this repository happens to keep one would have
    learned a repository (SPEC.md §10).
    """
    import time

    from test_agent import START_AGENT_SH, assert_reaped
    from test_lane import build_repo, git, status_of

    root = build_repo(tmp_path / "plain", agent={"command": "/bin/sh agent.sh"})
    (root / "agent.sh").write_text(START_AGENT_SH)
    (root / "stream.jsonl").write_text("")
    git(root, "add", "-A")
    git(root, "commit", "-m", "an agent, and a manifest that says nothing about starting")
    baseline = status_of(root)
    srv = serve(root, FACTORY_TEST_HOOKS="1")

    body = manifest_of(srv)
    assert "start_prompt_file" not in body["agent"], (
        f"the tool invented a start prompt for a repository that declared none: {body['agent']}")
    assert "agent.start_prompt" not in body["resolved"]["paths"], body["resolved"]

    claimed = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()

    started = srv.post(f"/api/lanes/{lane['lane']}/runs", json={})
    assert started.status_code == 201, started.text
    run = started.json()

    log = Path(lane["worktree"]) / "agent-log.txt"
    deadline = time.time() + 25
    while time.time() < deadline:
        if log.exists() and "ready" in log.read_text():
            break
        time.sleep(0.05)
    assert "ready" in log.read_text(), log.read_text()

    time.sleep(1.5)                     # long enough that a message which WAS coming would have
    text = log.read_text()
    assert [l for l in text.splitlines() if l.startswith("raw:")] == [], (
        f"E18 with no prompt wrote to stdin on a repository that declared nothing:\n{text}")

    meta = json.loads((root / ".factory" / "runs" / run["run_id"] / "meta.json").read_text())
    assert meta["turns"] == 0, meta

    srv.post(f"/api/lanes/{lane['lane']}/kill", json={"grace_s": 1})
    assert_reaped(run["pid"])
    assert status_of(root) == baseline, (
        f"the main checkout changed:\nbefore:\n{baseline}\nafter:\n{status_of(root)}")


# ── SRV-FIX-063 QA round 2, finding 1 ────────────────────────────────────────────────────────
#
# `devices: ["/home/dev"]` gave a lane the developer's home, listed and WRITABLE, from inside
# the sandbox that exists to make that impossible. Three places said it could not happen and none
# of them enforced it: §1's Safety clause ("never a directory, never a glob, never `/dev` itself"),
# §1's Pre-decided list ("No change to what a lane may WRITE"), and `SPEC.md:834` — added by that
# ticket — which publishes the property as a GUARANTEE.
#
# `confine.cpp` refused the literal string `/dev` and nothing else; `manifest/store.cpp` validated
# nothing at all. This is `ESCAPES.md` row 1 reachable again through one manifest line: the whole
# of `SRV-FEAT-002` is that the main checkout is ABSENT from a lane, not merely unmentioned.
#
# It is refused at LOAD, beside the `409` and the `422` that already name a JSON pointer, because a
# manifest that would widen the sandbox must be a named refusal and not a wider sandbox.

def _with_devices(entries, tmp):
    return {
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "resources": {"board": {"exclusive": True, "needed_by": ["core"], "devices": entries}},
    }


def test_a_device_that_is_a_directory_is_refused_at_load(repo, serve, tmp_path):
    """The breach, refused where it is stated rather than where it is exploited."""
    root = repo.manifest(_with_devices([str(tmp_path)], tmp_path))
    r = serve(root).get("/api/manifest")

    assert r.status_code == 422, (
        f"a manifest naming a DIRECTORY as a device loaded: {r.status_code} {r.text}\n"
        f"`--dev-bind` on a directory is a read-write bind of that whole tree into the lane, "
        f"which is SRV-FEAT-002's guarantee deleted by one manifest line.")
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["reason"] == "not_a_device", err
    assert err["detail"]["pointer"] == "/resources/board/devices/0", err
    assert str(tmp_path) in err["detail"]["path"], err


def test_a_device_that_resolves_to_the_root_is_refused_at_load(repo, serve):
    """`/dev/..` canonicalises to `/` and passed the string comparison at `confine.cpp:238`.

    QA's finding 2: it did not breach anything, it produced a lane that HUNG — no output, no exit
    status, 90 seconds. A guard that compares strings is not a boundary; asking "is this a device
    node" answers both this and the case above, and answers them before a process exists.
    """
    root = repo.manifest(_with_devices(["/dev/.."], None))
    r = serve(root).get("/api/manifest")

    assert r.status_code == 422, (
        f"`/dev/..` loaded: {r.status_code} {r.text}")
    assert r.json()["error"]["detail"]["reason"] == "not_a_device", r.text


def test_a_real_device_node_still_loads(repo, serve):
    """NEGATIVE (mandatory). The refusal must not become "no manifest may declare a device".

    `/dev/null` is a character device on every machine this can run on, and declaring it is the
    ordinary case SRV-FIX-063 exists to serve.
    """
    root = repo.manifest(_with_devices(["/dev/null"], None))
    r = serve(root).get("/api/manifest")

    assert r.status_code == 200, (
        f"a manifest declaring a real character device was refused: {r.status_code} {r.text}")
    board = r.json()["resources"]["board"]
    assert board["devices"] == ["/dev/null"], board


def test_a_device_that_is_not_there_is_named_rather_than_dropped(repo, serve):
    """QA's finding 3: a `devices` entry naming a node that is not present was silently dropped,
    so the lane held the lock and had no device — SRV-FIX-063's own defect, re-created by a typo.

    A typo in a manifest is a thing a person can fix in five seconds once they are told. Being
    told is the whole difference, and `SPEC.md` E2 already has the shape for it.
    """
    root = repo.manifest(_with_devices(["/dev/ttyUSB-that-is-not-there"], None))
    r = serve(root).get("/api/manifest")

    assert r.status_code == 422, (
        f"a device that does not exist loaded silently: {r.status_code} {r.text}")
    err = r.json()["error"]
    assert err["detail"]["reason"] == "device_not_found", err
    assert err["detail"]["pointer"] == "/resources/board/devices/0", err


def test_a_device_on_a_resource_no_lane_can_hold_is_refused_at_load(repo, serve):
    """SRV-FIX-070 — a device that would never arrive, named instead of ignored.

    `lane::Store::resourcesFor_` filters on `exclusive`, so a resource nothing queues on is never
    held by a lane and a device hung off it is validated at load and then bound nowhere. The lane
    starts, the manifest reads as if the board were declared, and the first anybody hears of it is
    an agent meeting `ENOENT` — `SRV-FIX-063`'s sentence arriving through the field it added.
    """
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "resources": {"board": {"exclusive": False, "needed_by": ["core"],
                                "devices": ["/dev/null"]}},
    })
    r = serve(root).get("/api/manifest")

    assert r.status_code == 422, (
        f"a device on a non-exclusive resource loaded, and would be bound into no lane at all: "
        f"{r.status_code} {r.text}")
    err = r.json()["error"]
    # §1 asks for the CODE and the MESSAGE too, and the shipped case asserted neither: both are
    # true of the live server and nothing was keeping them true (QA of this ticket, F1).
    assert err["code"] == "invalid_manifest", err
    assert "exclusive" in err["message"].lower(), (
        f"the refusal does not tell a person what has to change: {err['message']!r}")
    assert err["detail"]["reason"] == "devices_need_exclusive", err
    # `pointer` is at `exclusive` and not at `devices/<i>` — §1 was amended to the shipped answer
    # rather than the answer bent to §1. `pointer` says which part is wrong and `field` says what
    # to write there, and `exclusive` is the one a person actually edits (SRV-FIX-047's rule).
    assert err["detail"]["pointer"] == "/resources/board/exclusive", err
    assert err["detail"]["field"] == "exclusive", err


def test_a_non_exclusive_resource_without_devices_still_loads(repo, serve):
    """NEGATIVE (mandatory). The refusal is about `devices`, not about non-exclusive resources.

    A resource that queues nothing and names no device is a perfectly ordinary declaration, and the
    cheap fix — requiring `exclusive` everywhere — would refuse it.
    """
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "resources": {"quota": {"exclusive": False, "needed_by": ["core"],
                                "description": "not a lock"}},
    })
    r = serve(root).get("/api/manifest")
    assert r.status_code == 200, r.text
    assert r.json()["resources"]["quota"]["exclusive"] is False, r.text


# ── SRV-FIX-071 ══════════════════════════════════════════════════════════════════════════════

def test_an_invalid_manifest_is_visible_on_the_surfaces_that_are_not_it(repo, serve):
    """A failed load and a repository with no work in it were the same picture.

    `GET /api/manifest` answered `422` with a reason and a pointer, and every other endpoint
    answered `200` with empty data and an EMPTY `warnings` array — so the screens a person actually
    looks at showed a repository with no requirements, no tickets, no lanes and no resources, and
    nothing anywhere said why.

    In this repository that matters the moment `.factory/factory.json` declares a device: unplug
    the ESP32, or let the enumeration order move it to `ttyUSB2`, and the tool opens on an empty
    catalogue that looks exactly like a repository nobody has filed anything in.
    """
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "resources": {"board": {"exclusive": True, "needed_by": ["core"],
                                "devices": ["/dev/ttyUSB-unplugged"]}},
    })
    srv = serve(root)

    refused = srv.get("/api/manifest")
    assert refused.status_code == 422, refused.text
    said = refused.json()["error"]

    for path in ("/api/requirements", "/api/lanes"):
        r = srv.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        warnings = r.json().get("warnings", [])
        assert warnings, (
            f"{path} answers an empty body and an empty `warnings` while the manifest did not "
            f"load: {r.text[:300]}\nAn empty list and a failed load are the same picture.")
        w = warnings[0]
        assert w["code"] == "invalid_manifest", w
        assert w["message"] == said["message"], (
            f"{path} says something other than E2's own refusal: {w['message']!r} vs "
            f"{said['message']!r}")
        # the pointer is what makes it actionable, and E2 carries one for this manifest
        assert w.get("pointer") == said["detail"].get("pointer"), (said["detail"], w)


def test_a_repository_that_is_simply_empty_still_says_nothing(repo, serve):
    """NEGATIVE (mandatory). The cheap fix is a banner on every empty list.

    A repository whose manifest loads and which genuinely has no requirements is not a fault, and a
    warning there would teach a reader to ignore the one that matters.
    """
    root = repo.manifest({"factory": "0.1", "workspaces": {"core": {"path": "."}}})
    # The docs directories exist and hold nothing: an empty repository, not a mis-declared one.
    # Without them the answer warns `path_missing`, correctly, and this case would be asserting
    # against a different fault than the one it is about.
    for d in ("docs/epics", "docs/tickets", "docs/reports"):
        (root / d).mkdir(parents=True, exist_ok=True)
    srv = serve(root)

    assert srv.get("/api/manifest").status_code == 200

    for path in ("/api/requirements", "/api/lanes"):
        r = srv.get(path)
        assert r.status_code == 200, r.text
        assert r.json().get("warnings", []) == [], (
            f"{path} warns about an ordinary empty repository: {r.text[:200]}")


# ── SRV-FIX-077 ══════════════════════════════════════════════════════════════════════════════
#
# `SRV-FIX-063` carried the DEVICE into the lane and stopped there. A `master` lane could `open()`
# `/dev/ttyUSB1` and could not build for it: ESP-IDF lives under the developer's home, the lane's
# home is a tmpfs, and no manifest key existed to say so. So a workspace declares what it needs from
# the host, and — like every other declared path in this file — a path that is not there is refused
# AT LOAD, naming the JSON pointer that named it, rather than discovered at BUILD with the board
# locked.

def _with_toolchain(entries):
    return {
        "factory": "0.1",
        "workspaces": {"core": {"path": ".", "toolchain": entries}},
    }


def test_a_workspace_toolchain_that_does_not_exist_is_named_in_the_422(repo, serve, tmp_path):
    """THE criterion: a person is TOLD before a lane is claimed, with the pointer.

    THE NAME IS KEPT, and the `422` in it is where the refusal USED to be. `docs/operations/ESCAPES.md`'s
    2026-08-02 row names this case, which makes it an eval an escape paid for, and
    `test_every_case_the_ledger_names_is_collected` refuses a ledger row whose case no longer
    exists — it caught this rename the same day it was made. A case may be sharpened, never
    renamed.

    A lane that discovers at BUILD that its toolchain is absent has already won the board lock and
    already made every hardware criterion BLOCKED — which is `FIX-075`, eight criteria, one board
    held for the duration. A person can fix a manifest line in five seconds once they are told.

    `SRV-FIX-532` cluster E1 (folds `SRV-FIX-491`) moved WHERE the refusal is, and not whether
    there is one. It was the whole manifest, so one workspace nobody was claiming took every
    surface down with it — including the one a `factory-server` lane uses to serve the repository
    it is a lane OF, which is why tier 3 could not be run from a lane at all. The warning carries
    the same three facts the `422` carried, and `E12` refuses the claim.
    """
    missing = tmp_path / "no-such-toolchain"
    root = repo.manifest(_with_toolchain([str(missing)]))
    r = serve(root).get("/api/manifest")

    assert r.status_code == 200, (
        f"one workspace's missing toolchain still refuses the whole manifest: "
        f"{r.status_code} {r.text}")
    said = [w for w in r.json().get("warnings", []) if w.get("code") == "workspace_unbuildable"]
    assert len(said) == 1, r.json().get("warnings")
    assert said[0]["pointer"] == "/workspaces/core/toolchain/0", said[0]
    assert said[0]["field"] == "core", said[0]
    assert str(missing) in said[0]["path"], said[0]


def test_a_workspace_toolchain_that_is_a_whole_home_is_refused(repo, serve):
    """§1's Safety clause, enforced where it is stated: *do not bind a whole home directory*.

    The cheap fix for this whole ticket is one manifest line naming `~/`, and it would pass every
    other case here — a lane could then read the developer's ssh keys, which is `SRV-FEAT-002`'s
    guarantee deleted by a manifest rather than by a bug. Refused at load, for the reason
    `SRV-FIX-063`'s `not_a_device` is: a manifest that would widen the sandbox must be a named
    refusal, never a wider sandbox.
    """
    for entry in ("~/", "/"):
        root = repo.manifest(_with_toolchain([entry]))
        r = serve(root).get("/api/manifest")
        assert r.status_code == 422, (
            f"a workspace declaring {entry!r} as its toolchain loaded: {r.status_code} {r.text}\n"
            f"That is a read-only bind of a whole home into every lane for this workspace.")
        err = r.json()["error"]
        assert err["detail"]["reason"] == "toolchain_too_broad", err
        assert err["detail"]["pointer"] == "/workspaces/core/toolchain/0", err


def test_a_workspace_that_declares_a_real_toolchain_still_loads(repo, serve, tmp_path):
    """NEGATIVE (mandatory). The refusals above must not become "no workspace may declare one".

    A directory that is there is the ordinary case this key exists to serve, and E2 hands it back
    normalised — the manifest is the one door product knowledge comes through, so what it loaded has
    to be readable off the endpoint.
    """
    real = tmp_path / "toolchain"
    (real / "bin").mkdir(parents=True)
    root = repo.manifest(_with_toolchain([str(real)]))
    r = serve(root).get("/api/manifest")

    assert r.status_code == 200, (
        f"a workspace declaring a toolchain directory that exists was refused: "
        f"{r.status_code} {r.text}")
    assert r.json()["workspaces"]["core"]["toolchain"] == [str(real)], r.text


def test_a_workspace_with_no_toolchain_is_unchanged(repo, serve):
    """NEGATIVE (mandatory). Absent means absent — no default, no discovery, no empty array.

    Every workspace in every repository this tool has ever been pointed at declares nothing, and a
    key that appears in the answer when nobody wrote it is the tool inventing a fact about somebody
    else's repository.
    """
    root = repo.manifest({"factory": "0.1", "workspaces": {"core": {"path": "."}}})
    r = serve(root).get("/api/manifest")
    assert r.status_code == 200, r.text
    assert "toolchain" not in r.json()["workspaces"]["core"], r.text


# ── SRV-FIX-078 · the set of "too broad" is one layer short ───────────────────────────────────

def test_a_toolchain_naming_the_parent_of_every_home_is_refused(repo, serve):
    """`SRV-FIX-077` asked *is this path the home*; the question is *does it CONTAIN the home*.

    `/home` contains the developer's home and every other user's besides, so it loaded cleanly and
    was read-bound into every lane for its workspace — `SRV-FEAT-002`'s guarantee removed by one
    manifest line. The spellings are DERIVED from this machine, never hard-coded: a literal
    `/home` is the same bug one directory up on a machine whose homes live elsewhere.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    parent = os.path.dirname(home)
    assert parent and parent != home, f"cannot derive the parent of {home!r}"

    for entry in (parent, "~/..", home + "/.."):
        root = repo.manifest(_with_toolchain([entry]))
        r = serve(root).get("/api/manifest")
        assert r.status_code == 422, (
            f"a workspace declaring {entry!r} loaded: {r.status_code} {r.text}\n"
            f"That path contains {home!r} and would be read-bound into every lane for this "
            f"workspace — the developer's ssh keys, git credentials and agent state with it.")
        err = r.json()["error"]
        assert err["detail"]["reason"] == "toolchain_too_broad", err
        assert err["detail"]["pointer"] == "/workspaces/core/toolchain/0", err


def test_a_narrow_toolchain_under_the_home_still_loads(repo, serve, tmp_path):
    """NEGATIVE (mandatory). The cheap wrong fix is "refuse anything under a home".

    It would pass the case above and refuse this repository's own manifest — `~/environment/
    esp-idf-v5.5.1` and `~/.espressif` are both under the home — stopping every `master` lane from
    building. The rule is CONTAINMENT of the home, not residence under it.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    narrow = tmp_path / "narrow-toolchain"
    (narrow / "bin").mkdir(parents=True)
    # ...and one that genuinely lives under the developer's home, which is the shape that matters
    under_home = os.path.join(home, ".har078-narrow")
    os.makedirs(os.path.join(under_home, "bin"), exist_ok=True)
    try:
        for entry in (str(narrow), under_home, "~/.har078-narrow"):
            root = repo.manifest(_with_toolchain([entry]))
            r = serve(root).get("/api/manifest")
            assert r.status_code == 200, (
                f"a narrow toolchain {entry!r} was refused: {r.status_code} {r.text}\n"
                f"Refusing everything under a home stops every lane that needs a real toolchain.")
    finally:
        shutil.rmtree(under_home, ignore_errors=True)


def test_a_symlinked_home_does_not_defeat_the_containment_guard(repo, serve, tmp_path):
    """QA round 1, F2 — transcribed from its reproduction.

    The guard canonicalised one operand and not the other: `weakly_canonical` on the DECLARED path,
    and `developerHomeOfThisProcess()` returning `$HOME` (or the passwd entry) raw. One symlink in
    the home's spelling and the containment comparison compares two different vocabularies, so a
    path that canonically CONTAINS the home loads cleanly — and a lane claimed for that workspace
    read the private key: `REACHED-SSH`, then `cat` → `PRIVATE KEY`.

    §1's own pre-decision says the rule is *"asked of the canonical path"*. It was asked of one.
    """
    real = tmp_path / "export" / "homes" / "dev"
    (real / ".ssh").mkdir(parents=True)
    (real / ".ssh" / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE KEY\n")
    (tmp_path / "home").symlink_to(tmp_path / "export" / "homes")
    spelled_home = str(tmp_path / "home" / "dev")          # $HOME, through the symlink
    canonical_parent = str(tmp_path / "export" / "homes")  # contains the home, canonically

    root = repo.manifest(_with_toolchain([canonical_parent]))
    r = serve(root, HOME=spelled_home).get("/api/manifest")
    assert r.status_code == 422, (
        f"a path containing the developer's home loaded because $HOME was spelled through a "
        f"symlink: {r.status_code} {r.text[:300]}")
    err = r.json()["error"]
    assert err["detail"]["reason"] == "toolchain_too_broad", err

    # ...and the control, which is what makes this about the COMPARISON and not about the fixture:
    # the same directory with $HOME spelled canonically was already refused before this fix.
    r2 = serve(root, HOME=str(real)).get("/api/manifest")
    assert r2.status_code == 422, r2.text


# ── SRV-FIX-131 · the guard is on one key and the bind is on two ──────────────────────────────

def test_an_agent_binary_whose_directory_is_a_whole_home_is_refused(repo, serve, tmp_path):
    """`agent.command` becomes a read-only bind exactly as `toolchain` does.

    `resolveAgentPaths_` stores `real.parent_path()` as `agent.binaryDir` and `confine.cpp` binds
    every `hostReadOnly` entry at its real path. A binary sitting directly in the developer's home
    therefore makes `binaryDir` the home, and every lane for every workspace gets `~/.ssh`
    read-bound — measured before this fix as `REACHED-SSH`.

    `SRV-FIX-078` fixed the set of paths recognised as too broad. It did not fix the set of KEYS
    the question is asked of.
    """
    import shutil as _shutil, stat as _stat
    home = os.path.realpath(os.path.expanduser("~"))
    probe = Path(home) / ".har131-agent-at-home-root"
    _shutil.copy("/bin/sh", probe)
    probe.chmod(probe.stat().st_mode | _stat.S_IEXEC)
    try:
        root = repo.manifest({
            "factory": "0.1",
            "workspaces": {"core": {"path": "."}},
            "agent": {"command": str(probe)},
        })
        r = serve(root).get("/api/manifest")
        assert r.status_code == 422, (
            f"an agent binary whose directory IS the developer's home loaded: {r.status_code}\n"
            f"Its parent is bound read-only into every lane for every workspace.")
        err = r.json()["error"]
        assert err["code"] == "invalid_manifest", err
        assert err["detail"]["pointer"] == "/agent/command", err
    finally:
        probe.unlink(missing_ok=True)


def test_an_agent_binary_in_an_ordinary_place_under_the_home_still_loads(repo, serve):
    """NEGATIVE (mandatory). The cheap wrong fix is "refuse a binary under a home".

    It would refuse this repository's own manifest — `claude` resolves under
    `~/.local/share/claude/versions/<v>` — and stop every lane from running an agent at all. The
    rule is CONTAINMENT of the home, not residence under it, and it is the same rule `toolchain`
    already uses.
    """
    r = serve(repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "agent": {"command": "claude"},
    })).get("/api/manifest")
    assert r.status_code == 200, (
        f"the ordinary agent location was refused: {r.status_code} {r.text[:300]}\n"
        f"Refusing everything under a home stops every lane from running an agent.")
    paths = r.json()["resolved"]["paths"]
    assert "agent.binary_dir" in paths, paths
    assert paths["agent.binary_dir"] != os.path.realpath(os.path.expanduser("~")), paths


# ── SRV-FIX-181 · a monorepo is a set of repositories, and the manifest says so ═══════════════
#
# `.factory/factory.json` declared six workspaces and ONE documents root, and the three workspaces
# whose tickets live in a second tree therefore had no reachable ticket at all: 168 documents the
# tool could not open while every assertion about them passed. `repos` is the manifest learning to
# say what a monorepo is — a set of repositories, each with a path, a documents tree of its own and
# the workspaces that belong to it — and every path a repo declares is relative to THAT repo, so
# the day one of them is split out its `path` becomes `"."` and nothing else in the manifest moves.

DOCS = {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"}


def two_repos(workspaces=None):
    """The shape §1 fixed: two repos, both writing the SAME relative document paths."""
    return {
        "factory": "0.1",
        "repos": {
            "root-repo": {"path": ".", "docs": dict(DOCS)},
            "sub-repo": {"path": "sub", "docs": dict(DOCS)},
        },
        "workspaces": workspaces or {
            "core": {"repo": "root-repo", "path": "server"},
            "sub-core": {"repo": "sub-repo", "path": "server"},
        },
    }


def test_a_repos_docs_paths_are_relative_to_its_own_path(repo, serve):
    """Two repos writing the same relative path resolve to two different absolute ones.

    Asserted on `GET /api/manifest`'s own resolved output rather than on a string the test joins:
    a fixture whose two repos wrote DIFFERENT relative paths could not tell a resolver that honours
    the repo's path from one that ignores it, because both would produce distinguishable answers.
    Identical input, different output, is the whole of the property.
    """
    for rel in ("docs/epics", "docs/tickets", "docs/reports",
                "sub/docs/epics", "sub/docs/tickets", "sub/docs/reports",
                "server", "sub/server"):
        repo.mkdir(rel)
    srv = serve(repo.manifest(two_repos()))

    paths = manifest_of(srv)["resolved"]["paths"]
    for kind in ("epics", "tickets", "reports"):
        assert paths[f"repos.root-repo.docs.{kind}"] == str(repo.real / "docs" / kind), paths
        assert paths[f"repos.sub-repo.docs.{kind}"] == str(repo.real / "sub" / "docs" / kind), \
            paths
    assert paths["repos.root-repo"] == str(repo.real), paths
    assert paths["repos.sub-repo"] == str(repo.real / "sub"), paths


def test_a_manifest_with_no_repos_block_is_unchanged_in_every_resolved_path(repo, serve):
    """Backward compatibility, asserted as byte-equality rather than as a promise.

    A manifest with no `repos` block is one implicit repository at `.` carrying the top-level
    `docs`, so every repository already pointed at this tool keeps resolving to exactly the
    absolute paths it did before the block existed — and the manifest major stays `0.1`.
    """
    for rel in ("docs/epics", "docs/tickets", "docs/reports", "server"):
        repo.mkdir(rel)
    srv = serve(repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "server"}},
        "docs": dict(DOCS),
    }))

    body = manifest_of(srv)
    assert body["factory"] == "0.1", body
    assert body["docs"] == DOCS, body["docs"]
    assert "repos" not in body, f"a repos block was invented for a manifest without one: {body}"
    assert body["workspaces"] == {"core": {"path": "server"}}, body["workspaces"]

    paths = body["resolved"]["paths"]
    assert paths["docs.epics"] == str(repo.real / "docs" / "epics"), paths
    assert paths["docs.tickets"] == str(repo.real / "docs" / "tickets"), paths
    assert paths["docs.reports"] == str(repo.real / "docs" / "reports"), paths
    assert paths["workspaces.core"] == str(repo.real / "server"), paths


def test_declaring_both_repos_and_a_top_level_docs_is_refused(repo, serve):
    """NEGATIVE (mandatory) — two answers to *where are the documents* is a manifest that means
    nothing.

    Watched RED against an implementation that lets `repos` win: ranking them silently is how the
    next reader learns the wrong rule, and the reader here is whoever edits the manifest next.
    """
    m = two_repos()
    m["docs"] = dict(DOCS)
    r = serve(repo.manifest(m)).get("/api/manifest")

    assert r.status_code == 422, (
        f"a manifest carrying both `repos` and a top-level `docs` loaded: {r.status_code} "
        f"{r.text[:400]}")
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["reason"] == "docs_and_repos", err
    assert err["detail"]["pointer"] == "/docs", err


def test_a_manifest_declaring_both_repos_and_top_level_docs_is_refused(repo, serve, tmp_path):
    """SRV-FIX-532 cluster B-NEG (folds `SRV-FIX-448`) — the guard did not become *never emit*.

    Two worlds, in one case, because the fix for *"a `repos` block was invented for a manifest
    without one"* has an obvious wrong shape — stop serialising `repos` at all — and that shape
    passes criterion 1 while deleting a declaration somebody wrote.

      1. A manifest that DOES declare `repos` still publishes both entries, under their declared
         names and with their declared `docs`.
      2. A manifest that declares `repos` AND a top-level `docs` is still `422 docs_and_repos`
         with the JSON pointer that failed. The refusal is a decision about the DOCUMENT and the
         serialiser is downstream of it, so making the block invisible may not reach it.
    """
    declared = two_repos()
    for rel in ("docs/epics", "docs/tickets", "docs/reports",
                "sub/docs/epics", "sub/docs/tickets", "sub/docs/reports",
                "server", "sub/server"):
        repo.mkdir(rel)
    served = serve(repo.manifest(declared))
    body = manifest_of(served)
    assert set(body.get("repos", {})) == set(declared["repos"]), (
        "a manifest that declares repositories must publish them under their declared names — a "
        f"guard that answers criterion 1 by never emitting the block fails here: {body}")
    for name, spec in declared["repos"].items():
        assert body["repos"][name]["docs"]["tickets"] == spec["docs"]["tickets"], body["repos"]
    serve.stop(served)

    both = two_repos()
    both["docs"] = dict(DOCS)
    r = serve(repo.manifest(both)).get("/api/manifest")
    assert r.status_code == 422, (
        f"a manifest carrying both `repos` and a top-level `docs` loaded: {r.status_code} "
        f"{r.text[:400]}")
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["reason"] == "docs_and_repos", err
    assert err["detail"]["pointer"] == "/docs", err


def test_a_workspace_path_is_relative_to_its_repo(repo, serve, tmp_path):
    """`workspaces.<name>.path` is relative to its repo's path, and the lane agrees.

    Three facts, each read from a response rather than recomputed: the path `manifest` resolved,
    the working directory `E19`'s process reports from inside itself, and the directory the
    confinement made writable — proven by the file that process wrote landing there.
    """
    from test_lane import build_repo, git, status_of

    root = build_repo(tmp_path / "two-repo-checkout")
    (root / "server").mkdir(parents=True, exist_ok=True)
    (root / "server" / "keep").write_text("the root repo's workspace\n")
    (root / "sub" / "server").mkdir(parents=True, exist_ok=True)
    (root / "sub" / "server" / "keep").write_text("the sub repo's workspace\n")
    for kind in ("epics", "tickets", "reports"):
        (root / "sub" / "docs" / kind).mkdir(parents=True, exist_ok=True)
    (root / "sub" / "docs" / "tickets" / "SUB-001.md").write_text(
        "---\nid: SUB-001\ntitle: a ticket of the second repository\n"
        "workspace: sub-core\nstatus: todo\n---\n\nbody\n")
    m = json.loads((root / ".factory" / "factory.json").read_text())
    m.pop("docs")
    m["repos"] = {"root-repo": {"path": ".", "docs": dict(DOCS)},
                  "sub-repo": {"path": "sub", "docs": dict(DOCS)}}
    m["workspaces"] = {
        "core": {"repo": "root-repo", "path": "server"},
        "sub-core": {"repo": "sub-repo", "path": "server", "build": "pwd && touch built-here"},
    }
    (root / ".factory" / "factory.json").write_text(json.dumps(m, indent=2))
    git(root, "add", "-A")
    git(root, "commit", "-m", "two repositories in one checkout")
    before = status_of(root)
    srv = serve(root)

    resolved = manifest_of(srv)["resolved"]["paths"]["workspaces.sub-core"]
    assert resolved == str(Path(os.path.realpath(root)) / "sub" / "server"), (
        f"the workspace resolved to {resolved!r}; a `path` relative to its repo's `path` is "
        f"{Path(os.path.realpath(root)) / 'sub' / 'server'}")

    r = srv.post("/api/lanes", json={"ticket_id": "SUB-001", "workspace": "sub-core"})
    assert r.status_code == 201, r.text
    lane = r.json()
    worktree = lane["worktree"]

    # the workspace directory sits at the same repo-relative place inside the lane
    rel = os.path.relpath(resolved, os.path.realpath(root))
    assert rel == "sub/server", rel
    assert (Path(worktree) / rel / "keep").is_file(), (
        f"the lane has no {rel} in it: {sorted(p.name for p in Path(worktree).iterdir())}")

    ws = srv.ws()
    ws.send({"m": "subscribe", "scope": {"lane": lane["lane"]}})
    ws.wait_for("snapshot")
    try:
        started = srv.post("/api/lanes/" + lane["lane"] + "/commands", json={"which": "build"})
        assert started.status_code == 202, started.text
        chunks = []
        while True:
            frame = ws.wait_for("command", timeout=30)
            chunks.append(frame.get("chunk") or "")
            if frame.get("exit_code") is not None:
                assert frame["exit_code"] == 0, ("".join(chunks), frame)
                break
    finally:
        ws.close()

    # E19's working directory, reported by the process itself, and the confinement's writable path
    assert "".join(chunks).strip().splitlines()[0] == worktree, (
        f"E19 ran in {''.join(chunks).strip().splitlines()[0]!r}, the lane is at {worktree!r}")
    assert (Path(worktree) / "built-here").is_file(), (
        "the command's own file did not land in the lane's worktree")

    assert status_of(root) == before, "the main checkout changed"


# ── SRV-FIX-202 · every command a workspace declares is checked the same way ──────────────────
#
# `SRV-FIX-077` proved a workspace's declared TOOLCHAIN is on this machine. `SRV-FIX-191` proved a
# client lane can run its `build`. Neither asked the question one word over: *can a lane run this
# workspace's GATE* — and the answer for `server` and `factory-server` in this repository's own
# manifest is no, because both declare `pytest tests/ -v` and neither declares anything that
# provides `pytest`. Every `factory-server` ticket so far has had its suite run by an agent that
# installed pytest into the lane's tmpfs home by hand, once per lane, undeclared.
#
# The class §3 names is *a prerequisite proven for one of a declaration's commands and assumed for
# the rest*, so the cases below are quantified over the command keys the SCHEMA declares rather
# than over the pair `build`/`gate` somebody typed here.

MANIFEST_SCHEMA_202 = json.loads(
    (SERVER_DIR.parent / "docs/technical/contracts/manifest.schema.json").read_text())

# Keys of a workspace that name a PLACE; every other string key names a COMMAND.
WORKSPACE_PLACES_202 = {"path", "repo"}


def workspace_command_keys_202():
    props = MANIFEST_SCHEMA_202["properties"]["workspaces"]["additionalProperties"]["properties"]
    return sorted(k for k, v in props.items()
                  if v.get("type") == "string" and k not in WORKSPACE_PLACES_202)


# A PATH with nothing on it but the system's own directories — what a systemd unit, a cron
# entry or `env -i` gives a process, and what every case below starts its server with unless
# it is deliberately showing that the server's PATH makes no difference.
PLAIN_PATH_202 = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _standin_agent_202(tmp_path):
    """An agent binary this repository's manifests can declare.

    `claude` is not on PATH inside a lane, and `agent_binary_missing` is checked before anything
    here. A case about a workspace's commands that only runs on a machine with the CLI installed
    is a case that reports its environment.
    """
    d = tmp_path / "agent-202"
    d.mkdir(parents=True, exist_ok=True)
    binary = d / "claude"
    shutil.copy("/bin/sh", binary)
    binary.chmod(0o755)
    return binary


def _outside_the_repository(tmp_path, name):
    """A directory that is NOT inside the repository under test.

    `tmp_path` is the repository the `repo` fixture builds, and the repository is one of the
    three places a lane can execute a file from — its worktree is a checkout of it. A fixture
    that hid a binary inside the repository would be hiding it somewhere the lane has.
    """
    d = tmp_path.parent / f"srv-fix-202-{name}-{tmp_path.name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unreachable_tool(tmp_path, name="srv-fix-202-pytest"):
    """A REAL executable, on this process's PATH, in a directory no lane is given.

    This is `pytest` in `~/.local/bin` exactly: `command -v` finds it for the developer and for
    the server process, and a lane sees `/usr`, its declared toolchain and its worktree and
    nothing else. A fixture that merely omitted the binary would pass against a check that only
    asked "is this word on PATH", which is the check that says yes to every lane's undoing.
    """
    d = _outside_the_repository(tmp_path, "outside-every-lane") / "bin"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return d, p


def _unrunnable_warnings(body):
    """`command_unrunnable` warnings, keyed by the command they name."""
    return {w["pointer"]: w for w in body["warnings"] if w["code"] == "command_unrunnable"}


def test_a_workspace_whose_gate_names_a_binary_no_lane_has_is_reported_at_load(
        repo, serve, tmp_path):
    """THE criterion. Decided at LOAD, naming `/workspaces/<name>/gate` and the word.

    §1: *a lane that wins a resource and cannot run its own declared command is worse than one
    that never starts.* A person fixes a manifest line in five seconds once they are told which
    one; an agent discovers this at GATE, with the lane claimed and the round paid for.

    **Round 1 spelled `told` as `422` and the round failed for it.** On the repository this tool is
    pointed at that answer stopped `GET /api/manifest` and every `POST /api/lanes` — six
    workspaces, none claimable — and the manifest half of the fix is a file no agent may edit. So
    the verdict stays exactly where §1 put it and is delivered as E2's `warnings`;
    `test_the_repository_this_tool_is_pointed_at_loads_and_every_workspace_it_declares_can_be_claimed`
    is the other half of this case and the two are RED against each other's cheap fix.
    """
    outside, prog = _unreachable_tool(tmp_path)
    root = repo.manifest({
        "factory": "0.1",
        "agent": {"command": str(_standin_agent_202(tmp_path))},
        "workspaces": {"core": {
            "path": ".",
            # Resolves under /usr — so the report below is about `gate` and only about `gate`.
            "build": "cd . && python3 -c pass",
            "gate": f"cd . && {prog.name} tests/ -v",
        }},
    })
    srv = serve(root, PATH=f"{outside}:{os.environ['PATH']}")

    r = srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"a workspace whose gate no lane can run took the whole manifest down: "
        f"{r.status_code} {r.text}")
    told = _unrunnable_warnings(r.json())
    assert "/workspaces/core/gate" in told, (
        f"a workspace declaring a gate no lane can run was reported to nobody: {r.text}\n"
        f"{prog} is on the server's own PATH and under neither /usr nor anything this "
        f"workspace declares, which is where `pytest` is.")
    w = told["/workspaces/core/gate"]
    assert w["program"] == prog.name, w
    assert w["field"] == "workspaces.core.toolchain", w
    assert prog.name in w["message"], w
    assert "/workspaces/core/build" not in told, (
        f"a build that resolves under /usr was reported too: {told}")

    # E4 agrees with E2 — the repository is usable, which is the whole of what changed.
    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["manifest"] != "invalid", health.text

    # ── the same manifest gets the same answer from any shell ────────────────────────────────
    # `SRV-FIX-202`'s QA measured round 1 answering 200 or 422 for ONE manifest depending on
    # whether the developer's login shell happened to export the directory holding the program.
    # The domain is what the manifest DECLARES, so a server started without that directory —
    # a systemd unit, a cron entry, a stripped environment — must answer identically.
    stripped = serve(root, PATH=PLAIN_PATH_202)
    other = stripped.get("/api/manifest")
    assert other.status_code == r.status_code, (
        f"the same manifest answered {r.status_code} from a server whose PATH held {prog.name} "
        f"and {other.status_code} from one whose PATH did not. The manifest did not change; the "
        f"shell that started the tool did (SRV-FIX-218).")
    assert _unrunnable_warnings(other.json()) == told, (
        f"the same manifest was reported differently by two servers:\n{told}\n"
        f"{_unrunnable_warnings(other.json())}")


def test_the_check_is_the_same_for_build_and_for_gate(repo, serve, tmp_path):
    """One code path, quantified over the command keys — not two branches that agree today.

    The keys come off `manifest.schema.json`, so a third command declared there is covered the
    day it is declared. Every key must produce the same code, the same reason and the same
    sentence with its own name in it: two refusals that differ in wording are two implementations
    and the next one will be the one nobody wrote.
    """
    keys = workspace_command_keys_202()
    assert len(keys) >= 2, (
        f"the schema declares {keys} as a workspace's commands; a set of one cannot show the "
        f"difference between checking one command and checking all of them")

    outside, prog = _unreachable_tool(tmp_path)
    agent = _standin_agent_202(tmp_path)
    shapes = {}
    for key in keys:
        root = repo.manifest({
            "factory": "0.1",
            "agent": {"command": str(agent)},
            "workspaces": {"core": {"path": ".", key: f"{prog.name} --run"}},
        })
        srv = serve(root, PATH=f"{outside}:{os.environ['PATH']}")
        r = srv.get("/api/manifest")
        assert r.status_code == 200, (
            f"a workspace whose only declared command is {key!r} took the manifest down: "
            f"{r.status_code} {r.text}")
        told = _unrunnable_warnings(r.json())
        assert f"/workspaces/core/{key}" in told, (
            f"a workspace whose only declared command is {key!r} and names a binary no lane has "
            f"was reported to nobody: {r.text}")
        w = told[f"/workspaces/core/{key}"]
        shapes[key] = (r.status_code, w["code"], w["field"], w["program"],
                       w["message"].replace(key, "<key>"))

    assert len(set(shapes.values())) == 1, (
        f"the report differs between a workspace's commands: {shapes}\n"
        f"SRV-FIX-191 proved its prerequisite for `build` and assumed it for `gate`, which is "
        f"this ticket. Two code paths is how that happens again.")


def test_a_workspace_whose_commands_all_resolve_loads(repo, serve, tmp_path):
    """NEGATIVE (mandatory) — the check must still be able to say yes, and say nothing.

    Three arms, one per place a lane can execute a file from, because the cheap fix for
    everything above is a check that reports everything and the cheap fix for the criterion above
    is a check that reports nothing. Watched RED against both.

    Round 2 moved the teeth. When the answer was `422`, *loads cleanly* was the assertion; now
    that a workspace nobody can run loads too, loading proves nothing on its own and every arm
    asserts the manifest carries **no** `command_unrunnable` warning. Every arm also runs from a
    server with a plain `/usr` PATH, so nothing a developer's shell exports can stand in for
    something the manifest was supposed to declare.
    """
    agent = _standin_agent_202(tmp_path)

    # ARM 1 — everything under /usr, which every lane is given read-only.
    root = repo.manifest({
        "factory": "0.1",
        "agent": {"command": str(agent)},
        "workspaces": {"core": {
            "path": ".",
            "build": "cd . && python3 -c pass",
            "gate": "cd . && python3 -m pytest tests/ -v",
        }},
    })
    r = serve(root, PATH=PLAIN_PATH_202).get("/api/manifest")
    assert r.status_code == 200 and not _unrunnable_warnings(r.json()), (
        f"a workspace whose every command is a /usr binary was reported unrunnable: "
        f"{r.status_code} {r.text}")

    # ARM 2 — a binary that exists ONLY under the toolchain this workspace declares: once by
    # absolute path, once as a bare name found through a PATH entry inside that toolchain.
    tc = _outside_the_repository(tmp_path, "declared-toolchain")
    (tc / "bin").mkdir(parents=True, exist_ok=True)
    tool = tc / "bin" / "srv-fix-202-tool"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    root = repo.manifest({
        "factory": "0.1",
        "agent": {"command": str(agent)},
        "workspaces": {"core": {
            "path": ".",
            "toolchain": [str(tc)],
            "build": f"{tool} build",
            "gate": f"cd . && {tool.name} gate",
        }},
    })
    # The server's PATH deliberately does NOT carry the toolchain's `bin`: SRV-FIX-218 §3.2 is
    # that this arm used to supply the very condition it was asserting about, so the declaration
    # alone has to be what makes the bare name resolve.
    r = serve(root, PATH=PLAIN_PATH_202).get("/api/manifest")
    assert r.status_code == 200 and not _unrunnable_warnings(r.json()), (
        f"a workspace whose commands resolve under the toolchain it declares was reported "
        f"unrunnable: {r.status_code} {r.text}\n"
        f"Reporting this is reporting every workspace that builds with something the tool did "
        f"not ship.")

    # ARM 3 — a script in the repository, which is the tree the lane's worktree is a checkout of.
    script = repo.write("scripts/build.sh", "#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    root = repo.manifest({
        "factory": "0.1",
        "agent": {"command": str(agent)},
        "workspaces": {"core": {"path": ".", "build": "cd . && ./scripts/build.sh"}},
    })
    r = serve(root, PATH=PLAIN_PATH_202).get("/api/manifest")
    assert r.status_code == 200 and not _unrunnable_warnings(r.json()), (
        f"a workspace whose command is a script in its own repository was reported unrunnable: "
        f"{r.status_code} {r.text}")

    # ARM 4 — a command carrying shell the check cannot read: an expansion, a glob, and a
    # `find -exec` whose `\;` leaves a word where a program would be. A word this module cannot
    # read is SKIPPED, not refused — the alternative is a manifest refused because the tokeniser
    # was wrong, which is the worst failure this check has and the hardest one to argue with.
    for command in ('cd . && $BUILDER --run',
                    'cd . && ./scripts/*.sh',
                    r'cd . && find . -name "*.o" -exec rm {} \;',
                    'cd . && python3 -c "print(1)" 2>&1 | python3 -c "pass"'):
        root = repo.manifest({
            "factory": "0.1",
            "agent": {"command": str(agent)},
            "workspaces": {"core": {"path": ".", "build": command}},
        })
        r = serve(root, PATH=PLAIN_PATH_202).get("/api/manifest")
        assert r.status_code == 200 and not _unrunnable_warnings(r.json()), (
            f"a command the check cannot read was REPORTED rather than skipped: {command!r} -> "
            f"{r.status_code} {r.text}\n"
            f"A parser that reports what it cannot parse reports working manifests.")


# ── SRV-FIX-202 round 2 — the assertion round 1 lacked ───────────────────────────────────────
#
# Transcribed from `factory/docs/reports/SRV-FIX-202-qa.md` finding F1, whose reproduction is
# `factory/docs/reports/SRV-FIX-202-qa/05-e19-probe.py` driven against a repository carrying this
# repository's own workspace blocks. Round 1 passed four of five criteria and every manifest case
# in this file, and still took the tool down on the repository it is pointed at:
#
#     E2  GET /api/manifest -> HTTP 422 workspace_unrunnable
#         detail.reason=command_unrunnable  detail.pointer=/workspaces/server/gate
#         detail.program=pytest
#     E12 claim server          -> HTTP 409 invalid_manifest
#     E12 claim factory-server  -> HTTP 409 invalid_manifest
#     E12 claim factory         -> HTTP 409 invalid_manifest
#
# Six workspaces declared, zero exercised. Nothing in this suite said no, because every manifest
# case above builds its own fixture manifest out of shapes — so a check that refuses the REAL
# declarations leaves the suite entirely green. That is what this case exists to stop.


def _the_manifest_this_tool_is_pointed_at():
    """This repository's own `.factory/factory.json`, read off disk, never a copy."""
    return json.loads(
        (Path(os.path.realpath(SERVER_DIR.parents[0])) / ".factory" / "factory.json").read_text())


def _repository_carrying_this_repositorys_declarations(tmp_path, home):
    """A git repository whose manifest is this repository's declarations, and the deviations.

    Two are uexampleidable and both are RECORDED rather than quietly taken, because the ticket this
    case belongs to failed QA for discharging a criterion about the real manifest against a
    fixture that merely resembled it:

    · `resources` is dropped. This repository declares `/dev/ttyUSB1`, the loader refuses a
      device that is not there, and a lane has no board — so keeping it would make every run of
      this case an assertion about the machine rather than about the manifest.
    · a toolchain entry naming an ABSOLUTE path that does not exist on the machine running this
      case (`/opt/google/chrome`, `/run/dbus/system_bus_socket` on a CI box or inside a lane) is
      stood in for by an empty directory, because `toolchain_not_found` is a different check and
      is not this ticket's. `~/` entries are created under the fixture's own HOME for the same
      reason. **No command is altered**, and the commands are the whole of what is under test.
    """
    from test_lane import git

    real = _the_manifest_this_tool_is_pointed_at()
    root = tmp_path / "as-declared"
    elsewhere = tmp_path / "absent-on-this-machine"
    stood_in = []

    def toolchain_entry(entry):
        """Make the declared path exist, as the KIND it really is.

        `~/.gitconfig` is a file and `~/.config/gh` is a directory, and a fixture that made both
        directories gave git `unable to access '.../.gitconfig': Is a directory` and every lane
        `not a git repository` — a fixture defect that looks exactly like a product one.
        """
        if entry.startswith("~/"):
            target, model = home / entry[2:], Path.home() / entry[2:]
        elif Path(entry).exists():
            return entry
        else:
            target, model = elsewhere / entry.lstrip("/"), Path(entry)
            stood_in.append(entry)
        target.parent.mkdir(parents=True, exist_ok=True)
        if model.is_file():
            target.write_text("")
        else:
            target.mkdir(parents=True, exist_ok=True)
        return entry if entry.startswith("~/") else str(target)

    workspaces = {}
    for name, block in real["workspaces"].items():
        w = dict(block)
        if "toolchain" in w:
            w["toolchain"] = [toolchain_entry(t) for t in w["toolchain"]]
        workspaces[name] = w

    manifest = {
        "factory": real["factory"],
        "workspaces": workspaces,
        "lanes": real["lanes"],
        "stages": real["stages"],
        "status": real["status"],
        "display": real["display"],
        # The stand-in agent, for the reason `_standin_agent_202` gives: `claude` is not on PATH
        # inside a lane and `agent_binary_missing` is checked before any of this.
        "agent": {"command": str(_standin_agent_202(tmp_path))},
    }

    # Every path the declarations name has to exist in the checkout a lane is cut from — a
    # workspace's own `path`, and each repo's three document directories.
    (root / ".factory").mkdir(parents=True, exist_ok=True)
    (root / ".factory" / "factory.json").write_text(json.dumps(manifest, indent=2))
    (root / ".gitignore").write_text(".factory/worktrees/\n.factory/runs/\n.factory/ids.json\n")
    # A manifest with no `repos` block is one implicit repository at `.` carrying the top-level
    # `docs` — which is the shape THIS repository's manifest has. Reading `real["repos"]` was
    # reading the repository this one is cloned INTO (`SERVER_DIR.parents[1]`), so the fixture
    # stood in for somebody else's declarations.
    if "repos" in real:
        manifest["repos"] = real["repos"]
        declared_repos = real["repos"]
    else:
        manifest["docs"] = real["docs"]
        declared_repos = {real.get("name", "."): {"path": ".", "docs": real["docs"]}}

    tickets = {}
    for repo_name, repo_block in declared_repos.items():
        base = root / repo_block["path"]
        for key, rel in repo_block.get("docs", {}).items():
            (base / rel).mkdir(parents=True, exist_ok=True)
            (base / rel / ".keep").write_text("")
    real_root = Path(os.path.realpath(SERVER_DIR.parents[0]))
    for name, block in real["workspaces"].items():
        rel = Path(declared_repos[block["repo"]]["path"]
                   if block.get("repo") in declared_repos else ".") / block["path"]
        base = root / rel
        base.mkdir(parents=True, exist_ok=True)
        (base / "README.md").write_text(f"the {name} workspace\n")
        # A script this repository REALLY carries is carried here too, and one it does not is
        # not invented. `firmware/master`'s `build.sh` exists and its `agent-build.sh` does not
        # — the second is a genuine find of this ticket's, and a fixture that stubbed both would
        # be asserting against a repository that does not exist.
        for key in ("build", "gate"):
            for word in re.findall(r"\./[\w./-]+", block.get(key, "")):
                if (real_root / rel / word).exists():
                    stub = base / word
                    stub.parent.mkdir(parents=True, exist_ok=True)
                    stub.write_text("#!/bin/sh\nexit 0\n")
                    stub.chmod(0o755)
        ticket_id = "QA202-" + name.upper().replace("-", "")
        tickets[name] = ticket_id
        owning = declared_repos.get(block.get("repo"),
                                    next(iter(declared_repos.values())))
        docs = owning.get("docs", {}).get("tickets")
        if docs:
            (root / owning["path"] / docs / f"{ticket_id}.md").write_text(
                f"---\nid: {ticket_id}\ntitle: claim probe for {name}\n"
                f"workspace: {name}\nstatus: todo\n---\n\nbody\n")

    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "factory@test.local")
    git(root, "config", "user.name", "factory tests")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root, tickets, stood_in


def test_the_repository_this_tool_is_pointed_at_loads_and_every_workspace_it_declares_can_be_claimed(
        tmp_path, serve):
    """QA's owed assertion, in QA's words, against QA's fixture state.

    *The repository this tool is pointed at loads, and a lane can be claimed for every workspace
    it declares.*

    The fixture state that decides it is the one F1 names: a manifest carrying this repository's
    own workspace blocks — `"gate": "cd server && pytest tests/ -v"` with a `toolchain` of
    `~/.gitconfig` and `~/.config/gh` — and **a server process whose PATH does not contain a
    directory that holds `pytest`**. That is the developer's own machine: `pytest` is a `--user`
    install in `~/.local/bin`, which is on the developer's PATH and inside nothing a lane is
    given.

    A load-time check may say a command cannot run. It may not say it in a way that stops the
    repository being read at all: `GET /api/manifest -> 422` makes every lane unclaimable,
    including a lane for a workspace that declares no commands and could not have offended.
    """
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    root, tickets, stood_in = _repository_carrying_this_repositorys_declarations(tmp_path, home)
    declared = list(_the_manifest_this_tool_is_pointed_at()["workspaces"])

    srv = serve(root, HOME=str(home), PATH=PLAIN_PATH_202, FACTORY_TEST_HOOKS="1",
                FACTORY_AGENT_CONFIG_ROOT=str(tmp_path / "agent-homes"))

    r = srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"the repository this tool is pointed at did not load: {r.status_code} {r.text}\n"
        f"Its declarations are byte-identical to `.factory/factory.json` apart from the two "
        f"deviations `_repository_carrying_this_repositorys_declarations` records "
        f"(stood-in toolchain paths: {stood_in}). A tool that refuses the repository it is "
        f"pointed at is a tool nobody can start a lane with, and the manifest is the human's "
        f"file — no agent may edit it to make the tool load again.")

    body = r.json()
    served = list(body["workspaces"])
    assert served == declared, (
        f"the manifest loaded but does not carry every workspace it declares: "
        f"served {served}, declared {declared}")

    # ...and loading is not the same as saying nothing. The other cheap fix for this case is to
    # delete the check, so the verdict §1 asked for has to still be here — at LOAD, naming the
    # command and the word — with `pytest` for `server`'s gate the one this repository is about.
    told = _unrunnable_warnings(body)
    # DERIVED, not typed: which workspace declares a gate whose first word is `pytest` is a fact
    # about this repository's manifest, and the name of that workspace is this repository's to
    # choose. Typing `server` here read the repository this one is cloned INTO.
    real = _the_manifest_this_tool_is_pointed_at()
    gating = [name for name, block in real["workspaces"].items()
              if "pytest" in (block.get("gate") or "")]
    assert gating, (
        f"no workspace in this repository declares a gate run with `pytest`, so this case is "
        f"about a command nobody runs: { {k: v.get('gate') for k, v in real['workspaces'].items()} }")
    for name in gating:
        pointer = f"/workspaces/{name}/gate"
        assert pointer in told, (
            f"the repository loaded and nobody was told its `{name}` gate cannot run: "
            f"{json.dumps(body['warnings'], indent=2)}\n"
            f"Loading a manifest whose gate no lane can run and REPORTING it are the two halves "
            f"of this; a check that only does the first is the `warns about nothing` cheap fix.")
        said = told[pointer]
        assert said["program"] == "pytest", said
        assert said["field"] == f"workspaces.{name}.toolchain", said
        assert "pytest" in said["message"] and "toolchain" in said["message"], said

    for name in declared:
        # `requests` directly, not `srv.post`: a claim cuts a worktree and probes a sandbox,
        # and the client helper's five-second default is a timeout about the machine.
        claimed = requests.post(srv.base + "/api/lanes",
                                json={"ticket_id": tickets[name], "workspace": name}, timeout=300)
        assert claimed.status_code == 201, (
            f"no lane could be claimed for workspace {name!r}: "
            f"{claimed.status_code} {claimed.text}\n"
            f"Six workspaces are declared; a round that refuses one of them exercises five. "
            f"A workspace whose declared command cannot run is a fact to REPORT at load, not a "
            f"reason to refuse the lane that would have been told about it.")
        assert claimed.json()["workspace"] == name, claimed.text
        lane = claimed.json()["lane"]
        released = requests.delete(srv.base + f"/api/lanes/{lane}?force=true", timeout=300)
        assert released.status_code in (200, 204), (
            f"the lane claimed for {name!r} could not be released: "
            f"{released.status_code} {released.text}")


# ── SRV-FEAT-065 · this repository declares the checker it wants started ══════════════════════
#
# F31 is a mechanism with no user. `test_no_roles_block_changes_nothing` has been the only case
# exercising it since SRV-FEAT-022, because this repository — the only one the tool is pointed at
# — declared no roles, so every QA session it has ever run was a detached headless process started
# from a shell: no lane, no run record, no transcript, no cost and no Stop. The declaration is the
# whole of what was missing; the code has been there for months.

def test_this_repositorys_manifest_declares_the_qa_role(serve, tmp_path):
    """§1's criterion 4: the declaration is checked in, `roles.qa.prompt_file` resolves to a file
    that exists, and the manifest LOADS CLEAN with it.

    Loading is the half that matters and the half a JSON assertion cannot reach: `E2` refuses a
    role whose `prompt_file` is not there *at load*, naming the JSON pointer — so a declaration
    that is merely well-formed can still be a server that will not start. This drives the real
    loader over this repository's real manifest.
    """
    root = Path(os.path.realpath(SERVER_DIR.parents[0]))
    manifest = json.loads((root / ".factory" / "factory.json").read_text())

    roles = manifest.get("roles", {})
    assert "qa" in roles, (
        "this repository declares no `qa` role, so every QA session it runs is a process the tool "
        f"never started and cannot show, stop or release. roles = {roles!r}")

    qa = roles["qa"]
    assert qa["lane"] == "new", qa
    assert qa["start_on_stage"] == "QA", (
        "the role starts on the stage the implementing session reports when it hands over. "
        f"§1's own end-to-end says GATE; see the report for why this round moved it: {qa!r}")

    prompt = root / qa["prompt_file"]
    assert prompt.is_file(), f"{prompt} is not there, and E2 refuses this manifest at load"

    # ...and it is the repository's own words that reach the checker, not the tool's.
    assert "{ticket_path}" in prompt.read_text(), prompt.read_text()[:400]

    # This checks the real role declaration and prompt, not a developer's installed toolchain
    # or login. Keep the declaration intact while supplying empty, local loader prerequisites.
    here = tmp_path / "repository-role"
    (here / ".factory").mkdir(parents=True)
    home = tmp_path / "role-home"
    home.mkdir()
    binaries = tmp_path / "role-binaries"
    binaries.mkdir()
    toolchain = tmp_path / "role-toolchain"
    toolchain.mkdir()
    prepared = json.loads(json.dumps(manifest))
    for workspace in prepared["workspaces"].values():
        (here / workspace["path"]).mkdir(parents=True, exist_ok=True)
        if "toolchain" in workspace:
            workspace["toolchain"] = [str(toolchain)]
    restored = json.loads(json.dumps(prepared))
    for name, workspace in manifest["workspaces"].items():
        if "toolchain" in workspace:
            restored["workspaces"][name]["toolchain"] = workspace["toolchain"]
    assert restored == manifest, "only temporary toolchain paths may differ"

    profiles = list(manifest.get("agents", {}).values())
    if "agent" in manifest:
        profiles.append(manifest["agent"])
    assert profiles, "the repository declares no agent profile"
    prompts = {role["prompt_file"] for role in roles.values()}
    for profile in profiles:
        command = profile["command"].split()[0]
        assert "/" not in command, "the fixture controls command resolution through PATH"
        binary = binaries / command
        binary.write_text("#!/bin/sh\nexit 97\n")
        binary.chmod(0o755)
        if "credentials_dir" in profile:
            credentials = profile["credentials_dir"]
            assert credentials.startswith("~/"), credentials
            (home / credentials[2:]).mkdir(parents=True, exist_ok=True)
        if "start_prompt_file" in profile:
            prompts.add(profile["start_prompt_file"])
    for declared in prompts:
        source = root / declared
        target = here / declared
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        assert target.read_bytes() == source.read_bytes()
    for declared in manifest["docs"].values():
        source = root / declared
        target = here / declared
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    (here / ".factory/factory.json").write_text(json.dumps(prepared))

    srv = serve(here, HOME=str(home), PATH=f"{binaries}:/usr/bin:/bin",
                CLAUDE_CONFIG_DIR=None, CODEX_HOME=None, FACTORY_LANE_HOME=None,
                FACTORY_TEST_HOOKS="1")
    body = manifest_of(srv)
    assert body["roles"]["qa"]["start_on_stage"] == "QA", body["roles"]
    assert body["roles"]["qa"]["prompt_file"] == qa["prompt_file"], body["roles"]
    assert not any(path.is_file() for path in home.rglob("*")), "no authentication was furnished"


# ── SRV-FEAT-304 · a document tree the manifest does not declare is not a surface ────────────
#
# `SPEC.md` §4.15 and §4.16 add two surfaces over two document trees — a wiki and an escape
# ledger — and `S1` allows exactly one door for both: *"everything repository-specific enters
# through `.factory/factory.json`."* Two modules below `manifest` naming a path is the boundary
# gone, and a DEFAULT is that with the naming done at compile time: a tool that falls back to
# `docs/wiki` has learned a directory layout it was never told.
#
# So `docs.wiki` and `docs.escapes` are optional keys, and the two halves below are the two ways
# a reader can be wrong about where they came from.

def _docs_paths(body):
    """Everything in `E2`'s answer that could carry a wiki or an escapes path."""
    return {**body.get("docs", {}), **body.get("resolved", {}).get("paths", {})}


def test_a_surface_whose_document_path_is_undeclared_is_not_served(repo, serve):
    """MANDATORY NEGATIVE — the surface exists because the manifest says so, and for no other reason.

    Two manifests, and they DISAGREE about the two values a reader could take them from. The
    declared one writes `handbook` and `DEFECTS.md`, which are nothing like `docs/wiki` and
    `ESCAPES.md`: a tool reading a default and a tool reading the manifest cannot both be right
    about that repository, so the assertion names the value that must be read and the value that
    must not. A fixture writing the conventional names would pass identically either way, which
    is `SRV-FIX-184`'s blindness in the shape it takes here.
    """
    repo.mkdir("handbook")
    repo.write("DEFECTS.md", "# defects\n")
    root = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports",
                 "wiki": "handbook", "escapes": "DEFECTS.md"},
    })
    srv = serve(root)
    declared = manifest_of(srv)

    # 1 · the door is open: a repository that declares them is not told they are unknown keys.
    unknown = [w for w in declared.get("warnings", [])
               if w["code"] == "unknown_key" and w.get("pointer", "").startswith("/docs/")]
    assert unknown == [], (
        f"a manifest declaring `docs.wiki` and `docs.escapes` was told they are unknown keys: "
        f"{unknown}. Everything repository-specific enters through the manifest, and a key the "
        f"schema does not declare is a surface no repository can ask for.")

    # 2 · and nothing was invented for it: the tool did not answer with the conventional names.
    served = json.dumps(declared)
    assert "docs/wiki" not in served and "ESCAPES.md" not in served, (
        f"the tool answered with a path this manifest never wrote, so what reached the client "
        f"came from a default and not from the declaration: {served}")

    # ── the negative: declare neither, and there is no wiki and no ledger anywhere ────────────
    bare = repo.manifest({
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
    })
    other = serve(bare)
    body = manifest_of(other)

    invented = {k: v for k, v in _docs_paths(body).items() if "wiki" in k or "escapes" in k}
    assert invented == {}, (
        f"a manifest declaring no `docs.wiki` and no `docs.escapes` was resolved to {invented}. "
        f"A path read from a default is a repository-specific fact the tool taught itself.")
    assert "wiki" not in json.dumps(body).lower(), (
        f"the word reached the answer for a repository that never wrote it: {json.dumps(body)}")

    # ── and the default itself, which is the clause above's only real hazard ─────────────────
    #
    # Watching this negative fail showed it could not: `store.cpp` reads `epics`, `tickets` and
    # `reports` and nothing else, so a `"default"` written into the schema for `wiki` reaches no
    # answer and the two assertions above stay green over it. A negative that cannot go red on
    # the regression it names is documentation with an `assert` in front of it (§2).
    #
    # The schema IS the arbiter here — the server validates every manifest against this file
    # (`FACTORY_SCHEMA_PATH`), so it is where the decision lives until `E27` exists to read it.
    # The fixture is the one object with members that DISAGREE: three keys that default because
    # every repository has them, and two that must not because a repository may simply not.
    schema = json.loads((SERVER_DIR.parent / "docs/technical/contracts/manifest.schema.json").read_text())
    for where in (schema["properties"]["docs"],
                  schema["properties"]["repos"]["additionalProperties"]["properties"]["docs"]):
        props = where["properties"]
        defaulted = sorted(k for k, v in props.items() if "default" in v)
        assert defaulted == ["epics", "reports", "tickets"], (
            f"`docs` defaults {defaulted}. `wiki` and `escapes` may not carry one: a default is "
            f"the tool naming a directory nobody declared, and it is the whole of what the two "
            f"assertions above are protecting.")

    # ...and the refusal a reader meets names the key to add, rather than a directory to create.
    spec = (SERVER_DIR.parent / "docs/product/SPEC.md").read_text()
    for number, key in (("### 4.15", "docs.wiki"), ("### 4.16", "docs.escapes")):
        block = spec[spec.index(number):]
        block = block[:block.index("\n### ") if "\n### " in block[1:] else len(block)]
        assert f"`{key}`" in block, (
            f"SPEC.md §{number.split()[1]} does not name `{key}`, so a repository with no such "
            f"surface has nothing to read that says which line would give it one:\n{block[:600]}")


# ── SRV-FEAT-500 · `review` joins the ONE set checked at load ─────────────────────────────────

def test_review_is_checked_at_load_with_build_and_gate(repo, serve, tmp_path):
    """Criterion 1 — a third declared command, checked by the code that already checks the two.

    `SRV-FIX-191`/`SRV-FIX-195`, 2026-08-22: *the toolchain that builds is not the toolchain that
    gates*. A third command that is checked by a third branch is that row a third time, so the
    thing asserted here is not that the two answers agree today — it is that there is **one
    emission site** for `command_unrunnable` in the whole of `app/`, and that the schema's command
    keys are the set the loader walks.

    Three arms:
      1. a workspace declaring a `review` that resolves loads, `E2` echoes the string, and the
         manifest carries no warning about it — the check must still be able to say yes;
      2. a workspace declaring a `review` naming a program no lane is given warns
         `command_unrunnable` at `/workspaces/<name>/review`, and the manifest still LOADS;
      3. that warning and a bad `gate`'s, produced in ONE load of ONE manifest, are the same
         object with their own key in it — and `app/` has exactly one place that can emit either.
    """
    keys = workspace_command_keys_202()
    assert "review" in keys, (
        f"manifest.schema.json declares {keys} as a workspace's commands and `review` is not one "
        f"of them, so nothing below sweeps the command this ticket exists for")

    agent = _standin_agent_202(tmp_path)

    # ── one emission site, asserted against the source ───────────────────────────────────────
    # An EMISSION site: the code opening a warning object with this code. A comparison — the
    # renderer asking `w.code == "command_unrunnable"` — is a READ of the code and counting it
    # would put a site in the list that cannot produce a warning at all (`SRV-FIX-185`'s shape,
    # one size down).
    sites = []
    for path in sorted((SERVER_DIR / "app").rglob("*.cpp")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r'\{\s*"command_unrunnable"\s*,', line):
                sites.append(f"{path.relative_to(SERVER_DIR)}:{n}")
    assert len(sites) == 1, (
        f"`command_unrunnable` is emitted from {len(sites)} places: {sites}\n"
        f"Criterion 1 asks for one function and not two. A second site is a second "
        f"implementation, and the third command is the one it will disagree about.")

    # ── ARM 1 · a review that resolves says nothing ──────────────────────────────────────────
    declared = "sh ./review.sh"
    root = repo.manifest({
        "factory": "0.1",
        "agent": {"command": str(agent)},
        "workspaces": {"core": {"path": ".", "review": declared}},
    })
    srv = serve(root, PATH=PLAIN_PATH_202)
    r = srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"a workspace declaring a runnable review did not load: {r.status_code} {r.text}")
    echoed = r.json()["workspaces"]["core"]
    assert echoed.get("review") == declared, (
        f"E2 does not echo the review the manifest declares, so nothing on a screen can say the "
        f"repository has one: {echoed}")
    assert not _unrunnable_warnings(r.json()), (
        f"a review whose program is `sh` — which every lane is given under /usr — was reported "
        f"unrunnable: {r.json()['warnings']}")

    # ── ARM 2 and 3 · a bad review and a bad gate, in one manifest, one load ──────────────────
    outside, prog = _unreachable_tool(tmp_path, name="srv-feat-500-tool")
    root = repo.manifest({
        "factory": "0.1",
        "agent": {"command": str(agent)},
        "workspaces": {"core": {
            "path": ".",
            "build": "cd . && python3 -c pass",
            "gate": f"{prog.name} tests/ -v",
            "review": f"{prog.name} --review",
        }},
    })
    srv = serve(root, PATH=f"{outside}:{os.environ['PATH']}")
    r = srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"a workspace whose review no lane can run took the whole manifest down: "
        f"{r.status_code} {r.text}")
    told = _unrunnable_warnings(r.json())
    assert "/workspaces/core/review" in told, (
        f"a workspace declaring a review no lane can run was reported to nobody: {r.text}\n"
        f"{prog} is on the server's own PATH and under neither /usr nor anything this workspace "
        f"declares.")
    assert "/workspaces/core/gate" in told, told
    assert "/workspaces/core/build" not in told, (
        f"a build that resolves under /usr was reported too: {told}")

    shapes = {}
    for key in ("review", "gate"):
        w = told[f"/workspaces/core/{key}"]
        assert w["field"] == "workspaces.core.toolchain", w
        assert w["program"] == prog.name, w
        assert f"workspaces.core.{key}" in w["message"] or key in w["message"], w
        # The declared STRING is normalised before the key is, or `--review` becomes `--<key>`
        # and the two messages differ in a substitution this case made rather than in anything
        # the server wrote.
        normalised = (w["message"].replace(f"{prog.name} --review", "<cmd>")
                                  .replace(f"{prog.name} tests/ -v", "<cmd>")
                                  .replace(key, "<key>"))
        shapes[key] = (w["code"], w["field"], w["program"], normalised)
    assert len(set(shapes.values())) == 1, (
        f"the load-time report differs between `review` and `gate`: {shapes}\n"
        f"SRV-FIX-191 proved its prerequisite for `build` and assumed it for `gate`; a review "
        f"checked by its own branch is that ticket a third time.")

    # The repository is still usable — a warning, not a refusal (SRV-FIX-202 round 2).
    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["manifest"] != "invalid", health.text


# ── SRV-FEAT-499 · the block that says which line of a ticket is evidence, and after which
#    stage it stops being editable ───────────────────────────────────────────────────────────

EVIDENCE_STAGES = ["SPEC", "TEST", "IMPL", "GATE"]


def _evidence_repo(where, evidence=None, stages=EVIDENCE_STAGES):
    """A manifest at `where`, whose leaf directory name is always the same.

    The name matters: it is the repository's name in `E2`'s body, and the third assertion below
    compares two bodies BYTE FOR BYTE. Two fixtures under different leaf names would differ in
    four places that have nothing to do with this ticket, which is exactly the kind of noise that
    turns a strict comparison into a loose one.
    """
    root = Path(where) / "repo"
    (root / ".factory").mkdir(parents=True, exist_ok=True)
    body = {
        "factory": "0.1",
        "workspaces": {"core": {"path": "."}},
        "docs": {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"},
    }
    if stages is not None:
        body["stages"] = stages
    if evidence is not None:
        body["evidence"] = evidence
    (root / ".factory" / "factory.json").write_text(json.dumps(body, indent=2))
    return root


def test_evidence_freeze_is_declared_or_absent(serve, tmp_path):
    """A repository says which line of §1 names its evidence and after which stage that evidence
    stops being editable — or it says nothing and the tool behaves exactly as it did before.

    Three states, and the middle one is the point: a `frozen_after` naming a stage the manifest's
    own sequence does not contain is a freeze that can never fire, and a freeze that can never
    fire is indistinguishable from the feature being switched off. It is refused AT LOAD, naming
    the pointer, for the reason every other unsatisfiable manifest line is.

    The third state is asserted as a DIFFERENCE rather than as a shape: the body a manifest with
    no `evidence` block produces is the body the declaring one produces with that one member
    removed, byte for byte. A repository that never heard of this ticket cannot tell it happened.
    """
    declared = _evidence_repo(tmp_path / "declaring",
                              {"line_prefix": "Evidence:", "frozen_after": "TEST"})
    srv = serve(declared)

    r = srv.get("/api/manifest")
    assert r.status_code == 200, r.text
    assert r.json().get("evidence") == {"line_prefix": "Evidence:", "frozen_after": "TEST"}, (
        f"E2 does not echo the block the repository declared: {r.text}")
    declaring_body = r.text

    # ── a stage no sequence contains ─────────────────────────────────────────────────────────
    ship = _evidence_repo(tmp_path / "ship", {"line_prefix": "Evidence:", "frozen_after": "SHIP"})
    bad = serve(ship)
    r = bad.get("/api/manifest")
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_manifest", err
    assert err["detail"]["pointer"] == "/evidence/frozen_after", (
        f"the refusal does not name the member that cannot be satisfied: {err}")
    assert "SHIP" in err["message"], err["message"]

    # ── and a repository that declares nothing ───────────────────────────────────────────────
    bare = _evidence_repo(tmp_path / "bare")
    plain = serve(bare)
    r = plain.get("/api/manifest")
    assert r.status_code == 200, r.text
    assert "evidence" not in r.json(), (
        f"a manifest with no `evidence` block was answered with one: {r.text}")

    def normalise(text, root):
        return text.replace(str(Path(os.path.realpath(root))), "<root>")

    with_block = normalise(declaring_body, declared)
    without = normalise(r.text, bare)
    stripped = with_block.replace(
        ',"evidence":{"line_prefix":"Evidence:","frozen_after":"TEST"}', "")
    assert stripped == without, (
        "removing the `evidence` member from the declaring repository's answer does not give the "
        "answer a repository that declares none gets, so this ticket changed a body it was not "
        f"asked to change:\n  declaring: {stripped}\n  bare:      {without}")


# ── SRV-FIX-532 cluster C1 — a lane holds no forge credential ─────────────────────────────────
#
# `SRV-FIX-438`. A toolchain entry is READ-BOUND into every lane for its workspace, and a lane that
# can reach the forge can push, delete a branch or open a pull request as the operator. The denied
# set lives in ONE named table in `app/manifest/store.cpp`; the case below DERIVES its members by
# parsing that table out of the source, because a test that retypes the list tests the list.


def denied_credential_directories():
    """Every member of `CREDENTIAL_DIRECTORIES`, parsed out of `app/manifest/store.cpp`.

    The technique `test_spec.error_codes` already uses on `app/`: the product's own table is the
    corpus, so a member added there is a member this sweep drives without anybody remembering to
    come back and add it here.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "app" / "manifest" / "store.cpp").read_text()
    block = re.search(r"CREDENTIAL_DIRECTORIES\[\]\s*=\s*\{(.*?)\};", source, re.S)
    assert block, "the credential table is not in app/manifest/store.cpp under that name"
    members = re.findall(r'"([^"]+)"', block.group(1))
    assert len(members) > 3, f"the table parsed as {members}, which is not a table"
    return members


def test_a_toolchain_naming_a_credential_directory_is_refused(repo, serve, tmp_path):
    """§1 C1 — every member of the denied set, and a path UNDER a member, is refused by name.

    `SRV-FIX-438` criterion 1. Containment in both directions, asked of the canonical path —
    `SRV-FIX-078`'s rule, and the reason that ticket exists: it guarded against binding a whole
    home and asserted the status code for the two spellings its author had in mind, and `/home` was
    neither of them.

    The sweep is the product's own table plus one path under each member, and it is asserted
    non-empty before anything is concluded from it.
    """
    members = denied_credential_directories()
    home = tmp_path / "home"
    probes = {}
    for named in members:
        rel = named[2:] if named.startswith("~/") else named.lstrip("/")
        probes[named] = home / rel
        probes[named + " (a path under it)"] = home / rel / "inner"
    assert len(probes) >= 8, f"the sweep is thin: {sorted(probes)}"

    for what, path in sorted(probes.items()):
        path.mkdir(parents=True, exist_ok=True)
        root = repo.manifest(_with_toolchain([str(path)]))
        srv = serve(root, HOME=str(home))
        r = srv.get("/api/manifest")
        assert r.status_code == 422, (
            f"a toolchain naming {what} loaded: {r.status_code} {r.text[:300]}")
        err = r.json()["error"]
        assert err["code"] == "invalid_manifest", (what, err)
        assert err["detail"]["reason"] == "toolchain_holds_a_credential", (what, err)
        assert err["detail"]["pointer"] == "/workspaces/core/toolchain/0", (what, err)
        serve.stop(srv)


@pytest.mark.repository
def test_this_repositorys_manifest_declares_no_credential_directory():
    """§1 C1 — the fix reaches the FILE that was exposed, not only the guard.

    `SRV-FIX-438` criterion 5. Every toolchain entry in this repository's own manifest is checked
    against the product's own table, so the day somebody puts `~/.config/gh` back this goes red
    rather than a lane quietly gaining a push credential.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / ".factory" / "factory.json").read_text())
    members = denied_credential_directories()

    declared = {name: spec.get("toolchain", [])
                for name, spec in manifest["workspaces"].items()}
    assert any(declared.values()), (
        f"no workspace in this manifest declares a toolchain, so this case swept nothing: "
        f"{declared}")

    holding = []
    for name, entries in sorted(declared.items()):
        for entry in entries:
            for member in members:
                if entry == member or entry.startswith(member.rstrip("/") + "/") \
                        or member.startswith(entry.rstrip("/") + "/"):
                    holding.append(f"{name}: {entry!r} is, is under, or contains {member!r}")
    assert not holding, (
        "this repository's own manifest hands a credential to every lane for these workspaces:\n  "
        + "\n  ".join(holding))


# ── SRV-FIX-532 cluster E1 — one workspace's missing toolchain is not the manifest's failure ──
#
# `SRV-FIX-491`. A `toolchain` entry that is not on this machine invalidated the WHOLE manifest, so
# a `factory-server` lane — which is given `~/.gitconfig` and `~/.local/bin` and not `~/.nvm` —
# could not point an instance at the repository it is a lane OF: every surface answered `409` and
# tier 3 was unreachable from the place every ticket is worked (`SRV-FIX-533`). `E12` already asks
# *can this workspace build* at claim time; it keeps asking it, and becomes the only place that
# refuses.


def _two_workspaces(missing, present):
    return {
        "factory": "0.1",
        "workspaces": {
            "core": {"path": ".", "toolchain": [str(present)]},
            "web": {"path": ".", "toolchain": [str(missing)]},
        },
        "docs": dict(DOCS),
    }


def test_an_unbuildable_workspace_warns_and_does_not_refuse_the_manifest(repo, serve, tmp_path):
    """§1 E1 — the manifest loads, and says which workspace cannot build and why.

    `SRV-FIX-491` criterion 1. Two workspaces, one of whose toolchains is not on this machine:
    `GET /api/manifest` is `200`, the warning names the workspace, the declared entry, the path it
    resolved to and the JSON pointer, and the document surfaces answer `200` rather than being
    dead behind a `409`.
    """
    for rel in ("docs/epics", "docs/tickets", "docs/reports"):
        repo.mkdir(rel)
    present = tmp_path / "a-toolchain-that-is-here"
    present.mkdir()
    missing = tmp_path / "a-toolchain-that-is-not"
    assert not missing.exists(), missing

    srv = serve(repo.manifest(_two_workspaces(missing, present)))
    body = manifest_of(srv)
    assert body["factory"] == "0.1", body

    warnings = [w for w in body.get("warnings", []) if w.get("code") == "workspace_unbuildable"]
    assert len(warnings) == 1, (
        f"one workspace is unbuildable and the manifest reports {len(warnings)} warning(s) about "
        f"it: {body.get('warnings')}")
    said = warnings[0]
    assert said["field"] == "web", said
    assert said["pointer"] == "/workspaces/web/toolchain/0", said
    assert str(missing) in json.dumps(said), said

    for surface in ("/api/requirements?filter=all", "/api/tickets"):
        r = srv.get(surface)
        assert r.status_code == 200, (
            f"{surface} is dead because ANOTHER workspace cannot build: {r.status_code} {r.text}")


def test_a_structurally_invalid_manifest_is_still_refused(repo, serve, tmp_path):
    """§1 E-NEG — *this workspace cannot build* is not *this document is not a manifest*.

    `SRV-FIX-491` criterion 3, mandatory negative. Without it, "load it anyway" is a diff that
    makes every manifest valid. Three members, each a different structural failure, and each still
    answers with its own reason and pointer:

      * a major this build does not support;
      * a workspace whose `path` is empty;
      * a `toolchain` entry that is neither absolute nor under `~/`.
    """
    for rel in ("docs/epics", "docs/tickets", "docs/reports"):
        repo.mkdir(rel)
    present = tmp_path / "here"
    present.mkdir()

    members = {
        "a major this build does not support":
            ({"factory": "9.0", "workspaces": {"core": {"path": "."}}}, 409),
        "a workspace whose path is empty":
            ({"factory": "0.1", "workspaces": {"core": {"path": ""}}}, 422),
        "a toolchain that is neither absolute nor under ~/":
            ({"factory": "0.1", "workspaces": {"core": {"path": ".", "toolchain": ["bin"]}}}, 422),
    }
    assert len(members) >= 3, members

    for what, (manifest, status) in sorted(members.items()):
        srv = serve(repo.manifest(manifest))
        r = srv.get("/api/manifest")
        assert r.status_code == status, (
            f"{what} answered {r.status_code}, not {status} — a missing toolchain becoming a "
            f"warning may not make a malformed document load: {r.text[:300]}")
        assert r.json()["error"]["code"].startswith(("invalid_manifest",
                                                     "unsupported_manifest")), (what, r.text)
        serve.stop(srv)


@pytest.mark.repository
def test_a_lane_can_serve_the_repository_it_is_a_lane_of(serve, tmp_path):
    """§1 E1 — an instance pointed at THIS checkout loads its manifest, wherever that checkout is.

    `SRV-FIX-491` criterion 4, and the case that produced the ticket. Run from a lane this is a
    linked worktree of the factory with `~/.nvm`, `~/.gitconfig` and `~/.local/bin` out of reach;
    run from the developer's checkout they are all there. Either way the manifest is `ok` and the
    ticket surface serves a real document — the difference is only WHICH workspaces carry a
    warning, and the sweep asserts it saw at least one workspace in each state before it concludes
    anything, because a run that finds no unbuildable workspace has asked nothing.
    """
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    private_home = tmp_path / "private-home"
    for name in (".codex", ".claude", ".nvm", ".local/bin", ".local/lib"):
        (private_home / name).mkdir(parents=True, exist_ok=True)
    (private_home / ".gitconfig").write_text("[user]\n\tname = Fixture\n")
    before = sorted(str(path.relative_to(private_home)) for path in private_home.rglob("*"))
    srv = serve(here, HOME=str(private_home))
    health = srv.get("/api/health")
    assert health.status_code == 200, health.text
    assert health.json()["manifest"] == "ok", (
        "an instance pointed at the checkout this suite lives in cannot load its manifest, so no "
        f"lane of this repository can serve it: {health.text[:500]}")

    body = manifest_of(srv)
    unbuildable = {w["field"] for w in body.get("warnings", [])
                   if w.get("code") == "workspace_unbuildable"}
    declared = set(body["workspaces"])
    assert declared, body
    buildable = declared - unbuildable
    assert buildable or unbuildable, (
        f"the sweep saw neither state: declared={declared} unbuildable={unbuildable}")

    listing = srv.get("/api/tickets")
    assert listing.status_code == 200, listing.text
    assert listing.json()["groups"], f"the ticket surface serves nothing: {listing.text[:300]}"

    # A real document off disk, read whole — a listing can be served from a catalogue that never
    # opened a file, and this repository's own ticket is the one a lane is being run for.
    one = srv.get("/api/tickets/SRV-FIX-532")
    assert one.status_code == 200, (
        f"the ticket this lane exists for is not served from inside it: {one.status_code} "
        f"{one.text[:300]}")
    assert one.json()["workspace"] == "factory-server", one.json()
    assert sorted(str(path.relative_to(private_home)) for path in private_home.rglob("*")) == before, (
        "serving the repository modified the private profile fixture")



from test_progress import env  # noqa: E402,F401 — the fixture, so this case can claim a lane


def test_status_done_is_optional_and_must_be_a_list(env, serve, tmp_path):
    """SRV-FIX-599 §1.8 — NEGATIVE. `pass` is the repository's word, not the tool's (S1).

    A manifest that declares no `status.done` has no done tickets, so a ticket at `pass` draws its
    stage share, never 100. And a `done` written as a string is refused at load, naming the
    pointer — a manifest the tool silently read as an empty set would hide every done ticket.
    """
    from test_lane import build_repo
    from test_progress import claim, conform, lane_rows, seed_run, to, write_manifest, write_ticket

    def prepare(root):
        write_manifest(root, done=None)
        write_ticket(root, "DONE-1")
        seed_run(root, "DONE-1", to("REVIEW"), ended=False)
    env.build(prepare)
    lane = claim(env, "DONE-1")
    write_ticket(env.root, "DONE-1", status="pass")
    undeclared = lane_rows(env)[lane]["progress_pct"]

    bad = build_repo(tmp_path / "bad")
    write_manifest(bad, status={"done": "pass"})
    r = serve(bad).get("/api/manifest")
    assert (undeclared, r.status_code, r.json()["error"]["detail"].get("pointer")) \
        == (60, 422, "/status/done"), (undeclared, r.text)
    conform(lane_rows(env))
