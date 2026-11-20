"""SRV-FIX-571: receipt-only changes must wake a refused automatic landing."""

import json
import time

from test_land import (
    REVIEWER, RPT_DIR, TKT_DIR, delivery_of, land_lines, plant_role_run, spawned,
    serve_fast, set_status, ticket_on_main, verdict_text, wait_until,
    work_branch, world,
)


def independent_candidate(w, tid, *, fail_first=False):
    ticket_on_main(w, tid)
    work_branch(w, tid)
    qa = w.worktree(f"ticket/{tid}-qa", base=f"ticket/{tid}")
    if fail_first:
        w.write(f"{RPT_DIR}/{tid}-qa.md", verdict_text(tid, "FAIL"), cwd=qa)
        w.git.commit(f"{tid}: independent FAIL", who=REVIEWER, cwd=qa)
        w.failed_tip = w.tip("HEAD", cwd=qa)
    w.write(f"{RPT_DIR}/{tid}-qa.md", verdict_text(tid, "PASS"), cwd=qa)
    set_status(w, tid, "pass", qa)
    w.git.commit(f"{tid}: independently accepted", who=REVIEWER, cwd=qa)
    return w.tip("HEAD", cwd=qa)


def refusal(srv, tid):
    return wait_until(
        "missing independent session refusal",
        lambda: (lambda d: d if "no session recorded" in d.get("detail", "")
                 else None)(delivery_of(srv, tid)),
        timeout=12,
    )


def accepted_delivery(srv, tid, timeout=10):
    return wait_until(
        "accepted delivery with refusal cleared",
        lambda: (lambda d: d if d["state"] == "done"
                 and "no session recorded" not in d["detail"]
                 else None)(delivery_of(srv, tid)),
        timeout=timeout,
    )


def test_late_independent_receipt_retries_without_git_changes(world, serve):
    w = world
    tid = "T-571-LATE"
    tip = independent_candidate(w, tid)
    run_id = plant_role_run(w, tid, "qa", tip="")
    w.push_main()
    srv = serve_fast(serve, w.root)
    refusal(srv, tid)
    before = w.git("for-each-ref", "--format=%(refname) %(objectname)")
    meta = w.root / ".factory/runs" / run_id / "meta.json"
    record = json.loads(meta.read_text())
    record["branch_tip"] = tip
    meta.write_text(json.dumps(record))
    assert w.git("for-each-ref", "--format=%(refname) %(objectname)") == before
    wait_until("receipt-only landing", lambda: w.tip("main") != w.remote_main()
               or w.git.ok("show", f"main:src/{tid}.txt"), timeout=8)
    wait_until("accepted work pushed", lambda: w.remote_main() == w.tip("main"), timeout=8)
    result = accepted_delivery(srv, tid, timeout=8)
    assert "no session recorded" not in result["detail"]
    assert w.git("show", f"main:src/{tid}.txt") == f"{tid} did something"


def test_invalid_receipt_updates_do_not_authorize_landing(world, serve):
    w = world
    tid = "T-571-INVALID"
    tip = independent_candidate(w, tid, fail_first=True)
    run_id = plant_role_run(w, tid, "qa", tip="")
    w.push_main()
    srv = serve_fast(serve, w.root)
    refusal(srv, tid)
    before = w.tip("main"), w.remote_main()
    meta = w.root / ".factory/runs" / run_id / "meta.json"
    record = json.loads(meta.read_text())
    for changes in ({"role": "run", "branch_tip": tip},
                    {"role": "qa", "ticket_id": "T-UNRELATED", "branch_tip": tip},
                    {"role": "qa", "ticket_id": tid, "branch_tip": w.failed_tip},
                    {"role": "qa", "ticket_id": tid, "branch_tip": ""}):
        record.update(changes)
        meta.write_text(json.dumps(record))
        time.sleep(.8)
        assert (w.tip("main"), w.remote_main()) == before
        assert delivery_of(srv, tid)["state"] != "done"


