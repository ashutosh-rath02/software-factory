import json
import subprocess
from pathlib import Path


def test_codex_requests_escape_input_and_preserve_thread_identity(tmp_path):
    server = Path(__file__).resolve().parents[1]
    source = tmp_path / 'probe.cpp'
    source.write_text(r'''
#include <app/agent/codex_protocol.h>
#include <iostream>
int main() {
    std::cout << agent::codex::initialize(1) << '\n';
    std::cout << agent::codex::startThread(2, "gpt-test", "/tmp/a b", "") << '\n';
    std::cout << agent::codex::startThread(3, "gpt-test", "/tmp/a b", "thread-1") << '\n';
    std::cout << agent::codex::startThread(6, "gpt-test", "/tmp/a b", "", "never") << '\n';
    std::cout << agent::codex::startTurn(4, "thread-1", "quote\"\nline") << '\n';
    std::cout << agent::codex::interrupt(5, "thread-1", "turn-2") << '\n';
}
''')
    binary = tmp_path / 'probe'
    subprocess.run(['g++', '-std=c++17', '-I', str(server), str(source),
                    str(server / 'app/agent/codex_protocol.cpp'),
                    str(server / 'app/manifest/json.cpp'), '-o', str(binary)], check=True)
    frames = [json.loads(line) for line in subprocess.check_output([str(binary)], text=True).splitlines()]
    assert [f['id'] for f in frames] == [1, 2, 3, 6, 4, 5]
    assert [f['method'] for f in frames] == ['initialize', 'thread/start', 'thread/resume',
                                             'thread/start', 'turn/start', 'turn/interrupt']
    assert frames[1]['params']['model'] == 'gpt-test'
    assert frames[1]['params']['cwd'] == '/tmp/a b'
    # Factory is the hard confinement boundary; nesting Codex's Linux sandbox inside it prevents
    # even read-only shell tools from starting.
    assert frames[1]['params']['sandbox'] == 'danger-full-access'
    assert frames[1]['params']['approvalPolicy'] == 'on-request'
    assert frames[2]['params']['threadId'] == 'thread-1'
    assert frames[3]['params']['approvalPolicy'] == 'never'
    assert frames[4]['params']['input'][0]['text'] == 'quote"\nline'
    assert frames[5]['params'] == {'threadId': 'thread-1', 'turnId': 'turn-2'}


def test_codex_transcript_maps_completed_items_and_failed_turn(tmp_path):
    server = Path(__file__).resolve().parents[1]
    source = tmp_path / 'parser.cpp'
    source.write_text(r'''
#include <app/agent/transcript.h>
#include <iostream>
int main() {
    agent::transcript::Parser parser;
    std::string line;
    while (std::getline(std::cin, line))
        for (const auto& entry : parser.feed(line + "\n", 1000))
            std::cout << "{" << entry.members << "}" << '\n';
}
''')
    binary = tmp_path / 'parser'
    subprocess.run(['g++', '-std=c++17', '-I', str(server), str(source),
                    str(server / 'app/agent/transcript.cpp'),
                    str(server / 'app/manifest/json.cpp'), '-o', str(binary)], check=True)
    events = [
        {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'id': 'a', 'text': 'hello'}}},
        {'method': 'item/completed', 'params': {'item': {'type': 'commandExecution', 'id': 'b', 'command': 'pwd', 'status': 'completed', 'aggregatedOutput': '/lane', 'exitCode': 0}}},
        {'method': 'turn/completed', 'params': {'turn': {'id': 't', 'status': 'failed', 'error': {'message': 'unavailable'}}}},
    ]
    output = subprocess.check_output([str(binary)], input=''.join(json.dumps(e) + '\n' for e in events), text=True)
    rows = [json.loads(line) for line in output.splitlines()]
    assert [r['kind'] for r in rows] == ['prose', 'tool', 'error', 'turn']
    assert rows[0]['text'] == 'hello'
    assert rows[1]['ok'] is True and rows[1]['result'] == '/lane'
    assert rows[2]['text'] == 'unavailable'
    assert rows[3]['n'] == 1
