import base64
import hashlib
import json

import pytest

from test_lane_command import collect, output_of
from test_ticket_router import HOLD, eventually, router, step


CAPTURE = b'RECOVERY terminal capture\nRECOVERY final marker\n'


def unreadable_terminal_source(env, request_id):
    socket = env.subscribed()
    plan = [step('build', reuse=True), step('gate')]
    env.submit(request_id, plan)
    eventually(env.invocations, lambda rows: rows == ['build'])
    source = env.worktree / 'README.md'
    original = source.read_bytes()
    source.unlink()
    source.mkdir()
    try:
        unavailable = env.srv.get(env.path + '/' + request_id)
        assert unavailable.status_code == 409, unavailable.text
        assert unavailable.json()['error']['code'] == 'routing_unresolved'
        env.release_command()
        frames = collect(socket, timeout=12)
        assert frames[-1]['exit_code'] == 0
        assert output_of(frames).encode() == CAPTURE
        folder = env.root / '.factory/runs/.routing' / hashlib.sha256(
            env.lane['lane'].encode()).hexdigest()
        journal = folder / (request_id + '.json')

        def durable_state():
            if not journal.exists():
                return None
            return json.loads(journal.read_text())

        eventually(durable_state, lambda job: job is not None and (
            job['state'], job['reason']) == ('needs_main', 'source_unresolved'))
    finally:
        env.release_command()
        source.rmdir()
        source.write_bytes(original)
    return journal, plan


@pytest.mark.parametrize('restart', [False, True], ids=['cached-history', 'restart-load'])
def test_unreadable_terminal_source_can_be_reconciled_without_replay(router, restart):
    env = router(build_body="printf 'RECOVERY terminal capture\\n'\n" + HOLD
                 + "printf 'RECOVERY final marker\\n'\n")
    request_id = 'unknown-terminal-source'
    _, plan = unreadable_terminal_source(env, request_id)
    if restart:
        env.restart()
    retained = env.read(request_id, output=True)
    assert (retained['state'], retained['action'], retained['reason']) == (
        'needs_main', 'main', 'source_unresolved')
    assert retained['next_index'] == 1 and retained['checkpoint'] is None
    assert len(retained['receipts']) == 1
    receipt = retained['receipts'][0]
    assert receipt['source_after'] == ''
    assert receipt['source'] == retained['source']
    assert receipt['exit_code'] == 0 and receipt['accepted'] is True
    assert receipt['reused'] is False and receipt['complete'] is True
    assert receipt['bytes'] == len(CAPTURE)
    assert receipt['capture_sha256'] == hashlib.sha256(CAPTURE).hexdigest()
    assert base64.b64decode(receipt['output_base64'], validate=True) == CAPTURE
    assert env.capture(receipt).read_bytes() == CAPTURE
    assert env.invocations() == ['build']
    duplicate = env.submit(request_id, plan, status=200)
    assert (duplicate['state'], duplicate['reason']) == ('needs_main', 'source_unresolved')
    assert env.invocations() == ['build']
    env.submit('reconciled-fresh-plan', plan)
    fresh = env.finished('reconciled-fresh-plan', output=True)
    assert fresh['state'] == 'completed' and fresh['reason'] == 'commands_completed'
    assert not any(row['reused'] for row in fresh['receipts'])
    assert all(row['source_after'] == fresh['source'] for row in fresh['receipts'])
    assert env.invocations() == ['build', 'build', 'gate']


@pytest.mark.parametrize('damage', [
    'valid-source-unresolved', 'completed', 'running', 'interrupted', 'reused', 'earlier',
    'invalid-nonempty-source',
])
def test_unknown_terminal_source_is_not_valid_in_other_dispositions(router, damage):
    request_id = 'unknown-source-guard'
    if damage == 'earlier':
        env = router()
        env.submit(request_id, [step('build'), step('gate')])
        assert env.finished(request_id)['state'] == 'completed'
        folder = env.root / '.factory/runs/.routing' / hashlib.sha256(
            env.lane['lane'].encode()).hexdigest()
        journal = folder / (request_id + '.json')
    else:
        env = router(build_body="printf 'RECOVERY terminal capture\\n'\n" + HOLD
                     + "printf 'RECOVERY final marker\\n'\n")
        journal, _ = unreadable_terminal_source(env, request_id)
    if damage == 'valid-source-unresolved':
        env.restart()
        valid = env.read(request_id, output=True)
        assert (valid['state'], valid['action'], valid['reason']) == (
            'needs_main', 'main', 'source_unresolved')
        assert valid['receipts'][-1]['source_after'] == ''
        assert valid['receipts'][-1]['reused'] is False
        assert base64.b64decode(valid['receipts'][-1]['output_base64'], validate=True) == CAPTURE
        assert env.invocations() == ['build']
        return
    retained = json.loads(journal.read_text())
    if damage == 'completed':
        retained['state'], retained['reason'] = 'completed', 'commands_completed'
        retained['steps'] = retained['steps'][:1]
    elif damage == 'running':
        retained['state'], retained['action'] = 'running', 'wait'
        retained['reason'] = 'command_running'
    elif damage == 'interrupted':
        retained['state'], retained['reason'] = 'interrupted', 'interrupted'
    elif damage == 'reused':
        retained['receipts'][-1]['reused'] = True
    elif damage == 'earlier':
        retained['state'], retained['reason'] = 'needs_main', 'source_unresolved'
        retained['receipts'][0]['source_after'] = ''
    else:
        retained['receipts'][-1]['source_after'] = 'invalid-observation'
    journal.write_text(json.dumps(retained))
    invocations = env.invocations()
    assert invocations
    env.restart()
    inspected = env.srv.get(env.path + '/' + request_id + '?include_output=true')
    assert inspected.status_code == 409, inspected.text
    assert inspected.json()['error']['code'] == 'routing_unresolved'
    assert 'output_base64' not in inspected.text
    refused = env.srv.post(env.path, json={
        'request_id': 'cannot-certify-damaged-source', 'steps': [step('build', reuse=True)],
    })
    assert refused.status_code == 409, refused.text
    assert refused.json()['error']['code'] == 'routing_unresolved'
    assert env.invocations() == invocations
