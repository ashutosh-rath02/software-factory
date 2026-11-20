"""WEB-FEAT-173 — §11 check 12, executed.

`SPEC.md` §11 is a list of checks about `SPEC.md`. Ten of them are shell one-liners printed in the
document itself and run by whoever is reading it. Check 12 cannot be one of those: its subject is
**the server's source**, it has to be re-derived every time a refusal is added, and a check nobody
runs is a rule nobody keeps. So it lives here, in the suite that runs on every ticket.

    cd factory/server && pytest tests/test_spec.py -v

The rule, from `FAC-EPIC-006`:

  > Every state the tool can put on a screen carries an action that ends it. A refusal names what
  > to do *and offers it*. No failure's only remedy is a shell, and no failure's only remedy
  > destroys work.

Nothing here reads a hand-kept list of error codes. The rows come from `app/`, the same way
`test_agent_trust.py` re-derives its rows from captures — a table somebody maintains beside the
code is a table that goes stale in the direction that looks like the product working.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# SRV-FIX-342 drives `E31`'s declared refusals against a live server, and `SRV-FEAT-315` already
# owns the repository that has runs in it. Its planters are the fixture, not the subject.
from test_runs_index import ROLES_A, plant_repo, plant_run  # noqa: E402

SERVER = Path(__file__).resolve().parents[1]
FACTORY = SERVER.parent
APP = SERVER / "app"
SPEC = FACTORY / "docs/product/SPEC.md"
SCHEMA = FACTORY / "docs/technical/contracts/http.schema.json"
API_TYPES = FACTORY / "client" / "web" / "src" / "types" / "api.ts"
MANIFEST_SCHEMA = FACTORY / "docs/technical/contracts/manifest.schema.json"


def test_declared_command_profile_preparation_has_one_owner():
    """SRV-FIX-559: shared launch preparation, not a second command-specific seed."""
    callers = {str(path.relative_to(APP)) for path in APP.rglob('*.cpp')
               if path != APP / 'agent/launch.cpp'
               and re.search(r'\bconfigSeed\s*\(', path.read_text())}
    assert callers == {'api/manager.cpp', 'api/_recover-lane.cpp'}, callers
    lane = (APP / 'lane/store.cpp').read_text()
    spawning = lane.split('void lane::Store::startProcess(', 1)[1]
    assert 'prepareConfig_' in spawning
    assert 'configForWorktree' not in lane
    # Existing stage-history loading is outside this ticket. Configuration preparation
    # may not add a lane-to-agent dependency beside that pre-existing history reader.
    agent_headers = set(re.findall(r'#include\s+[<"](app/agent/[^>"]+)', lane))
    assert agent_headers == {'app/agent/history.h'}, agent_headers
    assert 'configSeed(' not in lane
    manager = (APP / 'api/manager.cpp').read_text()
    assert 'onPrepareConfig(' in manager
    design = (SERVER / 'design/agent-session.md').read_text()
    assert 'Cold declared commands' in design
    assert 'default profile' in design and 'configSeed' in design


# ── the source, which is the only place the codes come from ──────────────────────────────────

# An assignment to a member named `errorCode`. `(?!=)` keeps `errorCode == "not_found"` out: a
# comparison is a read of a code, not a site that can emit one, and counting it would put codes
# in the table that the file it was found in never produces.
ASSIGN = re.compile(r'errorCode\s*=(?!=)\s*([^;]*);', re.S)
# A literal error body, raw (`R"({"error":{"code":"x"`) or escaped (`"{\"error\":{\"code\":\"x\"`).
ENVELOPE = re.compile(r'\\?"error\\?"\s*:\s*\{\s*\\?"code\\?"\s*:\s*\\?"([a-z_]+)\\?"')
LITERAL = re.compile(r'"([a-z_]+)"')
FACTORY = re.compile(
    r'^[ \t]*(?:[A-Za-z_][\w:<>,]*[ \t*&]+)+'
    r'([A-Za-z_][\w:]*)\s*\(((?:[^()]|\([^()]*\))*)\)\s*\{(.*?)^\}', re.S | re.M)
ENVELOPE_CODE = re.compile(
    r'\\?"error\\?"\s*:\s*\{\s*\\?"code\\?"\s*:\s*"\s*(?:\+|<<)\s*'
    r'(?:[A-Za-z_]\w*::)?jstr\s*\(\s*(\w+)\s*\)')


def code_factories(sources):
    found = set()
    for path, text in sources:
        for factory in FACTORY.finditer(text):
            name, arguments, body = factory.groups()
            emitted = ENVELOPE_CODE.search(body)
            if not emitted:
                continue
            for position, parameter in enumerate(arguments.split(',')):
                code = re.fullmatch(r'\s*const\s+std::string\s*&\s*(\w+)\s*', parameter)
                if code and code[1] == emitted[1]:
                    found.add((path, name, position))
                    break
    return found


def error_codes():
    """Every error code `factory/server/app` can put in an error envelope, with its sites.

    Deliberately NOT every `{"code":…}` in the tree: `path_missing` and the `warnings[]` copy of
    `invalid_manifest` are notices about the repository — `http.schema.json` says so in as many
    words — and a notice is not a state anybody is stuck in.
    """
    found = {}
    sources = [(path, path.read_text()) for path in sorted(APP.rglob('*'))
               if path.suffix in ('.cpp', '.h')]
    factories = code_factories(sources)
    for path, text in sources:
        for match in ASSIGN.finditer(text):
            line = text[:match.start()].count("\n") + 1
            for code in LITERAL.findall(match.group(1)):
                found.setdefault(code, []).append(f"{path.relative_to(SERVER)}:{line}")
        for match in ENVELOPE.finditer(text):
            line = text[:match.start()].count("\n") + 1
            found.setdefault(match.group(1), []).append(f"{path.relative_to(SERVER)}:{line}")
        functions = list(FACTORY.finditer(text))
        for defined_at, name, position in sorted(factories):
            names = {name, name.split('::')[-1]}
            for called_as in sorted(names):
                call = re.compile(r'(?<![\w:])' + re.escape(called_as) + r'\s*\(\s*'
                                  + r'(?:[^,()]+,\s*)' * position + r'"([a-z_]+)"')
                for match in call.finditer(text):
                    if called_as == name.split('::')[-1]:
                        owner = name.rsplit('::', 1)[0] if '::' in name else ''
                        callers = [function[1] for function in functions
                                   if function.start() <= match.start() < function.end()]
                        if owner and not any(caller.startswith(owner + '::') for caller in callers):
                            continue
                        if not owner and path != defined_at:
                            continue
                    line = text[:match.start()].count('\n') + 1
                    site = f'{path.relative_to(SERVER)}:{line}'
                    sites = found.setdefault(match[1], [])
                    if site not in sites:
                        sites.append(site)
    return found


# ── the document ─────────────────────────────────────────────────────────────────────────────

REMEDIES_OPEN = "<!-- CHECK12-REMEDIES -->"
EXEMPTIONS_OPEN = "<!-- CHECK12-EXEMPTIONS -->"
SITE_EXEMPTIONS_OPEN = "<!-- CHECK12-SITE-EXEMPTIONS -->"
CLOSE = "<!-- END -->"
VERBS_OPEN = "<!-- REMEDY-VERBS -->"
NOUNS_OPEN = "<!-- DESTROYS-NOUNS -->"

# `` `verb` → `["a","b"]` ``, the one shape a remedy is written in. A cell the parser cannot read
# is a remedy that does not exist — which is the point: check 12 is machine-read or it is prose.
REMEDY = re.compile(r'`([a-z_]+)`\s*→\s*`(\[[^`]*\])`')


def rows(text, opener):
    """The `| a | b |` rows between an anchor comment and its `<!-- END -->`."""
    start = text.find(opener)
    if start < 0:
        return None
    end = text.find(CLOSE, start)
    block = text[start:end if end > 0 else len(text)]
    out = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0].strip("` ") in ("code", "action", "noun"):
            continue                                    # the header
        out.append(cells)
    return out


def bare(cell):
    return cell.strip().strip("`").strip()


def remedy_table(text):
    """code → [(action, destroys)]. A row whose cell parses to nothing lands as an empty list."""
    table = rows(text, REMEDIES_OPEN)
    if table is None:
        return None
    out = {}
    for cells in table:
        code = bare(cells[0])
        parsed = []
        for action, destroys in REMEDY.findall(cells[1] if len(cells) > 1 else ""):
            try:
                nouns = json.loads(destroys)
            except json.JSONDecodeError:
                nouns = None
            parsed.append((action, nouns))
        out[code] = parsed
    return out


def exemption_table(text):
    """code → reason, verbatim. A missing second cell is an empty reason, not an absent row."""
    table = rows(text, EXEMPTIONS_OPEN)
    if table is None:
        return None
    return {bare(cells[0]): (cells[1].strip() if len(cells) > 1 else "") for cells in table}


def site_exemption_table(text):
    """`(code, site)` → reason, for the one endpoint of a code that is remedied elsewhere.

    `SRV-FIX-343`: a remedy is a property of a SITE. `not_found` is a reload away from ending at
    `E10` and a dead end at `E8`, because the tool does not write a run's log. A table keyed by
    code cannot say that, and the cheap alternatives both lose something real — exempting the
    code makes every other `not_found` a dead end, and remedying the site puts a button on the
    screen whose only outcome is the same `404`.
    """
    table = rows(text, SITE_EXEMPTIONS_OPEN)
    if table is None:
        return None
    return {(bare(cells[0]), bare(cells[1]) if len(cells) > 1 else ""):
            (cells[2].strip() if len(cells) > 2 else "") for cells in table}


def verb_set(text):
    """§9's closed set: action → the row, so a complaint can quote what the document promised."""
    table = rows(text, VERBS_OPEN)
    return None if table is None else {bare(cells[0]): cells for cells in table}


def noun_set(text):
    table = rows(text, NOUNS_OPEN)
    return None if table is None else {bare(cells[0]) for cells in table}


# ── the check itself, as a function, so the negative can run it against a mutated document ────

# A reason has to be a sentence, not a shrug. Four words is the shortest thing in the exemption
# table that says anything ("the request is gone") and the bar is deliberately low: the assertion
# this number backs is about a reason being ABSENT, and a low bar keeps it about that.
MIN_REASON_WORDS = 4


def audit(spec_text, codes):
    """Every complaint check 12 has against `spec_text`, given the codes the source emits.

    Returns a list of sentences. Empty is green. It is a function rather than a run of asserts so
    that `test_an_exemption_without_a_reason_is_not_an_exemption` can hand it a document nobody
    would commit and read what it says — a checker that can only be run against the tree it was
    written for is a checker nobody has ever seen say no.
    """
    complaints = []
    remedies = remedy_table(spec_text)
    exemptions = exemption_table(spec_text)
    sites = site_exemption_table(spec_text)
    verbs = verb_set(spec_text)
    nouns = noun_set(spec_text)

    for name, table, anchor in (("remedy", remedies, REMEDIES_OPEN),
                                ("exemption", exemptions, EXEMPTIONS_OPEN),
                                ("site-exemption", sites, SITE_EXEMPTIONS_OPEN),
                                ("verb", verbs, VERBS_OPEN),
                                ("destroys-noun", nouns, NOUNS_OPEN)):
        if table is None:
            complaints.append(f"SPEC.md carries no {name} table — {anchor} is not in the document")
    # A missing table is a complaint, not a stop: the useful red is the one that NAMES the codes
    # with no way out, and returning early here would answer "the document has no table" to a
    # question about thirty-four states a person can be stuck in.
    remedies = remedies if remedies is not None else {}
    exemptions = exemptions if exemptions is not None else {}
    sites = sites if sites is not None else {}
    verbs = verbs if verbs is not None else {}
    nouns = nouns if nouns is not None else set()

    both = sorted(set(remedies) & set(exemptions))
    for code in both:
        complaints.append(
            f"`{code}` is in the remedy table AND the exemption table; a code is exempt or it is "
            f"remedied, and a code that is both is a code the check says nothing about")

    for code in sorted(codes):
        if code in remedies:
            if not remedies[code]:
                complaints.append(
                    f"`{code}` has a remedy row that names no remedy — the cell must read "
                    f"`action` → `[…]`, and it reads {remedies[code]!r}")
            continue
        if code in exemptions:
            reason = exemptions[code]
            if len(reason.split()) < MIN_REASON_WORDS:
                complaints.append(
                    f"`{code}` is exempt with no reason ({reason!r}). An exemption without a "
                    f"reason is not an exemption — it is the cheap way to make check 12 green")
            continue
        complaints.append(
            f"`{code}` is emitted by the server and is in neither table: it names no remedy and "
            f"no reasoned exemption. Sites: {', '.join(codes[code][:3])}")

    for code in sorted(set(remedies) | set(exemptions) | {c for c, _ in sites}):
        if code not in codes:
            complaints.append(
                f"`{code}` is in §11's check 12 and `app/` no longer emits it — the table has "
                f"drifted from the source it is derived from")

    # The site exemption table, and the four things that make a row legal. It is the cheapest
    # row in this document to abuse — one endpoint, one null, nobody notices — so each rule
    # below is the one that stops a different cheap way of writing it.
    specified = {eid for eid, *_ in endpoint_blocks(spec_text)}
    for (code, site), reason in sorted(sites.items()):
        if code in exemptions:
            complaints.append(
                f"`{code}` is exempt for every site AND exempt at `{site}`; a code is exempt "
                f"once, and saying it twice is a code check 12 says nothing about")
        elif code not in remedies:
            complaints.append(
                f"`{code}` is exempt at `{site}` and named in no remedy table row. A site "
                f"exemption is for a code that stays remediable somewhere else — a code that is "
                f"nowhere remediable belongs in the exemption table, with its reason")
        if not site:
            complaints.append(
                f"`{code}` is exempt at no site at all. A site exemption with no site is a "
                f"code-level exemption written where nothing will check it")
        elif specified and site not in specified:
            complaints.append(
                f"`{code}` is exempt at `{site}`, which §5 specifies no endpoint block for. A "
                f"site nobody can open and read is a site nobody can disagree with")
        if len(reason.split()) < MIN_REASON_WORDS:
            complaints.append(
                f"`{code}` at `{site}` is exempt with no reason ({reason!r}). An exemption "
                f"without a reason is not an exemption — it is the cheap way to make check 12 "
                f"green, one endpoint at a time")

    for code, offered in sorted(remedies.items()):
        for action, destroys in offered:
            if action not in verbs:
                complaints.append(
                    f"`{code}` offers `{action}`, which §9's closed verb set does not name. A "
                    f"server that invents a verb the client does not know is a schema failure")
            if destroys is None:
                complaints.append(f"`{code}`'s `{action}` has a `destroys` that is not a JSON array")
                continue
            for noun in destroys:
                if noun not in nouns:
                    complaints.append(
                        f"`{code}`'s `{action}` says it destroys {noun!r}, which is not a noun §9 "
                        f"declares. `destroys` is rendered and compared, not free prose")
    return complaints


def spec():
    return SPEC.read_text()


# ── 1 · every code names a remedy or a reasoned exemption ────────────────────────────────────

def test_every_error_code_names_a_remedy_or_a_reasoned_exemption():
    """`FAC-EPIC-006`'s rule, held by a test instead of by a sentence.

    `lane/03` sat for thirteen hours in a state the screen described perfectly and could not end.
    Every fact on that screen was true; not one of them could be acted on. This is the assertion
    that stops the thirty-fifth error code shipping as the next dead end.
    """
    codes = error_codes()
    assert len(codes) > 25, (
        f"only {len(codes)} error codes found in {APP} — the enumeration has stopped seeing the "
        f"source, and a check 12 with no rows passes for the wrong reason: {sorted(codes)}")
    complaints = audit(spec(), codes)
    assert not complaints, (
        f"check 12 has {len(complaints)} complaints against the {len(codes)} error codes "
        f"`app/` emits:\n  " + "\n  ".join(complaints))


# ── 2 · no code's only way out destroys work ─────────────────────────────────────────────────

