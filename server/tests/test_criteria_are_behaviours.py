"""A pass criterion is a behaviour, and names the test that evidences it.

`CLAUDE.md`, `docs/technical/workflow/WORKFLOW.md` and `docs/tickets/_TEMPLATE.md` §1 say so. This makes it
checkable, because the rule it replaces was prose too and drifted for 316 tickets.

Why the rule changed: a criterion that is only a test NAME can be satisfied by writing the test
that satisfies the name, and a QA session reading it can only grade the assertion. 21 of this
repository's 54 FAIL verdicts are a check fitted to what already worked; `SRV-FIX-351` failed four
rounds in one day and its mechanism was never wrong. A behaviour gives the test something outside
itself to be checked against.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.repository

FACTORY = Path(__file__).resolve().parents[2]
TICKETS = FACTORY / "docs" / "tickets"

# Frozen by NUMBER, the mechanism this repository already uses for a rule that arrives mid-life.
# Tickets below it were written under the old rule and are not retro-fitted; the set only shrinks
# because ticket numbers only go up.
#
# PER SERIES, not one global number. `FAC-` was one counter; `SRV-` and `WEB-` are two, and they
# are far apart — `SRV` was at 368 when this rule landed and `WEB` at 319. A single ceiling of 363
# therefore governed every new SRV ticket and NO new WEB ticket for the next forty-odd numbers.
# QA proved it: a `WEB-FIX-320` carrying both forbidden shapes left the suite at `3 passed`.
# (SRV-FIX-368 QA, F2, filed as SRV-FIX-372.)
FROM_NUMBER = {"SRV": 363, "WEB": 320}

# A criterion line that is ONLY a test name — optionally bolded, optionally backticked, optionally
# with a `file::case` path. Nothing a person does, nothing the product then does.
BARE = re.compile(
    r"""^\s*\d+\.\s*\**\s*`?[\w./]*(?:::)?(?:test_|it\(|describe\()[\w.:/-]*`?\s*\**\s*[.—-]?\s*$""",
    re.X)


def criteria_blocks():
    """Every `### Pass criteria` section of every ticket at or above the ceiling."""
    for f in sorted(TICKETS.glob("*.md")):
        if f.name.startswith("_"):
            continue
        text = f.read_text()
        m = re.search(r"^id:\s*(\S+)", text[:600], re.M)
        if not m:
            continue
        tid = m.group(1)
        num = re.search(r"(\d+)$", tid)
        ceiling = FROM_NUMBER.get(tid.split("-")[0])
        if not num or ceiling is None or int(num.group(1)) < ceiling or "NNN" in tid:
            continue
        block = re.search(r"^###\s*Pass criteria\s*$(.*?)^##\s", text, re.M | re.S)
        if block:
            yield tid, f, block.group(1)


def bare_names(block):
    """Criterion lines that are only a test name. A pure function, so the negatives below can
    drive it on synthetic input — this file's sweep is legitimately empty until a ticket is
    numbered at or above the ceiling, and a case that governs nothing proves nothing."""
    return [l.strip() for l in block.splitlines() if BARE.match(l)]


def unevidenced(block):
    """Criteria that state a behaviour and name no case."""
    items = [b for b in re.split(r"^\s*(?=\d+\.\s)", block, flags=re.M)
             if re.match(r"\s*\d+\.", b)]
    return [i.strip().splitlines()[0] for i in items
            if not re.search(r"(Evidence:|test_\w+|::\w+|\bit\(|\bdescribe\()", i)]


BEHAVIOUR_OK = """
1. Open the wiki surface on a repository with six declared documents. All six appear, in the
   declared order, each under its own section heading.
   Evidence: client/web/tests/wiki.test.tsx::renders_the_declared_sections
"""
BARE_NAME = """
1. **`test_the_wiki_surface_renders_six_sections`**
"""
NO_EVIDENCE = """
1. Open the wiki surface. All six documents appear in the declared order.
"""


def test_the_checkers_go_red_on_the_shapes_they_forbid():
    """MANDATORY NEGATIVE. Without it, both cases above pass identically against a checker that
    returns nothing — which is what they do today, the ceiling being ahead of every ticket."""
    assert bare_names(BARE_NAME), "a bare test name is not caught"
    assert not bare_names(BEHAVIOUR_OK), "a behaviour with evidence is wrongly flagged"
    assert unevidenced(NO_EVIDENCE), "a behaviour naming no case is not caught"
    assert not unevidenced(BEHAVIOUR_OK), "a behaviour with evidence is wrongly flagged"
    assert not unevidenced(BARE_NAME), "a bare test name names a case; the other rule owns it"


def test_a_pass_criterion_is_not_a_bare_test_name():
    """The rule `CLAUDE.md` states, made unable to drift the way its predecessor did."""
    offenders = [f"{tid}: {l[:88]}" for tid, _f, block in criteria_blocks()
                 for l in bare_names(block)]
    assert not offenders, (
        "these pass criteria are a bare test name and say nothing about what a person does or "
        "what the product then does:\n  " + "\n  ".join(offenders)
        + "\n\nWrite the behaviour, and name the case underneath as `Evidence:`.")


def test_a_pass_criterion_names_the_test_that_evidences_it():
    """A behaviour with no named case is prose, which is the failure the OLD rule existed for.
    Both halves are required; dropping either one is how this rule gets abused."""
    offenders = [f"{tid}: {l[:88]}" for tid, _f, block in criteria_blocks()
                 for l in unevidenced(block)]
    assert not offenders, (
        "these pass criteria state a behaviour and name no test to evidence it:\n  "
        + "\n  ".join(offenders)
        + "\n\nA criterion with no executable case is prose — the failure the old rule existed "
          "for, and it is not fixed by swapping one half for the other.")
