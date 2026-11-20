#!/usr/bin/env python3
"""The document gate, answered as a difference: what does THIS branch add?

`pytest tests/ -m repository` is red on `main` for reasons older than any one ticket, so its exit
status is not a verdict about a branch. The verdict is the failing SET on this branch against the
same command on its merge-base — the case names AND the ids each failing case lists, because half
these cases enumerate the tickets they refuse and a ticket's own commit puts its own name into one
without the list of case names changing at all.

    ./tools/docgate.py                          answer for this branch
    ./tools/docgate.py --ticket SRV-FIX-479     ... and write both raw logs beside the report
    ./tools/docgate.py --base <ref>             ... against something other than the merge-base

    exit 0   this branch adds nothing
    exit 1   it adds something; every addition is named on stdout
    exit 2   the question could not be answered — and a gate that could not be answered is
             never reported green, so nothing is written that could be quoted as a verdict
"""

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GATE = "python3 -m pytest tests/ -m repository -v -p no:cacheprovider"

TICKET_ID = re.compile(r"\b(?:SRV|WEB|FAC)-(?:FIX|FEAT|EPIC)-\d+\b")
SUMMARY = re.compile(r"^=+ .*\b\d+ (?:failed|passed|error|errors)\b.*=+$", re.M)
FAILED_SUMMARY = re.compile(r"^FAILED (\S+)(.*)$", re.M)
FAILED_VERBOSE = re.compile(r"^(\S+::\S+) FAILED\b", re.M)
FAILURES_HEAD = re.compile(r"^=+ FAILURES =+$")
SECTION = re.compile(r"^=+ \S.*=+$")
BLOCK = re.compile(r"^_+ (\S.*?) _+$")
INTERRUPTED = re.compile(r"^!+ Interrupted:.*!+$", re.M)

MANIFEST_REL = ".factory/factory.json"
TEMPLATE_NAME = "_TEMPLATE.md"
SECTION_TWO = re.compile(r"^## 2\b.*$", re.M)
NEXT_SECTION = re.compile(r"^## ", re.M)
FIRST_READ = re.compile(r"FIRST read:(.*?`\.)", re.S)
BACKTICKED = re.compile(r"`([^`]+)`")
ASSERTION = re.compile(r"^E\s{0,3}(.*)$", re.M)


def node(raw):
    """`server/tests/test_catalog.py::x` and `tests/test_catalog.py::x` are one case.

    The two committed captures this rule was derived from disagree, because the two runs had
    different working directories. Compared unnormalised, every case in such a pair is both new
    and gone at once.
    """
    return re.sub(r"^.*?tests/", "tests/", raw, count=1)


def failure_blocks(text):
    """The `FAILURES` section, split into one body per case by its `____ name ____` header."""
    lines = text.split("\n")
    start = next((i + 1 for i, ln in enumerate(lines) if FAILURES_HEAD.match(ln)), None)
    if start is None:
        return {}
    end = next((j for j in range(start, len(lines)) if SECTION.match(lines[j])), len(lines))

    blocks, name = {}, None
    for ln in lines[start:end]:
        m = BLOCK.match(ln)
        if m:
            name = m.group(1)
            blocks[name] = []
        elif name:
            blocks[name].append(ln)
    return {k: "\n".join(v) for k, v in blocks.items()}


def unfinished(text):
    """Why this output is not a verdict, or `None` if it is one.

    Two shapes, and the second is the one a summary line hides: a run whose collection collapses
    prints `185 deselected, 6 warnings, 24 errors in 5.14s` and no `FAILED` line at all, so a
    guard that only looks for the summary reads it as an EMPTY failing set.
    """
    if INTERRUPTED.search(text):
        return "its collection was interrupted before any case ran"
    if not SUMMARY.search(text):
        return "no pytest summary line in its output"
    return None


def parse(text):
    """The failing set: node id → the ticket ids that case lists. `None` if the run never finished."""
    if unfinished(text):
        return None

    said = {}
    for raw, tail in FAILED_SUMMARY.findall(text):
        said[node(raw)] = said.get(node(raw), "") + tail
    for raw in FAILED_VERBOSE.findall(text):
        said.setdefault(node(raw), "")

    blocks = failure_blocks(text)
    return {n: frozenset(TICKET_ID.findall(block_for(blocks, n) + tail))
            for n, tail in said.items()}