def test_every_code_has_a_remedy_that_destroys_nothing():
    """*No failure's only remedy destroys work.*

    The screen's one answer to a dead credential was **Release**, and release calls `remove_all`
    on the directory holding 3.6 MB of the session's memory. The cure and the amnesia were the
    same call. `destroys: []` is what makes that sentence checkable.
    """
    remedies = remedy_table(spec())
    assert remedies, "SPEC.md carries no check 12 remedy table"
    destructive = {
        code: [f"`{action}` destroys {destroys}" for action, destroys in offered]
        for code, offered in remedies.items()
        if not any(destroys == [] for _, destroys in offered)
    }
    assert not destructive, (
        "every offered way out of these states destroys work; a person following the screen "
        "loses something to fix something else:\n  "
        + "\n  ".join(f"{code}: {', '.join(what)}" for code, what in sorted(destructive.items())))


# ── 3 · the verb set is closed, and it is generated ──────────────────────────────────────────

def test_the_verb_set_is_closed_and_generated():
    """A verb is a wire word, and every wire word in this repository is generated, never typed.

    Three sets have to be one set: §9's table, the `action` enum in `http.schema.json`, and the
    literals in the client's generated types. Two of them agreeing is how a blank button ships.
    """
    verbs = verb_set(spec())
    assert verbs, "SPEC.md §9 declares no remedy verb set"

    schema = json.loads(SCHEMA.read_text())
    declared = schema["$defs"]["remedy"]["properties"]["action"]["enum"]
    assert sorted(declared) == sorted(verbs), (
        f"http.schema.json's `action` enum and SPEC.md §9's verb set disagree.\n"
        f"  in the schema, not in §9: {sorted(set(declared) - set(verbs))}\n"
        f"  in §9, not in the schema: {sorted(set(verbs) - set(declared))}")

    nouns = noun_set(spec())
    declared_nouns = schema["$defs"]["remedy"]["properties"]["destroys"]["items"]["enum"]
    assert sorted(declared_nouns) == sorted(nouns), (
        f"http.schema.json's `destroys` nouns and SPEC.md §9's disagree.\n"
        f"  in the schema, not in §9: {sorted(set(declared_nouns) - set(nouns))}\n"
        f"  in §9, not in the schema: {sorted(set(nouns) - set(declared_nouns))}")

    # ...and the generated file. `npm run gen:api-types` is the only thing allowed to write it,
    # `tests/api-types.test.ts` fails on drift and the browser gate validates the real server
    # against the same schema — so a verb missing HERE means the schema was edited and the
    # generator was never run, and the client cannot see the verb at all.
    assert API_TYPES.exists(), f"{API_TYPES} is missing — run `npm run gen:api-types`"
    generated = API_TYPES.read_text()
    missing = [verb for verb in sorted(verbs) if f"'{verb}'" not in generated]
    assert not missing, (
        f"{len(missing)} verbs §9 declares are not in the client's generated API types: "
        f"{missing}. Run `cd factory/client/web && npm run gen:api-types`")
    union = _action_union(generated)
    assert union == set(verbs), (
        f"the generated `action` union and SPEC.md \u00a79's verb set are not the same set.\n"
        f"  generated, not in \u00a79: {sorted(union - set(verbs))}\n"
        f"  \u00a79, not generated : {sorted(set(verbs) - union)}")


def _action_union(generated):
    """The literals of the generated `action:` union, and nothing else in the file."""
    match = re.search(r"^\s*action\??:\s*(.+)$", generated, re.M)
    return set(re.findall(r"'([a-z_]+)'", match.group(1))) if match else set()


# ── 4 · negative — an exemption without a reason is not an exemption ─────────────────────────

def test_an_exemption_without_a_reason_is_not_an_exemption():
    """The cheap way to make check 12 green is to exempt everything. This refuses it.

    Two shapes, because they fail differently: a code with an EMPTY reason cell, and a code with
    no second cell at all. Both are run against a document assembled here — the real §11 with one
    code moved out of the remedy table and into the exemption table without its sentence.
    """
    codes = error_codes()
    text = spec()
    assert not audit(text, codes), "the document is not green, so this negative proves nothing"

    victim = sorted(remedy_table(text))[0]
    for shape, row in (("an empty reason", f"| `{victim}` |  |"),
                       ("no reason cell at all", f"| `{victim}` |")):
        mutated = _move_to_exemptions(text, victim, row)
        complaints = audit(mutated, codes)
        assert any(victim in c and "exemption" in c for c in complaints), (
            f"a code exempted with {shape} was accepted by check 12. Complaints were: {complaints}")

    # And the same document with the sentence restored is green again — otherwise the case above
    # proves only that the mutation broke the table, not that the REASON is what carries it.
    with_reason = _move_to_exemptions(
        text, victim,
        f"| `{victim}` | there is nothing the tool could offer that would end this state |")
    assert not audit(with_reason, codes), (
        f"the same move with a reason sentence is refused too, so check 12 is rejecting the move "
        f"and not the missing reason: {audit(with_reason, codes)}")


def _move_to_exemptions(text, code, row):
    """The real document with `code` taken out of the remedy table and added to the exemptions."""
    without = re.sub(rf'^\|\s*`{re.escape(code)}`\s*\|.*$\n', '', text, count=1, flags=re.M)
    assert without != text, f"could not find `{code}`'s remedy row to move"
    where = without.find(CLOSE, without.find(EXEMPTIONS_OPEN))
    return without[:where] + row + "\n" + without[where:]


# ── 4b · negative — SRV-FIX-343's site exemption, written the four cheap ways ────────────────

def test_a_site_exemption_written_the_cheap_way_is_refused():
    """MANDATORY NEGATIVE for the table `SRV-FIX-343` adds.

    A per-site null is the cheapest row in this document to abuse: one endpoint, one `null`, and
    the code stays green in the remedy table where anybody looking would find it. So each of the
    four ways of writing one badly is run against a document assembled here — the real §11 with
    one extra row — and each must be refused BY NAME.
    """
    codes = error_codes()
    text = spec()
    assert not audit(text, codes), "the document is not green, so this negative proves nothing"

    remedied = sorted(remedy_table(text))[0]
    exempted = sorted(exemption_table(text))[0]
    reason = "there is nothing the tool could offer that would end this state"

    # Each shape names the phrase its own complaint must carry, so a row refused for the wrong
    # reason fails here. `any(code in complaint)` alone would let one rule cover for another.
    for shape, row, phrase in (
            ("no reason at all",
             f"| `{remedied}` | `E8` |  |", "exempt with no reason"),
            ("no reason cell",
             f"| `{remedied}` | `E8` |", "exempt with no reason"),
            ("a site §5 never specifies",
             f"| `{remedied}` | `E404` | {reason} |", "specifies no endpoint block"),
            ("no site at all",
             f"| `{remedied}` |  | {reason} |", "exempt at no site at all"),
            ("a code already exempt everywhere",
             f"| `{exempted}` | `E8` | {reason} |", "exempt once")):
        complaints = audit(_add_site_exemption(text, row), codes)
        assert any(shape_code(row) in c and phrase in c for c in complaints), (
            f"a site exemption with {shape} was not refused for being that: check 12 had to say "
            f"{phrase!r} about `{shape_code(row)}` and said {complaints}")

    # A code that is nowhere remediable does not get a site exemption either: it belongs in the
    # code-level table, where every one of its sites is covered by one sentence.
    moved = _add_site_exemption(
        re.sub(rf'^\|\s*`{re.escape(remedied)}`\s*\|.*$\n', '', text, count=1, flags=re.M),
        f"| `{remedied}` | `E8` | {reason} |")
    assert any(remedied in c and "remedy table row" in c for c in audit(moved, codes)), (
        f"a code with no remedy row anywhere was accepted as exempt at one site: "
        f"{audit(moved, codes)}")

    # ...and the well-formed row is green, so every refusal above is about the DEFECT in the row
    # and not about the mutation having broken the table.
    ok = _add_site_exemption(text, f"| `{remedied}` | `E8` | {reason} |")
    assert not audit(ok, codes), (
        f"a correctly written site exemption is refused too, so this case is rejecting the "
        f"insertion rather than what is wrong with each row: {audit(ok, codes)}")


def shape_code(row):
    """The code a fixture row is about, so a complaint can be matched to the row that caused it."""
    return row.strip("| ").split("|")[0].strip().strip("`")


def _add_site_exemption(text, row):
    """The real document with one extra row in the site exemption table."""
    where = text.find(CLOSE, text.find(SITE_EXEMPTIONS_OPEN))
    assert where > 0, "SPEC.md carries no site exemption table to add a row to"
    return text[:where] + row + "\n" + text[where:]


# ── SRV-FEAT-065 · the obligation that failed three QA rounds, made executable ────────────────
#
# §1 asked for one sentence in `SPEC.md` F31 — *"which commit a role's worktree starts from"* —
# and put the ask in a **Spec:** bullet rather than in the criteria list. Nothing could go red on
# it. Round 1's QA read it and set it aside in one clause (*"I did not verify the spec text as it
# is not a §1 assertion"*), round 3 enumerated it as criterion 7 and it was the ticket's only
# outright FAIL. Both readings were defensible, which is what makes the specification the defect
# and not either session.
#
# The structural half of this ticket is that an obligation about a document is asserted like any
# other. This is that assertion.

def test_f31_says_which_commit_a_role_lanes_worktree_starts_from():
    """F31's own entry states the base of a role lane's branch, not only its independence.

    The silence was load-bearing. F31 explains a new lane as *"a new worktree, a new branch and a
    new session, so the started role cannot inherit the transcript, the reasoning or the framing"*
    — independence from the SESSION, which is wanted — and says nothing about the commits, so
    independence from the WORK read as the same virtue. `docs/operations/ESCAPES.md` (2026-08-21) is that
    sentence being cashed in: a QA lane carrying three commits of a concurrent session and none of
    the fix it existed to judge, found by the operator and not by any gate.

    E12 gained the rule when `SRV-FIX-184` fixed the code. F31 is where a reader meets the feature,
    and it is where §1 asked for the sentence.
    """
    text = SPEC.read_text()
    start = text.index("**F31 · A declared role starts itself")
    # F31's own entry and no further: the next feature heading, or the rule that ends the section.
    end = min(i for i in (text.find("\n**F32", start), text.find("\n---", start), len(text))
              if i > start)
    f31 = text[start:end]

    assert "cut from the branch the work is on" in f31.lower() or \
           "branch the work is on" in f31.lower(), (
        "F31 still says nothing about which commit a role lane's worktree starts from. That "
        "silence is how a QA lane came to be cut from `HEAD` and judge whatever that happened to "
        "be — docs/operations/ESCAPES.md, 2026-08-21.\n\nF31 reads:\n" + f31[:1200])

    # ...and it is not the stale claim, which said the opposite in the same words' place.
    assert "cut fresh from `HEAD`" not in f31, (
        "F31 asserts the pre-SRV-FIX-184 behaviour, which the code no longer has:\n" + f31[:1200])


# ── SRV-FEAT-304 · §11's checks 1-7, executed ────────────────────────────────────────────────
#
# §11 is a list of checks about `SPEC.md`, and ten of them were shell one-liners printed in the
# document for whoever happened to be reading it. Nothing ran them. Two were red for months — the
# socket table's stated total and the vocabulary check "the whole product rests on" — and one gap
# was invisible to all of them by construction: check 2 iterates the endpoint blocks that EXIST,
# so an endpoint cited by §9 and never specified passes it in silence. `E25` did, from
# `SRV-FEAT-177` until this ticket.
#
# Every case below reads the document. None asserts a number this ticket also wrote into it.

SURFACE_SPLIT = re.compile(r'^### (4\.\d+.*)$', re.M)
ENDPOINT_SPLIT = re.compile(r'^#### (E\d+) · (\S+) `([^`]+)`.*$', re.M)
FEATURE_SPLIT = re.compile(r'^\*\*(F\d+) · ', re.M)
SOCKET_ROW = re.compile(r'^\| (M\d+) \| `', re.M)
CITED = re.compile(r'\bE(\d+)\b')


def surfaces(text):
    """`(heading, body)` for every `### 4.N` block, in document order — §11 check 1's own split."""
    parts = SURFACE_SPLIT.split(text)[1:]
    return list(zip(parts[::2], parts[1::2]))


def data_sources(body):
    """The `E<n>`/`M<n>` a surface's **Data** line names. Check 1's expression, character for
    character, so a surface this file judges is judged by the check the document publishes."""
    data = re.search(r'\*\*Data\*\*(.*?)(?:\n- \*\*|\n###|\n---)', body, re.S)
    return re.findall(r'`(E\d+|M\d+)', data.group(1)) if data else []


def surfaces_with_no_source(text):
    return [head.split()[0] for head, body in surfaces(text) if not data_sources(body)]


def endpoint_blocks(text):
    """`(id, method, path, body)` for every `#### E<n>` block."""
    parts = ENDPOINT_SPLIT.split(text)[1:]
    return [tuple(parts[i:i + 4]) for i in range(0, len(parts), 4)]


def incomplete_endpoints(text):
    """`{id: [the sections it does not state]}` — check 2, which is about the blocks that exist."""
    out = {}
    for eid, _method, _path, body in endpoint_blocks(text):
        missing = [f for f in ("Request", "Response", "Missing") if f"**{f}**" not in body]
        if missing:
            out[eid] = missing
    return out


def cited_but_unspecified(text):
    """Every `E<n>` the document names anywhere that has no `#### E<n>` block of its own."""
    specified = {eid for eid, *_ in endpoint_blocks(text)}
    cited = {f"E{n}" for n in CITED.findall(text)}
    return sorted(cited - specified, key=lambda e: int(e[1:]))


CHECKS_SECTION = re.compile(r'^## \d+\. How to check this document.*$', re.M)


def checks_section(text):
    """§11 — from its own heading to the next `## `, or to the end of the document.

    Every read of a check below is bounded to this span. The bound comes from the document's own
    headings and never from a line number, so §11 moving down the page as §4 and §5 grow does not
    move what a check is.
    """
    heads = list(CHECKS_SECTION.finditer(text))
    assert len(heads) == 1, (
        f"SPEC.md carries {len(heads)} sections headed `How to check this document`, so §11's "
        f"span is not a span. A check read out of an unlocatable section is a check the rest of "
        f"the document is free to define.")
    start = heads[0].start()
    after = re.search(r'^## ', text[heads[0].end():], re.M)
    return text[start:] if after is None else text[start:heads[0].end() + after.start()]


def check_block(text, n):
    """§11's check `n`, bounded below by §11 itself and above by the heading of check `n + 1`.

    Both bounds are asserted rather than defaulted: a block that ran to the end of the document
    would sweep every later check's text into whatever is measured here, and a block that started
    at the first `**n · ` ANYWHERE would let a worked example above §11 become the check. The
    second is `SRV-FIX-305` — a `**3 ·`/`**4 ·` pair in §4 shadowed check 3, and a `# expect 16`
    restated inside the real check 3 was then never looked at.
    """
    section = checks_section(text)
    start = section.find(f"\n**{n} · ")
    assert start > 0, f"§11 publishes no check {n} to read"
    rest = section[start + 1:]
    end = rest.find(f"\n**{n + 1} · ")
    assert end > 0, f"§11 has a check {n} and no check {n + 1} to bound it"
    return rest[:end]


def vocabulary_regex(text):
    """Check 6's expression, taken from §11's check 6 block instead of copied into this file.

    `SRV-FEAT-304`'s §1 asks for *"the bracketed regex is §11's, character for character"*.
    Reading it off the page is the only way to make that true and keep it true: a copy here would
    be a second definition of the assertion the whole product rests on, free to drift from the one
    the document publishes. Reading it off the WHOLE page is how something else becomes it — the
    first `grep -icE '…'` in the file was the definition until `SRV-FIX-305`, so a narrowed
    expression written anywhere above §11 silently replaced the assertion and the canary beneath
    it still fired.
    """
    match = re.search(r"grep -icE '([^']+)'", check_block(text, 6))
    assert match, "§11's check 6 publishes no `grep -icE '…'` expression to run"
    return match.group(1)


def repository_vocabulary(text):
    """`[(line number, line)]` for every line check 6's regex matches."""
    pattern = re.compile(vocabulary_regex(text), re.I)
    return [(n, line) for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]


