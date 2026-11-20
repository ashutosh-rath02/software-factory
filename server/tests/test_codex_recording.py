"""The installed app-server recording, not the peer, defines file-change input."""

import copy
import json
from pathlib import Path
import subprocess

import pytest


CAPTURE = Path(__file__).parent / "fixtures/cli-app-server-codex-2026-09-18.json"


@pytest.fixture(scope="module")
def recording():
    capture = json.loads(CAPTURE.read_text())
    assert capture["fixture_verified"] is True
    assert capture["credentials_unchanged"] is True
    assert capture["process_exit"] == 0
    assert capture["cli_version"].startswith("codex-cli ")
    return [json.loads(row["raw"]) for row in capture["records"] if row["direction"] == "server"]


@pytest.fixture(scope="module")
def parser(tmp_path_factory):
    server = Path(__file__).resolve().parents[1]
    work = tmp_path_factory.mktemp("codex-recording-parser")
    source = work / "probe.cpp"
    source.write_text(r'''
#include <app/agent/transcript.h>
#include <app/manifest/json.h>
#include <iostream>
int main() {
    agent::transcript::Parser parser;
    std::string line;
    while (std::getline(std::cin, line)) {
        for (const auto& entry : parser.feed(line + "\n", 1000)) {
            if (entry.kind.empty()) continue;
            std::cout << "{\"entry\":{" << entry.members
                      << "},\"has_words\":" << (entry.hasWords ? "true" : "false")
                      << ",\"words_kind\":" << manifest::jstr(entry.wordsKind)
                      << ",\"words\":" << manifest::jstr(entry.words) << "}\n";
        }
    }
}
''')
    binary = work / "probe"
    subprocess.run(["g++", "-std=c++17", "-I", str(server), str(source),
                    str(server / "app/agent/transcript.cpp"),
                    str(server / "app/manifest/json.cpp"), "-o", str(binary)], check=True)

    def replay(frames):
        result = subprocess.check_output([str(binary)], text=True,
                    input="".join(json.dumps(frame) + "\n" for frame in frames))
        return [json.loads(line) for line in result.splitlines()]
    return replay


def file_changes(recording):
    frames = [frame for frame in recording if frame.get("method") == "item/completed"
              and frame.get("params", {}).get("item", {}).get("type") == "fileChange"]
    assert frames, "the committed real recording must contain fileChange completions"
    changes = [change for frame in frames for change in frame["params"]["item"]["changes"]]
    assert {Path(change["path"]).name for change in changes} == {
        "created.txt", "modify.txt", "delete.txt"}
    return frames


def test_real_file_changes_preserve_every_path_diff_and_line_count(recording, parser):
    frames = file_changes(recording)
    changes = [change for frame in frames for change in frame["params"]["item"]["changes"]]
    rows = parser(frames)
    assert len(rows) == len(changes), "a completed file change must not disappear as unmapped"
    expected_counts = {"created.txt": (1, 0), "modify.txt": (1, 1), "delete.txt": (0, 1)}
    for row, change in zip(rows, changes):
        entry = row["entry"]
        assert entry["kind"] == "file_edit"
        assert entry["path"] == change["path"]
        assert entry["diff"] == change["diff"]
        assert (entry["added"], entry["removed"]) == expected_counts[Path(change["path"]).name]
        assert entry["ok"] is True
        assert row["has_words"] and row["words_kind"] == "file_edit"
        assert change["path"] in row["words"]


@pytest.mark.parametrize("status", ["failed", "declined", "inProgress", ""])
def test_unsuccessful_file_changes_never_claim_success(recording, parser, status):
    frames = copy.deepcopy(file_changes(recording))
    for frame in frames:
        frame["params"]["item"]["status"] = status
    rows = parser(frames)
    assert rows, "unsuccessful edits must remain visible instead of vanishing"
    assert all(row["entry"]["ok"] is False for row in rows)


def test_start_notifications_do_not_duplicate_completed_file_changes(recording, parser):
    completed = file_changes(recording)
    started = [frame for frame in recording if frame.get("method") == "item/started"
               and frame.get("params", {}).get("item", {}).get("type") == "fileChange"]
    assert started, "the recording must preserve both real lifecycle notifications"
    assert parser(started) == []
    assert parser(started + completed) == parser(completed)


def test_recorded_message_command_and_turn_still_replay(recording, parser):
    rows = parser(recording)
    kinds = {row["entry"]["kind"] for row in rows}
    assert {"prose", "tool", "file_edit", "turn"} <= kinds
    assert any(row["entry"].get("ok") is True for row in rows if row["entry"]["kind"] == "tool")


def test_an_empty_file_change_cannot_invent_an_edit(recording, parser):
    frame = copy.deepcopy(file_changes(recording)[0])
    frame["params"]["item"]["changes"] = []
    assert parser([frame]) == []
