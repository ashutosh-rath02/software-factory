"""QA end-to-end probe for SRV-FIX-290 — §1's `End-to-end` bullet, on THIS repository.

    "on this repository, `cd factory && ./build.sh run "$PWD"`, claim a lane for a `FAC-*`
     ticket, start it from the browser, and watch the transcript arrive on the card."

The server is started by the suite's own `serve` fixture on an ephemeral port so it cannot be
confused with the operator's live instance on :7778 — same binary, same repository root, same
`--repo` argument `build.sh run "$PWD"` would pass. The lane is released and the run ended before
the probe returns, and the main checkout is compared before and after.
"""

import json
import subprocess
import time
from pathlib import Path

REPO = Path("/home/dev/software-factory")
FACTORY = REPO / "factory"
TICKET = "SRV-FIX-286"          # status: todo, workspace factory-server, no branch yet
PROMPT = ("Reply with exactly the single word FACTORY-E2E-OK and then stop. "
          "Do not read, write or change any file, and do not run any command.")


def snapshot():
    return subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                          capture_output=True, text=True).stdout


def test_e2e_a_har_ticket_runs_in_a_lane_on_this_repository(serve):
    before = snapshot()
    srv = serve(FACTORY)

    health = srv.get("/api/health").json()
    print("PROBE health repo =", json.dumps(health["repo"]))
    assert health["repo"]["root"] == str(FACTORY), health["repo"]
    assert health["repo"]["git_root"] == str(REPO), health["repo"]

    lane = srv.post("/api/lanes", json={"ticket_id": TICKET, "workspace": "factory-server"})
    print("PROBE claim =", lane.status_code, lane.text[:300])
    assert lane.status_code == 201, lane.text
    name = lane.json()["lane"]
    worktree = lane.json()["worktree"]
    run = None
    try:
        started = srv.post(f"/api/lanes/{name}/runs", json={"prompt": PROMPT})
        print("PROBE start =", started.status_code, started.text[:400])
        assert started.status_code in (200, 201, 202), started.text
        run = started.json()["run_id"]

        path = FACTORY / ".factory" / "runs" / run / "transcript.jsonl"
        deadline = time.time() + 180
        entries = []
        while time.time() < deadline:
            if path.exists():
                entries = [l for l in path.read_text().splitlines() if l.strip()]
            if any('"kind":"prose"' in e for e in entries):
                break
            time.sleep(0.5)

        print("PROBE transcript =", path, "entries =", len(entries))
        for e in entries[:20]:
            print("PROBE  |", e[:300])

        lanes = srv.get("/api/lanes").json()["lanes"]
        print("PROBE lane card =", json.dumps(lanes[0])[:600] if lanes else "NONE")

        assert entries, f"no transcript ever arrived at {path}"
        assert any('"kind":"prose"' in e for e in entries), (
            "the agent produced no prose on the transcript — it did not run to an answer")
    finally:
        if run:
            print("PROBE end =", srv.post(f"/api/lanes/{name}/end", json={}).status_code)
        rel = srv.delete(f"/api/lanes/{name}?force=true")
        print("PROBE release =", rel.status_code, rel.text[:200])
        print("PROBE worktree still on disk =", Path(worktree).exists())

    after = snapshot()
    assert after == before, f"THE MAIN CHECKOUT CHANGED\nbefore:\n{before}\nafter:\n{after}"
