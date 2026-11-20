"""The linked-worktree shape, on a real one: this QA checkout.

`/home/dev/qa290` is `git worktree add`ed from the monorepo, so `/home/dev/qa290/.git`
is a FILE. `factory/` inside it is the same shape SRV-FIX-290 set out to serve.
"""

import json
import subprocess
from pathlib import Path

WT = Path("/home/dev/qa290")


def test_probe_serving_factory_inside_a_real_linked_worktree(serve):
    assert (WT / ".git").is_file(), "fixture assumption wrong"
    print("PROBE git rev-parse --show-toplevel =", subprocess.run(
        ["git", "-C", str(WT / "factory"), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True).stdout.strip())
    print("PROBE git rev-parse --git-common-dir =", subprocess.run(
        ["git", "-C", str(WT / "factory"), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True).stdout.strip())

    srv = serve(WT / "factory")
    print("PROBE health repo =", json.dumps(srv.get("/api/health").json()["repo"]))
    lane = srv.post("/api/lanes", json={"ticket_id": "SRV-FIX-286",
                                        "workspace": "factory-server"})
    print("PROBE claim =", lane.status_code, lane.text[:400])