def vocabulary_members(text):
    """The words check 6 forbids, with §11's bracketing of the first letter normalised away.

    `[b]oard` is written that way so the check's own line is not the one thing the check finds;
    the WORD is `board`, and a ratchet comparing two revisions has to compare words rather than
    spellings — otherwise re-bracketing a letter reads as a removal and a removal hides behind a
    re-bracketing.
    """
    inner = re.search(r"\(([^)]*)\)", vocabulary_regex(text)).group(1)
    return {re.sub(r"\[(.)\]", r"\1", word) for word in inner.split("|")}


def previous_spec():
    """`(rev, text)` for `SPEC.md` at the newest commit whose copy DIFFERS from the one on disk.

    The ratchet's left-hand side is derived, never recorded here. A sha or a member count written
    into this module is a baseline somebody has to move by hand, and a baseline nobody moves is a
    ratchet that has stopped ratcheting. Skipping the revisions equal to the working tree makes
    the answer the same before and after this ticket's own commit: the version this document last
    differed from.
    """
    current = SPEC.read_text()
    paths = (str(SPEC.relative_to(FACTORY)), "docs/SPEC.md")
    log = subprocess.run(["git", "log", "--format=%H", "--", *paths],
                         cwd=FACTORY, capture_output=True, text=True)
    for rev in log.stdout.split():
        for path in paths:
            blob = subprocess.run(["git", "show", f"{rev}:{path}"],
                                  cwd=FACTORY, capture_output=True, text=True)
            if blob.returncode == 0:
                if blob.stdout != current:
                    return rev, blob.stdout
                break
    return None, None


def removal_reason(block, word):
    """The line of check 6's block that names `word` and says it was removed, or `None`.

    The line is de-bracketed before it is read, exactly as `vocabulary_members` de-brackets the
    expression. §11 writes every word it discusses as `[f]irmware` so the paragraph is not itself
    found by the check, and a matcher that only reads the plain spelling refuses the removal note
    written in the document's own convention — measured, on the real document, by this ticket's
    second mandatory negative.
    """
    for line in block.splitlines():
        plain = re.sub(r"\[(.)\]", r"\1", line)
        if re.search(r"\bremoved\b", plain, re.I) and re.search(rf"\b{re.escape(word)}\b", plain, re.I):
            return line.strip()
    return None


def unreasoned_removals(previous_text, current_text):
    """Members that left check 6's expression without §11 carrying a line saying why."""
    gone = vocabulary_members(previous_text) - vocabulary_members(current_text)
    block = check_block(current_text, 6)
    return sorted(word for word in gone if not removal_reason(block, word))


NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


# ── check 1 · every surface names its data ───────────────────────────────────────────────────

def test_every_surface_in_section_4_names_a_data_source():
    """A screen with no source is a screen somebody will invent an endpoint for.

    The count is the document's own: a surface added without a `**Data**` line turns this red on
    the day it is added, and no literal here has to be kept in step by hand.

    The fixture is TWO surfaces that DISAGREE — one with a source, one without. On a uniform
    fixture `any` and `all` are the same function and a predicate over an empty list is `True`
    for both, which is `SRV-FEAT-165`'s blindness in the shape it takes here.
    """
    assert surfaces_with_no_source(spec()) == [], (
        f"§4 surfaces that name no data source: {surfaces_with_no_source(spec())}")

    disagreeing = (
        "### 4.98 A surface with a source\n\n"
        "- **Data** — `E1` for the version.\n"
        "- **Empty** — nothing to draw.\n\n"
        "### 4.99 A surface with none\n\n"
        "- **Loading** — the frame.\n"
        "- **Empty** — nothing to draw.\n\n---\n")
    assert len(surfaces(disagreeing)) == 2, "the fixture is not two surfaces"
    assert surfaces_with_no_source(disagreeing) == ["4.99"], (
        f"check 1 cannot tell the two apart: it named "
        f"{surfaces_with_no_source(disagreeing)} on a document with one of each")


# ── check 2 · every endpoint block is complete ───────────────────────────────────────────────

def test_every_endpoint_block_is_complete():
    """Method, path, request, response, and what a missing thing answers — every block, always."""
    assert incomplete_endpoints(spec()) == {}, (
        f"endpoint blocks that do not state all three: {incomplete_endpoints(spec())}")

    disagreeing = (
        "#### E98 · GET `/api/complete`\n"
        "- **Request** — none.\n- **Response** — `200 {}`.\n- **Missing** — `404`.\n\n"
        "#### E99 · GET `/api/half-written`\n"
        "- **Request** — none.\n- **Response** — `200 {}`.\n\n---\n")
    assert len(endpoint_blocks(disagreeing)) == 2, "the fixture is not two endpoints"
    assert incomplete_endpoints(disagreeing) == {"E99": ["Missing"]}, (
        f"check 2 cannot tell a complete block from a half-written one: "
        f"{incomplete_endpoints(disagreeing)}")


# ── check 2's blind spot · the negative it cannot be ─────────────────────────────────────────

def test_an_endpoint_cited_but_never_specified_is_a_failure():
    """MANDATORY NEGATIVE — the gap check 2 passes in silence, by construction.

    Check 2 iterates the `#### E<n>` blocks that exist and judges each one. An endpoint the
    document CITES and never specifies is not in that iteration, so it is not judged: `E25` was
    named by §9.2's `recover_lane` row from `SRV-FEAT-177` onwards, is `POST /api/lanes/{lane}/
    recover` in `app/api/_recover-lane.cpp`, and had no block until `SRV-FEAT-304`. Six more
    surfaces' endpoints are exactly the shape of gap this must not add six more of.
    """
    assert cited_but_unspecified(spec()) == [], (
        f"cited in SPEC.md and never specified: {cited_but_unspecified(spec())}. An endpoint a "
        f"reader can reach by name and cannot read is one check 2 will never mention.")

    disagreeing = (
        "#### E98 · GET `/api/specified`\n"
        "- **Request** — none.\n- **Response** — `200 {}`.\n- **Missing** — `404`.\n\n"
        "The remedy calls E99, which nothing below specifies.\n\n---\n")
    assert incomplete_endpoints(disagreeing) == {}, (
        "check 2 is green on the fixture, which is what makes it the right fixture")
    assert cited_but_unspecified(disagreeing) == ["E99"], (
        f"the citation of an unspecified endpoint was not caught: "
        f"{cited_but_unspecified(disagreeing)}")


# ── check 6 · no repository-specific vocabulary ──────────────────────────────────────────────

def test_no_repository_specific_vocabulary_in_the_spec():
    """MANDATORY NEGATIVE — §11 calls check 6 *"the assertion the whole product rests on"*.

    The tool reads whatever repository it is pointed at. A word from one of them in this document
    is a place where the next reader learns something about a roti machine from the specification
    of a tool that must not know it has one. Four lines carried such words for months, and
    nothing ran the check that says so.
    """
    text = spec()
    hits = repository_vocabulary(text)
    assert hits == [], (
        f"{len(hits)} lines carry a target repository's vocabulary — check 6 expects none:\n  "
        + "\n  ".join(f"SPEC.md:{n}: {line.strip()[:140]}" for n, line in hits))

    # ...and the regex is live, not a decoration: a document that DOES carry such a word is found
    # by the same expression, read off the same page.
    planted = text + "\nA lane may hold the board while the firmware ticket runs.\n"
    assert repository_vocabulary(planted), (
        "check 6's regex, read out of §11, matched nothing in a line written to be matched — so "
        "the green above is about the expression and not about the document")


def test_a_plural_of_a_forbidden_word_is_found():
    """MANDATORY NEGATIVE — the hole check 6 answered `0` about for every member at once.

    The expression was `\\b(…)\\b`, and the trailing boundary makes every member singular-only:
    `boards`, `seams`, `firmwares` and `rotis` were all outside *the assertion the whole product
    rests on*. `seams` was merely the one such word the document happened to carry
    (`SRV-FEAT-304`'s QA, `SPEC.md:7`), so a case about `seams` would close one line and leave
    the class open.

    The fixture is two lines that DISAGREE — one carrying `boards` and `firmwares` and neither
    singular, one an ordinary sentence with neither — so the case cannot pass by matching
    everything. The sweep underneath it is derived from the expression rather than typed, so a
    member added to the list tomorrow gains the property without an edit here.
    """
    text = spec()
    plural = "Two lanes hold boards while the firmwares build."
    ordinary = "A lane rail draws one row per claim and names nothing it cannot read."
    assert not re.search(r"\b(board|firmware)\b", plural), (
        "the fixture line carries a singular, so it would be found by the old expression too")

    assert text.endswith("\n"), "the fixture appends whole lines to the document"
    document = text + plural + "\n" + ordinary + "\n"
    first = len(text.splitlines()) + 1
    assert repository_vocabulary(text) == [], (
        "check 6 is green on the document, which is what makes the two appended lines the only "
        "thing this case can be measuring")
    assert repository_vocabulary(document) == [(first, plural)], (
        f"check 6, read off §11, does not report the plural line alone: "
        f"{repository_vocabulary(document)}. Expected {[(first, plural)]} — the ordinary sentence "
        f"one line below it carries no forbidden word and must not be reported.")

    silent = sorted(word for word in vocabulary_members(text)
                    if not repository_vocabulary(text + f"\nA line about {word}s.\n"))
    assert silent == [], (
        f"check 6 answers `0` about the plural of {silent} — every member of the list owes this "
        f"property, not the one word the document happened to leak")


def test_line_7_and_check_6_agree():
    """The instance, asserted by the same function that asserts the general rule.

    `SPEC.md`'s line about `MODEL.md` §2 read *"the six seams a ticket may not contradict"* while
    check 6 forbade `seam` — the document carrying a word its own check refuses, in the one form
    that check returned `0` over. This ticket takes §1's reading 1: `CLAUDE.md`'s statement of the
    boundary names `board` and `seam` in one sentence as the same kind of word, so the list is
    right and the line is a leak, rewritten in the tool's own word — `invariants`, which is how
    `CLAUDE.md` names `MODEL.md` §2's six.

    Green on its own proves only that the document says nothing forbidden; it would stay green if
    check 6 went blind. So the case also RESTORES the leak into the very line and requires the
    published expression to find it, at that line number and no other.
    """
    text = spec()
    assert repository_vocabulary(text) == [], (
        f"the document carries a target repository's vocabulary: {repository_vocabulary(text)}")

    lines = text.splitlines()
    about_model = [(n, line) for n, line in enumerate(lines, 1)
                   if "a ticket may not contradict" in line]
    assert len(about_model) == 1, (
        f"§11's check 6 and the line naming `MODEL.md` §2's six are asserted against each other "
        f"here, and that line is not findable: {about_model}")
    number, line = about_model[0]
    assert "invariant" in line, (
        f"SPEC.md:{number} does not name `MODEL.md` §2's six in the tool's own word: {line!r}")

    restored = "\n".join(lines[:number - 1] + [line.replace("invariants", "seams")]
                         + lines[number:])
    assert [n for n, _ in repository_vocabulary(restored)] == [number], (
        f"the expression §11 publishes does not find `seams` put back at SPEC.md:{number} — so "
        f"the green above is about the expression having gone blind, not about the document: "
        f"{repository_vocabulary(restored)}")


def test_removing_a_word_from_check_6_is_not_a_way_to_make_it_green():
    """MANDATORY NEGATIVE — this ticket's own cheap way out, closed.

    Check 6 is the one check whose subject is a LIST, so it is the one check that can be made
    green by shortening the list. §1's reading 2 — `[s]eam` leaves the expression — and *"delete
    the inconvenient word"* are the same diff, and nothing in the suite could tell them apart: a
    member that goes silently takes every future leak of that word with it.

    A word may still leave, and §11 says so in a line. The fixture is two removals that DISAGREE
    — one with that line, one without — because a check that accepts both is not a ratchet, and
    one that refuses both forbids a decision the document is allowed to make.
    """
    rev, previous = previous_spec()
    assert previous, (
        "no committed `SPEC.md` differs from the one on disk, so this case compared the document "
        "with itself and asserted nothing. An empty sweep is a red.")
    before = vocabulary_members(previous)
    assert before, (
        f"check 6's expression at {rev[:8]} forbids no words at all — that is a broken parse "
        f"reading as a clean history")
    assert unreasoned_removals(previous, spec()) == [], (
        f"these words left check 6's list since {rev[:8]} and §11 does not say why: "
        f"{unreasoned_removals(previous, spec())}")

    text = spec()
    standing = {word: removal_reason(check_block(text, 6), word)
                for word in vocabulary_members(text)}
    assert not any(standing.values()), (
        f"§11 already reads as a removal note for a word still ON the list: "
        f"{ {w: r for w, r in standing.items() if r} }. A standing excuse waves the next removal "
        f"through in silence, which is the whole of what this case refuses.")

    expression = vocabulary_regex(text)
    spellings = re.search(r"\(([^)]*)\)", expression).group(1).split("|")
    assert len(spellings) > 1, "a one-member list cannot be shortened by one and still be a list"
    victim = spellings[-1]                    # whichever word is last; never a word named here
    word = re.sub(r"\[(.)\]", r"\1", victim)
    dropped = text.replace(expression, expression.replace("|" + victim, ""), 1)
    assert vocabulary_members(dropped) == vocabulary_members(text) - {word}, (
        f"the fixture did not remove exactly one member: {vocabulary_members(dropped)}")
    assert not repository_vocabulary(dropped + f"\nA line about the {word} it dropped.\n"), (
        "a shortened list still finds the word it dropped, so the removal is not the hole this "
        "case exists to refuse")
    assert unreasoned_removals(text, dropped) == [word], (
        f"a member deleted from check 6 with no reason in §11 was accepted: "
        f"{unreasoned_removals(text, dropped)}")

    # Two spellings, because §11 has two and the ratchet must read both: the paragraph's own
    # convention brackets the first letter, and a word no longer on the list may be written plain.
    for spelling in (f"`{victim}`", f"`{word}`"):
        reasoned = dropped.replace(
            "\n**7 · ",
            f"\n{spelling} was removed from the list: it is a word about a target repository's "
            f"own build.\n\n**7 · ", 1)
        assert removal_reason(check_block(reasoned, 6), word), (
            f"§11's removal note written as {spelling} was not read as one")
        assert unreasoned_removals(text, reasoned) == [], (
            f"a member removed WITH §11's reason line written as {spelling} is refused, so the "
            f"ratchet forbids a decision the document may make: "
            f"{unreasoned_removals(text, reasoned)}")


# ── check 3 · the socket table states its own total ──────────────────────────────────────────

def test_the_socket_table_row_count_is_not_stated_twice():
    """§11 check 3 said `expect 16` over a table with seventeen rows.

    A count kept in step by hand is a count that goes stale, and the check that goes stale is the
    one nobody runs. So the total is DERIVED from the table here, §11's check derives it too, and
    the one place the document still names the number — §6's own closing sentence — is asserted
    against the rows rather than against another sentence.
    """
    text = spec()
    rows = SOCKET_ROW.findall(text)
    assert len(rows) > 10, f"§6's tables parsed to {rows}, which is not the socket contract"

    stated = re.findall(r'#\s*expect\s+(\d+)', check_block(text, 3))
    assert stated == [], (
        f"§11 check 3 states the socket table's total as {stated} instead of deriving it. The "
        f"table has {len(rows)} rows, and a literal here is a second place for that number to "
        f"live and a first place for it to be wrong.")

    sentence = re.search(r'^(\w+) messages\. (\w+) client→server, (\w+) server→client', text, re.M)
    assert sentence, "§6 no longer states its own totals in words, so nothing here is checked"
    total, to_server, to_client = (NUMBER_WORDS[w.lower()] for w in sentence.groups())
    assert (total, to_server, to_client) == (len(rows), len(rows) - to_client, len(rows) - to_server), (
        f"§6 says {sentence.group(0)!r} over a table of {len(rows)} rows: {rows}")


# ── check 4 · every feature has a runnable criterion ─────────────────────────────────────────

