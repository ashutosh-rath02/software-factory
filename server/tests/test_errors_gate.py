import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import test_errors
import test_spec
from test_errors import refusals


SERVER = Path(__file__).resolve().parents[1]
CORPUS = SERVER / 'tests/test_errors.py'


def collected(path):
    env = dict(os.environ, PYTHONPATH=str(SERVER / 'tests'))
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', str(path), '-m', 'repository',
         '--collect-only', '-q', '-o', 'addopts='],
        cwd=SERVER, env=env, capture_output=True, text=True, timeout=60)
    names = {line.split('::', 1)[1].strip() for line in result.stdout.splitlines()
             if '::test_' in line and line.split('::', 1)[0].endswith(path.name)}
    return result, names


@pytest.mark.repository
def test_repository_gate_collects_the_real_refusal_corpus():
    declared = {node.name for node in ast.parse(CORPUS.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}
    assert declared, 'the refusal corpus declares no assertions'
    result, names = collected(CORPUS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert names == declared, (
        f'repository GATE omitted real refusal assertions: {sorted(declared - names)}; '
        f'unexpected assertions: {sorted(names - declared)}\n{result.stdout}{result.stderr}')


@pytest.mark.repository
def test_repository_selection_negative_omits_an_unmarked_corpus(tmp_path):
    source = CORPUS.read_text()
    marker = 'pytestmark = pytest.mark.repository\n'
    count = source.count(marker)
    assert count == 1, 'the real corpus has no single repository marker'
    copied = tmp_path / 'server/tests' / CORPUS.name
    copied.parent.mkdir(parents=True)
    (tmp_path / 'docs').symlink_to(SERVER.parent / 'docs', target_is_directory=True)
    copied.write_text(source.replace(marker, '', 1))
    result, names = collected(copied)
    assert result.returncode == 5, result.stdout + result.stderr
    assert names == set(), names


@pytest.mark.repository
def test_inventory_includes_every_refusal_the_real_corpus_reached(refusals):
    reached = {entry['code'] for entry in refusals.seen}
    assert reached, 'the live refusal corpus reached no error body'
    emitted = set(test_spec.error_codes())
    assert reached <= emitted, f'actual HTTP codes invisible to the source inventory: {sorted(reached - emitted)}'


@pytest.mark.repository
def test_new_literal_factory_refusal_cannot_escape_the_real_corpus_guard(refusals, tmp_path, monkeypatch):
    copied = tmp_path / 'app'
    shutil.copytree(test_spec.APP, copied)
    source = copied / 'routing/store.cpp'
    text = source.read_text()
    assert '"routing_busy"' in text
    source.write_text(text.replace('"routing_busy"', '"fixture_uncovered_factory"', 1))
    monkeypatch.setattr(test_spec, 'APP', copied)
    monkeypatch.setattr(test_spec, 'SERVER', copied.parent)
    with pytest.raises(AssertionError, match='fixture_uncovered_factory'):
        test_errors.test_the_corpus_reaches_every_code_it_does_not_excuse(refusals)


@pytest.mark.repository
def test_new_factory_name_and_alias_return_type_preserve_real_coverage(refusals, tmp_path, monkeypatch):
    copied = tmp_path / 'app'
    shutil.copytree(test_spec.APP, copied)
    source = copied / 'api/routing.cpp'
    text = source.read_text()
    factory = next(match[0] for match in test_spec.FUNCTION_START.finditer(text)
                   if match[1] == 'api::Routing::error')
    new_factory = factory.replace('http::Response api::Routing::error',
                                  'SourceCopyResponse source_copy_refusal', 1)
    added = ('\nusing SourceCopyResponse = http::Response;\n' + new_factory
             + '\nSourceCopyResponse source_copy_probe()\n{\n'
             + '    return source_copy_refusal(409, "fixture_alias_refusal", "owned copy", {});\n}\n'
             + '\nnamespace unrelated {\nstd::string error(int status, const std::string& message);\n'
             + 'std::string probe();\n}\n'
             + '\nstd::string unrelated::error(int status, const std::string& message)\n{\n'
             + '    return message;\n}\n'
             + '\nstd::string unrelated::probe()\n{\n'
             + '    return error(409, "fixture_non_error_message");\n}\n')
    source.write_text(text + added)
    monkeypatch.setattr(test_spec, 'APP', copied)
    monkeypatch.setattr(test_spec, 'SERVER', copied.parent)
    emitted = test_spec.error_codes()
    assert 'fixture_alias_refusal' in emitted, 'an ordinary alias return type hid a real code factory'
    assert 'fixture_non_error_message' not in emitted, 'an unrelated namespace helper became a refusal factory'
    with pytest.raises(AssertionError, match='fixture_alias_refusal'):
        test_errors.test_the_corpus_reaches_every_code_it_does_not_excuse(refusals)
    source.write_text((text + added).replace('"fixture_alias_refusal"', '"bad_request"'))
    test_errors.test_the_corpus_reaches_every_code_it_does_not_excuse(refusals)
