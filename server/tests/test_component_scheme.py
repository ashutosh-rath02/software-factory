"""The prefix IS the workspace, and the stages ARE the component's — `docs/technical/workflow/WORKFLOW.md`.

Written the day the migration landed, because the migration alone does not hold. The scheme it
implements had been stated in `WORKFLOW.md` line 1 — *one product, two components* — for as long
as the repository has existed, and 316 tickets contradicted it. Nothing could see that: the rule
lived in prose, and prose is what drifted.

No grandfather list. Every ticket is migrated, so each case below is green because the repository
is correct rather than because its debt is excused.
"""
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.repository

FACTORY = Path(__file__).resolve().parents[2]
TICKETS = FACTORY / "docs" / "tickets"
EPICS = FACTORY / "docs" / "epics"
MANIFEST = FACTORY / ".factory" / "factory.json"

# `docs/technical/workflow/WORKFLOW.md`'s table, executable. `FAC-` is the product level and owns no component.
PREFIX_WORKSPACE = {"SRV": "factory-server", "WEB": "factory-client"}

# The sequences `WORKFLOW.md` declares. The web LEADS with DESIGN — a screen implemented first has
# no specification to be checked against — and drops CONVENTION, which the client has no
# diff-reading checker for.
STAGES = {
    "factory-server": "SPEC TEST IMPL CONVENTION BUILD GATE REVIEW QA MERGE VERIFY",
    "factory-client": "DESIGN SPEC TEST IMPL BUILD GATE REVIEW QA MERGE VERIFY",
}


def test_qa_prompt_uses_this_repositorys_shared_workflow_and_lane_boundary():
    """The QA adapter must not send a role into retired project paths or a direct main push."""
    prompt = (FACTORY / ".factory/prompts/qa-ticket.md").read_text()
    workflow = FACTORY / "docs/technical/workflow/WORKFLOW.md"
    skill = FACTORY / ".claude/skills/qa-ticket/SKILL.md"
    assert workflow.is_file() and skill.is_file()
    assert "## Context and evidence" in workflow.read_text()
    assert "THE TOOL LANDS IT" in skill.read_text()
    assert "docs/technical/workflow/WORKFLOW.md" in prompt
    assert ".claude/skills/qa-ticket/SKILL.md" in prompt
    assert "§1" in prompt and "independent" in prompt.lower()
    assert "blocker" in prompt and "minor" in prompt
    assert "FACTORY_PROGRESS_URL" in prompt and "FACTORY_PROGRESS_TOKEN" in prompt
    assert "If both" in prompt and "are present" in prompt
    assert "operator" in prompt.lower() and "lane" in prompt.lower()
    assert "HEAD:main" in prompt and "never" in prompt.lower()
    assert "attribution" in prompt.lower()
    for stale in ("docs/TICKET-WORKFLOW.md", "factory/server", "FAC-* tickets only",
                  "Acme ticket", "claude-opus"):
        assert stale not in prompt, f"QA adapter still instructs from obsolete scope: {stale}"


def test_run_prompt_uses_shared_workflow_and_preserves_independent_qa():
    """A ticket role must not inherit old project paths or mark its own work passed."""
    prompt = (FACTORY / ".factory/prompts/run-ticket.md").read_text()
    workflow = FACTORY / "docs/technical/workflow/WORKFLOW.md"
    skill = FACTORY / ".claude/skills/run-ticket/SKILL.md"
    for source, reference in ((workflow, "docs/technical/workflow/WORKFLOW.md"),
                              (skill, ".claude/skills/run-ticket/SKILL.md")):
        assert source.is_file() and reference in prompt
    assert "## Context and evidence" in workflow.read_text()
    assert "§1" in prompt and "§2" in prompt
    for needed in ("CLAUDE.md", "docs/technical/MODEL.md", "docs/technical/ARCHITECTURE.md",
                   "SPEC", "RED", "GATE", "blocker", "minor", "awaiting-qa",
                   "FACTORY_PROGRESS_URL", "FACTORY_PROGRESS_TOKEN", "attribution"):
        assert needed in prompt
    assert "If both" in prompt and "are present" in prompt
    assert "independent QA" in prompt and "Only QA" in prompt
    for stale in ("docs/TICKET-WORKFLOW.md", "factory/server", "the project the ticket belongs to",
                  "already in your environment", "claude-opus"):
        assert stale not in prompt, f"run adapter still instructs from obsolete scope: {stale}"


