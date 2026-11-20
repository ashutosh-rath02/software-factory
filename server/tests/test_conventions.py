"""The convention gate, run as part of the suite so it cannot be skipped.

Every other file here asks whether the code works. This one asks whether it was written the way
this codebase is written — and it ratchets, so a legacy violation is tolerated and a new one is
not. `tools/conventions.py --list` prints what is currently accepted.
"""

import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
CHECKER = SERVER / "tools" / "conventions.py"


def run(*args):
    return subprocess.run([sys.executable, str(CHECKER), *args],
                          capture_output=True, text=True, timeout=120, cwd=str(SERVER))


def test_no_new_convention_violation():
    r = run()
    assert r.returncode == 0, (
        f"the convention gate went red:\n{r.stdout}{r.stderr}\n"
        f"fix it, or accept it deliberately with `./tools/conventions.py --accept` and say why")


def test_the_gate_can_fail():
    """NEGATIVE (mandatory). A ratchet whose baseline already covers everything is a gate that
    cannot go red — which is what `registration-in-start` turned out to be, and why it is gone.
    Introduce a violation the baseline does not carry and require the checker to see it."""
    victim = SERVER / "app" / "catalog" / "store.h"
    before = victim.read_text()
    try:
        victim.write_text(before.replace("#include <core/queue.h>",
                                         "#include <core/queue.h>\n#include <boost/asio.hpp>"))
        r = run()
        assert r.returncode == 1, (
            f"a fresh `boost/asio` include in app/ did not fail the gate:\n{r.stdout}{r.stderr}")
        assert "app-names-asio" in r.stdout, r.stdout
    finally:
        victim.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the violation was undone"


def test_a_new_anonymous_namespace_in_a_component_module_still_fails():
    """RED, transcribed from SRV-FEAT-229's QA reproduction.

    SRV-FEAT-229 narrowed `anonymous-namespace` to a .cpp that DEFINES a component member or uses
    a handler macro. That took 26 `app/**/*.cpp` files and two `core/` files out of the rule's
    sight, and three of them carry no anonymous namespace today — so a NEW one lands green.

    QA proved it on `app/api/subscription.cpp`, which is part of `app/api` and whose component is
    `api::Manager`: identical probe, pre `1acfb100` exit 1, post `9a5b5307` exit 0. The rule got
    weaker and the baseline shrank by 42 rows rather than the 13 the ticket named — two of which
    are still in the source, having left the report only because the rule stopped looking.

    A gate that stops inspecting a file is not the same as a violation being fixed.
    """
    victim = SERVER / "app" / "api" / "subscription.cpp"
    before = victim.read_text()
    probe = "namespace {\nint qaProbeHelper() { return 0; }\n}\n\nnamespace api {"
    try:
        assert "namespace api {" in before, before[:200]
        victim.write_text(before.replace("namespace api {", probe, 1))
        r = run()
        assert r.returncode == 1, (
            f"a new anonymous namespace in app/api/subscription.cpp did not fail the gate — "
            f"the file is invisible to the rule:\n{r.stdout}{r.stderr}")
        assert "anonymous-namespace" in r.stdout and "subscription.cpp" in r.stdout, r.stdout
    finally:
        victim.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the probe was undone"


def test_a_fixed_violation_cannot_be_put_back_verbatim():
    """RED before the stale-entry check, transcribed from SRV-FEAT-230's QA reproduction.

    The baseline is a set of `rule|file|detail` keys. A violation that was FIXED but whose key
    still sits in the baseline can be reintroduced **verbatim** and the gate says nothing —
    QA measured exactly that on `app/permissions/store.h`:

        the EXACT line the ticket removed  → exit 0
        a NOVEL include                    → exit 1

    `test_the_gate_can_fail` could never see it, because it injects a novel include: the one
    shape that always works. This case injects the shape that did not.
    """
    victim = SERVER / "app" / "permissions" / "store.h"
    before = victim.read_text()
    try:
        victim.write_text(before.replace("#include <core/queue.h>",
                                         "#include <core/queue.h>\n#include <boost/asio/strand.hpp>", 1))
        r = run()
        assert r.returncode == 1, (
            f"the exact line SRV-FEAT-230 removed went back in and the gate stayed green:\n"
            f"{r.stdout}{r.stderr}")
        assert "app-names-asio" in r.stdout and "store.h" in r.stdout, r.stdout
    finally:
        victim.write_text(before)
    assert run().returncode == 0, "the gate did not go green again after the probe was undone"


def test_a_baselined_violation_that_no_longer_exists_fails_the_gate():
    """The reason the two above can be trusted: a stale entry is not tolerated.

    Not one of SRV-FEAT-229..236's commits edited `conventions-baseline.json`, and every one of
    them carries a C1 clause requiring it in the SAME commit. Pruning was a manual `--accept`,
    so it was deferrable, so it was deferred, and three QA sessions found the same defect three
    times. The gate now refuses while the baseline claims a violation the code no longer has.
    """
    import json
    baseline = SERVER / "tools" / "conventions-baseline.json"
    before = baseline.read_text()
    try:
        data = json.loads(before)
        data["occurrences"] = sorted(set(data["occurrences"]) |
                                     {"app-names-asio|app/qa/probe.cpp|#include <boost/asio.hpp>"})
        baseline.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        r = run()
        assert r.returncode == 1, (
            f"the baseline claims a violation that does not exist and the gate passed:\n{r.stdout}")
        assert "FIXED" in r.stdout and "app/qa/probe.cpp" in r.stdout, r.stdout
    finally:
        baseline.write_text(before)
    assert run().returncode == 0, "the gate did not go green again after the stale entry was removed"


