"""SRV-FEAT-558: declared profiles, real confined processes, app-server lifecycle.

The deterministic peer copies recorded app-server frames while exercising transport,
process supervision and explicit refusal controls without model calls.
"""
import json
from concurrent.futures import ThreadPoolExecutor
import shlex
import time
from pathlib import Path

import jsonschema
import pytest
import requests

from conftest import _start_instance, stop_process
from test_agent import assert_reaped
from test_lane import build_repo, git, status_of


CAPTURE = Path(__file__).parent / 'fixtures/cli-app-server-codex-2026-09-18.json'


PEER = r'''
import copy, json, os, shlex, subprocess, sys, time
with open('cli-app-server-codex-2026-09-18.json') as source:
    capture = json.load(source)
assert capture['fixture_verified'] is True
records = [(row['direction'], row['phase'], json.loads(row['raw']))
           for row in capture['records']]
def recorded(phase, accepts):
    matches = [frame for direction, label, frame in records
               if direction == 'server' and label == phase and accepts(frame)]
    assert len(matches) == 1, (phase, matches)
    return copy.deepcopy(matches[0])
def response(phase, method):
    requests = [frame for direction, label, frame in records
                if direction == 'client' and label == phase and frame.get('method') == method]
    assert len(requests) == 1, (phase, method)
    return recorded(phase, lambda frame: frame.get('id') == requests[0]['id']
                    and 'method' not in frame)
def notification(phase, method):
    return recorded(phase, lambda frame: frame.get('method') == method)
def remap(value, replacements):
    if isinstance(value, dict):
        return {key: remap(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [remap(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
    return value
def runtime(frame, turn=None):
    replacements = {response('thread-start', 'thread/start')['result']['cwd']: os.getcwd()}
    for direction, phase, source in records:
        result = source.get('result', {})
        if direction == 'server' and 'thread' in result:
            replacements[result['thread']['id']] = thread_id
        if turn and direction == 'server' and 'turn' in result:
            replacements[result['turn']['id']] = turn
    return remap(frame, replacements)
def emit(frame):
    print(json.dumps(frame), flush=True)
def reply(request, phase, turn=None):
    frame = response(phase, request['method'])
    if request['method'] != 'initialize':
        frame = runtime(frame, turn)
    frame['id'] = request['id']
    emit(frame)
def complete(phase):
    if phase != 'interrupt-turn':
        message = recorded(phase, lambda frame: frame.get('method') == 'item/completed'
                           and frame['params']['item']['type'] == 'agentMessage'
                           and frame['params']['item'].get('phase') == 'final_answer')
        emit(runtime(message, turn_id))
    emit(runtime(notification(phase, 'turn/completed'), turn_id))
with open('codex-log.jsonl', 'a', buffering=1) as log:
    pending_command = ''
    pending_approval = None
    thread_id = 'thread-1'
    turn_id = None
    turn_number = 0
    home = os.environ.get('CODEX_HOME')
    config = open(os.path.join(home, 'config.toml')).read()
    log.write(json.dumps({'home': home, 'cwd': os.getcwd(), 'config': config}) + '\n')
    for line in sys.stdin:
        frame = json.loads(line)
        log.write(json.dumps(frame) + '\n')
        method = frame.get('method')
        if method == 'initialize':
            reply(frame, 'initialization-error' if 'fail' in sys.argv else 'initialize')
        elif method in ('thread/start', 'thread/resume'):
            if 'slow_start' in sys.argv:
                open('codex-start-pending.txt', 'w').write('pending')
                time.sleep(60)
            thread_id = frame['params'].get('threadId', 'thread-1')
            reply(frame, 'thread-start' if method == 'thread/start' else 'thread-resume')
        elif method == 'turn/start':
            thread_id = frame['params']['threadId']
            turn_number += 1
            turn_id = 'turn-' + str(turn_number)
            text = frame['params']['input'][0]['text']
            phase = ('approval-decline' if text.startswith('approve') else
                     'interrupt-turn' if text == 'hold' else 'second-turn')
            reply(frame, phase, turn_id)
            if text.startswith('approve'):
                outside = text.partition(':')[2]
                pending_command = "printf approved > approved.txt"
                if outside:
                    pending_command += "; printf escaped > " + shlex.quote(outside)
                approval = runtime(notification(phase, 'item/commandExecution/requestApproval'), turn_id)
                original_command = approval['params']['commandActions'][0]['command']
                approval = remap(approval, {original_command: pending_command})
                pending_approval = approval['id']
                emit(approval)
            elif text != 'hold':
                complete(phase)
        elif method == 'turn/interrupt':
            assert frame['params'] == {'threadId': thread_id, 'turnId': turn_id}
            reply(frame, 'interrupt-turn', turn_id)
            complete('interrupt-turn')
        elif pending_approval is not None and frame.get('id') == pending_approval:
            allowed = frame.get('result', {}).get('decision') == 'accept'
            if allowed:
                subprocess.run(['/bin/sh', '-c', pending_command], check=False)
            pending_approval = None
            complete('approval-accept' if allowed else 'approval-decline')
'''