def test_every_feature_block_has_a_runnable_criterion():
    """Both counts derived. §7's promise is that every feature can be run, not described."""
    text = spec()
    parts = FEATURE_SPLIT.split(text)[1:]
    blocks = list(zip(parts[::2], parts[1::2]))
    assert len(blocks) > 25, f"§7 parsed to {len(blocks)} feature blocks, which is not §7"

    without = [fid for fid, body in blocks if not re.search(r'^\*Criteri', body, re.M)]
    assert without == [], f"feature blocks stating no criterion: {without}"

    headings = len(re.findall(r'^\*\*F\d+ · ', text, re.M))
    criteria = len(re.findall(r'^\*Criteri', text, re.M))
    assert headings == criteria, (
        f"{headings} feature headings and {criteria} criterion lines. Check 4 is the pair, and a "
        f"feature carrying two criterion lines while another carries none reads as balanced.")


# ── check 7 · the manifest schema and §9 agree, both directions ──────────────────────────────

def _schema_tree(node, path=''):
    kids = []
    for key, value in (node.get("properties") or {}).items():
        child = f"{path}.{key}" if path else key
        kids.append(child)
        yield from _schema_tree(value, child)
    extra = node.get("additionalProperties")
    if isinstance(extra, dict):
        kids.append(f"{path}.<name>")
        yield from _schema_tree(extra, f"{path}.<name>")
    yield (path, kids)


def test_the_manifest_schema_and_section_9_agree_in_both_directions():
    """§11 check 7, executed. A field the schema defines that §9 never mentions is a field the
    tool accepts and nothing specifies; a row in §9 the schema lacks is a specification of
    something no manifest may declare. Both are drift, and this ticket adds two optional keys to
    both sides at once.
    """
    schema = json.loads(MANIFEST_SCHEMA.read_text())
    tree = dict(_schema_tree(schema))
    paths = {p for p in tree if p}
    leaves = {p for p in paths if not tree.get(p)}

    text = spec()
    table = re.search(r'\n## 9\..*?\n\n(\|.*?)\n\n', text, re.S).group(1)
    rows = [r for r in table.splitlines() if r.startswith("| `")]
    used = {re.sub(r'[`~]', '', r.split("|")[1]).strip() for r in rows}

    assert leaves - used == set(), (
        f"the schema defines fields §9 relies on nowhere: {sorted(leaves - used)}")
    assert used - paths == set(), (
        f"§9 relies on fields the schema does not define: {sorted(used - paths)}")


# ── the six surfaces the delivered design adds ───────────────────────────────────────────────

NEW_SURFACES = ("4.15", "4.16", "4.17", "4.18", "4.19", "4.20")


def test_each_of_the_six_new_surfaces_is_specified():
    """`Dashboard.dc.html` draws eight destinations and five had no specification to build against.

    Each of the six gets one section, one data source, ONE endpoint, and an empty state. One
    endpoint and not one per panel: §4.18 draws three panels, and per-panel endpoints put six
    requests on one page load and give six ways for the panels to disagree about one repository.
    """
    blocks = {head.split()[0]: body for head, body in surfaces(spec())}
    absent = [n for n in NEW_SURFACES if n not in blocks]
    assert absent == [], f"§4 has no section for {absent}"

    complaints = []
    for number in NEW_SURFACES:
        body = blocks[number]
        sources = data_sources(body)
        endpoints = {s for s in sources if s.startswith("E")}
        if not sources:
            complaints.append(f"§{number} names no data source")
        if len(endpoints) != 1:
            complaints.append(
                f"§{number} names {sorted(endpoints) or 'no'} endpoint(s); a surface is one "
                f"request, so it names exactly one")
        if "**Empty**" not in body:
            complaints.append(f"§{number} states no empty state")
    assert complaints == [], "\n  " + "\n  ".join(complaints)


# ── SRV-FIX-384 · the repository document gate is part of what GATE means ────────────────────
#
# `WEB-FIX-328` reached `awaiting-qa` with `pytest tests/ -m repository` red on the commit that
# set that very status line, and nothing in GATE asked anybody to run it. The document gate was
# named in `CLAUDE.md` only as *"after a merge"* — which is after the last session that could act
# on it. These cases read `docs/technical/workflow/WORKFLOW.md`, the document a session is sent to for what a stage
# means, and they derive the stages from the document's own sequence blocks rather than from a
# list somebody typed here: a component added later brings its own stages with it.

WORKFLOW = FACTORY / "docs/technical/workflow/WORKFLOW.md"

# The document gate, by the command that IS it. Spacing is free; the selection is not.
DOCUMENT_GATE = re.compile(r'pytest\s+tests/\s+-m\s+repository')

# The case `WEB-FIX-328` turned red, and the commit it was red at.
THE_RED_CASE = "test_a_ticket_that_asks_for_a_screenshot_has_one"
WEB_FIX_328_AT = "8f2d1da"


def workflow_sections(text):
    """`[(title, body)]` for every `## ` section of `docs/technical/workflow/WORKFLOW.md`, in document order."""
    parts = re.split(r'^##\s+(.*)$', text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def stage_sequence(body):
    """A section's stages, read from ITS OWN fenced sequence block.

    Never a list typed here. A component whose stages this file does not know about is exactly the
    component a typed list would exempt, and the rule is about every component.
    """
    for block in re.findall(r'^```\n(.*?)^```', body, re.M | re.S):
        names = [w.strip(" `\n") for w in block.strip().split("·")]
        if len(names) > 1 and all(re.fullmatch(r'[A-Z][A-Z ]*', n) for n in names):
            return names
    return []


def gate_components(text):
    """`[(title, body, stages)]` — the sections that have a `GATE` stage.

    The product level (`REQUIREMENT → ANALYSIS → …`) has none and drops out on its own rather than
    by being named as an exception.
    """
    found = []
    for title, body in workflow_sections(text):
        stages = stage_sequence(body)
        if "GATE" in stages:
            found.append((title, body, stages))
    return found


def document_gate_mentions(body, stages):
    """`[(stage, line)]` for every line naming the document gate, labelled by WHERE it sits.

    A table row whose first cell is a stage is that stage; anything under a heading that names a
    gate is `GATE`; anything else is labelled with its heading, which is what the refusal quotes
    when a repository gate is named only after the merge.
    """
    found, heading = [], ""
    for line in body.splitlines():
        head = re.match(r'^#{3,6}\s+(.*)$', line)
        if head:
            heading = head.group(1).strip()
            continue
        if not DOCUMENT_GATE.search(line):
            continue
        cell = re.match(r'^\|\s*`?([^|`]+?)`?\s*\|', line)
        first = cell.group(1).strip().upper() if cell else ""
        titled = {w.strip("`*_.,:—-").upper() for w in heading.split()}
        if first in stages:
            found.append((first, line.strip()))
        elif re.search(r'\bgate axes\b', heading, re.I) or "GATE" in titled & set(stages):
            found.append(("GATE", line.strip()))
        else:
            found.append((heading or "(section prose)", line.strip()))
    return found


def components_not_naming_it_at_gate(text):
    """The complaint list criterion 1 refuses on, and the negative below drives directly."""
    complaints = []
    for title, body, stages in gate_components(text):
        where = [stage for stage, _line in document_gate_mentions(body, stages)]
        if "GATE" not in where:
            complaints.append(
                f"{title}: the repository gate is named under {where or ['no stage at all']}, "
                f"not under GATE")
    return complaints


def axes_rows(body):
    """`[[cell, …]]` for the rows of this section's `gate axes` table, header row excluded."""
    rows, in_table = [], False
    for line in body.splitlines():
        head = re.match(r'^#{3,6}\s+(.*)$', line)
        if head:
            in_table = bool(re.search(r'gate axes', head.group(1), re.I))
            continue
        if not in_table or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r':?-{2,}:?', c) for c in cells) or cells[0].lower() == "axis":
            continue
        rows.append(cells)
    return rows


def argv_for(line):
    """The document gate's argv, taken from the LINE THE DOCUMENT NAMES IT ON.

    Typing the command here would assert that this file and the workflow agree with this file.
    """
    span = next(s for s in re.findall(r'`([^`]+)`', line) if DOCUMENT_GATE.search(s))
    words = span.replace("cd server &&", "").strip().split()
    return [sys.executable, "-m"] + words


def test_the_gate_stage_names_the_repository_gate_for_a_document_change(tmp_path, monkeypatch):
    """A session finishing GATE on a ticket that changed a document is told to run the document
    gate, the command it is told to run reaches the case `WEB-FIX-328` turned red, and that case
    is red on `WEB-FIX-328` as it stood at `8f2d1da`.

    Three assertions and one behaviour. Presence alone is a claim about a string: the second says
    the command a session is sent to actually selects the case, and the third says the case
    actually refuses the state that walked past — the state this ticket exists for.
    """
    text = WORKFLOW.read_text()
    components = gate_components(text)
    assert components, (
        f"{WORKFLOW} declares no component with a GATE stage, so this case swept nothing — a "
        f"check that sweeps nothing has not run")

    assert components_not_naming_it_at_gate(text) == [], (
        "GATE does not name the repository gate:\n  "
        + "\n  ".join(components_not_naming_it_at_gate(text))
        + f"\n\n`{DOCUMENT_GATE.pattern}` reads the documents a ticket writes. Named after the "
          f"merge it is named after the last session that could act on it.")

    named = [line for _t, body, stages in components
             for stage, line in document_gate_mentions(body, stages) if stage == "GATE"]
    collected = subprocess.run(argv_for(named[0]) + ["--collect-only", "-q"],
                               cwd=SERVER, capture_output=True, text=True)
    assert THE_RED_CASE in collected.stdout, (
        f"the command GATE names — {' '.join(argv_for(named[0]))} — does not collect "
        f"{THE_RED_CASE}, so following GATE never reaches it:\n"
        + collected.stdout[-2000:] + collected.stderr[-2000:])

    root = tmp_path / "monorepo"
    tickets = root / "factory" / "docs" / "tickets"
    tickets.mkdir(parents=True)
    listing = subprocess.run(["git", "ls-tree", "-r", "--name-only", WEB_FIX_328_AT,
                              "docs/tickets/"], cwd=FACTORY, capture_output=True, text=True)
    assert listing.returncode == 0, f"git ls-tree at {WEB_FIX_328_AT} failed: {listing.stderr}"
    path = next(p for p in listing.stdout.split() if "WEB-FIX-328" in p)
    document = subprocess.run(["git", "show", f"{WEB_FIX_328_AT}:{path}"],
                              cwd=FACTORY, capture_output=True, text=True)
    assert document.returncode == 0, f"git show {WEB_FIX_328_AT}:{path} failed: {document.stderr}"
    (tickets / Path(path).name).write_text(document.stdout)

    evidence = subprocess.run(["git", "ls-tree", "-r", "--name-only", WEB_FIX_328_AT,
                               "docs/reports/WEB-FIX-328"], cwd=FACTORY,
                              capture_output=True, text=True)
    assert evidence.stdout.split() == [], (
        f"the reproduction assumes the evidence directory held nothing at {WEB_FIX_328_AT}; "
        f"it held {evidence.stdout.split()}")

    import test_catalog
    monkeypatch.setattr(test_catalog, "_repo_root", lambda: root)
    try:
        getattr(test_catalog, THE_RED_CASE)()
    except AssertionError as red:
        assert "WEB-FIX-328" in str(red), (
            f"{THE_RED_CASE} refused something else on this reproduction:\n{red}")
    else:
        raise AssertionError(
            f"{THE_RED_CASE} is GREEN on WEB-FIX-328 as {WEB_FIX_328_AT} left it — "
            f"`status: awaiting-qa`, §1 asking for a screenshot, and no image committed. The "
            f"reproduction is wrong, or the case no longer refuses what it refused on 2026-09-04")


#: The tiers a ticket already runs, keyed by the component the section names — `Server`, `Web`,
#: read off the title rather than off its number, which renumbers. Frozen verbatim at SRV-FIX-384: this ticket adds an axis and may not
#: touch a tier, and a later ticket that means to change one changes this line as a separate act.
#: The release tier is not here — it is a VERIFY axis, and GATE is what this case is about.
FROZEN_TIERS = {
    "Server": [["behaviour · tier 1", "the ticket's own assertions", "`pytest tests/test_<area>.py -v`"],
          ["behaviour · tier 2",
           "the module it touched **plus** `test_isolation.py test_confine.py`, always", "21 s"],
          ["human · tier 3", "what a person sees", "drive it at :7779"]],
    "Web": [["behaviour · tier 1/2", "the ticket's assertions, then the suite", "`npm run test`"],
          ["human · tier 3", "what a person sees", "drive it at :7779"]],
}


def component_of(title):
    """`2 · Server — \u0060SRV-*\u0060` → `Server`. The component a section is about, by name."""
    return re.sub(r'^\d+\s*·\s*', '', title).split("—")[0].strip()


def test_the_tiers_are_unchanged_and_tier_3_is_still_last():
    """The three tiers a ticket already runs are untouched, and tier 3 is still the last word on
    GATE: the document gate is an addition, not a fourth tier that replaces the human pass.

    A person reading the axes table top to bottom must reach `human · tier 3` last, because that
    is the order in which somebody works — and a row after it is a row that reads like the end.
    """
    text = WORKFLOW.read_text()
    components = gate_components(text)
    assert components, f"{WORKFLOW} declares no component with a GATE stage"

    for title, body, _stages in components:
        rows = axes_rows(body)
        assert rows, f"{title} has no gate axes table"

        tiers = {re.search(r'tier ([\d/]+)', " ".join(row)).group(1): row
                 for row in rows if re.search(r'tier [\d/]+', " ".join(row))}
        assert "3" in tiers, f"{title}: the axes table has no tier 3 row: {sorted(tiers)}"
        assert re.search(r'7779', " ".join(tiers["3"])), (
            f"{title}: tier 3 no longer says a person drives it at :7779 — {tiers['3']}")

        assert [row for row in rows if row in FROZEN_TIERS[component_of(title)]] == FROZEN_TIERS[component_of(title)], (
            f"{title}: a tier row changed. The tiers a ticket already runs are frozen here "
            f"deliberately, so changing one is an act somebody chose:\n"
            f"  now:    {[r for r in rows if re.search(r'tier', ' '.join(r))]}\n"
            f"  frozen: {FROZEN_TIERS[component_of(title)]}")

        of_gate = [row for row in rows if re.search(r'tier [\d/]+', " ".join(row))
                   and not re.search(r'VERIFY only|release', " ".join(row), re.I)]
        assert of_gate[-1] is tiers["3"], (
            f"{title}: the last tier GATE names is {of_gate[-1]!r}, not tier 3 — the document "
            f"gate is an addition to GATE, not the tier a person reads as its end")

        documents = [row for row in rows if DOCUMENT_GATE.search(" ".join(row))]
        assert documents, f"{title}: no axis runs the document gate"
        for row in documents:
            assert not re.search(r'tier [\d/]+', " ".join(row)), (
                f"{title}: the document gate is numbered as a tier — {row}. It is additional to "
                f"the three tiers, and a fourth number is read as replacing the third")
            assert rows.index(row) < rows.index(tiers["3"]), (
                f"{title}: the document gate is listed after tier 3, so the last word on GATE is "
                f"no longer a person driving the ticket — {row}")

    server = next(body for title, body, _s in components if "Server" in title)
    gate_cell = next(line for line in server.splitlines()
                     if re.match(r'^\|\s*`GATE`\s*\|', line))
    for tier in ("1", "2", "3"):
        assert f"tier {tier}" in gate_cell, (
            f"the server GATE row stopped naming tier {tier}: {gate_cell}")


def relocate_document_gate(text, stage):
    """`docs/technical/workflow/WORKFLOW.md` with every GATE mention of the document gate moved into `stage`'s row.

    Built from the real document by MOVING what this ticket added, which is the same break as
    reverting the fix — the gate named where `CLAUDE.md` had it, after the merge.
    """
    moved = []
    out = []
    for title, body, stages in gate_components(text):
        for _stage, line in document_gate_mentions(body, stages):
            moved.append(line)
    for line in text.splitlines():
        if line.strip() in moved:
            continue
        row = re.match(r'^\|\s*`?([A-Z]+)`?\s*\|', line)
        if row and row.group(1) == stage:
            line = line.rstrip("|").rstrip() + " — `pytest tests/ -m repository` |"
        out.append(line)
    return "\n".join(out) + "\n"