# ── SRV-FIX-380 · a convention the COMPILER refuses ──────────────────────────────────────────

RENDER_H = SERVER / "app" / "catalog" / "render.h"
COMPILE_COMMANDS = SERVER.parent / ".cache" / "server" / "build" / "compile_commands.json"


def _probe_flags():
    """The REAL compile flags this build uses, taken from the build's own database.

    Never a typed list: a probe compiled with flags somebody wrote down here is a probe about
    those flags. `render.cpp`'s own entry is used because it is the translation unit that defines
    what the probes call.
    """
    assert COMPILE_COMMANDS.exists(), (
        f"build first — cd factory/server && ./build.sh build ({COMPILE_COMMANDS})")
    entries = json.loads(COMPILE_COMMANDS.read_text())
    match = [e for e in entries if e["file"].endswith("app/catalog/render.cpp")]
    assert match, "no compile_commands.json entry for app/catalog/render.cpp"
    argv = shlex.split(match[0]["command"])
    out = []
    skip = 0
    for i, tok in enumerate(argv[1:]):
        if skip:
            skip -= 1
            continue
        if tok in ("-o", "-c"):
            skip = 1
            continue
        out.append(tok)
    return argv[0], out, match[0]["directory"]


def compile_probe(body):
    """Compile one translation unit against the real headers with the real flags.

    Returns `(returncode, stderr)`. `-fsyntax-only`: the question is whether the call is legal,
    not whether it links.
    """
    cxx, flags, cwd = _probe_flags()
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.cpp"
        probe.write_text(body)
        r = subprocess.run([cxx, *flags, "-fsyntax-only", str(probe)],
                           capture_output=True, text=True, timeout=180, cwd=cwd)
        return r.returncode, r.stdout + r.stderr


def _probe(call):
    return f"""#include <app/catalog/render.h>

catalog::Result emit()
{{
    return {call};
}}
"""


OMITS_THE_LOOKUP = {
    "renderTicket": 'catalog::renderTicket("TIX-001", catalog::DeliveryLookup{})',
    "renderTickets": 'catalog::renderTickets(catalog::TicketQuery{}, catalog::DeliveryLookup{})',
}
PASSES_THE_LOOKUP = {
    "renderTicket": ('catalog::renderTicket("TIX-001", catalog::DeliveryLookup{}, {}, {}, {}, '
                     'catalog::AuthorLookup{})'),
    "renderTickets": ('catalog::renderTickets(catalog::TicketQuery{}, catalog::DeliveryLookup{}, '
                      '{}, {}, {}, {}, catalog::AuthorLookup{})'),
}


def test_render_ticket_refuses_a_caller_with_no_author_lookup():
    """§1 criterion 3 · MANDATORY NEGATIVE (SRV-FIX-380).

    `socket.cpp:122` served a ticket document with no `owner` for as long as it did because
    `render.h:110` declared `const AuthorLookup& authors = {}` — the parameter that must not be
    forgotten was the one the compiler was told is optional. This does not assert that in prose or
    by grepping for `renderTicket(`, which would report zero the day a fourth emitter spells the
    call differently. It compiles a translation unit that omits the lookup and requires the
    COMPILER to refuse it.

    The control probe is the half that makes the refusal mean something: an otherwise identical
    call that DOES pass an `AuthorLookup` must compile clean in the same run, so a broken probe,
    a missing header or a stale build cannot make this case pass.
    """
    for name in ("renderTicket", "renderTickets"):
        code, err = compile_probe(_probe(OMITS_THE_LOOKUP[name]))
        assert code != 0, (
            f"`{name}` compiled with no AuthorLookup, so an emitter can silently serve a ticket "
            f"document with no owner — exactly the shape of SRV-FIX-377's escape:\n"
            f"{OMITS_THE_LOOKUP[name]}")
        assert "too few arguments" in err, (
            f"`{name}` was refused for some reason other than the missing lookup, so this case "
            f"is not about the default:\n{err}")

        code, err = compile_probe(_probe(PASSES_THE_LOOKUP[name]))
        assert code == 0, (
            f"the CONTROL probe for `{name}` did not compile, so the refusal above proves "
            f"nothing about the missing lookup:\n{err}")


