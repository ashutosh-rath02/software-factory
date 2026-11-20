#!/usr/bin/env python3
"""Convention gate: read the DIFF, not the behaviour.

Every other gate in this repository asks whether the code works. This one asks whether it was
written the way this codebase is written. It ratchets: a violation already in the baseline is
tolerated, a new one fails. Nothing here is a matter of taste — every rule is a grep whose
answer two people agree on.

    ./tools/conventions.py                 check, exit 1 on a new violation
    ./tools/conventions.py --list          print every current violation, grouped
    ./tools/conventions.py --accept        re-record the baseline (never silently)
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "conventions-baseline.json"

# What each rule actually looked at, so a green run can be told from a run that swept nothing.
# `--sweep` prints this; `tests/test_conventions.py` asserts it is non-empty before it trusts
# any verdict from the three SRV-FIX-443 rules.
SWEPT = {}


def sources(*globs):
    out = []
    for g in globs:
        out += sorted(p for p in ROOT.glob(g) if ".cmake_build" not in p.parts)
    return out


def lines_of(path):
    return path.read_text(errors="replace").split("\n")


def rel(path):
    return str(path.relative_to(ROOT))


# ── rules ────────────────────────────────────────────────────────────────────────────────────
# Each yields (rule, file, detail). `detail` must be stable across unrelated edits — it is half
# of the baseline key, so a line number would make every insertion a new violation.

def app_names_asio():
    """app/ holds a core::Queue, never a strand. core/ owns asio."""
    for p in sources("app/**/*.h", "app/**/*.cpp"):
        for i, line in enumerate(lines_of(p), 1):
            if "boost/asio" in line or "boost::asio" in line:
                yield "app-names-asio", rel(p), line.strip()[:80]


def anonymous_namespace():
    """Helpers of a COMPONENT belong to it as private statics.

    SRV-FEAT-229 narrowed this to files that DEFINE a component member, and its QA measured the
    cost: 26 `app/**/*.cpp` and two `core/` files stopped being inspected, three of which carry no
    anonymous namespace today, so a new one landed GREEN. Two of the three `app/catalog` rows the
    ticket claimed to fix were still in the source; they left the report because the rule stopped
    looking at them.

    A gate that stops inspecting a file is not the same as a violation being fixed. The rule is
    broad again. Where an anonymous namespace is genuinely right — `render.cpp` and
    `document.cpp` declare no COMPONENT, so their helpers have no component to be private statics
    ON, and `manifest/store.cpp` and `app/api`'s action files hold too many for a header — the
    entry sits in the baseline where a reader can see it, rather than being erased by narrowing
    the rule until it disappears.
    """
    for p in sources("app/**/*.cpp", "core/*.cpp"):
        for line in lines_of(p):
            if re.match(r"^namespace\s*\{", line):
                yield "anonymous-namespace", rel(p), "namespace {"


def free_namespace_beside_component():
    """A component header declares the component and nothing else."""
    for p in sources("app/**/*.h"):
        text = p.read_text(errors="replace")
        if "COMPONENT(" not in text:
            continue
        for line in text.split("\n"):
            m = re.match(r"^namespace\s+([a-z_][a-z0-9_]*)\s*\{", line)
            if m:
                yield "free-namespace-beside-component", rel(p), f"namespace {m.group(1)}"


HOP_CALL = re.compile(r"(Worker::instance\(\)\.post(?:Serialized)?|\.offload)\(")
SLOT_WRITE = re.compile(r"\*\s*[A-Za-z_]\w*\s*=(?!=)")
LAMBDA = re.compile(r"\[[^\]]*\]\s*(?:\([^)]*\)\s*)?(?:mutable\s*)?(?:->[^{;]*)?\{")


def _call_arguments(text, at):
    """The top-level arguments of the call whose `(` is at `at`, as source spans.

    Character-level, skipping string and character literals and comments, so a JSON body full of
    braces and parentheses does not move the depth. Returns [] if the call does not close.
    """
    depth = 0
    args, start = [], None
    i, n = at, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            quote = c
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i = text.find("*/", i)
            if i < 0:
                return []
            i += 2
            continue
        if c in "([{":
            depth += 1
            if depth == 1:
                start = i + 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i])
                return args
        elif c == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
        i += 1
    return []


def _skip_braces(text, at):
    depth = 0
    i = at
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _reposts(completion):
    """`.post(` written in the completion's OWN body, never inside a lambda nested in it.

    The distinction is the whole rule. A completion that posts again is a caller writing as
    though its completion landed on the raw engine. A `.post(` inside a callback the completion
    hands to something else — a spawner, a socket — is that callback coming back from wherever it
    ran, which is the hop this repository wants.
    """
    opener = LAMBDA.search(completion)
    if not opener:
        return False
    body = completion[opener.end():_skip_braces(completion, opener.end() - 1) - 1]
    i = 0
    while i < len(body):
        nested = LAMBDA.match(body, i)
        if nested:
            i = _skip_braces(body, nested.end() - 1)
            continue
        if body.startswith(".post(", i):
            return True
        i += 1
    return False


def manual_worker_hop():
    """core::Queue::offload carries the result, so neither of these can be written by hand.

    Its predecessor keyed on `boost::asio::post(` appearing near a `Worker::instance().post(`,
    which `app_names_asio` exists to guarantee is absent from `app/`. An unsatisfiable
    precondition reports zero on every input, and `--list` prints a dead rule and a clean tree
    identically: the tree carried thirty of the shape for sixteen days while this said `clean`.

    The compiler owns the big half now — the two-argument `core::Worker::post` is private to
    `core::Queue`, so a completion cannot be landed on the raw engine at all (SRV-FIX-476). What
    is left is the two shapes it cannot see:

      slot    a task lambda that assigns through a dereferenced capture, `*name = …`, which is
              how a result came back when the worker returned nothing;
      re-hop  a completion of an `.offload(` that posts onto a queue again, which is the caller
              still writing as though its completion landed somewhere else.
    """
    n = 0
    for p in sources("app/**/*.cpp"):
        n += 1
        text = p.read_text(errors="replace")
        for m in HOP_CALL.finditer(text):
            args = _call_arguments(text, m.end() - 1)
            if not args:
                continue
            if SLOT_WRITE.search(args[0]):
                yield "manual-worker-hop", rel(p), "a result slot written through in the worker"
            if m.group(1) == ".offload" and len(args) > 1 and _reposts(args[1]):
                yield "manual-worker-hop", rel(p), "a completion that posts onto a queue"
    SWEPT["manual-worker-hop"] = {"files": n}


def comment_density():
    """`No comments unless asked` — CLAUDE.md. Recorded per file so it can only fall."""
    for p in sources("app/**/*.h", "app/**/*.cpp", "core/*.h", "core/*.cpp"):
        n = 0
        block = False
        for line in lines_of(p):
            s = line.strip()
            if block:
                n += 1
                if "*/" in s:
                    block = False
                continue
            if s.startswith("/*"):
                n += 1
                block = "*/" not in s
            elif s.startswith("//"):
                n += 1
        if n:
            yield "comment-lines", rel(p), str(n)




# ── SRV-FIX-443 ──────────────────────────────────────────────────────────────────────────────
# `core::Task` built its timer on the BASE `core::Component`'s queue — a strand no component
# posts a handler onto — so every timer in `app/` mutated its component's state beside the
# handlers that own it. The type change makes the wrong binding uncompilable; these three rules
# keep the shapes AROUND it from coming back, and rule 3 derives its set from the source so it
# cannot be bounded by what its author thought of.

QUEUE_MEMBER = re.compile(r"^\s*(?:core::)?Queue\s+\w+\s*;\s*$")


def one_queue_per_component():
    """A component owns exactly one core::Queue, and core::Component owns none.

    With no base queue there is no strand nobody uses. Two queues in one component is two
    executors over one component's state, which is SRV-FIX-443's defect written by hand.
    """
    n = 0
    for p in sources("app/**/*.h"):
        text = p.read_text(errors="replace")
        if "COMPONENT(" not in text:
            continue
        n += 1
        found = sum(1 for line in text.split("\n") if QUEUE_MEMBER.match(line))
        if found != 1:
            yield "one-queue-per-component", rel(p), f"{found} core::Queue member(s), want 1"

    base = ROOT / "core" / "component.h"
    if base.exists():
        n += 1
        found = sum(1 for line in lines_of(base) if QUEUE_MEMBER.match(line))
        if found != 0:
            yield ("one-queue-per-component", rel(base),
                   f"{found} core::Queue member(s), want 0")
    SWEPT["one-queue-per-component"] = {"files": n}


BLOCKING_PRIMITIVES = ["sleep_for", "sleep_until", "usleep", "nanosleep", "::poll(", "::select(",
                       "waitpid(", "std::thread", "std::atomic", "condition_variable",
                       "std::future", "std::promise", "std::async", "popen", "::system("]


def blocking_primitive_in_app():
    """No thread-blocking primitive under app/ that the baseline does not already carry.

    `git::killGroup` slept up to 4 s on one of two Worker threads and nothing could see it: a
    sleep is never a wrong answer, so no behavioural gate produces a row for it. The count is
    carried IN the detail, so removing one of two sites is both a new key and a stale one.
    """
    n = 0
    for p in sources("app/**/*.h", "app/**/*.cpp"):
        n += 1
        text = p.read_text(errors="replace")
        for prim in BLOCKING_PRIMITIVES:
            count = text.count(prim)
            if count:
                yield "blocking-primitive-in-app", rel(p), f"{prim} \u00d7{count}"
    SWEPT["blocking-primitive-in-app"] = {"files": n}


REACHES_SUBPROCESS = re.compile(r"\b(?:run|runQuiet)\(|::fork\(|posix_spawnp?\(|waitpid\(|sleep_for\(")
DEFINITION = re.compile(r"^\S[^;{}]*?\b([A-Za-z_]\w*)\([^;{}]*?\)\s*\n\{\n(.*?)^\}", re.M | re.S)
SUBPROCESS_FILES = ("app/lane/git.cpp", "app/delivery/git.cpp", "app/land/git.cpp")


def _definitions(path):
    out = {}
    for m in DEFINITION.finditer(path.read_text(errors="replace")):
        out.setdefault(m.group(1), []).append(m.group(2))
    return out


def subprocess_functions():
    """Every function in the two git modules that reaches a subprocess, closed transitively.

    DERIVED, never listed. A hand-written tuple is bounded by what its author thought of on the
    day — `SRV-FIX-351` cost four rounds to exactly that — so a function added to `git.cpp`
    today is governed today, with no edit to this file.
    """
    found = set()
    for name in SUBPROCESS_FILES:
        path = ROOT / name
        if not path.exists():
            continue
        defined = _definitions(path)
        for fn, bodies in defined.items():
            if any(REACHES_SUBPROCESS.search(b) for b in bodies):
                found.add(fn)
        grew = True
        while grew:
            grew = False
            reaches = re.compile(r"\b(%s)\(" % "|".join(map(re.escape, sorted(found))))
            for fn, bodies in defined.items():
                if fn not in found and any(reaches.search(b) for b in bodies):
                    found.add(fn)
                    grew = True
    return found


def _worker_span(ls, i):
    depth, started, parens = 0, False, 0
    for k in range(i, len(ls)):
        for ch in ls[k]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
            elif ch == "(":
                parens += 1
            elif ch == ")":
                parens -= 1
        if started and depth == 0:
            return k
        if not started and parens <= 0 and k > i:
            return k
        if not started and parens <= 0 and ";" in ls[k]:
            return k
    return i


def blocking_call_outside_a_worker():
    """A subprocess-running call made from anywhere that is not lexically inside a Worker lambda.

    `reserve_` forked `git worktree list --porcelain` on the lane strand and held one of four io
    threads for as long as git took. `SRV-FIX-108` measured it, named the criterion, and was
    closed without one.
    """
    derived = sorted(subprocess_functions())
    SWEPT["blocking-call-outside-a-worker"] = {"files": 0, "derived": derived}
    if not derived:
        return
    call = re.compile(r"\b(?:git|delivery)::(%s)\(" % "|".join(map(re.escape, derived)))
    hop = HOP_CALL

    n = 0
    for p in sources("app/**/*.cpp"):
        if rel(p) in SUBPROCESS_FILES:
            continue
        n += 1
        ls = lines_of(p)
        counts = {}
        k = 0
        while k < len(ls):
            if hop.search(ls[k]):
                k = _worker_span(ls, k) + 1
                continue
            for m in call.finditer(ls[k]):
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            k += 1
        for fn in sorted(counts):
            yield "blocking-call-outside-a-worker", rel(p), f"{fn} \u00d7{counts[fn]}"
    SWEPT["blocking-call-outside-a-worker"]["files"] = n




TASK_BUILT = re.compile(r"core::Task(?:\s+\w+)?\s*\{\s*([^,{}]+?)\s*,"
                        r"|make_shared\s*<\s*core::Task\s*>\s*\(\s*([^,()]+?)\s*,")
OWN_QUEUE = re.compile(r"^\s*(?:core::)?Queue\s+(\w+)\s*;\s*$")


def task_on_its_own_components_queue():
    """A component's timer is built on that component's OWN queue, and no other.

    `core::Task` taking a `core::Queue&` makes `{this, …}` uncompilable — which is what caught
    `SRV-FEAT-454`'s `armWords_` the moment it met `SRV-FIX-443`. It does NOT make
    `{AnotherComponent::instance().queue(), …}` uncompilable: that is a perfectly good
    `core::Queue&` and it is the same defect one step along, a timer serialised with a strand
    its own state does not live on. The compiler cannot refuse that one, so this does.

    The set of legal arguments per module is DERIVED from the `core::Queue` members that module's
    own headers declare, never listed.
    """
    modules = {}
    for p in sources("app/**/*.h"):
        module = rel(p).rsplit("/", 1)[0]
        for line in lines_of(p):
            m = OWN_QUEUE.match(line)
            if m:
                modules.setdefault(module, set()).add(m.group(1))

    seen = 0
    for p in sources("app/**/*.h", "app/**/*.cpp"):
        module = rel(p).rsplit("/", 1)[0]
        allowed = modules.get(module, set())
        for m in TASK_BUILT.finditer(p.read_text(errors="replace")):
            arg = (m.group(1) or m.group(2) or "").strip()
            seen += 1
            if arg not in allowed:
                yield ("task-on-its-own-components-queue", rel(p),
                       f"built on {arg[:60]}")
    SWEPT["task-on-its-own-components-queue"] = {"constructions": seen}

RULES = [app_names_asio, anonymous_namespace, free_namespace_beside_component,
         manual_worker_hop, comment_density,
         one_queue_per_component, blocking_primitive_in_app, blocking_call_outside_a_worker,
         task_on_its_own_components_queue]

# Two rules accept nothing without a reason: §1 of SRV-FIX-443 says each remaining site is named
# with WHY it is allowed. A baselined key of these with no note in `notes` fails the gate, so
# `--accept` cannot make either green in silence.
NEEDS_A_REASON = {"blocking-primitive-in-app", "blocking-call-outside-a-worker",
                  "one-queue-per-component"}

# comment-lines is a COUNT, so its baseline is a number that may only decrease. The rest are
# occurrences, so their baseline is a set that may only shrink.
COUNTED = {"comment-lines"}


def collect():
    found = {}
    for rule in RULES:
        for name, path, detail in rule():
            found.setdefault(name, {}).setdefault(path, []).append(detail)
    return found


def flatten(found):
    keys, counts = set(), {}
    for rule, by_file in found.items():
        for path, details in by_file.items():
            if rule in COUNTED:
                counts[f"{rule}|{path}"] = int(details[0])
            else:
                for d in details:
                    keys.add(f"{rule}|{path}|{d}")
    return keys, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    found = collect()
    keys, counts = flatten(found)

    if args.sweep:
        print(json.dumps(SWEPT, indent=1, sort_keys=True))
        return 0

    if args.list:
        for rule in sorted(found):
            print(f"\n── {rule} ({sum(len(v) for v in found[rule].values())})")
            for path in sorted(found[rule]):
                for d in found[rule][path]:
                    print(f"   {path}: {d}")
        return 0

    if args.accept:
        notes = {}
        if BASELINE.exists():
            notes = json.loads(BASELINE.read_text()).get("notes", {})
        BASELINE.write_text(json.dumps(
            {"occurrences": sorted(keys), "counts": counts,
             "notes": {k: v for k, v in notes.items() if k in keys}},
            indent=1, sort_keys=True) + "\n")
        print(f"baseline re-recorded: {len(keys)} occurrences, {len(counts)} counted files")
        return 0

    if not BASELINE.exists():
        print("no baseline; run ./tools/conventions.py --accept once", file=sys.stderr)
        return 2

    base = json.loads(BASELINE.read_text())
    old_keys = set(base["occurrences"])
    old_counts = base["counts"]

    new = sorted(keys - old_keys)
    grew = sorted(f"{k}: {old_counts.get(k, 0)} -> {v}"
                  for k, v in counts.items() if v > old_counts.get(k, 0))
    added = sorted(k for k in counts if k not in old_counts)

    # A baselined violation that no longer exists is STALE, and a stale entry is not harmless.
    # Until it is pruned the exact line that was removed can be put back and the gate says
    # nothing, because its key is still accepted — SRV-FEAT-230's C3, measured by its QA:
    # re-adding the exact include exits 0 while a novel one exits 1. Pruning was a manual
    # `--accept`, so it was deferrable, so it was deferred: not one of SRV-FEAT-229..236's
    # commits edited this file, and all six carry a C1 clause requiring it in the SAME commit.
    # Three QA sessions found the same defect three times. Failing here is what makes the
    # baseline move with the fix instead of in a ticket-less commit afterwards.
    stale = sorted(old_keys - keys)
    stale_counts = sorted(f"{k}: {old_counts[k]} -> {counts.get(k, 0)}"
                          for k in old_counts if counts.get(k, 0) < old_counts[k])

    # A site allowed under app/ without a written reason is a site nobody decided. SRV-FIX-443
    # §1 names each of them and why; this is what keeps that true after the ticket closes.
    notes = base.get("notes", {})
    unreasoned = sorted(k for k in old_keys & keys
                        if k.split("|", 1)[0] in NEEDS_A_REASON and not notes.get(k))

    if not new and not grew and not added and not stale and not stale_counts and not unreasoned:
        print(f"conventions: clean ({len(keys)} known)")
        return 0

    for k in new:
        print(f"NEW  {k.replace('|', '  ')}")
    for k in added:
        print(f"NEW  comment-lines in a new file  {k}: {counts[k]}")
    for g in grew:
        print(f"GREW {g.replace('|', '  ')}")
    for k in stale:
        print(f"FIXED {k.replace('|', '  ')}")
    for g in stale_counts:
        print(f"FIXED {g.replace('|', '  ')}")
    for k in unreasoned:
        print(f"NO REASON  {k.replace('|', '  ')}")

    if unreasoned:
        print(f"\n{len(unreasoned)} accepted violation(s) of a rule that requires one carry no "
              f"reason. Add each to \"notes\" in conventions-baseline.json saying why that site "
              f"is allowed — on a Worker, race-free by construction, or owned by a named ticket.")
        return 1

    if new or grew or added:
        print("\na convention gate ratchets: fix it, or --accept deliberately and say why")
    else:
        print(f"\n{len(stale) + len(stale_counts)} baselined violation(s) no longer exist. "
              f"Run `./tools/conventions.py --accept` and commit the baseline WITH the fix — "
              f"until it is pruned, the exact line you removed can be put back and this gate "
              f"will say nothing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