def block_for(blocks, case):
    """A case's body in the `FAILURES` section. Parametrised ids carry a suffix the header does
    not, so an exact miss falls back to the header the node id starts with."""
    name = case.split("::", 1)[-1]
    body = blocks.get(name)
    if body is None:
        body = next((b for h, b in blocks.items() if name.startswith(h)), "")
    return body


def delta(base, head):
    """What `head` has that `base` does not. A case that went green, or an id that disappeared,
    is not something this branch added and is not reported."""
    added = []
    for n in sorted(head):
        if n not in base:
            added.append(("NEW CASE", n, ""))
        else:
            added += [("NEW ID", n, i) for i in sorted(head[n] - base[n])]
    return added


def section_two(text):
    """§2 of a ticket or of the template — the heading to the next `## `, or `""`.

    The same shape `test_catalog.py::_section_three` uses on §3, for the same reason: the section
    is located by its own heading rather than by a line number somebody wrote down.
    """
    m = SECTION_TWO.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = NEXT_SECTION.search(rest)
    return rest[:nxt.start()] if nxt else rest


def process_paths(root):
    """The files a change to which changes how every future round is run — derived, never typed.

    Six sources, and each one is a category with a reason rather than a name on a list:

      `.claude/`                  the skills, scripts and settings a session is actually run from
      `.codex/`                   Codex integration metadata and adapters
      every `AGENTS.md`           the instructions Codex discovers at repository boundaries
      links from `AGENTS.md`      provider-neutral process documents the adapters point at
      the FIRST read sentence     the documents `_TEMPLATE.md` §2 sends every session to first
      `_TEMPLATE.md`              §2 itself, the standing process a ticket follows verbatim
      `.factory/factory.json`     the manifest the two lines above are located through

    A checkout with none of them yields the empty set and the caller says `process change: none`;
    a template that has lost its `FIRST read:` sentence yields no named documents, which
    `test_a_process_change_is_named_by_the_gate` refuses on this repository.
    """
    found = set()
    claude = root / ".claude"
    if claude.is_dir():
        found |= {str(p.relative_to(root)) for p in claude.rglob("*") if p.is_file()}

    codex = root / ".codex"
    if codex.is_dir():
        found |= {str(p.relative_to(root)) for p in codex.rglob("*") if p.is_file()}

    agents_files = [p for p in root.rglob("AGENTS.md")
                    if ".factory/worktrees" not in p.relative_to(root).as_posix()]
    for adapter in agents_files:
        found.add(str(adapter.relative_to(root)))
        for raw in re.findall(r"\[[^]]+\]\(([^)]+\.md)\)", adapter.read_text()):
            target = (adapter.parent / raw).resolve()
            try:
                found.add(str(target.relative_to(root.resolve())))
            except ValueError:
                pass

    manifest_path = root / MANIFEST_REL
    if not manifest_path.is_file():
        return found
    found.add(MANIFEST_REL)

    try:
        tickets = json.loads(manifest_path.read_text())["docs"]["tickets"]
    except (ValueError, KeyError, TypeError):
        return found

    template = root / tickets / TEMPLATE_NAME
    if not template.is_file():
        return found
    found.add(str(template.relative_to(root)))

    sentence = FIRST_READ.search(section_two(template.read_text()))
    if sentence:
        found |= {s for s in BACKTICKED.findall(sentence.group(1)) if (root / s).is_file()}
    return found


def assertion_lines(blocks, case):
    """A failing case's own assertion lines, so a `NEW CASE` says WHY and not only WHICH.

    Read out of the head run's `FAILURES` section — pytest's own `E   …` prefix — so the text is
    the case's message and nothing this script composed. Without it the verdict names a node id
    and the operator opens a log to find out what it means.
    """
    return [l.rstrip() for l in ASSERTION.findall(block_for(blocks, case)) if l.strip()]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)


def resolve_base(root, ref):
    if ref:
        r = git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
        return (r.stdout.strip(), f"--base {ref}") if r.returncode == 0 else (None, None)

    for candidate in ("origin/main", "main"):
        if git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}").returncode:
            continue
        r = git(root, "merge-base", "HEAD", candidate)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), f"merge-base HEAD {candidate}"
    return None, None


