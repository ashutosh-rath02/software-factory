"""`docs/operations/ESCAPES.md` is the eval set, and a process change is gated on the whole of it.

`FAC-EPIC-375` §1, from the AI-native SDLC playbook: *"Each production incident becomes a permanent
eval"* and *"Gate configuration changes on results."* This repository has had the incident ledger
since 2026-07-26 and 117 of its 221 rows name the case that closed them. Nothing read that column
after the row was written, so a case an escape landed could be renamed, moved or deleted and the
row went on claiming it.

Three rows are what this file answers, and they are one shape — a rule that lives in prose and a
case nobody runs when the prose changes:

    2026-08-27  five document rules were red on `main` for up to twenty days and every gate that
                ran them read past it
    2026-09-08  the rule was written in three places and enforced in none (`WEB-FIX-370`)
    2026-08-09  a gate answers from files no commit contains (`SRV-FIX-021`)

`SRV-FEAT-498`.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_catalog import MANIFEST_REL, _repo_root, manifests_of_this_monorepo

pytestmark = pytest.mark.repository

SERVER = Path(__file__).resolve().parents[1]
CHECKOUT = SERVER.parent
DOCGATE = SERVER / "tools" / "docgate.py"

# §1's own count, taken with §1's own command:
#
#     grep "^| 20" docs/ESCAPES.md | grep -c "test_"      -> 117
#
# The ledger only ever grows, so this floor can only become easier to clear; it is here to say
# that the parse below still finds the rows, not to ration them. It is NOT the swept set — that
# is the `Change landed` column, counted separately and printed beside this one.
ROWS_NAMING_A_CASE = 117

# The one exclusion §1 states: a row whose `Change landed` cell names a ticket that is still open.
# The case it names has been filed and has not landed; the row is a debt with an owner, and the
# assertion message prints that owner per row so the debt is visible on every run.
STILL_OPEN = ("todo", "in-progress")

# Two cases in this file run this file again in a copy of this checkout: the scratch run below
# invokes `docgate.py`, which runs a pytest command in a checkout of this repository, and the
# placement cases run the module from three trees they copied. Either way a child wide enough to
# re-collect this file re-enters the case that spawned it and does not stop. This is the mechanism
# behind the scope: the child is told it is a nested run and every case that would spawn another
# refuses to be one twice.
SCRATCH = "FACTORY_LEDGER_EVAL_SCRATCH"

IDENTIFIER = re.compile(r"\btest_[a-z0-9_]+\b")
TICKET_ID = re.compile(r"\b(?:[A-Z][A-Z0-9]*-)?(?:FIX|FEAT|EPIC)-\d+\b")


def manifests_here(root):
    """Every manifest in this monorepo, and a refusal rather than a traceback when there is none.

    This file kept a copy of `_repo_root`'s rule and then read the ONE manifest at the root it
    resolved. The rule is right — the parent wins when it keeps a manifest — and the single read
    is what made the eval red on the developer's checkout and green in every lane, where the
    parent is `lane/` and the rule never fires (`SRV-FIX-520`). The enumeration behind the rule is
    the half that was not copied, so nothing is copied now.
    """
    assert (root / MANIFEST_REL).is_file(), (
        f"no manifest found at or above {root}: neither it nor its parent keeps "
        f"`{MANIFEST_REL}`, so there is no ledger for this case to read and the whole sweep would "
        "be silently empty")
    return manifests_of_this_monorepo(root)


def ledger_paths(manifests):
    """Every ledger any manifest in this monorepo declares, never a typed path (`docs/product/SPEC.md` §9.1).

    A manifest that declares none contributes nothing and is not an error — the monorepo holding
    this repository declares tickets and reports and no ledger. NO manifest declaring one is.
    """
    found = []
    for base, manifest in manifests:
        declared = manifest.get("docs", {}).get("escapes")
        if declared:
            found.append(base / declared)
    assert found, (
        "no manifest in this monorepo declares `docs.escapes`, so there is no ledger for this "
        "case to read and the whole sweep would be silently empty — examined: "
        + ", ".join(str(base / MANIFEST_REL) for base, _ in manifests))
    return found


def cells(line):
    """One markdown row split on `|`, ignoring pipes inside backticks.

    Ledger prose is full of `grep -c "x" \\| wc -l`, and splitting on those turns the tail
    columns of a row into different columns than the header has.
    """
    out, cur, tick = [], "", False
    for ch in line.strip().strip("|"):
        if ch == "`":
            tick = not tick
            cur += ch
        elif ch == "|" and not tick:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [c.strip() for c in out]


def first_table(text):
    """`(header cells, [row cells], [rows outside it])` for the FIRST markdown table.

    The header is the first `|` line carrying a `Date` cell; the table is the run of `|` lines
    after it. **Outside** is any other line that opens like a ledger row, and it is not a nicety:
    `WEB-FIX-002` appended two rows at the end of the file, below the prose that follows the
    table, where no reader of the ledger and no table parser sees them. A row nobody's table
    contains is a row this eval cannot read, so it is refused rather than skipped.
    """
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("|") and "Date" in cells(l)), None)
    if start is None:
        return [], [], [l for l in lines if l.startswith("| 20")]
    header = cells(lines[start])
    rows, end = [], start
    for i in range(start + 1, len(lines)):
        if not lines[i].startswith("|"):
            break
        end = i
        row = cells(lines[i])
        if all(re.fullmatch(r":?-{2,}:?", c) for c in row):
            continue
        rows.append(row)
    outside = [l for i, l in enumerate(lines)
               if l.startswith("| 20") and not (start < i <= end)]
    return header, rows, outside


def identifiers(cell):
    """Pre-decided, verbatim: `re.findall(r"\\btest_[a-z0-9_]+\\b", cell)`."""
    return sorted(set(IDENTIFIER.findall(cell)))


def collected_by_pytest(server=SERVER):
    """Every identifier `pytest tests/ --collect-only -q` reports, checked against its own count.

    Both shapes pytest has printed are read: `<Module test_x.py>` / `<Function test_y>` (pytest 9)
    and `tests/test_x.py::test_y` (earlier). A ledger row that names `test_isolation.py` names a
    file pytest collects, so modules count as reachable exactly as functions do.

    The entry count is compared with the `collected N items` line pytest itself printed. Without
    that, a release that changes the collection format turns every identifier in the ledger into
    an offender, and the case reports 200 defects in the documents instead of one in itself.
    """
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q",
                        "-p", "no:cacheprovider"],
                       cwd=str(server), capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    said = re.search(r"^collected (\d+) items?", out, re.M)
    assert said, f"pytest printed no `collected N items` line, so nothing here is collection:\n{out[-3000:]}"

    entries = re.findall(r"^\s*<Function (test_[a-z0-9_]+)", out, re.M) \
        or re.findall(r"::(test_[a-z0-9_]+)", out)
    assert len(entries) == int(said.group(1)), (
        f"this file parsed {len(entries)} cases out of `pytest tests/ --collect-only -q` and "
        f"pytest said it collected {said.group(1)}. The parse, not the ledger, is what is wrong: "
        f"fix it before believing any offender below it.\n{out[:3000]}")
    return set(IDENTIFIER.findall(out))


def vitest_cases(bases):
    """Every vitest case name under each manifest's own client, `snake_case`-normalised.

    `it('a lane row goes somewhere')` is how this repository names them, and a ledger row spells
    the same case `test_a_lane_row_goes_somewhere`. Swept per manifest base rather than under the
    root: on the developer's checkout the root is the monorepo and the client is one level down,
    where a sweep of the root alone finds nothing and every vitest case the ledger names becomes
    an offender.
    """
    found = set()
    for root in bases:
        for base in (root / "client" / "web" / "tests", root / "client" / "web" / "src"):
            if not base.is_dir():
                continue
            for f in sorted(base.rglob("*.test.ts")) + sorted(base.rglob("*.test.tsx")):
                for name in re.findall(r"""\b(?:it|test)\(\s*['"`]([^'"`]*)['"`]""", f.read_text()):
                    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                    if slug:
                        found.add(slug)
                        found.add(f"test_{slug}")
    return found


def ticket_status(manifests):
    """`{ticket id: status}` over every ticket directory a manifest in this monorepo declares."""
    found = {}
    for base, manifest in manifests:
        declared = manifest.get("docs", {}).get("tickets")
        if not declared:
            continue
        for f in sorted((base / declared).glob("*.md")):
            if f.name.startswith("_") or f.name == "README.md":
                continue
            head = f.read_text()[:1200]
            tid = re.search(r"^id:\s*(\S+)", head, re.M)
            status = re.search(r"^status:\s*(\S*)", head, re.M)
            if tid:
                found[tid.group(1)] = (status.group(1) if status else "").strip()
    return found


def evaluate(path, header, rows, reachable, statuses):
    """`(naming, swept, excluded, offenders)` for ONE ledger — the whole judgement, as data.

    Pure: everything it reads is an argument. The negatives below drive it on ledgers this
    repository does not have, which is the only way to watch a check that reads real documents
    fail on purpose. `path` is the ledger these rows came out of and every line below names it:
    a monorepo declares more than one, and a row with no file beside it is a row nobody can find.
    """
    column = header.index("Change landed") if "Change landed" in header else None
    assert column is not None, (
        f"{path}'s first table has no `Change landed` column — its header is {header}. There "
        "is nothing to read a landed case out of, and this sweep would be silently empty.")

    naming, swept, excluded, offenders, unread = [], [], [], [], []
    for row in rows:
        date = row[0] if row else ""
        if any(IDENTIFIER.search(c) for c in row):
            naming.append(date)
        if len(row) <= column:
            unread.append(f"{path} {date}: {len(row)} cells, the header has {len(header)}")
            continue
        cell = row[column]
        named = identifiers(cell)
        if not named:
            continue
        open_tickets = [t for t in TICKET_ID.findall(cell) if statuses.get(t) in STILL_OPEN]
        if open_tickets:
            excluded.append(
                f"{path} {date}: filed, not yet landed — {', '.join(sorted(set(open_tickets)))}")
            continue
        swept.append(date)
        for ident in named:
            if ident not in reachable:
                offenders.append(
                    f"{path} {date}: `{ident}` is named by the ledger and does not exist")
    return naming, swept, excluded, offenders, unread


def summary(ledgers, naming, swept, excluded, offenders, unread):
    return (f"ledgers: {' · '.join(str(p) for p in ledgers)} · "
            f"rows naming a case: {len(naming)} · swept (Change landed): {len(swept)} · "
            f"excluded (filed, not yet landed): {len(excluded)} · missing: {len(offenders)} · "
            f"not read (fewer cells than the header, `SRV-FIX-503`): {len(unread)}")


def refuse(tables, reachable, statuses):
    """The assertion itself over EVERY declared ledger, and the summary line it decided on.

    `tables` is `[(ledger path, header, rows, rows outside the table)]`, one per ledger any
    manifest in this monorepo declares. The counts are the union: a monorepo that declares two
    ledgers has one sweep, and a row missing its case in either of them is one offender naming
    the file it is in.
    """
    naming, swept, excluded, offenders, unread, outside = [], [], [], [], [], []
    for path, header, rows, out in tables:
        n, s, e, o, u = evaluate(path, header, rows, reachable, statuses)
        naming += n
        swept += s
        excluded += e
        offenders += o
        unread += u
        outside += [f"{path}: {l}" for l in out]
    said = summary([p for p, _h, _r, _o in tables], naming, swept, excluded, offenders, unread)

    assert swept, (
        f"the sweep is empty — {said}.\nEvery row that names a landed case was excluded, so this "
        "case examined nothing and passed. A check that sweeps nothing has not run: it is the "
        "shape a rule takes when it has become documentation with an `assert` in front of it.\n"
        + "\n  ".join([""] + excluded))

    assert not outside, (
        f"{said}.\nThese rows open like a ledger row and are not in the ledger's table, so no "
        "reader of the ledger and no parse of it — this one included — contains them:\n  "
        + "\n  ".join(l[:120] for l in outside)
        + "\n\nMove them into the table. A branch that appends its row at the end of the file "
          "instead of the end of the table writes it where nothing looks.")

    assert len(naming) >= ROWS_NAMING_A_CASE, (
        f"{said}.\nThe ledger named a case in {len(naming)} rows and `grep \"^| 20\" | grep -c "
        f"\"test_\"` counted {ROWS_NAMING_A_CASE} when this case was written. The ledger only "
        "grows, so a smaller number is this file's parse of it breaking, not the ledger shrinking.")

    assert not offenders, (
        f"{said}.\nThese ledger rows name a case that no longer exists. Each one is an eval an "
        "escape paid for, and nothing runs it:\n  " + "\n  ".join(offenders)
        + "\n\nRestore the case under the name the row gives it, or file the ticket that owes it "
          "and name that ticket in the row — a row whose `Change landed` cell names a `todo` or "
          "`in-progress` ticket is the one exclusion, and it is printed on every run.\n"
        + "\n  ".join(["Excluded on this run:"] + excluded)
        + "\n  " + "\n  ".join(["Not read at all — no `Change landed` cell to read (`SRV-FIX-503`):"]
                               + unread))

    return said


def this_repository():
    """The three inputs, taken from this monorepo, once — through the catalog's own door."""
    manifests = manifests_here(_repo_root())
    tables = [(p, *first_table(p.read_text())) for p in ledger_paths(manifests)]
    reachable = collected_by_pytest() | vitest_cases([base for base, _ in manifests])
    return tables, reachable, ticket_status(manifests)


@pytest.mark.repository
def test_every_case_the_ledger_names_is_collected():
    """Criterion 1. The ledger is the eval set.

    Open `docs/operations/ESCAPES.md` and read the `Change landed` column: every `test_*` it names is a case
    an incident bought, and every one of them is still reachable — collected by pytest under
    `server/tests/`, or named by a vitest case under the client. A row whose cell names a ticket
    that is still `todo` or `in-progress` has filed its case and not landed it; that row is
    excluded and its ticket id is printed, and no row is excluded for anything else.
    """
    tables, reachable, statuses = this_repository()
    for path, _header, rows, _outside in tables:
        assert rows, f"{path} has no first markdown table at all"
    print("\n" + refuse(tables, reachable, statuses))


def test_an_empty_sweep_is_red():
    """Criterion 4. NEGATIVE (mandatory) — a check that sweeps nothing has not run.

    A ledger every row of which is excluded is the shape this rule decays into: each row names a
    `todo` ticket, every row is forgiven, and the case passes having examined nothing. It must be
    RED, and it must say `sweep is empty` rather than reporting a clean ledger.
    """
    header = ["Date", "Escape", "Escaped from", "Found by", "Root cause of the MISS",
              "Change landed"]
    # Above the floor, so the sweep is what decides the verdict and not the size of the fixture.
    n_rows = ROWS_NAMING_A_CASE + 3
    rows = [[f"2026-01-{n:03d}", "an escape", "SRV-FIX-1", "a person", "a miss",
             f"`test_nothing_{n}` — **Open: `SRV-FIX-9{n:03d}`**"] for n in range(n_rows)]
    todo = {f"SRV-FIX-9{n:03d}": "todo" for n in range(n_rows)}

    ledger = Path("/nowhere/docs/operations/ESCAPES.md")
    with pytest.raises(AssertionError, match="sweep is empty") as empty:
        refuse([(ledger, header, rows, [])], [], todo)
    assert f"swept (Change landed): 0" in str(empty.value), (
        "an empty sweep did not print the count it swept, so a reader cannot tell it apart from "
        f"a clean ledger: {empty.value}")

    # And the same ledger with the tickets closed is NOT an empty sweep — it is every row.
    # Without this the case above passes against a helper that raises on every input.
    with pytest.raises(AssertionError, match="no longer exists") as caught:
        refuse([(ledger, header, rows, [])], [],
               {f"SRV-FIX-9{n:03d}": "pass" for n in range(n_rows)})
    assert "sweep is empty" not in str(caught.value)
    assert str(ledger) in str(caught.value), (
        "an offender does not name the ledger it is in, and a monorepo declares more than one: "
        f"{caught.value}")

    # A row outside the table is refused too, and not counted as a clean ledger.
    with pytest.raises(AssertionError, match="not in the ledger's table"):
        refuse([(ledger, header, rows, ["| 2026-01-999 | a row appended below the prose |"])],
               [f"test_nothing_{n}" for n in range(n_rows)],
               {f"SRV-FIX-9{n:03d}": "pass" for n in range(n_rows)})


def clone_for_scratch(tmp_path, name="scratch"):
    """A clone of this checkout under `tmp_path`, never a worktree of it.

    `git worktree add` cannot register a worktree from inside a lane — the git common directory is
    bound read-only with only `worktrees/<lane>` writable, which is `SRV-FIX-481` — and
    `docgate.py` needs `git worktree add` to work for its own merge-base run. A clone owns its own
    git directory, so the script under test runs the mechanism it really uses.
    """
    work = tmp_path / name
    r = subprocess.run(["git", "clone", "--no-hardlinks", "--quiet", str(CHECKOUT), str(work)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode:
        pytest.skip(f"could not clone this checkout: {r.stderr.strip()}")
    for args in (("config", "user.email", "lane@factory.test"),
                 ("config", "user.name", "lane")):
        subprocess.run(["git", *args], cwd=str(work), capture_output=True, text=True)
    return work


def git_out(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"git {' '.join(args)} in {cwd}:\n{r.stdout}{r.stderr}"
    return r.stdout


def run_docgate(cwd, *args):
    return subprocess.run([sys.executable, str(cwd / "server" / "tools" / "docgate.py"), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=1800,
                          env={**os.environ, SCRATCH: "1"})


def defined_under_tests(ident, tests=SERVER / "tests"):
    """The file under `server/tests` that defines `ident` as a function, or `None`.

    `reachable` counts a MODULE exactly as it counts a case, because a ledger row naming
    `test_isolation.py` names something pytest collects. The negative below has to RENAME a
    definition, so *reachable* is not the property it needs. `identifiers()` sorts, and one row
    whose prose mentions `test_catalog.py` put a module name first — the gate said
    `` `test_catalog` is reachable but no file under server/tests defines it `` (`SRV-FIX-520`).
    """
    return next((p for p in sorted(tests.glob("*.py"))
                 if re.search(rf"^def {ident}\b", p.read_text(), re.M)), None)


def a_row_that_names_a_living_case(tables, reachable, statuses):
    """One ledger row, picked BY READING THE LEDGER: its date, its cell, one identifier in it.

    Never a literal. A row named here would go stale the first time somebody edited it, and the
    case would then be about a row this file remembers rather than about the ledger.
    """
    for _path, header, rows, _outside in tables:
        column = header.index("Change landed")
        for row in rows:
            if len(row) <= column:
                continue
            named = [i for i in identifiers(row[column])
                     if i in reachable and defined_under_tests(i)]
            open_tickets = [t for t in TICKET_ID.findall(row[column])
                            if statuses.get(t) in STILL_OPEN]
            if named and not open_tickets:
                return row[0], named[0]
    pytest.fail("no ledger row names a case that exists, so this negative has nothing to delete")


@pytest.mark.repository
def test_a_process_change_that_loses_a_named_case_is_refused(tmp_path):
    """Criterion 3. NEGATIVE (mandatory) — the gate refuses a process change that drops an eval.

    In a scratch clone: delete the case one ledger row names — the row chosen by reading the
    ledger — and edit `.claude/skills/run-ticket/SKILL.md`, which is how every future round is
    run. `docgate.py` exits 1 and its output names that row's date, the identifier that went
    missing, and the skill file. Put the case back and the same worktree exits 0.
    """
    if os.environ.get(SCRATCH):
        pytest.skip(f"{SCRATCH} is set: this run IS the scratch worktree of an outer run of this "
                    "case, and driving the gate again from inside it does not terminate")

    tables, reachable, statuses = this_repository()
    date, ident = a_row_that_names_a_living_case(tables, reachable, statuses)
    assert defined_under_tests(ident), (
        f"`{ident}` is reachable but no file under server/tests defines it — `reachable` counts a "
        "module as it counts a case, and this negative has to rename a DEFINITION")

    work = clone_for_scratch(tmp_path)
    git_out(work, "checkout", "--quiet", "-b", "ticket/scratch")
    # The base is this branch's tip, where the eval EXISTS. Compared against `main` the case is
    # absent on both sides of the question and the script correctly says it cannot answer — the
    # comparison would be about a file, not about a deletion.
    base = git_out(work, "rev-parse", "HEAD").strip()

    skill = work / ".claude" / "skills" / "run-ticket" / "SKILL.md"
    assert skill.is_file(), f"{skill} is not in the clone, so this case cannot change the process"
    skill.write_text(skill.read_text() + "\n<!-- SRV-FEAT-498 scratch edit -->\n")

    source = defined_under_tests(ident, work / "server" / "tests")
    assert source, f"`{ident}` is reachable but no file under server/tests defines it"
    before = source.read_text()
    source.write_text(re.sub(rf"^def {ident}\b", f"def {ident}_renamed_by_srv_feat_498",
                             before, count=1, flags=re.M))
    assert source.read_text() != before, f"the deletion of `{ident}` changed nothing"

    git_out(work, "add", "-A")
    git_out(work, "commit", "--quiet", "-m", "scratch: a process change that loses an eval")

    # ONE node id, not the file: `docgate.py` runs this command on both sides, and a command that
    # re-collects this module re-enters this case inside its own scratch worktree.
    gate = ["--base", base, "--gate-cmd",
            f"{sys.executable} -m pytest tests/test_escapes_are_evals.py::"
            f"test_every_case_the_ledger_names_is_collected -v -p no:cacheprovider"]
    red = run_docgate(work, *gate)
    said = red.stdout + red.stderr
    assert red.returncode == 1, f"a process change that lost an eval did not exit 1:\n{said}"
    assert "process change:" in said and "run-ticket/SKILL.md" in said, \
        f"the gate did not name the process file this branch changed:\n{said}"
    assert date in said, f"the gate did not name the ledger row's date {date}:\n{said}"
    assert ident in said, f"the gate did not name the identifier that went missing:\n{said}"

    source.write_text(before)
    git_out(work, "add", "-A")
    git_out(work, "commit", "--quiet", "-m", "scratch: put the eval back")
    green = run_docgate(work, *gate)
    assert green.returncode == 0, (
        "restoring the case did not make the same worktree green — so the refusal above was "
        f"about something other than the missing eval:\n{green.stdout}{green.stderr}")
    assert "run-ticket/SKILL.md" in green.stdout, (
        "the process file is still changed on this branch and the gate stopped naming it:\n"
        + green.stdout)
    shutil.rmtree(work, ignore_errors=True)


# ── SRV-FIX-520 · where the checkout sits decides nothing ──────────────────────────────────────
#
# The eval resolved a root and then read ONE manifest at it. On the developer's checkout that root
# is the monorepo above this repository, whose manifest declares no ledger, so the eval was red
# there and green in every lane — the placement no gate of `SRV-FEAT-498` ran in. The cases below
# run the real module from three placements and require one answer out of all three.

MONOREPO_WITHOUT_A_LEDGER = {
    "factory": "0.1",
    "workspaces": {"product": {"path": "."}},
    "docs": {"tickets": "docs/tickets", "reports": "docs/reports"},
}

MONOREPO_WITH_A_LEDGER = {
    "factory": "0.1",
    "workspaces": {"product": {"path": "."}},
    "docs": {"tickets": "docs/tickets", "reports": "docs/reports",
             "escapes": "docs/operations/ESCAPES.md"},
}

SUMMARY = re.compile(r"^ledgers: .*$", re.M)


def tracked_by_git(source):
    """Every path `source`'s git index lists, and nothing else decides (`SRV-FIX-525`)."""
    out = subprocess.run(["git", "-C", str(source), "ls-files", "-z"],
                         capture_output=True, text=True, timeout=300, check=True).stdout
    return [Path(p) for p in out.split("\0") if p]


def copy_of_this_checkout(dest, source=CHECKOUT, without=()):
    """`source` at `dest`: what git tracks, with the working tree's own bytes, and nothing else.

    `without` is what the CALLER is deliberately making this placement lack, named at the call
    site with its reason. It is not an exclusion set for weight: the one caller passes
    `MANIFEST_REL`, for the placement whose whole subject is *no manifest anywhere*.
    """
    names = [rel for rel in tracked_by_git(source) if rel.as_posix() not in without]
    assert names, (
        f"`git ls-files` in {source} listed nothing, so this placement would be a copy of an "
        "empty tree and every case built on it would pass having read no repository at all")

    for rel in names:
        s, d = source / rel, dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_symlink():
            os.symlink(os.readlink(s), d)
        elif s.exists():
            shutil.copy2(s, d)
    return dest


def placement(tmp_path, rel, parent_manifest=None, source=CHECKOUT, manifest=True):
    """A copy of this checkout at `tmp_path/rel`, under a parent that may keep a manifest."""
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    copy_of_this_checkout(dest, source, without=() if manifest else (MANIFEST_REL,))
    if parent_manifest is not None:
        m = dest.parent / MANIFEST_REL
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps(parent_manifest, indent=2))
    return dest


def run_eval(root, *node_ids, scratch=True):
    """The real module, run by the real pytest, from the placement under test.

    `scratch` sets the re-entry guard this file already keeps: a run of the whole module inside a
    copy would otherwise reach these very cases and copy again, without end. The guard is the one
    `SRV-FEAT-498` built for the same reason, and it is what makes the nested run terminate.
    """
    env = {**os.environ}
    env.pop(SCRATCH, None)
    if scratch:
        env[SCRATCH] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest",
         *(node_ids or ("tests/test_escapes_are_evals.py",)),
         "-m", "repository", "-p", "no:cacheprovider", "-s", "-v", "-rA"],
        cwd=str(root / "server"), capture_output=True, text=True, timeout=1800, env=env)


def summary_line(out):
    m = SUMMARY.search(out)
    assert m, ("the run printed no `ledgers:` summary line, so nothing in it says which ledger it "
               f"read:\n{out[-4000:]}")
    return m.group(0)


def ledgers_of(said):
    m = re.search(r"^ledgers: (.*?) · rows naming a case:", said)
    assert m, f"the summary line names no ledger: {said}"
    return [Path(p) for p in m.group(1).split(" · ")]


def counts_of(said):
    got = []
    for key in ("rows naming a case", "swept (Change landed)",
                "excluded (filed, not yet landed)", "missing"):
        m = re.search(re.escape(key) + r": (\d+)", said)
        assert m, f"`{key}` is not in the summary line: {said}"
        got.append((key, int(m.group(1))))
    return tuple(got)


def monorepo_ledger(cell):
    return "\n".join([
        "# The monorepo's own escape ledger",
        "",
        "| Date | Escape | Escaped from | Found by | Root cause of the MISS | Change landed |",
        "|---|---|---|---|---|---|",
        f"| 2026-02-02 | a product escape | none | a person | a miss | {cell} |",
        ""])


def not_inside_a_nested_run():
    if os.environ.get(SCRATCH):
        pytest.skip(f"{SCRATCH} is set: this run IS a nested run of an outer placement case, and "
                    "copying the checkout again from inside it does not terminate")


def test_the_eval_reads_this_repositorys_ledger_from_inside_a_monorepo(tmp_path):
    """Criterion 1. The developer's checkout — the one placement the escape was visible in.

    `outer/` keeps a manifest that declares no ledger and `outer/inner/` is this repository, with
    its own manifest and its own ledger. The root rule resolves to `outer/` because the parent
    wins, and the ledger read must still be `inner/`'s.
    """
    not_inside_a_nested_run()
    inner = placement(tmp_path, "outer/inner", parent_manifest=MONOREPO_WITHOUT_A_LEDGER)
    outer_manifest = json.loads((inner.parent / MANIFEST_REL).read_text())
    assert not outer_manifest.get("docs", {}).get("escapes"), \
        "this fixture's parent declares a ledger, so it is not the shape the escape was found in"

    r = run_eval(inner)
    assert r.returncode == 0, \
        f"the eval is not green from a checkout inside a monorepo:\n{r.stdout[-6000:]}{r.stderr[-4000:]}"

    said = summary_line(r.stdout)
    assert ledgers_of(said) == [inner / "docs/operations/ESCAPES.md"], \
        f"the ledger read is not the one under inner/: {said}"
    assert str(inner.parent / MANIFEST_REL) not in said, \
        f"the summary names the parent's manifest, which declares no ledger: {said}"
    got = dict(counts_of(said))
    assert got["swept (Change landed)"] > 0 and got["rows naming a case"] >= ROWS_NAMING_A_CASE, \
        f"the eval passed from inside a monorepo having examined nothing: {said}"


def test_three_placements_read_one_ledger(tmp_path):
    """Criterion 2. Three placements, one answer.

    A checkout one level below a monorepo root, a lane worktree at
    `.factory/worktrees/lane/01`, and a bare clone with nothing above it. Each runs the real
    module and reports which ledger it read and what it counted; the ledgers must be the same
    bytes and the counts the same numbers.
    """
    not_inside_a_nested_run()
    places = {
        "monorepo child": placement(tmp_path, "outer/inner",
                                    parent_manifest=MONOREPO_WITHOUT_A_LEDGER),
        "lane worktree": placement(tmp_path, "x/.factory/worktrees/lane/01"),
        "bare clone": placement(tmp_path, "clone"),
    }
    assert len(places) == 3, "this case is about three placements"

    mine = (CHECKOUT / "docs/operations/ESCAPES.md").read_bytes()
    answers = {}
    for name, root in places.items():
        r = run_eval(root)
        assert r.returncode == 0, \
            f"the eval is not green from the {name}:\n{r.stdout[-6000:]}{r.stderr[-4000:]}"
        said = summary_line(r.stdout)
        read = ledgers_of(said)
        assert [p.read_bytes() for p in read] == [mine], \
            f"the {name} did not read this repository's ledger by content: {read}"
        answers[name] = counts_of(said)

    assert len(set(answers.values())) == 1, \
        "three placements of the same tree gave different answers:\n  " + "\n  ".join(
            f"{name}: {dict(c)}" for name, c in answers.items())
    one = dict(next(iter(answers.values())))
    assert one["swept (Change landed)"] > 0 and one["rows naming a case"] >= ROWS_NAMING_A_CASE, \
        f"all three agreed on a sweep that examined nothing: {one}"


def test_every_declared_ledger_is_swept_and_no_manifest_is_red(tmp_path):
    """Criterion 3. MANDATORY NEGATIVE — the two ways this must refuse.

    (a) The monorepo root declares a ledger of its OWN with a row naming a case that exists
        nowhere. Every declared ledger is swept, so the eval is RED naming that row's date, the
        identifier, and the PARENT's ledger path — and the same placement with the row naming a
        case that does exist is green, which is what says the refusal was about the row.
    (b) No manifest anywhere up the tree: RED saying so. An empty sweep is never a pass.
    """
    not_inside_a_nested_run()

    inner = placement(tmp_path, "two/inner", parent_manifest=MONOREPO_WITH_A_LEDGER)
    theirs = inner.parent / "docs/operations/ESCAPES.md"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text(monorepo_ledger("`test_a_case_this_monorepo_never_had`"))

    red = run_eval(inner)
    said = red.stdout + red.stderr
    assert red.returncode == 1, \
        f"a ledger row naming a case that exists nowhere did not fail:\n{said[-6000:]}"
    assert "test_a_case_this_monorepo_never_had" in said, \
        f"the refusal did not name the identifier that does not exist:\n{said[-6000:]}"
    assert "2026-02-02" in said, f"the refusal did not name the row's date:\n{said[-6000:]}"
    assert str(theirs) in said, \
        f"the refusal did not name the parent's ledger, so a reader cannot find the row:\n{said[-6000:]}"

    theirs.write_text(monorepo_ledger("`test_an_empty_sweep_is_red`"))
    green = run_eval(inner)
    assert green.returncode == 0, (
        "the same placement with the row naming a case that exists is still red, so the refusal "
        f"above was about something other than the missing case:\n{green.stdout[-6000:]}{green.stderr[-4000:]}")
    read = ledgers_of(summary_line(green.stdout))
    assert theirs in read and (inner / "docs/operations/ESCAPES.md") in read, \
        f"both declared ledgers must be swept, and these were read: {read}"

    lone = placement(tmp_path, "nowhere", manifest=False)
    assert not (lone / MANIFEST_REL).exists() and not (lone.parent / MANIFEST_REL).exists(), \
        "this fixture still has a manifest above it, so it is not the placement it claims to be"
    r = run_eval(lone)
    nothing = r.stdout + r.stderr
    assert r.returncode == 1, f"a placement with no manifest at all was not red:\n{nothing[-6000:]}"
    assert "no manifest found" in nothing, \
        f"a placement with no manifest did not say so; it must not be a traceback:\n{nothing[-6000:]}"


# ── SRV-FIX-525 · the copy is what git tracks ──────────────────────────────────────────────────
#
# The three placements above are three DESTINATIONS; the SOURCE of every one is the checkout the
# suite runs in, and it was copied minus a set somebody typed. The set named `.factory/worktrees`
# and `.factory/state` and not `.factory/runs`, where every run the tool has ever made leaves a
# `stdin` FIFO that `shutil.copytree` raises on. A lane has no `.factory/runs`, so `SRV-FIX-520` —
# the round that fixed *a check green only in lanes* — was itself checked only in lanes.
#
# `.gitignore:28` is `.factory/*` with three exceptions, which is `S2` written as policy: what the
# TOOL writes is not one of the repository's documents. Derive the copy from the index and the
# exclusion is that policy rather than a memory of it.


def a_checkout_the_tool_has_run_in(tmp_path, name="worked-in"):
    """A clone of this checkout with what the TOOL writes beside it — the developer's shape.

    134 run directories were on the machine that found this, the oldest FIFO three days old. The
    `git status` assertion is the half that makes the derivation below mean something: it says the
    tool's state is invisible to git HERE, in this fixture, rather than trusting that it is.
    """
    src = clone_for_scratch(tmp_path, name)
    run = src / ".factory" / "runs" / "run-26-ea321563"
    run.mkdir(parents=True)
    os.mkfifo(run / "stdin")
    (run / "run.log").write_text("the run that FIFO belonged to\n")
    (src / ".factory" / "worktrees" / "lane" / "01" / "server").mkdir(parents=True)
    (src / ".factory" / "ids.json").write_text(json.dumps({"SRV-FIX": 525}))

    assert (run / "stdin").is_fifo(), \
        f"{run / 'stdin'} is not a FIFO, so this fixture is not the shape the escape was found in"
    assert git_out(src, "status", "--short", "--", ".factory") == "", (
        "git can see the tool's state in this fixture, so `.gitignore` no longer covers "
        "`.factory/*` and a copy derived from the index would carry the FIFO in anyway")
    return src


def files_under(root):
    return {p.relative_to(root) for p in root.rglob("*") if p.is_symlink() or not p.is_dir()}


def test_the_placement_copy_is_what_git_tracks_and_nothing_the_tool_wrote(tmp_path):
    """Criterion 1 of `SRV-FIX-525`. Four placements built from a checkout the tool has run in.

    These are the placements the three cases above build — a checkout one level below a monorepo
    root, a lane worktree, a bare clone, and the one with no manifest anywhere — every one of them
    made from a source holding a run's `stdin` FIFO, a live lane directory and the tool's id file.
    Copying that source is the step all three cases died in.

    The file set of each copy is asserted to EQUAL the source's index, so the case says what the
    copy contains as well as what it does not; naming only the three absent paths would pass on a
    copy of nothing.
    """
    src = a_checkout_the_tool_has_run_in(tmp_path)
    ledger_rel = Path(json.loads((src / MANIFEST_REL).read_text())["docs"]["escapes"])
    tracked = set(tracked_by_git(src))
    assert len(tracked) > 1000, \
        f"`git ls-files` in {src} listed {len(tracked)} paths, so this fixture is not a checkout"

    out = tmp_path / "out"
    places = [
        ("monorepo child", "outer/inner", dict(parent_manifest=MONOREPO_WITHOUT_A_LEDGER)),
        ("lane worktree", "x/.factory/worktrees/lane/01", {}),
        ("bare clone", "clone", {}),
        ("no manifest anywhere", "nowhere", dict(manifest=False)),
    ]
    for name, rel, how in places:
        dest = placement(out, rel, source=src, **how)
        want = tracked - (set() if how.get("manifest", True) else {Path(MANIFEST_REL)})
        assert files_under(dest) == want, (
            f"the {name} copy is not this checkout's index. Only in the copy: "
            f"{sorted(str(p) for p in files_under(dest) - want)[:12]}; only in the index: "
            f"{sorted(str(p) for p in want - files_under(dest))[:12]}")
        for wrote in (".factory/runs", ".factory/worktrees", ".factory/state", ".factory/ids.json"):
            assert not (dest / wrote).exists(), (
                f"the {name} copy carries `{wrote}`, which is what the TOOL writes and not one of "
                "this repository's documents (`docs/technical/MODEL.md` §2, S2)")
        assert (dest / ledger_rel).is_file() and (dest / "server" / "tests").is_dir(), \
            f"the {name} copy is missing the tree the eval reads, so it is not a placement"
        assert (dest / MANIFEST_REL).is_file() is how.get("manifest", True), \
            f"the {name} copy's manifest does not match what the placement was asked for"


def test_the_copy_follows_the_index_not_the_directory(tmp_path):
    """Criterion 2 of `SRV-FIX-525`. MANDATORY NEGATIVE — git's index decides, a glob does not.

    Three files side by side in one scratch clone, under `docs/`, none of them distinguishable by
    name: a ledger row committed there, a working-tree edit on top of a tracked file, and a file
    nobody added. The first two are the branch as it is being worked and are in the copy; the
    third is not, and a `copytree` of the directory carries it.
    """
    src = clone_for_scratch(tmp_path, "index-not-directory")

    ledger_rel = Path(json.loads((src / MANIFEST_REL).read_text())["docs"]["escapes"])
    ledger = src / ledger_rel
    ledger.write_text(ledger.read_text().rstrip("\n") + "\n\nA row this fixture committed.\n")
    committed = src / "docs" / "a-document-this-fixture-committed.md"
    committed.write_text("as committed\n")
    git_out(src, "add", "--", "docs")
    git_out(src, "commit", "--quiet", "-m", "a ledger row and a document")

    edited = "the working tree's own bytes, never HEAD's\n"
    committed.write_text(edited)
    untracked = src / "docs" / "a-document-nobody-added.md"
    untracked.write_text("nobody ran `git add` on this\n")

    dest = placement(tmp_path, "copy", source=src)

    assert (dest / "docs" / "a-document-this-fixture-committed.md").read_text() == edited, (
        "the copy did not carry the working tree's bytes for a tracked file, so a placement "
        "cannot see the branch as it is being worked")
    assert "A row this fixture committed." in (dest / ledger_rel).read_text(), \
        "the copy did not carry a row committed to the ledger in this clone"
    assert not (dest / "docs" / "a-document-nobody-added.md").exists(), (
        "the copy carried an untracked file, so it is a copy of the DIRECTORY and not of the "
        "index — which is the whole of what `SRV-FIX-525` is about")

    seen = files_under(dest)
    assert seen == set(tracked_by_git(src)), \
        f"the copy and the index disagree on {len(seen ^ set(tracked_by_git(src)))} paths"
