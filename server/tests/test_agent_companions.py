"""SRV-FIX-560: declared companion files at the actual lane boundary."""
import os
from pathlib import Path

import pytest

from test_agent_profiles import CAPTURE, PEER
from test_isolation import laneenv, run_in_lane
from test_lane import git


def companion_lane(laneenv, tmp_path, declared, absolute=False):
    bundle = tmp_path / 'agent-package'
    bundle.mkdir()
    agent = bundle / 'fixture-agent'
    agent.write_text('#!/usr/bin/python3\n' + PEER)
    agent.chmod(0o755)
    helper = bundle / 'fixture-helper'
    helper.write_text('#!/bin/sh\nprintf "companion-ready\\n"\n')
    helper.chmod(0o755)
    sentinel = tmp_path / 'adjacent-undeclared-secret'
    sentinel.write_text('private host bytes')
    credentials = tmp_path / 'private-credentials'
    credentials.mkdir()
    secret = credentials / 'secret'
    secret.write_text('private credential bytes')
    workspace = {'path': '.', 'toolchain': [str(helper)] if declared else [],
                 'start_preflight': {'command':
                     'test -x /factory/bin/fixture-helper || '
                     '{ echo "required companion is unavailable"; exit 1; }'}}
    env = laneenv.build(
        workspaces={'core': workspace},
        agents={'fixture': {'driver': 'codex',
                            'command': str(agent) if absolute else 'fixture-agent',
                            'models': ['fixture-model'], 'default_model': 'fixture-model'}},
        env={'PATH': str(bundle) + ':' + os.environ['PATH']})
    (env.root / CAPTURE.name).write_bytes(CAPTURE.read_bytes())
    git(env.root, 'add', CAPTURE.name)
    git(env.root, 'commit', '-m', 'declare recorded app-server fixture')
    return env, env.claim(workspace='core'), sentinel, secret


@pytest.mark.parametrize('absolute', [False, True])
def test_declared_agent_companion_is_reachable_read_only_without_adjacent_secrets(
        laneenv, tmp_path, absolute):
    env, lane, sentinel, secret = companion_lane(laneenv, tmp_path, True, absolute)
    rc, output = run_in_lane(env, lane,
        '/factory/bin/fixture-helper && '
        'test ! -r ' + str(sentinel) + ' && test ! -r ' + str(secret) + ' && '
        '! sh -c "echo overwritten > /factory/bin/fixture-helper"')
    assert rc == 0, output
    assert 'companion-ready' in output
    start = env.srv.post(f"/api/lanes/{lane['lane']}/runs",
                         json={'agent': 'fixture', 'model': 'fixture-model', 'prompt': 'hello'})
    assert start.status_code == 201, start.text
    assert env.srv.post(f"/api/lanes/{lane['lane']}/end", json={}).status_code == 202


def test_undeclared_companion_refuses_before_provider_and_run_record(laneenv, tmp_path):
    env, lane, sentinel, secret = companion_lane(laneenv, tmp_path, False)
    start = env.srv.post(f"/api/lanes/{lane['lane']}/runs",
                         json={'agent': 'fixture', 'model': 'fixture-model', 'prompt': 'hello'})
    assert start.status_code == 409, start.text
    error = start.json()['error']
    assert error['code'] == 'start_preflight_failed'
    assert 'required companion is unavailable' in error['detail']['output']
    assert env.srv.get('/api/tickets/TCK-001/runs').json()['runs'] == []
    assert not (Path(lane['worktree']) / 'codex-log.jsonl').exists()
