#!/usr/bin/env python3
"""Handover gate: is what §1 names actually there, and was it ever watched fail?

Every other gate asks whether the code works. This one asks whether the HANDOVER is answerable —
does the case named after `Evidence:` exist, is there a committed log in which it is red, is there
one in which it is green. Those are the three facts QA's first blocker is usually about, and all
three can be read before a QA session spends forty minutes finding them by hand.

    server/tools/handover.py docs/tickets/SRV-FEAT-480-....md
    server/tools/handover.py docs/tickets/<file>.md --reports docs/reports/<id>

Exit 0 every case complete · 1 at least one incomplete · 2 nothing to check.

The result must be THIS CASE'S RESULT on the same line, or the log proves nothing. A name in a
command line or a `--collect-only` listing is a grep saying yes about a run that never happened;
so is the word `FAIL` inside a parametrised id `…::test_a_verdict_is_recorded[FAIL]` printed on a
`PASSED` line, the word `PASS` inside `FAILED …::test_x - AssertionError: assert 'FAIL' == 'PASS'`,
and the word `FAIL` inside a vitest suite title on a `✓` line. Both runners put the result in one
of two places and it is read only there: immediately after the case's address (`<nodeid> PASSED`),
or as the line's leading token (`FAILED <nodeid> - …`, `FAIL  <file> > …`, `✓`, `×`, `✗`).

And the result belongs to the case whose ADDRESS it is beside. A leading token is this case's only
when no other case address precedes it on the line — otherwise a failure of `test_rows` that
quotes a rendered row inside its message hands a red column to every case named in that row. A
mention inside a `file::name` address is this case's only when the file matches, by path parts
from the right, so a red and a green run of `somewhere/else/test_other.py::test_x` evidence
nothing about `server/tests/test_x.py::test_x`.

vitest only prints a per-case `✓` for a slow test, so capture the client suite with
`--reporter=verbose` if a client case is to be evidenced by its log.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

CASE = re.compile(r"([\w./+-]+\.(?:py|ts|tsx))::([A-Za-z_][\w\[\].-]*)")
ITEM = re.compile(r"^([ \t]*)(\d+|[A-Z]+[0-9]*(?:-[A-Z]+)?)\.\s", re.M)
START = re.compile(r"^(?:#{1,4}\s*Pass criteria|[ \t]*[-*]\s*\**Pass criteria)", re.M)
NODE = re.compile(r"<(?:Function|TestCaseFunction)\s+(.+?)>")
TITLE = re.compile(r"\b(?:it|test|describe)(?:\.\w+)?\s*(?:\([^)]*\)\s*)?\(\s*[\"'`]([^\"'`]+)")

PRUNE = {".git", ".cache", "node_modules", ".cmake_build", ".pytest_cache", "worktrees",
         ".venv", "build", "dist"}

RED = {"FAILED", "FAIL", "✗", "×"}
GREEN = {"PASSED", "PASS", "✓"}
MARK = r"(?:(?<![A-Za-z])(?:FAILED|FAIL|PASSED|PASS)(?![A-Za-z])|[✗×✓])"
LEADS = re.compile(r"^[ \t]*(" + MARK + r")(?=[ \t])")
FOLLOWS = re.compile(r"[ \t]+(" + MARK + r")(?![A-Za-z])")


def pass_blocks(text):
    lines = text.splitlines(keepends=True)
    starts = {i for i, line in enumerate(lines) if START.match(line)}
    for start in sorted(starts):
        block, fenced = [lines[start]], False
        for i in range(start + 1, len(lines)):
            if i in starts:
                break
            if lines[i].lstrip().startswith("```"):
                fenced = not fenced
            elif not fenced and re.match(r"#{1,4}\s|[-*]\s", lines[i]):
                break
            block.append(lines[i])
        yield "".join(block)


def criteria(text):
    out = []
    for block in pass_blocks(text):
        marks = list(ITEM.finditer(block))
        if not marks:
            continue
        level = min(len(m.group(1)) for m in marks)
        marks = [m for m in marks if len(m.group(1)) == level]
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
            out.append((m.group(2), block[m.end():end]))
    return out


def evidence(body):
    m = re.search(r"Evidence:\s*", body)
    if not m:
        return ""
    tail = re.sub(r"::[ \t]*\n[ \t]*", "::", body[m.end():])
    tail = re.sub(r"(?<=[\w.+-])[ \t]*\n[ \t]*(?=::)", "", tail)
    return " ".join(tail.split())


def resolve(spelling, root):
    found = []
    for base in (root, root / "server", Path.cwd()):
        candidate = base / spelling
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    if found:
        return found
    for candidate in sorted(root.rglob(Path(spelling).name)):
        if set(candidate.relative_to(root).parts) & PRUNE:
            continue
        wanted = Path(spelling).parts
        if candidate.parts[-len(wanted):] == wanted:
            found.append(candidate)
    return found


def collected(path):
    cwd = path.parent.parent if path.parent.name == "tests" else path.parent
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                            "-p", "no:cacheprovider", str(path)],
                           cwd=str(cwd), capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in (r.stdout + r.stderr).splitlines():
        line = line.strip()
        if "::" in line:
            names.add(line.split("::")[-1].split("[")[0].strip())
        node = NODE.match(line)
        if node:
            names.add(node.group(1).split("[")[0])
    return names


def titles(path):
    return set(TITLE.findall(path.read_text(errors="replace")))


def exists(spelling, name, root, cache):
    for path in resolve(spelling, root):
        if path not in cache:
            cache[path] = collected(path) if path.suffix == ".py" else titles(path)
        if name.split("[")[0] in cache[path] or name in cache[path]:
            return True
    return False


def same_file(spelling, other):
    want, got = Path(spelling).parts, Path(other).parts
    tail = min(len(want), len(got))
    return want[-tail:] == got[-tail:]


def verdicts(line, mention, foreign=()):
    seen = set()
    lead = LEADS.match(line)
    addressed = [m.end(2) for m in CASE.finditer(line)]
    for subject in mention.finditer(line):
        if any(a <= subject.start() < b for a, b in foreign):
            continue
        follows = FOLLOWS.match(line, subject.end())
        if follows:
            seen.add(follows.group(1))
        elif (lead and lead.end() <= subject.start()
                and not any(end <= subject.start() for end in addressed)):
            seen.add(lead.group(1))
    return seen & RED, seen & GREEN


def scan(logs, spelling, name):
    mention = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])(\[[^\]\s]*\])?")
    red = green = None
    for log in logs:
        for line in log.read_text(errors="replace").splitlines():
            if not mention.search(line):
                continue
            foreign = [(m.start(2), m.end(2)) for m in CASE.finditer(line)
                       if not same_file(spelling, m.group(1))]
            is_red, is_green = verdicts(line, mention, foreign)
            if red is None and is_red:
                red = log.name
            if green is None and is_green:
                green = log.name
        if red and green:
            break
    return red, green


def render(rows):
    head = ("#", "CASE", "EXISTS", "RED", "GREEN", "STATUS")
    width = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    for row in [head] + rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, width)).rstrip())


def main():
    ap = argparse.ArgumentParser(description="Is this ticket's handover answerable?")
    ap.add_argument("ticket")
    ap.add_argument("--reports", default=None,
                    help="where the committed logs are (default: docs/reports/<id>/)")
    args = ap.parse_args()

    ticket = Path(args.ticket).resolve()
    if not ticket.is_file():
        print(f"no such ticket: {args.ticket}", file=sys.stderr)
        return 2
    text = ticket.read_text(errors="replace")

    root = ticket.parents[2] if ticket.parent.name == "tickets" else ticket.parent
    ident = re.search(r"^id:\s*(\S+)", text[:2000], re.M)
    ident = ident.group(1) if ident else ticket.stem
    reports = Path(args.reports).resolve() if args.reports else root / "docs" / "reports" / ident
    logs = sorted(reports.rglob("*.log")) if reports.is_dir() else []

    items = criteria(text)
    if not items:
        print(f"{ident}: no numbered pass criteria in §1 — nothing to check", file=sys.stderr)
        return 2
    if not any(evidence(body) for _n, body in items):
        print(f"{ident}: not one criterion carries an `Evidence:` line, so the question cannot be "
              f"answered — write what evidences each behaviour", file=sys.stderr)
        return 2

    rows, counts, cache = [], dict(complete=0, missing=0, never_red=0, not_green=0, manual=0), {}
    for number, body in items:
        found = CASE.findall(evidence(body))
        if not found:
            counts["manual"] += 1
            rows.append((number, evidence(body)[:96] or "(no Evidence: line)",
                         "—", "—", "—", "manual"))
            continue
        for spelling, name in found:
            name = name.rstrip(".-")
            if not exists(spelling, name, root, cache):
                counts["missing"] += 1
                rows.append((number, f"{spelling}::{name}", "no", "—", "—", "missing"))
                continue
            red, green = scan(logs, spelling, name)
            state = []
            if not red:
                counts["never_red"] += 1
                state.append("never red")
            if not green:
                counts["not_green"] += 1
                state.append("not green")
            if not state:
                counts["complete"] += 1
                state = ["complete"]
            rows.append((number, f"{spelling}::{name}", "yes", red or "—", green or "—",
                         " · ".join(state)))

    print(f"{ident} · {len(logs)} log(s) under {reports}")
    render(rows)
    print(f"criteria: {len(items)} · complete: {counts['complete']} · missing: {counts['missing']}"
          f" · never red: {counts['never_red']} · not green: {counts['not_green']}"
          f" · manual: {counts['manual']}")

    named = (counts["complete"] + counts["missing"] + counts["never_red"]
             + counts["not_green"])
    if not named:
        nothing = ", ".join(str(number) for number, body in items)
        print(f"\nnot one criterion names a case this check could look for — criteria {nothing} "
              f"carry evidence that is prose. A handover is answerable only if something in it "
              f"can be looked for, and `manual: {counts['manual']}` over {len(logs)} committed "
              f"log(s) is the same state as a ticket with no `Evidence:` line at all.",
              file=sys.stderr)
        return 2

    if counts["missing"] or counts["never_red"] or counts["not_green"]:
        print("\nthis handover cannot be answered from what is committed. A case QA cannot find, "
              "or one no log ever shows failing, is a finding about the handover before any "
              "criterion is re-run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
