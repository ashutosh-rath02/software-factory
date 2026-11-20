import json
import time
from pathlib import Path

import pytest

from conftest import _start_instance, stop_process
from test_agent import assert_reaped
from test_agent_profiles import CAPTURE, PEER
from test_lane import build_repo, git


AGENT = '''import json, os, sys
from pathlib import Path
Path('agent-started.json').write_text(json.dumps({'argv': __import__('sys').argv[1:], 'tree': {k: os.environ.get(k) for k in ('FACTORY_TREE', 'FACTORY_TREE_AGENTS', 'FACTORY_TREE_DECIDER')}, 'unrelated': os.environ.get('FACTORY_UNRELATED_SENTINEL'), 'env': sorted(os.environ)}))
sys.stdin.read()
'''


def eventually(read, accepts, seconds=5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = read()
        if accepts(value):
            return value
        time.sleep(.05)
    raise AssertionError(f'condition not reached: {value!r}')


@pytest.fixture
def tree_repo(tmp_path, serve):
    count = 0
    def start(trees=True, preflight=False, files=('advisor', 'worker'), **server_env):
        nonlocal count
        count += 1
        root = build_repo(tmp_path / f'repo-{count}', lanes_host=2,
                          tickets=('TCK-001', 'TCK-002', 'TCK-003'))
        (root / 'agent.py').write_text(AGENT)
        (root / 'codex.py').write_text(PEER)
        (root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
        for name in files:
            for path in (root / '.claude/agents' / f'{name}.md',
                         root / '.codex/agents' / f'{name}.toml'):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
        path = root / '.factory/factory.json'
        manifest = json.loads(path.read_text())
        manifest['agents'] = {
            'claude': {'driver': 'claude', 'command': '/usr/bin/python3 agent.py',
                       'models': ['test'], 'default_model': 'test'},
            'codex': {'driver': 'codex', 'command': '/usr/bin/python3 codex.py',
                      'models': ['test'], 'default_model': 'test'}}
        manifest['default_agent'] = 'claude'
        if trees:
            manifest['trees'] = {'full': {'decider': 'rules', 'agents': ['advisor', 'worker']},
                                 'off': {'decider': 'rules', 'agents': []}}
            manifest['default_tree'] = 'full'
        if preflight:
            manifest['workspaces']['core']['start_preflight'] = {
                'command': 'printf reached > preflight-started.txt'}
        path.write_text(json.dumps(manifest))
        git(root, 'add', '.')
        git(root, 'commit', '-m', 'fixture')
        return root, serve(root, **server_env)
    return start


def claim(srv, ticket='TCK-001'):
    response = srv.post('/api/lanes', json={'ticket_id': ticket, 'workspace': 'core'})
    assert response.status_code == 201, response.text
    return response.json()


def start_run(srv, lane, body):
    return srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)


def started(lane):
    path = Path(lane['worktree']) / 'agent-started.json'
    return json.loads(path.read_text()) if path.exists() else None


def test_selected_preset_reaches_the_lane_environment(tree_repo):
    _, srv = tree_repo()
    full, off = claim(srv), claim(srv, 'TCK-002')
    first = start_run(srv, full, {'tree': 'full', 'prompt': 'hello'})
    second = start_run(srv, off, {'tree': 'off', 'prompt': 'hello'})
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    one = eventually(lambda: started(full), bool)
    two = eventually(lambda: started(off), bool)
    assert one['tree'] == {'FACTORY_TREE': 'full', 'FACTORY_TREE_AGENTS': 'advisor worker',
                           'FACTORY_TREE_DECIDER': 'rules'}
    assert two['tree'] == {'FACTORY_TREE': 'off', 'FACTORY_TREE_AGENTS': '',
                           'FACTORY_TREE_DECIDER': 'rules'}
    assert one['tree'] != two['tree']


def test_default_and_absent_trees(tree_repo):
    _, srv = tree_repo()
    lane = claim(srv)
    selected = start_run(srv, lane, {'prompt': 'hello'})
    assert selected.status_code == 201, selected.text
    assert selected.json()['tree'] == {'name': 'full', 'decider': 'rules',
                                       'agents': ['advisor', 'worker']}
    assert eventually(lambda: started(lane), bool)['tree']['FACTORY_TREE'] == 'full'
    root2, srv2 = tree_repo(trees=False, FACTORY_TREE_AGENTS='worker',
                            FACTORY_TREE_DECIDER='external', FACTORY_UNRELATED_SENTINEL='preserved')
    legacy = claim(srv2)
    started_legacy = start_run(srv2, legacy, {'prompt': 'hello'})
    assert started_legacy.status_code == 201, started_legacy.text
    data = eventually(lambda: started(legacy), bool)
    assert data['tree']['FACTORY_TREE'] == 'off'
    assert data['tree']['FACTORY_TREE_AGENTS'] is None
    assert data['tree']['FACTORY_TREE_DECIDER'] is None
    assert data['unrelated'] == 'preserved'
    assert data['argv'][:-1] == eventually(lambda: started(lane), bool)['argv'][:-1]


def test_run_history_records_the_tree_across_restart_and_reuse(tree_repo):
    root, srv = tree_repo()
    claim(srv, 'TCK-002')
    lane = claim(srv)
    first = start_run(srv, lane, {'tree': 'full', 'prompt': 'hello'})
    assert first.status_code == 201, first.text
    assert srv.post(f"/api/lanes/{lane['lane']}/end", json={}).status_code == 202
    assert_reaped(first.json()['pid'])
    second = start_run(srv, lane, {'tree': 'off', 'prompt': 'hello'})
    assert second.status_code == 201, second.text
    assert eventually(lambda: started(lane),
                      lambda data: data is not None and data['tree']['FACTORY_TREE'] == 'off')
    rows = eventually(lambda: srv.get('/api/tickets/TCK-001/runs').json()['runs'],
                      lambda rows: len(rows) >= 2)
    by_id = {r['run_id']: r['tree'] for r in rows}
    assert by_id[first.json()['run_id']]['name'] == 'full'
    assert by_id[second.json()['run_id']]['name'] == 'off'
    (Path(lane['worktree']) / 'agent-started.json').unlink()
    released = srv.delete(f"/api/lanes/{lane['lane']}?force=true")
    assert released.status_code == 200, released.text
    assert not any(row['lane'] == lane['lane'] for row in srv.get('/api/lanes').json()['lanes']), released.text
    assert not Path(lane['worktree']).exists(), released.text
    stop_process(srv.proc)
    restarted = _start_instance(root, srv.log.parent / 'tree-restart.log')
    try:
        persisted = restarted.get('/api/tickets/TCK-001/runs').json()['runs']
        assert {r['run_id']: r['tree'] for r in persisted} == by_id
        third_lane = claim(restarted, 'TCK-003')
        assert third_lane['lane'] == lane['lane']
        third = start_run(restarted, third_lane, {'tree': 'full', 'prompt': 'hello'})
        assert third.status_code == 201, third.text
        third_rows = eventually(lambda: restarted.get('/api/tickets/TCK-003/runs').json()['runs'],
                                lambda rows: any(r['run_id'] == third.json()['run_id'] for r in rows))
        assert third_rows[0]['tree']['name'] == 'full'
        assert {r['run_id']: r['tree'] for r in restarted.get('/api/tickets/TCK-001/runs').json()['runs']} == by_id
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


def test_missing_agent_file_refuses_per_driver(tree_repo):
    root, srv = tree_repo(preflight=True, files=('advisor',))
    lane = claim(srv)
    missing = start_run(srv, lane, {'agent': 'claude', 'tree': 'full', 'prompt': 'hello'})
    assert missing.status_code == 409, missing.text
    assert missing.json()['error']['code'] == 'tree_agent_missing'
    assert '.claude/agents/worker.md' in missing.text
    assert not (Path(lane['worktree']) / 'preflight-started.txt').exists()
    assert started(lane) is None
    assert not list((root / '.factory/runs').glob('run-*/meta.json'))
    (root / '.codex/agents/worker.toml').parent.mkdir(parents=True, exist_ok=True)
    (root / '.codex/agents/worker.toml').write_text('worker')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'codex agent file')
    other = claim(srv, 'TCK-002')
    codex = start_run(srv, other, {'agent': 'codex', 'tree': 'full', 'prompt': 'hello'})
    assert codex.status_code == 201, codex.text
    assert eventually(lambda: (Path(other['worktree']) / 'codex-log.jsonl').exists(), bool)


