import json
import re
from pathlib import Path

import jsonschema
import pytest

from test_agent import assert_reaped
from test_agent_profiles import profiles


ROOT = Path(__file__).resolve().parents[2]


def contract():
    schema = json.loads((ROOT / 'docs/technical/contracts/http.schema.json').read_text())
    response = schema['responses'].get('E18')
    assert response, 'E18 has no generated HTTP contract'
    reference = response.get('request', {}).get('$ref', '')
    assert reference.startswith('#/$defs/'), 'E18 does not declare its request body'
    request = schema['$defs'][reference.rsplit('/', 1)[1]]
    return schema, request, response


def test_start_run_request_and_response_are_declared_and_generated():
    schema, request, response = contract()
    assert response['method'] == 'POST'
    assert response['path'] == '/api/lanes/{lane}/runs'
    assert response['status'] == 201
    assert request['properties']['agent']['type'] == 'string'
    assert request['properties']['model']['type'] == 'string'
    assert request['properties']['resume']['default'] is True
    assert {'agent', 'driver', 'model'} <= set(response['required'])
    generated = (ROOT / 'client/web/src/types/api.ts').read_text()
    for node in (request, response):
        match = re.search(r'export interface ' + node['title'] + r' \{(.*?)\n\}',
                          generated, re.S)
        assert match, f"{node['title']} is not generated"
        for name in node['properties']:
            assert re.search(r'^\s+' + name + r'\??:', match.group(1), re.M), name
    specification = (ROOT / 'docs/product/SPEC.md').read_text().split('#### E18 ·', 1)[1]
    specification = specification.split('\n#### ', 1)[0]
    assert '"agent"?' in specification
    assert 'invalid_agent_selection' in specification
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize('selection', [
    {'agent': 'gpt', 'model': 'gpt-other'},
    {'agent': 'claude', 'model': 'claude-test'},
])
def test_real_e18_answers_match_the_declared_contract(profiles, selection):
    schema, request, response = contract()
    _, srv, lane = profiles()
    body = {**selection, 'resume': False, 'prompt': 'quoted "prompt"\nsecond line'}
    jsonschema.Draft202012Validator({**request, '$defs': schema['$defs']}).validate(body)
    path = response['path'].replace('{lane}', lane['lane'])
    started = srv.post(path, json=body)
    assert started.status_code == response['status'], started.text
    try:
        payload = started.json()
        jsonschema.Draft202012Validator({**response, '$defs': schema['$defs']}).validate(payload)
        assert payload['agent'] == selection['agent']
        assert payload['model'] == selection['model']
    finally:
        assert srv.post(path.removesuffix('/runs') + '/end', json={}).status_code == 202
        assert_reaped(started.json()['pid'])


def test_start_contract_rejects_wrong_selection_types_and_missing_attribution():
    schema, request, response = contract()
    request_check = jsonschema.Draft202012Validator({**request, '$defs': schema['$defs']})
    for field in ('agent', 'model'):
        with pytest.raises(jsonschema.ValidationError):
            request_check.validate({field: {'executable': '/tmp/not-a-profile'}})
    response_check = jsonschema.Draft202012Validator({**response, '$defs': schema['$defs']})
    with pytest.raises(jsonschema.ValidationError):
        response_check.validate({'run_id': 'run', 'session_id': 'thread', 'pid': 1})