def test_a_repository_gate_named_only_after_the_merge_is_refused():
    """MANDATORY NEGATIVE — the instruction has to be findable at GATE, not merely present.

    A document that names the repository gate only under `MERGE` is the state this ticket started
    from wearing a different hat: `CLAUDE.md` named it *"after a merge"*, which is a mention, and
    the round still walked past. The refusal quotes the stage it was found under, so the reader is
    told what is wrong rather than that something is.
    """
    text = WORKFLOW.read_text()
    assert components_not_naming_it_at_gate(text) == [], (
        "this case is about the checker's refusal; the document itself must be green first")

    after_the_merge = relocate_document_gate(text, "MERGE")
    complaints = components_not_naming_it_at_gate(after_the_merge)
    assert complaints, (
        "a workflow naming the repository gate only under MERGE is accepted, so criterion 1 "
        "cannot fail and is not a criterion")
    assert any("MERGE" in c for c in complaints), (
        f"the refusal does not quote the stage it was found under: {complaints}")

    deleted = "\n".join(line for line in text.splitlines()
                        if not DOCUMENT_GATE.search(line)) + "\n"
    assert components_not_naming_it_at_gate(deleted), (
        "a workflow that names the repository gate nowhere at all is accepted, so the case above "
        "passes on the relocation breaking the parse rather than on where the gate is named")


# ── SRV-FIX-305 · a check may not read its own definition from outside §11 ───────────────────
#
# `SRV-FEAT-304` made §11's checks run. Two of them find their own definition by first-match over
# the WHOLE document: `vocabulary_regex` takes the first `grep -icE '…'` anywhere in `SPEC.md`,
# and `check_block` anchors on the first `**n · ` anywhere in it. `check_block` asserts its upper
# bound and not its lower one; `vocabulary_regex` asserts neither. Everything above §11 — §4's
# surfaces, §5's endpoint blocks, a worked example — is therefore free to become the assertion.
#
# Every fixture below is a MUTATED COPY held as a string. `docs/product/SPEC.md` is never written to: the
# checks take the document as an argument, and a probe that edits the real file in a lane is a
# probe a concurrent reader can see.

PLANTED_REGEX = r"\b([b]oard)\b"

# The leak the narrowed expression misses. It carries four of check 6's six words and never the
# word `board`, which is what makes the fixture the two halves DISAGREEING: the planted expression
# matches the canary `test_no_repository_specific_vocabulary_in_the_spec` already plants, and an
# expression matching neither is caught by that canary without any of this.
PLANTED_LEAK = ("The esp32 runs the firmware acme ships, and the roti is done when the heater "
                "says so.")

SECTION_11_HEADING = "\n## 11. How to check this document"
ABOVE_SECTION_11 = "### 4.1 "


def _section_11(text):
    """§11 as these cases locate it — INDEPENDENTLY of the reader under test.

    The reader's own bound is the thing being judged, so an assertion built on it would agree
    with it by construction. This walks the same two headings by hand instead.
    """
    start = text.index(SECTION_11_HEADING) + 1
    after = re.search(r'^## ', text[start + 3:], re.M)
    return text[start:] if after is None else text[start:start + 3 + after.start()]


def _insert_above_section_11(text, blob):
    """`text` with `blob` inserted before §4.1 — above §11, asserted rather than assumed."""
    where = text.index(ABOVE_SECTION_11)
    assert where < text.index(SECTION_11_HEADING), (
        f"the fixture inserts at {where}, which is not above §11 — the reproduction is wrong")
    return text[:where] + blob + text[where:]


def _with_a_planted_grep(text):
    """Reproduction 1 — a narrowed `grep -icE` above §11, and a leak it cannot see."""
    return _insert_above_section_11(
        text,
        "How many pages name a device path:\n\n"
        "```bash\n"
        f"grep -icE '{PLANTED_REGEX}' factory/docs/product/SPEC.md   # expect 0\n"
        "```\n\n"
        f"{PLANTED_LEAK}\n\n")


def _with_the_leak_alone(text):
    """The control for reproduction 1: the identical leak, with no planted expression."""
    return _insert_above_section_11(text, f"{PLANTED_LEAK}\n\n")


def _with_a_restated_total(text):
    """§11's real check 3, restating the socket table's total — the defect check 3 exists for."""
    start = text.index("**3 · Every socket message states its trigger")
    fence = text.index("```bash", start)
    restated = "```bash\ngrep -c 'M row' factory/docs/product/SPEC.md   # expect 16\n```\n\n"
    return text[:fence] + restated + text[fence:]


def _with_a_shadow_pair(text, n):
    """Reproduction 2 — a `**n ·`/`**n+1 ·` pair above §11, shadowing check `n`'s anchor."""
    return _insert_above_section_11(
        text,
        f"**{n} · A worked example.** Three things a reader counts:\n\n"
        f"**{n + 1} · And the fourth.** Nothing here.\n\n")


def _refusal(case, document, monkeypatch):
    """Run a §11 check case — the real one — against `document`, and return what it said.

    `None` means the check accepted the document. The case is driven rather than restated: a
    negative that re-implements the assertion it is about is a negative about itself.
    """
    monkeypatch.setattr(sys.modules[__name__], "spec", lambda: document)
    try:
        case()
    except AssertionError as red:
        return str(red)
    return None


# ── criterion 1 · check 6's expression comes out of §11 ──────────────────────────────────────

def test_check_6s_regex_is_read_from_section_11_and_nowhere_else():
    """Check 6's expression is bounded to §11's check 6 block, and the bound is asserted.

    §11 calls check 6 *"the assertion the whole product rests on"*. `SRV-FEAT-304`'s criterion 4
    pinned it as *"§11's, character for character"* — true of the document as delivered, and a
    claim about that document rather than about the reader. This is the same claim moved onto the
    reader: whatever else the file holds, the expression comes from §11's check 6, or the check
    refuses to run.
    """
    text = spec()
    expression = vocabulary_regex(text)
    published = f"grep -icE '{expression}'"
    assert published in check_block(text, 6), (
        f"check 6's expression {expression!r} is not the one §11's check 6 publishes")
    assert _section_11(text).count(published) == 1, (
        "§11 publishes check 6's expression more than once, so 'the one it publishes' is ambiguous")

    # ...and a second `grep -icE` above §11 does not become it.
    planted = _with_a_planted_grep(text)
    assert vocabulary_regex(planted) == expression, (
        f"a `grep -icE` written above §11 became check 6: the expression read was "
        f"{vocabulary_regex(planted)!r}, and §11 publishes {expression!r}")

    # ...and the bound is asserted, not defaulted to the rest of the file. Two shapes, because a
    # bound nobody can find and a bound found twice are different failures, and the second is the
    # one a renumbering produces.
    unheaded = text.replace(SECTION_11_HEADING, "\n(the checks, unheaded)", 1)
    doubled = _insert_above_section_11(text, SECTION_11_HEADING.strip() + "\n\n")
    for shape, document in (("no §11 heading at all", unheaded), ("two §11 headings", doubled)):
        try:
            got = vocabulary_regex(document)
        except AssertionError:
            continue
        raise AssertionError(
            f"with {shape} the reader did not refuse — it returned {got!r} out of a document "
            f"whose §11 it cannot locate, which is the unbounded search wearing a hat")

    # ...and the bound is derived from the document's own headings, not from a line number: move
    # §11 five hundred lines down the file and it reads the same block.
    padded = "\n" * 500 + text
    assert vocabulary_regex(padded) == expression, (
        "padding above §11 changed which expression check 6 reads, so the bound is positional")
    assert check_block(padded, 3) == check_block(text, 3), (
        "padding above §11 changed check 3's block, so the bound is positional")


# ── criterion 2 · negative — a planted expression does not become check 6 ────────────────────

def test_a_grep_icE_written_above_section_11_does_not_become_check_6(monkeypatch):
    """MANDATORY NEGATIVE — reproduction 1 of `SRV-FIX-305` §1, run against a mutated copy.

    A `grep -icE '\\b([b]oard)\\b'` in a worked example above §11 narrows *the assertion the whole
    product rests on* to one word, and check 6 then reads green over a document naming `esp32`,
    `firmware`, `acme` and `roti`. The canary underneath check 6 does not fire, because the
    canary line contains `board` and the narrowed expression still matches it.
    """
    text = spec()
    assert _refusal(test_no_repository_specific_vocabulary_in_the_spec, text, monkeypatch) is None, (
        "the document itself is not green, so this negative proves nothing")

    # The fixture is the two halves disagreeing, asserted: the planted expression matches the
    # canary and misses the leak. An expression matching neither is caught by the canary alone.
    narrowed = re.compile(PLANTED_REGEX, re.I)
    assert narrowed.search("A lane may hold the board while the firmware ticket runs."), (
        "the planted expression does not match check 6's canary, so this fixture is round 1's")
    assert not narrowed.search(PLANTED_LEAK), (
        "the planted expression matches the leak, so the fixture cannot show a narrowing")

    red = _refusal(test_no_repository_specific_vocabulary_in_the_spec,
                   _with_a_planted_grep(text), monkeypatch)
    assert red is not None, (
        "check 6 accepted a SPEC.md naming esp32, firmware, acme and roti, because a `grep -icE` "
        "written above §11 became its definition")
    assert "esp32" in red, f"check 6 refused the document for some other reason:\n{red}"

    # The control: the identical leak with nothing planted IS caught, so this is about the
    # unbounded search and not about the leak being unfindable.
    control = _refusal(test_no_repository_specific_vocabulary_in_the_spec,
                       _with_the_leak_alone(text), monkeypatch)
    assert control is not None and "esp32" in control, (
        f"the control leak was not caught, so the negative above is about the leak: {control}")


# ── criterion 3 · negative — a numbered bold line above §11 does not shadow a check ──────────

def test_a_numbered_bold_line_above_section_11_does_not_shadow_a_check(monkeypatch):
    """MANDATORY NEGATIVE — reproduction 2 of `SRV-FIX-305` §1, run against a mutated copy.

    `check_block` anchors on the first `**3 · ` in the document. A `**3 ·`/`**4 ·` pair in a
    worked example above §11 becomes check 3, so a `# expect 16` restated inside §11's real check
    3 — the exact defect check 3 exists to catch — is never looked at.
    """
    text = spec()
    assert _refusal(test_the_socket_table_row_count_is_not_stated_twice, text, monkeypatch) is None, (
        "the document itself is not green, so this negative proves nothing")

    shadowed = _with_a_shadow_pair(_with_a_restated_total(text), 3)
    red = _refusal(test_the_socket_table_row_count_is_not_stated_twice, shadowed, monkeypatch)
    assert red is not None, (
        "check 3 accepted a §11 restating the socket table's total, because a `**3 ·`/`**4 ·` "
        "pair written above §11 became its block")
    assert "'16'" in red, f"check 3 refused the document for some other reason:\n{red}"

    # The control: the same restatement with no shadow IS caught, so the negative is about the
    # shadow and not about the restatement being unfindable.
    control = _refusal(test_the_socket_table_row_count_is_not_stated_twice,
                       _with_a_restated_total(text), monkeypatch)
    assert control is not None and "'16'" in control, (
        f"the control restatement was not caught, so the negative above proves nothing: {control}")


# ── criterion 4 · the anchoring property, once, for every check read by anchor ───────────────

# Every `check_block(<text>, <n>)` written in this module, taken from the module's own source. A
# seventh check read by anchor becomes a call site and inherits the property below without a case
# being written for it — which is the structural half of this ticket. A number typed into a tuple
# here would be bounded by the checks somebody had already thought of.
ANCHORED_READ = re.compile(r'check_block\(\s*\w+\s*,\s*(\d+)\s*\)')


def checks_read_by_anchor():
    return sorted({int(n) for n in ANCHORED_READ.findall(Path(__file__).read_text())})


def test_every_check_block_this_module_reads_is_inside_section_11():
    """Every check this module reads by anchor is read out of §11, and nothing above §11 moves it.

    The escape was two readers with the same defect, one of them half-guarded, so the property is
    asserted about the READER once rather than about each check by hand.
    """
    text = spec()
    section = _section_11(text)
    numbers = checks_read_by_anchor()
    assert numbers, (
        "no `check_block(text, n)` call site was found in this module's own source, so this case "
        "swept nothing — a check that sweeps nothing has not run")
    assert {3, 6} <= set(numbers), (
        f"§11's check 3 and check 6 are the two checks §1 names, and the checks read by anchor "
        f"are {numbers}. A check whose definition is not read as an anchored block of §11 is a "
        f"check still finding its own definition by first-match over the whole document.")

    complaints = []
    for n in numbers:
        block = check_block(text, n)
        shadowed = check_block(_with_a_shadow_pair(text, n), n)
        if not block.startswith(f"**{n} · "):
            complaints.append(
                f"check {n}'s block does not begin with its own heading: {block[:60]!r}")
        if block not in section:
            complaints.append(f"check {n}'s block was read from outside §11: {block[:60]!r}")
        if shadowed != block:
            complaints.append(
                f"a `**{n} ·`/`**{n + 1} ·` pair written above §11 became check {n}: the block "
                f"read was {shadowed[:60]!r}")
    assert complaints == [], "\n  " + "\n  ".join(complaints)


# ── SRV-FIX-342 · §5's prose refusals, compared against the server that serves them ───────────
#
# `SPEC.md` §5 `E31` said an unknown `before` cursor is refused with `404 not_found` and
# `detail.run_id`; the shipped server answers `400 bad_request` with `detail.param` and
# `detail.before`, which is what `SRV-FEAT-315` §1 asked for by name. Both were true for the life
# of that ticket and nothing in this repository looked at the pair: `test_spec.py` never asked the
# server anything, and `http.schema.json` records `E31`'s success node only, so the refusal lived
# in prose and a negative that lives only in prose is a negative nothing can check.
#
# What follows reads the statuses OUT OF THE DOCUMENT and drives each of them against a live
# server. No status, code or detail member is written down here: a check carrying `400` would
# agree with a server that had drifted to something else, and would have gone green against the
# `404` this file exists to have caught.

E31_HEADING = re.compile(r'^#### E31\b')
BLOCK_END = re.compile(r'^#{2,4} ')
BULLET = re.compile(r'^- \*\*Missing\*\*')
NEXT_BULLET = re.compile(r'^- ')
# `` `200` `` or `` `400 bad_request` `` — a status, optionally with the error code it carries.
DECLARED = re.compile(r'^(\d{3})(?:\s+([a-z_]+))?$')
BACKTICKED = re.compile(r'`([^`]+)`')
DETAIL_MEMBER = re.compile(r'detail\.([a-z_]+)')
# §5 writes every refusal as `<condition>: `<status>``. The colon is what makes a status span an
# ANSWER rather than a mention of one — `E28`, `E29` and `E31` all write it that way. A status
# span with no condition in front of it is prose, and prose is allowed to borrow another
# endpoint's status ONLY by naming that endpoint: `unattributed_statuses` below refuses the rest,
# so a refusal written without its colon cannot hide inside a sentence.
ANSWERS_A_CONDITION = re.compile(r':\s*$')
CROSS_REFERENCE = re.compile(r'`E\d+`')


def endpoint_block(spec_text):
    """The lines of `E31`'s `#### ` block, heading excluded.

    `E31` and not any endpoint on purpose: §3 of `SRV-FIX-342` says every other endpoint's prose
    negatives are the same class one endpoint at a time, worth their own ticket once the shape
    here is proven. A parameter nobody passes would claim the generalisation is already done.
    """
    lines = spec_text.splitlines()
    starts = [i for i, line in enumerate(lines) if E31_HEADING.match(line)]
    if not starts:
        return None
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if BLOCK_END.match(lines[i])), len(lines))
    return lines[start + 1:end]


def missing_bullet(spec_text):
    """The `- **Missing**` bullet of `E31`, as one string with its continuation lines joined.

    A bullet rather than a paragraph because that is how §5 writes every endpoint's refusals, and
    the continuation lines are part of the sentence: the status can sit on the line after the
    condition and regularly does.
    """
    block = endpoint_block(spec_text)
    if block is None:
        return None
    for i, line in enumerate(block):
        if not BULLET.match(line):
            continue
        body = [line]
        for nxt in block[i + 1:]:
            if NEXT_BULLET.match(nxt) or not nxt.strip():
                break
            body.append(nxt.strip())
        return " ".join(body)
    return None


