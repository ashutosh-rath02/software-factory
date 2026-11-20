import test_transcript
from test_transcript import env


def test_round_trip_waits_for_persisted_killed_not_any_old_entry(env, monkeypatch):
    wait_kind = env.wait_kind
    entries_on_disk = env.entries_on_disk
    before_kill = {}
    observed_killed = set()

    def wait_for_consequence(run, kind, timeout=20):
        entries = wait_kind(run, kind, timeout=timeout)
        if kind == "silence":
            assert entries and not any(entry["kind"] == "killed" for entry in entries)
            before_kill[run["run_id"]] = entries
        if kind == "killed":
            assert any(entry["kind"] == "killed" for entry in entries)
            observed_killed.add(run["run_id"])
        return entries

    def count_based_snapshot(run, timeout=15, atleast=1):
        run_id = run["run_id"]
        if run_id in before_kill and run_id not in observed_killed:
            snapshot = before_kill[run_id]
            assert len(snapshot) >= atleast
            return snapshot
        return entries_on_disk(run, timeout=timeout, atleast=atleast)

    monkeypatch.setattr(env, "wait_kind", wait_for_consequence)
    monkeypatch.setattr(env, "entries_on_disk", count_based_snapshot)

    test_transcript.test_every_kind_in_the_table_round_trips(env)

    assert len(before_kill) == 1
    assert observed_killed == set(before_kill)