def test_every_author_lookup_parameter_is_covered_by_a_compile_probe():
    """The sweep, so the case above cannot be discharged by the two names it happens to list.

    Derived from `render.h` itself, never from a typed tuple: every declaration in it that takes
    an `AuthorLookup` must carry no default for it AND must be one the probes above drive. A
    fourth emitter's signature added with `= {}` fails here on the day it is written, which is the
    thing `SRV-FIX-377` had no way to notice.
    """
    text = RENDER_H.read_text()
    declarations = {}
    for match in re.finditer(r"Result\s+(\w+)\s*\((.*?)\)\s*;", text, re.S):
        if "AuthorLookup" in match.group(2):
            declarations[match.group(1)] = match.group(2)

    assert declarations, (
        f"no declaration in {RENDER_H} takes an AuthorLookup — the sweep examined nothing, so "
        f"this case has not run")

    defaulted = sorted(name for name, params in declarations.items()
                       if re.search(r"AuthorLookup&\s*\w+\s*=", params))
    assert defaulted == [], (
        f"{defaulted} still declare `AuthorLookup ... = {{}}`, so an emitter that omits the "
        f"lookup compiles and serves a ticket document with no owner")

    assert sorted(declarations) == sorted(OMITS_THE_LOOKUP), (
        f"render.h declares {sorted(declarations)} taking an AuthorLookup and the compile probes "
        f"drive {sorted(OMITS_THE_LOOKUP)}. Write a probe for the new one rather than widening "
        f"this list — a name nobody compiled is a name nobody checked.")


# ── SRV-FIX-443 · the executor stops being findable by accident ──────────────────────────────
#
# `355a4ff2` put every `core::Task` timer on the BASE `core::Component`'s queue — a strand no
# component posts a handler onto — and the case written for it afterwards greps `core/task.cpp`
# for the string `strandOf(c->queue())`. *The component's queue* had two referents, so the grep
# was green while `lane::Store`'s sweep, silence and settle timers mutated `lanes_`, `silence_`
# and `settling_` beside every handler that touches them. These four cases are the structural
# half of SRV-FIX-443: the shapes that produced the defect cannot re-enter unseen.

BASELINE = SERVER / "tools" / "conventions-baseline.json"
STORE_H = SERVER / "app" / "lane" / "store.h"
STORE_CPP = SERVER / "app" / "lane" / "store.cpp"
RENDER_CPP = SERVER / "app" / "lane" / "render.cpp"
LANE_GIT_CPP = SERVER / "app" / "lane" / "git.cpp"


def sweep():
    """What each rule examined, asked of the checker.

    §2: a check that sweeps nothing has not run. Every case below asserts the rule it is about
    looked at a non-empty set BEFORE it trusts a green or a red.
    """
    r = run("--sweep")
    assert r.returncode == 0, f"--sweep failed:\n{r.stdout}{r.stderr}"
    return json.loads(r.stdout)


def occurrences_of(rule):
    data = json.loads(BASELINE.read_text())
    return sorted(k for k in data["occurrences"] if k.startswith(rule + "|"))


def test_a_second_queue_in_a_component_fails_the_gate():
    """§1 criterion 5 · MANDATORY NEGATIVE.

    A component owns exactly one `core::Queue` and `core::Component` owns none. With no base
    queue there is no strand nobody uses, and a timer bound to one cannot be written — which is
    the whole mechanism, and this is the gate that keeps a second one from coming back.
    """
    swept = sweep()["one-queue-per-component"]
    assert swept["files"] > 0, (
        f"the one-queue-per-component rule examined no component header at all, so a green run "
        f"from it means nothing: {swept}")

    assert run().returncode == 0, "the gate is not green before this case breaks anything"

    before = STORE_H.read_text()
    anchor = "    core::Queue exec_;"
    assert anchor in before, f"{STORE_H} no longer declares the queue this case is about"
    try:
        STORE_H.write_text(before.replace(anchor, anchor + "\n    core::Queue second_;", 1))
        r = run()
        assert r.returncode == 1, (
            f"a second core::Queue in a component did not fail the gate — a component with two "
            f"strands is two writers of one state, which is SRV-FIX-443's defect exactly:\n"
            f"{r.stdout}{r.stderr}")
        assert "one-queue-per-component" in r.stdout and "app/lane/store.h" in r.stdout, r.stdout
    finally:
        STORE_H.write_text(before)

    assert run().returncode == 0, (
        "the gate did not go green again after the second queue was removed")

    # The exceptions, asserted beside the rule rather than left where nobody looks. Two
    # components own no queue at all — they hold no state a handler mutates — and the rule says
    # "exactly one" rather than "at most one" so that adding a third such component is a
    # deliberate act with a reason, not a silent one.
    assert occurrences_of("one-queue-per-component") == sorted([
        "one-queue-per-component|app/api/manager.h|0 core::Queue member(s), want 1",
        "one-queue-per-component|app/manifest/store.h|0 core::Queue member(s), want 1",
    ]), ("the accepted set of components that own no core::Queue has changed:\n"
         + "\n".join(occurrences_of("one-queue-per-component")))

    notes = json.loads(BASELINE.read_text()).get("notes", {})
    missing = [k for k in occurrences_of("one-queue-per-component") if not notes.get(k)]
    assert missing == [], f"a component owns no queue and no reason is recorded: {missing}"