def frontmatter(path):
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}
    head = text.split("\n---", 2)
    if len(head) < 2:
        return {}
    return {k: v.strip() for k, v in re.findall(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", head[0][4:], re.M)}


def tickets():
    for f in sorted(TICKETS.glob("*.md")):
        if f.name.startswith("_") or f.name == "README.md":
            continue
        keys = frontmatter(f)
        tid = keys.get("id", "")
        if tid and "NNN" not in tid:
            yield tid, f, keys


# ── 1 · the prefix is the workspace ──────────────────────────────────────────────────────────

def test_a_ticket_prefix_matches_its_workspace():
    """`SRV-FIX-350` and `SRV-FIX-354` were both filed `FAC-*` into `factory-server` on
    2026-09-03, by a session that had read the rule. Prose does not hold a convention."""
    wrong = []
    for tid, _f, keys in tickets():
        prefix = tid.split("-")[0]
        want = PREFIX_WORKSPACE.get(prefix)
        if want is None:
            wrong.append(f"{tid}: `{prefix}-` is not a component prefix — `FAC-` is epics only")
        elif keys.get("workspace") != want:
            wrong.append(f"{tid}: `{prefix}-` wants `{want}`, the file says "
                         f"`{keys.get('workspace')}`")
    assert not wrong, "these tickets' prefixes disagree with their workspaces:\n  " + "\n  ".join(wrong)


def test_har_is_the_epic_prefix_and_nothing_else():
    """The rule the case above is a consequence of, asserted alone so it cannot be lost with it."""
    strays = sorted(t for t, _f, _k in tickets() if t.startswith("FAC-"))
    assert not strays, f"`FAC-` is the product level and owns no code; these are tickets: {strays}"
    epics = sorted(frontmatter(f).get("id", "") for f in EPICS.glob("*.md")
                   if not f.name.startswith("_") and f.name != "README.md")
    assert all(e.startswith("FAC-EPIC-") for e in epics), (
        f"every epic must carry the `FAC-EPIC-` prefix: {epics}")


# ── 2 · a workspace that holds tickets can be built and gated ────────────────────────────────

def test_a_workspace_that_holds_tickets_declares_a_build_and_a_gate():
    """DERIVED FROM THE MANIFEST, so a fourth workspace added later cannot repeat this.

    `factory` was `{"path": ".", "toolchain": [...]}` — no `build`, no `gate` — and 44 of the 48
    tickets in it declared BUILD and GATE stages. Forty-four tickets graded on stages the manifest
    has no command for, and every gate in the repository said yes.
    """
    declared = json.loads(MANIFEST.read_text())["workspaces"]
    used = {keys.get("workspace") for _t, _f, keys in tickets()}
    naked = sorted(w for w in used if w in declared
                   and not ("build" in declared[w] and "gate" in declared[w]))
    assert not naked, (
        f"these workspaces hold tickets and declare no build or no gate, so a ticket in them "
        f"cannot run BUILD or GATE: {naked}. Either the workspace declares both commands, or it "
        f"holds no tickets and is a lane scope only.")
    undeclared = sorted(w for w in used if w not in declared)
    assert not undeclared, f"these tickets name workspaces the manifest does not declare: {undeclared}"


def test_no_ticket_declares_the_factory_workspace():
    """It is a lane scope: the whole repository checked out, with no build and no gate. Work that
    genuinely spans both components is an EPIC WITH CHILDREN — that is what the product level is
    for, and it is why there is no third prefix."""
    strays = sorted(t for t, _f, keys in tickets() if keys.get("workspace") == "factory")
    assert not strays, (
        f"these tickets sit in the `factory` workspace, which cannot be built or gated: {strays}")


# ── 3 · the stages are the component's ───────────────────────────────────────────────────────

def test_every_ticket_declares_its_components_stage_sequence():
    """283 tickets declared `… QA PR MERGE VERIFY` — a `PR` stage `CLAUDE.md` removed: *a passed
    ticket merges to `main` directly — no PR*. Nine matched the server's sequence and three the
    web's. `CLAUDE.md` names a surface disagreeing about stage totals as its own defect class, and
    it was live in 304 of 316 tickets."""
    wrong = []
    for tid, _f, keys in tickets():
        want = STAGES.get(keys.get("workspace"))
        if want is None:
            continue
        got = " ".join(keys.get("stages", "").split())
        if got != want:
            wrong.append(f"{tid} ({keys.get('workspace')}):\n      declares {got or '<nothing>'}"
                         f"\n      WORKFLOW.md {want}")
    assert not wrong, (
        f"{len(wrong)} tickets declare a stage sequence their component does not run:\n  "
        + "\n  ".join(wrong[:6]) + ("\n  …" if len(wrong) > 6 else ""))


def test_the_stage_table_here_is_the_one_workflow_md_declares():
    """MANDATORY NEGATIVE for the case above: it compares tickets against `STAGES`, so `STAGES`
    being a copy of whatever the tickets happen to say would make it unfalsifiable. The sequences
    are read back out of `WORKFLOW.md`."""
    doc = (FACTORY / "docs/technical/workflow/WORKFLOW.md").read_text()
    for ws, seq in STAGES.items():
        dotted = " · ".join(seq.split())
        assert dotted in doc, (
            f"`WORKFLOW.md` does not declare the sequence this file uses for {ws}:\n"
            f"  expected the line: {dotted}\n"
            f"If the workflow changed, the workflow is the source and this table follows it.")


# ── 4 · the documents that INSTRUCT a prefix, and the tool that hands one out ─────────────────

# THE CORPUS IS DERIVED, AND A FILE IS IN IT UNLESS IT IS DEFINITIONALLY HISTORICAL.
#
# This was a hand-written list of five paths, and it omitted `CLAUDE.md` — the most-read document
# in the repository and the one that literally tells an agent how to ask for an id. The regex was
# right; the corpus was mine. That is round two of the identical mistake: round one renamed every
# citation and missed the instructions, round two fixed the instructions I could think of and then
# checked the paths I could think of. Both times the SET came from memory, and adding `CLAUDE.md`
# to a list would be round three.
#
# So the default inverts. Every tracked text file is normative unless it belongs to a category
# that is a RECORD rather than an instruction: a report is what a session did, and a ticket or an
# epic is a statement about one piece of work. Neither is a sentence an agent copies a prefix from.
# A new document, skill or script is therefore governed the day it is written, with nobody
# remembering to add it.
# A document either INSTRUCTS or it RECORDS. What instructs is read to decide what to type next:
# `CLAUDE.md`, the skills, the scripts, the templates, the specification. What records is read to
# learn what happened: a report, a ticket, an epic, the escape ledger, the design notes behind a
# module, and a test's own docstring quoting the defect it exists for. A record quotes the wrong
# thing ON PURPOSE, verbatim, and rewriting it to satisfy a grep would destroy the evidence.
#
# The line is drawn by CATEGORY and not by filename, so a new skill, script or template is governed
# the day it is written. That is the property the five-path list did not have.
RECORDS = ("docs/reports/", "docs/tickets/", "docs/epics/", "docs/archive/", "docs/notes/",
           "docs/operations/ESCAPES.md",
           "server/design/", "server/tests/", "client/web/src/", "client/web/tests/")
# AND A CHECKOUT OF THIS REPOSITORY IS A COPY OF IT, NOT A DOCUMENT IN IT.
# `<root>/.factory/worktrees/<lane>` is where the product puts a lane — `app/manifest/store.cpp`,
# FACTORY_DIR/WORKTREES_DIR — so every file beneath it is this same tree at some other commit, and
# `rel` there begins eleven directories above the categories below, which is why none of them fires.
# The walk therefore read another branch's `docs/reports/` as normative instruction and went red
# whenever a lane was RUNNING, which is the tool doing its job (`SRV-FIX-385`).
# ONE RULE, not eight more names in `SKIP_DIR`: the exclusion is the DIRECTORY THE PRODUCT OWNS, so
# a lane called anything, at any depth beneath it, is covered the day it is claimed.
# `.worktrees/` is the same thing made by hand: where a session puts a ticket's git worktree when
# no lane holds it, so it is another branch's copy of this tree and not this tree (SRV-FIX-600).
COPIES = (".factory/worktrees/", ".factory/temp/", ".worktrees/")
CORPUS_SUFFIX = (".md", ".sh", ".py", ".json", ".ts", ".tsx", ".mjs")
SKIP_DIR = {"node_modules", ".git", ".cache", ".cmake_build", "dist", "__pycache__", ".pytest_cache",
            "fixture-repo", "shots", "baselines"}


def normative_documents():
    """Every file that could TELL an agent which prefix to use. Derived from the tree."""
    for f in sorted(FACTORY.rglob("*")):
        if not f.is_file() or f.suffix not in CORPUS_SUFFIX:
            continue
        if SKIP_DIR & set(f.parts):
            continue
        rel = f.relative_to(FACTORY).as_posix()
        if rel.startswith(COPIES):
            continue
        # a TEMPLATE inside a historical directory is normative — it is the form people copy
        if rel.startswith(RECORDS) and not f.name.startswith("_"):
            continue
        yield f, rel


def test_normative_documents_excludes_runtime_docgate_copies(tmp_path, monkeypatch):
    monkeypatch.setattr("test_component_scheme.FACTORY", tmp_path)
    for rel in ("CLAUDE.md", ".factory/factory.json",
                ".factory/worktrees/lane/01/CLAUDE.md",
                ".factory/temp/docgate-01/CLAUDE.md",
                ".cache/server/build/CLAUDE.md"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("instruction\n")

    found = {rel for _path, rel in normative_documents()}
    assert {"CLAUDE.md", ".factory/factory.json"} <= found
    assert ".factory/worktrees/lane/01/CLAUDE.md" not in found
    assert ".factory/temp/docgate-01/CLAUDE.md" not in found
    assert ".cache/server/build/CLAUDE.md" not in found


# `SRV-FIX-351` in a sentence about what happened is a CITATION and stays true forever. What may
# not survive is a TEMPLATE — an id with a placeholder number, or a bare prefix being handed to the
# allocator — because that is the sentence an agent copies.
# TWO SIGNALS, both unambiguous, and the bare backticked prefix is deliberately NOT one.
# `SPEC.md` and `server/design/ticket-ids.md` describe the id FORMAT — "`FAC-FEAT`, `FAC-FIX` and
# `FAC-REF` draw from one series" is a true sentence about how the counter works, not a sentence
# an agent copies. Over a five-path list that alternative cost nothing; over the whole repository
# it produces eleven false positives against correct specification text, and a check that cries
# wolf against the spec is one somebody will delete.
#
# WHAT THIS DOES NOT CATCH, stated rather than discovered: prose that names a retired prefix
# without a placeholder number and without handing it to the allocator. `SRV-FIX-371` is where the
# tool-side half lives — a prefix the scheme forbids should be refused BY THE TOOL, which is the
# only check that does not depend on reading every sentence.
INSTRUCTS = re.compile(r"FAC-(?:FIX|FEAT|REF)-(?:NNN|0NN|N)\b|"
                       r"\"prefix\"\s*:\s*\"FAC-(?:FIX|FEAT|REF)\"")


def test_the_corpus_stops_at_the_repository_it_is_walking():
    """A LANE CHECKOUT IS A COPY OF THE REPOSITORY, NOT A DOCUMENT IN IT.

    `.factory/worktrees/<lane>` is where the product puts a lane, so the walk descending into one
    made this gate red whenever a lane was RUNNING — the normal state of the tool doing its job.
    `WEB-FIX-334` measured thirteen offenders on 2026-09-04 and every one of them was another
    branch's `docs/reports/`, read as normative instruction eleven directories below the copy of
    itself the walk already excludes.

    The checkout is built with `git archive HEAD`, which is this repository's own content at the
    commit a lane is cut from. It is not `git worktree add`: a lane's parent git directory is
    mounted read-only, so the command the product uses cannot run from inside one, and the walk
    under test reads a directory tree rather than git's opinion of it.
    """
    worktrees = FACTORY / ".factory" / "worktrees"
    existed = worktrees.is_dir()
    before = {rel for _f, rel in normative_documents()}

    lane = None
    for n in range(99, 89, -1):
        candidate = worktrees / "lane" / f"{n}"
        try:
            candidate.mkdir(parents=True)
        except FileExistsError:
            continue
        lane = candidate
        break
    assert lane is not None, (
        f"every `{worktrees}/lane/NN` from 99 down to 90 is taken, and this case claims one of "
        f"them rather than a real lane's — release a lane through the tool, never by hand")

    scratch = Path(tempfile.mkdtemp())
    tar = scratch / "checkout.tar"
    try:
        subprocess.run(["git", "archive", "--format=tar", "-o", str(tar), "HEAD"],
                       cwd=FACTORY, check=True)
        with tarfile.open(tar) as archive:
            archive.extractall(lane)

        planted = sorted(f.relative_to(FACTORY).as_posix() for f in lane.rglob("*")
                         if f.is_file() and f.suffix in CORPUS_SUFFIX
                         and not SKIP_DIR & set(f.parts)
                         and INSTRUCTS.search(f.read_text(errors="ignore")))
        assert planted, (
            f"`{lane}` holds no document this walk could mistake for an instruction, so this case "
            f"would pass over an empty sweep. The checkout is the repository at HEAD; if HEAD "
            f"really carries no such line, plant one rather than delete this assertion.")

        after = {rel for _f, rel in normative_documents()}
    finally:
        shutil.rmtree(lane, ignore_errors=True)
        try:
            lane.parent.rmdir()
        except OSError:
            pass
        if not existed:
            shutil.rmtree(worktrees, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)

    leaked = sorted(after - before)
    lost = sorted(before - after)
    assert after == before, (
        f"a lane checkout on disk changed what this gate reads.\n"
        f"  {len(planted)} of its files carry a line the walk calls an instruction, "
        f"e.g. {planted[0]}\n"
        f"  {len(leaked)} entered the corpus: {leaked[:5]}\n"
        f"  {len(lost)} left it: {lost[:5]}")


def test_the_corpus_reaches_the_documents_an_agent_actually_reads():
    """MANDATORY NEGATIVE for the case below: it is only as good as the set it walks, and the set
    it walked before was five paths that omitted the repository's own CLAUDE.md."""
    rels = {rel for _f, rel in normative_documents()}
    # `.factory/factory.json` is here because `COPIES` excludes a directory INSIDE `.factory/`:
    # without it this case cannot tell "the walk stops at a lane checkout" from "the walk stops at
    # `.factory/`", and the manifest is the one document of this repository that lives under it.
    for must in ("CLAUDE.md", ".claude/scripts/api.sh", ".claude/skills/new-ticket/SKILL.md",
                 "docs/tickets/_TEMPLATE.md", "docs/epics/_TEMPLATE.md", "docs/technical/workflow/WORKFLOW.md",
                 "docs/product/SPEC.md", ".factory/factory.json"):
        # NOT `server/design/ticket-ids.md`: it explains how the counter works to somebody changing
        # the id code, which is a record of reasoning and not a sentence an agent copies when
        # filing. Listing it here was this file contradicting the principle it had just written.
        assert must in rels, f"the corpus does not reach `{must}`, which instructs an agent"
    for records in ("docs/reports/", "docs/operations/ESCAPES.md", "server/design/"):
        assert not any(r.startswith(records) and not r.split("/")[-1].startswith("_")
                       for r in rels), f"`{records}` records what happened; it does not instruct"
    assert len(rels) > 50, f"the corpus collapsed to {len(rels)} files"


def test_no_document_instructs_an_agent_to_use_a_har_ticket_prefix():
    """The round that renamed 2,203 files rewrote every CITATION of a `FAC-` id and left the four
    lines that INSTRUCT one. `docs/epics/_TEMPLATE.md`'s children table told an epic author to name
    a `factory-server` child `FAC-FEAT-0NN` — the exact pairing criterion 1 exists to refuse.

    Blocker rather than cosmetic because the id is the COMMIT PREFIX: a ticket run under a
    forbidden id can be repaired only by shipping it or by renumbering, and renumbering severs
    ticket ↔ commits ↔ report permanently. (SRV-FIX-368 QA, F1.)
    """
    offenders = []
    for path, rel in normative_documents():
        try:
            lines = path.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(lines, 1):
            if INSTRUCTS.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:78]}")
    assert not offenders, (
        "these lines tell an agent to use a `FAC-` prefix for a TICKET, and `FAC-` is epics only:\n  "
        + "\n  ".join(offenders))


def test_the_epic_prefix_is_the_one_the_tool_will_allocate():
    """The inverse held: `FAC-FIX` was granted and `FAC-EPIC` refused — you could not file an epic.

    `surveyIds()` reads the TICKETS directory and every git ref's copy of it, and never the epics
    directory, so the one prefix the scheme reserves was the one prefix with no series. Asserted
    against the store rather than over HTTP so it cannot pass by nobody running a server.
    """
    ids = {frontmatter(f).get("id", "") for f in EPICS.glob("*.md") if not f.name.startswith("_")}
    prefixes = {i.rsplit("-", 1)[0] for i in ids if i}
    assert prefixes <= {"FAC-EPIC"}, f"epics carry prefixes other than FAC-EPIC: {sorted(prefixes)}"
    # The BODY of surveyIds, not the file: `requirements()` appears elsewhere in store.cpp, so a
    # whole-file grep passes while the survey still reads tickets only — which is how this
    # assertion first went green against the very defect it is for.
    text = (FACTORY / "server" / "app" / "catalog" / "store.cpp").read_text()
    start = text.index("catalog::Store::IdSurvey catalog::Store::surveyIds() const")
    body = text[start:text.index("\n}\n", start)]
    assert "requirements().documents" in body, (
        "`surveyIds()` does not read the epics directory, so `FAC-EPIC` has no series and "
        "`POST /api/tickets/ids '{\"prefix\":\"FAC-EPIC\"}'` answers 422 — the one prefix the "
        "scheme reserves is the one the tool refuses to allocate.")


# ── SRV-FIX-453 · one authority on whether a lane is live ────────────────────────────────────

# `MODEL.md` §2 `S5`: *whether a lane is live is answered by `GET /api/lanes` and by nothing
# else*. A module that answers it for itself by reading the process table is a second authority
# on the same fact, and the two disagreed on the same screen for a month — a lane
# `running · producing` beside a badge reading `unshipped`, and a crashed lane beside `running`.
#
# Two sites in `server/app/` may open `/proc`, and each is a DIFFERENT question with a reason:
PROC_READERS_ALLOWED = {
    "app/agent/proc.cpp":
        "the owner of every pid the tool spawns — `/proc` is where it reaps and signals them, "
        "and this is the module the rest of the tree asks instead of asking the kernel",
    "app/lane/store.cpp":
        "`holderOf` answers WHICH PID HOLDS THIS DEVICE FD, which is `S5`'s resource half and "
        "not its liveness half; it reads `/proc/*/fd` and forms no opinion about a lane's state",
}

# Not every mention of the string is a read. Excluded by CATEGORY, with a reason each, rather
# than by deleting rows until the sweep passes:
#
#   · `"--proc"` — a sandbox that BINDS `/proc` into a lane opens nothing and reads nothing;
#   · `#include <app/agent/proc.h>` — a module ASKING the owner instead of asking the kernel,
#     which is the one direction this ticket leaves open;
#   · the confinement shadow-prefix list — it compares a path against `/proc` but reads no
#     process table and grants no access to one;
#   · a comment is not code.
PROC_NOT_A_READ = re.compile(
    r'"--proc"|#\s*include\s*<app/agent/proc\.h>'
    r'|^\s*"/tmp",\s*"/proc",\s*"/dev",\s*AGENT_BIN_INSIDE,'
)


def test_only_the_process_owner_and_the_device_holder_read_proc():
    """No third module decides a fact from the process table.

    Derived from the tree, never from a list of files: a fourth module reading `/proc` is a
    module answering `S5`'s question for itself, and it goes red here before it ships.
    """
    app = FACTORY / "server" / "app"
    sources = sorted(app.rglob("*.cpp")) + sorted(app.rglob("*.h"))
    assert len(sources) > 20, (
        f"the sweep over {app} found {len(sources)} sources, so it examined nothing — a check "
        f"that sweeps nothing has not run")

    offenders = []
    for f in sources:
        rel = f.relative_to(FACTORY / "server").as_posix()
        if rel in PROC_READERS_ALLOWED:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if "/proc" not in line or PROC_NOT_A_READ.search(line):
                continue
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            offenders.append(f"{rel}:{n}: {line.strip()[:90]}")

    assert not offenders, (
        "these sites read the process table, and only "
        + " and ".join(sorted(PROC_READERS_ALLOWED))
        + " may. `S5` gives that fact to `GET /api/lanes`; a module deciding it from a proxy is "
          "the class SRV-FIX-125, SRV-FIX-128 and SRV-FIX-453 each closed one instance of:\n  "
        + "\n  ".join(offenders))

    for site, why in PROC_READERS_ALLOWED.items():
        path = FACTORY / "server" / site
        assert path.exists(), f"the allowed site {site} is gone; the reason recorded is: {why}"
        assert "/proc" in path.read_text(), (
            f"{site} no longer reads /proc, so it is an excuse rather than an exception — "
            f"delete the row. Its reason was: {why}")
