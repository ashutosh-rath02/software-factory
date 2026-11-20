"""Capture review.sh invoking the installed CLI on an intentionally defective throwaway diff."""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from capture_review_cli import AFTER, BEFORE, FACTORY, git

INSTRUCTIONS = """Review only the supplied diff in this synthetic event-driven program.
Shared state must use a serialized executor; introducing a mutex blocks its handler thread.
Report findings as: blocker|minor <file>:<line> <one sentence>, one per line.
Print no preamble or summary, and nothing if there are no findings.
Do not open other files or execute tools; the complete relevant diff is provided below.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).parent / "fixtures/cli-review-codex-2026-09-18.json")
    args = parser.parse_args()
    real = shutil.which("codex")
    if real is None:
        print("the real reviewer is not on PATH", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="srv-feat-558-review-capture-") as work:
        scratch = Path(work)
        root = scratch / "reviewed"
        (root / "app").mkdir(parents=True)
        (root / "docs/technical/workflow").mkdir(parents=True)
        (root / "docs/technical/workflow/REVIEW.md").write_text(INSTRUCTIONS)
        (root / "app/store.cpp").write_text(BEFORE)
        git(root, "init", "-b", "main")
        git(root, "config", "user.email", "factory@test.local")
        git(root, "config", "user.name", "factory capture")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "base")
        git(root, "checkout", "-qb", "ticket/REVIEW")
        (root / "app/store.cpp").write_text(AFTER)
        git(root, "commit", "-qam", "introduce an event-driven mutex")
        head = git(root, "rev-parse", "HEAD").stdout
        log = scratch / "invocation.json"
        bins = scratch / "bin"
        bins.mkdir()
        shim = bins / "codex"
        shim.write_text(f"""#!{sys.executable}
import json, pathlib, subprocess, sys
prompt = sys.stdin.read()
pathlib.Path({str(log)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'stdin': prompt}}))
result = subprocess.run([{real!r}, *sys.argv[1:]], input=prompt, text=True)
sys.exit(result.returncode)
""")
        shim.chmod(0o755)
        env = dict(os.environ, PATH=f"{bins}:{os.environ['PATH']}",
                   FACTORY_REVIEW_DRIVER="codex", FACTORY_REVIEW_MODEL=args.model)
        ran = subprocess.run(["sh", str(FACTORY / ".claude/scripts/review.sh"), "main"], cwd=root, env=env,
                             capture_output=True, text=True, timeout=300)
        called = json.loads(log.read_text())
        argv = called["argv"]
        argv[argv.index("--output-last-message") + 1] = "<final-message>"
        capture = {
            "cli_version": subprocess.run([real, "--version"], capture_output=True,
                                          text=True, check=True).stdout.strip(),
            "captured": datetime.date.today().isoformat(),
            "what": "review.sh invoked the real installed reviewer over synthetic instructions and a synthetic mutex diff; no private repository content was included",
            "model": args.model,
            "argv_tail": argv,
            "stdin_bytes": len(called["stdin"].encode()),
            "stdin_head": called["stdin"][:400],
            "rc": ran.returncode,
            "stdout": ran.stdout,
            "stderr": ran.stderr,
            "finding_lines": [line for line in ran.stdout.splitlines() if line.strip()],
            "workspace_unchanged": not git(root, "status", "--porcelain").stdout
                                   and git(root, "rev-parse", "HEAD").stdout == head,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(capture, indent=2) + "\n")
        print(json.dumps({key: capture[key] for key in
                          ("cli_version", "rc", "finding_lines", "workspace_unchanged")}, indent=2))
        return 0 if ran.returncode == 0 and capture["finding_lines"] and capture["workspace_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