def test_invalid_tree_selection_never_spawns(tree_repo):
    root, srv = tree_repo(preflight=True)
    lane = claim(srv)
    for body in ({'tree': 'unknown'}, {'tree': 4}):
        refusal = start_run(srv, lane, body)
        assert refusal.status_code == 400, refusal.text
        assert refusal.json()['error']['code'] == 'invalid_agent_selection'
        assert started(lane) is None
        assert not (Path(lane['worktree']) / 'preflight-started.txt').exists()
    assert not list((root / '.factory/runs').glob('run-*/meta.json'))
    path = root / '.factory/factory.json'
    manifest = json.loads(path.read_text())
    manifest['trees']['full']['decider'] = 'external'
    path.write_text(json.dumps(manifest))
    invalid = _start_instance(root, srv.log.parent / 'invalid-tree.log')
    try:
        response = invalid.get('/api/manifest')
        assert response.status_code == 422, response.text
        assert response.json()['error']['detail']['pointer'] == '/trees/full/decider'
    finally:
        stop_process(invalid.proc)
        invalid.handle.close()


def test_a_selected_agent_symlink_outside_the_lane_is_missing(tree_repo, tmp_path):
    root, srv = tree_repo(preflight=True, files=('advisor',))
    outside = tmp_path / 'unbound-agent.md'
    outside.write_text('host only')
    link = root / '.claude/agents/worker.md'
    link.symlink_to(outside)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'unbound agent symlink')
    lane = claim(srv)
    refusal = start_run(srv, lane, {'agent': 'claude', 'tree': 'full', 'prompt': 'hello'})
    assert refusal.status_code == 409, refusal.text
    assert refusal.json()['error']['code'] == 'tree_agent_missing'
    assert '.claude/agents/worker.md' in refusal.text
    assert not (Path(lane['worktree']) / 'preflight-started.txt').exists()
    assert started(lane) is None