def declared_answers(spec_text):
    """Every answer `E31`'s *Missing* bullet states, derived from the bullet itself.

    One entry per backticked status span, in the document's order:

        {"status": 400, "code": "bad_request", "condition": <prose before it>,
         "detail": {"param", "before"}, "span": "400 bad_request"}

    The condition is the prose since the previous span and the promise is the prose up to the
    next one, so a bullet that grows a third refusal grows a third entry with no edit here. This
    is the only place a status enters the check, and it enters it from the document.
    """
    bullet = missing_bullet(spec_text)
    if bullet is None:
        return None
    spans = []
    for match in BACKTICKED.finditer(bullet):
        declared = DECLARED.match(match.group(1).strip())
        if declared and ANSWERS_A_CONDITION.search(bullet[:match.start()]):
            spans.append((match, declared))
    answers = []
    for i, (match, declared) in enumerate(spans):
        condition_from = spans[i - 1][0].end() if i else 0
        promise_to = spans[i + 1][0].start() if i + 1 < len(spans) else len(bullet)
        answers.append({
            "status": int(declared.group(1)),
            "code": declared.group(2),
            "span": match.group(1).strip(),
            "condition": bullet[condition_from:match.start()],
            "promise": bullet[match.end():promise_to],
            "promise_span": (match.end(), promise_to),
            "detail": set(DETAIL_MEMBER.findall(bullet[match.end():promise_to])),
        })
    return answers


def unattributed_statuses(spec_text):
    """Status spans in the *Missing* bullet that answer no condition and name no other endpoint.

    `400` stays `E8`'s answer is a legitimate sentence in `E31`'s bullet; a bare status that
    answers nothing is a refusal somebody stated and nothing drives. The attribution is the other
    endpoint's own id, so the exemption cannot be claimed by prose in general.
    """
    bullet = missing_bullet(spec_text)
    if bullet is None:
        return []
    answers = declared_answers(spec_text)
    loose = []
    for match in BACKTICKED.finditer(bullet):
        if not DECLARED.match(match.group(1).strip()):
            continue
        if ANSWERS_A_CONDITION.search(bullet[:match.start()]):
            continue
        owner = [a for a in answers if a["promise_span"][0] <= match.start() < a["promise_span"][1]]
        if owner and CROSS_REFERENCE.search(owner[0]["promise"]):
            continue
        loose.append(match.group(1).strip())
    return loose


def mutate_e31(spec_text, old, new):
    """Replace `old` with `new` **inside `E31`'s block only**, and say if nothing moved.

    A `str.replace` over the whole document rewrites the first `` `400 bad_request` `` in §5,
    which belongs to another endpoint: the negative below then reads a document whose `E31` block
    is untouched and concludes the check is honest. Measured — it is why this helper exists.
    """
    lines = spec_text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if E31_HEADING.match(line)]
    if not starts:
        return spec_text, 0
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if BLOCK_END.match(lines[i])), len(lines))
    hits = 0
    for i in range(start, end):
        if old in lines[i]:
            lines[i] = lines[i].replace(old, new, 1)
            hits += 1
            break
    return "".join(lines), hits


# Which request puts the server in the state a clause describes. A predicate over the clause's
# own CONDITION TEXT, never over its position or its status: the bullet is prose and the order it
# happens to be written in is not a contract.
#
# Every clause must be claimed by exactly one of these. A clause nothing claims is a COMPLAINT and
# not a skip — that is what stops this check from quietly examining one of the two refusals, or
# none of a third somebody adds later.
DRIVERS = (
    ("empty_history", lambda condition: ".factory/runs/" in condition),
    ("unknown_cursor", lambda condition: "`before`" in condition),
)


def e31_missing_audit(spec_text, ask):
    """Every complaint against `spec_text`'s `E31` *Missing* bullet, given a live server to ask.

    `ask(kind)` returns the real HTTP response for one of `DRIVERS`' kinds. Returns a list of
    sentences; empty is green. A function rather than a run of asserts so that
    `test_the_check_fails_when_the_document_is_changed_under_it` can hand it a document nobody
    would commit and read what it says — the shape `audit()` above already uses.
    """
    complaints = []
    answers = declared_answers(spec_text)
    if answers is None:
        return ["SPEC.md has no `E31` block with a `- **Missing**` bullet in it — there is nothing "
                "to compare the server against"]
    if not answers:
        return ["`E31`'s *Missing* bullet states no status at all, so this check swept nothing"]
    for span in unattributed_statuses(spec_text):
        complaints.append(
            f"`E31`'s *Missing* bullet names `{span}`, which answers no condition and is not "
            f"attributed to another endpoint — a status this check cannot drive")

    for answer in answers:
        claimed = [kind for kind, matches in DRIVERS if matches(answer["condition"])]
        if len(claimed) != 1:
            complaints.append(
                f"`E31`'s *Missing* bullet declares `{answer['span']}` for a condition "
                f"{answer['condition'].strip()!r} that {len(claimed)} of this check's drivers "
                f"claim — a refusal nothing drives is a refusal nothing checks")
            continue
        kind = claimed[0]
        response = ask(kind)

        if response.status_code != answer["status"]:
            complaints.append(
                f"`E31` {kind}: SPEC.md says `{answer['span']}` and the server answers "
                f"{response.status_code} — {response.text.strip()[:200]}")

        try:
            body = response.json()
        except ValueError:
            complaints.append(f"`E31` {kind}: the server did not answer JSON — "
                              f"{response.text.strip()[:200]}")
            continue

        error = body.get("error")
        if answer["status"] < 400:
            if error is not None:
                complaints.append(
                    f"`E31` {kind}: SPEC.md states `{answer['span']}`, which is not a refusal, "
                    f"and the server sent an error envelope: {error}")
            if answer["detail"]:
                complaints.append(
                    f"`E31` {kind}: SPEC.md names {sorted(answer['detail'])} under a "
                    f"`{answer['span']}` that carries no error envelope to put them in")
            continue

        if error is None:
            complaints.append(
                f"`E31` {kind}: SPEC.md states `{answer['span']}` and the body carries no "
                f"`error` member: {json.dumps(body)[:200]}")
            continue

        if answer["code"] is not None and error.get("code") != answer["code"]:
            complaints.append(
                f"`E31` {kind}: SPEC.md names the code `{answer['code']}` and the server sent "
                f"`{error.get('code')}`")
        if answer["code"] is None:
            complaints.append(
                f"`E31` {kind}: SPEC.md states `{answer['span']}` and names no error code, so "
                f"the code the server sends (`{error.get('code')}`) is unstated")

        served = set(error.get("detail") or {})
        if served != answer["detail"]:
            missing = sorted(answer["detail"] - served)
            unstated = sorted(served - answer["detail"])
            complaints.append(
                f"`E31` {kind}: SPEC.md names detail {sorted(answer['detail'])} and the server "
                f"sends {sorted(served)}"
                + (f"; named and not served: {missing}" if missing else "")
                + (f"; served and not named: {unstated}" if unstated else ""))
    return complaints


@pytest.fixture
def e31_ask(tmp_path, serve):
    """A live server per `DRIVERS` kind, and the real HTTP call that reaches its refusal.

    Two repositories, because the two clauses describe two different states of one and a server
    cannot be in both: one with no `.factory/runs/` at all, one with runs and therefore a cursor
    that could have been honoured. The unknown cursor is asked of the populated one on purpose —
    against an empty history every cursor is unknown, and the refusal would be indistinguishable
    from the absence.
    """
    empty = plant_repo(tmp_path / "no-history", roles=ROLES_A, make_runs_dir=False)
    assert not (empty / ".factory" / "runs").exists()

    populated = plant_repo(tmp_path / "with-history", roles=ROLES_A)
    plant_run(populated, "run-1-a", "TKT-001", started="2026-08-30T09:00:00Z",
              ended="2026-08-30T09:01:00Z")
    plant_run(populated, "run-2-b", "TKT-002", started="2026-08-30T10:00:00Z",
              ended="2026-08-30T10:01:00Z")

    servers = {}

    def ask(kind):
        if kind == "empty_history":
            srv = servers.setdefault(kind, serve(empty))
            return srv.get("/api/runs")
        if kind == "unknown_cursor":
            srv = servers.setdefault(kind, serve(populated))
            # the same server honours a cursor it HAS, so the refusal below is about the value
            assert srv.get("/api/runs?before=run-2-b").status_code == 200
            return srv.get("/api/runs?before=run-9-nothing")
        raise AssertionError(f"no driver for {kind!r}")

    return ask


# ── 1 · the status the document names is the status the server answers ───────────────────────

def test_spec_and_the_server_agree_on_e31s_unknown_cursor(e31_ask):
    """§5 `E31`'s *Missing* bullet and the running server, on an unknown `before` cursor.

    The status is read out of the document. A case asserting `400` on both sides would go green
    against a `SPEC.md` still saying `404`, which is where this ticket started.
    """
    answers = declared_answers(spec())
    assert answers, "`E31`'s *Missing* bullet states no status — nothing was compared"

    cursor = [a for a in answers if "`before`" in a["condition"]]
    assert len(cursor) == 1, (
        f"the bullet states {len(cursor)} answers for an unknown `before` cursor, not one: "
        f"{[a['span'] for a in answers]}")
    declared = cursor[0]

    served = e31_ask("unknown_cursor")
    assert served.status_code == declared["status"], (
        f"SPEC.md §5 E31 says `{declared['span']}` for an unknown cursor and the server answers "
        f"{served.status_code}: {served.text.strip()}")


# ── 2 · and so are the code and the detail members ───────────────────────────────────────────

def test_the_error_code_and_detail_key_agree_too(e31_ask):
    """A status that matches while `detail.run_id` is absent is the same defect one field over."""
    answers = declared_answers(spec())
    declared = next(a for a in answers if "`before`" in a["condition"])

    assert declared["code"] is not None, (
        f"the bullet states `{declared['span']}` and names no error code, so nothing says which "
        f"code a client should branch on")
    assert declared["detail"], (
        f"the bullet states `{declared['span']}` and names no `detail.<member>`, so a refusal "
        f"that named the wrong parameter would satisfy it")

    error = e31_ask("unknown_cursor").json()["error"]
    assert error["code"] == declared["code"], (
        f"SPEC.md names `{declared['code']}` and the server sent `{error['code']}`")
    assert set(error.get("detail") or {}) == declared["detail"], (
        f"SPEC.md names detail {sorted(declared['detail'])} and the server sends "
        f"{sorted(error.get('detail') or {})}")


# ── 3 · negative (mandatory) — the check reads the document, or it reads nothing ──────────────

def test_the_check_fails_when_the_document_is_changed_under_it(e31_ask):
    """MANDATORY NEGATIVE — rewrite the bullet to something the server does not answer.

    Without this, criterion 1 could pass by reading nothing and comparing nothing. Both halves of
    the sentence are mutated separately, because a check that reads the status out of the document
    and the detail members out of its own memory is half a check.
    """
    text = spec()
    assert e31_missing_audit(text, e31_ask) == [], (
        "this case is about the checker's refusal; the document itself must be green first")

    answers = declared_answers(text)
    declared = next(a for a in answers if "`before`" in a["condition"])

    mutated, hits = mutate_e31(text, f"`{declared['span']}`", "`418 im_a_teapot`")
    assert hits == 1 and mutated != text, (
        f"the mutation changed nothing in `E31`'s block — `{declared['span']}` is not in it")
    complaints = e31_missing_audit(mutated, e31_ask)
    assert complaints, (
        "a SPEC.md declaring a status the server does not answer is accepted, so criterion 1 "
        "cannot fail and is not a criterion")
    assert any("418" in c for c in complaints), (
        f"the refusal does not name the status that was injected: {complaints}")

    for member in sorted(declared["detail"]):
        renamed, hits = mutate_e31(text, f"detail.{member}", "detail.nobody_sends_this")
        assert hits == 1 and renamed != text, (
            f"`detail.{member}` is not in `E31`'s block")
        complaints = e31_missing_audit(renamed, e31_ask)
        assert complaints, (
            f"a SPEC.md naming `detail.nobody_sends_this` in place of `detail.{member}` is "
            f"accepted, so the detail half of criterion 2 is not a criterion")
        assert any("nobody_sends_this" in c for c in complaints), (
            f"the refusal does not name the member that was injected: {complaints}")


# ── 4 · every answer the bullet states is driven, not just the one this ticket is about ───────

def test_every_negative_e31_declares_is_reachable(e31_ask):
    """Each answer `E31`'s *Missing* bullet states, driven against the live server.

    The sweep is derived from the bullet, so a refusal added to the document with no driver here
    fails this case instead of passing unexamined.
    """
    answers = declared_answers(spec())
    assert len(answers) >= 2, (
        f"`E31`'s *Missing* bullet states {len(answers)} answers; §5 states two — an absent "
        f"history and an unknown cursor: {[a['span'] for a in answers]}")

    driven = []
    for answer in answers:
        claimed = [kind for kind, matches in DRIVERS if matches(answer["condition"])]
        assert len(claimed) == 1, (
            f"`{answer['span']}` for {answer['condition'].strip()!r} is claimed by {claimed} — "
            f"a declared refusal nothing drives is a refusal nothing checks")
        served = e31_ask(claimed[0])
        assert served.status_code == answer["status"], (
            f"`E31` {claimed[0]}: SPEC.md says `{answer['span']}` and the server answers "
            f"{served.status_code}: {served.text.strip()[:200]}")
        driven.append(claimed[0])

    assert sorted(driven) == sorted(kind for kind, _ in DRIVERS), (
        f"only {sorted(set(driven))} of this check's drivers were reached")
    assert e31_missing_audit(spec(), e31_ask) == []

    # `SRV-FEAT-315`'s own two cases are the per-endpoint half of the same claim and stay green;
    # a rename would leave this ticket's gate quoting cases nobody runs.
    import test_runs_index
    for case in ("test_no_runs_at_all_is_an_empty_list_and_not_an_error",
                 "test_a_before_cursor_naming_a_run_that_does_not_exist_is_refused"):
        assert callable(getattr(test_runs_index, case, None)), (
            f"{case} is not in tests/test_runs_index.py")


# ── SRV-FEAT-501 · §5 E32, §4.21 and the module's own design note ─────────────────────────────

DESIGN_METRICS = SERVER / "design" / "metrics.md"


def e32_numbers():
    """The names `E32` puts on the wire, read off `http.schema.json` and never typed here.

    A set somebody typed is bounded by what they thought of on the day (`_TEMPLATE.md` §2, the
    first way a check lies). A field added to the endpoint tomorrow is a field this case demands
    a sentence for tomorrow, with no edit to this file.
    """
    schema = json.loads(SCHEMA.read_text())
    node = schema["responses"]["E32"]
    return sorted(node["required"])


def section(text, heading):
    """One `###`/`####` block of SPEC.md, from its heading to the next of the same depth."""
    depth = heading.split(" ", 1)[0]
    found = re.search(rf"^{re.escape(heading)}.*?(?=^{re.escape(depth)} |\Z)", text,
                      re.M | re.S)
    return found.group(0) if found else ""