def run_gate(command, cwd):
    r = subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True, text=True)
    return r.stdout + r.stderr


def snapshot_documents(source, target):
    """Copy declared documents into scratch without following links back into the live tree."""
    if source.is_symlink():
        raise ValueError(f"declared document is a symlink: {source}")
    if not source.exists():
        return
    paths = [source] if source.is_file() else [source, *source.rglob("*")]
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"declared document is a symlink: {path}")
        dest = target if path == source else target / path.relative_to(source)
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if dest.read_bytes() != path.read_bytes():
                    raise ValueError(f"declared document snapshots disagree: {path}")
                continue
            shutil.copy2(path, dest)
            dest.chmod(0o444)


def snapshot_enclosing(enclosing, outer, project):
    """Give the historical checkout today's declared parent documents and Git readbacks."""
    cloned = git(enclosing, "clone", "--no-hardlinks", "--no-checkout", str(enclosing), str(outer))
    if cloned.returncode:
        raise ValueError(f"could not snapshot enclosing Git history: {cloned.stderr.strip()}")
    manifest_path = enclosing / MANIFEST_REL
    manifest = json.loads(manifest_path.read_text())
    repos = manifest.get("repos", {"": manifest})
    if not isinstance(repos, dict):
        raise ValueError("enclosing manifest has no usable repos map")
    snapshot_documents(manifest_path, outer / MANIFEST_REL)
    defaults = {"epics": "docs/epics", "tickets": "docs/tickets", "reports": "docs/reports"}
    for spec in repos.values():
        if not isinstance(spec, dict):
            raise ValueError("enclosing manifest has an invalid repo declaration")
        relative = Path(spec.get("path", "."))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"enclosing manifest has an unsafe repo path: {relative}")
        where = (enclosing / relative).resolve()
        target = outer / relative
        if where == project or project in where.parents:
            continue
        if not where.is_relative_to(enclosing.resolve()):
            raise ValueError(f"declared repo leaves the enclosing checkout: {relative}")
        if where != enclosing and where.is_dir():
            git_root = git(where, "rev-parse", "--show-toplevel")
            if not git_root.returncode and Path(git_root.stdout.strip()).resolve() == where:
                cloned = git(where, "clone", "--no-hardlinks", "--no-checkout",
                             str(where), str(target))
                if cloned.returncode:
                    raise ValueError(f"could not snapshot declared Git history: {cloned.stderr.strip()}")
        docs = spec.get("docs", {})
        if not isinstance(docs, dict):
            raise ValueError(f"declared repo has an invalid docs map: {relative}")
        for name, default in defaults.items():
            docs.setdefault(name, default)
        for name in set(docs.values()):
            doc_path = Path(name)
            if doc_path.is_absolute() or ".." in doc_path.parts or doc_path == Path("."):
                raise ValueError(f"declared document path is unsafe: {name}")
            source = where / doc_path
            if not source.resolve().is_relative_to(where):
                raise ValueError(f"declared documents leave their repo: {source}")
            snapshot_documents(source, target / doc_path)
        snapshot_documents(where / MANIFEST_REL, target / MANIFEST_REL)
    for path in (enclosing / "docs").glob("*.md"):
        snapshot_documents(path, outer / path.relative_to(enclosing))
    for relative in ("CLAUDE.md", "AGENTS.md", ".claude/skills", ".factory/prompts"):
        source = enclosing / relative
        if relative.endswith("/prompts") or relative.endswith("/skills"):
            for path in source.rglob("*.md") if source.is_dir() else ():
                snapshot_documents(path, outer / path.relative_to(enclosing))
        else:
            snapshot_documents(source, outer / relative)


