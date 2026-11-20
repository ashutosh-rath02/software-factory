"""SRV-FEAT-565: route actual confined commands using retained source-bound evidence."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

import jsonschema
import pytest

from conftest import owned_confinement_helpers, stop_process
from test_lane import build_repo, git, status_of
from test_lane_command import collect, output_of


HOLD = "while [ ! -f .router-observation/release ]; do /bin/sleep .05; done\n"


def eventually(read, accepts, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if accepts(value):
            return value
        time.sleep(.03)
    raise AssertionError(f'condition not reached; last value: {value}')


def checkout_state(root):
    paths = git(root, 'ls-files', '-z').stdout.split('\0')
    contents = {}
    for name in paths:
        if not name:
            continue
        path = root / name
        if path.is_symlink():
            contents[name] = ('symlink', os.readlink(path))
        elif path.is_file():
            contents[name] = ('file', path.read_bytes())
        else:
            contents[name] = ('directory' if path.is_dir() else 'missing',)
    return (status_of(root), git(root, 'rev-parse', 'HEAD').stdout,
            git(root, 'for-each-ref', '--format=%(refname) %(objectname)').stdout,
            contents)


def step(which, **extra):
    return dict(which=which, accepted_exit_codes=[0], **extra)


def valid_response(payload, endpoint):
    schema = json.loads((Path(__file__).resolve().parents[2]
                         / 'docs/technical/contracts/http.schema.json').read_text())
    node = schema['responses'][endpoint]
    jsonschema.Draft202012Validator({**node, '$defs': schema['$defs']}).validate(payload)
    return payload


@pytest.fixture
def router(tmp_path, serve):
    made = []
    sockets = []

    class Env:
        def __init__(self, root, srv, lane):
            self.root, self.srv, self.lane = root, srv, lane
            self.worktree = Path(lane['worktree'])
            self.path = f"/api/lanes/{lane['lane']}/routing"
            self.before = checkout_state(root)

        def submit(self, request_id, steps, status=202):
            response = self.srv.post(self.path, json=dict(request_id=request_id, steps=steps))
            assert response.status_code == status, response.text
            return valid_response(response.json(), 'E33')

        def read(self, request_id, output=False):
            suffix = '?include_output=true' if output else ''
            response = self.srv.get(self.path + '/' + request_id + suffix)
            assert response.status_code == 200, response.text
            return valid_response(response.json(), 'E34')

        def finished(self, request_id, output=False):
            return eventually(lambda: self.read(request_id, output),
                              lambda job: job['state'] != 'running')

        def invocations(self):
            path = self.worktree / '.router-observation/invocations'
            return path.read_text().splitlines() if path.exists() else []

        def release_command(self):
            path = self.worktree / '.router-observation/release'
            path.parent.mkdir(exist_ok=True)
            path.write_text('release\n')

        def subscribed(self):
            socket = self.srv.ws()
            sockets.append(socket)
            socket.send({'m': 'subscribe', 'scope': {'lane': self.lane['lane']}})
            socket.wait_for('snapshot')
            return socket

        def restart(self):
            stop_process(self.srv.proc)
            self.srv = serve(self.root)

        def capture(self, receipt):
            path = Path(receipt['capture_path'])
            path = path if path.is_absolute() else self.root / path
            assert path.resolve().is_relative_to((self.root / '.factory/runs/.routing').resolve())
            return path

    def build(*, build_body="printf 'BUILD terminal\n'\n",
              gate_body="printf 'GATE terminal\n'\n", review_body=None, resources=None,
              prepare=None):
        workspaces = {'core': {'path': '.', 'build': '/bin/sh router-build.sh',
                               'gate': '/bin/sh router-gate.sh'}}
        if review_body is not None:
            workspaces['core']['review'] = '/bin/sh router-review.sh'
        root = build_repo(tmp_path / f'repo{len(made)}', workspaces=workspaces,
                          resources=resources, agent={'command': '/bin/sh agent-trap.sh'})
        ignore = root / '.gitignore'
        ignore.write_text(ignore.read_text() + '.router-observation/\n')
        for which, body in [('build', build_body), ('gate', gate_body), ('review', review_body)]:
            if body is not None:
                (root / f'router-{which}.sh').write_text(
                    '#!/bin/sh\nset -eu\n/bin/mkdir -p .router-observation\n'
                    f"printf '{which}\\n' >> .router-observation/invocations\n" + body)
        (root / 'agent-trap.sh').write_text(
            '#!/bin/sh\n/bin/mkdir -p .router-observation\n'
            "printf 'agent started\\n' > .router-observation/agent-started\nexit 99\n")
        git(root, 'add', '-A')
        if prepare is not None:
            prepare(root)
        git(root, 'commit', '-m', 'declared routing fixture commands')
        srv = serve(root)
        response = srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
        assert response.status_code == 201, response.text
        lane = response.json()
        assert git(Path(lane['worktree']), 'check-ignore', '.router-observation/invocations').returncode == 0
        (root / 'README.md').write_text('Operator has an unrelated uncommitted edit.\n')
        env = Env(root, srv, lane)
        made.append(env)
        return env

    yield build

    for socket in sockets:
        socket.close()
    for env in made:
        assert checkout_state(env.root) == env.before, 'routing changed the operator checkout or refs'


def assert_receipt(env, receipt, which, accepted=True):
    assert receipt['which'] == which
    assert receipt['command'] == f'/bin/sh router-{which}.sh'
    assert receipt['command_id']
    assert re.fullmatch('[0-9a-f]{64}', receipt['source'])
    assert re.fullmatch('[0-9a-f]{64}', receipt['source_after'])
    assert receipt['complete'] is True
    assert receipt['accepted'] is accepted
    capture = env.capture(receipt).read_bytes()
    assert receipt['bytes'] == len(capture)
    assert receipt['capture_sha256'] == hashlib.sha256(capture).hexdigest()
    if 'output_base64' in receipt:
        assert base64.b64decode(receipt['output_base64'], validate=True) == capture
    return capture


def test_declared_commands_route_once_without_an_agent(router):
    env = router()
    socket = env.subscribed()
    plan = [step('build'), step('gate')]
    started = env.submit('build-gate', plan)
    assert started['request_id'] == 'build-gate' and started['lane'] == env.lane['lane']
    frames = collect(socket) + collect(socket)
    job = env.finished('build-gate', output=True)
    assert (job['state'], job['action'], job['reason'], job['next_index']) == (
        'completed', 'main', 'commands_completed', 2)
    assert [row['index'] for row in job['receipts']] == [0, 1]
    for receipt, which in zip(job['receipts'], ['build', 'gate']):
        capture = assert_receipt(env, receipt, which)
        matching = [frame for frame in frames if frame['command_id'] == receipt['command_id']]
        assert matching and matching[-1]['exit_code'] == receipt['exit_code'] == 0
        assert output_of(matching).encode() == capture
        assert receipt['source'] == receipt['source_after'] == job['source']
        assert receipt['reused'] is False
    duplicate = env.submit('build-gate', plan, status=200)
    assert duplicate == env.read('build-gate')
    assert env.invocations() == ['build', 'gate']
    runs = env.srv.get('/api/tickets/TCK-001/runs')
    assert runs.status_code == 200 and runs.json()['runs'] == []
    assert not (env.worktree / '.router-observation/agent-started').exists()


def test_reuse_requires_current_git_visible_inputs(router):
    env = router()
    plan = [step('build', reuse=True), step('gate', reuse=True)]
    env.submit('first', plan)
    first = env.finished('first')
    env.submit('cached', plan)
    cached = env.finished('cached')
    assert cached['state'] == 'completed'
    assert all(row['reused'] for row in cached['receipts'])
    assert cached['source'] == first['source']
    assert env.invocations() == ['build', 'gate']
    tracked = env.worktree / 'README.md'
    before = tracked.stat()
    original = tracked.read_bytes()
    tracked.write_bytes(b'A' + original[1:])
    os.utime(tracked, ns=(before.st_atime_ns, before.st_mtime_ns))
    env.submit('tracked-change', plan)
    changed = env.finished('tracked-change')
    assert changed['source'] != cached['source']
    assert not any(row['reused'] for row in changed['receipts'])
    (env.worktree / 'new-input.txt').write_text('new deciding input\n')
    env.submit('untracked-change', plan)
    untracked = env.finished('untracked-change')
    assert untracked['source'] != changed['source']
    assert not any(row['reused'] for row in untracked['receipts'])
    env.submit('not-cacheable', [step('build'), step('gate')])
    fresh = env.finished('not-cacheable')
    assert not any(row['reused'] for row in fresh['receipts'])
    assert env.invocations() == ['build', 'gate'] * 4


def test_source_change_during_command_stops_next_step(router):
    env = router(build_body="printf 'PASS before exit\n'\n" + HOLD)
    env.submit('moving-source', [step('build'), step('gate')])
    eventually(env.invocations, lambda rows: rows == ['build'])
    (env.worktree / 'README.md').write_text('source changed during the command\n')
    env.release_command()
    job = env.finished('moving-source')
    assert (job['state'], job['action'], job['reason']) == ('needs_main', 'main', 'source_changed')
    assert env.invocations() == ['build']
    assert len(job['receipts']) == 1
    receipt = job['receipts'][0]
    assert_receipt(env, receipt, 'build')
    assert receipt['source_after'] != receipt['source']


def test_only_terminal_results_advance_and_decisions_return_to_main(router):
    env = router(gate_body="printf 'PASS before exit\n'\n" + HOLD + "printf 'real failure\n'\nexit 7\n")
    socket = env.subscribed()
    env.submit('held', [step('gate'), step('build')])
    first = socket.wait_for('command')
    assert 'PASS before exit' in first.get('chunk', '')
    assert first.get('exit_code') is None
    held = env.read('held')
    assert (held['state'], held['action'], held['next_index']) == ('running', 'wait', 0)
    assert env.invocations() == ['gate'] and held['receipts'] == []
    env.release_command()
    failed = env.finished('held', output=True)
    assert (failed['state'], failed['action'], failed['reason']) == ('needs_main', 'main', 'command_failed')
    assert env.invocations() == ['gate']
    capture = assert_receipt(env, failed['receipts'][0], 'gate', accepted=False)
    assert b'PASS before exit' in capture and capture.endswith(b'real failure\n')
    assert failed['receipts'][0]['exit_code'] == 7
    control = router()
    control.submit('judgment', [step('build', require_decision=True), step('gate')])
    decision = control.finished('judgment')
    assert (decision['state'], decision['action'], decision['reason']) == ('needs_main', 'main', 'decision_required')
    assert control.invocations() == ['build'] and len(decision['receipts']) == 1


def test_repeated_failures_create_one_advisor_checkpoint(router):
    env = router(gate_body="printf 'identical actual failure\n'\nexit 7\n")
    plan = [step('gate')]
    env.submit('failure-one', plan)
    first = env.finished('failure-one')
    assert first['action'] == 'main' and first['checkpoint'] is None
    env.submit('failure-two', plan)
    second = env.finished('failure-two')
    assert second['state'] == 'needs_main' and second['action'] == 'advisor'
    checkpoint = second['checkpoint']
    assert checkpoint['id'] and checkpoint['kind'] == 'repeated_error'
    assert checkpoint['cause'] and checkpoint['receipt_count'] >= 2
    for unused in range(3):
        assert env.read('failure-two')['checkpoint'] == checkpoint
        assert env.submit('failure-two', plan, status=200)['checkpoint'] == checkpoint
    assert env.invocations() == ['gate', 'gate']
    assert first['receipts'][0]['capture_sha256'] == second['receipts'][0]['capture_sha256']
    assert not (env.worktree / '.router-observation/agent-started').exists()


def test_restart_replays_completed_state_without_commands(router):
    env = router()
    plan = [step('build'), step('gate')]
    env.submit('completed-restart', plan)
    before = env.finished('completed-restart', output=True)
    assert before['state'] == 'completed'
    env.restart()
    assert env.read('completed-restart', output=True) == before
    duplicate = env.submit('completed-restart', plan, status=200)
    assert duplicate['state'] == 'completed'
    assert env.invocations() == ['build', 'gate']
    assert all(assert_receipt(env, row, row['which']) for row in before['receipts'])


def test_restart_leaves_uncertain_dispatch_interrupted(router):
    env = router(build_body="printf 'dispatch running\n'\n" + HOLD)
    helpers_before = set(owned_confinement_helpers())
    plan = [step('build'), step('gate')]
    env.submit('uncertain', plan)
    eventually(env.invocations, lambda rows: rows == ['build'])
    assert env.read('uncertain')['state'] == 'running'
    env.srv.proc.kill()
    env.srv.proc.wait(timeout=5)
    env.release_command()
    eventually(lambda: set(owned_confinement_helpers()), lambda found: found <= helpers_before)
    env.restart()
    interrupted = env.read('uncertain')
    assert (interrupted['state'], interrupted['action'], interrupted['reason']) == ('interrupted', 'main', 'interrupted')
    assert env.submit('uncertain', plan, status=200)['state'] == 'interrupted'
    assert env.invocations() == ['build']


def test_complete_capture_is_retained_and_served(router):
    expected = b'capture start\n' + b'x' * 8192 + b'\nFINAL_CAPTURE_MARKER\nSTDERR_CAPTURE_MARKER\n'
    body = "/usr/bin/python3 -c 'import os; os.write(1,b\"capture start\\n\"+b\"x\"*8192+b\"\\nFINAL_CAPTURE_MARKER\\n\"); os.write(2,b\"STDERR_CAPTURE_MARKER\\n\")'\n"
    env = router(build_body=body, gate_body='exit 0\n')
    env.submit('complete-output', [step('build'), step('gate')])
    job = env.finished('complete-output', output=True)
    assert job['state'] == 'completed'
    assert assert_receipt(env, job['receipts'][0], 'build') == expected
    assert base64.b64decode(job['receipts'][0]['output_base64'], validate=True) == expected
    assert assert_receipt(env, job['receipts'][1], 'gate') == b''
    hidden = env.read('complete-output')
    assert all('output_base64' not in row for row in hidden['receipts'])
    bad_query = env.srv.get(env.path + '/complete-output?include_output=perhaps')
    assert bad_query.status_code == 400, bad_query.text


def test_invalid_or_busy_requests_have_no_effect(router):
    env = router(build_body=HOLD)
    env.submit('active', [step('build')])
    eventually(env.invocations, lambda rows: rows == ['build'])
    malformed = [
        {}, {'request_id': '', 'steps': [step('build')]},
        {'request_id': 'bad/id', 'steps': [step('build')]},
        {'request_id': 'x' * 65, 'steps': [step('build')]},
        {'request_id': 'non-ascii-é', 'steps': [step('build')]},
        {'request_id': 'bad', 'steps': []},
        {'request_id': 'bad', 'steps': [step('build')] * 17},
        {'request_id': 'bad', 'steps': [dict(which='build', accepted_exit_codes=[])]},
        {'request_id': 'bad', 'steps': [dict(which='build', accepted_exit_codes=[True])]},
        {'request_id': 'bad', 'steps': [dict(which='build', accepted_exit_codes=[0, 0])]},
        {'request_id': 'bad', 'steps': [dict(which='build', accepted_exit_codes=[256])]},
        {'request_id': 'bad', 'steps': [dict(which='build', accepted_exit_codes=[-1])]},
        {'request_id': 'bad', 'steps': [step('build', reuse='yes')]},
        {'request_id': 'bad', 'steps': [step('build', unexpected=True)]},
        {'request_id': 'bad', 'steps': [step('build')], 'unexpected': True},
    ]
    env.release_command()
    env.finished('active')
    for body in malformed:
        refused = env.srv.post(env.path, json=body)
        assert refused.status_code == 400, (body, refused.text)
        assert refused.json()['error']['code'] == 'bad_request'
    for raw in [
        '{"request_id":"duplicate","request_id":"duplicate","steps":[{"which":"build","accepted_exit_codes":[0]}]}',
        '{"request_id":"duplicate","steps":[{"which":"build","which":"build","accepted_exit_codes":[0]}]}',
    ]:
        refused = env.srv.post(env.path, data=raw, headers={'Content-Type': 'application/json'})
        assert refused.status_code == 400, refused.text
    for which, status, code in [('deploy', 400, 'bad_which'), ('review', 404, 'not_declared')]:
        for steps in [[step(which)], [step('build'), step(which)]]:
            refused = env.srv.post(env.path, json={'request_id': which, 'steps': steps})
            assert refused.status_code == status, refused.text
            assert refused.json()['error']['code'] == code
    conflict = env.srv.post(env.path, json={'request_id': 'active', 'steps': [step('gate')]})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'routing_conflict'
    missing = env.srv.post('/api/lanes/lane/999/routing', json={'request_id': 'missing', 'steps': [step('build')]})
    assert missing.status_code == 404, missing.text
    assert env.invocations() == ['build']
    busy = router(build_body=HOLD)
    command = busy.srv.post(f"/api/lanes/{busy.lane['lane']}/commands", json={'which': 'build'})
    assert command.status_code == 202, command.text
    eventually(busy.invocations, lambda rows: rows == ['build'])
    refused = busy.srv.post(busy.path, json={'request_id': 'manual-busy', 'steps': [step('gate')]})
    assert refused.status_code == 409 and refused.json()['error']['code'] == 'wrong_state'
    busy.release_command()
    resource = router(resources={'chamber': {'exclusive': True, 'needed_by': ['core'], 'description': 'single fixture resource'}})
    queued = resource.srv.post('/api/lanes', json={'ticket_id': 'TCK-002', 'workspace': 'core'})
    assert queued.status_code == 201, queued.text
    resource.before = checkout_state(resource.root)
    queued_lane = queued.json()
    refused = resource.srv.post(f"/api/lanes/{queued_lane['lane']}/routing", json={'request_id': 'queued', 'steps': [step('build')]})
    assert refused.status_code == 409 and refused.json()['error']['code'] == 'resource_busy'
    assert not (Path(queued_lane['worktree']) / '.router-observation/invocations').exists()


def test_missing_or_tampered_evidence_cannot_advance(router):
    for damage in ['missing', 'tampered']:
        env = router()
        plan = [step('build', reuse=True)]
        env.submit('original', plan)
        first = env.finished('original')
        assert first['state'] == 'completed'
        env.submit('valid-control', plan)
        assert env.finished('valid-control')['receipts'][0]['reused'] is True
        capture = env.capture(first['receipts'][0])
        if damage == 'missing':
            capture.unlink()
        else:
            capture.write_bytes(b'forged successful capture\n')
        inspected = env.srv.get(env.path + '/original')
        if inspected.status_code == 409:
            assert inspected.json()['error']['code'] == 'routing_unresolved'
        else:
            assert inspected.status_code == 200, inspected.text
            assert inspected.json()['state'] == 'needs_main' and inspected.json()['action'] == 'main'
        refused = env.srv.post(env.path, json={'request_id': 'damaged-reuse', 'steps': plan})
        assert refused.status_code == 409, refused.text
        assert refused.json()['error']['code'] == 'routing_unresolved'
        assert env.invocations() == ['build']


def test_routing_never_claims_ticket_acceptance(router):
    env = router()
    ticket = env.worktree / 'docs/tickets/TCK-001.md'
    original = ticket.read_bytes()
    env.submit('no-acceptance', [step('build'), step('gate')])
    assert env.finished('no-acceptance')['state'] == 'completed'
    for stage in ['GATE', 'QA', 'MERGE']:
        forged = env.srv.post('/api/progress', json={'token': '0' * 32, 'stage': stage})
        assert forged.status_code == 404, forged.text
    assert ticket.read_bytes() == original
    detail = env.srv.get('/api/tickets/TCK-001')
    assert detail.status_code == 200 and detail.json()['stage']['index'] == 0
    lanes = env.srv.get('/api/lanes')
    assert lanes.status_code == 200
    assert [(row['ticket_id'], row['role']) for row in lanes.json()['lanes']] == [('TCK-001', 'run')]
    assert env.srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []
    assert not (env.worktree / '.router-observation/agent-started').exists()


def test_concurrent_duplicate_deliveries_dispatch_one_command(router):
    env = router(build_body=HOLD)
    body = {'request_id': 'concurrent', 'steps': [step('build'), step('gate')]}
    barrier = threading.Barrier(6)

    def deliver(unused):
        barrier.wait(timeout=5)
        return env.srv.post(env.path, json=body)

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(deliver, range(6)))
    assert sorted(response.status_code for response in responses) == [200] * 5 + [202], [response.text for response in responses]
    eventually(env.invocations, lambda rows: rows == ['build'])
    busy = env.srv.post(env.path, json={'request_id': 'other', 'steps': [step('gate')]})
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'routing_busy'
    conflict = env.srv.post(env.path, json={'request_id': 'concurrent', 'steps': [step('gate')]})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'routing_conflict'
    assert env.invocations() == ['build']
    env.release_command()
    assert env.finished('concurrent')['state'] == 'completed'
    assert env.invocations() == ['build', 'gate']


def test_routed_and_manual_commands_share_one_writer_reservation(router):
    env = router(build_body=HOLD)
    manual_path = f"/api/lanes/{env.lane['lane']}/commands"
    barrier = threading.Barrier(17)

    def deliver(index):
        barrier.wait(timeout=5)
        if index == 0:
            return env.srv.post(env.path, json={
                'request_id': 'mixed-admission', 'steps': [step('build')]})
        return env.srv.post(manual_path, json={'which': 'build'})

    try:
        with ThreadPoolExecutor(max_workers=17) as pool:
            responses = list(pool.map(deliver, range(17)))
        routed, *manual = responses
        assert routed.status_code in [202, 409], routed.text
        assert all(response.status_code in [202, 409] for response in manual), [response.text for response in manual]
        accepted = [response for response in responses if response.status_code == 202]
        assert len(accepted) == 1, [(response.status_code, response.text) for response in responses]
        eventually(env.invocations, lambda rows: len(rows) > 0)
        assert env.invocations() == ['build']
        for unused in range(4):
            refused = env.srv.post(manual_path, json={'which': 'build'})
            assert refused.status_code == 409, refused.text
        assert env.invocations() == ['build']
        if routed.status_code == 202:
            active = env.read('mixed-admission')
            assert active['state'] == 'running' and active['receipts'] == []
        else:
            assert routed.json()['error']['code'] in ['wrong_state', 'routing_busy']
    finally:
        env.release_command()
    if routed.status_code == 202:
        assert env.finished('mixed-admission')['state'] == 'completed'


def test_declared_nonzero_acceptance_and_each_steps_exit_policy(router):
    env = router(gate_body="printf 'declared gate result\n'\nexit 7\n")
    accepted_gate = dict(which='gate', accepted_exit_codes=[7], reuse=True)
    env.submit('nonzero-accepted', [accepted_gate, step('build')])
    accepted = env.finished('nonzero-accepted')
    assert (accepted['state'], accepted['action'], accepted['reason']) == ('completed', 'main', 'commands_completed')
    assert accepted['checkpoint'] is None
    assert_receipt(env, accepted['receipts'][0], 'gate')
    assert accepted['receipts'][0]['exit_code'] == 7
    rejected_gate = step('gate', reuse=True)
    env.submit('different-policy', [rejected_gate, step('build')])
    rejected = env.finished('different-policy')
    assert rejected['state'] == 'needs_main' and rejected['reason'] == 'command_failed'
    assert rejected['receipts'][0]['reused'] is False
    assert_receipt(env, rejected['receipts'][0], 'gate', accepted=False)
    assert env.invocations() == ['gate', 'build', 'gate']
    env.submit('cross-step-policy', [accepted_gate, rejected_gate, step('build')])
    mixed = env.finished('cross-step-policy')
    assert mixed['state'] == 'needs_main' and mixed['reason'] == 'command_failed'
    assert [row['index'] for row in mixed['receipts']] == [0, 1]
    assert mixed['receipts'][0]['accepted'] is True and mixed['receipts'][0]['reused'] is True
    assert mixed['receipts'][1]['accepted'] is False and mixed['receipts'][1]['reused'] is False
    assert env.invocations() == ['gate', 'build', 'gate', 'gate']


def test_symlink_identity_uses_literal_target_without_following_it(router, tmp_path):
    first_target = tmp_path / 'external-first'
    second_target = tmp_path / 'external-second'
    first_target.write_text('external initial content\n')
    second_target.write_text('another external content\n')

    def prepare(root):
        (root / 'literal-link').symlink_to(first_target)
        git(root, 'add', 'literal-link')

    env = router(prepare=prepare)
    plan = [step('build', reuse=True)]
    env.submit('literal-original', plan)
    original = env.finished('literal-original')
    first_target.write_text('external content changes are outside source identity\n')
    env.submit('external-target-changed', plan)
    external = env.finished('external-target-changed')
    assert external['state'] == 'completed' and external['source'] == original['source']
    assert external['receipts'][0]['reused'] is True
    link = env.worktree / 'literal-link'
    link.unlink()
    link.symlink_to(second_target)
    env.submit('literal-target-changed', plan)
    changed = env.finished('literal-target-changed')
    assert changed['state'] == 'completed' and changed['source'] != original['source']
    assert changed['receipts'][0]['reused'] is False
    assert env.invocations() == ['build', 'build']


@pytest.mark.parametrize('kind', ['ancestor_symlink', 'gitlink'])
def test_unsupported_source_inputs_refuse_before_dispatch(router, tmp_path, kind):
    def prepare(root):
        if kind == 'gitlink':
            head = git(root, 'rev-parse', 'HEAD').stdout.strip()
            git(root, 'update-index', '--add', '--cacheinfo', '160000', head, 'vendor/dependency')
        else:
            source = root / 'source-dir/input.txt'
            source.parent.mkdir()
            source.write_text('tracked input\n')
            git(root, 'add', 'source-dir')

    env = router(prepare=prepare)
    if kind == 'ancestor_symlink':
        outside = tmp_path / 'external-directory'
        outside.mkdir()
        (outside / 'input.txt').write_text('outside replacement\n')
        observations = env.worktree / '.router-observation'
        observations.mkdir()
        source = env.worktree / 'source-dir'
        source.rename(observations / 'original-source-dir')
        source.symlink_to(outside, target_is_directory=True)
    refused = env.srv.post(env.path, json={'request_id': 'unsupported-source', 'steps': [step('build')]})
    assert refused.status_code == 409, refused.text
    assert refused.json()['error']['code'] == 'routing_unresolved'
    assert env.invocations() == []
    assert env.srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []


def test_success_is_not_reused_for_a_new_lane_incarnation(router):
    env = router()
    plan = [step('build', reuse=True)]
    env.submit('old-incarnation', plan)
    old = env.finished('old-incarnation')
    assert old['state'] == 'completed'
    old_head = git(env.worktree, 'rev-parse', 'HEAD').stdout
    released = env.srv.delete(f"/api/lanes/{env.lane['lane']}")
    assert released.status_code == 200, released.text
    claimed = env.srv.post('/api/lanes', json={'ticket_id': 'TCK-001', 'workspace': 'core'})
    assert claimed.status_code == 201, claimed.text
    env.lane = claimed.json()
    env.worktree = Path(env.lane['worktree'])
    env.path = f"/api/lanes/{env.lane['lane']}/routing"
    assert git(env.worktree, 'rev-parse', 'HEAD').stdout == old_head
    preserved_invocations = env.invocations()
    env.submit('new-incarnation', plan)
    new = env.finished('new-incarnation')
    assert new['state'] == 'completed'
    assert new['source'] != old['source']
    assert new['receipts'][0]['reused'] is False
    assert new['receipts'][0]['command_id'] != old['receipts'][0]['command_id']
    assert env.invocations() == preserved_invocations + ['build']
