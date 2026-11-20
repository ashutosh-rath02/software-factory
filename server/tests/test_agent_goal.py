"""SRV-FEAT-593: goals and durable provider usage over real HTTP."""
import json
import time
from pathlib import Path

import pytest

from conftest import _start_instance, stop_process
from test_agent import assert_reaped
from test_agent_profiles import CAPTURE, PEER, eventually, frames
from test_agent_tree import AGENT, claim, start_run, started
from test_lane import build_repo, git


GOAL_CAPTURE = Path(__file__).parent / 'fixtures/cli-app-server-goal-2026-10-11.json'


EXTRA = r'''
            if text.startswith('goal:'):
                status = text.split(':', 1)[1]
                if status == 'blocked_permission':
                    status = 'blocked'
                goal_notice = copy.deepcopy(next(f for direction, f in goal_records
                    if direction == 'server' and f.get('method') == 'thread/goal/updated'))
                goal_notice['params']['threadId'] = thread_id
                goal_notice['params']['turnId'] = turn_id
                goal_notice['params']['goal'].update(threadId=thread_id, status=status)
                emit(goal_notice)
                if text == 'goal:blocked_permission':
                    approval = runtime(notification_for_permission, turn_id)
                    pending_approval = approval['id']
                    emit(approval)
            elif text.startswith('usage:'):
                index = int(text.split(':', 1)[1])
                usages = [source for direction, phase, source in records
                          if direction == 'server'
                          and source.get('method') == 'thread/tokenUsage/updated']
                emit(runtime(usages[index], turn_id))
            log.write(json.dumps({'emitted': text}) + '\n')
'''
GOAL_PEER = PEER.replace("with open('codex-log.jsonl', 'a', buffering=1) as log:", """
with open('cli-app-server-goal-2026-10-11.json') as source:
    goal_records = [(row['direction'], json.loads(row['raw']))
                    for row in json.load(source)['records']]
def goal_reply(request):
    captured = next(frame for direction, frame in goal_records
                    if direction == 'client' and frame.get('method') == request['method'])
    assert set(request['params']) == set(captured['params'])
    result = copy.deepcopy(next(frame for direction, frame in goal_records
                               if direction == 'server' and frame.get('id') == captured['id']))
    result['id'] = request['id']
    if request['method'] == 'thread/goal/set':
        result['result']['goal'].update(request['params'])
    emit(result)
notification_for_permission = notification('approval-decline',
    'item/commandExecution/requestApproval')
with open('codex-log.jsonl', 'a', buffering=1) as log:""")
GOAL_PEER = GOAL_PEER.replace("import copy, json, os, shlex, subprocess, sys, time",
                              "import copy, json, os, shlex, subprocess, sys, time")
GOAL_PEER = GOAL_PEER.replace("    pending_command = ''", "    open('progress-token.txt', 'w').write(os.environ['FACTORY_PROGRESS_TOKEN'])\n    pending_command = ''")
GOAL_PEER = GOAL_PEER.replace("        elif method == 'turn/start':", """
        elif method == 'thread/goal/set':
            if 'reject_goal' in sys.argv:
                rejection = copy.deepcopy(next(source for direction, source in goal_records
                    if direction == 'server' and source.get('id') == 5))
                rejection['id'] = frame['id']
                emit(rejection)
            else:
                goal_reply(frame)
        elif method == 'turn/start':""")
GOAL_PEER = GOAL_PEER.replace("                complete(phase)\n        elif method == 'turn/interrupt':", "                if text == 'autonomous':\n                    complete(phase)\n                    turn_id = 'turn-' + str(turn_number + 1)\n                    emit(runtime(notification('second-turn', 'turn/started'), turn_id))\n                    log.write(json.dumps({'emitted': 'autonomous-started'}) + '\\n')\n                elif not text.startswith('goal:'):\n                    complete(phase)\n" + EXTRA + "        elif method == 'turn/interrupt':")