def gate_at_base(root, sha, command):
    """Run the historical tree with the checkout's enclosing Git repository context."""
    with tempfile.TemporaryDirectory(prefix="docgate-") as scratch:
        outer = Path(scratch) / "outer"

        common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common.returncode:
            return None, f"could not locate Git history: {common.stderr.strip()}"
        own_common = Path(common.stdout.strip()).resolve()
        enclosing = None
        for at in own_common.parents:
            if not (at / MANIFEST_REL).is_file():
                continue
            candidate = git(at, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if not candidate.returncode and Path(candidate.stdout.strip()).resolve() == own_common:
                continue
            enclosing = at
            break
        if enclosing:
            try:
                snapshot_enclosing(enclosing, outer, root)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                return None, f"could not preserve enclosing documents: {error}"
        relative = (root.relative_to(enclosing) if enclosing and root.is_relative_to(enclosing)
                    else Path("linked") / root.name if enclosing else Path(root.name))
        work = outer / relative
        work.parent.mkdir(parents=True, exist_ok=True)
        git_dir = outer / ".git" / "modules" / "docgate-project"
        git_dir.parent.mkdir(parents=True, exist_ok=True)

        cloned = git(root, "clone", "--no-hardlinks", "--no-checkout",
                     "--separate-git-dir", str(git_dir), str(root), str(work))
        if cloned.returncode:
            return None, f"could not clone the merge-base history: {cloned.stderr.strip()}"
        checked = git(work, "checkout", "--detach", sha)
        if checked.returncode:
            return None, f"could not check out the merge-base: {checked.stderr.strip()}"
        source_main = git(root, "rev-parse", "--verify", "--quiet", "refs/heads/main")
        if not source_main.returncode:
            restored = git(work, "update-ref", "refs/heads/main", source_main.stdout.strip())
            if restored.returncode:
                return None, f"could not restore the source's main: {restored.stderr.strip()}"
        return run_gate(command, work / "server"), str(work / "server")


def cannot(why):
    print(f"docgate: cannot answer — {why}", file=sys.stderr)
    return 2


def main():
    ap = argparse.ArgumentParser(description="what this branch adds to the document gate")
    ap.add_argument("--ticket", help="write both raw logs under docs/reports/<ID>/")
    ap.add_argument("--base", help="the ref to compare against (default: the merge-base with main)")
    ap.add_argument("--gate-cmd", default=GATE, help=f"the gate each side runs (default: {GATE})")
    args = ap.parse_args()

    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode:
        return cannot("this is not a git checkout")
    root = Path(r.stdout.strip())

    base_sha, how = resolve_base(root, args.base)
    if not base_sha:
        return cannot("this branch has no merge-base — no origin/main, no main, or an unrelated "
                      "history. Nothing can be compared, so nothing is reported.")

    head_sha = git(root, "rev-parse", "HEAD").stdout.strip()
    head_dir = root / "server"
    head_out = run_gate(args.gate_cmd, head_dir)
    base_out, base_dir = gate_at_base(root, base_sha, args.gate_cmd)
    if base_out is None:
        return cannot(base_dir)

    for side, out in (("head", head_out), ("merge-base", base_out)):
        why = unfinished(out)
        if why:
            return cannot(f"the {side} gate did not finish — {why}. A gate that did not finish "
                          f"is not a gate that passed.")

    head_set, base_set = parse(head_out), parse(base_out)

    added = delta(base_set, head_set)

    changed = git(root, "diff", "--name-only", base_sha, head_sha).stdout.splitlines()
    touched = sorted(set(changed) & process_paths(root))

    print(f"docgate: base {base_sha[:8]} ({how}) · head {head_sha[:8]}")
    print(f"docgate: gate ran from {head_dir} and {base_dir}")
    print(f"process change: {len(touched)} files" if touched else "process change: none")
    for rel in touched:
        print(f"    {rel}")
    print(f"base: {len(base_set)} red · head: {len(head_set)} red · added: {len(added)}")
    blocks = failure_blocks(head_out)
    for kind, n, extra in added:
        print(f"{kind:8s}  {n}{'  ' + extra if extra else ''}")
        if kind == "NEW CASE":
            for line in assertion_lines(blocks, n):
                print(f"          {line}")
    if not added:
        print("this branch adds nothing to the document gate")

    if args.ticket:
        day = datetime.date.today().isoformat()
        out = root / "docs" / "reports" / args.ticket
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.ticket}-document-gate-{day}.log").write_text(head_out)
        (out / f"{args.ticket}-document-gate-merge-base-{day}.log").write_text(base_out)
        print(f"docgate: both logs written under {out}")

    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main())
