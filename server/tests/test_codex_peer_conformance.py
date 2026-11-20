"""The confined profile peer must reproduce recorded app-server responses."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_agent_profiles import PEER


CAPTURE = Path(__file__).parent / 'fixtures/cli-app-server-codex-2026-09-18.json'


@pytest.fixture
def recording():
    captured = json.loads(CAPTURE.read_text())
    assert captured['fixture_verified'] is True
    assert captured['cli_version'].startswith('codex-cli ')
    return captured


def exchange(recording, phase, method):
    records = [(row['direction'], json.loads(row['raw']))
               for row in recording['records'] if row['phase'] == phase]
    requests = [frame for direction, frame in records
                if direction == 'client' and frame.get('method') == method]
    assert len(requests) == 1, (phase, method, requests)
    responses = [frame for direction, frame in records
                 if direction == 'server' and frame.get('id') == requests[0]['id']
                 and 'method' not in frame]
    assert len(responses) == 1, (phase, method, responses)
    return requests[0], responses[0]


def run_peer(tmp_path, recording, requests, *, fail=False):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'config.toml').write_text('')
    (tmp_path / CAPTURE.name).write_text(json.dumps(recording))
    result = subprocess.run(
        [sys.executable, '-c', PEER, *(['fail'] if fail else [])], cwd=tmp_path,
        env=dict(os.environ, CODEX_HOME=str(config)),
        input=''.join(json.dumps(frame) + '\n' for frame in requests),
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()]


@pytest.mark.parametrize('fail', [False, True], ids=['accepted', 'refused'])
def test_profile_peer_reproduces_recorded_initialization(tmp_path, recording, fail):
    phase = 'initialization-error' if fail else 'initialize'
    request, expected = exchange(recording, phase, 'initialize')
    actual = run_peer(tmp_path, recording, [request], fail=fail)
    assert actual == [expected], 'the peer must emit the real recorded response, including all fields'


def notification(recording, phase, method, *, final_message=False):
    frames = [json.loads(row['raw']) for row in recording['records']
              if row['direction'] == 'server' and row['phase'] == phase]
    matches = [frame for frame in frames if frame.get('method') == method
               and (not final_message or frame['params']['item'].get('phase') == 'final_answer')]
    assert len(matches) == 1, (phase, method, matches)
    return matches[0]


def expected_runtime(recording, frame, work, *, turn=None, command=None):
    frame = copy.deepcopy(frame)
    _, started = exchange(recording, 'thread-start', 'thread/start')
    replacements = {started['result']['cwd']: str(work)}
    for row in recording['records']:
        source = json.loads(row['raw'])
        result = source.get('result', {})
        if row['direction'] == 'server' and 'thread' in result:
            replacements[result['thread']['id']] = 'thread-1'
        if turn and row['direction'] == 'server' and 'turn' in result:
            replacements[result['turn']['id']] = turn
    if command:
        replacements[frame['params']['commandActions'][0]['command']] = command
    encoded = json.dumps(frame)
    for old, new in replacements.items():
        encoded = encoded.replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1])
    return json.loads(encoded)


def test_profile_peer_lifecycle_preserves_recorded_envelopes(tmp_path, recording):
    initialize, initialized = exchange(recording, 'initialize', 'initialize')
    start, started = exchange(recording, 'thread-start', 'thread/start')
    turn, turning = exchange(recording, 'second-turn', 'turn/start')
    hold, holding = exchange(recording, 'interrupt-turn', 'turn/start')
    interrupt, interrupted = exchange(recording, 'interrupt-turn', 'turn/interrupt')
    resume, resumed = exchange(recording, 'thread-resume', 'thread/resume')
    turn['params']['threadId'] = hold['params']['threadId'] = 'thread-1'
    turn['params']['input'][0]['text'] = 'quote"\nline'
    hold['params']['input'][0]['text'] = 'hold'
    interrupt['params'] = {'threadId': 'thread-1', 'turnId': 'turn-2'}
    resume['params']['threadId'] = 'thread-1'
    actual = run_peer(tmp_path, recording, [initialize, start, turn, hold, interrupt, resume])
    expected = [initialized, expected_runtime(recording, started, tmp_path)]
    expected.extend(expected_runtime(recording, frame, tmp_path, turn='turn-1') for frame in (
        turning,
        notification(recording, 'second-turn', 'item/completed', final_message=True),
        notification(recording, 'second-turn', 'turn/completed'),
    ))
    expected.extend(expected_runtime(recording, frame, tmp_path, turn='turn-2') for frame in (
        holding, interrupted, notification(recording, 'interrupt-turn', 'turn/completed'),
    ))
    expected.append(expected_runtime(recording, resumed, tmp_path))
    assert actual == expected
    completions = [frame['params']['turn'] for frame in actual
                   if frame.get('method') == 'turn/completed']
    assert [frame['id'] for frame in completions] == ['turn-1', 'turn-2']
    assert [frame['status'] for frame in completions] == ['completed', 'interrupted']


@pytest.mark.parametrize('decision', ['decline', 'accept'])
def test_profile_peer_approval_uses_recorded_request_and_decision(tmp_path, recording, decision):
    initialize, initialized = exchange(recording, 'initialize', 'initialize')
    start, started = exchange(recording, 'thread-start', 'thread/start')
    turn, turning = exchange(recording, 'approval-decline', 'turn/start')
    turn['params']['threadId'] = 'thread-1'
    turn['params']['input'][0]['text'] = 'approve'
    approval = notification(recording, 'approval-decline', 'item/commandExecution/requestApproval')
    phase = 'approval-' + decision
    recorded_decisions = [json.loads(row['raw']) for row in recording['records']
                          if row['direction'] == 'client' and row['phase'] == phase
                          and json.loads(row['raw']).get('result', {}).get('decision') == decision]
    assert len(recorded_decisions) == 1
    answer = recorded_decisions[0]
    answer['id'] = approval['id']
    actual = run_peer(tmp_path, recording, [initialize, start, turn, answer])
    expected = [initialized, expected_runtime(recording, started, tmp_path),
                expected_runtime(recording, turning, tmp_path, turn='turn-1'),
                expected_runtime(recording, approval, tmp_path, turn='turn-1',
                                 command='printf approved > approved.txt')]
    expected.extend(expected_runtime(recording, frame, tmp_path, turn='turn-1') for frame in (
        notification(recording, phase, 'item/completed', final_message=True),
        notification(recording, phase, 'turn/completed'),
    ))
    assert actual == expected
    assert (tmp_path / 'approved.txt').exists() is (decision == 'accept')


def test_profile_peer_cannot_invent_a_missing_approval_record(tmp_path, recording):
    initialize, _ = exchange(recording, 'initialize', 'initialize')
    start, _ = exchange(recording, 'thread-start', 'thread/start')
    turn, _ = exchange(recording, 'approval-decline', 'turn/start')
    turn['params']['input'][0]['text'] = 'approve'
    recording['records'] = [row for row in recording['records']
                            if json.loads(row['raw']).get('method')
                            != 'item/commandExecution/requestApproval']
    with pytest.raises(AssertionError, match='approval-decline'):
        run_peer(tmp_path, recording, [initialize, start, turn])
    assert not (tmp_path / 'approved.txt').exists()