def the_lander_row():
    """The accepted `app/land/lander.cpp` row, with the count READ rather than typed.

    `SRV-FIX-461` accepted it as a COUNT, which is what makes the acceptance safe: a call more is
    a different number and therefore a new row, and `test_a_land_git_call_outside_a_worker_fails_
    the_gate` is the negative that proves it. What the count IS, though, is a fact about the file
    and it moves every time the module gains a call — 31, then 36 (`SRV-FIX-518`), then 37
    (`SRV-FIX-532`) — so a number typed into two cases here is a ratchet that goes red on work
    that is otherwise correct, and says nothing when it does.
    """
    rows = [row for row in json.loads(BASELINE.read_text()).get("notes", {})
            if row.startswith("blocking-call-outside-a-worker|app/land/lander.cpp|run ×")]
    assert len(rows) == 1, (
        f"the lander's accepted row is not one row, so neither case below knows which it means: "
        f"{rows}")
    return rows[0]


def test_an_accepted_violation_without_a_reason_fails_the_gate():
    """§1 criteria 6 and 7 say each remaining site is "named with why it is allowed".

    A reason nothing checks is a reason that rots, and `--accept` would otherwise be a way to
    make either rule green in silence — which is the whole failure mode `--accept` already
    carries a warning about. Removing one reason from the baseline has to turn the gate red,
    naming the site that lost it.
    """
    before = BASELINE.read_text()
    victims = [
        "blocking-call-outside-a-worker|app/lane/store.cpp|groupIsEmpty ×1",
        # SRV-FIX-461 criterion 2. app/land/ reached this gate only in the merge — main added the
        # module while SRV-FIX-443 was in QA — and its 31 calls are accepted on a safety argument
        # about lander.cpp:133's offload. An argument nothing checks is an argument that rots.
        the_lander_row(),
    ]
    for victim in victims:
        data = json.loads(before)
        assert victim in data.get("notes", {}), (
            f"{victim} carries no reason to remove, so this case has nothing to measure")
        try:
            del data["notes"][victim]
            BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
            r = run()
            assert r.returncode == 1, (
                f"a subprocess-running call is accepted outside a Worker with its reason deleted "
                f"and the gate stayed green:\n{r.stdout}{r.stderr}")
            assert "NO REASON" in r.stdout, r.stdout
            assert victim.split("|")[-1].split(" ")[0] in r.stdout, r.stdout
        finally:
            BASELINE.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the reasons were restored"


LANDER_CPP = SERVER / "app" / "land" / "lander.cpp"


def test_a_land_git_call_outside_a_worker_fails_the_gate():
    """SRV-FIX-461 criterion 3 · MANDATORY NEGATIVE for the 31 calls this branch baselined.

    The row is accepted as a COUNT, which is what makes the acceptance safe: the 31 calls are
    reached only through `lander.cpp:133`'s `queue_.offload`, and a 32nd one is a different
    number and therefore a new row. This puts that 32nd call in `consider()` — which runs on
    `queue_`, an engine io thread, and is exactly where it must never go — and requires the gate
    to see it.

    Without this case the accepted count is a number nobody can make move, which is the shape of
    ratchet `registration-in-start` turned out to be.
    """
    before = LANDER_CPP.read_text()
    anchor = "        pending_ = requests;"
    assert anchor in before, f"{LANDER_CPP} no longer has the queue_-side body this case probes"
    probe = ('        const land::git::Output leaked =\n'
             '            git::run(root_, "rev-parse", {"--verify", "HEAD"});\n'
             '        (void)leaked;\n')
    try:
        LANDER_CPP.write_text(before.replace(anchor, probe + anchor, 1))
        r = run()
        assert r.returncode == 1, (
            f"a land::git::run added to consider(), which runs on the lander's own queue and not "
            f"on a Worker, did not fail the gate:\n{r.stdout}{r.stderr}")
        assert ("blocking-call-outside-a-worker" in r.stdout
                and "app/land/lander.cpp" in r.stdout), r.stdout
    finally:
        LANDER_CPP.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the probe was undone"


def the_lane_git_waitpid_row():
    """The accepted `app/lane/git.cpp` waitpid row, with the count READ rather than typed.

    Same reason as `the_lander_row`: what the count IS is a fact about the file and it moves when
    the module gains a wait — `SRV-FIX-532` added the third, which reaps a child whose exec
    failed and is therefore already gone. What `SRV-FIX-443` accepted is the SHAPE of the row and
    the reason beside it, and that is what this case still asserts.
    """
    rows = [row for row in json.loads(BASELINE.read_text()).get("notes", {})
            if row.startswith("blocking-primitive-in-app|app/lane/git.cpp|waitpid( ×")]
    assert len(rows) == 1, f"the row is not one row: {rows}"
    return rows[0]


