import base64
from pathlib import Path

import pytest

from test_ticket_router import checkout_state, router, step


@pytest.mark.parametrize('cached', [False, True], ids=['restart-load', 'cached-history'])
def test_failed_capture_is_bound_to_current_lane_claim(router, cached):
    env = router(gate_body="printf 'PRIOR_CLAIM_FAILURE_CAPTURE\\n'\nexit 7\n")
    plan = [step('gate')]
    env.submit('failed-prior-claim', plan)
    failed = env.finished('failed-prior-claim', output=True)
    assert (failed['state'], failed['reason'], failed['ticket_id']) == (
        'needs_main', 'command_failed', 'TCK-001')
    receipt = failed['receipts'][0]
    assert receipt['exit_code'] == 7 and receipt['accepted'] is False
    captured = base64.b64decode(receipt['output_base64'], validate=True)
    assert captured == b'PRIOR_CLAIM_FAILURE_CAPTURE\n'
    assert env.read('failed-prior-claim', output=True) == failed
    env.restart()
    assert env.read('failed-prior-claim', output=True) == failed
    assert env.invocations() == ['gate']
    original_lane = env.lane['lane']
    released = env.srv.delete(f'/api/lanes/{original_lane}?discard_ignored=true')
    assert released.status_code == 200, released.text
    assert released.json()['worktree_removed'] is True
    env.restart()
    claimed = env.srv.post('/api/lanes', json={'ticket_id': 'TCK-002', 'workspace': 'core'})
    assert claimed.status_code == 201, claimed.text
    current = claimed.json()
    assert current['lane'] == original_lane
    assert current['ticket_id'] != failed['ticket_id']
    env.lane = current
    env.worktree = Path(current['worktree'])
    env.path = f'/api/lanes/{original_lane}/routing'
    env.before = checkout_state(env.root)
    if cached:
        unrelated = env.srv.get(env.path + '/no-such-current-request')
        assert unrelated.status_code == 404, unrelated.text
    refused = env.srv.get(env.path + '/failed-prior-claim?include_output=true')
    assert refused.status_code == 409, refused.text
    assert refused.json()['error']['code'] == 'routing_unresolved'
    assert 'output_base64' not in refused.text
    assert env.invocations() == []
    duplicate = env.srv.post(env.path, json={'request_id': 'failed-prior-claim', 'steps': plan})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()['error']['code'] == 'routing_unresolved'
    env.submit('current-claim-build', [step('build')])
    fresh = env.finished('current-claim-build', output=True)
    assert fresh['state'] == 'completed' and fresh['ticket_id'] == 'TCK-002'
    assert fresh['receipts'][0]['reused'] is False
    assert env.invocations() == ['build']


def test_failed_capture_is_not_inherited_by_same_ticket_reclaim(router):
    env = router(gate_body="printf 'PRIOR_INCARNATION_FAILURE_CAPTURE\\n'\nexit 7\n")
    env.submit('failed-prior-incarnation', [step('gate')])
    failed = env.finished('failed-prior-incarnation', output=True)
    assert failed['state'] == 'needs_main' and failed['ticket_id'] == 'TCK-001'
    assert env.read('failed-prior-incarnation', output=True) == failed
    original_lane = env.lane['lane']
    released = env.srv.delete(f'/api/lanes/{original_lane}')
    assert released.status_code == 200, released.text
    assert released.json()['worktree_removed'] is False
    claimed = env.srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
    assert claimed.status_code == 201, claimed.text
    current = claimed.json()
    assert current['lane'] == original_lane and current['ticket_id'] == failed['ticket_id']
    env.before = checkout_state(env.root)
    refused = env.srv.get(env.path + '/failed-prior-incarnation?include_output=true')
    assert refused.status_code == 409, refused.text
    assert refused.json()['error']['code'] == 'routing_unresolved'
    assert env.invocations() == ['gate']
    env.submit('current-incarnation-build', [step('build')])
    fresh = env.finished('current-incarnation-build')
    assert fresh['state'] == 'completed' and fresh['ticket_id'] == 'TCK-001'
    assert fresh['source'] != failed['source']
    assert env.invocations() == ['gate', 'build']
