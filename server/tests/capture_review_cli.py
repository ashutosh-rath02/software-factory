"""SRV-FEAT-500 — record the REAL agent CLI doing the job `.claude/scripts/review.sh` needs.

`docs/operations/ESCAPES.md`: `SRV-FEAT-009`'s stand-in read plain lines because the test wrote it to, the
tool wrote plain lines for the same reason, and nothing checked either against the CLI. So the
stand-in `claude` that `test_spec.py::test_this_repository_declares_a_review_per_workspace`
spawns is not trusted on its own — this script records what the real program does when
`review.sh` runs it, and the committed capture is the arbiter.

The capture is taken **while the program does the job the product needs**: a real git repository
with a real diff, `review.sh` itself as the caller, and a shim first on `PATH` that records the
argv and the stdin `review.sh` built and then `exec`s the real CLI. What is recorded is therefore
the product's own invocation, not a re-typed approximation of it.

To re-capture (costs one real model call, takes about a minute):

    cd server && python3 tests/capture_review_cli.py

It writes `tests/fixtures/cli-review-<date>-review-sh-prompt-to-findings.json`.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FACTORY = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE_DATE = "2026-09-11"
NAME = f"cli-review-{CAPTURE_DATE}-review-sh-prompt-to-findings.json"

# A diff that really does violate two of REVIEW.md's passes, so the recording shows the program
# producing findings rather than producing nothing. A capture of a clean diff proves only that
# the CLI can be started.
BEFORE = """#include "store.h"

void app::Store::note(const std::string& what)
{
    entries_.push_back(what);
}
"""
AFTER = """#include "store.h"

#include <mutex>

static std::mutex g_lock;

void app::Store::note(const std::string& what)
{
    std::lock_guard<std::mutex> held(g_lock);
    entries_.push_back(what);
}
"""

SHIM = """#!/bin/sh
log="%s"
printf 'argv:' >> "$log"
for a in "$@" ; do printf ' %%s' "$a" >> "$log" ; done
printf '\\n' >> "$log"
tee -a "$log.stdin" | exec %s "$@"
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args),
                          capture_output=True, text=True, check=True)


def main():
    real = shutil.which("claude")
    if real is None:
        print("the real agent CLI is not on PATH — nothing to record", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="srv-feat-500-capture-"))
    root = work / "reviewed"
    (root / "app").mkdir(parents=True)
    (root / "docs/technical/workflow").mkdir(parents=True)
    shutil.copy(FACTORY / "docs/technical/workflow/REVIEW.md", root / "docs/technical/workflow/REVIEW.md")
    shutil.copytree(FACTORY / ".claude", root / ".claude")
    (root / "app" / "store.cpp").write_text(BEFORE)

    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "factory@test.local")
    git(root, "config", "user.name", "factory capture")
    git(root, "add", "-A")
    git(root, "commit", "-m", "base")
    git(root, "checkout", "-b", "ticket/TCK-500")
    (root / "app" / "store.cpp").write_text(AFTER)
    git(root, "add", "-A")
    git(root, "commit", "-m", "TCK-500: serialise the store")

    log = work / "claude.log"
    shim_dir = work / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "claude"
    shim.write_text(SHIM % (log, real))
    shim.chmod(0o755)

    env = dict(os.environ, PATH=f"{shim_dir}:{os.environ['PATH']}")
    ran = subprocess.run(["sh", ".claude/scripts/review.sh", "main"],
                         cwd=root, env=env, capture_output=True, text=True, timeout=900)

    argv_line = next((l for l in log.read_text().splitlines() if l.startswith("argv:")), "")
    stdin_path = Path(f"{log}.stdin")
    prompt = stdin_path.read_text() if stdin_path.exists() else ""

    capture = {
        "cli_version": subprocess.run([real, "--version"], capture_output=True,
                                      text=True).stdout.strip(),
        "captured": CAPTURE_DATE,
        "what": "`.claude/scripts/review.sh` running the real CLI over a real diff that violates "
                "two of docs/technical/workflow/REVIEW.md's passes",
        "argv_tail": argv_line[len("argv:"):].split(),
        "stdin_bytes": len(prompt),
        "stdin_head": prompt[:400],
        "rc": ran.returncode,
        "stdout": ran.stdout,
        "stderr": ran.stderr[-2000:],
        "finding_lines": [l for l in ran.stdout.splitlines() if l.strip()],
    }
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / NAME).write_text(json.dumps(capture, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in capture.items() if k != "stdin_head"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