def test_a_sleep_added_to_app_fails_the_gate():
    """§1 criterion 6 · MANDATORY NEGATIVE.

    `git::killGroup` slept up to 4 s on one of two `core::Worker` threads, so two concurrent
    force-releases stalled every catalog render, every offload and the reaper `settle_` waits on.
    Nothing in the suite could see it: a sleep is not a wrong answer.

    The accepted SET is asserted in the SAME case as the live rule. §1 names the sites that may
    remain and why each is allowed; a row nobody wrote a reason for is a row nobody decided.
    """
    swept = sweep()["blocking-primitive-in-app"]
    assert swept["files"] > 0, f"the blocking-primitive rule examined nothing: {swept}"

    assert occurrences_of("blocking-primitive-in-app") == sorted([
        "blocking-primitive-in-app|app/api/_slow.cpp|sleep_for ×1",
        # SRV-FIX-532 D1 made the ledger its own counter, and the exclusion between two servers is
        # an O_CREAT|O_EXCL file that a loser has to wait on. The wait is inside the allocate
        # offload — a core::Worker — and no handler thread reaches it.
        "blocking-primitive-in-app|app/catalog/ids.cpp|usleep ×1",
        "blocking-primitive-in-app|app/delivery/git.cpp|::poll( ×1",
        "blocking-primitive-in-app|app/delivery/git.cpp|waitpid( ×1",
        "blocking-primitive-in-app|app/lane/command.cpp|std::atomic ×1",
        the_lane_git_waitpid_row(),
        "blocking-primitive-in-app|app/lane/state.cpp|std::atomic ×1",
        "blocking-primitive-in-app|app/lane/store.cpp|waitpid( ×1",
        # app/land/ landed on main while SRV-FIX-443 was in QA, so these two reached this rule
        # only in the merge (SRV-FIX-461). They are land's subprocess runner — the same shape as
        # app/delivery/git.cpp's two rows above — and land::git::run's only consumer is
        # app/land/lander.cpp, where every call is under attempt_ on lander.cpp:133's offload.
        "blocking-primitive-in-app|app/land/git.cpp|::poll( ×1",
        "blocking-primitive-in-app|app/land/git.cpp|waitpid( ×1",
    ]), (
        "the accepted set of thread-blocking primitives under app/ is not the one SRV-FIX-443 §1 "
        "names. Each of these is on a Worker or race-free by construction, and the two "
        "std::atomic belong to SRV-FIX-444:\n"
        + "\n".join(occurrences_of("blocking-primitive-in-app")))

    notes = json.loads(BASELINE.read_text()).get("notes", {})
    missing = [k for k in occurrences_of("blocking-primitive-in-app") if not notes.get(k)]
    assert missing == [], (
        f"a blocking primitive is accepted under app/ with no reason recorded: {missing}")

    before = RENDER_CPP.read_text()
    anchor = "namespace {"
    assert anchor in before, f"{RENDER_CPP} has no anonymous namespace to put the probe in"
    probe = ("#include <thread>\n#include <chrono>\n\nnamespace {\n"
             "void qaProbe() { std::this_thread::sleep_for(std::chrono::milliseconds(1)); }\n")
    try:
        RENDER_CPP.write_text(before.replace(anchor, probe, 1))
        r = run()
        assert r.returncode == 1, (
            f"a sleep_for added to app/lane/render.cpp did not fail the gate:\n"
            f"{r.stdout}{r.stderr}")
        assert ("blocking-primitive-in-app" in r.stdout
                and "app/lane/render.cpp" in r.stdout), r.stdout
    finally:
        RENDER_CPP.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the sleep was undone"


def test_git_put_back_on_the_strand_fails_the_gate():
    """§1 criterion 7 · MANDATORY NEGATIVE.

    `reserve_` forked `git worktree list --porcelain` on `lane::Store`'s own strand and blocked
    it for as long as git took — `SRV-FIX-108` measured it on 2026-08-07, named the criterion,
    and was closed by decision without one being written. Putting the exact call back has to turn
    this gate red.
    """
    swept = sweep()["blocking-call-outside-a-worker"]
    assert swept["files"] > 0, f"the blocking-call rule examined no app/ source: {swept}"

    assert occurrences_of("blocking-call-outside-a-worker") == sorted([
        "blocking-call-outside-a-worker|app/catalog/store.cpp|git ×2",
        "blocking-call-outside-a-worker|app/lane/diff.cpp|runQuiet ×1",
        "blocking-call-outside-a-worker|app/lane/store.cpp|registeredWorktrees ×1",
        # SRV-FIX-443 §1 enumerated the three above. `groupIsEmpty` is a FOURTH, and it is one
        # this ticket created: it is the non-blocking half of the stepped kill grace and runs on
        # the lane strand deliberately. It lands in the derived set because the derivation is
        # transitive and it reaches `reapLeader_`, whose `waitpid` carries WNOHANG. Narrowing the
        # derivation to exclude it would be deleting a row until the gate passed; it is recorded
        # with its reason instead, which is what §1 asks of every accepted site.
        "blocking-call-outside-a-worker|app/lane/store.cpp|groupIsEmpty ×1",
        # SRV-FIX-461: the 31 calls in main's app/land/lander.cpp. Accepted as a COUNT, which is
        # what makes it safe — every one is under attempt_, reached only through
        # lander.cpp:133's queue_.offload, and a 32nd call is a different number and so a new
        # row. test_a_land_git_call_outside_a_worker_fails_the_gate is that negative.
        the_lander_row(),
    ]), (
        "the accepted set of subprocess-running calls made outside a Worker is not the one "
        "SRV-FIX-443 §1 names — `store.cpp`'s `worktreeForBranch` is not in it:\n"
        + "\n".join(occurrences_of("blocking-call-outside-a-worker")))

    notes = json.loads(BASELINE.read_text()).get("notes", {})
    missing = [k for k in occurrences_of("blocking-call-outside-a-worker") if not notes.get(k)]
    assert missing == [], (
        f"a subprocess-running call is accepted outside a Worker with no reason recorded: "
        f"{missing}")

    before = STORE_CPP.read_text()
    anchor = "std::string lane::Store::branchFor_"
    assert anchor in before, f"{STORE_CPP} has no anchor for the probe"
    probe = ("void lane::Store::qaProbe_()\n{\n"
             "    const std::string existing = git::worktreeForBranch(root_, root_);\n"
             "    (void)existing;\n}\n\n")
    try:
        STORE_CPP.write_text(before.replace(anchor, probe + anchor, 1))
        r = run()
        assert r.returncode == 1, (
            f"`git::worktreeForBranch` put back on the strand did not fail the gate:\n"
            f"{r.stdout}{r.stderr}")
        assert ("blocking-call-outside-a-worker" in r.stdout
                and "app/lane/store.cpp" in r.stdout
                and "worktreeForBranch" in r.stdout), r.stdout
    finally:
        STORE_CPP.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the probe was undone"


