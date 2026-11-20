import json

import pytest

from test_lane import git
from test_lane_command import collect
from test_roles import AGENT, PROMPT, ROLE, TURN_END
from test_ticket_router import HOLD, checkout_state, eventually, router, step


RESOURCE = {'chamber': {'exclusive': True, 'needed_by': ['core'],
                        'description': 'one virtual fixture resource'}}


def prepare_roles(root):
    manifest_path = root / '.factory/factory.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['roles'] = ROLE
    manifest_path.write_text(json.dumps(manifest, indent=2))
    prompt = root / '.factory/prompts/reviewer.md'
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(PROMPT)
    (root / 'agent-trap.sh').write_text(AGENT.replace(
        'log=./agent-log.txt',
        '/bin/mkdir -p .router-observation\nlog=./.router-observation/agent-log.txt'))
    git(root, 'add', '-A')


def lane_snapshot(env):
    response = env.srv.get('/api/lanes')
    assert response.status_code == 200, response.text
    return response.json()


def resource(snapshot):
    return next(row for row in snapshot['resources'] if row['name'] == 'chamber')


def lane(snapshot, name):
    return next(row for row in snapshot['lanes'] if row['lane'] == name)


def finish_agent_turn(env):
    observation = env.worktree / '.router-observation'
    observation.mkdir(exist_ok=True)
    (observation / 'turn.jsonl').write_text(TURN_END)
    log = observation / 'agent-log.txt'
    started = env.srv.post(f"/api/lanes/{env.lane['lane']}/runs", json={
        'prompt': 'emit:.router-observation/turn.jsonl'})
    assert started.status_code == 201, started.text
    eventually(lambda: log.read_text() if log.exists() else '',
               lambda text: 'emitted=.router-observation/turn.jsonl' in text)
    idle = eventually(lambda: lane_snapshot(env), lambda snapshot:
                      lane(snapshot, env.lane['lane'])['state']['process'] == 'idle')
    assert resource(idle)['held_by'] == env.lane['lane']
    assert resource(idle)['holder_alive'] is True
    ended = env.srv.post(f"/api/lanes/{env.lane['lane']}/messages", json={'text': 'bye'})
    assert ended.status_code == 200, ended.text
    stopped = eventually(lambda: lane_snapshot(env), lambda snapshot:
                         resource(snapshot)['holder_alive'] is False)
    assert resource(stopped)['held_by'] == env.lane['lane']


@pytest.mark.parametrize('endpoint', ['commands', 'routing'])
@pytest.mark.parametrize('close_output', [False, True], ids=['open-output', 'closed-output'])
def test_ordinary_command_keeps_resource_after_prior_agent_turn_ends(router, endpoint, close_output):
    body = ('exec 1>&- 2>&-\n/bin/sleep .15\n'
            "printf 'closed\\n' > .router-observation/output-closed\n"
            if close_output else '') + HOLD
    env = router(build_body=body, resources=RESOURCE, prepare=prepare_roles)
    finish_agent_turn(env)
    socket = env.subscribed() if endpoint == 'commands' else None
    if endpoint == 'routing':
        env.submit('retain-resource', [step('build')])
    else:
        started = env.srv.post(f"/api/lanes/{env.lane['lane']}/commands",
                               json={'which': 'build'})
        assert started.status_code == 202, started.text
    try:
        eventually(env.invocations, lambda rows: rows == ['build'])
        if close_output:
            eventually(lambda: (env.worktree / '.router-observation/output-closed').exists(), bool)
        running = lane_snapshot(env)
        assert resource(running)['held_by'] == env.lane['lane']
        assert resource(running)['holder_alive'] is True
        if endpoint == 'routing':
            current = env.read('retain-resource')
            assert current['state'] == 'running' and current['receipts'] == []
        claimed = env.srv.post('/api/lanes', json={
            'ticket_id': 'TCK-001', 'workspace': 'core', 'role': 'reviewer'})
        assert claimed.status_code == 201, claimed.text
        role_lane = claimed.json()['lane']
        env.before = checkout_state(env.root)
        queued = lane_snapshot(env)
        assert lane(queued, role_lane)['state']['process'] == 'queued', (
            f'{endpoint} is still running, but the same-ticket role took its resource: {queued}')
        assert resource(queued)['held_by'] == env.lane['lane']
        assert resource(queued)['queue'] == [role_lane]
        assert lane(queued, env.lane['lane'])['resources_held'] == ['chamber']
        assert lane(queued, role_lane)['resources_held'] == []
        refused = env.srv.post(f'/api/lanes/{role_lane}/commands', json={'which': 'build'})
        assert refused.status_code == 409, refused.text
        assert refused.json()['error']['code'] == 'resource_busy'
    finally:
        env.release_command()
        if endpoint == 'routing':
            completed = env.finished('retain-resource')
        else:
            frames = collect(socket)
    if endpoint == 'routing':
        assert completed['state'] == 'completed'
        assert len(completed['receipts']) == 1
        assert completed['receipts'][0]['exit_code'] == 0
    else:
        assert frames[-1]['exit_code'] == 0
    granted = eventually(lambda: lane_snapshot(env), lambda snapshot:
                         resource(snapshot)['held_by'] == role_lane)
    assert lane(granted, role_lane)['resources_held'] == ['chamber']
    assert lane(granted, env.lane['lane'])['resources_held'] == []
    assert env.invocations() == ['build']