def test_e32_is_specified_with_its_derivations():
    """§1.6 — `E32` is in §5's table AND specified; §4.21 names every number and where it comes
    from; `server/design/metrics.md` says why a rate is `null` on nothing measured and why the
    script is the arbiter.

    The sweep is the schema's own `required` list, so it cannot be satisfied by documenting the
    fields whoever wrote this case remembered. And the negative below is a document with the
    section present and one number undocumented — the shape a field-by-field check must catch,
    and the shape a check that only asserted *§4.21 exists* passes in silence.
    """
    text = spec()
    numbers = e32_numbers()
    assert len(numbers) >= 8, f"E32 declares only {numbers} — the sweep has stopped seeing it"

    assert re.search(r"^\| E32 \| GET \| `/api/metrics` \|", text, re.M), (
        "§5's table has no E32 row, so the endpoint is reachable by name and not by reading")
    block = section(text, "#### E32 · GET `/api/metrics`")
    assert block, "§5 specifies no E32 block"
    for field in ("Request", "Response", "Missing"):
        assert f"**{field}**" in block, f"E32's block states no {field}"

    surface = section(text, "### 4.21")
    assert surface, "§4 has no 4.21 — the screen E32 exists for is unspecified"
    assert re.search(r"\*\*Data\*\*.*?`E32`", surface, re.S), (
        "§4.21 names no data source; a screen with none is a screen somebody invents an "
        "endpoint for")

    undocumented = [n for n in numbers if f"`{n}`" not in surface]
    assert undocumented == [], (
        f"§4.21 draws numbers it does not name: {undocumented}. Every member of E32's answer is "
        f"a tile, and a tile whose derivation is not beside it is a number people argue about "
        f"instead of act on.")

    # the negative: the same predicates over a §4.21 that documents all but one of them
    planted = surface.replace(f"`{numbers[0]}`", "`something_else`")
    assert [n for n in numbers if f"`{n}`" not in planted] == [numbers[0]], (
        "the sweep cannot tell a documented number from an undocumented one, so the green "
        "above is about the expression and not about the document")

    note = DESIGN_METRICS.read_text() if DESIGN_METRICS.exists() else ""
    assert note, f"{DESIGN_METRICS} does not exist"
    assert "measure-rounds.py" in note, (
        "the design note does not name the recorded arbiter the endpoint is checked against")
    assert re.search(r"null", note) and re.search(r"\bnothing was measured\b", note), (
        "the design note does not say why a rate is `null` rather than `0` when nothing was "
        "measured — which is the one decision a reader of E32 cannot derive from the code")


# ── SRV-FEAT-500 · this repository's own REVIEW, and the stage that runs it ───────────────────

REVIEW_DOC = FACTORY / "docs/technical/workflow/REVIEW.md"
REVIEW_SH = FACTORY / ".claude" / "scripts" / "review.sh"
RUN_TICKET_SKILL = FACTORY / ".claude" / "skills" / "run-ticket" / "SKILL.md"
WORKFLOW = FACTORY / "docs/technical/workflow/WORKFLOW.md"
LANE_DESIGN = FACTORY / "server" / "design" / "lane.md"
FACTORY_MANIFEST = FACTORY / ".factory" / "factory.json"
REVIEW_CLI_FIXTURE = (Path(__file__).resolve().parent / "fixtures"
                      / "cli-review-2026-09-11-review-sh-prompt-to-findings.json")

# What a finding looks like on stdout, per §1's Pre-decided: `blocker|minor <file>:<line> <one
# sentence>`, so a report can be grepped for the severities `CLAUDE.md` already defines.
FINDING = re.compile(r'^(blocker|minor) (\S+:\d+) (\S.*)$')


def severity_definitions():
    """The two severities as `CLAUDE.md` writes them — read from the table, never retyped.

    Criterion 4 asks for `CLAUDE.md`'s definitions **quoted verbatim** in `docs/technical/workflow/REVIEW.md`. A
    definition typed into this file would be a third copy, and three copies is how the two that
    matter drift apart without anything going red.
    """
    text = (FACTORY / "CLAUDE.md").read_text()
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] in ("`blocker`", "`minor`"):
            out[cells[0].strip("`")] = cells[1]
    return out


def declaring_workspaces():
    """Every workspace in this repository's manifest that declares any command at all.

    Derived from the manifest, not from the pair a person would type: the question criterion 4
    asks is whether a workspace that has a `build` and a `gate` also has a `review`, and the set
    of such workspaces is a fact about the file.
    """
    manifest = json.loads(FACTORY_MANIFEST.read_text())
    return {name: w for name, w in manifest["workspaces"].items()
            if any(k in w for k in ("build", "gate", "review"))}


def _stand_in_claude(directory, log):
    """A `claude` that records what it was handed and answers with four findings.

    Two blockers and two minors, INTERLEAVED and each pair disagreeing about position, so the
    ordering criterion 4 asks for is asserted on a population that does not already agree with it
    (§2: a reduction over a collection whose members agree is the identity).

    It is not written from what this repository believes the CLI does. `argv` and `rc` are checked
    against `fixtures/cli-review-2026-09-11-…json`, a recording of the real
    `claude --model claude-opus-5 -p` doing this exact job, by
    `test_this_repository_declares_a_review_per_workspace` before the stand-in's output is trusted.
    """
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "claude"
    p.write_text(
        "#!/bin/sh\n"
        f'log="{log}"\n'
        'printf "argv:" >> "$log"\n'
        'for a in "$@" ; do printf " %s" "$a" >> "$log" ; done\n'
        'printf "\\n" >> "$log"\n'
        'echo "--- stdin ---" >> "$log"\n'
        'cat >> "$log"\n'
        'echo "minor client/web/src/services/http.ts:252 the third command has no button yet."\n'
        'echo "blocker server/app/lane/command.cpp:63 the enum lists two names, not three."\n'
        'echo "minor docs/technical/workflow/WORKFLOW.md:58 the stage table still describes the stage by hand."\n'
        'echo "blocker server/app/manifest/store.cpp:921 a declared command is not in the set."\n'
        "exit 0\n")
    p.chmod(0o755)
    return p


def _repo_with_a_diff(tmp_path):
    """A git repository with a base branch and a commit on top — what `review.sh` reviews."""
    root = tmp_path / "reviewed"
    root.mkdir(parents=True, exist_ok=True)

    def g(*args):
        return subprocess.run(["git", "-C", str(root)] + list(args),
                              capture_output=True, text=True, check=True)

    g("init", "-b", "main")
    g("config", "user.email", "factory@test.local")
    g("config", "user.name", "factory tests")
    (root / "kept.txt").write_text("one\n")
    g("add", "-A")
    g("commit", "-m", "base")
    g("checkout", "-b", "ticket/TCK-500")
    (root / "kept.txt").write_text("one\ntwo\n")
    g("add", "-A")
    g("commit", "-m", "TCK-500: the change under review")
    return root


@pytest.mark.repository
def test_this_repository_declares_a_review_per_workspace(tmp_path, serve):
    """Criterion 4 — the document, the script, and both declarations.

    The script is not read, it is RUN: a real repository with a real diff, a stand-in `claude`
    first on `PATH`, and the four findings it prints are judged where they land. `SRV-FEAT-009`
    is why the stand-in itself is checked against a recording of the real CLI before a single
    thing it printed is believed.
    """
    # ── the document ─────────────────────────────────────────────────────────────────────────
    assert REVIEW_DOC.exists(), (
        f"{REVIEW_DOC} does not exist, so the command both workspaces declare has no "
        f"instructions to read")
    doc = REVIEW_DOC.read_text()

    passes = {
        "the seams": "docs/technical/MODEL.md",
        "the four ways a check lies": "docs/tickets/_TEMPLATE.md",
        "the server's architecture rules": "server/CLAUDE.md",
        "the client's generated types and no-poll rule": "client/web",
    }
    for what, path in passes.items():
        assert (FACTORY / path).exists(), (
            f"the pass named {what!r} is asserted against {path}, which is not in this "
            f"repository — the assertion below would be about nothing")
        assert path in doc, (
            f"docs/technical/workflow/REVIEW.md declares no pass over {path} ({what}), so a review run against it "
            f"cannot find what §1 says a review is for:\n{doc[:800]}")
    for word in ("no-poll", "generated"):
        assert word in doc, f"docs/technical/workflow/REVIEW.md never mentions {word!r}: {doc[:800]}"

    severities = severity_definitions()
    assert set(severities) == {"blocker", "minor"}, (
        f"CLAUDE.md's severity table did not parse into two rows: {severities}")
    for name, definition in severities.items():
        assert definition in doc, (
            f"docs/technical/workflow/REVIEW.md does not carry CLAUDE.md's definition of `{name}` verbatim.\n"
            f"CLAUDE.md: {definition}\n"
            f"Two definitions of one word is how a review starts grading against a scale "
            f"nobody else is using.")

    for excluded in ("generated", "docs/reports/"):
        assert excluded in doc, (
            f"docs/technical/workflow/REVIEW.md states no exclusion for {excluded!r}, so every regenerated type and "
            f"every committed log is diff a review will spend its findings on")

    # ── the stand-in, judged against the real CLI before anything it says is used ─────────────
    assert REVIEW_CLI_FIXTURE.exists(), (
        f"{REVIEW_CLI_FIXTURE.name} is missing. The stand-in below is a program written by the "
        f"session that wrote review.sh, and without the recording it can only agree with it "
        f"(SRV-FEAT-009).")
    recorded = json.loads(REVIEW_CLI_FIXTURE.read_text())
    assert recorded["rc"] == 0, recorded
    assert recorded["argv_tail"], recorded

    # ── the script, executed ─────────────────────────────────────────────────────────────────
    assert REVIEW_SH.exists(), f"{REVIEW_SH} does not exist"
    assert os.access(REVIEW_SH, os.X_OK), f"{REVIEW_SH} is not executable"

    reviewed = _repo_with_a_diff(tmp_path)
    shutil.copytree(FACTORY / ".claude", reviewed / ".claude")
    (reviewed / "docs/technical/workflow").mkdir(parents=True, exist_ok=True)
    shutil.copy(REVIEW_DOC, reviewed / "docs/technical/workflow/REVIEW.md")

    log = tmp_path / "claude.log"
    stand_in = _stand_in_claude(tmp_path / "bin", log)
    env = dict(os.environ, PATH=f"{stand_in.parent}:{os.environ['PATH']}")
    ran = subprocess.run(["sh", ".claude/scripts/review.sh", "main"],
                         cwd=reviewed, env=env, capture_output=True, text=True, timeout=120)

    assert ran.returncode == 0, (
        f"review.sh exited {ran.returncode}. Findings do not approve or block, so the script "
        f"that produces them exits 0 always:\n{ran.stdout}\n{ran.stderr}")

    handed = log.read_text()
    argv_line = next((l for l in handed.splitlines() if l.startswith("argv:")), "")
    assert argv_line, f"review.sh never invoked `claude` at all:\n{handed}"
    for flag in ("--model", "claude-opus-5", "-p"):
        assert flag in argv_line, (
            f"review.sh built {argv_line!r}, which does not carry {flag!r}. The model rule is "
            f"not negotiable and the recording says the real CLI took "
            f"{recorded['argv_tail']}.")
    assert set(recorded["argv_tail"]) <= set(argv_line.split()[1:]), (
        f"review.sh's argv {argv_line.split()[1:]} is not the argv the real CLI was recorded "
        f"accepting: {recorded['argv_tail']}")

    prompt = handed.split("--- stdin ---", 1)[1] if "--- stdin ---" in handed else ""
    assert "docs/technical/MODEL.md" in prompt, (
        f"the prompt review.sh built does not carry REVIEW.md's own text:\n{prompt[:600]}")
    assert "TCK-500: the change under review" in prompt or "+two" in prompt, (
        f"the prompt review.sh built does not carry `git diff main...HEAD`:\n{prompt[:600]}")

    # A base that does not resolve is a review that DID NOT RUN. Round 2 of this ticket's own
    # REVIEW found the script reporting it as "nothing to review" at exit 0 — the same
    # silence-reads-as-clean defect one line above the place it had just been fixed.
    no_base = subprocess.run(["sh", ".claude/scripts/review.sh", "no-such-base"],
                             cwd=reviewed, env=env, capture_output=True, text=True, timeout=120)
    assert no_base.returncode != 0, (
        f"review.sh was given a base that does not exist and exited {no_base.returncode} with "
        f"stdout {no_base.stdout!r} — indistinguishable from a diff with nothing wrong in it")
    assert "DID NOT RUN" in no_base.stderr, (
        f"the refusal does not say the review did not run: {no_base.stderr!r}")

    findings = [FINDING.match(l) for l in ran.stdout.splitlines() if FINDING.match(l)]
    assert len(findings) == 4, (
        f"review.sh printed {len(findings)} findings from a stand-in that emitted four:\n"
        f"{ran.stdout}")
    order = [m.group(1) for m in findings]
    assert order == ["blocker", "blocker", "minor", "minor"], (
        f"review.sh printed {order}. The stand-in emitted minor, blocker, minor, blocker — so a "
        f"script that printed what it was given, or sorted by anything but severity, produces "
        f"exactly this failure.")

    # ── both declarations, and E2 on this repository showing them ────────────────────────────
    declaring = declaring_workspaces()
    assert len(declaring) >= 2, (
        f"this repository's manifest declares commands on {sorted(declaring)}; a set of one "
        f"cannot show that a review is declared per workspace")
    missing = sorted(n for n, w in declaring.items() if "review" not in w)
    assert not missing, (
        f"{missing} declare a build or a gate and no review, so the REVIEW stage has no command "
        f"to run in them")

    # `E2` is asked about THIS repository's own manifest, byte-for-byte, from a root the case
    # controls. A toolchain path this machine does not have — `/opt/google/chrome`, `~/.nvm` in a
    # lane's empty home — makes the whole manifest `422 invalid_manifest` before a single
    # workspace is echoed, which is `SRV-FIX-504`: the factory's own manifest does not load from
    # inside a lane. So the toolchain ARRAYS, and nothing else, are pointed at a directory that
    # exists, and the case then asserts that the two documents differ in nothing but those arrays.
    # A case that only ran where Chrome is installed would be a case reporting its environment.
    declared_text = json.loads(FACTORY_MANIFEST.read_text())
    here = tmp_path / "as-this-repository"
    (here / ".factory").mkdir(parents=True, exist_ok=True)
    stand_in_toolchain = tmp_path / "toolchain"
    stand_in_toolchain.mkdir(exist_ok=True)

    served_doc = json.loads(json.dumps(declared_text))
    for w in served_doc["workspaces"].values():
        if "toolchain" in w:
            w["toolchain"] = [str(stand_in_toolchain)]
    changed = [f"workspaces.{n}.{k}"
               for n, w in served_doc["workspaces"].items()
               for k in w
               if w[k] != declared_text["workspaces"][n][k]]
    assert all(c.endswith(".toolchain") for c in changed), (
        f"the manifest served here differs from this repository's in {changed}, which is more "
        f"than the toolchain arrays this case is allowed to move")
    (here / ".factory" / "factory.json").write_text(json.dumps(served_doc, indent=2))
    for key in ("epics", "tickets", "reports"):
        (here / declared_text["docs"][key]).mkdir(parents=True, exist_ok=True)
    # Everything else the manifest POINTS AT — the role prompts, the wiki, the ledger — is copied
    # or created so the only thing that can refuse this load is the manifest's own content.
    shutil.copytree(FACTORY / ".factory" / "prompts", here / ".factory" / "prompts",
                    dirs_exist_ok=True)
    for key in ("wiki", "escapes"):
        target = declared_text["docs"].get(key)
        if not target:
            continue
        dest = here / target
        if (FACTORY / target).is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("")

    # Prepare every declared profile's home-relative credentials, without editing the manifest
    # or copying host credentials. A lane's home is an empty tmpfs, including for Codex.
    home = tmp_path / "home"
    profiles = list(declared_text.get("agents", {}).values())
    if "agent" in declared_text:
        profiles.append(declared_text["agent"])
    assert profiles, "the repository declares no agent profile"
    for profile in profiles:
        credentials = profile.get("credentials_dir", "~/.claude")
        assert credentials.startswith("~/"), credentials
        (home / credentials[2:]).mkdir(parents=True, exist_ok=True)
    srv = serve(here, HOME=str(home))
    r = srv.get("/api/manifest")
    assert r.status_code == 200, (
        f"this repository's own manifest did not load: {r.status_code} {r.text}")
    served = r.json()["workspaces"]
    for name, w in declaring.items():
        assert served.get(name, {}).get("review") == w["review"], (
            f"E2 does not echo {name}'s review: {served.get(name)}\n"
            f"`.factory/factory.json` declares {w['review']!r}")