def test_the_blocking_set_is_derived_not_listed():
    """§1 criterion 7 · the half that makes the rule survive its author.

    A hand-written tuple of subprocess-running functions is bounded by what its author thought of
    — `SRV-FIX-351` cost four rounds to exactly that shape. The set is derived from
    `app/lane/git.cpp` and `app/delivery/git.cpp` at run time, closed transitively over the file,
    so a function added TODAY is in the set today with no edit to the checker.
    """
    derived = set(sweep()["blocking-call-outside-a-worker"]["derived"])
    assert derived, "the blocking set is empty; nothing was derived, so the rule governs nothing"
    for known in ("run", "runQuiet", "worktreeForBranch", "spawnConfined", "registeredWorktrees"):
        assert known in derived, (
            f"`{known}` reaches a subprocess and is not in the derived set: {sorted(derived)}")

    git_before = LANE_GIT_CPP.read_text()
    store_before = STORE_CPP.read_text()
    new_fn = ("bool lane::git::qaProbeSubprocess(const std::string& repoRoot)\n{\n"
              "    return run(repoRoot, {\"status\"}).ok;\n}\n\n")
    caller = ("void lane::Store::qaProbe_()\n{\n"
              "    (void)git::qaProbeSubprocess(root_);\n}\n\n")
    anchor_git = "bool lane::git::isRunning(int pid)"
    anchor_store = "std::string lane::Store::branchFor_"
    assert anchor_git in git_before, f"{LANE_GIT_CPP} has no anchor for the probe"
    assert anchor_store in store_before, f"{STORE_CPP} has no anchor for the probe"
    try:
        LANE_GIT_CPP.write_text(git_before.replace(anchor_git, new_fn + anchor_git, 1))
        after = set(sweep()["blocking-call-outside-a-worker"]["derived"])
        assert "qaProbeSubprocess" in after, (
            f"a function added to app/lane/git.cpp whose body calls `run(` did not appear in the "
            f"derived set, so the set is a list somebody typed: {sorted(after - derived)}")

        STORE_CPP.write_text(store_before.replace(anchor_store, caller + anchor_store, 1))
        r = run()
        assert r.returncode == 1, (
            f"a call to the newly derived function outside a Worker did not fail the gate:\n"
            f"{r.stdout}{r.stderr}")
        assert "qaProbeSubprocess" in r.stdout and "app/lane/store.cpp" in r.stdout, r.stdout
    finally:
        STORE_CPP.write_text(store_before)
        LANE_GIT_CPP.write_text(git_before)

    assert run().returncode == 0, "the gate did not go green again after both probes were undone"


# ── SRV-FIX-469 · a timer belongs to the component whose state it touches ────────────────────

def test_a_task_built_on_another_components_queue_fails_the_gate():
    """§1 criterion 3 of SRV-FIX-469 · MANDATORY NEGATIVE.

    `core::Task` taking a `core::Queue&` makes `{this, …}` uncompilable, which is what caught
    `SRV-FEAT-454`'s `armWords_` the moment the two trees met. It does NOT make
    `{SomeOtherComponent::instance().queue(), …}` uncompilable — that is a perfectly good
    `core::Queue&`, and it is the same defect one step along: a timer serialised with a strand
    that is not the one its own state lives on.

    The compiler cannot see that one, so the gate must. Every `core::Task` constructed under
    `app/` names a `core::Queue` member declared by the component in its own module; a
    construction whose first argument reaches into anything else turns the gate red naming the
    file.
    """
    swept = sweep()["task-on-its-own-components-queue"]
    assert swept["constructions"] > 0, (
        f"the rule examined no core::Task construction at all, so a green run from it means "
        f"nothing: {swept}")

    assert run().returncode == 0, "the gate is not green before this case breaks anything"

    before = STORE_CPP.read_text()
    anchor = "    core::Task task {exec_, [this, name] {"
    assert anchor in before, f"{STORE_CPP} no longer builds the silence task this case rebinds"
    try:
        STORE_CPP.write_text(before.replace(
            anchor,
            "    core::Task task {api::Socket::instance().queue(), [this, name] {", 1))
        r = run()
        assert r.returncode == 1, (
            f"a core::Task bound to ANOTHER component's queue did not fail the gate — the timer "
            f"is serialised with a strand its own state does not live on, which is SRV-FIX-443's "
            f"defect one step along:\n{r.stdout}{r.stderr}")
        assert ("task-on-its-own-components-queue" in r.stdout
                and "app/lane/store.cpp" in r.stdout), r.stdout
    finally:
        STORE_CPP.write_text(before)

    assert run().returncode == 0, "the gate did not go green again after the probe was undone"