def test_a_selected_agent_parent_symlink_outside_the_lane_is_missing(tree_repo, tmp_path):
    root, srv = tree_repo(preflight=True)
    outside = tmp_path / 'unbound-agents'
    outside.mkdir()
    for name in ('advisor', 'worker'):
        (outside / f'{name}.md').write_text(name)
    agents = root / '.claude/agents'
    agents.rename(root / '.claude/agents-local')
    agents.symlink_to(outside, target_is_directory=True)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'unbound agent directory')
    lane = claim(srv)
    refusal = start_run(srv, lane, {'agent': 'claude', 'tree': 'full', 'prompt': 'hello'})
    assert refusal.status_code == 409, refusal.text
    assert refusal.json()['error']['code'] == 'tree_agent_missing'
    assert '.claude/agents/advisor.md' in refusal.text
    assert not (Path(lane['worktree']) / 'preflight-started.txt').exists()
    assert started(lane) is None


def test_this_repository_declares_its_presets():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / '.factory/factory.json').read_text())
    assert manifest['default_tree'] == 'full'
    assert manifest['trees'] == {
        'full': {'decider': 'rules', 'agents': ['advisor', 'worker', 'explorer', 'researcher']},
        'lean': {'decider': 'rules', 'agents': ['advisor']},
        'off': {'decider': 'rules', 'agents': []}}
    skill = (root / '.claude/skills/run-ticket/SKILL.md').read_text()
    section = skill.split('## 0b. The agent tree', 1)[1].split('## 1. SPEC', 1)[0]
    assert '$FACTORY_TREE_AGENTS' in section
    assert 'advisor file is absent' not in section