@pytest.mark.repository
def test_the_review_stage_names_the_command_it_runs(tmp_path, serve):
    """Criterion 5 — the stage is the command, and the skill's own fenced line is the evidence.

    The line is not read for keywords. It is lifted out of `SKILL.md`, pointed at a live server
    holding a real lane, and RUN — so a fenced command that has drifted from the endpoint fails
    here rather than in the next agent's session (§2: the test must execute the PRODUCT, and the
    skill's own fenced commands are the product here).
    """
    skill = RUN_TICKET_SKILL.read_text()

    fenced = [l.strip() for l in skill.splitlines()
              if ".claude/scripts/api.sh" in l and "/commands" in l]
    assert fenced, (
        "run-ticket/SKILL.md's REVIEW stage does not run the declared command through "
        "`.claude/scripts/api.sh`, so the stage is still whatever the session decides it is")
    assert any('"which":"review"' in l or '"which": "review"' in l for l in fenced), (
        f"the skill's command does not ask for `review`: {fenced}")

    assert "REVIEW" in skill and "review failed" in skill, (
        "the skill never says that a non-zero review is a failed review; a report is then free "
        "to read a `127` as a review with no findings")
    for owed in ("blocker", "minor", "/api/tickets/ids"):
        assert owed in skill, (
            f"run-ticket/SKILL.md's REVIEW stage does not say what happens to a {owed!r}: a "
            f"blocker is fixed before handover and a minor is filed with an id from the tool")

    # ── the fenced line, executed against a live lane ────────────────────────────────────────
    from test_lane import build_repo, git
    root = build_repo(tmp_path / "target",
                      workspaces={"core": {"path": ".", "review": "sh ./review.sh"}})
    script = root / "review.sh"
    script.write_text("#!/bin/sh\necho 'minor a.cpp:1 nothing much'\n")
    script.chmod(0o755)
    git(root, "add", "-A")
    git(root, "commit", "-m", "review script")
    srv = serve(root)

    claimed = srv.post("/api/lanes", json={"ticket_id": "TCK-001", "workspace": "core"})
    assert claimed.status_code == 201, claimed.text
    lane = claimed.json()["lane"]

    line = next(l for l in fenced
                if '"which":"review"' in l or '"which": "review"' in l)
    command = line.replace("<lane>", lane).replace("{lane}", lane)
    ran = subprocess.run(["sh", "-c", command], cwd=FACTORY, timeout=60,
                         env=dict(os.environ, FACTORY_PORT=str(srv.port)),
                         capture_output=True, text=True)
    assert ran.returncode == 0, f"{command!r} failed:\n{ran.stdout}\n{ran.stderr}"
    answered = json.loads(ran.stdout)
    assert answered.get("which") == "review", (
        f"the skill's own fenced command did not start a review: {ran.stdout}")
    assert answered.get("command") == "sh ./review.sh", answered

    # ── the two documents the stage is written down in ───────────────────────────────────────
    workflow = WORKFLOW.read_text()
    stage_row = next((l for l in workflow.splitlines()
                      if l.startswith("| `REVIEW`")), "")
    assert stage_row, "docs/technical/workflow/WORKFLOW.md has no REVIEW row in the stage table"
    assert "review" in stage_row and ("declared" in stage_row or "E19" in stage_row), (
        f"the REVIEW row still describes the stage rather than naming the command the tool "
        f"runs: {stage_row}")
    assert "REVIEW stage is doing that work by hand" not in workflow, (
        "docs/technical/workflow/WORKFLOW.md still says the REVIEW stage does the client's convention work BY HAND, "
        "which is the sentence this ticket replaces")

    design = LANE_DESIGN.read_text()
    assert "closed set" in design and "the repository's" in design, (
        "server/design/lane.md does not record that a declared command is one of a closed set "
        "the tool names and that what each does is the repository's — the rule that keeps a "
        "fourth command from becoming a fourth branch")


# ── SRV-FEAT-499 · the freeze is written down where the request is drawn ─────────────────────

DESIGN = SERVER / "design" / "permissions.md"
SOCKET_SCHEMA_PATH = FACTORY / "docs/technical/contracts/socket.schema.json"
THIS_MANIFEST = FACTORY / ".factory" / "factory.json"


def test_the_freeze_is_specified_where_the_request_is_drawn():
    """§1 criterion 7. Three documents, and each one is where a different reader looks.

    A person deciding whether the answer should have been `deny` reads `server/design/permissions.md`;
    a person drawing the request reads `SPEC.md` §4.12 and has to know what the extra member means;
    and a repository that wants the freeze at all has to say so in its own manifest, which is the
    only door this fact may come through. The manifest half is not decoration — with no `evidence`
    block here, every case this ticket wrote passes and **the next ticket run in this repository is
    not frozen**, which is the whole of what §1 asked for.
    """
    design = DESIGN.read_text()
    assert "fourth input" in design, (
        "server/design/permissions.md does not record where the freeze sits among the inputs to "
        "the decision, so the ORDER — the thing that decides whether a grant can answer a frozen "
        "request — lives only in the code")
    assert re.search(r"\basked\b.*\bnever\b.*\bdeny\b", design, re.S | re.I), (
        "the design does not say that the freeze is `asked` and never `deny`; a refusal and a "
        "question are different products and the difference is the person who wrote the criterion")

    # §4.12 is where the request is drawn, so it is where the extra member is specified.
    text = spec()
    block = text[text.index("### 4.12"):]
    block = block[:block.index("\n### ")]
    assert "frozen_by" in block, (
        f"SPEC.md §4.12 never mentions `frozen_by`, so the surface that draws a permission "
        f"request has nothing that says what the field means:\n{block[:800]}")

    # ...and the frame it draws really does carry it, optionally.
    frame = json.loads(SOCKET_SCHEMA_PATH.read_text())["$defs"]["message"]["permission.request"]
    assert "frozen_by" in frame["properties"], (
        "§4.12 specifies a member `socket.schema.json` does not declare, so the client's "
        "generated type does not have it")
    assert "frozen_by" not in frame["required"], (
        "`frozen_by` is required, so every request that was not frozen is now invalid against "
        "the contract the browser gate validates the real server against")

    # §9 carries both manifest members, in both directions — check 7's subject.
    schema = json.loads(MANIFEST_SCHEMA.read_text())
    assert "evidence" in schema["properties"], "the manifest schema declares no `evidence` block"

    # This repository declares it, so its own next ticket run is frozen.
    mine = json.loads(THIS_MANIFEST.read_text())
    assert "evidence" in mine, (
        f"{THIS_MANIFEST} declares no `evidence` block, so nothing this repository runs is ever "
        f"frozen and the feature ships switched off in the one repository that asked for it")
    assert mine["evidence"]["frozen_after"] in mine["stages"], (
        f"this repository freezes after {mine['evidence']['frozen_after']!r}, which is not one of "
        f"the stages it declares: {mine['stages']}")
    assert mine["evidence"]["line_prefix"], "the declared line prefix is empty"


# ── SRV-FEAT-498 · the ledger eval, named where a session will meet it ─────────────────────────

CLAUDE_MD = FACTORY / "CLAUDE.md"
DOCGATE_DESIGN = SERVER / "design" / "docgate.md"
THE_LEDGER_EVAL = "SRV-FEAT-498"
LEDGER_EVAL_CASE = "test_every_case_the_ledger_names_is_collected"


def ledger_eval_sentences(text):
    """The lines of a document that tie a process change to the ledger eval.

    A line qualifies only if it carries BOTH halves — the thing that changed (a process file, a
    skill, `CLAUDE.md`, the template, the manifest) and what is then run (the ledger, the escapes,
    the eval). Either half alone is a document that mentions two subjects in one paragraph, which
    is the state `WEB-FIX-370` is about: *the rule was written in three places and enforced in
    none*.
    """
    changed = re.compile(r"process change|process file|`\.claude/|skill|_TEMPLATE|manifest", re.I)
    runs = re.compile(r"ledger|ESCAPES\.md|eval", re.I)
    return [l.strip() for l in text.splitlines() if changed.search(l) and runs.search(l)]


def test_the_workflow_names_the_ledger_eval():
    """Criterion 5 of `SRV-FEAT-498`. A session that edits the process is TOLD what now runs.

    A person editing a skill or `CLAUDE.md` reads `docs/technical/workflow/WORKFLOW.md`'s GATE row and `CLAUDE.md`'s
    document-gate sentence, and both say that a change to a process file runs the ledger eval and
    which ticket that is. `server/design/docgate.md` carries the rule itself, so the reasoning is
    beside the module rather than in the source. A rule written in prose and enforced nowhere is
    the 2026-09-08 row of the ledger; a rule enforced and written nowhere is a gate nobody can
    obey. Both halves, or neither counts.
    """
    workflow = WORKFLOW.read_text()
    components = gate_components(workflow)
    assert components, f"{WORKFLOW} declares no component with a GATE stage — nothing was swept"

    gate_rows = [line for _t, body, stages in components
                 for stage, line in document_gate_mentions(body, stages) if stage == "GATE"]
    assert gate_rows, f"{WORKFLOW} names the document gate under no GATE stage"

    for path, text in ((WORKFLOW, workflow), (CLAUDE_MD, CLAUDE_MD.read_text())):
        said = ledger_eval_sentences(text)
        assert said, (
            f"{path} never says that a change to a process file runs the ledger eval. A session "
            f"editing `.claude/` or `CLAUDE.md` is not told that {LEDGER_EVAL_CASE} now decides "
            "whether its change lands.")
        assert any(THE_LEDGER_EVAL in l for l in said), (
            f"{path} says it and names no ticket, so nothing points at the round that decided "
            f"it:\n  " + "\n  ".join(said))

    design = DOCGATE_DESIGN.read_text()
    assert "eval" in design.lower() and "ESCAPES.md" in design, (
        f"{DOCGATE_DESIGN} does not carry the rule — a ledger row's named case is an eval, and a "
        "process change is gated on the whole set. `CLAUDE.md` puts the reasoning in "
        "`server/design/<module>.md` and not in the source.")
    assert THE_LEDGER_EVAL in design, f"{DOCGATE_DESIGN} does not name {THE_LEDGER_EVAL}"

    # NEGATIVE: the two halves are load-bearing separately. A document naming the ledger with no
    # process change in the line, or a process change with no ledger, satisfies neither.
    assert ledger_eval_sentences("a process change is named by the gate\n") == []
    assert ledger_eval_sentences("the ledger is read on every run\n") == []
    assert ledger_eval_sentences("a process change runs the ledger eval\n") != []


# ── SRV-FIX-525 · what a copy of "this repository" is ──────────────────────────────────────────

THE_COPY_FIX = "SRV-FIX-525"

# §1's own three claims, each as the PAIR of halves that has to appear on one line. Either half
# alone is a document that mentions two subjects in one paragraph — the state `WEB-FIX-370` is
# about — so the pair is what is searched for, never the sentence this ticket happened to write.
A_COPY_OF_THE_REPOSITORY = {
    "a copy of this repository is what git tracks, derived from the index": (
        re.compile(r"git ls-files|git's index|the index\b", re.I),
        re.compile(r"cop(y|ies|ied)|placement", re.I)),
    "a typed exclusion list is the first way a check lies": (
        re.compile(r"typed|exclusion (set|list)", re.I),
        re.compile(r"_TEMPLATE\.md|way a check lies", re.I)),
    "`.factory/` is the tool's, not the repository's": (
        re.compile(r"`?\.factory/"),
        re.compile(r"the tool (writes|wrote|owns)|the tool's|not (one of )?(this |the )?repositor",
                   re.I)),
}


def paired(text, left, right):
    return [l.strip() for l in text.splitlines() if left.search(l) and right.search(l)]


def test_docgate_md_says_a_copy_of_the_repository_is_the_index():
    """Criterion 4 of `SRV-FIX-525`. The rule beside the module, not in the source.

    `copy_of_this_checkout` excluded a typed set of directories and the set did not name
    `.factory/runs`, where every run leaves a `stdin` FIFO `shutil.copytree` raises on — so the
    three placement cases were green in every lane and red on every checkout the tool had ever
    been pointed at. The derivation is git's index now, and `CLAUDE.md` puts the reasoning in
    `server/design/<module>.md` rather than in a comment above the helper.
    """
    design = DOCGATE_DESIGN.read_text()
    for claim, (left, right) in A_COPY_OF_THE_REPOSITORY.items():
        said = paired(design, left, right)
        assert said, (
            f"{DOCGATE_DESIGN} nowhere says, on one line, that {claim}. A session writing the next "
            "copy of this repository reads this file and is told nothing that would stop it typing "
            "a set of directories to leave out.")
    assert THE_COPY_FIX in design, \
        f"{DOCGATE_DESIGN} says it and names no ticket, so nothing points at the round that decided it"

    # NEGATIVE: each half alone is not the claim, and a line carrying both is.
    assert paired("the copy leaves out a typed set of directories\n",
                  *A_COPY_OF_THE_REPOSITORY["a typed exclusion list is the first way a check lies"]) == []
    assert paired("`_TEMPLATE.md` §2 lists four ways a check lies\n",
                  *A_COPY_OF_THE_REPOSITORY["a typed exclusion list is the first way a check lies"]) == []
    assert paired("a typed exclusion list is the first way a check lies (`_TEMPLATE.md` §2)\n",
                  *A_COPY_OF_THE_REPOSITORY["a typed exclusion list is the first way a check lies"]) != []


# ── SRV-FIX-518 · a scratch worktree outlives no attempt ───────────────────────────────────────

LAND_DESIGN = SERVER / "design" / "land.md"
THE_SCRATCH_FIX = "SRV-FIX-518"

# §1 criterion 4's rule, as the PAIRS of halves that have to meet on one line. Either half alone is
# a document that mentions two subjects in one paragraph — `WEB-FIX-370`'s shape — so the pair is
# what is searched for, never the sentence this ticket happened to write.
A_SCRATCH_OUTLIVES_NO_ATTEMPT = {
    "a scratch the tool made is removed after EVERY outcome of the step it was made for": (
        re.compile(r"scratch", re.I),
        re.compile(r"every outcome|any outcome|whatever the outcome|each outcome", re.I)),
    "with the FORCE that outcome needs": (
        re.compile(r"--force|forced?\b", re.I),
        re.compile(r"scratch|conflict marker|worktree remove", re.I)),
    "an attempt is asserted TWICE, and the second is the one that meets what the first left "
    "behind": (
        re.compile(r"twice|second attempt", re.I),
        re.compile(r"left behind|leftover|what the first|first attempt", re.I)),
}


def test_land_md_says_a_scratch_outlives_no_attempt():
    """Criterion 4 of `SRV-FIX-518`. The rule beside the module, not in the source.

    `closeScratch_` removed a scratch worktree without `--force`; git refuses to remove a checkout
    holding conflict markers, so a conflicting merge left one behind, and `openScratch_` refused
    the path on every later attempt on the same tip. The operator then read
    `no scratch checkout could be made` — which names neither the conflict nor its paths — for four
    and a half hours. `CLAUDE.md` puts the reasoning in `server/design/<module>.md` rather than in a
    comment above the helper, and `docs/operations/ESCAPES.md` says the class is *a check about what an
    operation must not damage that never asks what the operation itself left behind*.
    """
    design = LAND_DESIGN.read_text()
    for claim, (left, right) in A_SCRATCH_OUTLIVES_NO_ATTEMPT.items():
        said = paired(design, left, right)
        assert said, (
            f"{LAND_DESIGN} nowhere says, on one line, that {claim}. A session writing the next "
            "scratch worktree reads this file and is told nothing that would stop it removing one "
            "without the force the outcome needs, or asserting a single attempt.")
    assert THE_SCRATCH_FIX in design, (
        f"{LAND_DESIGN} says it and names no ticket, so nothing points at the round that "
        "decided it")

    # NEGATIVE: each half alone is not the claim, and a line carrying both is.
    twice = A_SCRATCH_OUTLIVES_NO_ATTEMPT[
        "an attempt is asserted TWICE, and the second is the one that meets what the first left "
        "behind"]
    assert paired("the scratch is removed and the attempt is asserted twice\n", *twice) == []
    assert paired("a failed attempt leaves nothing behind\n", *twice) == []
    assert paired("an attempt is asserted twice — the second meets what the first left behind\n",
                  *twice) != []