# ── SRV-FIX-476 · the rule that owned the shape could not fire ───────────────────────────────
#
# `manual_worker_hop` yielded only when `boost::asio::post(` sat within twelve lines of a
# `Worker::instance().post(` — and `app_names_asio` exists to guarantee that string is absent
# from `app/`. Its precondition was unsatisfiable from the day SRV-FEAT-239's U1 went green, so
# it reported zero on every input, and `--list` prints a dead rule and a clean tree identically.
# The tree carried thirty of the shape while the gate answered `conventions: clean`.
#
# The compiler now refuses the hand-written hop (`test_queue.py::test_a_completion_off_the_queue
# _has_no_type`). These two cases are about the residual shapes the compiler cannot see, and each
# one drives the rule over BOTH halves: a fixture that has the shape must yield a row, and the
# correct shape must yield none in the same case. The second half is what proves the rule is not
# a rule that fires on everything.

PROBE = SERVER / "app" / "qa-probe.cpp"

SLOT_SHAPE = """#include <memory>

#include <core/queue.h>

void probe(core::Queue& queue)
{
    auto out = std::make_shared<int>(0);
    queue.offload([out] { *out = 42; }, [out] { (void)*out; });
}
"""

SLOT_SHAPE_DONE_RIGHT = """#include <core/queue.h>

void probe(core::Queue& queue)
{
    queue.offload([] { return 42; }, [](int value) { (void)value; });
}
"""

REHOP_SHAPE = """#include <core/queue.h>

void probe(core::Queue& queue, core::Queue& other)
{
    queue.offload([] { return 42; }, [&other](int value) { other.post([value] { (void)value; }); });
}
"""

REHOP_SHAPE_DONE_RIGHT = """#include <core/queue.h>

void probe(core::Queue& queue)
{
    queue.offload([] { return 42; }, [&queue](int value) { (void)value; (void)&queue; });
}
"""


def _sweep():
    r = run("--sweep")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _gate_over(source):
    """Put one fixture source under app/ and give the gate's verdict on it back."""
    PROBE.write_text(source)
    try:
        r = run()
        swept = _sweep()
    finally:
        PROBE.unlink()
    return r, swept


def test_a_result_slot_written_through_in_a_worker_fails_the_gate():
    """§1 criterion 6 · the `slot` shape.

    57 of these were in `app/` on 2026-09-08, eight of them at one call site, because the worker
    returned nothing and the only way back was a `std::shared_ptr` allocated before the hop. The
    result is the completion's parameter now, so a task lambda that assigns through a dereferenced
    capture is a caller that went round the type.

    MANDATORY NEGATIVE for the gate is the second half: the correct shape — the same offload with
    the value returned and received as a parameter — must yield NO row in this same case, so this
    is not a rule that fires on every `offload`.
    """
    red, swept = _gate_over(SLOT_SHAPE)
    assert red.returncode == 1, (
        f"a worker task writing its result through `*out = …` did not fail the gate:\n"
        f"{red.stdout}{red.stderr}")
    assert "manual-worker-hop" in red.stdout and "qa-probe.cpp" in red.stdout, red.stdout
    assert swept.get("manual-worker-hop", {}).get("files"), (
        f"the rule reported a verdict having swept nothing — which is exactly how the old "
        f"`manual_worker_hop` stayed green over thirty violations: {swept}")

    green, swept = _gate_over(SLOT_SHAPE_DONE_RIGHT)
    assert green.returncode == 0, (
        f"the correct shape — the value returned by the work and taken by the completion — was "
        f"refused, so this rule fires on every offload and says nothing about the defect:\n"
        f"{green.stdout}{green.stderr}")
    assert swept.get("manual-worker-hop", {}).get("files"), swept


def test_a_completion_that_reposts_onto_a_queue_fails_the_gate():
    """§1 criterion 6 · the `re-hop` shape.

    13 sites in `app/` re-posted their completion onto their own queue by hand, because the
    two-argument `core::Worker::post` landed it on the raw engine. `Queue::offload` puts it on the
    caller's own strand, so a completion that posts again is a completion somebody wrote as though
    it were still on the wrong executor — the lambdas nest three deep and the capture list is
    written twice.

    MANDATORY NEGATIVE for the gate: the same completion with no inner post yields no row in this
    same case.
    """
    red, swept = _gate_over(REHOP_SHAPE)
    assert red.returncode == 1, (
        f"a completion that posts onto a queue by hand did not fail the gate:\n"
        f"{red.stdout}{red.stderr}")
    assert "manual-worker-hop" in red.stdout and "qa-probe.cpp" in red.stdout, red.stdout
    assert swept.get("manual-worker-hop", {}).get("files"), swept

    green, swept = _gate_over(REHOP_SHAPE_DONE_RIGHT)
    assert green.returncode == 0, (
        f"a completion that does NOT re-hop was refused, so this rule fires on every offload:\n"
        f"{green.stdout}{green.stderr}")
    assert swept.get("manual-worker-hop", {}).get("files"), swept


