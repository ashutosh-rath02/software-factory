"""SRV-FEAT-480 — a handover is checked before it happens.

QA fails 37% of the verdicts it writes and the first blocker is, more often than not, the SHAPE
of what was handed over: a criterion naming a case that does not exist, a mandatory negative that
is green against the implementation it must be red against, a criterion nobody ever ran. Every
one of those is a fact a script can read off the ticket and the committed logs before a QA
session spends forty minutes finding it.

    server/tools/handover.py docs/tickets/<file>.md

A STAND-IN IS JUDGED AGAINST THE REAL THING. The subject here is two external programs' output —
what pytest prints for a case that failed and for one that passed, and what vitest prints — so
nothing in this file invents a log line:

* **pytest is run for real**, here, at test time. The fixture's cases are written failing, run,
  and the output kept as the red log; then written passing, run, and kept as the green log. That
  is the same program the resolver shells out to, so the two cannot drift apart.
* **vitest cannot run here** — this machine has node 12 and no npm — so its lines are taken
  **verbatim out of captures already committed to this repository**, produced by real runs of the
  real reporter:

      docs/reports/WEB-FEAT-050/WEB-FEAT-050-red-2026-07-30.log:10
      docs/reports/SRV-FEAT-165/SRV-FEAT-165-vitest-2026-08-18.log:15

  Both name `a_card_navigates_to_its_ticket`, which `client/web/tests/lanes-page.test.tsx:147`
  still defines, so the case, its red line and its green line are all real artefacts of this
  repository. To re-capture: `cd client/web && npm run test -- --reporter=verbose`, and keep the
  run in `docs/reports/<ticket-id>/`. `real_line()` asserts the capture still holds what it is
  read for, so a capture that goes stale fails loudly instead of silently sweeping nothing.

    cd server && pytest tests/test_handover.py -v
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
FACTORY = SERVER.parent
TOOL = SERVER / "tools" / "handover.py"

TICKET_ID = "SRV-FEAT-999"

# The width every capture in this file is taken at, and the width the comparison below
# measures it against. pytest renders its short summary to the terminal it believes it has, so
# an unpinned capture is a different document on a different machine — QA round 2, blocker B1.
CAPTURE_COLUMNS = "200"

VITEST_CASE = "a_card_navigates_to_its_ticket"
VITEST_SOURCE = FACTORY / "client" / "web" / "tests" / "lanes-page.test.tsx"
VITEST_REAL_RED = FACTORY / "docs" / "reports" / "WEB-FEAT-050" / "WEB-FEAT-050-red-2026-07-30.log"
VITEST_REAL_GREEN = (FACTORY / "docs" / "reports" / "SRV-FEAT-165"
                     / "SRV-FEAT-165-vitest-2026-08-18.log")

PY_FILE = "tests/test_cases.py"
PY_EVIDENCE = "server/" + PY_FILE
TS_EVIDENCE = "client/web/tests/lanes-page.test.tsx"

PY_PASSING = """def test_the_first_case():
    assert True


def test_the_second_case():
    assert True
"""

PY_FAILING = """def test_the_first_case():
    assert False


def test_the_second_case():
    assert False
"""

PARAMETRISED_OVER_VERDICTS = '''import pytest


@pytest.mark.parametrize("verdict", ["PASS", "FAIL"])
def test_a_verdict_is_recorded(verdict):
    assert verdict in ("PASS", "FAIL")
'''

FAILS_AGAINST_THE_WORD_PASS = '''def test_the_first_case():
    verdict = "FAIL"
    assert verdict == "PASS"
'''

FAILS_SAYING_NOTHING = """def test_the_first_case():
    assert False
"""

REAL_MARKER_CASE = "test_probe1_a_stale_role_branch_validates_a_self_written_pass"
REAL_MARKER_LOG = (FACTORY / "docs" / "reports" / "SRV-FEAT-437"
                   / "SRV-FEAT-437-qa4-break-probe-2026-09-07.log")
REAL_MARKER_SOURCE = f"""def {REAL_MARKER_CASE}():
    assert False
"""


# ── the fixture repository ───────────────────────────────────────────────────────────────────

def project(tmp_path):
    """A repository shaped like this one: `docs/tickets`, `docs/reports/<id>`, `server`, `client`."""
    root = tmp_path / "repo"
    (root / "docs" / "tickets").mkdir(parents=True)
    (root / "docs" / "reports" / TICKET_ID).mkdir(parents=True)
    (root / "server" / "tests").mkdir(parents=True)
    (root / "client" / "web" / "tests").mkdir(parents=True)
    assert VITEST_SOURCE.is_file(), f"{VITEST_SOURCE} is gone; the vitest half has no real file"
    shutil.copy(VITEST_SOURCE, root / "client" / "web" / "tests" / VITEST_SOURCE.name)
    (root / "server" / PY_FILE).write_text(PY_PASSING)
    return root


def ticket(root, criteria, ident=TICKET_ID):
    """A ticket whose §1 carries `criteria` — a list of (behaviour, evidence) pairs."""
    items = "\n\n".join(
        f"{n}. {behaviour}\n   Evidence: {evidence}" if evidence else f"{n}. {behaviour}"
        for n, (behaviour, evidence) in enumerate(criteria, 1))
    path = root / "docs" / "tickets" / f"{ident}-fixture.md"
    path.write_text(
        f"---\nid: {ident}\ntitle: a fixture ticket\nworkspace: factory-server\nstatus: todo\n"
        f"stages: SPEC TEST IMPL BUILD GATE QA\n---\n\n"
        f"# Factory Ticket — a fixture\n\n## 1. Request\n\n"
        f"### Pass criteria — `factory-server`\n\n{items}\n\n"
        f"- **Safety:** this repository only.\n\n"
        f"## 2. Standing process  (FIXED)\n\nnothing here is read.\n")
    return path


def pytest_capture(root, source, targets):
    """Run the REAL pytest over the fixture's cases and return what it printed."""
    (root / "server" / PY_FILE).write_text(source)
    r = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", *targets],
                       cwd=str(root / "server"), capture_output=True, text=True, timeout=300,
                       env={**os.environ, "COLUMNS": CAPTURE_COLUMNS})
    (root / "server" / PY_FILE).write_text(PY_PASSING)
    out = r.stdout + r.stderr
    assert re.search(r"tests/test_cases\.py::test_\w+ (PASSED|FAILED)", out), (
        f"the fixture pytest run produced no result for any case, so the log it becomes proves "
        f"nothing:\n{out}")
    return out


def probe(root, source, targets):
    """Run the REAL pytest over `source` and LEAVE it in place, so the case still exists.

    `pytest_capture` restores the passing fixture because its callers only want the output;
    these cases need the run AND the file, because a case the collector cannot find is reported
    `missing` and the column being measured is never reached.
    """
    (root / "server" / PY_FILE).write_text(source)
    r = subprocess.run([sys.executable, "-m", "pytest", "-v", "--tb=short",
                        "-p", "no:cacheprovider", *targets],
                       cwd=str(root / "server"), capture_output=True, text=True, timeout=300,
                       env={**os.environ, "COLUMNS": CAPTURE_COLUMNS})
    out = r.stdout + r.stderr
    assert re.search(r"tests/test_cases\.py::\S+ (PASSED|FAILED)", out), (
        f"the fixture pytest run produced no result for any case, so the log it becomes proves "
        f"nothing:\n{out}")
    return out