@pytest.fixture
def goal_repo(tmp_path, serve):
    count = 0
    def build(reject_goal=False):
        nonlocal count
        count += 1
        root = build_repo(tmp_path / f'repo-{count}', lanes_host=2,
                          tickets=('TCK-001', 'TCK-002', 'TCK-003'))
        (root / 'codex.py').write_text(GOAL_PEER)
        (root / 'agent.py').write_text(AGENT)
        (root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
        (root / GOAL_CAPTURE.name).write_bytes(GOAL_CAPTURE.read_bytes())
        path = root / '.factory/factory.json'
        manifest = json.loads(path.read_text())
        manifest['agents'] = {
            'codex': {'driver': 'codex', 'command': '/usr/bin/python3 codex.py'
                      + (' reject_goal' if reject_goal else ''),
                      'models': ['test'], 'default_model': 'test'},
            'claude': {'driver': 'claude', 'command': '/usr/bin/python3 agent.py',
                       'models': ['test'], 'default_model': 'test'}}
        manifest['default_agent'] = 'codex'
        manifest['trees'] = {
            'goal': {'decider': 'rules', 'agents': [], 'continuation': 'goal',
                     'token_budget': 500000},
            'off': {'decider': 'rules', 'agents': []}}
        manifest['default_tree'] = 'off'
        manifest['workspaces']['core']['start_preflight'] = {
            'command': 'printf reached > preflight-started.txt'}
        path.write_text(json.dumps(manifest))
        git(root, 'add', '.')
        git(root, 'commit', '-m', 'goal fixture')
        return root, serve(root)
    return build


def requests_for(lane, method):
    return [frame for frame in frames(lane) if frame.get('method') == method]


def emitted(lane, prompt):
    eventually(lambda: frames(lane), lambda rows: any(r.get('emitted') == prompt for r in rows))
    time.sleep(.2)


def say(srv, lane, text):
    response = srv.post(f"/api/lanes/{lane['lane']}/messages", json={'text': text})
    assert response.status_code == 200, response.text
    emitted(lane, text)


def end(srv, lane, run):
    assert srv.post(f"/api/lanes/{lane['lane']}/end", json={}).status_code == 202
    assert_reaped(run['pid'])


def test_goal_is_set_once_per_run_from_the_preset(goal_repo):
    _, srv = goal_repo()
    lane = claim(srv)
    run = start_run(srv, lane, {'tree': 'goal', 'prompt': 'hello'})
    assert run.status_code == 201, run.text
    emitted(lane, 'hello')
    goals = requests_for(lane, 'thread/goal/set')
    assert len(goals) == 1, goals
    wire = frames(lane)
    assert wire.index(goals[0]) < next(i for i, frame in enumerate(wire)
                                     if frame.get('method') == 'turn/start')
    params = goals[0]['params']
    assert params['threadId'] == run.json()['session_id']
    assert params['status'] == 'active'
    assert params['tokenBudget'] == 500000
    assert 'TCK-001' in params['objective']
    assert 'handover' in params['objective'].lower()
    say(srv, lane, 'second')
    assert len(requests_for(lane, 'thread/goal/set')) == 1
    other = claim(srv, 'TCK-002')
    plain = start_run(srv, other, {'tree': 'off', 'prompt': 'hello'})
    assert plain.status_code == 201, plain.text
    emitted(other, 'hello')
    assert requests_for(other, 'thread/goal/set') == []


def test_goal_status_maps_to_needs_you(goal_repo):
    for status in ('active', 'complete', 'blocked', 'budgetLimited', 'usageLimited'):
        _, srv = goal_repo()
        lane = claim(srv)
        run = start_run(srv, lane, {'tree': 'goal', 'prompt': 'goal:' + status})
        assert run.status_code == 201, run.text
        emitted(lane, 'goal:' + status)
        row = next(row for row in srv.get('/api/lanes').json()['lanes']
                   if row['lane'] == lane['lane'])
        expected = status in ('blocked', 'budgetLimited', 'usageLimited')
        assert row['needs_you'] is expected, (status, row)
        if expected:
            assert row['state']['blocked']['reason'] == status, row


def test_permission_request_remains_visible_during_blocked_goal(goal_repo):
    _, srv = goal_repo()
    lane = claim(srv)
    run = start_run(srv, lane, {'tree': 'goal', 'prompt': 'goal:blocked_permission'})
    assert run.status_code == 201, run.text
    emitted(lane, 'goal:blocked_permission')
    row = next(row for row in srv.get('/api/lanes').json()['lanes']
               if row['lane'] == lane['lane'])
    blocked = row['state']['blocked']
    assert blocked['reason'] == 'permission', blocked
    assert blocked['request_id'], blocked
    answer = srv.post('/api/permissions/decisions', json={
        'request_id': blocked['request_id'], 'decision': 'deny'})
    assert answer.status_code == 200, answer.text
    eventually(lambda: next(row for row in srv.get('/api/lanes').json()['lanes']
                            if row['lane'] == lane['lane']),
               lambda row: row['state']['blocked'] == {
                   'reason': 'blocked', 'request_id': ''})


def test_goal_rejection_refuses_start_before_first_turn(goal_repo):
    _, srv = goal_repo(reject_goal=True)
    lane = claim(srv)
    refused = start_run(srv, lane, {'tree': 'goal', 'prompt': 'hello'})
    assert refused.status_code == 502, refused.text
    assert refused.json()['error']['code'] == 'agent_start_failed'
    assert 'thread not found' in refused.text
    calls = frames(lane)
    assert len([frame for frame in calls if frame.get('method') == 'thread/goal/set']) == 1
    assert not any(frame.get('method') == 'turn/start' for frame in calls)


def test_stage_compaction_is_refused_until_its_own_ticket(goal_repo):
    root, srv = goal_repo()
    for ticket, preset in (('TCK-001', 'off'), ('TCK-002', 'goal')):
        lane = claim(srv, ticket)
        run = start_run(srv, lane, {'tree': preset, 'prompt': 'hello'})
        assert run.status_code == 201, run.text
        emitted(lane, 'hello')
        token = (Path(lane['worktree']) / 'progress-token.txt').read_text()
        assert srv.post('/api/progress', json={'token': token, 'stage': 'SPEC'}).status_code == 202
        time.sleep(.2)
        assert requests_for(lane, 'thread/compact/start') == []
    path = root / '.factory/factory.json'
    manifest = json.loads(path.read_text())
    manifest['trees']['stage_compact'] = {
        'decider': 'rules', 'agents': [], 'compact_on_stage': True}
    path.write_text(json.dumps(manifest))
    invalid = _start_instance(root, srv.log.parent / 'invalid-stage-compaction.log')
    try:
        response = invalid.get('/api/manifest')
        assert response.status_code == 422, response.text
        assert response.json()['error']['detail']['pointer'] == (
            '/trees/stage_compact/compact_on_stage')
    finally:
        stop_process(invalid.proc)
        invalid.handle.close()


def test_provider_started_goal_continuation_is_active(goal_repo):
    _, srv = goal_repo()
    lane = claim(srv)
    run = start_run(srv, lane, {'tree': 'goal', 'prompt': 'autonomous'})
    assert run.status_code == 201, run.text
    emitted(lane, 'autonomous-started')
    message = srv.post(f"/api/lanes/{lane['lane']}/messages", json={'text': 'wait for me'})
    assert message.status_code == 200, message.text
    assert message.json()['status'] == 'held'
    interrupted = srv.post(f"/api/lanes/{lane['lane']}/interrupt", json={})
    assert interrupted.status_code == 200, interrupted.text
    assert interrupted.json()['interrupted'] is True
    calls = requests_for(lane, 'turn/interrupt')
    assert len(calls) == 1
    assert calls[0]['params']['turnId'] == 'turn-2'


def test_usage_survives_the_lane(goal_repo):
    root, srv = goal_repo()
    claim(srv, 'TCK-002')
    lane = claim(srv)
    run = start_run(srv, lane, {'tree': 'off', 'prompt': 'usage:0'})
    assert run.status_code == 201, run.text
    run = run.json()
    meta = root / '.factory/runs' / run['run_id'] / 'meta.json'
    expected = {'input_tokens': 14435, 'cached_input_tokens': 11136,
                'output_tokens': 106, 'reasoning_output_tokens': 16,
                'effective_tokens': 14435 - 11136 + .1 * 11136 + 106}
    eventually(lambda: json.loads(meta.read_text()).get('usage'), lambda usage: usage == expected)
    say(srv, lane, 'usage:1')
    expected = {'input_tokens': 29213, 'cached_input_tokens': 25344,
                'output_tokens': 124, 'reasoning_output_tokens': 16,
                'effective_tokens': 29213 - 25344 + .1 * 25344 + 124}
    eventually(lambda: json.loads(meta.read_text()).get('usage'), lambda usage: usage == expected)
    rows = srv.get('/api/tickets/TCK-001/runs').json()['runs']
    assert next(row for row in rows if row['run_id'] == run['run_id'])['usage'] == expected
    end(srv, lane, run)
    for name in ('codex-log.jsonl', 'preflight-started.txt', 'progress-token.txt'):
        (Path(lane['worktree']) / name).unlink()
    released = srv.delete(f"/api/lanes/{lane['lane']}?force=true")
    assert released.status_code == 200, released.text
    assert not Path(lane['worktree']).exists()
    stop_process(srv.proc)
    restarted = _start_instance(root, srv.log.parent / 'usage-restart.log')
    try:
        other = claim(restarted, 'TCK-003')
        assert other['lane'] == lane['lane']
        fresh = start_run(restarted, other, {'tree': 'off', 'prompt': 'usage:0'})
        assert fresh.status_code == 201, fresh.text
        emitted(other, 'usage:0')
        persisted = restarted.get('/api/tickets/TCK-001/runs').json()['runs']
        assert next(row for row in persisted if row['run_id'] == run['run_id'])['usage'] == expected
        assert json.loads(meta.read_text())['usage'] == expected
    finally:
        stop_process(restarted.proc)
        restarted.handle.close()


def test_unsupported_continuation_never_spawns(goal_repo):
    root, srv = goal_repo()
    lane = claim(srv)
    for preset, field in (('goal', 'continuation'),):
        refusal = start_run(srv, lane, {'agent': 'claude', 'tree': preset, 'prompt': 'hello'})
        assert refusal.status_code == 409, refusal.text
        assert field in refusal.text
        assert started(lane) is None
        assert not (Path(lane['worktree']) / 'preflight-started.txt').exists()
        assert not list((root / '.factory/runs').glob('run-*/meta.json'))
    path = root / '.factory/factory.json'
    manifest = json.loads(path.read_text())
    manifest['trees']['goal']['continuation'] = 'loop'
    path.write_text(json.dumps(manifest))
    invalid = _start_instance(root, srv.log.parent / 'invalid-continuation.log')
    try:
        response = invalid.get('/api/manifest')
        assert response.status_code == 422, response.text
        assert response.json()['error']['detail']['pointer'] == '/trees/goal/continuation'
    finally:
        stop_process(invalid.proc)
        invalid.handle.close()