# ── SRV-FIX-532 cluster F3 — the blocking-call rule covers the domain its criterion states ────
#
# `SRV-FIX-457`. `SRV-FIX-443` §1 criterion 7 says the call is refused *from anywhere in
# `app/**/*.cpp`*, and two approximations left that false: the exemption for the definition sites
# was a BASENAME test, and the span a Worker hop is credited with covering ran to the end of the
# file when the hop's line carried no brace. Both are the same shape — an approximation nobody
# checked against a case that leaves the region it is supposed to cover.

STORE_CPP = SERVER / "app" / "lane" / "store.cpp"


def test_a_third_git_cpp_is_not_exempt_from_the_blocking_rule():
    """§1 F3 — the exemption is the definition sites BY PATH, never every file so named.

    `SRV-FIX-457` criterion 1, mandatory negative. `app/error/git.cpp` calling
    `lane::git::worktreeForBranch` outside any Worker is a `fork` + `execvp` + blocking `waitpid`
    made from `app/`, and the gate called it clean because the file happened to be called
    `git.cpp`.

    The definition sites stay exempt, and the case asserts they are still swept as definitions
    afterwards — a fix that exempted nothing would pass the probe and turn the whole tree red.
    """
    probe = SERVER / "app" / "error" / "git.cpp"
    assert not probe.exists(), f"{probe} already exists; this case would overwrite it"
    assert run().returncode == 0, "the gate is not green before the probe, so it decides nothing"
    try:
        probe.write_text(
            "#include <app/lane/git.h>\n"
            "#include <string>\n\n"
            "namespace lane {\n"
            "std::string qaProbeC(const std::string& root)\n"
            "{\n"
            "    return lane::git::worktreeForBranch(root, \"main\");\n"
            "}\n"
            "}\n")
        r = run()
        assert r.returncode == 1, (
            "a subprocess-running call from a third file named git.cpp did not fail the gate:\n"
            f"{r.stdout}{r.stderr}")
        assert "blocking-call-outside-a-worker" in r.stdout, r.stdout
        assert "app/error/git.cpp" in r.stdout, r.stdout
    finally:
        probe.unlink()
    assert run().returncode == 0, "the gate did not go green again after the probe was deleted"

    derived = sweep().get("blocking-call-outside-a-worker", {}).get("derived", [])
    assert "worktreeForBranch" in derived, (
        "the definition sites are no longer read for the functions they define, so the exemption "
        f"became an exclusion: {derived}")


def test_a_brace_free_offload_does_not_hide_the_rest_of_the_file():
    """§1 F3 — a hop whose line carries no brace covers its own statement and nothing more.

    `SRV-FIX-457` criterion 2, mandatory negative. `_worker_span` returned only once brace depth
    came back to zero AFTER it had seen a `{`; an offload written with named callbacks instead of
    inline lambdas has no brace on its line, so the span ran to the end of the file and every
    subprocess-running call below it — including `store.cpp`'s own baselined sites — was skipped.

    The probe is placed ABOVE the file's own calls on purpose: at the end it hides only itself.
    """
    before = STORE_CPP.read_text()
    anchor = "void lane::Store::init()"
    assert anchor in before, f"{STORE_CPP} no longer has the body this case probes"
    assert run().returncode == 0, "the gate is not green before the probe, so it decides nothing"

    blind = ("void lane::Store::qaProbeD_()\n"
             "{\n"
             "    exec_.offload(nullptr, nullptr);\n"
             "    const std::string existing = git::worktreeForBranch(root_, \"main\");\n"
             "    (void)existing;\n"
             "}\n\n")
    try:
        STORE_CPP.write_text(before.replace(anchor, blind + anchor, 1))
        r = run()
        assert r.returncode == 1, (
            "a brace-free offload above an unguarded subprocess call did not fail the gate, so "
            f"it is still blinding everything below it:\n{r.stdout}{r.stderr}")
        assert "blocking-call-outside-a-worker" in r.stdout, r.stdout
        assert "app/lane/store.cpp" in r.stdout and "worktreeForBranch" in r.stdout, r.stdout

        # ...and removing only the unguarded CALL turns it green while the brace-free offload
        # stays, so the case is about the span and not about the word `offload`.
        kept = ("void lane::Store::qaProbeD_()\n"
                "{\n"
                "    exec_.offload(nullptr, nullptr);\n"
                "}\n\n")
        STORE_CPP.write_text(before.replace(anchor, kept + anchor, 1))
        assert run().returncode == 0, (
            "a brace-free offload on its own failed the gate, so the fix refuses the hop rather "
            f"than bounding it:\n{run().stdout}")
    finally:
        STORE_CPP.write_text(before)
    assert run().returncode == 0, "the gate did not go green again after the probe was undone"
