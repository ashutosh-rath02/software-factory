#!/usr/bin/env python3
"""The ticket gate: tiers 1 and 2 of CLAUDE.md, answered against a known-red baseline.

The full suite (~1,050 cases, ~35 minutes, red on `main` for reasons older than any ticket) is the
RELEASE gate. A ticket runs:

    tier 1   every case its §1 names on an `Evidence: server/tests/...::case` line
    tier 2   the test modules of every server module and tool the branch touched,
             plus test_isolation.py and test_confine.py, always

Cases that call a real provider CLI are switched off (`FACTORY_REAL_CLI=0`); they need a login a
lane does not carry and belong to the release tier — `--live` runs them.

A failure listed in `tests/known-red.txt` is pre-existing and reported, not counted. Anything else
that fails, and any named Evidence case that does not exist, is this branch's.

    ./tools/tier_gate.py                      ticket from --ticket, $FACTORY_TICKET_ID or the branch
    ./tools/tier_gate.py --ticket SRV-FEAT-573
    ./tools/tier_gate.py --list               print the selection and exit

    exit 0   nothing new is red
    exit 1   something new is red, or a named case is missing; each is named
    exit 2   the question could not be answered
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
ROOT = SERVER.parent
TESTS = SERVER / "tests"
KNOWN_RED = TESTS / "known-red.txt"
ALWAYS = ["tests/test_isolation.py", "tests/test_confine.py"]
EVIDENCE = re.compile(r"Evidence:\s*server/(tests/[\w/.-]+\.py)(?:::([\w\[\]-]+))?")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def ticket_id(given):
    if given:
        return given
    if os.environ.get("FACTORY_TICKET_ID"):
        return os.environ["FACTORY_TICKET_ID"]
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    m = re.match(r"ticket/([A-Z]+-[A-Z]+-\d+)", branch)
    return m.group(1) if m else None


def tier1(tid):
    found = sorted((ROOT / "docs" / "tickets").glob(f"{tid}-*.md"))
    if len(found) != 1:
        return None
    nodes = []
    for path, case in EVIDENCE.findall(found[0].read_text()):
        nodes.append(f"{path}::{case}" if case else path)
    return nodes


def tier2():
    base = git("merge-base", "main", "HEAD").stdout.strip()
    if not base:
        return None
    changed = set(git("diff", "--name-only", base).stdout.split())
    changed |= set(git("ls-files", "--others", "--exclude-standard").stdout.split())
    files = set(ALWAYS)
    for name in changed:
        parts = Path(name).parts
        stem = None
        if len(parts) >= 3 and parts[:2] == ("server", "app"):
            stem = parts[2]
        elif len(parts) == 3 and parts[:2] == ("server", "tools") and name.endswith(".py"):
            stem = Path(name).stem
        elif len(parts) >= 3 and parts[:2] == ("server", "tests") and name.endswith(".py"):
            files.add(str(Path(*parts[1:])))
        if stem:
            for match in TESTS.glob(f"test_{stem}*.py"):
                files.add(str(match.relative_to(SERVER)))
    return sorted(f for f in files if (SERVER / f).exists())


def known_red():
    if not KNOWN_RED.exists():
        return set()
    return {l.split("#")[0].strip() for l in KNOWN_RED.read_text().splitlines()
            if l.split("#")[0].strip()}


def main():
    ap = argparse.ArgumentParser(description="tiers 1 and 2 against a known-red baseline")
    ap.add_argument("--ticket")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--live", action="store_true",
                    help="also run the cases that call a real provider CLI (release tier)")
    args = ap.parse_args()

    tid = ticket_id(args.ticket)
    if not tid:
        print("tier-gate: no ticket — pass --ticket, set FACTORY_TICKET_ID or check out ticket/<id>")
        return 2
    t1 = tier1(tid)
    t2 = tier2()
    if t1 is None or t2 is None:
        print(f"tier-gate: cannot resolve {'the ticket file' if t1 is None else 'the merge-base with main'} for {tid}")
        return 2

    missing = [n for n in t1 if not (SERVER / n.split("::")[0]).exists()]
    covered = [n for n in t1 if n.split("::")[0] not in t2 and n not in missing]
    selection = t2 + covered
    print(f"tier-gate {tid}: tier 1 {len(t1)} named case(s) · tier 2 {len(t2)} module(s)")
    for s in selection:
        print(f"  {s}")
    if args.list:
        return 0

    report = SERVER / ".." / ".cache" / "tier-gate" / f"{tid}.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if not args.live:
        env["FACTORY_REAL_CLI"] = "0"
    runner = [shutil.which("pytest")] if shutil.which("pytest") else [sys.executable, "-m", "pytest"]
    proc = subprocess.run([*runner, "-q", "-p", "no:cacheprovider", "-rfE", *selection],
                          cwd=SERVER, env=env, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    report.write_text(out)
    summary = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed|error)", l)]
    print(summary[-1] if summary else out[-2000:])

    if not summary:
        print(f"tier-gate {tid}: NOT RUN — pytest produced no result (exit {proc.returncode}); "
              f"log {report.resolve()}")
        return 2
    if proc.returncode == 4:
        print(f"tier-gate {tid}: RED — a named Evidence case does not exist; log {report.resolve()}")
        return 1
    red = {re.sub(r" - .*", "", l.split(" ", 1)[1]) for l in out.splitlines()
           if l.startswith(("FAILED ", "ERROR "))}
    if proc.returncode not in (0, 1) and not red:
        print(f"tier-gate: pytest exited {proc.returncode} without a result; log {report.resolve()}")
        return 2
    known = known_red()
    new = sorted(red - known)
    for n in sorted(red & known):
        print(f"  known red (pre-existing): {n}")
    for n in missing:
        print(f"  MISSING named evidence file: {n}")
    for n in new:
        print(f"  NEW RED: {n}")
    print(f"log: {report.resolve()}")
    if new or missing:
        print(f"tier-gate {tid}: RED — {len(new)} new failure(s), {len(missing)} missing evidence file(s)")
        return 1
    print(f"tier-gate {tid}: GREEN — nothing new is red ({len(red & known)} known red)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
