"""Capture E3's body for `test_catalog.flat_target()` from the built server.

`SRV-FEAT-547` criterion 4 asks whether a target with no `parent:` anywhere still answers what it
always answered. An expectation typed into the suite would be this session's opinion of what the
server used to answer, and that opinion is the thing under test — so the baseline is a recording
of the real binary, taken once and committed.

    cd server && ./build.sh build
    python3 tests/capture_e3_flat.py ../docs/reports/SRV-FEAT-547/e3-flat-<date>.json

The recording is normalised by `test_catalog.normalised()` — the same function the case uses — so
nothing that is a fact about one temporary directory reaches the file.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import conftest                                                            # noqa: E402
import test_catalog                                                        # noqa: E402


class Repo:
    """`conftest.repo`'s object, without pytest."""

    def __init__(self, root):
        self.root = Path(root)
        self.real = Path(os.path.realpath(root))

    def manifest(self, content):
        d = self.root / ".factory"
        d.mkdir(exist_ok=True)
        (d / "factory.json").write_text(json.dumps(content, indent=2))
        return self.root

    def mkdir(self, rel):
        p = self.root / rel
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p


def main(out):
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repo(tmp)
        root = test_catalog.flat_target(repo)
        log = Path(tmp) / "server.log"
        inst = conftest._start_instance(root, log)
        try:
            body = test_catalog.ok(inst, "/api/requirements")
        finally:
            conftest.stop_process(inst.proc)
            inst.handle.close()
        captured = test_catalog.normalised(body, repo)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n")
    print(f"captured {len(captured['requirements'])} rows -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "e3-flat.json")
