"""SRV-FIX-175 — a refusal states the problem AND the way out.

`SPEC.md` §3.6, frozen by `WEB-FEAT-173`:

  > Every state the tool can put on a screen carries an action that ends it. A refusal names what
  > to do *and offers it*.

`test_spec.py` holds check 12 against the DOCUMENT — every code the server emits has a remedy row
or a reasoned exemption in §11. That is a check about a table. This file is the check about the
WIRE: the bodies the running server actually sends.

    cd factory/server && pytest tests/test_errors.py -v

Nothing here keeps a list of remedies beside the code. The verbs, their targets and the nouns
`destroys` may name are read out of `SPEC.md` §9 and `http.schema.json` on every run, and the
codes are re-derived from `app/` by `test_spec.error_codes` — the same derivation check 12 uses.
A table maintained beside the thing it describes is a table that goes stale in the direction that
looks like the product working.
"""

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema
import pytest

from conftest import _start_instance, stop_process
from test_catalog import SUB, doc, two_repo_manifest
from test_lane import build_repo, git, status_of
from test_spec import (error_codes, exemption_table, noun_set, remedy_table, rows,
                       site_exemption_table, spec, verb_set, VERBS_OPEN)
from test_agent_profiles import CAPTURE, PEER, frames, eventually
from test_agent import assert_reaped
from test_lane_command import collect

pytestmark = pytest.mark.repository

FACTORY = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((FACTORY / "docs/technical/contracts/http.schema.json").read_text())
ERROR_BODY = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"],
              "$ref": "#/$defs/errorBody"}
ERROR_VALIDATOR = jsonschema.Draft202012Validator(ERROR_BODY)


# ── what §9.2 says a verb's target must carry ────────────────────────────────────────────────

def target_fields():
    """action → the set of member names §9.2's `target` column names for it.

    Read out of the document, so a verb whose target changes changes this check with it. A cell
    reads `` `{lane, force}` `` or `` `{}` ``.
    """
    out = {}
    for cells in rows(spec(), VERBS_OPEN):
        action = cells[0].strip().strip("`").strip()
        cell = cells[2].strip().strip("`").strip() if len(cells) > 2 else "{}"
        inner = cell.strip("{}").strip()
        out[action] = {p.strip() for p in inner.split(",") if p.strip()}
    return out


# ── the corpus of refusals ───────────────────────────────────────────────────────────────────
#
# Every one of these is a real HTTP call against a real server, and each names the code it must
# provoke — so a provocation that stops reaching its refusal fails HERE rather than quietly
# shrinking the domain the assertions below quantify over.

# Codes `app/` emits that this corpus does not reach, and why. A reason each, for the same reason
# §11's exemption table demands one: the cheap way to make a coverage assertion green is to excuse
# everything.
UNPROVOKED = {
    "device_busy": "needs a character device declared in a manifest AND held by another process; "
                   "`test_isolation.py` skips itself when the machine has no safe unheld node, "
                   "and a corpus that silently skips is a corpus that reports coverage it lacks.",
    "gone": "needs a permission request whose run dies between the ask and the answer — a race "
            "`test_permissions.py` builds with a live agent, two threads and a kill.",
    "spawn_failed": "needs fork() or mkfifo() to fail on a machine where they do not.",
    "remove_failed": "needs `git worktree remove` to fail on a checkout that passed every guard "
                     "immediately before it.",
    "write_failed": "needs `.factory/permissions.json` to be unwritable at the instant a grant "
                    "is committed, with the grant itself already decided.",
    "unreadable_worktree": "needs `git status` to fail on a directory `stat` succeeded on.",
    "empty_message": "needs a live agent run: the empty-text guard is behind the has-a-run guard, "
                     "so a lane with no run answers `wrong_state` and never reaches it.",
    "ticket_not_on_branch": "needs a declared agent, a start prompt and a ticket committed to the "
                            "main checkout but not to the lane's branch (`test_agent.py`).",
    "ambiguous_ticket_id": "needs two declared repositories holding one ticket id "
                           "(`test_catalog.py`); the corpus builds one repository per server.",
    "worktree_in_the_way": "`SRV-FIX-059` round 7 made the claim step PAST anything already at a "
                           "lane path — `skipTaken_` moves the counter — so reaching this needs a "
                           "directory to appear between the name being allocated and git being "
                           "asked, which is a race a test cannot hold open.",
    "agent_not_declared": "unreachable over HTTP: `/agent/command` defaults to `claude` and an "
                          "empty one is refused AT LOAD as `empty_agent_command`, so no manifest "
                          "that loads can produce an empty argv for a run to refuse on.",
}