def real_line(capture, case, mark):
    """One genuine line out of a committed capture — the arbiter for what vitest prints."""
    assert capture.is_file(), f"{capture} is gone; re-capture it and update this file"
    for line in capture.read_text(errors="replace").splitlines():
        if case in line and mark in line:
            return line
    raise AssertionError(f"{capture} no longer holds a `{mark}` line for `{case}`")


def log(root, name, text):
    (root / "docs" / "reports" / TICKET_ID / name).write_text(text if text.endswith("\n")
                                                              else text + "\n")


def run(ticket_path, reports=None):
    argv = [str(TOOL), str(ticket_path)]
    if reports is not None:
        argv += ["--reports", str(reports)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=600)


def rows(out):
    """The table's data rows, keyed by the criterion number they carry."""
    found = {}
    for line in out.splitlines():
        m = re.match(r"^(\d+)\s\s+(\S.*)$", line)
        if m:
            found.setdefault(int(m.group(1)), []).append(line)
    return found


def summary(out):
    for line in out.splitlines():
        if line.startswith("criteria:"):
            return line
    raise AssertionError(f"no summary line in:\n{out}")


# ── criterion 1 ──────────────────────────────────────────────────────────────────────────────

def test_a_complete_handover_passes_with_one_row_per_criterion_across_both_test_kinds(tmp_path):
    """§1 criterion 1. Three criteria, BOTH kinds of case — a pytest node id and a vitest
    `path::name` — every one existing, red in one committed log and green in another.

    Both kinds are in one case deliberately: a resolver asserted only on pytest is a resolver
    nobody has driven against the shape half this repository's evidence is written in.
    """
    root = project(tmp_path)
    t = ticket(root, [
        ("A person runs the tool on a ticket whose cases are all real. It prints a row for this "
         "one.", f"{PY_EVIDENCE}::test_the_first_case"),
        ("The second case is read the same way, so the sweep is more than one member.",
         f"{PY_EVIDENCE}::test_the_second_case"),
        ("A client case resolves out of its own file, not out of pytest.",
         f"{TS_EVIDENCE}::{VITEST_CASE}"),
    ])
    log(root, "pytest-red.log", pytest_capture(root, PY_FAILING, [PY_FILE]))
    log(root, "pytest-green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))
    log(root, "vitest-red.log", real_line(VITEST_REAL_RED, VITEST_CASE, "×"))
    log(root, "vitest-green.log", real_line(VITEST_REAL_GREEN, VITEST_CASE, "✓"))

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"a complete handover was refused:\n{out}"

    table = rows(out)
    assert sorted(table) == [1, 2, 3], f"one row per criterion is not what was printed:\n{out}"
    for n, case, red, green in ((1, "test_the_first_case", "pytest-red.log", "pytest-green.log"),
                                (2, "test_the_second_case", "pytest-red.log", "pytest-green.log"),
                                (3, VITEST_CASE, "vitest-red.log", "vitest-green.log")):
        row = table[n][0]
        assert case in row and "yes" in row, f"row {n} does not report the case as existing: {row}"
        assert red in row, f"row {n} does not name the log the case was RED in: {row}"
        assert green in row, f"row {n} does not name the log the case is GREEN in: {row}"
        assert "complete" in row, f"row {n} is not complete: {row}"

    assert summary(out) == ("criteria: 3 · complete: 3 · missing: 0 · never red: 0 · "
                            "not green: 0 · manual: 0"), summary(out)


# ── criterion 2 ──────────────────────────────────────────────────────────────────────────────

def test_a_case_that_exists_nowhere_is_refused_by_name(tmp_path):
    """§1 criterion 2. Both kinds again, because the two resolvers are two programs: one asks
    pytest to collect, the other reads the file. A criterion naming a case nobody wrote is the
    single most common first blocker in this repository's FAIL verdicts.

    The third criterion names a case that IS there, so this asserts a resolver that tells them
    apart rather than one that refuses everything.
    """
    root = project(tmp_path)
    t = ticket(root, [
        ("The tool is run against a criterion naming a pytest case nobody wrote.",
         f"{PY_EVIDENCE}::test_gone"),
        ("And one naming a client case nobody wrote.", f"{TS_EVIDENCE}::gone"),
        ("A real case beside them still resolves.", f"{PY_EVIDENCE}::test_the_first_case"),
    ])
    log(root, "pytest-red.log", pytest_capture(root, PY_FAILING, [PY_FILE]))
    log(root, "pytest-green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, f"a case that exists nowhere did not refuse the handover:\n{out}"
    assert "test_gone" in rows(out)[1][0] and "missing" in rows(out)[1][0], rows(out)[1][0]
    assert "gone" in rows(out)[2][0] and "missing" in rows(out)[2][0], rows(out)[2][0]
    assert "missing" not in rows(out)[3][0], (
        f"the real case was called missing too — the resolver refuses everything: {rows(out)[3][0]}")
    assert "missing: 2" in summary(out), summary(out)


# ── criterion 3 ──────────────────────────────────────────────────────────────────────────────

def test_a_case_never_seen_red_is_refused(tmp_path):
    """§1 criterion 3. TEST-FIRST read from the logs rather than from the report.

    A case that exists and is green proves the code works. It does not prove the check would have
    noticed if the code did not — which is the thing 21 of this repository's 54 FAIL verdicts are
    about. The second criterion is complete, so the refusal is attributable to the missing red log
    and not to an empty reports directory.
    """
    root = project(tmp_path)
    t = ticket(root, [
        ("A case that exists and has a green log and was never watched fail.",
         f"{PY_EVIDENCE}::test_the_first_case"),
        ("A case beside it that was seen both ways.", f"{PY_EVIDENCE}::test_the_second_case"),
    ])
    log(root, "pytest-green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))
    log(root, "pytest-red-second-only.log",
        pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_second_case"]))

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, f"a case never seen red was handed over:\n{out}"
    row = rows(out)[1][0]
    assert "never red" in row, f"row 1 does not say `never red`: {row}"
    assert "complete" in rows(out)[2][0], f"row 2 should be complete: {rows(out)[2][0]}"
    assert "never red: 1" in summary(out), summary(out)


# ── criterion 4 ──────────────────────────────────────────────────────────────────────────────

def test_a_case_with_no_green_log_is_refused_even_when_another_case_is_green(tmp_path):
    """§1 criterion 4. Red once, green nowhere.

    The fixture holds a green log — for a DIFFERENT case. A tool that asks *is some log green*
    rather than *is this case green in some log* passes here and is worth nothing, which is why
    the second criterion must come out `complete` in the same run.
    """
    root = project(tmp_path)
    t = ticket(root, [
        ("A case watched fail and never watched pass.", f"{PY_EVIDENCE}::test_the_first_case"),
        ("A case beside it that is green in the same directory.",
         f"{PY_EVIDENCE}::test_the_second_case"),
    ])
    log(root, "pytest-red.log", pytest_capture(root, PY_FAILING, [PY_FILE]))
    log(root, "pytest-green-second-only.log",
        pytest_capture(root, PY_PASSING, [f"{PY_FILE}::test_the_second_case"]))

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, f"a case green in no log was handed over:\n{out}"
    assert "not green" in rows(out)[1][0], f"row 1 does not say `not green`: {rows(out)[1][0]}"
    assert "complete" in rows(out)[2][0], (
        f"row 2 is not complete, so `some log is green` was never the question: {rows(out)[2][0]}")
    assert "not green: 1" in summary(out), summary(out)


# ── criterion 5 ──────────────────────────────────────────────────────────────────────────────

def test_non_case_evidence_is_listed_as_manual_and_counted(tmp_path):
    """§1 criterion 5. A transcript, a screenshot or a sentence is what QA must do by hand.

    It is listed with its text and counted, and it does NOT block: a tool that refuses every
    criterion it cannot execute would refuse the dogfood criterion every ticket in this
    repository carries, and would be turned off within a week.
    """
    root = project(tmp_path)
    t = ticket(root, [
        ("A real case, so the run has something to be complete about.",
         f"{PY_EVIDENCE}::test_the_first_case"),
        ("This ticket's own handover goes through the tool.",
         "the transcript in the report, every row complete, exit 0."),
        ("The lanes surface is driven by hand in both themes.",
         "the screenshot of the lanes surface, both themes"),
    ])
    log(root, "pytest-red.log", pytest_capture(root, PY_FAILING, [PY_FILE]))
    log(root, "pytest-green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"manual evidence blocked the handover:\n{out}"
    assert "manual" in rows(out)[2][0] and "transcript in the report" in rows(out)[2][0], \
        rows(out)[2][0]
    assert "manual" in rows(out)[3][0] and "screenshot of the lanes surface" in rows(out)[3][0], \
        rows(out)[3][0]
    assert "criteria: 3 · complete: 1" in summary(out), summary(out)
    assert "manual: 2" in summary(out), summary(out)


# ── criterion 6 · MANDATORY NEGATIVE ─────────────────────────────────────────────────────────

def test_a_log_that_only_mentions_the_case_proves_nothing_and_a_ticket_without_evidence_is_exit_2(
        tmp_path):
    """§1 criterion 6, both halves.

    A log naming the case in a command line or a `--collect-only` listing is the shape a session
    reaches for when it wants a green row and has no run: the name is in the file, and a grep for
    the name says yes. The result has to be on the same line as the name or the log proves
    nothing — the row is `never red` AND `not green`, and the exit is 1.

    THE FIXTURE PUTS THE RESULTS SOMEWHERE ELSE IN THE SAME FILES, and that is what makes this a
    negative at all. A first version left the two logs carrying no `PASSED`/`FAILED` anywhere, so
    the whole-file check and the per-line check agreed: reverting the fix to a file-level match
    left the case GREEN. `WEB-FIX-031`'s rule — which break you choose *is* the test. Here one log
    holds the case in a command line beside another case's `FAILED`, the other holds it in a
    collection listing beside another case's `PASSED`, so a file-level match reports the row
    `complete` and exits 0 and this case is deterministic-red against it.

    The same fixture with real red and green runs added exits 0, so what is being measured is the
    missing RESULT and not the missing log.

    Second half: a ticket with no `Evidence:` line at all exits **2**. The question cannot be
    answered, and answering it `pass` — exit 0 — is the failure this whole program exists to stop.
    """
    root = project(tmp_path)
    t = ticket(root, [("A case named in a log, with no result beside it.",
                       f"{PY_EVIDENCE}::test_the_first_case")])
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", PY_FILE],
        cwd=str(root / "server"), capture_output=True, text=True, timeout=300)
    listing = collected.stdout + collected.stderr
    assert "test_the_first_case" in listing, f"the collection listing named no case:\n{listing}"
    beside_a_failure = (f"$ python3 -m pytest {PY_FILE}::test_the_first_case -v\n"
                        + pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_second_case"]))
    beside_a_pass = listing + pytest_capture(root, PY_PASSING,
                                             [f"{PY_FILE}::test_the_second_case"])
    for name, text in (("mentions-beside-a-failure.log", beside_a_failure),
                       ("mentions-beside-a-pass.log", beside_a_pass)):
        assert not re.search(r"test_the_first_case.*(PASSED|FAILED)", text), (
            f"{name} carries a RESULT for the case, so it is not a mention-only log:\n{text}")
        log(root, name, text)
    assert "FAILED" in beside_a_failure and "PASSED" in beside_a_pass, (
        "neither log carries a result for any case, so a whole-file match would agree with a "
        "per-line one and this negative would pin nothing")

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, f"a log that only mentions the case was accepted as proof:\n{out}"
    row = rows(out)[1][0]
    assert "never red" in row and "not green" in row, (
        f"a mention with no result satisfied a column: {row}")
    assert "never red: 1" in summary(out) and "not green: 1" in summary(out), summary(out)

    log(root, "pytest-red.log", pytest_capture(root, PY_FAILING, [PY_FILE]))
    log(root, "pytest-green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))
    r = run(t)
    assert r.returncode == 0, (
        "the same fixture with real runs added is still refused, so the refusal above was about "
        f"something other than the missing result:\n{r.stdout + r.stderr}")

    bare = ticket(root, [("A criterion stating a behaviour and naming nothing executable.", None)],
                  ident="SRV-FEAT-998")
    r = run(bare, reports=root / "docs" / "reports" / TICKET_ID)
    out = r.stdout + r.stderr
    assert r.returncode == 2, (
        f"a ticket with no `Evidence:` line exited {r.returncode}, not 2 — the question cannot be "
        f"answered and 0 says it was:\n{out}")


# ── criteria 3 and 4 again · the class the round-1 QA verdict found ───────────────────────────
#
# `docs/reports/SRV-FEAT-480-qa.md` B1 and B2. The two cases above exhibit ONE member each of the
# class their criteria describe — a case with no red log, a case with no green log — and each
# fixture's names carry no marker substring, so nothing in either could tell a per-line substring
# match from a per-result one. A second member of each class, in which the case's own id or its
# own failure message carries the other tool word, is accepted by a substring match and refused by
# a result match. Both fixtures below are QA's reproductions verbatim, run through the real pytest.

def test_a_green_run_whose_parametrised_id_carries_the_word_fail_is_no_red_log(tmp_path):
    """§1 criterion 3, second member. QA verdict B1.

    `test_a_verdict_is_recorded[FAIL]` is a pytest node id, and the run that prints it ends
    `2 passed`. The word `FAIL` is the parameter, not the result; the result on that line is
    `PASSED`. A case that was never watched fail was handed over as if it had been, because its
    own id supplied the marker its logs did not.
    """
    root = project(tmp_path)
    t = ticket(root, [("A case that was NEVER watched fail — the only run committed is green.",
                       f"{PY_EVIDENCE}::test_a_verdict_is_recorded")])
    green_only = probe(root, PARAMETRISED_OVER_VERDICTS, [PY_FILE])
    log(root, "green-only.log", green_only)

    assert "test_a_verdict_is_recorded[FAIL] PASSED" in green_only, (
        f"the parametrised id carrying the word FAIL is not on a PASSED line, so this fixture "
        f"cannot exhibit the defect it exists for:\n{green_only}")
    assert not re.search(r"::\S+ FAILED", green_only) and "✗" not in green_only, (
        f"the only committed log holds a real failure, so `never red` would be the wrong "
        f"answer:\n{green_only}")

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"a case appearing FAILED in no log was handed over with exit {r.returncode}:\n{out}")
    row = rows(out)[1][0]
    assert "never red" in row, f"row 1 does not say `never red`: {row}"
    assert row.split()[3] == "—", f"the RED column names a log no case ever failed in: {row}"
    assert "never red: 1" in summary(out), summary(out)


def test_a_red_run_whose_failure_message_carries_the_word_pass_is_no_green_log(tmp_path):
    """§1 criterion 4, second member. QA verdict B2.

    pytest writes a failure as `FAILED <nodeid> - AssertionError: assert 'FAIL' == 'PASS'`. The
    words in the assertion text are what the case compared, not what the case did, and a run
    ending `1 failed` is not evidence that anything passed.

    The isolating baseline is in the same case, exactly as QA reported it: the identical fixture
    failing on `assert False` — same case, same single all-red log, no `PASS` in the message — is
    already refused correctly. So what is asserted here is the difference between the two, which
    is one word inside a failure message.
    """
    root = project(tmp_path)
    t = ticket(root, [("A case that is GREEN NOWHERE — the only run committed is red.",
                       f"{PY_EVIDENCE}::test_the_first_case")])

    silent = probe(root, FAILS_SAYING_NOTHING, [PY_FILE])
    log(root, "red-only.log", silent)
    assert "assert False" in silent and "'PASS'" not in silent, (
        f"the baseline log carries the word PASS, so it is not the baseline:\n{silent}")
    baseline = run(t)
    assert baseline.returncode == 1 and "not green" in rows(baseline.stdout + baseline.stderr)[1][0], (
        f"the baseline is already wrong, so this case measures the fixture and not the word:\n"
        f"{baseline.stdout + baseline.stderr}")

    red_only = probe(root, FAILS_AGAINST_THE_WORD_PASS, [PY_FILE])
    log(root, "red-only.log", red_only)
    assert "FAILED tests/test_cases.py::test_the_first_case - AssertionError: assert 'FAIL' == " \
           "'PASS'" in red_only, (
        f"the failure message does not carry the word PASS, so this fixture cannot exhibit the "
        f"defect it exists for:\n{red_only}")
    assert not re.search(r"::\S+ PASSED", red_only) and "✓" not in red_only, (
        f"the only committed log holds a real pass, so `not green` would be the wrong answer:\n"
        f"{red_only}")

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"a case green in no log was handed over with exit {r.returncode}:\n{out}")
    row = rows(out)[1][0]
    assert "not green" in row, f"row 1 does not say `not green`: {row}"
    assert "not green: 1" in summary(out), summary(out)


def test_a_failure_of_another_case_that_quotes_this_ones_address_is_no_red_log(tmp_path):
    """REVIEW, round 3. §1 criterion 3, in the shape the round-1 fix left open.

    The round-1 verdict's two blockers were a marker on a line being credited to a case it did
    not belong to, and the fix made the marker have to sit beside the case's address — for the
    marker that FOLLOWS a case. The marker that LEADS a line is still handed to every case named
    anywhere after it, and this file is a manufacturer of such lines: its own assertions print a
    rendered table row inside the failure message, so `FAILED …::test_rows - …: 1  <address>  yes
    …` names a second case with a leading `FAILED` in front of it.

    A case whose only committed failure is somebody else's is a case nobody watched fail. Driven
    by hand at round 3 it read `complete`, exit 0 — the handover granted on evidence about
    another test.
    """
    root = project(tmp_path)
    t = ticket(root, [("A case whose only red line is another case's failure quoting its row.",
                       f"{PY_EVIDENCE}::test_the_first_case")])
    log(root, "green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))
    quoted = (f"FAILED tests/test_handover.py::test_rows - AssertionError: row 1 does not say "
              f"`never red`: 1  {PY_EVIDENCE}::test_the_first_case  yes  \u2014  green.log  never red")
    log(root, "another-case-failed.log", quoted)

    assert quoted.startswith("FAILED ") and f"{PY_EVIDENCE}::test_the_first_case" in quoted, quoted
    assert not re.search(r"::test_the_first_case\s+(FAILED|PASSED)", quoted), (
        f"the quoted row carries a result of its own, so the line is not the shape this case is "
        f"about:\n{quoted}")

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"a case whose only failure line belongs to another test was handed over with exit "
        f"{r.returncode}:\n{out}")
    row = rows(out)[1][0]
    assert "never red" in row, f"row 1 does not say `never red`: {row}"
    assert row.split()[3] == "\u2014", f"the RED column names another case's failure: {row}"


def test_a_run_of_a_same_named_case_in_another_file_evidences_nothing(tmp_path):
    """REVIEW, round 3. §1 criteria 3 and 4 — a case is `path::name`, and only the path says which.

    `Pre-decided` writes a case as `path::name`, and the resolver honours the path: two files may
    each define `test_the_first_case` and they are different cases. The log side read the NAME
    alone, so both columns of `server/tests/test_cases.py::test_the_first_case` were satisfied by
    a red run and a green run of `somewhere/else/test_other.py::test_the_first_case` — a handover
    answered entirely out of another file's history. Driven by hand at round 3: `complete`, exit 0.

    The two files are forced apart before the assertion, which is the whole of the criterion: a
    fixture whose logs name the case's own file passes identically whichever the code reads.
    """
    root = project(tmp_path)
    t = ticket(root, [("A case whose only red and green runs are another file's case of the same "
                       "name.", f"{PY_EVIDENCE}::test_the_first_case")])
    log(root, "other-red.log", "somewhere/else/test_other.py::test_the_first_case FAILED  [100%]")
    log(root, "other-green.log", "somewhere/else/test_other.py::test_the_first_case PASSED  [100%]")

    assert PY_EVIDENCE not in "somewhere/else/test_other.py", PY_EVIDENCE

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"another file's case of the same name answered this one's handover with exit "
        f"{r.returncode}:\n{out}")
    row = rows(out)[1][0]
    assert "yes" in row, f"the case did not resolve, so no column was measured: {row}"
    assert "never red" in row and "not green" in row, (
        f"a run of another file's case satisfied a column: {row}")

def test_a_result_line_this_repository_committed_is_read_as_the_one_result_it_is(tmp_path):
    """The same shape, out of a log that was already committed here — not one this file invented.

    `docs/reports/SRV-FEAT-437/SRV-FEAT-437-qa4-break-probe-2026-09-07.log` records a real break
    probe whose failure message reads *"the only PASS anybody wrote for this ticket was written by
    the session that built it"*. One line, one case, one result — `FAILED` — and the word `PASS`
    sitting in the sentence after it. A run held only that line and the tool called the case
    green.
    """
    root = project(tmp_path)
    line = real_line(REAL_MARKER_LOG, REAL_MARKER_CASE, "FAILED")
    assert "PASS" in line, f"{REAL_MARKER_LOG} no longer holds the word PASS on that line: {line}"

    address = re.search(r"([\w./+-]+\.py)::" + REAL_MARKER_CASE, line)
    assert address, f"the committed line no longer carries the case's address: {line}"
    spelling = address.group(1)

    (root / "server" / spelling).write_text(REAL_MARKER_SOURCE)
    t = ticket(root, [("A case evidenced by one real committed failure line.",
                       f"server/{spelling}::{REAL_MARKER_CASE}")])
    log(root, "committed-failure.log", line)

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"one real FAILED line was read as proof the case is green:\n{out}")
    row = rows(out)[1][0]
    assert "yes" in row, f"the case did not resolve, so no column was measured: {row}"
    assert "committed-failure.log" in row, f"the failure was not read as the RED log: {row}"
    assert "not green" in row, f"a FAILED line satisfied the GREEN column: {row}"
    assert "never red" not in row, f"a FAILED line did not satisfy the RED column: {row}"


# ── what driving it over the real corpus found (no rider: asserted here, same commit) ─────────

def module():
    """`handover.py` loaded as a module, so the parser can be driven over documents directly."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("handover", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_ticket_written_under_the_evidence_convention_is_read_by_the_parser(tmp_path):
    """The parser is driven over EVERY ticket here, not over the one shape this ticket uses.

    Written after driving the first implementation by hand: `SRV-FIX-476` reported *no numbered
    pass criteria in §1* and exited 2 while carrying seven. `_TEMPLATE.md` writes the block as a
    BULLET — `- **Pass criteria** — one block per workspace touched` — with the criteria indented
    under it; this ticket writes it as a `### Pass criteria` HEADING with them at column zero.
    Both are correct, the corpus uses both, and a parser that reads one silently answers `nothing
    to check` about the other — the one verdict this program may never give.

    The population is the tickets written under the `Evidence:` convention, because those are the
    ones this program can answer for at all. The older ones — `SRV-FEAT-001` and its neighbours,
    whose criteria are unnumbered bullets and name no case — exit 2, and exit 2 is the honest
    answer there: the question cannot be put.

    Two properties, because the first alone is satisfied by a parser that finds ONE criterion and
    drops the rest: every such ticket yields criteria, and **every `Evidence:` line inside the
    block lands inside a criterion body**. A criterion sliced away takes its case with it and the
    summary quietly shrinks.
    """
    mod = module()
    tickets = [t for t in sorted((FACTORY / "docs" / "tickets").glob("*.md"))
               if not t.name.startswith("_")]
    assert len(tickets) > 50, f"the sweep found {len(tickets)} tickets and proves nothing"

    swept, shapes, blind, lost = 0, set(), [], []
    for t in tickets:
        text = t.read_text(errors="replace")
        block = "".join(mod.pass_blocks(text))
        if "Evidence:" not in block:
            continue
        swept += 1
        shapes.add("heading" if re.search(r"^#{1,4}\s*Pass criteria", text, re.M) else "bullet")
        items = mod.criteria(text)
        if not any("Evidence:" in body for _n, body in items):
            blind.append(t.name)
        elif sum(body.count("Evidence:") for _n, body in items) != block.count("Evidence:"):
            lost.append(t.name)

    assert swept > 100, f"only {swept} tickets are written under the convention; the sweep is thin"
    assert shapes == {"heading", "bullet"}, (
        f"the corpus only writes {shapes}, so one shape is exercised and the other is unasserted")
    assert not blind, (
        "these tickets name their evidence and the parser sees no criterion, so `handover.py` "
        f"answers `nothing to check` about them: {blind}")
    assert not lost, (
        f"these tickets have `Evidence:` lines that fall outside every criterion body, so their "
        f"cases are silently dropped from the table: {lost}")


def test_a_case_spelled_without_its_directory_still_resolves(tmp_path):
    """Also from driving it: this repository writes `test_land.py::<case>` as often as it writes
    `server/tests/test_land.py::<case>`, and the first spelling resolved to nothing.

    A false `missing` is worse than no check at all — it refuses a handover that was correct and
    teaches the next session to ignore the tool. The refusal has to survive it: a case nobody
    wrote is still `missing`, in the same run, or the fallback has just made the resolver say yes
    to everything.
    """
    mod = module()
    root = FACTORY
    cache = {}
    assert mod.exists("test_land.py", "test_a_passed_branch_reaches_main_as_one_commit_and_is_reaped",
                      root, cache), (
        "`test_land.py::<case>` — the spelling half this repository's tickets use — resolved to "
        "nothing, so every criterion written that way is refused as missing")
    assert mod.exists("server/tests/test_land.py",
                      "test_a_passed_branch_reaches_main_as_one_commit_and_is_reaped", root, cache)
    assert not mod.exists("test_land.py", "test_nobody_ever_wrote_this_one", root, cache), (
        "the fallback resolves a case nobody wrote; it says yes to everything")
    assert not mod.exists("test_no_such_file_anywhere.py", "test_x", root, cache)


def test_a_case_name_that_ends_a_sentence_is_not_refused_for_its_full_stop(tmp_path):
    """REVIEW, not a verdict — no ticket writes this shape today and one will.

    `Evidence:` is prose, and prose ends in a full stop. `…::test_x.` captured the period as part
    of the name, which resolves to nothing and prints `missing` about a case that is there. Same
    class as the bare-filename spelling above, and a false `missing` refuses a correct handover.
    """
    root = project(tmp_path)
    t = ticket(root, [
        ("A criterion whose evidence line ends in a full stop.",
         f"{PY_EVIDENCE}::test_the_first_case."),
        ("And one that ends in nothing at all.", f"{PY_EVIDENCE}::test_the_second_case"),
    ])
    log(root, "pytest-red.log", pytest_capture(root, PY_FAILING, [PY_FILE]))
    log(root, "pytest-green.log", pytest_capture(root, PY_PASSING, [PY_FILE]))

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"a case name ending a sentence was refused:\n{out}"
    assert "complete" in rows(out)[1][0], rows(out)[1][0]
    assert "complete" in rows(out)[2][0], rows(out)[2][0]


RESULT_LINE = (
    ("green", re.compile(r"^\s*(?:\[\S+\]\s*)?[\w./+-]+\.py::(?P<name>[A-Za-z_]\w*)"
                         r"(?:\[[^\]]*\])?\s+PASSED\b")),
    ("red", re.compile(r"^\s*(?:\[\S+\]\s*)?[\w./+-]+\.py::(?P<name>[A-Za-z_]\w*)"
                       r"(?:\[[^\]]*\])?\s+FAILED\b")),
    ("red", re.compile(r"^\s*FAILED\s+[\w./+-]+\.py::(?P<name>[A-Za-z_]\w*)(?:\[[^\]]*\])?")),
    ("green", re.compile(r"^\s*✓\s+[\w./+-]+\.tsx?\s*>\s*(?P<name>.+)$")),
    ("red", re.compile(r"^\s*[×✗]\s+[\w./+-]+\.tsx?\s*>\s*(?P<name>.+)$")),
)


def corpus_case(kind, match):
    if kind == "py":
        return match.group("name")
    tail = match.group("name").split(">")[-1].strip()
    word = re.match(r"[A-Za-z_]\w*", tail)
    return word.group(0) if word else ""


def test_no_result_line_in_the_committed_corpus_is_read_as_both_colours_or_as_neither():
    """Both directions of the same rule, over every log this repository has committed.

    The three cases above are the round-1 verdict's reproductions, and a fixture a session wrote
    is bounded by what that session already thought of. The corpus is not: every `*.log` under
    `docs/reports/` is a real run of one of the two tools this program reads, and each tool's own
    result grammar — `<nodeid> PASSED`, `<nodeid> FAILED`, `FAILED <nodeid> - …`, a leading `✓`,
    a leading `×`/`✗` — says what the line means without this file deciding it.

    Two properties, and the second is why this case exists at all. A result that is read as BOTH
    colours is the round-1 blocker: one line hands over a case that was never watched fail, or one
    that never passed. A result that is read as NEITHER is the cost of overshooting the fix for
    it — a false `never red` about a case somebody did watch fail, which
    `test_a_case_spelled_without_its_directory_still_resolves` states the price of: it teaches the
    next session to ignore the tool.
    """
    mod = module()
    logs = sorted((FACTORY / "docs" / "reports").rglob("*.log"))
    both, neither = [], []
    seen = [0] * len(RESULT_LINE)
    for path in logs:
        for line in path.read_text(errors="replace").splitlines():
            for index, (colour, grammar) in enumerate(RESULT_LINE):
                m = grammar.match(line)
                if not m:
                    continue
                seen[index] += 1
                name = corpus_case("py" if index < 3 else "ts", m)
                if not name:
                    break
                mention = re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])(\[[^\]\s]*\])?")
                is_red, is_green = mod.verdicts(line, mention)
                got = {"red": bool(is_red), "green": bool(is_green)}
                if is_red and is_green:
                    both.append((path.name, line[:160]))
                elif not got[colour]:
                    neither.append((colour, path.name, line[:160]))
                break

    assert logs, "no committed log was swept, so this case examined nothing"
    for index, (colour, _grammar) in enumerate(RESULT_LINE):
        assert seen[index], (
            f"grammar {index} ({colour}) matched no line in {len(logs)} logs; both tools and "
            f"both colours must be present or the sweep is not the sweep it claims to be")
    assert not both, (
        f"{len(both)} committed result line(s) satisfy the RED column and the GREEN column at "
        f"once, so one line is evidence that a case both failed and passed:\n"
        + "\n".join(f"  {n}: {l}" for n, l in both[:8]))
    assert not neither, (
        f"{len(neither)} committed result line(s) no longer read as the result they are, so a "
        f"case somebody did watch fail would be refused as `never red`:\n"
        + "\n".join(f"  want {c}: {n}: {l}" for c, n, l in neither[:8]))


# ── criteria 3 and 4 in the client's spelling · what REVIEW found on the round-2 diff ─────────
#
# The round-1 verdict's two blockers were both pytest lines, and the fix that closed them decides
# a marker by the text BETWEEN it and the case address. That leaves the same class open on the
# other tool: vitest writes the result as the LEADING token of the line and the words in between
# are a suite title, which a person is free to write in. So a green client run whose suite says
# the word FAIL supplies a red column — B1 in the client's spelling — and a green client run
# whose suite title carries a colon supplies nothing at all, which is the false `not green` that
# teaches the next session to ignore the tool.
#
# Both lines below are the committed capture's own line with ONE edit, inside the suite title and
# nowhere else. `vitest_suite()` makes that edit and asserts what it did, so a capture that goes
# stale fails here rather than sweeping nothing.

def vitest_suite(capture, case, mark, title):
    """The committed `mark` line for `case`, with only its SUITE TITLE replaced by `title`."""
    line = real_line(capture, case, mark)
    m = re.match(r"^(\s*" + re.escape(mark) + r"\s+)(.*?)( > " + re.escape(case) + r"\b.*)$", line)
    assert m, f"the committed line is no longer `{mark} <suite> > {case} …`: {line}"
    edited = m.group(1) + title + m.group(3)
    assert case in edited and edited.lstrip().startswith(mark), edited
    return edited


def test_a_green_client_run_whose_suite_title_says_fail_is_no_red_log(tmp_path):
    """§1 criterion 3, the client's spelling. Same class as the round-1 verdict's B1.

    A vitest suite title is prose a person writes, and this repository writes titles about gates
    saying no. The result of the line is its leading `✓`; the word FAIL inside the title is the
    subject of the suite, not what the run did.
    """
    root = project(tmp_path)
    t = ticket(root, [("A client case whose only committed run is green.",
                       f"{TS_EVIDENCE}::{VITEST_CASE}")])
    green = vitest_suite(VITEST_REAL_GREEN, VITEST_CASE, "✓",
                         "4.14 the gate says FAIL when a lane is gone")
    log(root, "vitest-green.log", green)

    assert "FAIL" in green and green.lstrip().startswith("✓"), green
    assert "×" not in green and "✗" not in green, green

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 1, (
        f"a client case appearing red in no log was handed over with exit {r.returncode}:\n{out}")
    row = rows(out)[1][0]
    assert "never red" in row, f"row 1 does not say `never red`: {row}"
    assert row.split()[3] == "—", f"the RED column names a log no case ever failed in: {row}"


def test_a_client_result_survives_a_suite_title_the_reader_writes_in(tmp_path):
    """The other direction, and the cost of overshooting the fix for the first.

    `✓ 4.14 all lanes: the card > a_card_navigates_to_its_ticket` is a real pass. A rule that
    reads the words between the marker and the case name loses it to the colon, and the tool then
    refuses a handover that is in order — `test_a_case_spelled_without_its_directory_still_resolves`
    states what that costs. The result is the leading token; the title is not evidence.
    """
    root = project(tmp_path)
    t = ticket(root, [("A client case red once and green once, both suites titled in prose.",
                       f"{TS_EVIDENCE}::{VITEST_CASE}")])
    green = vitest_suite(VITEST_REAL_GREEN, VITEST_CASE, "✓", "4.14 all lanes: the card")
    red = vitest_suite(VITEST_REAL_RED, VITEST_CASE, "×", "4.14 all lanes — a card that is gone")
    log(root, "vitest-green.log", green)
    log(root, "vitest-red.log", red)

    assert ": " in green and "—" in red, (green, red)

    r = run(t)
    out = r.stdout + r.stderr
    assert r.returncode == 0, (
        f"a complete client handover was refused over the words in a suite title:\n{out}")
    row = rows(out)[1][0]
    assert "complete" in row, f"row 1 is not complete: {row}"
    assert "vitest-red.log" in row and "vitest-green.log" in row, row


# ── the file's own captures are not about the terminal that took them ────────────────────────

def test_no_case_in_this_file_answers_differently_at_eighty_columns(request):
    """QA round 2's blocker, transcribed from the two commands its verdict reproduces it with.

        python3 -m pytest $C -q                 # COLUMNS unset — what a redirected gate gets
        #   1 failed in 0.62s
        COLUMNS=200 python3 -m pytest $C -q     # a wide interactive terminal
        #   1 passed in 0.79s

    Every case here measures `handover.py` against text pytest printed, and pytest renders its
    short summary to the width of the terminal it believes it has — truncating it with `...`
    when the line will not fit. So a fixture that reads that line answers one way on the author's
    terminal and the other way inside `run-gate.sh`, where stdout is a file and the width is 80.
    A gate that decides whether a handover may happen then decides it about the shell it was run
    in, which is the one thing this ticket exists to stop.

    The sweep is every case pytest collects from this file, taken from pytest rather than typed;
    the one exclusion is this case itself, because a case that collects and runs itself does not
    terminate. Both widths come from the verdict's own two commands.
    """
    mine = request.node.name
    here = f"tests/{Path(__file__).name}"
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", here],
        cwd=str(SERVER), capture_output=True, text=True, timeout=300)
    listing = collected.stdout + collected.stderr
    names = [m.group(1) for m in re.finditer(r"^\s*(?:\S*::)?<?(?:Function\s+)?(test_\w+)>?\s*$",
                                             listing, re.M)]
    assert mine in names, (
        f"this case is not in its own collection, so the exclusion below sweeps the wrong set:\n"
        f"{listing}")
    targets = [f"{here}::{n}" for n in names if n != mine]
    assert len(targets) >= 2, (
        f"the sweep holds {len(targets)} case(s) and examines nothing:\n{collected.stdout}")

    seen = {}
    for width in ("80", CAPTURE_COLUMNS):
        r = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider",
                            *targets],
                           cwd=str(SERVER), capture_output=True, text=True, timeout=1800,
                           env={**os.environ, "COLUMNS": width})
        out = r.stdout + r.stderr
        seen[width] = {m.group(1): m.group(2) for m in
                       re.finditer(r"^(tests/\S+::\S+) (PASSED|FAILED|ERROR)", out, re.M)}
        assert sorted(seen[width]) == sorted(targets), (
            f"at COLUMNS={width} pytest reported on {len(seen[width])} of the {len(targets)} "
            f"cases collected, so the comparison below is over a set neither run measured:\n{out}")

    differ = [i for i in targets if seen["80"][i] != seen[CAPTURE_COLUMNS][i]]
    assert not differ, (
        "these cases answer differently at 80 columns than at " + CAPTURE_COLUMNS
        + ", so this file's verdict is about the terminal it ran in:\n"
        + "\n".join(f"  {i}: COLUMNS=80 {seen['80'][i]} · COLUMNS={CAPTURE_COLUMNS} "
                    f"{seen[CAPTURE_COLUMNS][i]}" for i in differ))
    red = [i for i in targets if seen["80"][i] != "PASSED"]
    assert not red, (
        "agreeing at both widths is not enough — these are red at 80 columns, which is the width "
        "every gate here runs at:\n" + "\n".join(f"  {i}: {seen['80'][i]}" for i in red))


# ── SRV-FIX-532 cluster F1 — a handover with no case in it, and an evidence line that wraps ───
#
# `SRV-FIX-494` and `SRV-FIX-496`. Two ways the check said YES where it should have said NO, and
# both reach the same place: the criterion falls through to `manual`, which by `SRV-FEAT-480` §1
# criterion 5 does not block. A ticket with no `Evidence:` line at all is exit 2 — *the question
# cannot be answered* — and a ticket whose every `Evidence:` line names no case was exit 0. Those
# are the same condition reached two ways, and they got opposite answers.


def wrapped(path, name):
    """One node id broken across two lines, the way a person reflowing a ticket breaks it.

    216 `Evidence:` lines in `docs/tickets/` are already longer than the 100 columns every other
    line in these documents is wrapped at, and none is wrapped today. Nothing makes that true
    tomorrow.
    """
    return f"`{path}::\n   {name}`"


def test_a_ticket_whose_every_criterion_is_manual_cannot_be_handed_over(tmp_path):
    """§1 F1 — a ticket that names no case is refused the way one with no evidence is.

    `SRV-FIX-494` criterion 1. Three criteria, each carrying an `Evidence:` line naming a
    screenshot, a transcript and a quoted refusal. Exit 2, and the message names the criteria that
    carry no case — so the session is told what to write, not what to delete.

    RED before the fix: `criteria: 3 · complete: 0 · … · manual: 3` and `EXIT=0`, over a reports
    directory holding zero logs.
    """
    root = project(tmp_path)
    path = ticket(root, [
        ("the screenshot in the report, both themes",
         "the screenshot in the report, both themes"),
        ("the transcript in the report, every row complete",
         "the transcript in the report, every row complete, exit 0."),
        ("the refusal quoted in the report", "the refusal quoted in the report"),
    ])

    r = run(path)
    assert r.returncode == 2, (
        f"a ticket naming no case at all was handed over: EXIT={r.returncode}\n{r.stdout}\n"
        f"{r.stderr}")
    assert "manual: 3" in summary(r.stdout), summary(r.stdout)
    for number in ("1", "2", "3"):
        assert number in r.stderr, (
            f"the refusal does not name criterion {number}, so the session is told there is a "
            f"problem and not where:\n{r.stderr}")


def test_one_real_case_still_carries_a_ticket_whose_other_criteria_are_manual(tmp_path):
    """§1 F-NEG — one real case is enough, and `manual` beside it still does not block.

    `SRV-FIX-494` criterion 2. `SRV-FEAT-480` §1 criterion 5 is not narrowed by the fix: a ticket
    with one complete case and two prose criteria is exit 0 with `complete: 1 · manual: 2`. A fix
    that refused everything carrying prose would pass the case above and fail here.
    """
    root = project(tmp_path)
    red = pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_first_case"])
    green = pytest_capture(root, PY_PASSING, [f"{PY_FILE}::test_the_first_case"])
    log(root, "red-2026-09-11.log", red)
    log(root, "green-2026-09-11.log", green)

    path = ticket(root, [
        ("the behaviour a case evidences", f"server/{PY_FILE}::test_the_first_case"),
        ("the screenshot in the report, both themes", "the screenshot in the report, both themes"),
        ("the refusal quoted in the report", "the refusal quoted in the report"),
    ])

    r = run(path)
    assert r.returncode == 0, (
        f"a ticket with one complete case and two prose criteria was refused: "
        f"EXIT={r.returncode}\n{r.stdout}\n{r.stderr}")
    assert "complete: 1" in summary(r.stdout) and "manual: 2" in summary(r.stdout), (
        summary(r.stdout))


def test_a_node_id_wrapped_across_two_lines_is_read_as_the_case_it_names(tmp_path):
    """§1 F1 — a wrapped node id is the case it names, and its absence is `missing`, exit 1.

    `SRV-FIX-496` criterion 1. The join in `evidence()` collapsed the break to a single space, and
    the pattern cannot cross a space, so the criterion fell through to `manual` and the handover
    passed with a criterion naming a case that exists nowhere.

    RED before the fix: the row reads ``server/tests/test_cases.py:: test_a_case_never_written``
    with status `manual`, and `EXIT=0`.
    """
    root = project(tmp_path)
    red = pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_first_case"])
    green = pytest_capture(root, PY_PASSING, [f"{PY_FILE}::test_the_first_case"])
    log(root, "red-2026-09-11.log", red)
    log(root, "green-2026-09-11.log", green)

    path = ticket(root, [
        ("a behaviour whose case was never written",
         wrapped(f"server/{PY_FILE}", "test_a_case_that_was_never_written")),
        ("a behaviour that is evidenced", f"server/{PY_FILE}::test_the_first_case"),
    ])

    r = run(path)
    assert r.returncode == 1, (
        f"a wrapped node id naming a case that does not exist was not refused: "
        f"EXIT={r.returncode}\n{r.stdout}\n{r.stderr}")
    assert "missing: 1" in summary(r.stdout), summary(r.stdout)
    assert "manual: 0" in summary(r.stdout), (
        f"the wrapped id was still read as prose:\n{summary(r.stdout)}")
    first = " ".join(rows(r.stdout)[1])
    assert "test_a_case_that_was_never_written" in first and ":: " not in first, (
        f"the row does not name the case the wrap spells:\n{first}")


def test_a_wrapped_node_id_for_a_real_case_is_read_to_completion(tmp_path):
    """§1 F1 — the same wrap on a case that DOES exist reaches the log columns.

    `SRV-FIX-496` criterion 2. The fix resolves the case rather than merely refusing everything it
    cannot parse: a wrapped id naming a real case, with a committed red log and a committed green
    log, comes out `complete`.
    """
    root = project(tmp_path)
    red = pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_first_case"])
    green = pytest_capture(root, PY_PASSING, [f"{PY_FILE}::test_the_first_case"])
    log(root, "red-2026-09-11.log", red)
    log(root, "green-2026-09-11.log", green)

    path = ticket(root, [
        ("a behaviour that is evidenced",
         wrapped(f"server/{PY_FILE}", "test_the_first_case")),
    ])

    r = run(path)
    assert r.returncode == 0, (
        f"a wrapped id naming a real, red-then-green case was refused: EXIT={r.returncode}\n"
        f"{r.stdout}\n{r.stderr}")
    assert "complete: 1" in summary(r.stdout), summary(r.stdout)


def test_prose_naming_a_file_is_still_manual_after_the_wrap_is_read(tmp_path):
    """§1 F-NEG — prose is still prose, and a sentence containing a file name is not a case.

    `SRV-FIX-496` criterion 3, the mandatory negative. A fix that widens the pattern until every
    mention of a `.py` file becomes a case turns `SRV-FEAT-480` §1 criterion 5 red and refuses the
    dogfood criterion every ticket in this repository carries. Both members are driven: a sentence
    with no path at all, and a sentence naming a real path with no `::` and no case.
    """
    root = project(tmp_path)
    red = pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_first_case"])
    green = pytest_capture(root, PY_PASSING, [f"{PY_FILE}::test_the_first_case"])
    log(root, "red-2026-09-11.log", red)
    log(root, "green-2026-09-11.log", green)

    prose = {
        "a sentence with no path in it":
            "the transcript in the report, every row complete, exit 0.",
        "a sentence naming a real path with no case":
            f"the screenshot of `server/{PY_FILE}` being edited, both themes",
    }
    path = ticket(root, [("a behaviour that is evidenced",
                          f"server/{PY_FILE}::test_the_first_case")]
                  + [(what, said) for what, said in sorted(prose.items())])

    r = run(path)
    assert r.returncode == 0, (
        f"prose beside one real case blocked the handover: EXIT={r.returncode}\n{r.stdout}\n"
        f"{r.stderr}")
    assert f"manual: {len(prose)}" in summary(r.stdout), (
        f"a sentence was read as a case:\n{summary(r.stdout)}\n{r.stdout}")


def test_a_named_case_that_is_absent_is_still_exit_1_not_exit_2(tmp_path):
    """§1 F1 MANDATORY NEGATIVE — the refusal is about the absence of a CASE, not a word count.

    `SRV-FIX-494` criterion 3. A ticket whose only criterion names a case that does not exist is
    exit 1 with `missing`: the question was asked and answered `no`, and collapsing the two exits
    would hide which of them happened. And the exit-2 message must not fire on a ticket that names
    one case and nine sentences — driven here, because that is the shape every ticket in this
    repository with a dogfood criterion has.
    """
    root = project(tmp_path)
    path = ticket(root, [("a behaviour whose case was never written",
                          f"server/{PY_FILE}::test_a_case_that_was_never_written")])
    r = run(path)
    assert r.returncode == 1, (
        f"a named case that is absent must be exit 1, not {r.returncode}:\n{r.stdout}\n{r.stderr}")
    assert "missing: 1" in summary(r.stdout), summary(r.stdout)
    assert "not one criterion names a case" not in r.stderr, (
        f"the exit-2 message fired on a ticket that names a case:\n{r.stderr}")

    red = pytest_capture(root, PY_FAILING, [f"{PY_FILE}::test_the_first_case"])
    green = pytest_capture(root, PY_PASSING, [f"{PY_FILE}::test_the_first_case"])
    log(root, "red-2026-09-11.log", red)
    log(root, "green-2026-09-11.log", green)
    nine = [(f"a behaviour nobody automated ({i})", f"the report's own account, round {i}")
            for i in range(1, 10)]
    path = ticket(root, [("a behaviour that is evidenced",
                          f"server/{PY_FILE}::test_the_first_case")] + nine)
    r = run(path)
    assert r.returncode == 0, (
        f"one case and nine sentences was refused: EXIT={r.returncode}\n{r.stdout}\n{r.stderr}")
    assert "manual: 9" in summary(r.stdout), summary(r.stdout)