def start_proof_session(w, tid, serve):
    from pathlib import Path
    import stand_in

    manifest = json.loads((w.root / ".factory/factory.json").read_text())
    manifest["agent"] = {"command": "/bin/sh proof-agent.sh"}
    manifest["roles"] = {"qa": {"start_on_stage": "QA", "lane": "new",
                                "prompt_file": ".factory/prompts/qa.md"}}
    w.write(".factory/factory.json", json.dumps(manifest))
    w.write(".factory/prompts/qa.md", "Produce the independent verdict for {ticket_id}.")
    agent = "#!/bin/sh\nmkdir -p .factory/runs\nlog=.factory/runs/proof-frames.log\n" + stand_in.FRAME_READER + f"""
while read_frame; do
    printf '# {tid} — QA verdict — PASS\\n' > {RPT_DIR}/{tid}-qa.md
    sed -i 's/status: awaiting-qa/status: pass/' {TKT_DIR}/{tid}.md
    git add {TKT_DIR}/{tid}.md {RPT_DIR}/{tid}-qa.md
    git -c user.name='Reviewer Three' -c user.email=reviewer@example.invalid commit -m '{tid}: independent PASS' >> "$log" 2>&1
    mkdir -p .factory/runs
    git rev-parse HEAD > .factory/runs/proof-ready
done
"""
    w.write("proof-agent.sh", agent)
    w.git.commit("local recorded-frame proof agent")
    ticket_on_main(w, tid)
    work_branch(w, tid)
    w.push_main()
    srv = serve_fast(serve, w.root)
    claimed = srv.post("/api/lanes", json={"ticket_id": tid, "workspace": "alpha", "role": "qa"})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()
    started = srv.post(f"/api/lanes/{lane['lane']}/runs", json={"prompt": "Write the verdict"})
    assert started.status_code == 201, started.text
    run_id = started.json()["run_id"]
    marker = Path(lane["worktree"]) / ".factory/runs/proof-ready"
    wait_until("agent committed its verdict", marker.exists, timeout=15)
    tip = marker.read_text().strip()
    refusal(srv, tid)
    return srv, lane, run_id, tip


def test_session_end_receipt_triggers_landing(world, serve):
    w = world
    tid = "T-571-END"
    srv, lane, run_id, tip = start_proof_session(w, tid, serve)
    meta = w.root / ".factory/runs" / run_id / "meta.json"
    assert not json.loads(meta.read_text()).get("branch_tip")
    before = w.git("for-each-ref", "--format=%(refname) %(objectname)")
    ended = srv.post(f"/api/lanes/{lane['lane']}/end", json={})
    assert ended.status_code == 202, ended.text
    wait_until("authentic completed-session tip", lambda: json.loads(meta.read_text()).get("branch_tip") == tip,
               timeout=15)
    wait_until("real session completion lands accepted work",
               lambda: w.git.ok("show", f"main:src/{tid}.txt"), timeout=10)
    wait_until("completed session work pushed", lambda: w.remote_main() == w.tip("main"), timeout=10)
    result = accepted_delivery(srv, tid)
    assert "no session recorded" not in result["detail"]
    assert before != w.git("for-each-ref", "--format=%(refname) %(objectname)")


def test_receipt_retries_are_scoped_and_idempotent(world, serve):
    w = world
    valid, invalid = "T-571-VALID", "T-571-STALE"
    valid_tip = independent_candidate(w, valid)
    independent_candidate(w, invalid)
    valid_run = plant_role_run(w, valid, "qa", tip="")
    invalid_run = plant_role_run(w, invalid, "qa", tip="")
    other_invalid_run = plant_role_run(w, invalid, "qa", tip="", run_id="run-second-stale")
    w.push_main()
    srv = serve_fast(serve, w.root)
    refusal(srv, valid)
    refusal(srv, invalid)
    valid_meta = w.root / ".factory/runs" / valid_run / "meta.json"
    record = json.loads(valid_meta.read_text())
    record["branch_tip"] = valid_tip
    valid_meta.write_text(json.dumps(record))
    wait_until("only valid delayed receipt lands", lambda: w.git.ok("show", f"main:src/{valid}.txt"),
               timeout=10)
    wait_until("valid work pushed", lambda: w.remote_main() == w.tip("main"), timeout=10)
    assert not w.git.ok("show", f"main:src/{invalid}.txt")
    assert accepted_delivery(srv, valid)["state"] == "done"
    assert delivery_of(srv, invalid)["state"] != "done"
    time.sleep(.5)
    pushed = [argv for argv in spawned(srv) if "push" in argv]
    refused = [line for line in land_lines(srv) if f"land: {invalid}" in line
               and "no session recorded" in line]
    assert refused
    before = w.tip("main"), w.remote_main()
    orders = []
    for n in range(3):
        record["duration_s"] = n + 400
        valid_meta.write_text(json.dumps(record, sort_keys=bool(n % 2)))
        wrong = json.loads((w.root / ".factory/runs" / invalid_run / "meta.json").read_text())
        wrong["duration_s"] = n + 500
        wrong["started_iso"] = f"2026-09-{8 if n % 2 else 6:02d}T10:00:00Z"
        (w.root / ".factory/runs" / invalid_run / "meta.json").write_text(json.dumps(wrong))
        time.sleep(.7)
        rows = srv.get(f"/api/tickets/{invalid}/runs").json()["runs"]
        orders.append(tuple(row["run_id"] for row in rows))
    assert set(orders) == {(invalid_run, other_invalid_run), (other_invalid_run, invalid_run)}
    assert (w.tip("main"), w.remote_main()) == before
    assert [argv for argv in spawned(srv) if "push" in argv] == pushed
    assert [line for line in land_lines(srv) if f"land: {invalid}" in line
            and "no session recorded" in line] == refused
    assert not w.git.ok("show", f"main:src/{invalid}.txt")
