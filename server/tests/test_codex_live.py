"""Opt-in authenticated Codex smoke through the actual Factory confinement boundary.

FACTORY_CODEX_BINARY, FACTORY_CODEX_MODEL and FACTORY_CODEX_CREDENTIALS must be
explicit; the ordinary suite neither reads developer credentials nor spends tokens.
"""
import json
import os
from pathlib import Path

import pytest
import requests

from test_agent import assert_reaped
from test_agent_profiles import eventually
from test_lane import build_repo, git, status_of


def test_real_codex_tool_turn_and_resume(tmp_path, serve):
    binary = os.environ.get('FACTORY_CODEX_BINARY')
    model = os.environ.get('FACTORY_CODEX_MODEL')
    credentials = os.environ.get('FACTORY_CODEX_CREDENTIALS')
    if not all((binary, model, credentials)):
        pytest.skip('explicit Codex executable, model and credentials required')
    binary_path = Path(binary).resolve()
    companion = binary_path.with_name('codex-code-mode-host')
    assert binary_path.is_file() and os.access(binary_path, os.X_OK), binary_path
    assert companion.is_file() and os.access(companion, os.X_OK), companion
    root = build_repo(tmp_path / 'repo')
    manifest_path = root / '.factory/factory.json'
    manifest = json.loads(manifest_path.read_text())
    manifest.update(default_agent='codex', agents={'codex': {
        'driver': 'codex', 'command': 'codex',
        'models': [model], 'default_model': model,
        'credentials_dir': credentials,
    }})
    manifest['workspaces']['core']['toolchain'] = [str(companion)]
    manifest_path.write_text(json.dumps(manifest))
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'live Codex fixture')
    before = status_of(root)
    srv = serve(root, PATH=str(binary_path.parent) + os.pathsep + os.environ['PATH'])
    claimed = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()
    path = f"/api/lanes/{lane['lane']}"
    run = None
    try:
        started = requests.post(srv.base + path + '/runs', timeout=40, json={
            'agent': 'codex', 'model': model,
            'prompt': 'Use the shell to run pwd, then reply exactly FACTORY_CODEX_PONG. Do not edit files.',
        })
        assert started.status_code == 201, started.text
        run = started.json()
        def entries():
            response = srv.get(f"/api/runs/{run['run_id']}/transcript")
            return response.json().get('entries', [])
        transcript = eventually(entries, lambda rows: any(e.get('kind') == 'turn' for e in rows), seconds=120)
        assert any(e.get('kind') == 'prose' and 'FACTORY_CODEX_PONG' in e.get('text', '') for e in transcript), transcript
        assert any(e.get('kind') == 'tool' and e.get('ok') for e in transcript), transcript
        assert srv.post(path + '/end', json={}).status_code == 202
        assert_reaped(run['pid'])
        resumed = requests.post(srv.base + path + '/runs', timeout=40, json={'agent': 'codex', 'resume': True})
        assert resumed.status_code == 201, resumed.text
        assert resumed.json()['session_id'] == run['session_id']
        run = resumed.json()
        assert srv.post(path + '/end', json={}).status_code == 202
        assert_reaped(run['pid'])
        assert status_of(root) == before
    finally:
        if run:
            srv.post(path + '/kill', json={'grace_s': 1})