class Corpus:
    """Every refusal this file provokes, collected once."""

    def __init__(self, tmp_path_factory):
        self.dir = tmp_path_factory.mktemp("errors")
        self.servers = []
        self.seen = []
        self.n = 0

    # ── plumbing ──
    def repo(self, **kw):
        """A synthetic repository. Never this one.

        The agent is declared as `/bin/sh` unless a case says otherwise: the manifest's default
        is `claude`, and a corpus that refuses to load its own fixtures on a machine without that
        binary installed measures the machine rather than the server.
        """
        self.n += 1
        kw.setdefault("agent", {"command": "/bin/sh"})
        return build_repo(self.dir / f"repo{self.n}", **kw)

    def serve(self, root, **env):
        inst = _start_instance(root, self.dir / f"srv{len(self.servers)}.log", **env)
        self.servers.append(inst)
        return inst

    def stop(self):
        for inst in self.servers:
            if inst.proc is not None:
                stop_process(inst.proc)
            if inst.handle is not None:
                inst.handle.close()

    def record(self, how, expect, response, site=None):
        """`site` is the `E<n>` this call reached, and it is given only where §11 exempts it.

        A remedy is a property of a site (`SRV-FIX-343`), so a refusal whose code is remedied
        everywhere else and terminal HERE has to say which `here` it is. Every other entry
        leaves it `None` and is judged by its code alone, as it always was.
        """
        assert response.status_code >= 400, (
            f"[{how}] did not refuse: {response.status_code} {response.text[:300]}")
        body = response.json()
        code = body.get("error", {}).get("code")
        assert code == expect, (
            f"[{how}] provoked {code!r}, not {expect!r}: "
            f"{response.status_code} {response.text[:300]}")
        self.seen.append({"how": how, "code": code, "site": site,
                          "status": response.status_code, "body": body})
        return body

    def by_code(self):
        out = {}
        for entry in self.seen:
            out.setdefault(entry["code"], []).append(entry)
        return out

    # ── the provocations ──
    def collect(self):
        self._manifest()
        self._catalog()
        self._claim()
        self._commands()
        self._leftovers()
        self._permissions()
        self._agent()
        self._profile_starts()
        self._tree()
        self._routing()
        return self

    def _tree(self):
        root = self.repo(workspaces={'core': {'path': '.', 'start_preflight': {
            'command': 'printf reached > preflight-started.txt'}}})
        path = root / '.factory/factory.json'
        manifest = json.loads(path.read_text())
        manifest['trees'] = {'full': {'decider': 'rules', 'agents': ['absent']}}
        manifest['default_tree'] = 'full'
        path.write_text(json.dumps(manifest))
        git(root, 'add', '-A')
        git(root, 'commit', '-m', 'missing selected tree agent')
        srv = self.serve(root)
        made = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
        assert made.status_code == 201, made.text
        lane = made.json()
        self.record('POST /api/lanes/<lane>/runs with a missing selected tree agent',
                    'tree_agent_missing',
                    srv.post(f"/api/lanes/{lane['lane']}/runs", json={'tree': 'full'}))
        assert not (Path(lane['worktree']) / 'preflight-started.txt').exists()
        assert srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []

    def _profile_starts(self):
        credentials = self.dir / 'profile-credentials'
        credentials.mkdir()
        (credentials / 'auth.json').write_text('{"token":"fixture-secret"}\n')
        root = self.repo(agent=None, agents={
            'gpt': {'driver': 'codex', 'command': '/usr/bin/python3 agent.py fail',
                    'models': ['gpt-test'], 'default_model': 'gpt-test',
                    'credentials_dir': str(credentials)}})
        (root / 'agent.py').write_text(PEER)
        (root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
        git(root, 'add', '-A')
        git(root, 'commit', '-m', 'recorded initialization refusal')
        srv = self.serve(root, FACTORY_AGENT_CONFIG_ROOT=str(self.dir / 'profile-runtime'))
        made = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
        assert made.status_code == 201, made.text
        lane = made.json()
        endpoint = f"/api/lanes/{lane['lane']}/runs"
        for body in ({'agent': 'undeclared'}, {'model': []}):
            self.record('POST /api/lanes/<lane>/runs with an invalid declared selection',
                        'invalid_agent_selection', srv.post(endpoint, json=body))
            assert frames(lane) == []
        self.record('POST /api/lanes/<lane>/runs with a recorded initialization error',
                    'agent_start_failed', srv.post(endpoint, json={}))
        assert any(row.get('method') == 'initialize' for row in frames(lane))
        eventually(lambda: srv.get('/api/tickets/TCK-001/runs').json()['runs'],
                   lambda rows: rows and all(row['outcome'] != 'running' for row in rows))

        root = self.repo(workspaces={'core': {'path': '.', 'start_preflight': {
            'command': '/bin/sh preflight.sh', 'timeout_seconds': 2}}},
            agent=None, agents={
                'gpt': {'driver': 'codex', 'command': '/usr/bin/python3 agent.py',
                        'models': ['gpt-test'], 'default_model': 'gpt-test',
                        'credentials_dir': str(credentials)}})
        (root / 'agent.py').write_text(PEER)
        (root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
        ignore = root / '.gitignore'
        ignore.write_text(ignore.read_text() + '.refusal-observation/\n')
        (root / 'preflight.sh').write_text(
            '/bin/mkdir -p .refusal-observation\n'
            'printf ready > .refusal-observation/pending\n'
            'test ! -f .refusal-observation/pass || exit 0\n'
            'while [ ! -f .refusal-observation/release ]; do /bin/sleep .02; done\n'
            'exit 7\n')
        git(root, 'add', '-A')
        git(root, 'commit', '-m', 'held confined preflight')
        srv = self.serve(root, FACTORY_AGENT_CONFIG_ROOT=str(self.dir / 'preflight-runtime'))
        made = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
        assert made.status_code == 201, made.text
        worktree = Path(made.json()['worktree'])
        endpoint = f"/api/lanes/{made.json()['lane']}/runs"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(srv.post, endpoint, json={'prompt': 'go'})
            try:
                eventually(lambda: (worktree / '.refusal-observation/pending').exists(), bool)
                for attempt in range(2):
                    self.record(f'POST /api/lanes/<lane>/runs retry {attempt} while real preflight is held',
                                'start_preflight_running', srv.post(endpoint, json={'prompt': 'go'}))
                assert srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []
                assert frames({'worktree': str(worktree)}) == []
            finally:
                release = worktree / '.refusal-observation/release'
                release.parent.mkdir(exist_ok=True)
                release.write_text('release\n')
            self.record('POST /api/lanes/<lane>/runs after preflight exits nonzero',
                        'start_preflight_failed', pending.result(timeout=10))
        release.unlink()
        self.record('POST /api/lanes/<lane>/runs whose actual preflight times out',
                    'start_preflight_timeout', srv.post(endpoint, json={'prompt': 'go'}))
        assert srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []
        assert frames({'worktree': str(worktree)}) == []
        (worktree / '.refusal-observation/pass').write_text('ready\n')
        healthy = srv.post(endpoint, json={'prompt': 'go'})
        assert healthy.status_code == 201, healthy.text
        observed = eventually(lambda: frames({'worktree': str(worktree)}),
                              lambda rows: any(row.get('method') == 'turn/start' for row in rows))
        assert sum(row.get('method') == 'initialize' for row in observed) == 1
        assert 'remedy' not in json.dumps(healthy.json())
        ended = srv.post(endpoint.removesuffix('/runs') + '/end', json={})
        assert ended.status_code == 202, ended.text
        assert_reaped(healthy.json()['pid'])

    def _routing(self):
        root = self.repo(workspaces={'core': {'path': '.', 'build': '/bin/sh build.sh'}})
        ignore = root / '.gitignore'
        ignore.write_text(ignore.read_text() + '.refusal-observation/\n')
        (root / 'build.sh').write_text(
            '/bin/mkdir -p .refusal-observation\n'
            'printf executed >> .refusal-observation/effects\n'
            'if [ -f .refusal-observation/hold ]; then\n'
            '  printf ready > .refusal-observation/pending\n'
            '  while [ ! -f .refusal-observation/release ]; do /bin/sleep .02; done\n'
            'fi\n'
            'printf "actual terminal capture\\n"\n')
        git(root, 'add', '-A')
        git(root, 'commit', '-m', 'declared confined routing command')
        srv = self.serve(root)
        made = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
        assert made.status_code == 201, made.text
        worktree = Path(made.json()['worktree'])
        endpoint = f"/api/lanes/{made.json()['lane']}/routing"
        plan = {'request_id': 'valid-control', 'steps': [
            {'which': 'build', 'accepted_exit_codes': [0], 'reuse': True}]}
        self.record('POST /api/lanes/<lane>/routing with malformed plan',
                    'bad_request', srv.post(endpoint, json={'request_id': 'malformed', 'steps': []}))
        self.record('POST /api/lanes/<lane>/routing with an unknown command',
                    'bad_which', srv.post(endpoint, json={**plan, 'steps': [
                        {'which': 'undeclared-name', 'accepted_exit_codes': [0]}]}))
        self.record('POST /api/lanes/<lane>/routing with an undeclared gate command',
                    'not_declared', srv.post(endpoint, json={**plan, 'steps': [
                        {'which': 'gate', 'accepted_exit_codes': [0]}]}))
        self.record('POST /api/lanes/<missing>/routing', 'not_found',
                    srv.post('/api/lanes/lane/999/routing', json=plan))
        self.record('GET /api/lanes/<lane>/routing/<missing>', 'not_found',
                    srv.get(endpoint + '/missing-request'))
        assert not (worktree / '.refusal-observation/effects').exists()
        observations = worktree / '.refusal-observation'
        observations.mkdir()
        (observations / 'hold').write_text('hold\n')
        started = srv.post(endpoint, json=plan)
        assert started.status_code == 202, started.text
        try:
            eventually(lambda: (observations / 'pending').exists(), bool)
            self.record('POST /api/lanes/<lane>/routing while another real request is held',
                        'routing_busy', srv.post(endpoint, json={**plan, 'request_id': 'busy-retry'}))
            conflict = {**plan, 'steps': [{'which': 'build', 'accepted_exit_codes': [7]}]}
            conflicting = self.record('POST /api/lanes/<lane>/routing reusing an ID for a changed plan',
                                      'routing_conflict', srv.post(endpoint, json=conflict))
            assert conflicting['error']['remedy'] is None
            duplicate = srv.post(endpoint, json=plan)
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()['state'] == 'running'
            assert (observations / 'effects').read_bytes() == b'executed'
        finally:
            (observations / 'release').write_text('release\n')
        final = eventually(lambda: srv.get(endpoint + '/valid-control').json(),
                           lambda body: body.get('state') != 'running')
        assert final['state'] == 'completed', final
        assert 'remedy' not in json.dumps(started.json())
        assert 'remedy' not in json.dumps(final)
        receipt = final['receipts'][0]
        assert receipt['complete'] is True and receipt['exit_code'] == 0
        path = Path(receipt['capture_path'])
        capture = path if path.is_absolute() else root / path
        assert capture.read_bytes() == b'actual terminal capture\n'
        effects = worktree / '.refusal-observation/effects'
        assert effects.read_bytes() == b'executed'
        retried = srv.post(endpoint, json={**plan, 'request_id': 'busy-retry'})
        assert retried.status_code == 202, retried.text
        retry_final = eventually(lambda: srv.get(endpoint + '/busy-retry').json(),
                                 lambda body: body.get('state') != 'running')
        assert retry_final['state'] == 'completed'
        assert retry_final['receipts'][0]['reused'] is True
        assert effects.read_bytes() == b'executed'
        (observations / 'release').unlink()
        (observations / 'pending').unlink()
        socket = srv.ws()
        socket.send({'m': 'subscribe', 'scope': {'lane': made.json()['lane']}})
        socket.wait_for('snapshot')
        manual = srv.post(endpoint.removesuffix('/routing') + '/commands', json={'which': 'build'})
        assert manual.status_code == 202, manual.text
        try:
            eventually(lambda: (observations / 'pending').exists(), bool)
            self.record('POST /api/lanes/<lane>/routing while a manual command is held',
                        'wrong_state', srv.post(endpoint, json={**plan, 'request_id': 'manual-busy'}))
            assert effects.read_bytes() == b'executedexecuted'
        finally:
            (observations / 'release').write_text('release\n')
        try:
            terminal = collect(socket)
            assert terminal[-1]['exit_code'] == 0
            assert terminal[-1]['command_id'] == manual.json()['command_id']
        finally:
            socket.close()
        expected_effects = effects.read_bytes()
        capture.write_bytes(b'changed capture bytes\n')
        refused = self.record('GET /api/lanes/<lane>/routing/<request> with a tampered capture',
                              'routing_unresolved', srv.get(endpoint + '/valid-control?include_output=true'))
        assert refused['error']['remedy'] is None
        assert 'output_base64' not in json.dumps(refused)
        self.record('POST /api/lanes/<lane>/routing after capture tampering',
                    'routing_unresolved', srv.post(endpoint, json={**plan, 'request_id': 'tampered'}))
        assert effects.read_bytes() == expected_effects
        assert srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []

    def _manifest(self):
        broken = self.dir / "broken"
        (broken / ".factory").mkdir(parents=True)
        (broken / ".factory" / "factory.json").write_text(json.dumps({"factory": "0.1"}))
        srv = self.serve(broken)
        self.record("GET /api/manifest, manifest with no workspaces",
                    "invalid_manifest", srv.get("/api/manifest"))

        future = self.dir / "future"
        (future / ".factory").mkdir(parents=True)
        (future / ".factory" / "factory.json").write_text(
            json.dumps({"factory": "9.0", "workspaces": {"core": {"path": "."}}}))
        srv = self.serve(future)
        self.record("GET /api/manifest, manifest major this build does not speak",
                    "unsupported_manifest_major", srv.get("/api/manifest"))

        nothing = self.dir / "nothing"
        nothing.mkdir()
        srv = self.serve(nothing)
        self.record("GET /api/manifest, no manifest on disk",
                    "not_found", srv.get("/api/manifest"))

        # E12 refuses against a manifest the tool would not load — its own inline body.
        self.record("POST /api/lanes against a manifest that did not load",
                    "invalid_manifest",
                    self.servers[0].post("/api/lanes",
                                         json={"ticket_id": "TCK-001", "workspace": "core"}))

    def _catalog(self):
        srv = self.serve(self.repo())
        self.record("GET /api/requirements?filter=nope",
                    "bad_filter", srv.get("/api/requirements?filter=nope"))
        self.record("GET /api/requirements/NOPE",
                    "not_found", srv.get("/api/requirements/NOPE"))
        self.record("GET /api/tickets/NOPE", "not_found", srv.get("/api/tickets/NOPE"))
        # E26 · SRV-FIX-188. `build_repo` writes TCK-001 and TCK-002, so `TCK` is a prefix in use
        # and `NOPE` is not — and the tool will not start a numbering series for a typo.
        self.record("POST /api/tickets/ids with a prefix no declared repository uses",
                    "unknown_prefix", srv.post("/api/tickets/ids", json={"prefix": "NOPE"}))
        # E32 · SRV-FEAT-501. A window is a whole number of days between 1 and 3650, and the
        # refusal happens before anything is read — so the corpus reaches it against a
        # repository with no run records at all.
        self.record("GET /api/metrics?days=abc — a window that is not a number of days",
                    "bad_window", srv.get("/api/metrics?days=abc"))

    def _claim(self):
        srv = self.serve(self.repo())
        self.record("POST /api/lanes with a body that is not JSON",
                    "bad_request", srv.post("/api/lanes", data="{"))
        self.record("POST /api/lanes for a ticket the repository does not hold",
                    "not_found", srv.post("/api/lanes",
                                          json={"ticket_id": "NOPE", "workspace": "core"}))
        self.record("POST /api/lanes for a workspace the manifest does not declare",
                    "not_found", srv.post("/api/lanes",
                                          json={"ticket_id": "TCK-001", "workspace": "nope"}))
        assert srv.post("/api/lanes",
                        json={"ticket_id": "TCK-001", "workspace": "core"}).status_code == 201
        self.record("POST /api/lanes twice for one ticket and role",
                    "lane_exists", srv.post("/api/lanes",
                                            json={"ticket_id": "TCK-001", "workspace": "core"}))
        self.record("DELETE /api/lanes/<a name that leaves the lane root>",
                    "bad_lane", srv.delete("/api/lanes/..%2f..%2felsewhere?force=true"))
        self.record("DELETE /api/lanes/lane/99 — no such lane",
                    "not_found", srv.delete("/api/lanes/lane/99?force=true"))
        self.record("POST /api/progress with no token",
                    "bad_request", srv.post("/api/progress", json={}))
        self.record("POST /api/progress with a token no run holds",
                    "not_found", srv.post("/api/progress",
                                          json={"token": "nope", "stage": "SPEC"}))

        # SRV-FIX-290 — a manifest root with no git repository above it anywhere. Confinement
        # binds the git root, so a lane here could never be run; the refusal belongs at the claim
        # and not three steps later as git's own "not a git repository".
        import shutil as _shutil
        rootless = self.repo()
        _shutil.rmtree(rootless / ".git")
        self.record("POST /api/lanes on a root with no git repository above it",
                    "no_git_root", self.serve(rootless).post(
                        "/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"}))

        one = self.serve(self.repo(lanes_host=1))
        assert one.post("/api/lanes",
                        json={"ticket_id": "TCK-001", "workspace": "core"}).status_code == 201
        self.record("POST /api/lanes with every host seat taken",
                    "no_seat", one.post("/api/lanes",
                                        json={"ticket_id": "TCK-002", "workspace": "core"}))

        # A workspace whose declared toolchain went away after the manifest loaded.
        toolchain = self.dir / "toolchain"
        (toolchain / "bin").mkdir(parents=True)
        root = self.repo(workspaces={"core": {"path": ".", "toolchain": [str(toolchain)]}})
        gone = self.serve(root)
        assert gone.get("/api/manifest").status_code == 200
        shutil.rmtree(toolchain)
        self.record("POST /api/lanes for a workspace whose toolchain is gone",
                    "workspace_unrunnable",
                    gone.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"}))

        # A claim whose lane root cannot be read.
        root = self.repo()
        srv = self.serve(root)
        assert srv.post("/api/lanes",
                        json={"ticket_id": "TCK-001", "workspace": "core"}).status_code == 201
        lane_root = Path(root) / ".factory" / "worktrees"
        mode = os.stat(lane_root).st_mode
        try:
            os.chmod(lane_root, 0o000)
            self.record("POST /api/lanes with a lane root nothing can read",
                        "worktree_unreadable",
                        srv.post("/api/lanes",
                                 json={"ticket_id": "TCK-002", "workspace": "core"}))
        finally:
            os.chmod(lane_root, mode)

        # A branch already checked out somewhere git will not check it out twice.
        root = self.repo()
        elsewhere = self.dir / "elsewhere"
        git(root, "worktree", "add", "-b", "ticket/TCK-001", str(elsewhere))
        srv = self.serve(root)
        self.record("POST /api/lanes whose branch is checked out outside the lane root",
                    "worktree_failed",
                    srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"}))

        # SRV-FIX-478 — a ticket whose own document says the work is finished. The refusal is
        # decided in `api` on the document `E12` already loads, before `lane::Store` is entered,
        # and it is terminal: the way out is a person filing a FIX or editing the status, and the
        # tool writes none of the repository's documents.
        passed = self.repo()
        (Path(passed) / "docs" / "tickets" / "TCK-001.md").write_text(
            "---\nid: TCK-001\ntitle: ticket TCK-001\nstatus: pass\n---\n\nbody\n")
        self.record("POST /api/lanes for a ticket whose document says `status: pass`",
                    "already_passed",
                    self.serve(passed).post(
                        "/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"}))

        # SRV-FIX-189 — a role re-claimed for round 2, whose own verdict and whose round-2 work
        # edited the same lines. The work cannot be brought in, so the claim refuses rather than
        # hand back a tree that is neither round.
        root = self.repo()
        srv = self.serve(root)

        def lane_of(response):
            assert response.status_code == 201, response.text
            return response.json()

        run_lane = lane_of(srv.post("/api/lanes",
                                    json={"ticket_id": "TCK-001", "workspace": "core"}))
        run_wt = run_lane["worktree"]
        (Path(run_wt) / "contested.txt").write_text("round 1\n")
        git(run_wt, "add", "-A")
        git(run_wt, "commit", "-m", "round 1: the work")

        role = lane_of(srv.post("/api/lanes",
                                json={"ticket_id": "TCK-001", "workspace": "core", "role": "qa"}))
        (Path(role["worktree"]) / "contested.txt").write_text("the role's own edit\n")
        git(role["worktree"], "add", "-A")
        git(role["worktree"], "commit", "-m", "QA verdict round 1 — FAIL")
        assert srv.delete(f"/api/lanes/{role['lane']}?force=true").status_code == 200

        (Path(run_wt) / "contested.txt").write_text("round 2: the implementer's answer\n")
        git(run_wt, "add", "-A")
        git(run_wt, "commit", "-m", "round 2: the fix QA asked for")

        self.record("POST /api/lanes re-claiming a role whose work will not merge",
                    "work_conflicts",
                    srv.post("/api/lanes",
                             json={"ticket_id": "TCK-001", "workspace": "core", "role": "qa"}))

    def _commands(self):
        root = self.repo(
            workspaces={"edge": {"path": ".", "build": "true"}, "bare": {"path": "."}},
            resources={"chamber": {"exclusive": True, "needed_by": ["edge"],
                                   "description": "one chamber, one run at a time"}},
            tickets=("TCK-001", "TCK-002", "TCK-003"))
        srv = self.serve(root)
        first = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "edge"})
        assert first.status_code == 201, first.text
        second = srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "edge"})
        assert second.status_code == 201, second.text
        queued = second.json()["lane"]
        self.record("POST /api/lanes/<queued>/runs — the resource is held",
                    "resource_busy", srv.post(f"/api/lanes/{queued}/runs", json={}))
        self.record("POST /api/lanes/<queued>/commands — the resource is held",
                    "resource_busy",
                    srv.post(f"/api/lanes/{queued}/commands", json={"which": "build"}))
        self.record('POST /api/lanes/<queued>/routing while a resource is held',
                    'resource_busy', srv.post(f'/api/lanes/{queued}/routing', json={
                        'request_id': 'queued-routing', 'steps': [
                            {'which': 'build', 'accepted_exit_codes': [0]}]}))

        lane = first.json()["lane"]
        self.record("POST /api/lanes/<lane>/commands with a which that is neither",
                    "bad_which",
                    srv.post(f"/api/lanes/{lane}/commands", json={"which": "deploy"}))
        bare = srv.post("/api/lanes", json={"ticket_id": "TCK-003", "workspace": "bare"})
        assert bare.status_code == 201, bare.text
        self.record("POST /api/lanes/<lane>/commands for a command the workspace declares none of",
                    "not_declared",
                    srv.post(f"/api/lanes/{bare.json()['lane']}/commands",
                             json={"which": "build"}))
        self.record("POST /api/lanes/<lane>/messages with no run in the lane",
                    "wrong_state",
                    srv.post(f"/api/lanes/{lane}/messages", json={"text": "anybody there"}))
        self.record("POST /api/lanes/<nope>/messages", "not_found",
                    srv.post("/api/lanes/lane/98/messages", json={"text": "hello"}))
        self.record("GET /api/lanes/<nope>/diff", "not_found",
                    srv.get("/api/lanes/lane/98/diff"))
        self.record("GET /api/runs/<nope>/transcript", "not_found",
                    srv.get("/api/runs/run-nope/transcript"), site="E8")
        self.record("GET /api/tickets/<nope>/runs", "not_found",
                    srv.get("/api/tickets/NOPE/runs"))

    def _leftovers(self):
        """A lane's checkout with the tool's memory of it gone — E13's other door."""
        def leftover(prepare):
            root = self.repo()
            srv = self.serve(root, FACTORY_TEST_HOOKS="1")
            made = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
            assert made.status_code == 201, made.text
            lane = made.json()["lane"]
            tree = Path(root) / ".factory" / "worktrees" / lane
            prepare(root, tree)
            forgot = srv.post("/api/_test/forget-lane", json={"lane": lane})
            assert forgot.status_code in (200, 202), forgot.text
            return srv, lane, tree

        srv, lane, _ = leftover(
            lambda root, tree: (tree / "HALF-AN-AFTERNOON.md").write_text("not committed\n"))
        self.record("DELETE /api/lanes/<leftover holding uncommitted work>",
                    "uncommitted_changes", srv.delete(f"/api/lanes/{lane}?force=true"))

        def ignored(root, tree):
            (tree / ".gitignore").write_text("evidence/\n")
            (tree / "evidence").mkdir()
            (tree / "evidence" / "gate.log").write_text("one board, one run\n")
            git(tree, "add", "-A")
            git(tree, "commit", "-q", "-m", "ignore the capture directory")
            sha = git(tree, "rev-parse", "HEAD").stdout.strip()
            git(root, "branch", "-f", "carries-it", sha)

        srv, lane, tree = leftover(ignored)
        self.record("DELETE /api/lanes/<leftover holding ignored files>",
                    "ignored_work", srv.delete(f"/api/lanes/{lane}?force=true"))

        def committed(root, tree):
            (tree / "COMMITTED.md").write_text("an afternoon, and it was committed\n")
            git(tree, "add", "-A")
            git(tree, "commit", "-q", "-m", "work nothing else carries")

        srv, lane, _ = leftover(committed)
        self.record("DELETE /api/lanes/<leftover on a branch nothing else carries>",
                    "unmerged_commits", srv.delete(f"/api/lanes/{lane}?force=true"))

        # Somebody else's directory at a lane path.
        root = self.repo()
        srv = self.serve(root, FACTORY_TEST_HOOKS="1")
        stranger = Path(root) / ".factory" / "worktrees" / "lane" / "01"
        stranger.mkdir(parents=True)
        (stranger / "not-a-worktree.txt").write_text("somebody else's\n")
        self.record("DELETE /api/lanes/lane/01 — a directory this tool did not create",
                    "not_ours", srv.delete("/api/lanes/lane/01?force=true"))

    def _permissions(self):
        srv = self.serve(self.repo())
        self.record("PUT /api/permissions/policy with a policy that is not one of three",
                    "bad_policy",
                    srv.put("/api/permissions/policy", json={"policy": "sure-why-not"}))
        self.record("POST /api/permissions/decisions with a decision that is not one of three",
                    "bad_decision",
                    srv.post("/api/permissions/decisions",
                             json={"request_id": "req-nope", "decision": "maybe"}))
        self.record("POST /api/permissions/decisions for a request nothing is waiting on",
                    "not_found",
                    srv.post("/api/permissions/decisions",
                             json={"request_id": "req-nope", "decision": "once"}))
        self.record("DELETE /api/permissions/rules/<nope>",
                    "not_found", srv.delete("/api/permissions/rules/rule-nope"))
        self.record("POST /api/permission with no token",
                    "bad_request", srv.post("/api/permission", json={}))
        self.record("POST /api/permission with a token no run holds",
                    "not_found",
                    srv.post("/api/permission",
                             json={"token": "nope", "tool": "Bash", "input": {}}))

        srv = self.serve(self.repo(agent={"command": "/bin/sh agent.sh",
                                          "allowed_tools": ["Read"]}))
        listed = srv.get("/api/permissions/rules")
        assert listed.status_code == 200, listed.text
        rule = listed.json()["rules"][0]["rule_id"]
        self.record("DELETE /api/permissions/rules/<one the manifest declares>",
                    "not_revocable", srv.delete(f"/api/permissions/rules/{rule}"))

    def _agent(self):
        # A start prompt that was proven at load and has gone since.
        root = self.repo(agent={"command": "/bin/sh agent.sh",
                                "start_prompt_file": "docs/START.md"})
        (Path(root) / "docs" / "START.md").write_text("run {ticket_path}\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "a start prompt")
        srv = self.serve(root)
        assert srv.get("/api/manifest").status_code == 200
        made = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        assert made.status_code == 201, made.text
        lane = made.json()["lane"]
        (Path(root) / "docs" / "START.md").unlink()
        try:
            self.record("POST /api/lanes/<lane>/runs whose start prompt has gone",
                        "start_prompt_unreadable", srv.post(f"/api/lanes/{lane}/runs", json={}))
        finally:
            (Path(root) / "docs" / "START.md").write_text("run {ticket_path}\n")

        # A lane whose configuration directory would sit inside the developer's home: the
        # confinement refuses to be built, so nothing runs.
        inside = Path.home() / ".cache" / "har175-should-never-be-used"
        shutil.rmtree(inside, ignore_errors=True)
        root = self.repo(agent={"command": "/bin/sh agent.sh"})
        srv = self.serve(root, FACTORY_TEST_HOOKS="1",
                         FACTORY_AGENT_CONFIG_ROOT=str(inside))
        made = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
        assert made.status_code == 201, made.text
        lane = made.json()["lane"]
        self.record("POST /api/_test/lane-process into a lane that cannot be confined",
                    "confinement_unavailable",
                    srv.post("/api/_test/lane-process",
                             json={"lane": lane, "command": "echo x"}))


@pytest.fixture(scope="module")
def refusals(tmp_path_factory):
    corpus = Corpus(tmp_path_factory)
    try:
        yield corpus.collect()
    finally:
        corpus.stop()


# ── 1 · every refusal carries a remedy ───────────────────────────────────────────────────────

def test_every_refusal_the_suite_provokes_carries_a_remedy(refusals):
    """§3.6, on the wire.

    `lane/03` sat for thirteen hours in a state the screen described perfectly and could not end.
    Every body below validates against `http.schema.json` — and carries `remedy`, unless §11
    check 12 exempts its code, in which case it carries `null` and §11 carries the reason.
    """
    exempt = exemption_table(spec())
    remedied = remedy_table(spec())
    sites = site_exemption_table(spec())
    silent = []
    checked_a_site = 0
    for entry in refusals.seen:
        ERROR_VALIDATOR.validate(entry["body"])
        error = entry["body"]["error"]
        code = entry["code"]
        if code in exempt:
            assert "remedy" in error and error["remedy"] is None, (
                f"[{entry['how']}] `{code}` is exempt in §11, so its body carries "
                f"`remedy: null` and not {error.get('remedy')!r}")
            continue
        if (code, entry.get("site")) in sites:
            checked_a_site += 1
            assert "remedy" in error and error["remedy"] is None, (
                f"[{entry['how']}] `{code}` is exempt at `{entry['site']}` in §11, so its body "
                f"carries `remedy: null` and not {error.get('remedy')!r}. The member is present "
                f"and null: absent is a different answer")
            continue
        if code not in remedied:
            continue                       # §11 governs neither way — pinned by its own test
        if not isinstance(error.get("remedy"), dict):
            silent.append(f"{code}: {entry['how']} → remedy {error.get('remedy')!r}")
    assert not silent, (
        f"{len(silent)} refusal(s) name what is wrong and not what ends it:\n  "
        + "\n  ".join(silent))
    assert checked_a_site == len(sites), (
        f"§11 exempts {len(sites)} site(s) and this corpus reached {checked_a_site} of them. A "
        f"site exemption the corpus never provokes is a null nothing on the wire is judging: "
        f"{sorted(sites)}")


# ── 2 · a remedy names a call the client can make ────────────────────────────────────────────

def test_a_remedy_names_a_call_the_client_can_make(refusals):
    """A verb with the wrong target is a button that 404s.

    `action` from §9.2's closed set, `target` carrying exactly the members §9.2's `target` column
    names for that verb, and `destroys` from §9.3's nouns. All three read out of the document.
    """
    verbs = verb_set(spec())
    fields = target_fields()
    nouns = noun_set(spec())
    complaints = []
    offered = 0
    for entry in refusals.seen:
        remedy = entry["body"]["error"].get("remedy")
        if not isinstance(remedy, dict):
            continue
        offered += 1
        action = remedy.get("action")
        if action not in verbs:
            complaints.append(f"[{entry['how']}] `{entry['code']}` offers {action!r}, which "
                              f"§9.2's closed verb set does not name")
            continue
        got = set((remedy.get("target") or {}).keys())
        if got != fields[action]:
            complaints.append(
                f"[{entry['how']}] `{entry['code']}`'s `{action}` target is {sorted(got)}, "
                f"and §9.2 says that verb acts on {sorted(fields[action])}")
        if not remedy.get("label", "").strip():
            complaints.append(f"[{entry['how']}] `{entry['code']}`'s `{action}` has no label, "
                              f"so the control has no words on it")
        for noun in remedy.get("destroys", []):
            if noun not in nouns:
                complaints.append(f"[{entry['how']}] `{entry['code']}`'s `{action}` says it "
                                  f"destroys {noun!r}, which §9.3 does not declare")
    assert offered, (
        f"not one of the {len(refusals.seen)} refusals this corpus provoked offers a remedy at "
        f"all, so there is nothing here to judge — a check that quantifies over an empty set is "
        f"a check that passes for the wrong reason")
    assert not complaints, (
        f"{len(complaints)} remedy/remedies name a call the client cannot build:\n  "
        + "\n  ".join(complaints))


# ── 3 · twenty-one manifest sites, one remedy ────────────────────────────────────────────────

def test_the_twenty_one_manifest_sites_share_one_remedy(refusals):
    """`invalid_manifest` is assigned at two dozen sites in one file.

    Twenty-one bespoke remedies is the shape this fix fails as, so the assertion is that every
    `invalid_manifest` body offers the SAME action — what differs between them is `detail`, which
    is where the pointer that failed belongs.
    """
    sites = error_codes()["invalid_manifest"]
    assert len(sites) >= 21, (
        f"only {len(sites)} `invalid_manifest` sites found in app/ — the derivation has stopped "
        f"seeing the source and this assertion would pass for the wrong reason")

    bodies = [e for e in refusals.seen if e["code"] == "invalid_manifest"]
    assert len(bodies) >= 2, (
        f"the corpus reaches `invalid_manifest` from fewer than two call sites, so 'they share "
        f"one remedy' is not something it can measure: {[b['how'] for b in bodies]}")
    actions = {b["body"]["error"]["remedy"]["action"] for b in bodies}
    assert actions == {"reload_manifest"}, (
        f"the `invalid_manifest` sites do not share one remedy: {actions}")
    # ...and one remedy is not one BODY: the pointer that failed still travels.
    assert all("detail" in b["body"]["error"] for b in bodies), bodies


# ── 4 · a destructive remedy says what it destroys ───────────────────────────────────────────

def test_a_destructive_remedy_says_what_it_destroys(refusals):
    """`destroys` is the member that makes *no failure's only remedy destroys work* checkable.

    The list, not its presence. `release_lane` on a LIVE lane ends its session and removes its
    checkout; on a leftover the claim rolled back there is no session to lose, and the wire says
    so — §9.2 allows the wire to be narrower than the verb's worst case, and a confirm dialog
    that overstates what it costs is the same defect as one that understates it.
    """
    by_code = refusals.by_code()

    # SRV-FEAT-180 — `resource_busy` has TWO ways out and which one is offered is a fact about
    # the holder, not about the code. Every lane this corpus builds is claimed and never started,
    # so its holder has no process and the way out is the one that costs nothing: free what the
    # corpse holds, and the holder keeps its session and its checkout. The destructive branch —
    # a LIVE holder, `release_lane`, `["session","worktree"]`, with a confirm — needs a running
    # process, which this corpus cannot build; `test_lane.py::test_a_live_holder_is_never_freed_
    # this_way` asserts it there, at both call sites, against a real one.
    busy = by_code["resource_busy"][0]["body"]["error"]["remedy"]
    assert busy["action"] == "release_resource", busy
    assert busy["destroys"] == [], (
        f"the holder of this resource is not running, so the way out is to free what it holds "
        f"and that costs nothing; the remedy says {busy['destroys']}")
    assert busy["target"] == {"resource": "chamber", "lane": "lane/01"}, busy

    ignored = by_code["ignored_work"][0]["body"]["error"]["remedy"]
    assert ignored["action"] == "discard_ignored", ignored
    assert ignored["destroys"] == ["worktree", "ignored_files"], (
        f"discarding a leftover's ignored files loses the checkout and the files, and no "
        f"session — the record was gone before the call: {ignored}")
    assert ignored["target"].get("paths"), (
        f"a person deciding whether to lose something is owed what it is: {ignored}")

    # ...and nothing else the corpus reached offers a way out that costs anything.
    costly = {e["code"] for e in refusals.seen
              if isinstance(e["body"]["error"].get("remedy"), dict)
              and e["body"]["error"]["remedy"]["destroys"]}
    assert costly == {"ignored_work"}, (
        f"a refusal outside the two this corpus reaches offers a destructive way out: "
        f"{sorted(costly)}")


# ── 5 · NEGATIVE (mandatory) — a success carries no remedy ───────────────────────────────────

def test_a_success_carries_no_remedy(refusals):
    """The cheap way to make the schema pass everywhere is to attach a remedy to every response.

    A remedy on a success is a control offered for a state that is not wrong. Watched RED against
    an implementation that attaches it in the response builder rather than at the refusal.
    """
    srv = refusals.servers[4]                 # the corpus's first healthy repository
    root = srv.root
    ok = []
    for path in ("/api/health", "/api/manifest", "/api/requirements", "/api/lanes",
                 "/api/permissions/rules", "/api/ota/nothing"):
        r = srv.get(path)
        if r.status_code >= 400:
            continue
        ok.append((path, r.json()))
    made = srv.post("/api/lanes", json={"ticket_id": "TCK-002", "workspace": "core"})
    if made.status_code < 400:
        ok.append(("POST /api/lanes", made.json()))
        released = srv.delete(f"/api/lanes/{made.json()['lane']}?force=true")
        if released.status_code < 400:
            ok.append(("DELETE /api/lanes/<lane>", released.json()))

    assert len(ok) >= 5, f"too few 2xx bodies to judge: {[p for p, _ in ok]}"
    carrying = [path for path, body in ok if "remedy" in json.dumps(body)]
    assert not carrying, (
        f"{len(carrying)} successful response(s) carry a remedy — a control offered for a state "
        f"that is not wrong: {carrying}")
    assert Path(root).is_dir()


# ── 6 · the one code §11 does not govern, pinned ─────────────────────────────────────────────

def test_every_refusal_check_12_reaches_is_governed_by_section_11(refusals):
    """§11 governs every refusal, both tables together — and `bad_filter` was the last hole.

    It is a string argument to `catalog::errorJson` rather than an `errorCode` assignment, so §11
    check 12 could not see it and neither table named it. `SPEC.md` was `WEB-FEAT-173`'s to edit
    (`_RESERVATION-173-180.md`), so the ticket that found the gap could only refuse to let it grow:
    this case asserted the ungoverned set was EXACTLY `{"bad_filter"}`.

    That reservation outlived all eight of its children — every one `pass` or `superseded` — and
    went on reserving the section for a ticket that had finished, which is why the gap survived for
    weeks while `test_spec.py::test_every_error_code_names_a_remedy_or_a_reasoned_exemption` stayed
    RED against it. **The two suites contradicted each other**: this one required `bad_filter` in
    neither table, that one required every code in one. Both could not be satisfied, so a green
    run was impossible and everybody learned to read past two failures.

    The row is written and the assertion inverts: nothing is ungoverned, and a new refusal that
    escapes both tables turns this red instead of joining a documented exception.
    """
    governed = set(remedy_table(spec())) | set(exemption_table(spec()))
    reached = {e["code"] for e in refusals.seen}
    assert reached - governed == set(), (
        f"these refusals the corpus reaches are named by neither of §11's tables: "
        f"{sorted(reached - governed)}. Give each a remedy, or an exemption AND a reason.")
    # An exemption is a CLAIM, so the body carries `"remedy": null` and does not merely omit the
    # member — an omission is indistinguishable from a code nobody has decided about yet, which is
    # exactly the state `bad_filter` was in.
    for entry in refusals.seen:
        if entry["code"] == "bad_filter":
            error = entry["body"]["error"]
            assert "remedy" in error and error["remedy"] is None, error


# ── 7 · the corpus covers what it claims to ──────────────────────────────────────────────────

def test_the_corpus_reaches_every_code_it_does_not_excuse(refusals):
    """A criterion may not quantify over a domain it does not enumerate.

    Every code `app/` emits is provoked here, or is in `UNPROVOKED` with a reason. The reasons
    are what stop this becoming the cheap way to make the assertions above green.
    """
    emitted = set(error_codes())
    reached = {e["code"] for e in refusals.seen}
    missing = sorted(emitted - reached - set(UNPROVOKED))
    assert not missing, (
        f"{len(missing)} error code(s) this server can emit are neither provoked nor excused: "
        f"{missing}")
    stale = sorted(set(UNPROVOKED) & reached)
    assert not stale, f"excused but reached after all — delete the excuse: {stale}"
    thin = sorted(c for c, why in UNPROVOKED.items() if len(why.split()) < 8)
    assert not thin, f"an excuse that says nothing is not an excuse: {thin}"