def eventually(read, accepts, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if accepts(value):
            return value
        time.sleep(.03)
    raise AssertionError(f'condition not reached; last value: {value}')


@pytest.fixture
def profiles(tmp_path, serve):
    def build(fail=False, start_preflight=None, slow_start=False, permission_mode=None):
        root = build_repo(tmp_path / 'repo')
        credentials = tmp_path / 'codex-credentials'
        credentials.mkdir(exist_ok=True)
        (credentials / 'auth.json').write_text('{"token":"fixture-secret"}\n')
        (root / 'agent.py').write_text(PEER)
        (root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
        path = root / '.factory/factory.json'
        manifest = json.loads(path.read_text())
        command = ('/usr/bin/python3 agent.py' + (' fail' if fail else '')
                   + (' slow_start' if slow_start else ''))
        manifest.update(default_agent='gpt', agents={
            'gpt': {'driver': 'codex', 'command': command,
                    'models': ['gpt-test', 'gpt-other'], 'default_model': 'gpt-test',
                    'credentials_dir': str(credentials)},
            'claude': {'driver': 'claude', 'command': '/bin/cat',
                       'models': ['claude-test'], 'default_model': 'claude-test'},
        })
        if permission_mode is not None:
            manifest['agents']['gpt']['permission_mode'] = permission_mode
        if start_preflight is not None:
            manifest['workspaces']['core']['start_preflight'] = start_preflight
        path.write_text(json.dumps(manifest))
        git(root, 'add', '.')
        git(root, 'commit', '-m', 'agent profiles')
        srv = serve(root)
        response = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
        assert response.status_code == 201, response.text
        return root, srv, response.json()
    return build


def frames(lane):
    log = Path(lane['worktree']) / 'codex-log.jsonl'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_profile_selection_and_history(profiles):
    root, srv, lane = profiles()
    before = status_of(root)
    manifest = srv.get('/api/manifest').json()
    assert manifest['default_agent'] == 'gpt'
    assert manifest['agents']['gpt']['models'] == ['gpt-test', 'gpt-other']
    path = f"/api/lanes/{lane['lane']}"
    start = srv.post(path + '/runs', json={'agent': 'gpt', 'model': 'gpt-other', 'prompt': 'hello'})
    assert start.status_code == 201, start.text
    assert start.json()['session_id'] == 'thread-1'
    log = eventually(lambda: frames(lane), lambda f: any(x.get('method') == 'turn/start' for x in f))
    thread = next(f for f in log if f.get('method') == 'thread/start')
    assert thread['params']['model'] == 'gpt-other'
    assert log[0]['home'] == '/factory/agent-config'
    assert log[0]['cwd'] == lane['worktree']
    assert log[0]['config'] == (
        f'[projects."{lane["worktree"]}"]\ntrust_level = "trusted"\n')
    assert f'[projects."{root}"]' not in log[0]['config']
    assert srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(start.json()['pid'])
    rows = eventually(lambda: srv.get('/api/tickets/TCK-001/runs').json()['runs'],
                      lambda rows: rows and rows[0]['outcome'] != 'running')
    assert rows[0]['agent'] == 'gpt' and rows[0]['model'] == 'gpt-other'
    assert rows[0]['driver'] == 'codex'
    (Path(lane['worktree']) / 'codex-log.jsonl').unlink()
    released = srv.delete(path)
    assert released.status_code == 200, released.text
    stop_process(srv.proc)
    restarted = _start_instance(root, srv.log.parent / 'profile-restart.log')
    try:
        after = restarted.get('/api/tickets/TCK-001/runs')
        assert after.status_code == 200, after.text
        persisted = after.json()['runs'][0]
        assert (persisted['agent'], persisted['model'], persisted['driver']) == (
            'gpt', 'gpt-other', 'codex')
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()
    assert status_of(root) == before


def test_selected_profile_preflight_sees_scope_inside_confined_lane(profiles, tmp_path):
    sentinel = tmp_path / 'host-only-sentinel'
    sentinel.write_text('host readable')
    command = (f'test ! -r {shlex.quote(str(sentinel))} && '
               'test -f "$CODEX_HOME/config.toml" && '
               'test -f "$CODEX_HOME/auth.json" && '
               'test "$FACTORY_TICKET_ID:$FACTORY_WORKSPACE:$FACTORY_AGENT_PROFILE:'
               '$FACTORY_AGENT_MODEL" = "TCK-001:core:gpt:gpt-other" && '
               'printf reached > preflight-reached.txt')
    root, srv, lane = profiles(start_preflight={'command': command})
    start = srv.post(f"/api/lanes/{lane['lane']}/runs",
                     json={'agent': 'gpt', 'model': 'gpt-other', 'prompt': 'hello'})
    assert start.status_code == 201, start.text
    assert (Path(lane['worktree']) / 'preflight-reached.txt').read_text() == 'reached'
    assert srv.post(f"/api/lanes/{lane['lane']}/end", json={}).status_code == 202
    assert_reaped(start.json()['pid'])


def test_shutdown_after_preflight_completion_during_codex_startup(profiles):
    _, srv, lane = profiles(start_preflight={'command': 'printf done > preflight-done.txt'},
                            slow_start=True)
    worktree = Path(lane['worktree'])
    endpoint = f"/api/lanes/{lane['lane']}/runs"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(srv.post, endpoint, json={'prompt': 'hello'})
        eventually(lambda: (worktree / 'preflight-done.txt').exists()
                   and (worktree / 'codex-start-pending.txt').exists(), bool)
        stop_process(srv.proc)
        try:
            response = pending.result(timeout=7)
        except requests.RequestException:
            pass
        else:
            assert response.status_code != 201, response.text
    assert (worktree / 'preflight-done.txt').read_text() == 'done'


def test_codex_lifecycle(profiles):
    _, srv, lane = profiles()
    path = f"/api/lanes/{lane['lane']}"
    socket = srv.ws()
    socket.recv()
    socket.send({'m': 'subscribe', 'scope': {'lane': lane['lane']}})
    socket.wait_for('snapshot')
    start = srv.post(path + '/runs', json={'prompt': 'hold'})
    assert start.status_code == 201, start.text
    eventually(lambda: frames(lane), lambda f: any(x.get('method') == 'turn/start' for x in f))
    held = srv.post(path + '/messages', json={'text': 'second'})
    assert held.status_code == 200, held.text
    assert held.json()['status'] == 'held'
    interrupt = srv.post(path + '/interrupt', json={})
    assert interrupt.status_code == 200 and interrupt.json()['interrupted'], interrupt.text
    log = eventually(lambda: frames(lane), lambda f: sum(x.get('method') == 'turn/start' for x in f) == 2)
    transcript = socket.wait_for('transcript')
    schema = json.loads((Path(__file__).resolve().parents[2] / 'docs/technical/contracts/socket.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(transcript)
    methods = [x.get('method') for x in log]
    assert methods.index('turn/interrupt') < len(methods) - 1
    assert srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(start.json()['pid'])
    eventually(lambda: srv.get('/api/lanes').json()['lanes'], lambda rows: next(
        row for row in rows if row['lane'] == lane['lane'])['state']['process']
        not in ('running', 'blocked', 'idle'))
    resumed = srv.post(path + '/runs', json={'resume': True})
    assert resumed.status_code == 201, resumed.text
    log = eventually(lambda: frames(lane), lambda f: any(x.get('method') == 'thread/resume' for x in f))
    assert next(f for f in log if f.get('method') == 'thread/resume')['params']['threadId'] == 'thread-1'
    assert srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(resumed.json()['pid'])
    socket.close()


def test_codex_ask_all_uses_native_approval_for_commands(profiles):
    _, srv, lane = profiles(permission_mode='ask_all')
    path = f"/api/lanes/{lane['lane']}"
    started = srv.post(path + '/runs', json={'prompt': 'hello'})
    assert started.status_code == 201, started.text
    log = eventually(lambda: frames(lane),
                     lambda rows: any(row.get('method') == 'thread/start' for row in rows))
    thread = next(row for row in log if row.get('method') == 'thread/start')
    assert thread['params']['approvalPolicy'] == 'untrusted', thread
    assert srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(started.json()['pid'])


def test_codex_preserves_prompt_bytes_at_e18(profiles):
    _, srv, lane = profiles()
    path = f"/api/lanes/{lane['lane']}"
    prompt = '  quote"\nline\n  '
    start = srv.post(path + '/runs', json={'prompt': prompt})
    assert start.status_code == 201, start.text
    log = eventually(lambda: frames(lane),
                     lambda rows: any(row.get('method') == 'turn/start' for row in rows))
    turn = next(row for row in log if row.get('method') == 'turn/start')
    assert turn['params']['input'][0]['text'] == prompt
    assert srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(start.json()['pid'])


def test_codex_approval_and_confinement(profiles):
    root, srv, lane = profiles()
    developer_file = root / 'developer-config'
    developer_file.write_text('unchanged')
    credentials = root.parent / 'codex-credentials' / 'auth.json'
    credential_before = credentials.read_bytes()
    path = f"/api/lanes/{lane['lane']}"
    socket = srv.ws()
    socket.recv()
    socket.send({'m': 'subscribe', 'scope': {'lane': lane['lane']}})
    socket.wait_for('snapshot')

    started = srv.post(path + '/runs', json={'prompt': 'approve'})
    assert started.status_code == 201, started.text
    denied = socket.wait_for('permission.request')
    answer = srv.post('/api/permissions/decisions', json={
        'request_id': denied['request_id'], 'decision': 'deny',
    })
    assert answer.status_code == 200, answer.text
    captured = json.loads(CAPTURE.read_text())
    requests = [json.loads(row['raw']) for row in captured['records']
                if row['direction'] == 'server' and row['phase'] == 'approval-decline']
    approval_id = next(frame['id'] for frame in requests
                       if frame.get('method') == 'item/commandExecution/requestApproval')
    eventually(lambda: frames(lane), lambda rows: any(
        row.get('id') == approval_id
        and row.get('result', {}).get('decision') == 'decline' for row in rows))
    assert not (Path(lane['worktree']) / 'approved.txt').exists()

    sent = srv.post(path + '/messages', json={'text': f'approve:{developer_file}'})
    assert sent.status_code == 200, sent.text
    allowed = socket.wait_for('permission.request')
    answer = srv.post('/api/permissions/decisions', json={
        'request_id': allowed['request_id'], 'decision': 'once',
    })
    assert answer.status_code == 200, answer.text
    eventually(lambda: (Path(lane['worktree']) / 'approved.txt').exists(), bool)
    assert (Path(lane['worktree']) / 'approved.txt').read_text() == 'approved'
    assert developer_file.read_text() == 'unchanged'
    assert credentials.read_bytes() == credential_before
    assert srv.post(path + '/end', json={}).status_code == 202
    assert_reaped(started.json()['pid'])
    socket.close()


@pytest.mark.parametrize('body', [{'agent': 'unknown'}, {'agent': '../escape'},
    {'agent': 'gpt', 'model': 'claude-test'}, {'agent': []}, {'model': ''}, {'model': 7}])
def test_invalid_selection_does_not_spawn(profiles, body):
    _, srv, lane = profiles()
    response = srv.post(f"/api/lanes/{lane['lane']}/runs", json=body)
    assert response.status_code == 400, response.text
    assert frames(lane) == []


def test_codex_initialization_error_is_not_a_started_run(profiles):
    _, srv, lane = profiles(fail=True)
    response = srv.post(f"/api/lanes/{lane['lane']}/runs", json={})
    assert response.status_code == 502, response.text
    assert response.json()['error']['code'] == 'agent_start_failed'
    capture = json.loads(CAPTURE.read_text())
    errors = [json.loads(row['raw']) for row in capture['records']
              if row['direction'] == 'server' and row['phase'] == 'initialization-error']
    recorded_error = next(frame['error']['message'] for frame in errors if 'error' in frame)
    assert recorded_error in response.text
